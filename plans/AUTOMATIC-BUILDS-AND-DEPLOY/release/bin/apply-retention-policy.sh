#!/usr/bin/env bash
set -euo pipefail

# Apply the desired ECR lifecycle policy to every backend repository with
# immediate read-back (Pass 3, subphase 3.8). Each `aws ecr put-lifecycle-policy`
# is immediately followed by `aws ecr get-lifecycle-policy` and a byte
# comparison against the desired policy — a failed read-back fails closed and
# the script stops at the first drift.
#
# The APPLY PATH IS REFUSED OFFLINE: `--apply` requires the environment gate
# ONLINESHOP_RETENTION_LIVE_APPLY=1, which is set only by the consolidated
# Pass 3 live pass. Without it the script exits 2 with a deferral message —
# the offline gates never exercise a live apply.
#
# --dry-run (the default) previews what the policy would expire first:
#   offline  with --images <observed-ecr.json>: the modeled evaluation
#            (deterministic, no AWS call);
#   live     without --images: ECR's start/get-lifecycle-policy-preview
#            (a DRY-RUN that deletes nothing) + validation, review required
#            before any apply.
#
# Usage:
#   apply-retention-policy.sh [--dry-run | --apply] [--policy <lifecycle-policy.json>]
#                             [--images <observed-ecr.json>] [--observed <observed.json>]
#                             [--reference-date <ISO>] [--profile dpm-profile]
#                             [--region eu-north-1]
#
# Exit 0 when applied AND read back as intended (or when a dry-run preview
# passes); exit 1 when the preview/read-back fails closed; exit 2 on usage/IO
# errors and on the offline apply refusal.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" >&2
}

MODE="dry-run"
POLICY="$RELEASE/ecr/lifecycle-policy.json"
IMAGES=""
OBSERVED=""
REFERENCE_DATE=""
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) MODE="dry-run"; shift ;;
    --apply) MODE="apply"; shift ;;
    --policy) POLICY="${2:-}"; shift 2 ;;
    --images) IMAGES="${2:-}"; shift 2 ;;
    --observed) OBSERVED="${2:-}"; shift 2 ;;
    --reference-date) REFERENCE_DATE="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

# The mandatory profile/region are non-overridable (AGENTS.md rule).
if [ "$PROFILE" != "dpm-profile" ] || [ "$REGION" != "eu-north-1" ]; then
  echo "ERROR: --profile must be dpm-profile and --region must be eu-north-1 (mandatory)" >&2
  exit 2
fi
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")
rl_assert_regular_file "$POLICY" || exit 2

# The offline gates must never exercise a live apply: --apply is refused
# unless the consolidated-pass operator sets the explicit environment gate.
if [ "$MODE" = "apply" ] && [ "${ONLINESHOP_RETENTION_LIVE_APPLY:-}" != "1" ]; then
  echo "REFUSED: live lifecycle policy application is deferred to the consolidated Pass 3" >&2
  echo "verification pass. Preview and review first (apply-retention-policy.sh --dry-run);" >&2
  echo "only the consolidated live pass may set ONLINESHOP_RETENTION_LIVE_APPLY=1." >&2
  exit 2
fi

# --- Dry-run: preview the exact expiration candidates before any apply -------
if [ "$MODE" = "dry-run" ]; then
  if [ -n "$IMAGES" ]; then
    bash "$RELEASE/bin/preview-retention-policy.sh" \
      --policy "$POLICY" --images "$IMAGES" ${REFERENCE_DATE:+--reference-date "$REFERENCE_DATE"}
  else
    bash "$RELEASE/bin/preview-retention-policy.sh" \
      --policy "$POLICY" ${OBSERVED:+--observed "$OBSERVED"} \
      ${REFERENCE_DATE:+--reference-date "$REFERENCE_DATE"}
  fi
  echo "DRY-RUN (no mutation): the preview above lists the exact candidates; run --apply only from the consolidated live pass after review" >&2
  exit 0
fi

# --- Apply (consolidated live pass only) -------------------------------------
[ -n "$OBSERVED" ] || {
  echo "ERROR: --apply requires --observed <observed.json> (gathered read-only beforehand)" >&2
  exit 2
}
rl_assert_regular_file "$OBSERVED" || exit 2

# shellcheck source=/dev/null
source "$RELEASE/../../../scripts/config/production.env"
[ "${LC_ECR_REPOSITORIES:-}" != "" ] || {
  echo "ERROR: apply needs scripts/config/production.env with LC_ECR_REPOSITORIES" >&2
  exit 2
}
ACCOUNT=$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query 'Account' --output text)
[ "$ACCOUNT" = "799111666795" ] || {
  echo "ERROR: identity preflight failed: unexpected AWS account $ACCOUNT (expected 799111666795)" >&2
  exit 1
}

# The policy must pass the desired-state validation before any mutation.
set +e
PYTHONPATH="$RELEASE/src" python3 -m release_contract.retention validate-policy \
  --policy "$POLICY" >/dev/null 2>&1
VALIDATE_RC=$?
set -e
[ "$VALIDATE_RC" -eq 0 ] || {
  echo "ERROR: the desired policy does not pass validate-policy; nothing applied" >&2
  exit 1
}

# ECR lifecycle-policy-text accepts ONLY the {rules: [...]} document, never the
# desired-state wrapper (accountId/region/repositories/notes metadata).
POLICY_TEXT=$(jq -c "{rules: .rules}" "$POLICY")
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
TMP_DESIRED="$TMP/desired.json"
TMP_READBACK="$TMP/readback.json"
for repo in "${LC_ECR_REPOSITORIES[@]}"; do
  echo "Applying lifecycle policy to $repo"
  aws ecr put-lifecycle-policy "${AWS_ARGS[@]}" \
    --repository-name "$repo" \
    --lifecycle-policy-text "$POLICY_TEXT"
  # Immediate read-back: exit 0 is not proof; the policy must match byte for
  # byte (fail closed on drift).
  READBACK=$(aws ecr get-lifecycle-policy "${AWS_ARGS[@]}" \
    --repository-name "$repo" --query 'lifecyclePolicyText' --output text)
  printf '%s' "$READBACK" | jq -S . > "$TMP_READBACK" 2>/dev/null || {
    echo "ERROR: read-back for $repo is not valid JSON; the applied policy drifts from desired" >&2
    exit 1
  }
  printf '%s' "$POLICY_TEXT" | jq -S . > "$TMP_DESIRED" 2>/dev/null || true
  cmp -s "$TMP_DESIRED" "$TMP_READBACK" || {
    echo "ERROR: read-back drift for $repo: the applied policy does not match the desired policy" >&2
    exit 1
  }
  echo "Read back $repo: applied policy matches desired."
done

echo "Applied the lifecycle policy and read back all repositories."
