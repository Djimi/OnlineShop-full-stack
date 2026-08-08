#!/usr/bin/env bash
set -euo pipefail

# Validates a production release task definition (Pass 3, subphase 3.5).
# Delegates to the fixture-tested `release_contract.ecs_config` module:
#   - Fargate awsvpc + a valid task-level CPU/memory pair
#   - digest-pinned container images (never a floating tag)
#   - versionConsistency=enabled, container health check, awslogs logging
#   - named Service Connect port mappings, positive stopTimeout
#   - execution role present; every secret injected only via secrets[].valueFrom
#     with a FULL arn:aws:secretsmanager: ARN and never repeated in
#     environment/command plaintext
#
# Usage:
#   validate-task-definition.sh --input <task-definition.json>
#
# Exit 0 when valid; 1 when invalid; 2 on usage/IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,18p' "${BASH_SOURCE[0]}" >&2
}

INPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --input) INPUT="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

[ -n "$INPUT" ] || { usage; exit 2; }
rl_assert_regular_file "$INPUT" || exit 2

RESULT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.ecs_config validate-td \
  --input "$INPUT") || {
  printf '%s' "$RESULT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  echo "ERROR: task definition failed hardening validation" >&2
  exit 1
}
printf '%s' "$RESULT" | jq -e '.valid == true' >/dev/null || {
  printf '%s' "$RESULT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  echo "ERROR: task definition failed hardening validation" >&2
  exit 1
}
echo "OK: task definition passed production hardening validation."
