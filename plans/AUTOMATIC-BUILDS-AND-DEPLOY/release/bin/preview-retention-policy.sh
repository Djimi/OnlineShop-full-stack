#!/usr/bin/env bash
set -euo pipefail

# Preview an ECR lifecycle policy BEFORE any apply (Pass 3, subphase 3.8).
# Prints the exact candidate image IDs/tags the policy would expire and
# requires review before any apply — apply-retention-policy.sh refuses to run
# offline.
#
# Two modes:
#
#   offline:  --images <observed-ecr.json> runs the modeled first-match-wins
#             evaluation (release_contract.retention evaluate) deterministically
#             for a given repository state and reference date. No AWS call is
#             made at all.
#   live:     no --images: runs `aws ecr start-lifecycle-policy-preview` (a
#             DRY-RUN — it evaluates the policy text WITHOUT deleting any
#             image) per backend repository, polls
#             `aws ecr get-lifecycle-policy-preview` to COMPLETE, then validates
#             ECR's own results against the model with
#             `release_contract.retention validate-preview` and the mandatory
#             identity preflight + --profile dpm-profile --region eu-north-1.
#             Fail closed on PREVIEW_DISAGREEMENT and on any protected
#             rollback-window digest being selected (PROTECTED_IMAGE_EXPIRING /
#             RELEASE_RULE_NOT_APPLIED).
#
# Usage:
#   preview-retention-policy.sh [--policy <lifecycle-policy.json>]
#                               [--images <observed-ecr.json>]
#                               [--index <index.json>] [--observed <observed.json>]
#                               [--reference-date <ISO>] [--profile dpm-profile]
#                               [--region eu-north-1]
#
# Exit 0 only when the previewed policy expires exactly the intended set;
# exit 1 when the preview would break the rollback window or disagrees with
# the model; exit 2 on usage/IO errors.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,28p' "${BASH_SOURCE[0]}" >&2
}

POLICY="$RELEASE/ecr/lifecycle-policy.json"
IMAGES=""
OBSERVED=""
REFERENCE_DATE=""
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
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

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PY_ARGS=(--policy "$POLICY")
if [ -n "$REFERENCE_DATE" ]; then
  PY_ARGS+=(--reference-date "$REFERENCE_DATE")
fi

# --- Offline mode: modeled evaluation ----------------------------------------
if [ -n "$IMAGES" ]; then
  rl_assert_regular_file "$IMAGES" || exit 2
  set +e
  OUT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.retention evaluate \
    "${PY_ARGS[@]}" --images "$IMAGES" 2>"$TMP/python.err")
  RC=$?
  set -e
  printf '%s\n' "$OUT"
  [ -s "$TMP/python.err" ] && { cat "$TMP/python.err" >&2 || true; }
  if [ "$RC" -ne 0 ]; then
    echo "PREVIEW FAILED: the modeled evaluation fails closed (see issues above)" >&2
    exit 1
  fi
  echo "REVIEW REQUIRED: the images listed as expiring above are the exact candidates; apply is refused offline (see apply-retention-policy.sh)" >&2
  exit 0
fi

# --- Live preview (read-only dry-run; deferred to the consolidated pass) -----
[ -n "$OBSERVED" ] || {
  echo "ERROR: live preview requires --observed <observed.json> (gather it read-only with audit-retention-window.sh or pass a fixture); without observed state the protected-digest check cannot run" >&2
  exit 2
}
rl_assert_regular_file "$OBSERVED" || exit 2
# The observed ecr section feeds the model; the protected set is every
# release-tagged digest observed (the keep-10 rollback window protection).
jq '.ecr' "$OBSERVED" > "$TMP/observed-images.json"
jq -c '[.ecr[].images[]? | select(.imageTags != null) | select(.imageTags[] | startswith("release-")) | .imageDigest] | unique' \
  "$OBSERVED" > "$TMP/protected.json"

# shellcheck source=/dev/null
source "$RELEASE/../../../scripts/config/production.env"
[ "${LC_ECR_REPOSITORIES:-}" != "" ] || {
  echo "ERROR: live preview needs scripts/config/production.env with LC_ECR_REPOSITORIES" >&2
  exit 2
}
ACCOUNT=$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query 'Account' --output text)
[ "$ACCOUNT" = "799111666795" ] || {
  echo "ERROR: identity preflight failed: unexpected AWS account $ACCOUNT (expected 799111666795)" >&2
  exit 1
}

# ECR lifecycle-policy-text accepts ONLY the {rules: [...]} document, never the
# desired-state wrapper (accountId/region/repositories/notes metadata).
POLICY_TEXT=$(jq -c "{rules: .rules}" "$POLICY")
jq -n '{}' > "$TMP/previews.json"
for repo in "${LC_ECR_REPOSITORIES[@]}"; do
  aws ecr start-lifecycle-policy-preview "${AWS_ARGS[@]}" \
    --repository-name "$repo" \
    --lifecycle-policy-text "$POLICY_TEXT" >/dev/null
  # Bounded polling (AGENTS.md rule: no long blocking loops).
  STATUS=""
  for _ in $(seq 1 20); do
    set +e
    PREVIEW=$(aws ecr get-lifecycle-policy-preview "${AWS_ARGS[@]}" \
      --repository-name "$repo" 2>"$TMP/preview.err")
    RC=$?
    set -e
    if [ "$RC" -ne 0 ]; then
      echo "ERROR: get-lifecycle-policy-preview failed for $repo:" >&2
      cat "$TMP/preview.err" >&2 || true
      exit 1
    fi
    STATUS=$(printf '%s' "$PREVIEW" | jq -r '.status // "UNKNOWN"')
    [ "$STATUS" = "COMPLETE" ] && break
    sleep 5
  done
  [ "$STATUS" = "COMPLETE" ] || {
    echo "ERROR: lifecycle policy preview for $repo did not complete ($STATUS)" >&2
    exit 1
  }
  jq --arg repo "$repo" --argjson preview "$PREVIEW" \
    '. + {($repo): {previewResults: $preview.previewResults}}' "$TMP/previews.json" > "$TMP/previews.next.json"
  mv "$TMP/previews.next.json" "$TMP/previews.json"
done

# Validate ECR's preview against the model: a disagreement, a protected digest
# selected for expiration, or a release image selected by a non-official rule
# all fail closed.
set +e
OUT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.retention validate-preview \
  --policy "$POLICY" --preview "$TMP/previews.json" --images "$TMP/observed-images.json" \
  --protected "$TMP/protected.json" \
  ${REFERENCE_DATE:+--reference-date "$REFERENCE_DATE"} 2>"$TMP/python.err")
RC=$?
set -e
printf '%s\n' "$OUT"
[ -s "$TMP/python.err" ] && { cat "$TMP/python.err" >&2 || true; }
if [ "$RC" -ne 0 ]; then
  echo "PREVIEW REJECTED: the previewed policy would break the retention contract (see issues above)" >&2
  exit 1
fi
echo "REVIEW REQUIRED: the live preview above lists the exact images ECR would expire; review before apply" >&2
exit 0
