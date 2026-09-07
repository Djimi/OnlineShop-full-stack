#!/usr/bin/env bash
set -euo pipefail

# Read-only promotion preflight (Pass 3, subphase 3.4). Confirms the manual
# promotion inputs identify one verified candidate before any AWS mutation:
#
#   - the dispatch inputs (version + candidate run id) are valid;
#   - the selected run is a successful `push` on refs/heads/main at the exact
#     candidate SHA with a successful cloud staging E2E job (the staging gate);
#   - the candidate is a descendant of the last official release and reachable
#     from the current `main` (Decision 9, monotonic promotion);
#   - the release identity (git tag / ECR release tags / frontend prefix) is
#     free or resumable;
#   - a database/schema change is blocked unless its migration review is
#     recorded (Decision 8).
#
# This script is strictly read-only. The promotion workflow runs it in the
# approved `promote` job AFTER the protected `production` Environment approval
# and concurrency-lock acquisition; only that run authorizes mutation
# (time-of-check race closure). The pre-approval `preflight` job validates the
# dispatch inputs and the candidate manifest contract without AWS; this full
# preflight adds the run evidence, ancestry, release-identity, and database
# checks.
#
# Usage:
#   promotion-preflight.sh \
#     --manifest <candidate-manifest.json> \
#     [--run <github-run.json>] [--ancestry <ancestry.json>] \
#     [--identity <identity.json>] [--db-change <present|absent>] \
#     [--migration-reviewed <true|false>] \
#     [--profile dpm-profile] [--region eu-north-1]
#
# When --run/--ancestry/--identity are omitted the script gathers them
# read-only from GitHub/AWS (requires GITHUB_REPOSITORY/GITHUB_TOKEN and the
# frontend bucket). Environment inputs: GITHUB_REPOSITORY, GITHUB_TOKEN.
#
# Exit 0 when the preflight passes; 1 when any check fails closed; 2 on usage
# or IO error.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,38p' "${BASH_SOURCE[0]}" >&2
}

MANIFEST=""
RUN=""
ANCESTRY=""
IDENTITY=""
DB_CHANGE="absent"
MIGRATION_REVIEWED="false"
PROFILE="dpm-profile"
REGION="eu-north-1"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --manifest) MANIFEST="${2:-}"; shift 2 ;;
    --run) RUN="${2:-}"; shift 2 ;;
    --ancestry) ANCESTRY="${2:-}"; shift 2 ;;
    --identity) IDENTITY="${2:-}"; shift 2 ;;
    --db-change) DB_CHANGE="${2:-}"; shift 2 ;;
    --migration-reviewed) MIGRATION_REVIEWED="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
# shellcheck disable=SC2034  # AWS_ARGS is the mandatory profile/region contract
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

[ -n "$MANIFEST" ] || { usage; exit 2; }
rl_assert_regular_file "$MANIFEST" || exit 2

# The candidate manifest must be schema-valid before its identity/run evidence
# may be trusted.
bash "$RELEASE/bin/validate-manifest.sh" "$MANIFEST" || {
  echo "ERROR: candidate manifest failed validation; refusing to preflight" >&2
  exit 1
}

VERSION=$(jq -r '.release.version' "$MANIFEST")
SOURCE_SHA=$(jq -r '.release.sourceSha' "$MANIFEST")
GIT_TAG=$(jq -r '.release.gitTag' "$MANIFEST")
RUN_ID=$(jq -r '.release.candidateWorkflow.runId' "$MANIFEST")
RUN_ATTEMPT=$(jq -r '.release.candidateWorkflow.runAttempt' "$MANIFEST")
RELEASE_TAG=$(jq -r '.components.auth.releaseTag' "$MANIFEST")
rl_assert_semver "$VERSION" || exit 2
rl_assert_full_sha "$SOURCE_SHA" || exit 2
rl_assert_positive_integer "$RUN_ID" || exit 2
rl_assert_positive_integer "$RUN_ATTEMPT" || exit 2

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# --- Dispatch inputs (validated, then passed via argv/JSON only) ------------
jq -n --arg version "$VERSION" --argjson runId "$RUN_ID" \
  '{version: $version, runId: $runId}' > "$TMP/dispatch.json"
