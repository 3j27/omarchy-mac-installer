#!/usr/bin/env python3
"""Write the failing acceptance evidence record of the placeholder acceptance job.

Until ticket 06 runs the VM harness, `.github/workflows/mac-acceptance.yml`
writes this record for every image: result `fail`, no scenarios, and the
reason. It has the documented shape (docs/mac-acceptance-evidence.md) so the
rest of the pipeline handles it like any failing run, and both evidence gates
(`mac-upload.py check-evidence`, `catalog_release.py check-evidence`) refuse it.
Ticket 06 deletes this script.

Usage:
  mac-acceptance-placeholder.py --sha256 HEX --run-url URL --out FILE
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REASON = "acceptance not implemented"


def record(sha256: str, run_url: str) -> dict:
    return {
        "image_sha256": sha256,
        "result": "fail",
        "scenarios": [],
        "harness_commit": "0" * 40,
        "runner": "placeholder (no VM ran)",
        "run_url": run_url,
        "reason": REASON,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{64}", args.sha256) or not args.run_url.startswith("https://"):
        print("mac-acceptance-placeholder: need a 64-hex sha256 and an https run URL", file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(record(args.sha256, args.run_url), indent=2) + "\n")
    print(f"evidence for {args.sha256}: fail ({REASON})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
