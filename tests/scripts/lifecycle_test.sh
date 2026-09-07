#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
# shellcheck source=../../scripts/lib/lifecycle.sh
source "$REPO_ROOT/scripts/lib/lifecycle.sh"

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

assert_success lc_is_present value
assert_failure lc_is_present ""
assert_failure lc_is_present None

LOG_OUTPUT=$(LC_ENVIRONMENT=test lc_log_step "1/2" "about 1 minute" "Perform test work." 2>&1)
assert_contains "$LOG_OUTPUT" "[test] STEP 1/2 (typical: about 1 minute) — Perform test work."
LOG_OUTPUT=$(LC_ENVIRONMENT=test lc_log_complete "Test operation" "$SECONDS" 2>&1)
assert_contains "$LOG_OUTPUT" "[test] COMPLETE — Test operation finished in"

LC_ENVIRONMENT=staging
assert_success lc_require_environment staging
assert_failure lc_require_environment production
assert_success lc_validate_staging_snapshot_name onlineshop-staging-debug-ci-failure
assert_success lc_validate_staging_snapshot_name onlineshop-staging-dr-manual
assert_failure lc_validate_staging_snapshot_name onlineshop-staging-latest
assert_failure lc_validate_staging_snapshot_name production-backup

source "$REPO_ROOT/scripts/config/production.env"
[ "$LC_ENVIRONMENT" = "production" ] || fail "production config environment mismatch"
[[ "$LC_CLUSTER" != *staging* ]] || fail "production config references staging cluster"
[[ "$LC_DB_INSTANCE" != *staging* ]] || fail "production config references staging database"

source "$REPO_ROOT/scripts/config/staging.env"
[ "$LC_ENVIRONMENT" = "staging" ] || fail "staging config environment mismatch"
[[ "$LC_CLUSTER" = *staging* ]] || fail "staging cluster guard is not explicit"
[[ "$LC_DB_INSTANCE" = *staging* ]] || fail "staging database guard is not explicit"

# Staging has no CloudFront distribution: the re-point must be a silent no-op
# (no AWS calls) so staging resume never touches the production distribution.
unset LC_CLOUDFRONT_DISTRIBUTION
assert_success lc_repoint_cloudfront_alb_origin example.com

assert_success bash "$REPO_ROOT/scripts/resume-staging.sh" --help
assert_success bash "$REPO_ROOT/scripts/pause-staging.sh" --help
assert_success bash "$REPO_ROOT/scripts/resume-playground.sh" --help
assert_failure bash "$REPO_ROOT/scripts/pause-playground.sh" unexpected

if rg -n 'onlineshop-staging-latest|restore-db-instance-from-db-snapshot' \
  "$REPO_ROOT/scripts" --glob '*.sh' --glob '*.env'; then
  fail "runtime scripts still depend on snapshot restoration"
fi

echo "Lifecycle helper tests passed."