DISPATCH=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion dispatch \
  --version "$VERSION" --run-id "$RUN_ID") || {
  echo "ERROR: invalid promotion dispatch inputs (fail closed):" >&2
  printf '%s' "$DISPATCH" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

# --- Candidate run evidence -------------------------------------------------
if [ -n "$RUN" ]; then
  rl_assert_regular_file "$RUN" || exit 2
  cp "$RUN" "$TMP/run.json"
else
  [ -n "${GITHUB_REPOSITORY:-}" ] || { echo "ERROR: GITHUB_REPOSITORY is required to resolve the run" >&2; exit 2; }
  command -v gh >/dev/null 2>&1 || { echo "ERROR: gh is required to resolve the run" >&2; exit 2; }
  set +e
  RUN_JSON=$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}/attempts/${RUN_ATTEMPT}" 2>"$TMP/run.err")
  RUN_RC=$?
  set -e
  if [ "$RUN_RC" -ne 0 ] || [ -z "$RUN_JSON" ]; then
    echo "ERROR: cannot read candidate run ${RUN_ID}/attempt ${RUN_ATTEMPT}:" >&2
    cat "$TMP/run.err" >&2 || true
    exit 1
  fi
  # The attempt-scoped endpoint is still an untrusted API response. Require
  # its id to be a JSON number representing a positive integer and to equal
  # the validated run selected from the candidate manifest before consuming
  # any attempt metadata. A missing, string-typed, fractional, or different
  # value must never be able to authorize the jobs read below.
  if ! printf '%s' "$RUN_JSON" | jq -e --argjson expected "$RUN_ID" '
    if (.id | type) != "number" then false
    elif (.id | floor) != .id then false
    elif .id < 1 then false
    else .id == $expected
    end
  ' >/dev/null 2>"$TMP/id.err"; then
    echo "ERROR: candidate run id does not match requested run ID ${RUN_ID} (fail closed):" >&2
    cat "$TMP/id.err" >&2 || true
    exit 1
  fi
  # The attempt-scoped endpoint is still an untrusted API response. Require
  # its run_attempt to be a JSON number representing a positive integer and to
  # equal the validated attempt selected from the candidate manifest. A
  # missing, string-typed, fractional, or different value must never be able
  # to authorize the jobs read below, even when those jobs otherwise pass.
  if ! printf '%s' "$RUN_JSON" | jq -e --argjson expected "$RUN_ATTEMPT" '
    if (.run_attempt | type) != "number" then false
    elif (.run_attempt | floor) != .run_attempt then false
    elif .run_attempt < 1 then false
    else .run_attempt == $expected
    end
  ' >/dev/null 2>"$TMP/attempt.err"; then
    echo "ERROR: candidate run attempt does not match requested attempt ${RUN_ATTEMPT} (fail closed):" >&2
    cat "$TMP/attempt.err" >&2 || true
    exit 1
  fi
  set +e
  JOBS_JSON=$(gh api "repos/${GITHUB_REPOSITORY}/actions/runs/${RUN_ID}/attempts/${RUN_ATTEMPT}/jobs" \
    --paginate --jq '.jobs[] | {name, conclusion}' 2>"$TMP/jobs.err")
  JOBS_RC=$?
  set -e
  if [ "$JOBS_RC" -ne 0 ]; then
    echo "ERROR: cannot read the jobs of candidate run ${RUN_ID}:" >&2
    cat "$TMP/jobs.err" >&2 || true
    exit 1
  fi
  # The GitHub workflow-run API returns `head_branch: "main"` for an
  # allowed push candidate, while the local contract deliberately stores the
  # fully-qualified `refs/heads/main`.  Normalize only that exact
  # event/branch pair.  Any other shape (including an already-qualified ref,
  # another branch, a non-string, or a missing value) becomes null and is
  # rejected by the run decision; never blindly prefix attacker-controlled
  # branch text or trust a contract-shaped value from the API.
  printf '%s' "$RUN_JSON" | jq '{runId: .id, runAttempt: .run_attempt, url: .html_url, event: .event, ref: (if (.event == "push" and (.head_branch | type) == "string" and .head_branch == "main") then "refs/heads/main" else null end), headSha: .head_sha, conclusion: .conclusion}' > "$TMP/run-base.json"
  printf '%s' "$JOBS_JSON" | jq -s '{jobs: (map({key: .name, value: .conclusion}) | from_entries)}' > "$TMP/jobs.json"
  jq -s '.[0] * .[1]' "$TMP/run-base.json" "$TMP/jobs.json" > "$TMP/run.json"
