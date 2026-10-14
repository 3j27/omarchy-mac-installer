#!/bin/bash
# The app release workflow and its scripts: what each trigger may do, the
# signing order and flags, the publishing refusals, and the workflow's
# security invariants. Stubs stand in for codesign and gh, so this runs on any
# host; the real signing runs in the workflow's dry run.

set -euo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/base-test.sh"
source "$ROOT/Packaging/identity.conf"

release=$ROOT/Packaging/release
workflow=$ROOT/.github/workflows/mac-app-release.yml
test_tmp=$(mktemp -d)
trap 'rm -rf "$test_tmp"' EXIT

# ── plan: what each trigger may do ─────────────────────────────────────────
plan() {
  env -i PATH="$PATH" "$@" bash "$release/plan" 2>&1
}
expect_plan() {
  local description=$1 expected=$2 output
  shift 2
  output=$(plan "$@") || fail "$description" "$output"
  [[ $output == *"$expected"* ]] || fail "$description" "$output"
  pass "$description"
}
refuse_plan() {
  local description=$1 output
  shift
  if output=$(plan "$@"); then
    fail "$description" "$output"
  fi
  pass "$description"
}

expect_plan "a pushed v* tag is a Developer ID release of the tag's version" \
  $'dry_run=false\nsigning=developer-id\nversion=2.2.0\nbuild_number=107\nstand_in_trust_root=false' \
  EVENT=push REF=refs/tags/v2.2.0 RUN_NUMBER=7
expect_plan "a dispatch defaults to an ad hoc dry run" \
  $'dry_run=true\nsigning=ad-hoc\nversion=0.0.0-dry-run' \
  EVENT=workflow_dispatch REF=refs/heads/some-branch RUN_NUMBER=1 VERSION=0.0.0-dry-run
expect_plan "main may dry-run with the Developer ID (to test the secrets)" "signing=developer-id" \
  EVENT=workflow_dispatch REF=refs/heads/main RUN_NUMBER=1 DRY_RUN=true SIGNING=developer-id VERSION=2.2.0
expect_plan "a dispatch on a v* tag takes the tag's version" "version=2.3.0" \
  EVENT=workflow_dispatch REF=refs/tags/v2.3.0 RUN_NUMBER=1 DRY_RUN=false SIGNING=developer-id
refuse_plan "pull requests never run the release" EVENT=pull_request REF=refs/pull/1/merge RUN_NUMBER=1
refuse_plan "pull_request_target never runs the release" EVENT=pull_request_target REF=refs/heads/main RUN_NUMBER=1
refuse_plan "a push to a branch is not a release" EVENT=push REF=refs/heads/main RUN_NUMBER=1
refuse_plan "a tag that is not v<version> is refused" EVENT=push REF=refs/tags/vnext RUN_NUMBER=1
refuse_plan "only a v* tag publishes" \
  EVENT=workflow_dispatch REF=refs/heads/main RUN_NUMBER=1 DRY_RUN=false SIGNING=developer-id VERSION=2.2.0
refuse_plan "a release is never ad hoc" \
  EVENT=workflow_dispatch REF=refs/tags/v2.2.0 RUN_NUMBER=1 DRY_RUN=false SIGNING=ad-hoc
refuse_plan "the Developer ID signs only main and v* tags" \
  EVENT=workflow_dispatch REF=refs/heads/feature RUN_NUMBER=1 DRY_RUN=true SIGNING=developer-id VERSION=2.2.0
refuse_plan "a stand-in trust root never meets the Developer ID" \
  EVENT=workflow_dispatch REF=refs/heads/main RUN_NUMBER=1 SIGNING=developer-id STAND_IN_TRUST_ROOT=true VERSION=2.2.0
refuse_plan "a tag dispatch cannot override the tag's version" \
  EVENT=workflow_dispatch REF=refs/tags/v2.2.0 RUN_NUMBER=1 VERSION=9.9.9

# ── sign-app: inside-out, hardened runtime, timestamps, never --deep ───────
stubs=$test_tmp/stubs
mkdir -p "$stubs"
cat >"$stubs/codesign" <<'STUB'
#!/bin/bash
printf '%s\n' "$*" >>"$CODESIGN_LOG"
case " $* " in
  *" --display "*)
    if [[ $STUB_SIGNATURE == adhoc ]]; then
      printf 'CodeDirectory v=20500 size=1 flags=0x10002(adhoc,runtime) hashes=1+0\nSignature=adhoc\n' >&2
    else
      printf 'CodeDirectory v=20500 size=1 flags=0x10000(runtime) hashes=1+0\n' >&2
      printf 'Authority=Developer ID Application: Someone (%s)\nTimestamp=1 Jan 2027\nTeamIdentifier=%s\n' \
        "$STUB_TEAM" "$STUB_TEAM" >&2
    fi
    ;;
