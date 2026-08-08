#!/usr/bin/env bash
set -euo pipefail

# Applies the desired immutable-tag repository configuration to every backend
# ECR repository (Pass 3, subphase 3.3). Mutation + immediate read-back:
# each `aws ecr put-image-tag-mutability` is immediately followed by
# `aws ecr describe-repositories` (via verify-immutable-repositories.sh) and a
# comparison to the desired state. A failed read-back fails closed and the
# script stops at the first drift.
#
# Every backend repository is configured IMMUTABLE_WITH_EXCLUSION so SHA and
# `release-*` tags can never be overwritten, with narrowly scoped mutable
# exclusions only for `main-latest` and `branch-*`. `latest` stays absent.
#
# Usage:
#   apply-immutable-repositories.sh [--profile dpm-profile] [--region eu-north-1]
#
# Environment inputs: none (desired state is release/ecr/immutable-repositories.json).
# Exit 0 when every repository was applied AND read back as intended.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$RELEASE/ecr/immutable-repositories.json"
[ -f "$CONFIG" ] || { echo "ERROR: missing config: $CONFIG" >&2; exit 2; }

PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) sed -n '2,16p' "${BASH_SOURCE[0]}" >&2; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

MUTABILITY=$(jq -r '.imageTagMutability' "$CONFIG")
FILTERS=$(jq -c '.exclusionFilters' "$CONFIG")

while IFS= read -r repo; do
  echo "Applying immutable-tag configuration to $repo ($MUTABILITY, exclusions=$FILTERS)"
  aws ecr put-image-tag-mutability "${AWS_ARGS[@]}" \
    --repository-name "$repo" \
    --image-tag-mutability "$MUTABILITY" \
    --image-tag-mutability-exclusion-filters "$FILTERS"
  # Immediate read-back: exit 0 is not proof; the repository must match.
  bash "$RELEASE/bin/verify-immutable-repositories.sh" --repo "$repo" "${AWS_ARGS[@]}"
done < <(jq -r '.repositories[]' "$CONFIG")

echo "Applied immutable-tag configuration and read back all repositories."
