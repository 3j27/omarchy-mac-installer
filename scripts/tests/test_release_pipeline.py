#!/usr/bin/env python3
"""The release pipeline's workflows: what can trigger them, what each job
waits for, where secrets go, and that the acceptance placeholder fails closed.

The workflows are read as text (no YAML library on the runners' Pythons), by
indentation, as GitHub's own layout of these files has it. Step scripts that
decide something (plan's refusal, acceptance's gate) are extracted and run.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
SCRIPTS = ROOT / "scripts"
PIPELINE = WORKFLOWS / "mac-release-pipeline.yml"
ACCEPTANCE = WORKFLOWS / "mac-acceptance.yml"
CONFIG = ROOT / ".github" / "mac-release-pipeline.json"
SHA = "a" * 64
RUN_URL = "https://github.com/omacom/omarchy-mac-installer/actions/runs/1"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


upload = load("mac_upload", SCRIPTS / "mac-upload.py")
catalog = load("catalog_release", SCRIPTS / "catalog_release.py")
fixture = load("mac_upload_fixture", SCRIPTS / "tests" / "make-mac-upload-fixture.py")


def text(path: Path) -> str:
    return path.read_text()


def lines(path: Path) -> list[str]:
    return [line for line in text(path).splitlines() if not line.lstrip().startswith("#")]


def triggers(path: Path) -> set[str]:
    found, inside = set(), False
    for line in lines(path):
        if line == "on:":
            inside = True
        elif inside and line and not line.startswith(" "):
            break
        elif inside and re.match(r"^  [a-z_]+:", line):
            found.add(line.strip().split(":")[0])
    return found


def jobs(path: Path) -> dict[str, str]:
    """Each job's lines, keyed by job id."""
    found: dict[str, list[str]] = {}
    current, inside = None, False
    for line in lines(path):
        if line == "jobs:":
            inside = True
        elif inside and line and not line.startswith(" "):
            break
        elif inside and (match := re.match(r"^  ([a-z][a-z0-9-]*):$", line)):
            current = match.group(1)
            found[current] = []
        elif inside and current:
            found[current].append(line)
    return {job: "\n".join(body) for job, body in found.items()}


def field(job: str, name: str) -> str:
    match = re.search(rf"^    {re.escape(name)}: (.*)$", job, re.M)
    return match.group(1) if match else ""


def step_script(path: Path, step: str) -> str:
    """The run block of the step named STEP, dedented."""
    body = text(path).splitlines()
    start = body.index(f"      - name: {step}")
    run = next(index for index in range(start + 1, len(body)) if body[index].strip() == "run: |")
    script = []
    for line in body[run + 1:]:
        if line.strip() and not line.startswith(" " * 10):
            break
        script.append(line)
    return textwrap.dedent("\n".join(script))


class Triggers(unittest.TestCase):
    EXPECTED = {
        "mac-release-pipeline.yml": {"workflow_dispatch"},
        "mac-acceptance.yml": {"workflow_call"},
        "mac-image.yml": {"workflow_call", "workflow_dispatch"},
        "mac-upload.yml": {"workflow_call", "workflow_dispatch"},
        "mac-catalog.yml": {"workflow_call", "workflow_dispatch"},
        "edge-dispatch.yml": {"repository_dispatch"},
    }

    def test_no_pipeline_workflow_runs_for_pull_requests_pushes_or_other_runs(self):
        for name, expected in self.EXPECTED.items():
            with self.subTest(name):
                self.assertEqual(triggers(WORKFLOWS / name), expected)

    def test_edge_publishes_start_the_pipeline_not_the_bare_build(self):
        dispatch = text(WORKFLOWS / "edge-dispatch.yml")
        self.assertIn('gh workflow run mac-release-pipeline.yml --repo "$REPOSITORY" --ref "$DEFAULT_BRANCH" -f dry-run=false',
                      dispatch)
        self.assertNotRegex(dispatch, r"gh workflow run mac-image\.yml")

    def test_a_manual_run_is_a_dry_run_unless_asked(self):
        self.assertRegex(text(PIPELINE), r"dry-run:\n        description: [^\n]*\n        type: boolean\n        default: true")