esac
STUB
chmod +x "$stubs/codesign"
app=$test_tmp/$INSTALLER_APP_NAME.app
mkdir -p "$app/Contents/Library/LaunchServices"
: >"$app/Contents/Library/LaunchServices/$INSTALLER_HELPER_IDENTIFIER"
helper=$app/Contents/Library/LaunchServices/$INSTALLER_HELPER_IDENTIFIER

sign() {
  CODESIGN_LOG=$test_tmp/codesign.log PATH="$stubs:$PATH" STUB_TEAM=$INSTALLER_TEAM_ID "$@"
}
: >"$test_tmp/codesign.log"
sign env STUB_SIGNATURE=devid bash "$release/sign-app" "$app" "Developer ID Application: Someone ($INSTALLER_TEAM_ID)" \
  "$test_tmp/release.keychain-db" >/dev/null || fail "sign-app signs with a Developer ID"
mapfile -t signing < <(grep -e '--sign ' "$test_tmp/codesign.log")
(( ${#signing[@]} == 2 )) || fail "sign-app signs exactly the helper and the app" "$(cat "$test_tmp/codesign.log")"
[[ ${signing[0]} == *"--identifier $INSTALLER_HELPER_IDENTIFIER $helper" ]] ||
  fail "the helper is signed first, under its own identifier" "${signing[0]}"
[[ ${signing[1]} == *" $app" && ${signing[1]} != *"--identifier"* ]] || fail "the app is signed second" "${signing[1]}"
for line in "${signing[@]}"; do
  [[ " $line " == *" --options runtime "* && " $line " == *" --timestamp "* ]] ||
    fail "every Developer ID signature has the hardened runtime and a secure timestamp" "$line"
  [[ " $line " == *" --keychain $test_tmp/release.keychain-db "* ]] || fail "signing reads only the given keychain" "$line"
  [[ " $line " != *" --deep "* && " $line " != *" --entitlements"* ]] ||
    fail "signing never uses --deep or entitlements" "$line"
done
grep -qF -- "-R=anchor apple generic and identifier \"$INSTALLER_HELPER_IDENTIFIER\" and certificate leaf[subject.OU] = \"$INSTALLER_TEAM_ID\" $helper" \
  "$test_tmp/codesign.log" || fail "the helper is verified against the team's designated requirement"
grep -qF -- "-R=anchor apple generic and identifier \"$INSTALLER_APP_IDENTIFIER\" and certificate leaf[subject.OU] = \"$INSTALLER_TEAM_ID\" $app" \
  "$test_tmp/codesign.log" || fail "the app is verified against the team's designated requirement"
pass "sign-app signs the helper then the app with runtime and timestamps, never --deep, and checks both requirements"

: >"$test_tmp/codesign.log"
sign env STUB_SIGNATURE=adhoc bash "$release/sign-app" "$app" - >/dev/null || fail "sign-app signs ad hoc"
grep -e '--sign ' "$test_tmp/codesign.log" | grep -qv -e '--timestamp=none' &&
  fail "an ad hoc dry run asks for no timestamp" "$(cat "$test_tmp/codesign.log")"
grep -qF -- "-R=identifier \"$INSTALLER_HELPER_IDENTIFIER\" $helper" "$test_tmp/codesign.log" ||
  fail "an ad hoc helper is verified by its identifier"
pass "sign-app signs a dry run ad hoc with the same order"

if sign env STUB_SIGNATURE=adhoc bash "$release/sign-app" "$app" "Developer ID Application: Someone ($INSTALLER_TEAM_ID)" \
  >/dev/null 2>&1; then
  fail "sign-app refuses a Developer ID run whose signature is ad hoc"
fi
pass "sign-app refuses a signature without the Developer ID team and timestamp"

# ── publish-app: draft only, never latest, never overwrite ─────────────────
dmg_dir=$test_tmp/out
mkdir -p "$dmg_dir"
dmg=$dmg_dir/$INSTALLER_FILE_STEM-2.2.0.dmg
printf 'dmg bytes' >"$dmg"
(cd "$dmg_dir" && sha256sum "${dmg##*/}" >"${dmg##*/}.sha256")
cat >"$stubs/gh" <<'STUB'
#!/bin/bash
printf '%s\n' "$*" >>"$GH_LOG"
case "$1 $2" in
  "release view")
    if [[ $* == *isDraft* ]]; then
      [[ $GH_STATE == missing ]] && { echo "release not found" >&2; exit 1; }
      [[ $GH_STATE == published ]] && echo false || echo true
    else
      printf '%s\n' ${GH_ASSETS:-}
    fi
    ;;
  "release download")
    dir=${@: -1}
    cp "$GH_SOURCE"/* "$dir/"
    ;;
esac
STUB
chmod +x "$stubs/gh"
publish() {
  GH_LOG=$test_tmp/gh.log GH_SOURCE=$dmg_dir GITHUB_REPOSITORY=owner/repo PATH="$stubs:$PATH" "$@" \
    bash "$release/publish-app" --target github-release --version 2.2.0 "$dmg" "$dmg.sha256" 2>&1
}
: >"$test_tmp/gh.log"
output=$(publish env GH_STATE=missing) || fail "publish-app creates a draft for a new version" "$output"
create=$(grep '^release create' "$test_tmp/gh.log")
for flag in "release create v2.2.0" --verify-tag --draft --prerelease --latest=false; do
  [[ " $create " == *" $flag "* ]] || fail "the release is a draft prerelease on the existing tag, never latest" "$create"
done
grep -q '^release upload v2.2.0' "$test_tmp/gh.log" || fail "publish-app uploads the dmg and its sha256"
grep -q '^release download v2.2.0' "$test_tmp/gh.log" || fail "publish-app downloads its files back"
pass "publish-app creates a draft prerelease, never latest, and checks what it uploaded"

output=$(publish env GH_STATE=published) && fail "publish-app refuses a published release" "$output"
[[ $output == *"already published"* ]] || fail "publish-app refuses a published release" "$output"
output=$(publish env GH_STATE=draft GH_ASSETS="${dmg##*/}") && fail "publish-app refuses to overwrite a file" "$output"
[[ $output == *"immutable"* ]] || fail "publish-app refuses to overwrite a file" "$output"
pass "publish-app never changes a published release or replaces a file"

