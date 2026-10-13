#!/usr/bin/env python3
"""The builder's payload is the one the installer engine takes, read from this repository: the
installer_data.json build-mac-image writes and mac-image-check expects is the engine's bundled
Engine/installer_data.json for that payload, and the engine's full-OS package admission
(omarchy_asahi.AsahiStage1Adapter.preflight) takes the payload mac-image-check passes and refuses
nothing mac-image-check lets through. A change on either side fails here."""
import importlib.machinery
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import fixtures  # noqa: E402

ROOT = fixtures.ROOT
ENGINE = ROOT.parent / "Engine"
NAME = "omarchy-2026.10.14-aarch64-apple-silicon-mac-edge-os-package.zip"
LAB_NAME = "omarchy-2026.10.14-aarch64-apple-silicon-mac-edge-lab-os-package.zip"

_loader = importlib.machinery.SourceFileLoader("mac_image_check", str(ROOT / "bin/mac-image-check"))
check = importlib.util.module_from_spec(importlib.util.spec_from_loader("mac_image_check", _loader))
_loader.exec_module(check)


# The engine runs over pinned asahi-installer modules (Engine/source-lock.json, v0.9.2) that this
# repository does not carry. Stand-ins give it what preflight reads from them; psize and min_size are
# upstream's, so the partition sizes the metadata declares are read the way the Mac reads them.
def psize(value, align=None):
    """asahi-installer v0.9.2 src/util.py psize, without alignment."""
    value = value.upper().replace(" ", "")
    base = 1000
    if value[-2] == "I":
        base = 1024
        value = value[:-2] + value[-1]
    for suffix, power in {"TB": 4, "GB": 3, "MB": 2, "KB": 1, "B": 0, "": 0}.items():
        if value.endswith(suffix):
            return int(float(value[:-len(suffix)]) * (base ** power))
    return None


class OSInstaller:
    """What preflight reads of asahi-installer v0.9.2 src/osinstall.py OSInstaller."""

    def __init__(self, dutil, data, template):
        self.template = template

    @property
    def min_size(self):
        return sum(-(-psize(part["size"]) // (1 << 20)) << 20 for part in self.template["partitions"])

    @property
    def min_recommended_size(self):
        expandable = any(part.get("expand", False) for part in self.template["partitions"])
        return self.min_size * 2 if expandable else self.min_size


class StubInstaller:
    def __init__(self, sysinfo, dutil, osinfo):
        self.ipsw = None

    def install_files(self, current_os):
        pass

    def load_ipsw(self, ipsw):
        self.ipsw = ipsw


sys.modules["asahi_firmware"] = SimpleNamespace(core=SimpleNamespace(FWPackage=object))
sys.modules["osinstall"] = SimpleNamespace(OSInstaller=OSInstaller, psize=psize)
sys.modules["stub"] = SimpleNamespace(StubInstaller=StubInstaller)
sys.modules["util"] = SimpleNamespace(align_down=lambda value, align=16384: value & ~(align - 1))
sys.path.insert(0, str(ENGINE / "overlay/src"))
import omarchy_asahi  # noqa: E402


def engine_metadata(package: str) -> dict:
    """The engine's bundled installer_data.json, as the engine loads it, naming PACKAGE."""
    metadata = omarchy_asahi.load_metadata(str(ENGINE / "installer_data.json"))
    templates = [t for t in metadata["os_list"] if t.get("omarchy_target") == omarchy_asahi.TARGET]
    assert len(templates) == 1, "Engine/installer_data.json has exactly one full-OS target"
    templates[0]["package"] = package
    return metadata


def written_metadata(package: str, out: Path) -> dict:
    """The installer_data.json build-mac-image writes for PACKAGE."""
    subprocess.run(["bash", "-c", 'source "$1" && write_installer_data "$2" "$3"', "bash",
                    str(ROOT / "bin/build-mac-image"), package, str(out)], check=True)
    return json.loads(out.read_text())


def engine_admits(metadata: Path, payload: Path) -> list:
    """Runs the engine's preflight on METADATA and PAYLOAD; returns the firmware it chose from."""
    chosen = []
    installer = SimpleNamespace(dutil=None, sysinfo=None, osinfo=None,
                                choose_ipsw=lambda supported: chosen.append(supported) or "ipsw")
    adapter = omarchy_asahi.AsahiStage1Adapter(installer=installer, metadata_path=str(metadata),
                                               payload_path=str(payload), stub_size=0)
    try:
        adapter.preflight(SimpleNamespace(length_bytes=1 << 50))
    finally:
        if adapter.osins is not None and getattr(adapter.osins, "pkg", None) is not None:
            adapter.osins.pkg.close()
    return chosen


class EngineContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_builders_metadata_is_the_engines(self):
        for name in (NAME, LAB_NAME):
            with self.subTest(name=name):
                expected = engine_metadata(name)
                self.assertEqual(check.expected_metadata(name), expected,
                                 "mac-image-check expects the engine's installer_data.json")
                self.assertEqual(written_metadata(name, self.out / "installer_data.json"), expected,
                                 "build-mac-image writes the engine's installer_data.json")

    def test_the_engine_admits_the_builders_payload(self):
        for name, profile in ((NAME, "release"), (LAB_NAME, "lab")):
            with self.subTest(name=name):
                payload = self.out / name
                fixtures.write_payload(payload, check.BOOT_UUID, check.ROOT_UUID)
                metadata = self.out / "installer_data.json"
                written_metadata(name, metadata)
                metadata.chmod(0o644)
                check.check_payload("edge", payload, metadata, profile)
                chosen = engine_admits(metadata, payload)
                self.assertEqual(chosen, [list(check.expected_metadata(name)["os_list"][0]["supported_fw"])])

    def test_the_builder_refuses_what_the_engine_refuses(self):
        # Every way a member of the payload build-mac-image packages can fall short; whatever the
        # engine refuses, the builder's check must refuse before the payload is published.
        fixtures.write_payload(self.out / "full.zip", check.BOOT_UUID, check.ROOT_UUID)
        with zipfile.ZipFile(self.out / "full.zip") as archive:
            members = sorted(info.filename for info in archive.infolist() if not info.is_dir())
        changes = [{"omit": (member,)} for member in members] + [{"empty": (member,)} for member in members]
        # Grown by a byte, a sector and a page: off every alignment either side might ask of an image.
        changes += [{"grow": {member: count}} for member in members for count in (1, 512, 4096)]
        metadata = self.out / "installer_data.json"
        written_metadata(NAME, metadata)
        metadata.chmod(0o644)
        payload = self.out / NAME
        engine_refused = 0
        for change in changes:
            with self.subTest(**{k: str(v) for k, v in change.items()}):
                fixtures.write_payload(payload, check.BOOT_UUID, check.ROOT_UUID, **change)
                try:
                    engine_admits(metadata, payload)
                except omarchy_asahi.AsahiAdapterError:
                    engine_refused += 1
                else:
                    continue
                with self.assertRaises(check.CheckError, msg=f"the engine refuses {change}; the builder must too"):
                    check.check_payload("edge", payload, metadata)
        self.assertGreater(engine_refused, 0, "the engine refuses some short payloads")


if __name__ == "__main__":
    unittest.main()
