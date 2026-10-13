# Apple installer packaging

`build-app.sh` assembles a signed macOS application bundle without installing it,
registering its privileged helper, submitting it for notarization, or changing a
disk. The result is safe to inspect before any separately authorized deployment
step.

## Identity and hosting

`identity.conf` is the one place that sets the app name, bundle, helper and package identifiers, the Apple Developer team and its Developer ID identities, the Keychain service of the catalog signing key, the catalog base the app reads (`https://mac.omarchy.org/v1`), and the hosts the publishing tools write to. `build-app.sh` writes them into `Info.plist` and the helper's launch-daemon property list, the Swift build compiles them in through `OmarchyInstallerIdentityPlugin`, and the package, notarization and release scripts source the same file.

Moving to another identity or host is an edit to `identity.conf` followed by `scripts/make-release-descriptor` for a matching `Release/release.json`. The catalog trust root is the one value kept outside `identity.conf`: it is `Release/trust-root.ed25519.pub`, and swapping it is the same replace-then-regenerate step (see `Release/README.md`). `test/all` fails when the descriptor drifts from the configuration or when a configured value is repeated in code, scripts or tests.

## Bundle layout

The generated `Omarchy Installer.app` contains:

- the SwiftUI application in `Contents/MacOS`;
- the root helper in `Contents/Library/LaunchServices`, named by its label, with its Info.plist (carrying the build number it reports) and, for builds with a real signing identity, its `SMJobBless` launchd job linked into `__TEXT` sections;
- the helper's declaration for `SMJobBless` (`SMPrivilegedExecutables`) in the app's Info.plist, for builds with a real signing identity;
- the launch-daemon property list the fallback installer package derives its system daemon from, in `Contents/Library/LaunchDaemons`, pointing at the same helper;
- the immutable release descriptor and Ed25519 trust root in
  `Contents/Resources/Release`; and
- the pinned Asahi validation engine in `Contents/Resources/Engine/artifacts`.

The helper and application use reciprocal code-signing requirements. The helper
also authenticates each XPC client before accepting a request. A release
descriptor whose helper identity does not match the compiled product is rejected.

## Build

Provide a directory containing the production-owned `release.json` and the exact
32-byte `trust-root.ed25519.pub` named by that descriptor:

```sh
Packaging/build-app.sh /absolute/path/to/release-inputs /absolute/path/to/output
```

For a private or offline build, the same directory may also contain the signed
pair `catalog.json` and `catalog.json.sig`. The packager accepts the pair only
when both are regular, non-symlinked files within the catalog size limits and
the signature is exactly 64 bytes. The app verifies this sealed catalog with
the same bundled Ed25519 trust root; when the pair is absent, it fetches the
configured HTTPS catalog as normal.

Build concurrency defaults to 10 workers so a 14-core Mac retains four cores for
responsiveness. Override it with `OMARCHY_BUILD_JOBS`; the same value is exported
as `CARGO_BUILD_JOBS` for nested Rust builds.

The default signing identity is `-`, which creates an ad-hoc development bundle
for local structural validation only. A named identity also requires its
10-character team identifier:

```sh
OMARCHY_APP_SIGNING_IDENTITY="Apple Development: Name (TEAMID)" \
OMARCHY_TEAM_ID="TEAMID" \
Packaging/build-app.sh /absolute/path/to/release-inputs /absolute/path/to/output
```

Production distribution must use a `Developer ID Application` identity, a
hardened-runtime signature and secure timestamp, followed by notarization and
stapling. After an explicitly authorized production build, notarize it with a
preconfigured keychain profile:

```sh
OMARCHY_NOTARY_PROFILE="omarchy-notary" \
Packaging/notarize-app.sh "/absolute/path/Omarchy Installer.app"
```

The app installs its own helper with `SMJobBless` the first time the person authorizes an install, using the password they typed; ad hoc builds leave the declaration out and still need the installer package. After notarization, package the download:

```sh
Packaging/make-download-zip.sh "/absolute/path/Omarchy Installer.app" /absolute/path/Omarchy-Installer.zip
```

It refuses an app without a stapled ticket and checks that the zip holds only the app.

After a completed removal, a helper the app installed itself with `SMJobBless` uninstalls itself: its job file, its binary in `/Library/PrivilegedHelperTools` and its state directory go, and the app sets it up again at the next authorization. A helper an installer package put in place (the package from `pkg/build-pkg.sh`, which runs it from inside the app in `/Applications`, or the private ad hoc package, whose build cannot bless its helper) stays registered after a removal, because nothing but the package could register it again; Remove followed by Install then works without running the package again. The helper tells the two apart by where it runs from and whether its build declares `SMAuthorizedClients`.

A helper that stays keeps its removal records. Before any install or removal it checks them: an unfinished removal blocks both until the record and disk layout are checked, and a completed removal whose install journals were not yet set aside (the helper stopped in between, or moving them failed) gets them set aside first, or the request is refused. If the app loses a removal's reply, it asks the kept helper whether the removal finished; the macOS container size alone only shows that macOS has its space back.

Notarization is deliberately separate from the assembler. The script rejects
ad-hoc and development-signed bundles, submits a temporary ZIP, staples the
accepted ticket to the app, and validates it with Gatekeeper. It requires the
owner's explicit authorization because it uses production credentials and
changes the application bundle.

The script refuses unsafe or mismatched release inputs and will not overwrite an
existing application bundle.
