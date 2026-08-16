#!/usr/bin/env bash
set -euo pipefail

# Read-only pre-promotion snapshot of the production environment (Pass 3,
# subphase 3.4). Captures the exact state that promotion may mutate so that a
# later failure can compensate (Decision 13) and an interrupted promotion can
# resume deterministically:
#
#   - per-service desired count, capacity-provider strategy, current
#     task-definition ARN, running container digest, ALB load-balancer wiring,
#     and the active deployment id;
#   - the deployed frontend release.json marker and the live index.html checksum;
#   - the current official release identity (version/git tag/source SHA).
#
# The snapshot is written to stdout as JSON and validated by
# `release_contract.promotion snapshot` against the candidate manifest. This
# script never mutates anything.
#
# Usage:
#   snapshot-production.sh --manifest <candidate-manifest.json> [--json]
#     [--profile dpm-profile] [--region eu-north-1]
#
# Environment inputs: scripts/config/production.env is loaded for the cluster,
# services, and frontend bucket.
#
# Exit 0 when the snapshot is gathered AND valid; 1 when any read or validation
# fails closed; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,26p' "${BASH_SOURCE[0]}" >&2
}

MANIFEST=""
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$MANIFEST" ] || { usage; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2
[ -f "$REPO_ROOT/scripts/config/production.env" ] || {
  echo "ERROR: missing scripts/config/production.env" >&2
  exit 1
}
# shellcheck source=/dev/null
source "$REPO_ROOT/scripts/config/production.env"
[ "$LC_PROFILE" = "dpm-profile" ] && [ "$LC_REGION" = "eu-north-1" ] || {
  echo "ERROR: scripts/config/production.env profile/region drift" >&2
  exit 1
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

CLUSTER="$LC_CLUSTER"

# Gather each service's pre-promotion state (read-only).
jq -n '{}' > "$TMP/services.json"
RUNNING_COUNT=0
for service in "${LC_SERVICES[@]}"; do
  set +e
  DESCRIBE=$(aws ecs describe-services "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
    --services "$service" --query 'services[0]' --output json 2>"$TMP/ecs.err")
  RC=$?
  set -e
  if [ "$RC" -ne 0 ] || [ -z "$DESCRIBE" ] || [ "$DESCRIBE" = "null" ]; then
    echo "ERROR: cannot describe production service $service (read failed, not absent):" >&2
    sed -n '1,3p' "$TMP/ecs.err" >&2 || true
    exit 1
  fi
  TD_ARN=$(printf '%s' "$DESCRIBE" | jq -r '.taskDefinition // ""')
  DESIRED=$(printf '%s' "$DESCRIBE" | jq -r '.desiredCount // 0')
  DEPLOY_ID=$(printf '%s' "$DESCRIBE" | jq -r '.deployments[0].id // ""')
  DEPLOY_ROLLOUT=$(printf '%s' "$DESCRIBE" | jq -r '.deployments[0].rolloutState // ""')
  RUNNING_DIGEST=""
  set +e
  TASK_LIST=$(aws ecs list-tasks "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
    --service-name "$service" --query 'taskArns' --output json 2>/dev/null)
  RC=$?
  set -e
  if [ "$RC" -eq 0 ]; then
    COUNT=$(printf '%s' "${TASK_LIST:-[]}" | jq 'length')
    RUNNING_COUNT=$((RUNNING_COUNT + COUNT))
    if [ "$COUNT" -gt 0 ]; then
      FIRST_TASK=$(printf '%s' "${TASK_LIST:-[]}" | jq -r '.[0]')
      set +e
      TASK_JSON=$(aws ecs describe-tasks "${AWS_ARGS[@]}" --cluster "$CLUSTER" \
        --tasks "$FIRST_TASK" --query 'tasks[0]' --output json 2>/dev/null)
      RC=$?
      set -e
      if [ "$RC" -eq 0 ] && [ -n "$TASK_JSON" ]; then
        RUNNING_DIGEST=$(printf '%s' "$TASK_JSON" | jq -r '.containers[0].imageDigest // ""')
      fi
    fi
  fi
  if [ -z "$RUNNING_DIGEST" ]; then
    RUNNING_DIGEST="null"
  else
    RUNNING_DIGEST="\"$RUNNING_DIGEST\""
  fi

  jq --arg service "$service" --argjson desired "$DESIRED" \
    --arg td "$TD_ARN" --argjson runningDigest "$RUNNING_DIGEST" \
    --arg deployId "$DEPLOY_ID" --arg rollout "$DEPLOY_ROLLOUT" \
    --argjson lb "$(printf '%s' "$DESCRIBE" | jq '.loadBalancers // []')" \
    --argjson cps "$(printf '%s' "$DESCRIBE" | jq '.capacityProviderStrategy // []')" \
    '. + {($service): {desiredCount: $desired, capacityProviderStrategy: $cps,
      taskDefinitionArn: $td, runningDigest: $runningDigest, loadBalancers: $lb,
      deployments: [{id: $deployId, rolloutState: ($rollout | if . == "" then "UNKNOWN" else . end)}]}}' \
    "$TMP/services.json" > "$TMP/services.next.json"
  mv "$TMP/services.next.json" "$TMP/services.json"
