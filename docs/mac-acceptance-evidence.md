# Mac acceptance evidence record

The VM acceptance job writes one evidence record per tested image. The upload job (`mac-upload.yml`) and the catalog job refuse an image unless its record passes. `scripts/mac-upload.py check-evidence --evidence FILE --sha256 HEX` is the upload's check, and the acceptance job's gate uses it too. The catalog checks with `catalog_release.py check-evidence`, which takes every record the upload takes (it also allows `skip` for scenarios outside the required three).

## Format

A JSON object in a file named `acceptance-evidence.json`, uploaded as its own workflow artifact (the upload job's `evidence-artifact` input names it):

```json
{
  "image_sha256": "<sha256 of the image zip, 64 lowercase hex digits>",
  "result": "pass",
  "scenarios": [
    {"name": "first-boot", "result": "pass"},
    {"name": "conversion", "result": "pass"},
    {"name": "second-boot", "result": "pass"}
  ],
  "harness_commit": "<40-hex commit of the omarchy-mac-pkgs VM harness>",
  "runner": "<runner label and host, for example namespace-profile-... M4>",
  "run_url": "https://github.com/omacom/omarchy-mac-installer/actions/runs/<id>"
}
```

- `image_sha256`: the digest of the exact zip the harness booted. It must equal the upload's expected sha256.
- `result`: `pass` or `fail`. A failing run still writes a record, with `fail`.
- `scenarios`: one entry per scenario run, each `pass` or `fail`. A passing record has every scenario `pass` and includes at least `first-boot`, `conversion` and `second-boot`.
- `harness_commit`, `runner`, `run_url`: where and with what the scenarios ran. `run_url` is the GitHub Actions run that uploaded the record's artifact; a real upload refuses a record that names any other run.

Other keys are allowed and ignored, so the harness can add detail (log digests, timings) without a format change.

## Where a record is trusted from

A real upload takes the record only from a run of this repository's `main` branch that was not triggered by a pull request and, unless it is the upload's own run, that succeeded (`check-run`). Any such run can upload an artifact, so the acceptance job should also attest the record; binding the record to the acceptance workflow is left to ticket 06. Dry runs accept a record from any run they're given and still check it.
