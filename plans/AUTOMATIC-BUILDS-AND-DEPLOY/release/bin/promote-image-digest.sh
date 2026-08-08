#!/usr/bin/env bash
set -euo pipefail

# Promotes one backend candidate image to an immutable `release-<version>` tag
# server-side (Pass 3, subphase 3.3). The exact manifest bytes already stored
# in ECR under `sha-<full-sha>` are re-tagged with `ecr:PutImage`; the image is
# never pulled, rebuilt, or re-uploaded.
#
# The promotion decision is delegated to the fixture-tested
# `release_contract.ecr decide` module:
#   action=mint   -> release tag absent; candidate tag resolves to the recorded
#                    digest; put-image may run.
#   action=reuse  -> release tag already resolves to the recorded digest; this
#                    is an idempotent resume, nothing is mutated.
#   any issue     -> fail closed (candidate missing/mismatched, or release tag
#                    exists at different bytes; immutable tags are never
#                    overwritten).
# After a mint the script immediately reads both tags back and runs
# `release_contract.ecr verify` to prove both resolve to the recorded digest.
#
# Usage:
#   promote-image-digest.sh \
#     --repository <onlineshop-auth|onlineshop-items|onlineshop-api-gateway> \
#     --candidate-tag sha-<full-sha> --release-tag release-<version> \
#     --digest sha256:<hex> \
#     [--dry-run] [--profile dpm-profile] [--region eu-north-1]
#
# Exit 0 on success (mint or reuse); non-zero (fail closed) otherwise.
# `--dry-run` resolves and decides without mutating anything.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,28p' "${BASH_SOURCE[0]}" >&2
}

REPOSITORY=""
CANDIDATE_TAG=""
RELEASE_TAG=""
DIGEST=""
DRY_RUN=0
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --repository) REPOSITORY="${2:-}"; shift 2 ;;
    --candidate-tag) CANDIDATE_TAG="${2:-}"; shift 2 ;;
    --release-tag) RELEASE_TAG="${2:-}"; shift 2 ;;
    --digest) DIGEST="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$REPOSITORY" ] && [ -n "$CANDIDATE_TAG" ] && [ -n "$RELEASE_TAG" ] && [ -n "$DIGEST" ] || { usage; exit 2; }

# Strict validation of the derived identities (subphase 3.1 helpers). Every
# value is passed to commands through argv or JSON files, never interpolated.
SOURCE_SHA="${CANDIDATE_TAG#sha-}"
VERSION="${RELEASE_TAG#release-}"
[ "$CANDIDATE_TAG" = "sha-$SOURCE_SHA" ] || { echo "ERROR: candidate tag must be sha-<full-sha>" >&2; exit 2; }
[ "$RELEASE_TAG" = "release-$VERSION" ] || { echo "ERROR: release tag must be release-<version>" >&2; exit 2; }
rl_assert_full_sha "$SOURCE_SHA" || exit 2
rl_assert_semver "$VERSION" || exit 2
rl_assert_image_digest "$DIGEST" || exit 2

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# Resolve the candidate manifest bytes server-side (batch-get-image returns the
# manifest + service-reported digest without pulling any layers).
CANDIDATE=$(aws ecr batch-get-image "${AWS_ARGS[@]}" \
  --repository-name "$REPOSITORY" \
  --image-ids "imageTag=$CANDIDATE_TAG" \
  --accepted-media-types "application/vnd.docker.distribution.manifest.v2+json" \
    "application/vnd.oci.image.manifest.v1+json" \
    "application/vnd.docker.distribution.manifest.list.v2+json" \
    "application/vnd.oci.image.index.v1+json" \
  --query 'images[0]' --output json 2>/dev/null || true)
if [ -z "$CANDIDATE" ] || [ "$CANDIDATE" = "null" ]; then
  echo "ERROR: candidate tag $CANDIDATE_TAG not found in ECR ($REPOSITORY); fail closed" >&2
  exit 1
fi
CANDIDATE_DIGEST=$(printf '%s' "$CANDIDATE" | jq -r '.imageId.imageDigest // ""')
MANIFEST=$(printf '%s' "$CANDIDATE" | jq -c '.imageManifest // ""')
MEDIA_TYPE=$(printf '%s' "$CANDIDATE" | jq -r '.imageManifestMediaType // ""')
[ -n "$CANDIDATE_DIGEST" ] && [ -n "$MANIFEST" ] && [ -n "$MEDIA_TYPE" ] || {
  echo "ERROR: incomplete batch-get-image result for $CANDIDATE_TAG" >&2
  exit 1
}