fi
RUN_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion run \
  --run "$TMP/run.json" --source-sha "$SOURCE_SHA") || true
printf '%s' "$RUN_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: candidate run evidence failed (fail closed):" >&2
  printf '%s' "$RUN_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

# --- Ancestry (monotonic + reachable) ----------------------------------------
if [ -n "$ANCESTRY" ]; then
  rl_assert_regular_file "$ANCESTRY" || exit 2
  cp "$ANCESTRY" "$TMP/ancestry.json"
else
  [ -n "${GITHUB_REPOSITORY:-}" ] || { echo "ERROR: GITHUB_REPOSITORY is required to check ancestry" >&2; exit 2; }
  command -v gh >/dev/null 2>&1 || { echo "ERROR: gh is required to check ancestry" >&2; exit 2; }
  # Last official release SHA: the newest canonical `v<semver>` tag resolved to
  # its peeled commit (the compare API needs a commit, not a tag object).
  # Numeric semver ordering avoids v1.9.0 > v1.10.0 string-sort mistakes.
  LAST_OFFICIAL=$(gh api "repos/${GITHUB_REPOSITORY}/tags" \
    --jq '[.[] | select(.name | test("^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$"))
           | {name: .name, sha: .commit.sha,
              parts: (.name[1:] | split(".") | map(tonumber))}]
          | sort_by(.parts) | last // empty' 2>/dev/null || true)
  LAST_OFFICIAL_SHA=""
  LAST_OFFICIAL_VERSION=""
  if [ -n "$LAST_OFFICIAL" ] && [ "$LAST_OFFICIAL" != "null" ]; then
    LAST_OFFICIAL_SHA=$(printf '%s' "$LAST_OFFICIAL" | jq -r '.sha // ""')
    LAST_OFFICIAL_VERSION=$(printf '%s' "$LAST_OFFICIAL" | jq -r '.name' | sed 's/^v//')
  fi
  set -e
  set +e
  CURRENT_MAIN=$(gh api "repos/${GITHUB_REPOSITORY}/git/ref/heads/main" \
    --jq '.object.sha' 2>"$TMP/main.err")
  MAIN_RC=$?
  set -e
  if [ "$MAIN_RC" -ne 0 ] || [ -z "$CURRENT_MAIN" ]; then
    echo "ERROR: cannot read the current main SHA:" >&2
    cat "$TMP/main.err" >&2 || true
    exit 1
  fi
  if [ -n "$LAST_OFFICIAL_SHA" ]; then
    set +e
    DESC_JSON=$(gh api "repos/${GITHUB_REPOSITORY}/compare/${LAST_OFFICIAL_SHA}...${SOURCE_SHA}" \
      --jq '{status: .status, aheadBy: .ahead_by, behindBy: .behind_by}' 2>"$TMP/desc.err")
    DESC_RC=$?
    set -e
    if [ "$DESC_RC" -ne 0 ]; then
      echo "ERROR: cannot compare the last official release to the candidate:" >&2
      cat "$TMP/desc.err" >&2 || true
      exit 1
    fi
  else
    DESC_JSON="null"
  fi
  set +e
  MAIN_JSON=$(gh api "repos/${GITHUB_REPOSITORY}/compare/${CURRENT_MAIN}...${SOURCE_SHA}" \
    --jq '{status: .status, aheadBy: .ahead_by, behindBy: .behind_by}' 2>"$TMP/maincmp.err")
  MAIN_CMP_RC=$?
  set -e
  if [ "$MAIN_CMP_RC" -ne 0 ]; then
    echo "ERROR: cannot compare main to the candidate:" >&2
    cat "$TMP/maincmp.err" >&2 || true
    exit 1
  fi
  jq -n \
    --arg lastVersion "$LAST_OFFICIAL_VERSION" \
    --arg lastSha "${LAST_OFFICIAL_SHA:-}" \
    --arg candidateSha "$SOURCE_SHA" \
    --arg candidateVersion "$VERSION" \
    --argjson descendant "${DESC_JSON:-null}" \
    --argjson reachable "$MAIN_JSON" \
    '{lastOfficialVersion: ($lastVersion | if . == "" then null else . end),
      lastOfficialSha: ($lastSha | if . == "" then null else . end),
      candidateSha: $candidateSha, candidateVersion: $candidateVersion,
      descendantOfOfficial: $descendant, reachableFromMain: $reachable}' \
    > "$TMP/ancestry.json"