done

# Frontend state: the deployed live marker + the live index.html checksum.
# The marker is the source of truth for the release that compensation must
# restore.  A missing/malformed marker is not a paused/empty environment: it
# is an unsafe snapshot and fails closed before any promotion mutation.
MARKER_JSON=""
set +e
aws s3api get-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
  --key release.json "$TMP/live.json" >/dev/null 2>"$TMP/frontend.err"
RC=$?
set -e
if [ "$RC" -eq 0 ]; then
  if jq -e 'type == "object" and
      (.version | type == "string") and
      (.sourceSha | type == "string") and
      (.frontendSha256 | type == "string")' "$TMP/live.json" >/dev/null 2>&1; then
    MARKER_JSON=$(jq -c '.' "$TMP/live.json")
  else
    echo "ERROR: deployed release.json marker is missing a canonical version/source/checksum identity" >&2
    exit 1
  fi
else
  echo "ERROR: cannot read the deployed frontend marker (missing/read failed):" >&2
  sed -n '1,3p' "$TMP/frontend.err" >&2 || true
  exit 1
fi

LIVE_VERSION=$(jq -r '.version' <<<"$MARKER_JSON")
LIVE_SOURCE_SHA=$(jq -r '.sourceSha' <<<"$MARKER_JSON")
LIVE_FRONTEND_SHA=$(jq -r '.frontendSha256' <<<"$MARKER_JSON")
rl_assert_semver "$LIVE_VERSION" || exit 1
rl_assert_full_sha "$LIVE_SOURCE_SHA" || exit 1
rl_assert_sha256_hex "$LIVE_FRONTEND_SHA" || exit 1

INDEX_SHA=""
set +e
aws s3api head-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
  --checksum-mode ENABLED --key index.html \
  --query '{checksum: ChecksumSHA256, checksumType: ChecksumType}' \
  --output json >"$TMP/index-head.json" 2>/dev/null
RC=$?
set -e
if [ "$RC" -ne 0 ]; then
  echo "ERROR: cannot read the deployed frontend index.html checksum (missing/read failed)" >&2
  exit 1
fi

# S3 returns ChecksumSHA256 as base64, not hex. Decode only the queried
# full-object checksum and require canonical RFC 4648 encoding for exactly one
# SHA-256 digest. In particular, never fall back to ETag: it is not a SHA-256
# checksum and may be an MD5 or multipart-composite value.
INDEX_SHA=$(python3 - "$TMP/index-head.json" <<'PY'
import base64
import binascii
import json
import re
import sys


def fail(message):
    print(f"ERROR: invalid full-object S3 SHA-256 checksum metadata: {message}", file=sys.stderr)
    raise SystemExit(1)


try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        metadata = json.load(handle)
except (OSError, ValueError):
    fail("head-object response is not valid JSON")

if not isinstance(metadata, dict):
    fail("head-object response is not an object")

checksum = metadata.get("checksum")
checksum_type = metadata.get("checksumType")
if not isinstance(checksum, str) or not checksum:
    fail("ChecksumSHA256 is absent")
if checksum_type not in (None, "FULL_OBJECT"):
    fail("checksum type is not FULL_OBJECT")

# A 32-byte SHA-256 digest has exactly 44 canonical base64 characters. The
# round-trip check rejects non-canonical pad bits in addition to malformed
# alphabet/padding and whitespace.
if not re.fullmatch(r"[A-Za-z0-9+/]{43}=", checksum):
    fail("ChecksumSHA256 is not canonical base64")
try:
    decoded = base64.b64decode(checksum, validate=True)
except (binascii.Error, ValueError):
    fail("ChecksumSHA256 is not valid base64")
if len(decoded) != 32:
    fail("ChecksumSHA256 does not decode to 32 bytes")
if base64.b64encode(decoded).decode("ascii") != checksum:
    fail("ChecksumSHA256 is not canonical base64")
print(decoded.hex())
PY
)
rl_assert_sha256_hex "$INDEX_SHA" || exit 1

