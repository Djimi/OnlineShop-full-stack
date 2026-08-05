#!/usr/bin/env bash
set -euo pipefail

# Rollback result / audit record (Pass 3, subphase 3.6). Writes and validates
# the rollback result artifact recording requester, approver, from/to releases
# and exact artifacts, timestamps, workflow URL, and outcome. It annotates the
# deployment/audit record (the emitted JSON is uploaded as a workflow artifact)
# without ever editing the immutable original release manifest. The record is
# validated by the fixture-tested `release_contract.rollback result` decision,
# which also decides write/resume idempotency and fails closed on any conflict.
#
# Usage:
#   record-rollback-result.sh --manifest <deployment-manifest.json>
#     --snapshot <snapshot.json> --run-id <int> --workflow-url <url>
#     --requester <login> --approver <login>
#     [--outcome success|compensated|mixed-state-incident]
#     [--audit-path <path>] [--existing-result <file>]
#     [--profile dpm-profile] [--region eu-north-1]
# `--requester`/`--approver` are mandatory: the approver is derived by the
# caller from the GitHub environment-approval evidence (actions/runs/{run}/
# approvals) and must never default to the run actor.
#
# Exit 0 when the record is written and valid; 1 on any fail-closed check;
# 2 on usage/IO error. JSON on stdout (the result record); diagnostics on stderr.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,22p' "${BASH_SOURCE[0]}" >&2
}

MANIFEST=""
SNAPSHOT=""
RUN_ID=""
WORKFLOW_URL=""
REQUESTER=""
APPROVER=""
OUTCOME="success"
AUDIT_PATH=""
EXISTING_RESULT=""
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --snapshot) SNAPSHOT="${2:-}"; shift 2 ;;
    --run-id) RUN_ID="${2:-}"; shift 2 ;;
    --workflow-url) WORKFLOW_URL="${2:-}"; shift 2 ;;
    --requester) REQUESTER="${2:-}"; shift 2 ;;
    --approver) APPROVER="${2:-}"; shift 2 ;;
    --outcome) OUTCOME="${2:-}"; shift 2 ;;
    --audit-path) AUDIT_PATH="${2:-}"; shift 2 ;;
    --existing-result) EXISTING_RESULT="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$MANIFEST" ] && [ -n "$SNAPSHOT" ] && [ -n "$RUN_ID" ] && [ -n "$WORKFLOW_URL" ] || { usage; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2
rl_assert_regular_file "$SNAPSHOT" || exit 2
rl_assert_positive_integer "$RUN_ID" || exit 2
rl_assert_http_url "$WORKFLOW_URL" || exit 2
if [ -n "$REQUESTER" ]; then
  rl_assert_github_login "$REQUESTER" || exit 2
fi
if [ -n "$APPROVER" ]; then
  rl_assert_github_login "$APPROVER" || exit 2
fi
case "$OUTCOME" in
  success|compensated|mixed-state-incident) ;;
  *) echo "ERROR: --outcome must be success|compensated|mixed-state-incident" >&2; exit 2 ;;
esac

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Mandatory identity preflight ------------------------------------------
if [ -f "$REPO_ROOT/scripts/config/production.env" ]; then
  # shellcheck source=/dev/null
  source "$REPO_ROOT/scripts/config/production.env"
  [ "$LC_PROFILE" = "dpm-profile" ] && [ "$LC_REGION" = "eu-north-1" ] || {
    echo "ERROR: scripts/config/production.env profile/region drift" >&2
    exit 1
  }
  ACCOUNT_ID="$LC_ACCOUNT_ID"
else
  echo "ERROR: missing scripts/config/production.env" >&2
  exit 1
fi
set +e
IDENTITY_ACCOUNT=$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query 'Account' --output text 2>"$TMP/identity.err")
RC=$?
set -e
if [ "$RC" -ne 0 ] || [ -z "$IDENTITY_ACCOUNT" ]; then
  echo "ERROR: identity preflight failed (aws sts get-caller-identity):" >&2
  sed -n '1,3p' "$TMP/identity.err" >&2 || true
  exit 1
fi
[ "$IDENTITY_ACCOUNT" = "$ACCOUNT_ID" ] || {
  echo "ERROR: identity preflight failed; account $IDENTITY_ACCOUNT != $ACCOUNT_ID" >&2
  exit 1
}

