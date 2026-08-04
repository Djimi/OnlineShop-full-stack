#!/usr/bin/env bash
set -euo pipefail

# Reads an ECR image's digest and OCI labels by tag (Pass 3, subphase 3.2).
# Prints one JSON line: {"imageDigest": "<sha256:...>", "labels": {...}}.
#
# Usage:
#   image-labels.sh --registry <registry> --repository <repo> --tag <tag>
#                   [--profile <p>] [--region <r>]
#
# The digest comes from ECR (service-reported, never inferred from the tag
# string). The labels live in the image *config* blob, which
# `docker manifest inspect --verbose` does not expose (it only references the
# config by digest). `docker buildx imagetools inspect --format '{{json .Image}}'`
# fetches just the manifest + config blob from the registry (no layer pull) and
# returns the config directly for single-platform images or a per-platform map
# for indexes. The caller must be logged in to the registry
# (aws-actions/amazon-ecr-login).
#
# Exit codes: 0 labels read, 1 read/decode error, 2 usage error,
#             3 tag not found in ECR (callers may then decide to push).

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" >&2
}

REGISTRY=""
REPOSITORY=""
TAG=""
AWS_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --registry) REGISTRY="${2:-}"; shift 2 ;;
    --repository) REPOSITORY="${2:-}"; shift 2 ;;
    --tag) TAG="${2:-}"; shift 2 ;;
    --profile) AWS_ARGS+=(--profile "${2:-}"); shift 2 ;;
    --region) AWS_ARGS+=(--region "${2:-}"); shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$REGISTRY" ] && [ -n "$REPOSITORY" ] && [ -n "$TAG" ] || { usage; exit 2; }

DIGEST=$(aws ecr describe-images "${AWS_ARGS[@]}" \
  --repository-name "$REPOSITORY" \
  --image-ids "imageTag=$TAG" \
  --query 'imageDetails[0].imageDigest' \
  --output text 2>/dev/null || true)
if [ -z "$DIGEST" ] || [ "$DIGEST" = "None" ]; then
  echo "ERROR: image $REPOSITORY:$TAG not found in ECR" >&2
  exit 3
fi

CONFIG=$(docker buildx imagetools inspect --format '{{json .Image}}' \
  "${REGISTRY}/${REPOSITORY}:${TAG}" 2>/dev/null || true)
if [ -z "$CONFIG" ]; then
  echo "ERROR: cannot inspect ${REGISTRY}/${REPOSITORY}:${TAG}" >&2
  exit 1
fi

# Single-platform images return the config blob directly; an index returns a
# per-platform map. Pick the labels deterministically.
LABELS=$(printf '%s' "$CONFIG" | jq -c '
  if (.config | type) == "object" then .config.Labels // {}
  else [ .[] | select((.config | type) == "object") ][0].config.Labels // {}
  end' 2>/dev/null) || LABELS=""
if [ -z "$LABELS" ]; then
  echo "ERROR: cannot decode image labels for $REPOSITORY:$TAG" >&2
  exit 1
fi

jq -n --arg digest "$DIGEST" --argjson labels "$LABELS" '{imageDigest: $digest, labels: $labels}'