# Compensation restores the live root from this immutable prefix. Verify the
# exact identity and index bytes now, while the environment is still
# untouched; a later compensation must never discover that its source was
# missing or belonged to another release.
PREFIX="_releases/v${LIVE_VERSION}/"
set +e
aws s3api get-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
  --key "${PREFIX}release.json" "$TMP/prefix-marker.json" >/dev/null 2>"$TMP/prefix-marker.err"
PREFIX_MARKER_RC=$?
set -e
if [ "$PREFIX_MARKER_RC" -ne 0 ] || ! jq -e 'type == "object"' "$TMP/prefix-marker.json" >/dev/null 2>&1; then
  echo "ERROR: immutable frontend prefix ${PREFIX}release.json is missing/unreadable" >&2
  sed -n '1,3p' "$TMP/prefix-marker.err" >&2 || true
  exit 1
fi
jq -e --arg version "$LIVE_VERSION" --arg sourceSha "$LIVE_SOURCE_SHA" \
  --arg frontendSha256 "$LIVE_FRONTEND_SHA" \
  '.version == $version and .sourceSha == $sourceSha and .frontendSha256 == $frontendSha256' \
  "$TMP/prefix-marker.json" >/dev/null || {
  echo "ERROR: immutable frontend prefix marker does not match the live frontend marker" >&2
  exit 1
}

set +e
aws s3api get-object "${AWS_ARGS[@]}" --bucket "$LC_FRONTEND_BUCKET" \
  --key "${PREFIX}index.html" "$TMP/prefix-index.html" >/dev/null 2>"$TMP/prefix-index.err"
PREFIX_INDEX_RC=$?
set -e
if [ "$PREFIX_INDEX_RC" -ne 0 ] || [ ! -f "$TMP/prefix-index.html" ]; then
  echo "ERROR: immutable frontend prefix ${PREFIX}index.html is missing/unreadable" >&2
  sed -n '1,3p' "$TMP/prefix-index.err" >&2 || true
  exit 1
fi
PREFIX_INDEX_SHA=$(sha256sum "$TMP/prefix-index.html" | awk '{print $1}')
rl_assert_sha256_hex "$PREFIX_INDEX_SHA" || exit 1
[ "$PREFIX_INDEX_SHA" = "$INDEX_SHA" ] || {
  echo "ERROR: immutable frontend prefix index checksum does not match the live index checksum" >&2
  exit 1
}

# Resolve the exact canonical GitHub tag named by the live marker. A GitHub
# repository/CLI is mandatory here: an absent or ambiguous tag cannot be
# replaced by a guessed/newest release identity.
if [ -z "${GITHUB_REPOSITORY:-}" ] || ! command -v gh >/dev/null 2>&1; then
  echo "ERROR: GITHUB_REPOSITORY and gh are required to resolve the live official release" >&2
  exit 1
fi
set +e
gh api "repos/${GITHUB_REPOSITORY}/tags?per_page=100" --paginate --slurp \
  > "$TMP/official-tags.json" 2>"$TMP/official-tags.err"
TAGS_RC=$?
set -e
if [ "$TAGS_RC" -ne 0 ]; then
  echo "ERROR: cannot read paginated GitHub tags (read failed):" >&2
  sed -n '1,3p' "$TMP/official-tags.err" >&2 || true
  exit 1
fi

set +e
SELECTED_TAG=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.official \
  "$TMP/official-tags.json" --version "$LIVE_VERSION" --source-sha "$LIVE_SOURCE_SHA" \
  2>"$TMP/official-tag.err")
SELECTED_RC=$?
set -e
if [ "$SELECTED_RC" -ne 0 ] || ! jq -e 'type == "object" and
    (.tag | type == "string") and (.version | type == "string") and
    (.sha | type == "string")' <<<"$SELECTED_TAG" >/dev/null 2>&1; then
  echo "ERROR: the live frontend marker has no unambiguous canonical Git tag:" >&2
  sed -n '1,3p' "$TMP/official-tag.err" >&2 || true
  exit 1