# Does the release tag already exist? Absent/None -> null (mint path).
RELEASE_DIGEST=$(aws ecr describe-images "${AWS_ARGS[@]}" \
  --repository-name "$REPOSITORY" \
  --image-ids "imageTag=$RELEASE_TAG" \
  --query 'imageDetails[0].imageDigest' \
  --output text 2>/dev/null || true)
if [ -z "$RELEASE_DIGEST" ] || [ "$RELEASE_DIGEST" = "None" ] || [ "$RELEASE_DIGEST" = "null" ]; then
  RELEASE_DIGEST="null"
else
  RELEASE_DIGEST="\"$RELEASE_DIGEST\""
fi

jq -n --arg candidateDigest "$CANDIDATE_DIGEST" --argjson releaseDigest "$RELEASE_DIGEST" \
  '{candidateDigest: $candidateDigest, releaseDigest: $releaseDigest}' > "$TMP/existing.json"
jq -n \
  --arg version "$VERSION" \
  --arg sourceSha "$SOURCE_SHA" \
  --arg repository "$REPOSITORY" \
  --arg imageDigest "$DIGEST" \
  --arg candidateTag "$CANDIDATE_TAG" \
  --arg releaseTag "$RELEASE_TAG" \
  '{version: $version, sourceSha: $sourceSha, repository: $repository, imageDigest: $imageDigest, candidateTag: $candidateTag, releaseTag: $releaseTag}' \
  > "$TMP/expected.json"

DECISION=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.ecr decide \
  --existing "$TMP/existing.json" --expected "$TMP/expected.json") || {
  echo "ERROR: cannot promote $REPOSITORY to $RELEASE_TAG (fail closed):" >&2
  printf '%s' "$DECISION" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}
ACTION=$(printf '%s' "$DECISION" | jq -r '.action')

if [ "$ACTION" = "reuse" ]; then
  echo "action=reuse"
  echo "Release tag $RELEASE_TAG already resolves to $DIGEST; nothing to do."
elif [ "$DRY_RUN" -eq 1 ]; then
  echo "action=mint (dry-run; no mutation performed)"
  echo "Would tag $REPOSITORY:$RELEASE_TAG -> $DIGEST from $CANDIDATE_TAG"
else
  echo "action=mint"
  aws ecr put-image "${AWS_ARGS[@]}" \
    --repository-name "$REPOSITORY" \
    --image-tag "$RELEASE_TAG" \
    --image-manifest "$MANIFEST" \
    --image-manifest-media-type "$MEDIA_TYPE"
fi

if [ "$DRY_RUN" -eq 1 ]; then
  echo "candidateTag=$CANDIDATE_TAG releaseTag=$RELEASE_TAG digest=$DIGEST"
  exit 0
fi

# Immediate read-back: both tags must resolve to the recorded digest.
CANDIDATE_VERIFY=$(aws ecr describe-images "${AWS_ARGS[@]}" \
  --repository-name "$REPOSITORY" --image-ids "imageTag=$CANDIDATE_TAG" \
  --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)
RELEASE_VERIFY=$(aws ecr describe-images "${AWS_ARGS[@]}" \
  --repository-name "$REPOSITORY" --image-ids "imageTag=$RELEASE_TAG" \
  --query 'imageDetails[0].imageDigest' --output text 2>/dev/null || true)

norm_digest() {
  local value="${1:-}"
  case "$value" in
    "" | "None" | "null") echo "null" ;;
    *) printf '"%s"' "$value" ;;
  esac
}

jq -n \
  --argjson candidateDigest "$(norm_digest "$CANDIDATE_VERIFY")" \
  --argjson releaseDigest "$(norm_digest "$RELEASE_VERIFY")" \
  '{candidateDigest: $candidateDigest, releaseDigest: $releaseDigest}' > "$TMP/verify-existing.json"

VERIFY=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.ecr verify \
  --existing "$TMP/verify-existing.json" --expected "$TMP/expected.json") || {
  echo "ERROR: read-back verification failed for $REPOSITORY:$RELEASE_TAG (fail closed):" >&2
  printf '%s' "$VERIFY" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}
printf '%s' "$VERIFY" | jq -e '.valid == true' >/dev/null || {
  echo "ERROR: read-back verification failed for $REPOSITORY:$RELEASE_TAG (fail closed):" >&2
  printf '%s' "$VERIFY" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

echo "candidateTag=$CANDIDATE_TAG releaseTag=$RELEASE_TAG digest=$DIGEST verified"
