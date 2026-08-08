#!/usr/bin/env bash
set -euo pipefail

# Renders a schema-valid `candidate` release manifest from a candidate
# evidence bundle and the owner-assigned SemVer (Pass 3, subphase 3.2/3.4).
#
# The 3.2 evidence bundle records immutable facts without a version (the owner
# assigns SemVer at promotion time, Decision 3). This wrapper feeds those facts
# to the fixture-tested `release_contract.candidate build-manifest`, which
# renders the candidate manifest and validates it against the release contract
# before writing it.
#
# Usage:
#   emit-candidate-manifest.sh \
#     --evidence <candidate-evidence.json> \
#     --version <MAJOR.MINOR.PATCH> \
#     --output <release-manifest.candidate.json>
#
# Exit codes: 0 success (schema-valid candidate manifest written),
#             1 invalid version/evidence, 2 usage error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" >&2
}

EVIDENCE=""
VERSION=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --evidence) EVIDENCE="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$EVIDENCE" ] && [ -n "$VERSION" ] && [ -n "$OUTPUT" ] || { usage; exit 2; }
[ -f "$EVIDENCE" ] || { echo "ERROR: evidence file not found: $EVIDENCE" >&2; exit 1; }

rl_assert_semver "$VERSION"

# Build the context/component facts for the manifest builder. Backend
# repositories are taken from the canonical component map; only digests and the
# frontend checksum come from the evidence file.
SOURCE_SHA=$(jq -r '.release.sourceSha' "$EVIDENCE")
REPOSITORY=$(jq -r '.release.repository' "$EVIDENCE")
CREATED_AT=$(jq -r '.createdAt' "$EVIDENCE")
CANDIDATE_RUN_ID=$(jq -r '.release.candidateWorkflow.runId' "$EVIDENCE")
CANDIDATE_RUN_ATTEMPT=$(jq -r '.release.candidateWorkflow.runAttempt' "$EVIDENCE")
ARTIFACT_RUN_ID=$(jq -r '.release.artifactWorkflow.runId' "$EVIDENCE")
ARTIFACT_RUN_ATTEMPT=$(jq -r '.release.artifactWorkflow.runAttempt' "$EVIDENCE")
VALIDATED_AT=$(jq -r '.release.stagingValidation.validatedAt' "$EVIDENCE")

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

jq -n \
  --arg version "$VERSION" \
  --arg sourceSha "$SOURCE_SHA" \
  --arg repository "$REPOSITORY" \
  --arg createdAt "$CREATED_AT" \
  --argjson candidateRunId "$CANDIDATE_RUN_ID" \
  --argjson candidateRunAttempt "$CANDIDATE_RUN_ATTEMPT" \
  --argjson artifactRunId "$ARTIFACT_RUN_ID" \
  --argjson artifactRunAttempt "$ARTIFACT_RUN_ATTEMPT" \
  --arg validatedAt "$VALIDATED_AT" \
  '{version: $version, sourceSha: $sourceSha, repository: $repository, createdAt: $createdAt,
    candidateRunId: $candidateRunId, candidateRunAttempt: $candidateRunAttempt,
    artifactRunId: $artifactRunId, artifactRunAttempt: $artifactRunAttempt, validatedAt: $validatedAt}' \
  > "$TMP/context.json"

jq -n \
  --arg authDigest "$(jq -r '.components.auth.imageDigest' "$EVIDENCE")" \
  --arg itemsDigest "$(jq -r '.components.items.imageDigest' "$EVIDENCE")" \
  --arg gatewayDigest "$(jq -r '.components.apiGateway.imageDigest' "$EVIDENCE")" \
  --arg frontendSha256 "$(jq -r '.components.frontend.sha256' "$EVIDENCE")" \
  '{auth: {repository: "onlineshop-auth", imageDigest: $authDigest},
    items: {repository: "onlineshop-items", imageDigest: $itemsDigest},
    apiGateway: {repository: "onlineshop-api-gateway", imageDigest: $gatewayDigest},
    frontend: {sha256: $frontendSha256}}' \
  > "$TMP/components.json"

PYTHONPATH="$RELEASE/src" python3 -m release_contract.candidate build-manifest \
  --context "$TMP/context.json" \
  --components "$TMP/components.json" \
  --output "$OUTPUT" || {
  echo "ERROR: could not render a schema-valid candidate manifest for version $VERSION" >&2
  exit 1
}

# Read-back: the rendered manifest must validate and its version/git tag must
# match the assigned version.
bash "$RELEASE/bin/validate-manifest.sh" "$OUTPUT" >/dev/null || {
  echo "ERROR: rendered candidate manifest failed validation: $OUTPUT" >&2
  exit 1
}
echo "Wrote schema-valid candidate manifest: $OUTPUT"
echo "Version: $VERSION (git tag v$VERSION)"
