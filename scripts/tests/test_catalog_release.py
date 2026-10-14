#!/usr/bin/env python3
"""The Mac catalog release: build, evidence gate, CI signing, hosts, dry run.

Driven through scripts/catalog_release.py's command line, as the workflow
runs it, with a throwaway Ed25519 key. Signing needs OpenSSL 3; on a Mac the
signatures are also checked with catalog-signing.swift verify.
"""
from __future__ import annotations

import base64
import contextlib
import datetime
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "catalog_release.py"
SIGNING_TOOL = ROOT / "scripts" / "catalog-signing.swift"
WORKFLOW = ROOT / ".github" / "workflows" / "mac-catalog.yml"
FIXTURE = ROOT / "Tests" / "OmarchyAppleInstallerTrustCoreTests" / "Fixtures" / "ci-signed-catalog"

spec = importlib.util.spec_from_file_location("catalog_release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

try:
    OPENSSL = release.find_openssl()
except release.Refusal:
    OPENSSL = None
if OPENSSL is None and os.environ.get("CI"):
    raise SystemExit("CI must provide OpenSSL 3 for the catalog signing tests")

IMAGE = "omarchy-mac-edge-2026.10.14-0123456789ab"
NOW = "2026-10-14T00:00:00Z"
SEQUENCE = int(datetime.datetime(2026, 10, 14, tzinfo=datetime.timezone.utc).timestamp())
REVISION = "1" * 40


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def release_base(tag: str) -> str:
    return f"https://github.com/{release.stopgap_repo()}/releases/download/{tag}"


def artifact(tag: str, name: str, data: bytes) -> dict:
    return {"url": f"{release_base(tag)}/{name}", "fileName": name, "sizeBytes": len(data), "sha256": sha(data)}


PAYLOAD_PARTS = [b"payload-part-zero" * 50, b"payload-part-one" * 40]
PAYLOAD = b"".join(PAYLOAD_PARTS)
ENGINE = b"engine-tarball" * 30
METADATA = b'{"os_list": []}\n'
ZIP = f"{IMAGE}.zip"


def payload_document() -> dict:
    document = artifact(IMAGE, ZIP, PAYLOAD)
    document["parts"] = [
        artifact(IMAGE, f"{ZIP}.part{index:02d}", part) for index, part in enumerate(PAYLOAD_PARTS)
    ]
    return document


def engine_document() -> dict:
    return dict(
        artifact("engine-v0.9.2-omarchy.28", "installer-v0.9.2-omarchy.28.tar.gz", ENGINE),
        version="v0.9.2-omarchy.28",
        downstreamRevision=REVISION,
    )


def evidence_document(image_sha256: str = sha(PAYLOAD)) -> dict:
    return {
        "image_sha256": image_sha256,
        "result": "pass",
        "scenarios": [
            {"name": "first-boot", "result": "pass"},
            {"name": "conversion", "result": "pass"},
            {"name": "second-boot", "result": "pass"},
        ],
        "harness_commit": "2" * 40,
        "runner": "namespace linux-on-apple arm64",
        "run_url": "https://github.com/example/acceptance/actions/runs/1",
    }


def generate_key(directory: Path) -> tuple[str, Path]:
    """A throwaway key: its base64 seed and its trust-root file."""
    der = subprocess.run(
        [OPENSSL, "genpkey", "-algorithm", "ed25519", "-outform", "DER"], capture_output=True, check=True
    ).stdout
    seed = der[-32:]
    trust_root = directory / "trust-root.ed25519.pub"
    trust_root.write_bytes(release.openssl_public_key(OPENSSL, seed))
    return base64.b64encode(seed).decode(), trust_root


@unittest.skipIf(OPENSSL is None, "OpenSSL 3 is required")
class CatalogReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = Path(tempfile.mkdtemp(prefix="omarchy-catalog-release-"))
        self.addCleanup(shutil.rmtree, self.directory)
        self.key, self.trust_root = generate_key(self.directory)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        self.calls = self.directory / "calls.log"
        self.calls.write_text("")
        # Anything that would reach a real host records itself and fails.
        for tool in ("gh", "curl", "aws", "wget"):
            shim = self.bin / tool
            shim.write_text(f'#!/bin/bash\nprintf "%s\\n" "{tool} $*" >>"{self.calls}"\nexit 97\n')
            shim.chmod(0o755)
        self.environment = dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", OMARCHY_OPENSSL=OPENSSL)
        self.environment.pop("CATALOG_SIGNING_KEY", None)

    def write(self, name: str, document: object) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(document))
        return path

    def run_tool(self, *arguments: str, environment: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            capture_output=True, text=True, env=environment or self.environment,
        )

    def prepare(self, evidence: object = "default", payload: dict | None = None, out: str = "out",
                *extra: str) -> subprocess.CompletedProcess:
        arguments = [
            "prepare", "--channel", "edge", "--image", IMAGE,
            "--payload", str(self.write("payload.json", payload or payload_document())),
            "--engine", str(self.write("engine.json", engine_document())),
            "--installer-data", str(self.write("metadata.json", artifact(IMAGE, "installer_data.json", METADATA))),
            "--now", NOW, "--out", str(self.directory / out), *extra,
        ]
        if evidence is not None:
            document = evidence_document() if evidence == "default" else evidence
            arguments += ["--evidence", str(self.write("evidence.json", document))]
        return self.run_tool(*arguments)

    def sign(self, catalog: Path, key: str | None = None, trust_root: Path | None = None,
             out: str = "signed") -> subprocess.CompletedProcess:
        environment = dict(self.environment, CATALOG_SIGNING_KEY=self.key if key is None else key)
        return self.run_tool(
            "sign", "--key-env", "CATALOG_SIGNING_KEY", "--catalog", str(catalog),
            "--trust-root", str(trust_root or self.trust_root), "--out", str(self.directory / out),
            environment=environment,
        )

    def assert_refused(self, result: subprocess.CompletedProcess, reason: str) -> None:
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn("refused", result.stderr)
        self.assertIn(reason, result.stderr)

    def built_catalog(self) -> Path:
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        return self.directory / "out" / "catalog-edge.json"

    # ── build, sign, verify ──────────────────────────────────────────────────

    def test_round_trip_enables_every_mac_and_pins_the_digests(self) -> None:
        catalog_path = self.built_catalog()
        catalog = json.loads(catalog_path.read_text())
        supported, _ = release.supported_models.load()
        untested = release.supported_models.untested()
        self.assertEqual([model["deviceIdentifier"] for model in catalog["models"]], supported)
        self.assertEqual(catalog["schemaVersion"], 4)
        self.assertEqual(catalog["sequence"], SEQUENCE)
        self.assertNotIn("expiresAt", catalog)
        for model in catalog["models"]:
            self.assertEqual(model["status"], "enabled")
            self.assertEqual(model["payloadDigest"], f"sha256:{sha(PAYLOAD)}")
            self.assertEqual(model["engineDigest"], f"sha256:{sha(ENGINE)}")
            self.assertEqual(model["metadataDigest"], f"sha256:{sha(METADATA)}")
            self.assertEqual(model["evidenceRevision"], IMAGE)
            self.assertEqual(
                [part["sha256"] for part in model["payloadArtifact"]["parts"]],
                [f"sha256:{sha(part)}" for part in PAYLOAD_PARTS],
            )
            self.assertEqual(model.get("previewNote"), untested.get(model["deviceIdentifier"]))
        self.assertTrue(untested, "M3 Macs are marked untested")

        signed = self.sign(catalog_path)
        self.assertEqual(signed.returncode, 0, signed.stderr)
        self.assertNotIn(self.key, signed.stdout + signed.stderr)
        envelope = self.directory / "signed" / "catalog-edge.signed.json"
        verified = self.run_tool("verify-envelope", "--envelope", str(envelope), "--trust-root", str(self.trust_root),
                                 "--expect-sha256", sha(catalog_path.read_bytes()))
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn("catalog_signature=passed", verified.stdout)
        document = json.loads(envelope.read_text())
        self.assertEqual(set(document), {"schema_version", "catalog", "signature"})
        self.assertEqual(base64.b64decode(document["catalog"]), catalog_path.read_bytes())
        self.assertEqual(self.calls.read_text(), "")

        if platform.system() == "Darwin" and shutil.which("xcrun"):
            signature = self.directory / "signed" / "catalog-edge.json.sig"
            swift = subprocess.run(
                ["xcrun", "swift", str(SIGNING_TOOL), "verify", str(self.trust_root), str(catalog_path),
                 str(signature)], capture_output=True, text=True,
            )
            self.assertEqual(swift.stdout.strip(), "catalog_signature=passed", swift.stderr)

    def test_a_tampered_envelope_does_not_verify(self) -> None:
        self.assertEqual(self.sign(self.built_catalog()).returncode, 0)
        envelope = self.directory / "signed" / "catalog-edge.signed.json"
        document = json.loads(envelope.read_text())
        catalog = base64.b64decode(document["catalog"]).replace(b'"enabled"', b'"disabled"', 1)
        document["catalog"] = base64.b64encode(catalog).decode()
        envelope.write_text(json.dumps(document))
        result = self.run_tool("verify-envelope", "--envelope", str(envelope), "--trust-root", str(self.trust_root))
        self.assert_refused(result, "does not verify")

    def test_the_app_fixture_verifies_and_covers_every_mac(self) -> None:
        trust_root = (FIXTURE / "trust-root.ed25519.pub").read_bytes()
        document = release.verify_envelope((FIXTURE / "catalog-edge.signed.json").read_bytes(), trust_root, OPENSSL)
        self.assertEqual(release.supported_models.catalog_errors(document), [])

    # ── the evidence gate ────────────────────────────────────────────────────

    def test_refuses_without_evidence(self) -> None:
        self.assert_refused(self.prepare(evidence=None), "no VM evidence record")
        self.assertFalse((self.directory / "out").exists())

    def test_refuses_failed_evidence(self) -> None:
        self.assert_refused(self.prepare(evidence=dict(evidence_document(), result="fail")), "did not pass")

    def test_refuses_evidence_for_another_image(self) -> None:
        self.assert_refused(self.prepare(evidence=evidence_document("f" * 64)), "not " + sha(PAYLOAD))

    def test_refuses_evidence_missing_a_required_scenario(self) -> None:
        evidence = evidence_document()
        evidence["scenarios"] = evidence["scenarios"][:2]
        self.assert_refused(self.prepare(evidence=evidence), "no passing second-boot")

    def test_refuses_a_pass_with_a_failed_scenario(self) -> None:
        evidence = evidence_document()
        evidence["scenarios"].append({"name": "update", "result": "fail"})
        self.assert_refused(self.prepare(evidence=evidence), "scenarios failed: update")

    def test_refuses_malformed_evidence(self) -> None:
        self.assert_refused(self.prepare(evidence=dict(evidence_document(), harness_commit="main")), "harness_commit")
        self.assert_refused(self.prepare(evidence=["pass"]), "JSON object")
        missing = evidence_document()
        del missing["run_url"]
        self.assert_refused(self.prepare(evidence=missing), "no run_url")

    def test_the_signing_job_rechecks_the_evidence(self) -> None:
        catalog = self.built_catalog()
        ok = self.run_tool("check-evidence", "--catalog", str(catalog),
                           "--evidence", str(self.write("e.json", evidence_document())))
        self.assertEqual(ok.returncode, 0, ok.stderr)
        other = self.run_tool("check-evidence", "--catalog", str(catalog),
                              "--evidence", str(self.write("e.json", evidence_document("e" * 64))))
        self.assert_refused(other, "not " + sha(PAYLOAD))
        self.assert_refused(self.run_tool("check-evidence", "--catalog", str(catalog)), "no VM evidence")

    # ── inputs ───────────────────────────────────────────────────────────────

    def test_refuses_artifacts_outside_the_release_locations(self) -> None:
        payload = payload_document()
        payload["parts"][0]["url"] = payload["parts"][0]["url"].replace("github.com", "example.com")
        self.assert_refused(self.prepare(payload=payload), "not under an allowed release location")
        payload = payload_document()
        payload["url"] = f"{release_base('catalog-edge')}/{ZIP}"
        self.assert_refused(self.prepare(payload=payload), "immutable release tag")
        payload = payload_document()
        payload["url"] = payload["url"] + "?x=1"
        self.assert_refused(self.prepare(payload=payload), "plain https URL")

    def test_refuses_inconsistent_parts(self) -> None:
        payload = payload_document()
        payload["sizeBytes"] += 1
        self.assert_refused(self.prepare(payload=payload), "parts sum to")
        payload = payload_document()
        payload["parts"][1]["fileName"] = "other.zip"
        self.assert_refused(self.prepare(payload=payload), "must end with its fileName")

    def test_refuses_another_channel_or_image_name(self) -> None:
        result = self.run_tool("prepare", "--channel", "stable", "--image", IMAGE, "--payload", "x",
                               "--engine", "x", "--installer-data", "x", "--out", str(self.directory / "o"))
        self.assert_refused(result, "rc and stable come from promotion")
        with self.assertRaisesRegex(release.Refusal, "image must be"):
            release.check_image_name("omarchy-mac-edge-latest", "edge")

    # ── signing ──────────────────────────────────────────────────────────────

    def test_refuses_a_key_that_is_not_the_trust_root(self) -> None:
        other = self.directory / "other"
        other.mkdir()
        other_key, _ = generate_key(other)
        self.assert_refused(self.sign(self.built_catalog(), key=other_key), "does not match the trust root")
        self.assertFalse((self.directory / "signed").exists())

    def test_refuses_a_malformed_or_missing_key(self) -> None:
        catalog = self.built_catalog()
        self.assert_refused(self.sign(catalog, key="not-a-key"), "neither 32 raw bytes nor base64")
        self.assert_refused(self.sign(catalog, key=base64.b64encode(b"short").decode()), "not a 32-byte")
        self.assert_refused(self.sign(catalog, key=""), "empty or unset")

    def test_the_key_file_form_signs_the_same_bytes(self) -> None:
        catalog = self.built_catalog()
        key_file = self.directory / "key.raw"
        key_file.write_bytes(base64.b64decode(self.key))
        result = self.run_tool("sign", "--key-file", str(key_file), "--catalog", str(catalog),
                               "--trust-root", str(self.trust_root), "--out", str(self.directory / "from-file"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sign(catalog).returncode, 0)
        # Ed25519 is deterministic: one key, one catalog, one signature.
        self.assertEqual((self.directory / "from-file" / "catalog-edge.signed.json").read_bytes(),
                         (self.directory / "signed" / "catalog-edge.signed.json").read_bytes())

    # ── hosts and the dry run ────────────────────────────────────────────────

    def test_a_dry_run_touches_nothing_but_scratch(self) -> None:
        """The workflow's dry run: prepare, sign, publish to a scratch host."""
        scratch = self.directory / "scratch-host"
        result = self.prepare("default", None, "out", "--check-live", "--host", f"scratch:{scratch}",
                              "--trust-root", str(self.trust_root))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.sign(self.directory / "out" / "catalog-edge.json").returncode, 0)
        envelope = self.directory / "signed" / "catalog-edge.signed.json"
        published = self.run_tool("publish", "--channel", "edge", "--envelope", str(envelope),
                                  "--host", f"scratch:{scratch}", "--trust-root", str(self.trust_root))
        self.assertEqual(published.returncode, 0, published.stderr)
        self.assertEqual((scratch / "catalog-edge.signed.json").read_bytes(), envelope.read_bytes())
        self.assertEqual(self.calls.read_text(), "", "a dry run must not call any host tool")

    def test_publish_refuses_a_sequence_that_does_not_move_forward(self) -> None:
        scratch = self.directory / "scratch-host"
        self.assertEqual(self.sign(self.built_catalog()).returncode, 0)
        envelope = self.directory / "signed" / "catalog-edge.signed.json"
        arguments = ("publish", "--channel", "edge", "--envelope", str(envelope),
                     "--host", f"scratch:{scratch}", "--trust-root", str(self.trust_root))
        self.assertEqual(self.run_tool(*arguments).returncode, 0)
        self.assert_refused(self.run_tool(*arguments), f"does not follow the published {SEQUENCE}")
        (scratch / "catalog-edge.signed.json").write_text("{}")
        self.assert_refused(self.run_tool(*arguments), "does not verify")

    def test_the_github_release_host_replaces_only_the_catalog_asset(self) -> None:
        self.assertEqual(self.sign(self.built_catalog()).returncode, 0)
        envelope = self.directory / "signed" / "catalog-edge.signed.json"
        state = self.directory / "release-state"
        served = self.directory / "served"
        gh = self.bin / "gh"
        gh.write_text(f"""#!/bin/bash
printf '%s\\n' "gh $*" >>"{self.calls}"
case "$1 $2" in
  "release view") [[ -f {state} ]] && {{ cat {state}; exit 0; }}; echo "release not found" >&2; exit 1 ;;
  "release create") echo '{{"isDraft":false,"isPrerelease":true,"tagName":"catalog-edge"}}' >{state} ;;
  "release upload") cp "$4" {served} ;;
  *) exit 97 ;;
esac
""")
        host = release.GitHubReleaseHost(release.stopgap_repo())

        def fake_open(url, timeout=60):
            self.assertEqual(url, host.describe("edge"))
            if not served.exists():
                raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())
            return contextlib.closing(io.BytesIO(served.read_bytes()))

        arguments = ["publish", "--channel", "edge", "--envelope", str(envelope), "--host", "github-release",
                     "--trust-root", str(self.trust_root)]
        saved_open, saved_path, saved_ssl = release.open_url, os.environ["PATH"], os.environ.get("OMARCHY_OPENSSL")
        release.open_url = fake_open
        os.environ["PATH"] = self.environment["PATH"]
        os.environ["OMARCHY_OPENSSL"] = OPENSSL
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(release.main(arguments), 0)
            calls = self.calls.read_text().splitlines()
            self.assertTrue(calls[1].startswith("gh release create catalog-edge --prerelease --latest=false"), calls)
            self.assertIn("gh release upload catalog-edge", calls[2])
            self.assertIn("--clobber", calls[2])
            self.assertEqual(served.read_bytes(), envelope.read_bytes())
            state.write_text('{"isDraft":true,"isPrerelease":true,"tagName":"catalog-edge"}')
            served.unlink()
            with contextlib.redirect_stderr(io.StringIO()) as errors:
                self.assertEqual(release.main(arguments), 1)
            self.assertIn("must be a published prerelease", errors.getvalue())
        finally:
            release.open_url = saved_open
            os.environ["PATH"] = saved_path
            if saved_ssl is None:
                os.environ.pop("OMARCHY_OPENSSL", None)
            else:
                os.environ["OMARCHY_OPENSSL"] = saved_ssl

    # ── remote verification ──────────────────────────────────────────────────

    def test_remote_verification_checks_every_part_and_the_whole(self) -> None:
        served = {part["url"]: data for part, data in zip(payload_document()["parts"], PAYLOAD_PARTS)}
        saved = release.open_url
        release.open_url = lambda url, timeout=60: contextlib.closing(io.BytesIO(served[url]))
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                release.verify_remote_artifact(payload_document(), "payload")
            first = payload_document()["parts"][0]["url"]
            served[first] = served[first][:-1] + b"X"
            with self.assertRaisesRegex(release.Refusal, "not the pinned"):
                release.verify_remote_artifact(payload_document(), "payload")
            served[first] = PAYLOAD_PARTS[0] + b"extra"
            with self.assertRaisesRegex(release.Refusal, "more than its pinned"):
                release.verify_remote_artifact(payload_document(), "payload")
        finally:
            release.open_url = saved

    # ── the workflow ─────────────────────────────────────────────────────────

    def test_the_workflow_keeps_the_key_in_one_approved_step(self) -> None:
        text = WORKFLOW.read_text()
        self.assertNotIn("pull_request", text)
        self.assertEqual(text.count("secrets."), 1)
        self.assertEqual(text.count("environment: catalog-release"), 1)
        sign_job = text.split("\n  sign:\n")[1].split("\n  publish:\n")[0]
        self.assertIn("environment: catalog-release", sign_job)
        self.assertIn("secrets.CATALOG_SIGNING_KEY", sign_job)
        self.assertIn("contents: read", sign_job)
        publish_job = text.split("\n  publish:\n")[1]
        self.assertIn("if: ${{ !inputs.dry-run }}", publish_job)
        self.assertNotIn("secrets.", publish_job)


if __name__ == "__main__":
    unittest.main()