class Ordering(unittest.TestCase):
    def setUp(self) -> None:
        self.jobs = jobs(PIPELINE)

    def test_the_chain_is_build_acceptance_inputs_upload_catalog(self):
        self.assertEqual(list(self.jobs), ["plan", "build", "acceptance", "catalog-inputs", "upload", "catalog"])
        self.assertEqual(field(self.jobs["build"], "needs"), "plan")
        self.assertEqual(field(self.jobs["acceptance"], "needs"), "build")
        self.assertEqual(field(self.jobs["upload"], "needs"), "[build, acceptance, catalog-inputs]")
        self.assertEqual(field(self.jobs["catalog-inputs"], "needs"), "acceptance")
        self.assertEqual(field(self.jobs["catalog"], "needs"), "[build, acceptance, upload, catalog-inputs]")
        for job, workflow in (("build", "mac-image"), ("acceptance", "mac-acceptance"), ("upload", "mac-upload"),
                              ("catalog", "mac-catalog")):
            self.assertEqual(field(self.jobs[job], "uses"), f"./.github/workflows/{workflow}.yml")

    def test_upload_and_catalog_never_run_without_passing_evidence(self):
        passed = "needs.acceptance.outputs.result == 'pass'"
        for job in ("upload", "catalog-inputs"):
            self.assertEqual(field(self.jobs[job], "if"), passed)
        self.assertEqual(field(self.jobs["catalog"], "if"), f"{passed} && !inputs.dry-run")
        for job in ("upload", "catalog-inputs", "catalog"):
            condition = field(self.jobs[job], "if")
            # A status function would let a job run after a failed need.
            self.assertNotRegex(condition, r"always\(\)|failure\(\)|cancelled\(\)|success\(\)")
        # Nothing becomes public before the catalog's inputs are known good.
        self.assertIn("catalog-inputs", field(self.jobs["upload"], "needs"))

    def test_the_acceptance_result_is_its_gate_not_a_default(self):
        acceptance = jobs(ACCEPTANCE)["acceptance"]
        self.assertIn("result: ${{ steps.gate.outputs.result }}", acceptance)
        self.assertIn("value: ${{ jobs.acceptance.outputs.result }}", text(ACCEPTANCE))

    def test_the_catalog_takes_what_the_upload_verified(self):
        upload_job, catalog_job = self.jobs["upload"], self.jobs["catalog"]
        for line in ("image-sha256: ${{ needs.build.outputs.sha256 }}", "dry-run: ${{ inputs.dry-run }}",
                     "evidence-artifact: ${{ needs.acceptance.outputs.evidence-artifact }}",
                     "make-public: ${{ !inputs.dry-run }}"):
            self.assertIn(line, upload_job)
        for line in ("image: ${{ needs.build.outputs.name }}", "payload: ${{ needs.upload.outputs.payload }}",
                     "installer-data: ${{ needs.upload.outputs.installer-data }}",
                     "evidence: ${{ needs.upload.outputs.evidence }}", "channel: edge", "dry-run: false"):
            self.assertIn(line, catalog_job)

    def test_called_workflows_declare_what_the_pipeline_reads(self):
        for workflow, outputs in (("mac-image.yml", ("name", "payload", "sha256")),
                                  ("mac-acceptance.yml", ("result", "evidence-artifact")),
                                  ("mac-upload.yml", ("payload", "installer-data", "evidence"))):
            for output in outputs:
                with self.subTest(f"{workflow} {output}"):
                    self.assertRegex(text(WORKFLOWS / workflow),
                                     rf"\n      {re.escape(output)}:\n        description: [^\n]*\n        value: \$\{{\{{ jobs\.")


