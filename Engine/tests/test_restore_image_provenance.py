#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ENGINE = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('restore_provenance', ENGINE / 'verify-restore-image.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RestoreProvenanceTests(unittest.TestCase):
    def test_source_and_binary_are_bound_to_build_record(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Path(directory)
            root = engine / 'RestoreImage'
            (root / 'Sources').mkdir(parents=True)
            binary = engine / 'overlay/bin/omarchy-restore-image'
            binary.parent.mkdir(parents=True)
            binary.write_bytes(b'synthetic executable')
            paths = [root / 'Package.swift', root / 'build.sh', root / 'Sources/main.swift']
            for path in paths:
                path.write_text('synthetic source')
            record = {
                'schema': 1,
                'reference_repository': 'https://github.com/erykwieliczko/macos-firmware-extract',
                'reference_commit': '71b413af680edec1808a709cb7addfbde5c21cfc',
                'target': 'arm64-apple-macosx14.0',
                'reproducibility': 'two-identical-clean-builds-same-host-pinned-toolchain',
                'swift': 'recorded compiler', 'sdk': 'recorded SDK',
                'sources': [{'path': str(p.relative_to(root)), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()}
                            for p in paths],
                'binary_size': binary.stat().st_size,
                'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
            }
            (root / 'build-record.json').write_text(json.dumps(record))
            MODULE.verify(engine)
            paths[-1].write_text('changed')
            with self.assertRaisesRegex(ValueError, 'source differs'):
                MODULE.verify(engine)
            paths[-1].write_text('synthetic source')
            binary.write_bytes(b'changed executable')
            with self.assertRaisesRegex(ValueError, 'differs from its Swift build'):
                MODULE.verify(engine)

    def test_production_helper_has_checked_in_source(self):
        # This checks the actual source/build identity independently of the archive lock.
        MODULE.verify(ENGINE)


if __name__ == '__main__':
    unittest.main()
