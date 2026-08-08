#!/usr/bin/env bash
set -euo pipefail

# Decides whether a `sha-<full-sha>` candidate image must be pushed or may be
# reused (Pass 3, subphase 3.2, Decision 11 canonical artifact producer).
#
# On the first trusted `main` push for a SHA the tag does not exist -> the run
# must push. On a rerun the tag exists and its OCI producer labels identify the
# original run; the script verifies (a) the label identity (event=push,
# ref=refs/heads/main, revision=current SHA) and (b) via the GitHub API that
# the producer run concluded success, then emits `decision=reuse`. Anything
# else fails closed: reruns must never rebuild and overwrite canonical bytes,
# and SHA tags produced by feature/manual runs must never be claimed as
# canonical (subphase 3.3/3.4 enforce immutability and release tagging).
#
# Feature/dev branches always push (current behavior preserved): their SHA tags
# are never canonical. A manual `workflow_dispatch` on `main` is NOT a producer:
# it pushes only when no `sha-<full-sha>` tag exists yet, and otherwise reuses
# the existing canonical image or fails closed — it must never push rebuilt
# bytes over a canonical tag.
#
# The shell script only gathers data (ECR + docker labels + GitHub API) and
# feeds it as JSON files to the fixture-tested `release_contract.candidate`
# decision; no security-sensitive value is parsed ad-hoc.
#
# Environment inputs:
#   ECR_REGISTRY, ECR_REPOSITORY, CANDIDATE_TAG, GITHUB_SHA (full 40),
#   GITHUB_RUN_ID, GITHUB_RUN_ATTEMPT, GITHUB_EVENT_NAME, GITHUB_REF,
#   GITHUB_REPOSITORY, GITHUB_TOKEN
# AWS args: --profile <p> --region <r>
#
# Output: `decision=push` or `decision=reuse` (also on stdout). Exit non-zero
# (fail closed) when an existing tag is not a trusted reusable canonical image
# or when the existing tag could not be read.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  sed -n '2,32p' "${BASH_SOURCE[0]}" >&2
}

AWS_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --profile) AWS_ARGS+=(--profile "${2:-}"); shift 2 ;;
    --region) AWS_ARGS+=(--region "${2:-}"); shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

emit_decision() {
  local decision="$1"
  echo "decision=$decision"
  if [ -n "${GITHUB_OUTPUT:-}" ]; then
    echo "decision=$decision" >> "$GITHUB_OUTPUT"
  fi
}

# Feature/dev branches always push (Pass 2 behavior preserved; their SHA tags
# are never canonical). This shortcut also keeps manual/dev runs from needing a
# registry/GitHub read at all.
case "${GITHUB_REF:-}" in
  refs/heads/feature/*)
    emit_decision push
    exit 0
    ;;
esac

for var in ECR_REGISTRY ECR_REPOSITORY CANDIDATE_TAG GITHUB_SHA GITHUB_RUN_ID \
  GITHUB_RUN_ATTEMPT GITHUB_EVENT_NAME GITHUB_REF GITHUB_REPOSITORY GITHUB_TOKEN; do
  [ -n "${!var:-}" ] || { echo "ERROR: required environment variable $var is not set" >&2; exit 2; }
done

# Does the immutable candidate tag already exist? Read digest + OCI labels via
# the shared reader (requires docker + registry login, done by the calling job).
# Exit 3 from image-labels.sh means the tag is genuinely absent (push); any
# other failure is a read/decode error and must fail closed, never push over an
# existing canonical image.
set +e
RECORD=$(bash "$RELEASE/bin/image-labels.sh" \
  --registry "$ECR_REGISTRY" --repository "$ECR_REPOSITORY" --tag "$CANDIDATE_TAG" \
  "${AWS_ARGS[@]}" 2>/dev/null)
LABELS_RC=$?
set -e
if [ "$LABELS_RC" -eq 3 ]; then
  emit_decision push
  exit 0
fi
if [ "$LABELS_RC" -ne 0 ]; then
  echo "ERROR: cannot read labels for $ECR_REPOSITORY:$CANDIDATE_TAG (exit $LABELS_RC); failing closed" >&2
  exit 1
fi
DIGEST=$(printf '%s' "$RECORD" | jq -r '.imageDigest')
LABELS=$(printf '%s' "$RECORD" | jq -c '.labels')

echo "Candidate tag $CANDIDATE_TAG already exists ($DIGEST); checking producer trust..."

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

PRODUCER_RUN_ID=$(printf '%s' "$LABELS" | jq -r '."org.onlineshop.producer.run-id" // ""')
PRODUCER_RUN_ATTEMPT=$(printf '%s' "$LABELS" | jq -r '."org.onlineshop.producer.run-attempt" // ""')

# Verify the producer run concluded success on the exact SHA via the GitHub
# API. A missing/failed/unknown conclusion fails closed (a failed run's push is
# not a trusted canonical producer).
PRODUCER_CONCLUSION="unknown"
if [ -n "$PRODUCER_RUN_ID" ] && [ -n "$PRODUCER_RUN_ATTEMPT" ]; then
  RUN_JSON=$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${PRODUCER_RUN_ID}/attempts/${PRODUCER_RUN_ATTEMPT}" 2>/dev/null || true)
  if [ -n "$RUN_JSON" ]; then
    PRODUCER_CONCLUSION=$(printf '%s' "$RUN_JSON" | jq -r '.conclusion // "unknown"')
    PRODUCER_HEAD_SHA=$(printf '%s' "$RUN_JSON" | jq -r '.head_sha // ""')
    if [ "$PRODUCER_HEAD_SHA" != "$GITHUB_SHA" ]; then
      echo "ERROR: producer run head_sha ${PRODUCER_HEAD_SHA} does not match candidate SHA ${GITHUB_SHA}" >&2
      exit 1
    fi
  fi
fi

jq -n \
  --arg digest "$DIGEST" \
  --argjson labels "$LABELS" \
  '{imageDigest: $digest, labels: $labels}' > "$TMP/existing.json"

jq -n \
  --arg sha "$GITHUB_SHA" \
  --argjson runId "$GITHUB_RUN_ID" \
  --argjson runAttempt "$GITHUB_RUN_ATTEMPT" \
  --arg event "$GITHUB_EVENT_NAME" \
  --arg ref "$GITHUB_REF" \
  --arg producerConclusion "$PRODUCER_CONCLUSION" \
  '{sha: $sha, runId: $runId, runAttempt: $runAttempt, event: $event, ref: $ref, producerConclusion: $producerConclusion}' \
  > "$TMP/expected.json"

DECISION_JSON=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.candidate decide \
  --existing "$TMP/existing.json" --expected "$TMP/expected.json") || {
  echo "ERROR: existing $CANDIDATE_TAG cannot be reused (fail closed):" >&2
  printf '%s' "$DECISION_JSON" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

if [ "$(printf '%s' "$DECISION_JSON" | jq -r '.reuse')" = "true" ]; then
  emit_decision reuse
  echo "Reusing existing canonical image $CANDIDATE_TAG ($DIGEST) produced by run ${PRODUCER_RUN_ID}/${PRODUCER_RUN_ATTEMPT}"
else
  echo "ERROR: existing $CANDIDATE_TAG cannot be reused (fail closed):" >&2
  printf '%s' "$DECISION_JSON" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
fi
