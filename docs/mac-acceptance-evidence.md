# Mac acceptance evidence record

The VM acceptance job writes one evidence record per tested image. The upload job (`mac-upload.yml`) and the catalog job refuse an image unless its record passes. `scripts/mac-upload.py check-evidence --evidence FILE --sha256 HEX` is the check; both jobs use it.

## Format

A JSON object in a file named `acceptance-evidence.json`, uploaded as its own workflow artifact (the upload job's `evidence-artifact` input names it):

```json
{
  "image_sha256": "<sha256 of the image zip, 64 lowercase hex digits>",
  "result": "pass",
  "scenarios": [
    {"name": "first-boot", "result": "pass"},
    {"name": "encrypted-conversion", "result": "pass"},
    {"name": "second-boot", "result": "pass"}
  ],
  "harness_commit": "<40-hex commit of the omarchy-mac-pkgs VM harness>",
  "runner": "<runner label and host, for example namespace-profile-... M4>",
  "run_url": "https://github.com/omacom/omarchy-mac-installer/actions/runs/<id>"
}
```

- `image_sha256`: the digest of the exact zip the harness booted. It must equal the upload's expected sha256.
- `result`: `pass` or `fail`. A failing run still writes a record, with `fail`.
- `scenarios`: one entry per scenario run, each `pass` or `fail`. A passing record has every scenario `pass` and includes at least `first-boot`, `encrypted-conversion` and `second-boot`.
- `harness_commit`, `runner`, `run_url`: where and with what the scenarios ran.

Other keys are allowed and ignored, so the harness can add detail (log digests, timings) without a format change.

## Where a record is trusted from

A real upload takes the record only from a run of this repository's `main` branch that was not triggered by a pull request (`check-run`). Dry runs accept any record they're given and still check it.
