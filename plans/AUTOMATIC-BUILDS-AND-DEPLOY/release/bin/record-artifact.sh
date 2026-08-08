#!/usr/bin/env bash
set -euo pipefail

# Records the GitHub artifact identity of the candidate evidence bundle from the
# actions/upload-artifact@v4 step outputs (Pass 3, subphase 3.2).
#
# actions/upload-artifact@v4 returns artifact-id, artifact-url and
# artifact-digest (the GitHub service-reported SHA-256 of the uploaded artifact
# archive) as step outputs, so no post-upload artifacts-API query is needed.
# This script validates those values and writes the exact consumption tuple
# {runId, runAttempt, artifactId, artifactUrl, artifactDigest, name} so
# promotion consumes the bundle "by exact run ID, attempt, artifact ID, and
# name" and verifies the service-reported digest (03_RELEASE_TRACEABILITY.md,
# subphase 3.2).
#
# The record is written AFTER the bundle upload and uploaded as a second,
# separate pointer artifact: the bundle's own artifact ID/digest cannot be
# embedded inside the bundle it describes (a circular self-checksum), so the
# pointer is the durable external reference. Consumption rejects expired or
# duplicate artifacts via `release_contract.artifact.select_artifact`.
#
# Usage:
#   record-artifact.sh --run-id <n> --run-attempt <n> --artifact-id <n> \
#     --artifact-url <url> --artifact-digest <sha256-hex> \
#     --artifact-name <name> --output <record.json>
#
# Output: {"runId", "runAttempt", "artifactId", "artifactUrl", "artifactDigest", "name"}
# Exit codes: 0 recorded, 1 validation error, 2 usage error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  sed -n '2,22p' "${BASH_SOURCE[0]}" >&2
}

RUN_ID=""
RUN_ATTEMPT=""
ARTIFACT_ID=""
ARTIFACT_URL=""
ARTIFACT_DIGEST=""
ARTIFACT_NAME=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --run-id) RUN_ID="${2:-}"; shift 2 ;;
    --run-attempt) RUN_ATTEMPT="${2:-}"; shift 2 ;;
    --artifact-id) ARTIFACT_ID="${2:-}"; shift 2 ;;
    --artifact-url) ARTIFACT_URL="${2:-}"; shift 2 ;;
    --artifact-digest) ARTIFACT_DIGEST="${2:-}"; shift 2 ;;
    --artifact-name) ARTIFACT_NAME="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$RUN_ID" ] && [ -n "$RUN_ATTEMPT" ] && [ -n "$ARTIFACT_ID" ] && [ -n "$ARTIFACT_URL" ] \
  && [ -n "$ARTIFACT_DIGEST" ] && [ -n "$ARTIFACT_NAME" ] && [ -n "$OUTPUT" ] || { usage; exit 2; }

# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"
rl_assert_positive_integer "$RUN_ID" || { echo "ERROR: invalid --run-id" >&2; exit 1; }
rl_assert_positive_integer "$RUN_ATTEMPT" || { echo "ERROR: invalid --run-attempt" >&2; exit 1; }
rl_assert_positive_integer "$ARTIFACT_ID" || { echo "ERROR: invalid --artifact-id" >&2; exit 1; }
rl_assert_http_url "$ARTIFACT_URL" || { echo "ERROR: invalid --artifact-url" >&2; exit 1; }
# upload-artifact@v4 reports the bare SHA-256 hex of the uploaded archive.
rl_assert_sha256_hex "$ARTIFACT_DIGEST" || { echo "ERROR: invalid --artifact-digest" >&2; exit 1; }

jq -n \
  --argjson runId "$RUN_ID" \
  --argjson runAttempt "$RUN_ATTEMPT" \
  --argjson artifactId "$ARTIFACT_ID" \
  --arg artifactUrl "$ARTIFACT_URL" \
  --arg artifactDigest "$ARTIFACT_DIGEST" \
  --arg name "$ARTIFACT_NAME" \
  '{runId: $runId, runAttempt: $runAttempt, artifactId: $artifactId, artifactUrl: $artifactUrl, artifactDigest: $artifactDigest, name: $name}' \
  > "$OUTPUT"

echo "Recorded artifact id $ARTIFACT_ID (digest $ARTIFACT_DIGEST) for $ARTIFACT_NAME"
echo "Wrote $OUTPUT"