fi
TAG=$(jq -r '.tag' <<<"$SELECTED_TAG")
OFFICIAL_VERSION=$(jq -r '.version' <<<"$SELECTED_TAG")
LISTED_SHA=$(jq -r '.sha' <<<"$SELECTED_TAG")
[ "$OFFICIAL_VERSION" = "$LIVE_VERSION" ] && [ "$TAG" = "v$LIVE_VERSION" ] || {
  echo "ERROR: resolved Git tag identity does not match the live frontend marker" >&2
  exit 1
}
rl_assert_full_sha "$LISTED_SHA" || exit 1

# Resolve the exact ref and peel annotated tags. The tag-listing SHA, peeled
# commit SHA, and live marker source SHA must all agree.
set +e
REF_JSON=$(gh api "repos/${GITHUB_REPOSITORY}/git/ref/tags/${TAG}" \
  2>"$TMP/official-ref.err")
REF_RC=$?
set -e
if [ "$REF_RC" -ne 0 ] || ! jq -e --arg ref "refs/tags/$TAG" \
    'type == "object" and .ref == $ref and
     (.object | type == "object") and
     (.object.type == "commit" or .object.type == "tag") and
     (.object.sha | type == "string")' <<<"$REF_JSON" >/dev/null 2>&1; then
  echo "ERROR: canonical Git tag $TAG cannot be resolved exactly:" >&2
  sed -n '1,3p' "$TMP/official-ref.err" >&2 || true
  exit 1
fi
REF_TYPE=$(jq -r '.object.type' <<<"$REF_JSON")
REF_SHA=$(jq -r '.object.sha' <<<"$REF_JSON")
rl_assert_full_sha "$REF_SHA" || exit 1
if [ "$REF_TYPE" = "tag" ]; then
  set +e
  TAG_OBJECT_JSON=$(gh api "repos/${GITHUB_REPOSITORY}/git/tags/${REF_SHA}" \
    2>"$TMP/official-tag-object.err")
  TAG_OBJECT_RC=$?
  set -e
  if [ "$TAG_OBJECT_RC" -ne 0 ] || ! jq -e 'type == "object" and
      (.object | type == "object") and (.object.type == "commit") and
      (.object.sha | type == "string")' <<<"$TAG_OBJECT_JSON" >/dev/null 2>&1; then
    echo "ERROR: annotated official Git tag $TAG cannot be peeled to a commit:" >&2
    sed -n '1,3p' "$TMP/official-tag-object.err" >&2 || true
    exit 1
  fi
  REF_SHA=$(jq -r '.object.sha' <<<"$TAG_OBJECT_JSON")
  rl_assert_full_sha "$REF_SHA" || exit 1
fi
[ "$REF_SHA" = "$LISTED_SHA" ] && [ "$REF_SHA" = "$LIVE_SOURCE_SHA" ] || {
  echo "ERROR: canonical Git tag SHA disagrees with the live frontend marker" >&2
  exit 1
}
jq -n --arg version "$LIVE_VERSION" --arg tag "$TAG" --arg sha "$REF_SHA" \
  '{version: $version, gitTag: $tag, sourceSha: $sha}' > "$TMP/official.json"
OFFICIAL_JSON=$(jq -c '.' "$TMP/official.json")

# The snapshot must be schema-complete; a paused environment is recorded
# honestly with its current task-definition digests.
PAUSED=false
[ "$RUNNING_COUNT" -eq 0 ] && PAUSED=true

jq -n \
  --argjson paused "$PAUSED" \
  --argjson services "$(cat "$TMP/services.json")" \
  --argjson marker "${MARKER_JSON:-null}" \
  --arg indexSha "$INDEX_SHA" \
  --argjson official "${OFFICIAL_JSON:-null}" \
  '{paused: $paused, services: $services,
    frontend: {marker: $marker, indexSha256: $indexSha},
    officialRelease: $official}' > "$TMP/snapshot.json"

SNAPSHOT_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion snapshot \
  --snapshot "$TMP/snapshot.json" --manifest "$MANIFEST") || true
printf '%s' "$SNAPSHOT_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: pre-promotion snapshot is incomplete (fail closed):" >&2
  printf '%s' "$SNAPSHOT_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

cat "$TMP/snapshot.json"
echo "snapshot-production: OK (paused=$PAUSED, services=${#LC_SERVICES[@]})" >&2