class Secrets(unittest.TestCase):
    def test_secrets_are_inherited_only_into_the_catalog_signer(self):
        # The pipeline passes the key by name. A workflow may forward what it
        # was given with inherit only into the signing workflow (mac-catalog.yml
        # into mac-catalog-sign.yml, once that split lands).
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            for job, body in jobs(workflow).items():
                if re.search(r"(?m)^    secrets:\s*inherit", body):
                    with self.subTest(f"{workflow.name} {job}"):
                        self.assertEqual(field(body, "uses"), "./.github/workflows/mac-catalog-sign.yml")

    def test_the_pipeline_passes_only_the_catalog_key_and_only_to_the_catalog(self):
        pipeline_jobs = jobs(PIPELINE)
        for job, body in pipeline_jobs.items():
            with self.subTest(job):
                expected = ["CATALOG_SIGNING_KEY: ${{ secrets.CATALOG_SIGNING_KEY }}"] if job == "catalog" else []
                self.assertEqual([line.strip() for line in body.splitlines() if "secrets." in line], expected)
                self.assertNotIn("environment:", body)

    def test_the_key_is_read_only_in_the_catalog_release_environment(self):
        # Wherever the signing job lives, only a job in catalog-release reads
        # the key; other jobs may only pass it on to a called workflow.
        readers = []
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            for job, body in jobs(workflow).items():
                if "secrets.CATALOG_SIGNING_KEY" not in body:
                    continue
                with self.subTest(f"{workflow.name} {job}"):
                    if field(body, "uses").startswith("./.github/workflows/"):
                        self.assertRegex(body, r"(?m)^      CATALOG_SIGNING_KEY: \$\{\{ secrets\.CATALOG_SIGNING_KEY \}\}$")
                    else:
                        self.assertEqual(field(body, "environment"), "catalog-release")
                        readers.append(f"{workflow.name} {job}")
        self.assertEqual(len(readers), 1, readers)

    def test_build_and_acceptance_hold_no_secrets(self):
        for workflow in ("mac-image.yml", "mac-acceptance.yml"):
            with self.subTest(workflow):
                self.assertNotIn("secrets.", text(WORKFLOWS / workflow))

    def test_a_real_upload_runs_in_the_publish_environment(self):
        self.assertEqual(field(jobs(WORKFLOWS / "mac-upload.yml")["upload"], "environment"),
                         "${{ !inputs.dry-run && 'publish' || '' }}")


class Hardening(unittest.TestCase):
    PIPELINE_FILES = ("mac-release-pipeline.yml", "mac-acceptance.yml", "mac-image.yml", "mac-upload.yml",
                      "mac-catalog.yml", "edge-dispatch.yml")

    def test_every_action_is_pinned_to_a_commit(self):
        for workflow in sorted(WORKFLOWS.glob("*.yml")):
            for line in lines(workflow):
                if "uses: " not in line:
                    continue
                with self.subTest(f"{workflow.name}: {line.strip()}"):
                    target = line.split("uses: ", 1)[1]
                    if target.startswith("./"):
                        self.assertRegex(target, r"^\./\.github/workflows/[a-z0-9-]+\.yml$")
                        self.assertTrue((ROOT / target).is_file())
                    else:
                        self.assertRegex(target, r"^[A-Za-z0-9-]+/[A-Za-z0-9._/-]+@[0-9a-f]{40} # v[0-9]")

    def test_permissions_start_from_nothing_and_every_job_names_its_own(self):
        self.assertIn("\npermissions:\n  contents: read\n", text(WORKFLOWS / "mac-image.yml"))
        for name in ("mac-upload.yml", "mac-catalog.yml", "edge-dispatch.yml"):
            self.assertIn("\npermissions: {}\n", text(WORKFLOWS / name))
        for name in ("mac-release-pipeline.yml", "mac-acceptance.yml"):
            with self.subTest(name):
                self.assertIn("\npermissions: {}\n", text(WORKFLOWS / name))
                for job, body in jobs(WORKFLOWS / name).items():
                    self.assertRegex(body, r"(?m)^    permissions:", f"{name} job {job} names no permissions")

    def test_no_workflow_checks_out_with_persisted_credentials(self):
        for name in self.PIPELINE_FILES:
            body = text(WORKFLOWS / name)
            with self.subTest(name):
                self.assertEqual(body.count("uses: actions/checkout@"), body.count("persist-credentials: false"))