fi
ANCESTRY_ISSUES=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion ancestry \
  --ancestry "$TMP/ancestry.json") || true
printf '%s' "$ANCESTRY_ISSUES" | jq -e '.valid == true' >/dev/null 2>&1 || {
  echo "ERROR: candidate ancestry failed (fail closed):" >&2
  printf '%s' "$ANCESTRY_ISSUES" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

# --- Release-identity collision check (read-only) ----------------------------
if [ -n "$IDENTITY" ]; then
  # A process substitution (or a regular file) is accepted so offline/gate
  # runs can pass a pre-built identity decision; its JSON shape is validated by
  # the Python decision layer below.
  cat "$IDENTITY" > "$TMP/identity.json" 2>/dev/null || {
    echo "ERROR: cannot read the identity decision: $IDENTITY" >&2
    exit 2
  }
else
  [ -f "$REPO_ROOT/scripts/config/production.env" ] || { echo "ERROR: missing scripts/config/production.env" >&2; exit 1; }
  # shellcheck source=/dev/null
  source "$REPO_ROOT/scripts/config/production.env"
  IDENTITY_OUT=$(bash "$RELEASE/bin/check-release-identity.sh" \
    --manifest "$MANIFEST" --bucket "$LC_FRONTEND_BUCKET" \
    --profile "$PROFILE" --region "$REGION") || {
    echo "ERROR: release identity collision (fail closed):" >&2
    printf '%s' "$IDENTITY_OUT" | sed 's/^/  /' >&2 || true
    exit 1
  }
  ACTION=$(printf '%s' "$IDENTITY_OUT" | grep -o 'action=[a-z-]*' | head -1 | cut -d= -f2)
  jq -n --arg action "$ACTION" '{action: $action, issues: []}' > "$TMP/identity.json"
fi

# --- Database-change review (Decision 8) --------------------------------------
case "$DB_CHANGE" in
  present|absent) ;;
  *) echo "ERROR: --db-change must be present or absent" >&2; exit 2 ;;
esac
case "$MIGRATION_REVIEWED" in
  true|false) ;;
  *) echo "ERROR: --migration-reviewed must be true or false" >&2; exit 2 ;;
esac
DB_PRESENT=false
[ "$DB_CHANGE" = "present" ] && DB_PRESENT=true
DB_REVIEWED=false
[ "$MIGRATION_REVIEWED" = "true" ] && DB_REVIEWED=true
jq -n --argjson present "$DB_PRESENT" --argjson reviewed "$DB_REVIEWED" \
  '{present: $present, migrationReviewed: $reviewed}' > "$TMP/db-change.json"

# --- Combined preflight decision ----------------------------------------------
jq -s \
  --slurpfile run "$TMP/run.json" \
  --slurpfile ancestry "$TMP/ancestry.json" \
  --slurpfile identity "$TMP/identity.json" \
  --slurpfile dbChange "$TMP/db-change.json" \
  '{run: $run[0], ancestry: $ancestry[0], identity: $identity[0], databaseChange: $dbChange[0]}' \
  /dev/null > "$TMP/observed.json"

PREFLIGHT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.promotion preflight \
  --manifest "$MANIFEST" --observed "$TMP/observed.json") || {
  echo "ERROR: promotion preflight failed (fail closed):" >&2
  printf '%s' "$PREFLIGHT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}
printf '%s' "$PREFLIGHT" | jq -e '.valid == true' >/dev/null || {
  echo "ERROR: promotion preflight failed (fail closed):" >&2
  printf '%s' "$PREFLIGHT" | jq -r '.issues[] | "  [\(.code)] \(.field): \(.message)"' >&2 || true
  exit 1
}

echo "promotion-preflight: OK"
echo "version=$VERSION gitTag=$GIT_TAG sourceSha=$SOURCE_SHA runId=$RUN_ID attempt=$RUN_ATTEMPT"
echo "releaseTag=$RELEASE_TAG dbChange=$DB_CHANGE migrationReviewed=$MIGRATION_REVIEWED"
