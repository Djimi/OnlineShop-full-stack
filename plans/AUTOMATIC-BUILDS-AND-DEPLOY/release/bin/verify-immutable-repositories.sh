#!/usr/bin/env bash
set -euo pipefail

# Verifies that each backend ECR repository matches the desired immutable-tag
# configuration (Pass 3, subphase 3.3). Read-only: it only calls
# `aws ecr describe-repositories` and never mutates anything.
#
# Every tag in a backend repository must be immutable except the narrowly
# scoped mutable exclusions `main-latest` and `branch-*` (encoded in
# release/ecr/immutable-repositories.json). SHA and `release-*` tags must be
# immutable and `latest` must stay absent (Decision 4).
#
# Usage:
#   verify-immutable-repositories.sh [--profile dpm-profile] [--region eu-north-1]
#   verify-immutable-repositories.sh --repo onlineshop-auth [--profile p] [--region r]
#
# Exit 0 when every repository matches; non-zero (fail closed) on any drift.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$RELEASE/ecr/immutable-repositories.json"
[ -f "$CONFIG" ] || { echo "ERROR: missing config: $CONFIG" >&2; exit 2; }

# The mandatory profile/region are the defaults so this script can never run
# without them; explicit --profile/--region still override.
PROFILE="dpm-profile"
REGION="eu-north-1"
REPO_FILTER=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --repo) REPO_FILTER="${2:-}"; shift 2 ;;
    --help) sed -n '2,16p' "${BASH_SOURCE[0]}" >&2; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

EXPECTED_MUTABILITY=$(jq -r '.imageTagMutability' "$CONFIG")
EXPECTED_FILTERS=$(jq -c '[.exclusionFilters[] | {filterType: .filterType, filter: .filter}] | sort_by(.filter)' "$CONFIG")

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

check_repo() {
  local repo="$1"
  local actual
  actual=$(aws ecr describe-repositories "${AWS_ARGS[@]}" \
    --repository-name "$repo" \
    --query 'repositories[0]' \
    --output json 2>/dev/null) || fail "cannot read back repository $repo (aws describe-repositories failed)"

  local mutability
  mutability=$(printf '%s' "$actual" | jq -r '.imageTagMutability // "<absent>"')
  local filters
  filters=$(printf '%s' "$actual" | jq -c '[.imageTagMutabilityExclusionFilters // [] | .[] | {filterType: .filterType, filter: .filter}] | sort_by(.filter)')

  [ "$mutability" = "$EXPECTED_MUTABILITY" ] || {
    fail "repository $repo has imageTagMutability=$mutability, expected $EXPECTED_MUTABILITY"
  }
  [ "$filters" = "$EXPECTED_FILTERS" ] || {
    fail "repository $repo has exclusion filters $filters, expected $EXPECTED_FILTERS"
  }
  echo "OK: $repo imageTagMutability=$mutability exclusions=$filters"
}

if [ -n "$REPO_FILTER" ]; then
  check_repo "$REPO_FILTER"
else
  while IFS= read -r repo; do
    check_repo "$repo"
  done < <(jq -r '.repositories[]' "$CONFIG")
fi

echo "All backend repositories match the immutable-tag desired state."