printf 'other bytes' >"$dmg"
output=$(publish env GH_STATE=missing) && fail "publish-app refuses a dmg its sha256 does not match" "$output"
pass "publish-app refuses a dmg that does not match its sha256"
output=$(GH_LOG=/dev/null bash "$release/publish-app" --target r2 --version 2.2.0 "$dmg" "$dmg.sha256" 2>&1) &&
  fail "the r2 target is refused until ticket 13" "$output"
pass "the r2 target is refused until it exists"

# ── the workflow's security invariants ─────────────────────────────────────
triggers=$(awk '/^on:/{on=1; next} on && /^[^ ]/{exit} on && /^  [a-z_]+:/{sub(/:.*/, ""); print $1}' "$workflow")
[[ $triggers == $'push\nworkflow_dispatch' ]] || fail "the workflow runs only for pushes (v* tags) and dispatches" "$triggers"
grep -A2 '^  push:' "$workflow" | grep -q "tags: \['v\*'\]" || fail "pushes run only for v* tags"
pass "pull requests cannot trigger the release workflow"

# Each job's lines, keyed by job id.
job_of() {
  awk -v job="$1" '/^jobs:/{in_jobs=1; next} in_jobs && /^  [a-z][a-z0-9-]*:$/{current=$1; sub(/:$/, "", current)} in_jobs && current == job' "$workflow"
}
jobs=$(awk '/^jobs:/{in_jobs=1; next} in_jobs && /^  [a-z][a-z0-9-]*:$/{sub(/:$/, "", $1); print $1}' "$workflow")
[[ $jobs == $'plan\nengine\nbuild\nsign-ad-hoc\nsign\npublish' ]] || fail "the workflow has the expected jobs" "$jobs"
for job in $jobs; do
  body=$(job_of "$job")
  if [[ $job == sign ]]; then
    grep -q '^    environment: macos-release$' <<<"$body" || fail "the sign job runs in the macos-release environment"
  else
    ! grep -q 'secrets\.' <<<"$body" || fail "only the sign job reads secrets ($job does)"
    ! grep -q 'environment:' <<<"$body" || fail "only the sign job uses an environment ($job does)"
  fi
done
grep -q '^permissions: {}$' "$workflow" || fail "the workflow grants no permissions by default"
! job_of sign | grep -Eq '(^|[^a-z])(brew|npm|npx|pip3?|gem|cargo|curl|wget) ' ||
  fail "the sign job installs nothing"
[[ -z $(job_of sign | grep 'uses: ' | grep -Ev 'actions/(checkout|download-artifact|upload-artifact)@') ]] ||
  fail "the sign job uses only GitHub's checkout and artifact actions"
pass "only the sign job reads secrets, in macos-release, and it installs nothing"

while IFS= read -r line; do
  [[ $line =~ uses:\ [a-z0-9-]+/[a-z0-9-]+@[0-9a-f]{40}\ \#\ v[0-9] ]] || fail "every action is pinned to a commit" "$line"
done < <(grep 'uses: ' "$workflow")
pass "every action in the workflow is pinned to a commit"

! grep -n -- '^[^#]*codesign .*--deep' "$release/sign-app" "$release/sign-and-package" | grep -v -- '--verify' ||
  fail "the release scripts never sign with --deep"
pass "the release scripts use --deep only to verify"