@unittest.skipIf(shutil.which("jq") is None, "jq is required")
class Placeholder(unittest.TestCase):
    """Until ticket 06 the acceptance job writes a failing record and fails."""

    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="omarchy-release-pipeline-"))
        self.addCleanup(shutil.rmtree, self.directory)
        self.record = self.directory / "acceptance-evidence.json"

    def write_placeholder_record(self) -> dict:
        subprocess.run([sys.executable, "-I", str(SCRIPTS / "mac-acceptance-placeholder.py"), "--sha256", SHA,
                        "--run-url", RUN_URL, "--out", str(self.record)], check=True, capture_output=True)
        return json.loads(self.record.read_text())

    def gate(self) -> tuple[int, str]:
        output = self.directory / "output"
        output.write_text("")
        env = {"PATH": os.environ["PATH"], "IMAGE": "omarchy-mac-edge-2026.10.14-0123456789ab", "IMAGE_SHA256": SHA,
               "RECORD": str(self.record), "GITHUB_OUTPUT": str(output),
               "GITHUB_STEP_SUMMARY": str(self.directory / "summary")}
        result = subprocess.run(["bash", "--noprofile", "--norc", "-c", step_script(ACCEPTANCE, "Stop unless the image passed")],
                                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
        self.stderr = result.stderr
        return result.returncode, output.read_text()

    def test_the_placeholder_record_fails_and_names_the_image(self):
        record = self.write_placeholder_record()
        self.assertEqual((record["result"], record["image_sha256"], record["run_url"]), ("fail", SHA, RUN_URL))
        self.assertEqual(record["reason"], "acceptance not implemented")

    def test_both_evidence_gates_refuse_the_placeholder_record(self):
        record = self.write_placeholder_record()
        self.assertRaises(upload.Refusal, upload.check_evidence, record, SHA)
        self.assertRaisesRegex(catalog.Refusal, "did not pass", catalog.check_evidence, record, SHA)

    def test_the_acceptance_job_fails_closed_on_the_placeholder_record(self):
        self.write_placeholder_record()
        status, output = self.gate()
        self.assertNotEqual(status, 0)
        self.assertEqual(output, "result=fail\n", self.stderr)

    def test_the_gate_passes_only_a_passing_record_for_the_image(self):
        self.record.write_text(json.dumps(fixture.evidence(SHA, RUN_URL)))
        self.assertEqual(self.gate(), (0, "result=pass\n"), self.stderr)
        self.record.write_text(json.dumps(fixture.evidence("b" * 64, RUN_URL)))
        self.assertEqual(self.gate()[1], "result=fail\n", self.stderr)

    def test_the_workflow_writes_the_placeholder_record_before_the_gate(self):
        body = text(ACCEPTANCE)
        write = body.index("python3 -I scripts/mac-acceptance-placeholder.py")
        keep = body.index("name: mac-acceptance-evidence")
        gate = body.index("- name: Stop unless the image passed")
        self.assertLess(write, keep)
        self.assertLess(keep, gate)


class Plan(unittest.TestCase):
    def plan(self, dry_run: str, ref: str, event: str = "workflow_dispatch") -> int:
        env = {"PATH": os.environ["PATH"], "DRY_RUN": dry_run, "GITHUB_REF": ref, "EVENT": event}
        return subprocess.run(["bash", "--noprofile", "--norc", "-c", step_script(PIPELINE, "Refuse a real run off main")], env=env,
                              stdin=subprocess.DEVNULL, capture_output=True).returncode

    def test_the_plan_reads_the_runs_own_dry_run_input(self):
        plan = jobs(PIPELINE)["plan"]
        self.assertIn("DRY_RUN: ${{ inputs.dry-run }}", plan)
        self.assertIn("EVENT: ${{ github.event_name }}", plan)

    def test_a_real_run_starts_only_from_main(self):
        self.assertEqual(self.plan("false", "refs/heads/main"), 0)
        self.assertNotEqual(self.plan("false", "refs/heads/feature"), 0)
        self.assertNotEqual(self.plan("false", "refs/tags/v2.2.0"), 0)
        self.assertEqual(self.plan("true", "refs/heads/feature"), 0)

    def test_pull_requests_never_run_it(self):
        for event in ("pull_request", "pull_request_target"):
            self.assertNotEqual(self.plan("true", "refs/heads/main", event), 0)


class Config(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads(CONFIG.read_text())

    def test_the_engine_is_the_one_the_app_pins(self):
        engine = self.config["engine"]
        build_app = (ROOT / "Packaging" / "build-app.sh").read_text()
        self.assertIn(f'engine_file_name="{engine["fileName"]}"', build_app)
        self.assertIn(f'engine_digest="{engine["sha256"]}"', build_app)
        lock = json.loads((ROOT / "Engine" / "source-lock.json").read_text())
        self.assertEqual((lock["validation_artifact"]["filename"], lock["validation_artifact"]["size_bytes"],
                          lock["validation_artifact"]["sha256"], lock["downstream_overlay"]["version"]),
                         (engine["fileName"], engine["sizeBytes"], engine["sha256"], engine["version"]))
        template = json.loads((SCRIPTS / "release-inputs.template.json").read_text())
        self.assertEqual(engine["downstreamRevision"], template["downstream_revision"])
        catalog.check_engine(engine)

    def test_the_app_block_is_unset_or_one_the_catalog_takes(self):
        self.assertEqual(set(self.config), {"engine", "installer"})
        if self.config["installer"] is not None:
            catalog.check_installer(self.config["installer"], self.config["engine"]["version"])


if __name__ == "__main__":
    unittest.main()
