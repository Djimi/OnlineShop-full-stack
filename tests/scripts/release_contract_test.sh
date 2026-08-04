#!/usr/bin/env bash
set -euo pipefail

# Release contract (Pass 3, subphase 3.1) verification gate.
# Runs the Python unittest suite, exercises the CLI against every fixture,
# verifies deterministic JSON output and the checksum guard, and lints the
# shell and Python sources when the tools are available.

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_success() {
  "$@" || fail "expected success: $*"
}

assert_failure() {
  if "$@" >/dev/null 2>&1; then
    fail "expected failure: $*"
  fi
}

assert_contains() {
  local value="$1" expected="$2"
  [[ "$value" == *"$expected"* ]] || fail "expected output to contain: $expected"
}

assert_exit_code() {
  local expected="$1"
  shift
  set +e
  "$@" >/dev/null 2>&1
  local actual=$?
  set -e
  [ "$actual" -eq "$expected" ] || fail "expected exit $expected, got $actual: $*"
}

echo "[1/6] Python syntax check + schema metaschema validation"
python3 -m py_compile "$RELEASE"/src/release_contract/*.py "$RELEASE"/tests/*.py || fail "Python syntax check failed"
python3 -c '
import json, sys
from jsonschema import Draft7Validator
with open(sys.argv[1], encoding="utf-8") as handle:
    Draft7Validator.check_schema(json.load(handle))
' "$RELEASE/schema/release-manifest.schema.json" || fail "schema is not Draft-07 metaschema-valid"

echo "[2/6] Python unit/validation tests"
(
  cd "$RELEASE" && PYTHONPATH="$RELEASE/src" python3 -m unittest discover -s tests
) || fail "Python validation tests failed"

echo "[3/6] CLI: every valid fixture accepted"
for fixture in "$RELEASE"/fixtures/valid/*.json; do
  output=$(bash "$RELEASE/bin/validate-manifest.sh" "$fixture") || fail "valid fixture rejected: $fixture"
  echo "$output" | jq -e '.valid == true' >/dev/null || fail "valid fixture missing valid=true: $fixture"
done

echo "[4/6] CLI: every invalid fixture rejected with machine-readable JSON"
for fixture in "$RELEASE"/fixtures/invalid/*.json; do
  if bash "$RELEASE/bin/validate-manifest.sh" "$fixture" >/dev/null 2>&1; then
    fail "invalid fixture accepted: $fixture"
  fi
done
output=$(bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/invalid/malformed-semver.json" 2>/dev/null || true)
echo "$output" | jq -e '.valid == false' >/dev/null || fail "invalid manifest output missing valid=false"
echo "$output" | jq -e '.issues | any(.code == "INVALID_FORMAT")' >/dev/null || fail "missing INVALID_FORMAT issue"
echo "$output" | jq -e '.errorCount > 0' >/dev/null || fail "missing errorCount"

echo "[5/6] CLI determinism and manifest checksum guard"
first=$(bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/invalid/unsupported-schema-version.json" 2>/dev/null || true)
second=$(bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/invalid/unsupported-schema-version.json" 2>/dev/null || true)
[ "$(printf '%s' "$first" | jq -S .)" = "$(printf '%s' "$second" | jq -S .)" ] || fail "non-deterministic CLI output"

checksum=$(bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/valid/official-v1.2.1.json" | jq -r '.checksum')
[ -n "$checksum" ] && [ "${#checksum}" -eq 64 ] || fail "missing or malformed manifest checksum"
assert_success bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/valid/official-v1.2.1.json" --check-checksum "$checksum" >/dev/null
assert_failure bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/valid/official-v1.2.1.json" --check-checksum "0000000000000000000000000000000000000000000000000000000000000000"

echo "[6/6] Strict shell input helpers and wrapper guards"
# shellcheck source=../../plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"
assert_success rl_assert_semver 1.2.3
assert_success rl_assert_semver 0.0.0
assert_failure rl_assert_semver 1.2.3-beta
assert_failure rl_assert_semver 1.2.3+build.5
assert_failure rl_assert_semver 1.02.3
assert_failure rl_assert_semver v1.2.3
assert_failure rl_assert_semver "1.2.3;rm -rf /"
assert_failure rl_assert_semver "1.2.$(printf '9%.0s' {1..30})"
assert_success rl_assert_full_sha a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4
assert_failure rl_assert_full_sha a1b2c3d4
assert_failure rl_assert_full_sha "$(printf 'a%.0s' {1..41})"
assert_failure rl_assert_full_sha "$(printf 'A%.0s' {1..40})"
assert_success rl_assert_sha256_hex "$(printf 'f%.0s' {1..64})"
assert_failure rl_assert_sha256_hex "$(printf 'f%.0s' {1..63})"
assert_success rl_assert_positive_integer 123
assert_failure rl_assert_positive_integer 0
assert_failure rl_assert_positive_integer -1
assert_failure rl_assert_positive_integer abc
assert_failure rl_assert_positive_integer "$(printf '9%.0s' {1..19})"
assert_success rl_assert_github_login djimi
assert_failure rl_assert_github_login "djimi; rm -rf /"
assert_failure rl_assert_github_login "djimi name"
assert_success rl_assert_github_login "$(printf 'a%.0s' {1..39})"
assert_failure rl_assert_github_login "$(printf 'a%.0s' {1..40})"
assert_success rl_assert_http_url "https://github.com/Djimi/OnlineShop-full-stack/actions/runs/1"
assert_failure rl_assert_http_url "javascript:alert(1)"
assert_success rl_assert_regular_file "$RELEASE/README.md"
assert_failure rl_assert_regular_file "$RELEASE/nonexistent"
assert_failure rl_assert_regular_file "$RELEASE"
assert_failure bash "$RELEASE/bin/validate-manifest.sh"
assert_failure bash "$RELEASE/bin/validate-manifest.sh" --bogus
assert_failure bash "$RELEASE/bin/validate-manifest.sh" /nonexistent/manifest.json
# Unknown long options are forwarded to the Python CLI via argv.
assert_failure bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/valid/official-v1.2.1.json" --bogus >/dev/null 2>&1
assert_success bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/valid/official-v1.2.1.json" --human >/dev/null

# Wrapper usage/IO errors must exit 2 (documented 0=valid, 1=invalid, 2=usage/IO).
assert_exit_code 2 bash "$RELEASE/bin/validate-manifest.sh" /nonexistent/manifest.json
assert_exit_code 2 bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/valid/official-v1.2.1.json" --schema /nonexistent/schema.json
assert_exit_code 2 bash "$RELEASE/bin/validate-manifest.sh" "$RELEASE/fixtures/valid/official-v1.2.1.json" --check-checksum nothex

echo "Optional lint: ruff (Python)"
if command -v ruff >/dev/null 2>&1; then
  (cd "$RELEASE" && ruff check src tests) || fail "ruff lint failed"
else
  echo "ruff not found; skipping (report this)"
fi

echo "Optional lint: shellcheck (shell)"
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck \
    "$RELEASE/bin/validate-manifest.sh" \
    "$RELEASE/bin/release-input.sh" \
    "${BASH_SOURCE[0]}" || fail "shellcheck failed"
else
  echo "shellcheck not found; skipping (report this)"
fi

echo "Release contract tests passed."
