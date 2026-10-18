#!/usr/bin/env python3
"""Check the CI-built native Recovery app through the Neo image contract.

Packages and filesystems are disposable signed fixtures; the Recovery app is
the real macOS build artifact. This is source integration, not a boot test or
a channel package build, and never changes production trust roots.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

import fixtures
import test_mac_image_check as image

recovery = fixtures.load('native_recovery_lane', 'builder/recovery_app.py')
channel = fixtures.load('native_recovery_channel', 'builder/channel_set.py')
inspection = fixtures.load('native_recovery_inspection', 'builder/inspection.py')


def check(template, output):
    output.mkdir(parents=True, exist_ok=False)
    before = recovery.validate(template)
    with tempfile.TemporaryDirectory(prefix='neo-recovery-check-') as temporary:
        work = Path(temporary)
        signers = []
        try:
            for role in ('candidate', 'channel'):
                signers.append(fixtures.Signer(work / role))
            trust = fixtures.channel_trust(work, *signers)
            database, digest = fixtures.make_channel(work / 'edge', signers[1], hardware_profile='neo')
            summary = channel.snapshot(database, digest, 'edge', work / 'edge/packages',
                                       work / 'frozen', trust, 'neo')
            assert {'omarchy-dev', 'omarchy-settings-dev', 'aura', 'uboot-aurora'} <= {
                package['name'] for package in summary['packages']}
            root = work / 'root'
            fixtures.make_root(root, work / 'frozen')
            # Fixture directories stand in for Btrfs subvolumes in this unprivileged lane.
            inspection.is_subvolume = lambda path: path == root / '.snapshots'
            inspection.OWNER_UID = os.geteuid()
            report = inspection.inspect(root, work / 'frozen', 'edge', trust=trust)
            (output / 'neo-root-inspection.json').write_text(json.dumps(report, indent=2) + '\n')
            if report['result'] != 'passed':
                raise ValueError('Neo fixture inspection failed: ' + str(report['failed_checks']))
            frozen = work / 'recovery-frozen'
            recovery.snapshot(template, frozen, before)
            inputs = dict(image.CHANNEL_INPUTS, hardware_profile='neo', recovery_app_manifest_sha256=before)
            image.build(output, inputs=inputs, hardware_profile='neo', recovery_app=frozen,
                        pin='', sources=(('omarchy-mac', fixtures.SOURCE),),
                        packages=image.CHANNEL_PACKAGES, candidates=image.CHANNEL_PACKAGES[:2])
            image.check.check_provenance('edge', output)
            image.check.check_descriptor('edge', output)
            assert recovery.validate(template) == before
            payload = output / image.NAME
            payload.unlink()  # Synthetic image must not be mistaken for a deployable artifact.
            (output / 'check-only.json').write_text(json.dumps({
                'result': 'passed', 'hardware_profile': 'neo', 'nativeRecoveryManifestSha256': before,
                'sourceCommit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip(),
                'scope': 'real native Recovery artifact; signed fixture packages and filesystems',
                'hardwareTested': False, 'channelPackagesBuilt': False,
            }, indent=2) + '\n')
        finally:
            for signer in signers:
                signer.close()
            # The frozen package set is intentionally read-only.
            for directory, _, _ in os.walk(work):
                Path(directory).chmod(0o700)
    print('PASS: CI-built native Recovery app and Neo image contract (check only)')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--recovery-app', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    check(args.recovery_app, args.output)
