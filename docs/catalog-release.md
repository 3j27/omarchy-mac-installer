# Mac catalog release

`.github/workflows/mac-catalog.yml` signs and publishes a channel catalog that pins one uploaded image, its engine and its `installer_data.json` by digest. It is a reusable workflow (`workflow_call`) for the edge publish trigger and can be run by hand (`workflow_dispatch`). Only `edge` is accepted until rc and stable promotion is unparked.

## The Omacom catalog key

- Ed25519, the format `scripts/catalog-signing.swift generate` writes: 32 raw private-key bytes; the public half is the 32 raw bytes of `Release/trust-root.ed25519.pub`.
- Public key fingerprint (pinned in `Release/release.json`): `sha256:bb914ffba2a74d05fd8d57013999376654ff9d7489929c76996a7f7716f27ea3`.
- The private key is the secret `CATALOG_SIGNING_KEY` (base64 of the 32 bytes) of the `catalog-release` environment of omacom/omarchy-mac-installer. The environment deploys from `main` only, needs the owner's approval for every run, and administrators cannot bypass it.
- The owner keeps a backup: the same base64 text as the Keychain generic password `omacom-catalog-signing-key` (account `omarchy`) on the owner's Mac, copied into 1Password. `catalog-signing.swift sign-keychain omacom-catalog-signing-key ...` signs with it locally (break glass); Keychain asks the owner first.
- No other copy exists. Rotating the key means a new trust root, a new `release.json` and a new app build: apps already shipped keep trusting only the old key.

## Jobs

| Job | Holds | Does |
| --- | --- | --- |
| `prepare` | no secrets, `contents: read` | runs the catalog tests, refuses unless the evidence record passed for the image's exact sha256, downloads every pinned artifact (or part) and checks its size and sha256, checks the new sequence follows the published one, writes the unsigned `catalog-<channel>.json` |
| `sign` | `catalog-release` (owner approval), `contents: read` | checks the catalog is the one `prepare` wrote and re-checks the evidence, signs with OpenSSL 3, wraps the envelope with `publish-channels envelope` and verifies it against `Release/trust-root.ed25519.pub`. A dry run publishes to a scratch directory on the runner and keeps only the log |
| `publish` | `contents: write`, no key; skipped in a dry run | verifies the envelope again, refuses a sequence that does not follow the published one, replaces the catalog on the host and reads it back |

`scripts/catalog_release.py` implements every step; `scripts/tests/test_catalog_release.py` drives it as the workflow does (round trip, evidence refusals, signing refusals, hosts, dry run). `Tests/OmarchyAppleInstallerTrustCoreTests/CISignedCatalogTests.swift` checks the app accepts an envelope the CI path signed.

## Inputs

All JSON inputs are strict: unknown keys are refused. Digests are 64 lowercase hex characters without a prefix. Every URL is https, without query, fragment or escapes, ends with its `fileName`, and lies under an immutable release location: `https://mac.omarchy.org/v1/releases/` or `.../v1/engine/`, or an asset of a release of omacom/omarchy-mac-installer whose tag does not start with `catalog-`.

- `image`: `omarchy-mac-<channel>-<YYYY.MM.DD>-<12 hex package set id>`, the name `mac-image-check name` gives. It becomes every model's `evidenceRevision`.
- `payload` (ticket 07's upload output for the image zip): `{"url", "fileName", "sizeBytes", "sha256", "parts"?}`. `parts` (2 to 16, for a host with a file size limit) is a list of `{"url", "fileName", "sizeBytes", "sha256"}` in order; their sizes sum to `sizeBytes` and their bytes join into `sha256`. The whole-file `url` is informational when parts are given.
- `installer-data`: the image's `installer_data.json` as `{"url", "fileName", "sizeBytes", "sha256"}`.
- `engine`: `{"version", "url", "fileName", "sizeBytes", "sha256", "downstreamRevision"}`, optionally `asahiInstallerTag`, `asahiInstallerRevision` and `asahiInstallerDataRevision`, which default to `Engine/source-lock.json`.
- `installer` (optional): `{"minimumVersion", "latestVersion", "downloadURL"}`, the app compatibility block. Engines from `v0.9.2-omarchy.18` need `minimumVersion` 2.1.0 or later.
- `evidence`: the VM evidence record below.
- `dry-run`: defaults to true.

## VM evidence record

Ticket 06's acceptance job writes it; ticket 07's upload and this workflow consume it.

```json
{
  "image_sha256": "<sha256 of the image zip, 64 lowercase hex>",
  "result": "pass",
  "scenarios": [
    {"name": "first-boot", "result": "pass"},
    {"name": "conversion", "result": "pass"},
    {"name": "second-boot", "result": "pass"}
  ],
  "harness_commit": "<40-hex omarchy-mac-pkgs commit of tools/acceptance/mac-image>",
  "runner": "<runner label or host description>",
  "run_url": "https://github.com/<owner>/<repo>/actions/runs/<id>"
}
```

- `result` is `pass` or `fail`; each scenario's `result` is `pass`, `fail` or `skip`. Scenario names are the harness's (`tools/acceptance/mac-image/run --list`).
- A publish is refused unless `image_sha256` equals the payload's `sha256`, `result` is `pass`, no scenario failed, and `first-boot`, `conversion` and `second-boot` (the harness's `fresh-install`) all passed.
- Other keys may be added and are ignored here.
- The record is trusted as passed in: when chained, it comes from the acceptance job of the same run; run by hand, the owner's approval of the `sign` job is the check that it is genuine.

## Hosts

`catalog_release.py publish --host` selects where catalogs go. Every host serves `catalog-<channel>.signed.json`, the envelope `publish-channels envelope` writes, and is the only mutable object.

- `github-release` (the stopgap): the asset `catalog-<channel>.signed.json` of the prerelease `catalog-<channel>` of the repository named by `INSTALLER_CATALOG_STOPGAP_REPO`, created on first publish (never marked latest) and replaced in place with `gh release upload --clobber`. While it is replaced the asset is briefly missing, so a client fetching in that moment gets a 404. It is read back from `https://github.com/<repo>/releases/download/catalog-<channel>/catalog-<channel>.signed.json`.
- `scratch:DIR`: a directory, for dry runs and tests.
- Ticket 13 adds the R2 host at `mac.omarchy.org/v1/` as another host class (`fetch`, `publish`, `describe`) and switches the workflow's `--host`.

The app compiled from `Release/` reads `mac.omarchy.org/v1`, so it does not see the stopgap catalogs; a preview build reads them with its own `release.json` (`build-app.sh RELEASE_DIRECTORY`) naming the stopgap URLs and this trust root.

## Running it

After the workflow is on `main`:

```bash
gh workflow run mac-catalog.yml -R omacom/omarchy-mac-installer -f dry-run=true \
  -f image=... -f payload='{...}' -f engine='{...}' -f installer-data='{...}' -f evidence='{...}'
```

The `sign` job waits for the owner's approval in the run page, in a dry run too: a dry run signs with the real key, then writes only to the runner's scratch directory.