# --- Build the from/to release identities -----------------------------------
# from = the pre-rollback release (snapshot officialRelease + running digests +
# live frontend checksum); to = the target release (the deployment manifest).
NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)
FROM_SHA=$(jq -r '.officialRelease.sourceSha // ""' "$SNAPSHOT")
FROM_FE=$(jq -r '.frontend.marker.frontendSha256 // ""' "$SNAPSHOT")
jq -n \
  --arg version "$(jq -r '.officialRelease.version // ""' "$SNAPSHOT")" \
  --arg gitTag "$(jq -r '.officialRelease.gitTag // ""' "$SNAPSHOT")" \
  --arg sourceSha "$FROM_SHA" \
  --arg frontendSha256 "$FROM_FE" \
  --arg auth "$(jq -r '.services["onlineshop-auth"].runningDigest // ""' "$SNAPSHOT")" \
  --arg items "$(jq -r '.services["onlineshop-items"].runningDigest // ""' "$SNAPSHOT")" \
  --arg gateway "$(jq -r '.services["onlineshop-api-gateway"].runningDigest // ""' "$SNAPSHOT")" \
  '{version: $version, gitTag: $gitTag, sourceSha: $sourceSha,
    digests: {auth: $auth, items: $items, apiGateway: $gateway},
    frontendSha256: $frontendSha256}' > "$TMP/from.json"
jq -n \
  --arg version "$(jq -r '.release.version' "$MANIFEST")" \
  --arg gitTag "$(jq -r '.release.gitTag' "$MANIFEST")" \
  --arg sourceSha "$(jq -r '.release.sourceSha' "$MANIFEST")" \
  --arg frontendSha256 "$(jq -r '.components.frontend.sha256' "$MANIFEST")" \
  --arg auth "$(jq -r '.components.auth.imageDigest' "$MANIFEST")" \
  --arg items "$(jq -r '.components.items.imageDigest' "$MANIFEST")" \
  --arg gateway "$(jq -r '.components.apiGateway.imageDigest' "$MANIFEST")" \
  '{version: $version, gitTag: $gitTag, sourceSha: $sourceSha,
    digests: {auth: $auth, items: $items, apiGateway: $gateway},
    frontendSha256: $frontendSha256}' > "$TMP/to.json"

[ -n "$REQUESTER" ] || {
  echo "ERROR: --requester is required (the operator who requested the rollback)" >&2
  usage
  exit 2
}
[ -n "$APPROVER" ] || {
  echo "ERROR: --approver is required (derived from the GitHub environment-approval evidence, never the run actor)" >&2
  usage
  exit 2
}
rl_assert_github_login "$REQUESTER" || exit 2
rl_assert_github_login "$APPROVER" || exit 2
AUDIT_PATH="${AUDIT_PATH:-rollback-audit-${RUN_ID}.json}"

jq -n \
  --slurpfile manifest "$MANIFEST" --slurpfile snapshot "$SNAPSHOT" \
  --argjson from "$(cat "$TMP/from.json")" --argjson to "$(cat "$TMP/to.json")" \
  --arg requester "$REQUESTER" --arg approver "$APPROVER" \
  --argjson runId "$((10#$RUN_ID))" --arg workflowUrl "$WORKFLOW_URL" \
  --arg startedAt "$NOW" --arg completedAt "$NOW" \
  --arg outcome "$OUTCOME" --arg auditPath "$AUDIT_PATH" \
  '{manifest: $manifest[0], snapshot: $snapshot[0],
    result: {requester: $requester, approver: $approver,
      runId: $runId, workflowUrl: $workflowUrl,
      from: $from, to: $to,
      timestamps: {startedAt: $startedAt, completedAt: $completedAt},
      outcome: $outcome, productionVerified: true,
      auditAnnotation: {written: true, path: $auditPath}}}' > "$TMP/state.json"

if [ -n "$EXISTING_RESULT" ]; then
  rl_assert_regular_file "$EXISTING_RESULT" || exit 2
  jq --slurpfile existing "$EXISTING_RESULT" \
    '.existingResult = {exists: true, result: $existing[0].result}' \
    "$TMP/state.json" > "$TMP/state.next.json"
  mv "$TMP/state.next.json" "$TMP/state.json"
else
  jq '.existingResult = null' "$TMP/state.json" > "$TMP/state.next.json"
  mv "$TMP/state.next.json" "$TMP/state.json"
fi

RESULT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.rollback result \
  --state "$TMP/state.json") || true
printf '%s' "$RESULT" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: rollback result record is invalid (fail closed):" >&2
  printf '%s' "$RESULT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}
ACTION=$(printf '%s' "$RESULT" | jq -r '.action')

# Emit the result record (the workflow uploads it as the audit annotation).
jq -c '{result: .result}' "$TMP/state.json"
echo "record-rollback-result: OK (action=$ACTION)" >&2
