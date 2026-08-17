#!/usr/bin/env bash
# Offline verification gate for Pass 3, subphase 3.8 — retention and
# rollback-window enforcement.
#
# The live half of the gate — the real ECR lifecycle policy preview/apply/
# read-back against live AWS, the read-only live retention audit against real
# production state, and the real S3/frontend retention — is deferred to the
# consolidated Pass 3 verification pass and is NOT claimed here. This gate
# proves the offline implementation: the `release_contract.retention`
# decision layer (policy validation, first-match-wins evaluation model,
# ECR-preview validation, rollback-window audit, keep-10 coverage, frontend
# prefix retention, GitHub retention classes), the desired-state lifecycle
# policy document, the retention fixtures, the read-only
# `audit-retention-window.sh` + `preview-retention-policy.sh` runs against a
# stateful AWS stub, the offline-refused `apply-retention-policy.sh`, the
# GitHub artifact retention-days static checks, the mandatory profile/region +
# identity preflight + no-secrets + read-back scans, and
# ruff/shellcheck/`git diff --check`.
set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
RELEASE="$REPO_ROOT/plans/AUTOMATIC-BUILDS-AND-DEPLOY/release"
FX="$RELEASE/fixtures/retention"
POLICY="$RELEASE/ecr/lifecycle-policy.json"
REFERENCE_DATE="2026-08-04T00:00:00Z"

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_contains() {
  local value="$1" expected="$2"
  [[ "$value" == *"$expected"* ]] || fail "expected output to contain: $expected"
}

expect_issue_code() {
  local value="$1" expected="$2"
  printf '%s' "$value" | jq -e --arg code "$expected" '.issues[] | select(.code == $code)' >/dev/null \
    || fail "expected issue code $expected not present in: $value"
}

run_retention() {
  PYTHONPATH="$RELEASE/src" python3 -m release_contract.retention "$@"
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

# ---------------------------------------------------------------------------
echo "[ 1/10] Python syntax + unit tests"
python3 -m py_compile "$RELEASE"/src/release_contract/*.py "$RELEASE"/tests/*.py || fail "Python syntax check failed"
(
  cd "$RELEASE" && PYTHONPATH="$RELEASE/src" python3 -m unittest discover -s tests
) || fail "Python validation tests failed"

# ---------------------------------------------------------------------------
echo "[ 2/10] retention decision-layer CLI against fixtures"
# validate-policy
OUT=$(run_retention validate-policy --policy "$FX/policy.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "desired policy must pass validation"
OUT=$(run_retention validate-policy --policy "$FX/policy-invalid-order.json" 2>&1) \
  && fail "keep-10 rule not first must fail validation"
expect_issue_code "$OUT" "POLICY_RELEASE_RULE_NOT_FIRST"
OUT=$(run_retention validate-policy --policy "$FX/policy-generic-exclusion.json" 2>&1) \
  && fail "generic negative/exclusion policy must fail validation"
expect_issue_code "$OUT" "POLICY_AMBIGUOUS_SELECTION"
expect_issue_code "$OUT" "POLICY_EXCLUSION_FILTER"
OUT=$(run_retention validate-policy --policy "$FX/policy-wrong-counts.json" 2>&1) \
  && fail "wrong retention counts must fail validation"
expect_issue_code "$OUT" "POLICY_RELEASE_RULE_MISCONFIGURED"
expect_issue_code "$OUT" "POLICY_CANDIDATE_RULE_MISCONFIGURED"
expect_issue_code "$OUT" "POLICY_UNTAGGED_RULE_MISCONFIGURED"
# A merged multi-prefix tagged rule is rejected: AWS documents that a
# multi-entry tagPrefixList selects only images carrying ALL the listed tags,
# so a merged rule would silently select nothing.
jq '.rules[2].selection.tagPrefixList = ["main-latest", "branch-"]' "$FX/policy.json" \
  > "$TMP/policy-multi-prefix.json"
OUT=$(run_retention validate-policy --policy "$TMP/policy-multi-prefix.json" 2>&1) \
  && fail "a merged multi-prefix tagged rule must fail validation"
expect_issue_code "$OUT" "POLICY_TAGPREFIX_MULTI"

# evaluate: the modeled first-match-wins evaluation over the multi-tag fixture.
# All 12 release images are pushed BEFORE the 30-day cutoff: the newest 10 by
# push order are KEPT by rule 1 (priority 1) — proving a retained multi-tag
# release image can never be selected by the lower-priority candidate rule.
OUT=$(run_retention evaluate --policy "$FX/policy.json" --images "$FX/images-multitag.json" \
  --reference-date "$REFERENCE_DATE")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "multi-tag evaluation must pass"
for repo in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  EXPIRING=$(printf '%s' "$OUT" | jq -c --arg repo "$repo" '.data.repositories[$repo].expiring')
  jq -e --argjson e "$EXPIRING" '($e | length) == 5' <<<'{}' >/dev/null \
    || fail "exactly 5 images must expire per repository"
  # The newest 10 release images (v1.12.0..v1.3.0) stay kept by rule 1 even
  # though every one of them was pushed more than 30 days ago.
  for i in $(seq 3 12); do
    DIGEST=$(printf 'sha256:1%063d' "$i")
    printf '%s' "$OUT" | jq -e --arg d "$DIGEST" \
      '.data.repositories["onlineshop-auth"].images[] | select(.imageDigest == $d) | .action == "keep" and .appliedRulePriority == 1' >/dev/null \
      || fail "release image $DIGEST must be kept by rule 1 (multi-tag protection)"
  done
  # v1.2.0/v1.1.0 expire by rule 1; sha-*/main-latest candidates by rule 2;
  # the old untagged image by rule 3.
  for i in 1 2; do
    DIGEST=$(printf 'sha256:1%063d' "$i")
    printf '%s' "$OUT" | jq -e --arg d "$DIGEST" \
      '.data.repositories["onlineshop-auth"].images[] | select(.imageDigest == $d) | .action == "expire" and .appliedRulePriority == 1' >/dev/null \
      || fail "oldest release image $DIGEST must expire by rule 1"
  done
  DIGEST=$(printf 'sha256:1%063d' 100)
  printf '%s' "$OUT" | jq -e --arg d "$DIGEST" \
    '.data.repositories["onlineshop-auth"].images[] | select(.imageDigest == $d) | .action == "expire" and .appliedRulePriority == 2' >/dev/null \
    || fail "old sha-* candidate must expire by rule 2 after 30 days"
  DIGEST=$(printf 'sha256:1%063d' 102)
  printf '%s' "$OUT" | jq -e --arg d "$DIGEST" \
    '.data.repositories["onlineshop-auth"].images[] | select(.imageDigest == $d) | .action == "expire" and .appliedRulePriority == 3' >/dev/null \
    || fail "old main-latest convenience tag must expire by rule 3 after 30 days"
  DIGEST=$(printf 'sha256:1%063d' 103)
  printf '%s' "$OUT" | jq -e --arg d "$DIGEST" \
    '.data.repositories["onlineshop-auth"].images[] | select(.imageDigest == $d) | .action == "expire" and .appliedRulePriority == 5' >/dev/null \
    || fail "old untagged image must expire by rule 5 after the grace period"
done

# validate-preview: ECR's own preview results must agree with the model.
OUT=$(run_retention validate-preview --policy "$FX/policy.json" --images "$FX/images-multitag.json" \
  --preview "$FX/evaluator-ok.json" --protected "$FX/protected.json" --reference-date "$REFERENCE_DATE")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "consistent ECR preview must pass"
OUT=$(run_retention validate-preview --policy "$FX/policy.json" --images "$FX/images-multitag.json" \
  --preview "$FX/evaluator-protected-expiring.json" --protected "$FX/protected.json" \
  --reference-date "$REFERENCE_DATE" 2>&1) && fail "a preview expiring a protected release must fail"
expect_issue_code "$OUT" "PROTECTED_IMAGE_EXPIRING"
expect_issue_code "$OUT" "RELEASE_RULE_NOT_APPLIED"
OUT=$(run_retention validate-preview --policy "$FX/policy.json" --images "$FX/images-multitag.json" \
  --preview "$FX/evaluator-disagreement.json" --protected "$FX/protected.json" \
  --reference-date "$REFERENCE_DATE" 2>&1) && fail "a preview disagreeing with the model must fail"
expect_issue_code "$OUT" "PREVIEW_DISAGREEMENT"

# audit: the read-only rollback-window audit.
OUT=$(run_retention audit --index "$FX/index.json" --observed "$FX/observed-audit-ok.json")
jq -e '.valid == true and .data.window == 2 and .data.released == 2' <<<"$OUT" >/dev/null \
  || fail "audit with fewer than 10 releases must list all of them"
jq -e '.data.rollbackCapable == ["1.2.1", "1.1.0"]' <<<"$OUT" >/dev/null \
  || fail "audit must list both complete releases as rollback-capable"
OUT=$(run_retention audit --index "$FX/index.json" --observed "$FX/observed-audit-missing.json" 2>&1) \
  && fail "audit with a missing artifact must fail"
expect_issue_code "$OUT" "RETENTION_ARTIFACT_MISSING"
OUT=$(run_retention audit --index "$FX/index.json" --observed "$FX/observed-audit-mismatch.json" 2>&1) \
  && fail "audit with a mismatched artifact must fail"
expect_issue_code "$OUT" "RETENTION_ARTIFACT_MISMATCH"
# 10-or-all: with 12 official releases the audit reports exactly 10 and never
# claims the older two.
OUT=$(run_retention audit --index "$FX/window-index.json" --observed "$FX/window-observed-ok.json")
jq -e '.valid == true and (.data.rollbackCapable | length) == 10' <<<"$OUT" >/dev/null \
  || fail "audit must report exactly 10 rollback-capable releases"
jq -e '.data.rollbackCapable[0] == "1.12.0" and .data.rollbackCapable[-1] == "1.3.0"' <<<"$OUT" >/dev/null \
  || fail "the newest 10 versions must be the rollback window"
jq -e '.data.outsideWindow == ["1.2.0", "1.1.0"]' <<<"$OUT" >/dev/null \
  || fail "older releases must be reported as outside the window, never rollback-capable"

# coverage: the push-order keep-10 must cover the version-order window.
OUT=$(run_retention coverage --policy "$FX/policy.json" --index "$FX/window-index.json" \
  --observed "$FX/window-observed-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "in-order window must be covered"
OUT=$(run_retention coverage --policy "$FX/policy.json" --index "$FX/window-index.json" \
  --observed "$FX/window-observed-gap.json" 2>&1) \
  && fail "an out-of-order (backport) push must fail the coverage check"
expect_issue_code "$OUT" "POLICY_WINDOW_GAP"

# frontend-retention + retention-classes
OUT=$(run_retention frontend-retention --state "$FX/frontend-prefixes-ok.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "frontend retention plan must pass"
jq -e '.data.expirablePrefixes == ["_releases/v1.1.0/", "_releases/v1.2.0/"]' <<<"$OUT" >/dev/null \
  || fail "only the out-of-window prefixes may be expirable"
OUT=$(run_retention frontend-retention --state "$FX/frontend-prefixes-fail.json" 2>&1) \
  && fail "deleting a protected frontend prefix must fail"
expect_issue_code "$OUT" "FRONTEND_PROTECTED_DELETE"
expect_issue_code "$OUT" "FRONTEND_UNKNOWN_PREFIX"
OUT=$(run_retention retention-classes --config "$FX/retention-classes.json")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "retention classes must pass"
OUT=$(run_retention retention-classes --config "$FX/retention-classes-invalid.json" 2>&1) \
  && fail "invalid retention classes must fail"
expect_issue_code "$OUT" "RETENTION_CLASS_MISMATCH"
expect_issue_code "$OUT" "RETENTION_CLASS_UNKNOWN"

# ---------------------------------------------------------------------------
echo "[ 3/10] Desired-state lifecycle policy static checks"
# The keep-10 release rule has the HIGHEST priority, the candidate age rules
# sit between it and the untagged grace rule (which is last) — rule ORDER is
# load-bearing: ECR evaluates every rule and an image matching a higher-priority
# rule's tagging requirements can never be expired by a lower-priority rule.
jq -e '.rules | map(.rulePriority) == [1, 2, 3, 4, 5]' "$POLICY" >/dev/null \
  || fail "desired policy rule priorities must be [1, 2, 3, 4, 5]"
jq -e '.rules[0].selection.tagStatus == "tagged" and (.rules[0].selection.tagPrefixList | index("release-")) != null and .rules[0].selection.countType == "imageCountMoreThan" and .rules[0].selection.countNumber == 10' "$POLICY" >/dev/null \
  || fail "rule 1 must be the release-* keep-10 rule (highest priority)"
jq -e '.rules[1].selection.tagPrefixList == ["sha-"] and .rules[1].selection.countType == "sinceImagePushed" and .rules[1].selection.countUnit == "days" and .rules[1].selection.countNumber == 30' "$POLICY" >/dev/null \
  || fail "rule 2 must expire sha-* candidates after 30 days"
jq -e '.rules[2].selection.tagPrefixList == ["main-latest"] and .rules[2].selection.countType == "sinceImagePushed" and .rules[2].selection.countUnit == "days" and .rules[2].selection.countNumber == 30' "$POLICY" >/dev/null \
  || fail "rule 3 must expire the main-latest convenience tag after 30 days"
jq -e '.rules[3].selection.tagPrefixList == ["branch-"] and .rules[3].selection.countType == "sinceImagePushed" and .rules[3].selection.countUnit == "days" and .rules[3].selection.countNumber == 30' "$POLICY" >/dev/null \
  || fail "rule 4 must expire branch-* convenience tags after 30 days"
jq -e '.rules[4].selection.tagStatus == "untagged" and .rules[4].selection.countType == "sinceImagePushed" and .rules[4].selection.countUnit == "days" and .rules[4].selection.countNumber == 14' "$POLICY" >/dev/null \
  || fail "rule 5 must expire untagged images after the 14-day grace period"
# ECR schema: every tagged rule must carry an explicit tagPrefixList — a
# generic negative/exclusion rule ('expire everything except releases') is
# not expressible and is never used.
jq -e '.rules[] | select(.selection.tagStatus == "tagged") | .selection.tagPrefixList | length > 0' "$POLICY" >/dev/null \
  || fail "every tagged rule must enumerate its tag prefix list"
# AWS documents that a multi-entry tagPrefixList selects only images carrying
# ALL the listed tags ("only the images with all specified tags are selected")
# — a merged multi-prefix rule would silently select nothing, so every tagged
# rule must select exactly ONE prefix (each candidate family gets its own rule).
jq -e '[.rules[] | select(.selection.tagStatus == "tagged") | .selection.tagPrefixList | length] | all(. == 1)' "$POLICY" >/dev/null \
  || fail "every tagged rule must select exactly one tag prefix (all-specified-tags semantics)"
jq -e '[.. | objects | select(has("tagStatus")) | .tagStatus] | all(. == "tagged" or . == "untagged")' "$POLICY" >/dev/null \
  || fail "the desired policy must never use a generic 'any' selection"
jq -e '[.. | objects | select(has("excludeTaggedImages"))] | length == 0' "$POLICY" >/dev/null \
  || fail "the desired policy must never use negative/exclusion filters"
OUT=$(run_retention validate-policy --policy "$POLICY")
jq -e '.valid == true' <<<"$OUT" >/dev/null || fail "the desired policy document must pass validate-policy"

# ---------------------------------------------------------------------------
echo "[ 4/10] audit-retention-window.sh offline (fixtures)"
OUT=$(bash "$RELEASE/bin/audit-retention-window.sh" \
  --index "$FX/index.json" --observed "$FX/observed-audit-ok.json" \
  --profile dpm-profile --region eu-north-1)
jq -e 'select(.data.rollbackCapable != null) | .valid == true and (.data.rollbackCapable == ["1.2.1", "1.1.0"])' <<<"$OUT" >/dev/null \
  || fail "offline audit must pass and list both releases"
OUT=$(bash "$RELEASE/bin/audit-retention-window.sh" \
  --index "$FX/index.json" --observed "$FX/observed-audit-missing.json" \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "offline audit with a missing artifact must fail"
assert_contains "$OUT" "RETENTION_ARTIFACT_MISSING"
# The 12-release window lists exactly 10 rollback-capable releases.
OUT=$(bash "$RELEASE/bin/audit-retention-window.sh" \
  --index "$FX/window-index.json" --observed "$FX/window-observed-ok.json" \
  --profile dpm-profile --region eu-north-1)
jq -e 'select(.data.rollbackCapable != null) | .valid == true and (.data.rollbackCapable | length) == 10' <<<"$OUT" >/dev/null \
  || fail "offline audit of 12 releases must report exactly 10"
# Wrong mandatory profile/region fails closed.
OUT=$(bash "$RELEASE/bin/audit-retention-window.sh" \
  --index "$FX/index.json" --observed "$FX/observed-audit-ok.json" \
  --profile other --region eu-north-1 2>&1) && fail "wrong profile must be rejected"
assert_contains "$OUT" "mandatory"

# ---------------------------------------------------------------------------
# Stateful AWS stub. Backed by $TMP/state.json, records every call in
# $TMP/calls.txt. The stub serves ECR describe-images (tagged + untagged),
# S3 prefix markers, the lifecycle policy preview APIs, and the identity
# preflight; STUB_IDENTITY overrides the account.
# ---------------------------------------------------------------------------
write_stub_clis() {
  mkdir -p "$TMP/bin"
  cat > "$TMP/bin/aws" <<'PY'
#!/usr/bin/env python3
import json
import os
import sys

state = json.load(open(os.environ["STUB_STATE"], encoding="utf-8"))
calls_path = os.environ["STUB_CALLS"]


def record(line):
    with open(calls_path, "a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def arg(name):
    for index, item in enumerate(sys.argv):
        if item == name and index + 1 < len(sys.argv):
            return sys.argv[index + 1]
    return None


def emit(value):
    print(json.dumps(value, sort_keys=True))


def text(value):
    print(value)


args = sys.argv[1:]
pos = [a for a in args if not a.startswith("-")]
service = pos[0] if len(pos) > 0 else ""
sub = pos[1] if len(pos) > 1 else ""
q = arg("--query") or ""

if service == "sts" and sub == "get-caller-identity":
    record("sts get-caller-identity")
    text(state.get("identity", "799111666795"))
elif service == "ecr" and sub == "describe-images":
    repo = arg("--repository-name") or ""
    record(f"ecr describe-images {repo}")
    if "imageDetails[]" in q:
        emit(state["ecr"].get(repo, {}).get("images", []))
    else:
        emit(state["ecr"].get(repo, {}))
elif service == "ecr" and sub == "start-lifecycle-policy-preview":
    repo = arg("--repository-name") or ""
    record(f"ecr start-lifecycle-policy-preview {repo}")
    text("{}")
elif service == "ecr" and sub == "get-lifecycle-policy-preview":
    repo = arg("--repository-name") or ""
    record(f"ecr get-lifecycle-policy-preview {repo}")
    emit(state.get("previews", {}).get(repo, {"status": "COMPLETE", "previewResults": []}))
elif service == "ecr" and sub == "put-lifecycle-policy":
    repo = arg("--repository-name") or ""
    record(f"ecr put-lifecycle-policy {repo}")
    text("{}")
elif service == "ecr" and sub == "get-lifecycle-policy":
    repo = arg("--repository-name") or ""
    record(f"ecr get-lifecycle-policy {repo}")
    emit(state.get("policies", {}).get(repo, {"lifecyclePolicyText": "{}"}))
elif service == "s3api" and sub == "get-object":
    key = arg("--key") or ""
    record(f"s3api get-object {key}")
    content = state.get("frontend", {}).get(key)
    out = [a for a in args if not a.startswith("-")][-1]
    if content is None:
        print("An error occurred (NoSuchKey): not found", file=sys.stderr)
        sys.exit(255)
    if isinstance(content, dict):
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(content, handle)
    else:
        with open(out, "w", encoding="utf-8") as handle:
            handle.write(str(content))
else:
    record(f"unhandled {service} {sub}")
PY
  chmod +x "$TMP/bin/aws"
}

# Build the stub state from an observed fixture: identity + ecr images +
# frontend objects + (optionally) preview results per repository.
stub_state_from() {
  local observed="$1" previews="${2:-}"
  python3 - "$observed" "$previews" "$TMP/state.json" <<'PY'
import json
import sys

observed, previews, out = sys.argv[1], sys.argv[2] or "", sys.argv[3]
obs = json.load(open(observed, encoding="utf-8"))
state = {"identity": "799111666795", "ecr": obs["ecr"], "frontend": {}}
for key, entry in obs.get("frontend", {}).get("prefixMarkers", {}).items():
    if entry.get("exists"):
        state["frontend"][key] = entry.get("marker")
if previews:
    state["previews"] = {
        repo: {"status": "COMPLETE", **entry} for repo, entry in json.load(open(previews, encoding="utf-8")).items()
    }
json.dump(state, open(out, "w"), indent=2, sort_keys=True)
PY
}

write_stub_clis
export PATH="$TMP/bin:$PATH"
export STUB_STATE="$TMP/state.json"
export STUB_CALLS="$TMP/calls.txt"

# ---------------------------------------------------------------------------
echo "[ 5/10] audit-retention-window.sh live gather path with the AWS stub"
stub_state_from "$FX/observed-audit-ok.json"
: > "$STUB_CALLS"
assert_success() { "$@" >/dev/null 2>&1 || fail "expected success: $*"; }
assert_failure() { if "$@" >/dev/null 2>&1; then fail "expected failure: $*"; fi; }
assert_success bash "$RELEASE/bin/audit-retention-window.sh" \
  --index "$FX/index.json" --profile dpm-profile --region eu-north-1
grep -q "sts get-caller-identity" "$STUB_CALLS" || fail "audit must run the identity preflight"
for repo in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  grep -q "ecr describe-images $repo" "$STUB_CALLS" || fail "audit must describe-images $repo"
done
grep -q "s3api get-object _releases/v1.1.0/release.json" "$STUB_CALLS" \
  || fail "audit must read the immutable frontend prefix markers"
if grep -Eq ' (put|create|update|delete|register|run|start)-' "$STUB_CALLS"; then
  fail "audit-retention-window.sh must be read-only; got: $(grep -E ' (put|create|update|delete|register|run|start)-' "$STUB_CALLS" | head -1)"
fi
# A missing artifact fails closed on the live gather path too.
stub_state_from "$FX/observed-audit-missing.json"
: > "$STUB_CALLS"
OUT=$(bash "$RELEASE/bin/audit-retention-window.sh" \
  --index "$FX/index.json" --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "live audit with a missing artifact must fail"
assert_contains "$OUT" "RETENTION_ARTIFACT_MISSING"
# Wrong account identity fails the mandatory preflight.
stub_state_from "$FX/observed-audit-ok.json"
python3 - "$TMP/state.json" <<PY
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    state = json.load(handle)
state["identity"] = "000000000000"
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(state, handle, indent=2, sort_keys=True)
PY
OUT=$(bash "$RELEASE/bin/audit-retention-window.sh" \
  --index "$FX/index.json" --profile dpm-profile --region eu-north-1 2>&1) \
  && fail "wrong account identity must fail the preflight"
assert_contains "$OUT" "identity preflight failed"

# ---------------------------------------------------------------------------
echo "[ 6/10] preview-retention-policy.sh (offline model + live ECR preview)"
# Offline mode makes NO AWS call at all.
: > "$STUB_CALLS"
OUT=$(bash "$RELEASE/bin/preview-retention-policy.sh" \
  --images "$FX/images-multitag.json" --reference-date "$REFERENCE_DATE" 2>"$TMP/preview.err")
assert_contains "$(cat "$TMP/preview.err")" "REVIEW REQUIRED"
jq -e 'select(.valid == true) | (.data.repositories["onlineshop-auth"].expiring | length) == 5' <<<"$OUT" >/dev/null \
  || fail "offline preview must list the exact 5 expiring images"
[ -s "$STUB_CALLS" ] && fail "offline preview must not call AWS"

# Live preview (read-only start/get-lifecycle-policy-preview) against the
# stub: ECR's own results agree with the model -> pass.
stub_state_from "$FX/observed-preview.json" "$FX/evaluator-ok.json"
: > "$STUB_CALLS"
assert_success bash "$RELEASE/bin/preview-retention-policy.sh" \
  --observed "$FX/observed-preview.json" --reference-date "$REFERENCE_DATE" \
  --profile dpm-profile --region eu-north-1
grep -q "sts get-caller-identity" "$STUB_CALLS" || fail "preview must run the identity preflight"
for repo in onlineshop-auth onlineshop-items onlineshop-api-gateway; do
  grep -q "ecr start-lifecycle-policy-preview $repo" "$STUB_CALLS" \
    || fail "preview must start a lifecycle policy preview for $repo"
  grep -q "ecr get-lifecycle-policy-preview $repo" "$STUB_CALLS" \
    || fail "preview must poll the lifecycle policy preview for $repo"
done
if grep -Eq ' (put|create|update|delete|register|run)-' "$STUB_CALLS"; then
  fail "preview-retention-policy.sh must be read-only; got: $(grep -E ' (put|create|update|delete|register|run)-' "$STUB_CALLS" | head -1)"
fi
# A preview that would expire a protected release fails closed.
stub_state_from "$FX/observed-preview.json" "$FX/evaluator-protected-expiring.json"
: > "$STUB_CALLS"
OUT=$(bash "$RELEASE/bin/preview-retention-policy.sh" \
  --observed "$FX/observed-preview.json" --reference-date "$REFERENCE_DATE" \
  --profile dpm-profile --region eu-north-1 2>&1) && fail "a preview expiring a protected release must fail"
assert_contains "$OUT" "PROTECTED_IMAGE_EXPIRING"

# ---------------------------------------------------------------------------
echo "[ 7/10] apply-retention-policy.sh (dry-run + offline refusal + read-back)"
# The gate must never run with the live-apply gate set.
if [ "${ONLINESHOP_RETENTION_LIVE_APPLY:-}" = "1" ]; then
  fail "the offline gate must never run with ONLINESHOP_RETENTION_LIVE_APPLY=1 set"
fi
# Dry-run (offline) mutates nothing and makes no AWS call.
: > "$STUB_CALLS"
OUT=$(bash "$RELEASE/bin/apply-retention-policy.sh" --dry-run \
  --images "$FX/images-multitag.json" --reference-date "$REFERENCE_DATE" 2>&1)
assert_contains "$OUT" "DRY-RUN"
assert_contains "$OUT" "REVIEW REQUIRED"
[ -s "$STUB_CALLS" ] && fail "apply --dry-run offline must not call AWS"
# --apply is refused offline with a clear deferral message.
OUT=$(bash "$RELEASE/bin/apply-retention-policy.sh" --apply 2>&1) \
  && fail "--apply must be refused without the live gate"
assert_contains "$OUT" "REFUSED"
assert_contains "$OUT" "ONLINESHOP_RETENTION_LIVE_APPLY=1"
# Static proof of the apply path structure: every put-lifecycle-policy is
# immediately followed by a get-lifecycle-policy read-back, and the read-back
# is compared for drift (fail closed). Comments are stripped first so the
# pairing must hold in the actual code, never only in the header comment.
python3 - "$RELEASE/bin/apply-retention-policy.sh" <<'PY' || fail "apply read-back pairing checks failed"
import re
import sys

lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
script = "\n".join(line for line in lines if not line.lstrip().startswith("#"))
problems = []
if "aws ecr put-lifecycle-policy" not in script:
    problems.append("apply must call put-lifecycle-policy")
if "aws ecr get-lifecycle-policy" not in script:
    problems.append("apply must read back with get-lifecycle-policy after put-lifecycle-policy")
put_pos = script.index("put-lifecycle-policy")
get_pos = script.index("get-lifecycle-policy")
if get_pos < put_pos:
    problems.append("read-back (get-lifecycle-policy) must come AFTER the mutation (put-lifecycle-policy)")
if "cmp" not in script and "diff" not in script:
    problems.append("apply must compare the read-back against the desired policy and fail closed on drift")
for name in ("ONLINESHOP_RETENTION_LIVE_APPLY", "REFUSED"):
    if name not in script:
        problems.append(f"apply must contain the offline refusal gate ({name})")
if problems:
    print("\n".join(problems))
    sys.exit(1)
PY

# ---------------------------------------------------------------------------
echo "[ 8/10] GitHub retention classes static checks (checkbox 5)"
python3 - "$REPO_ROOT" "$FX/retention-classes.json" <<'PY' || fail "GitHub retention-days checks failed"
import json
import re
import sys

root, classes_path = sys.argv[1], sys.argv[2]
classes = json.load(open(classes_path, encoding="utf-8"))["classes"]
problems = []


def artifacts_of(workflow_path):
    """(step name, artifact name) -> retention-days (literal ints only)."""
    lines = open(workflow_path, encoding="utf-8").read().splitlines()
    steps = []
    current = None
    for line in lines:
        if line.startswith("      - name:"):
            current = []
            steps.append(current)
        if current is not None:
            current.append(line)
    result = {}
    for block in steps:
        joined = "\n".join(block)
        step_match = re.match(r"\s*- name:\s*(.+)", block[0])
        artifact_match = re.search(r"with:\n\s+name:\s*(\S+)", joined)
        days_match = re.search(r"retention-days:\s*(\d+)", joined)
        if step_match and days_match:
            result[(step_match.group(1).strip(), artifact_match.group(1) if artifact_match else "")] = int(
                days_match.group(1)
            )
    return result


def require_days(path, artifact_substring, expected, label):
    artifacts = artifacts_of(path)
    for (step, artifact), days in artifacts.items():
        if artifact_substring in step or artifact_substring in artifact:
            if days != expected:
                problems.append(f"{path} {step}: retention-days {days}, expected {expected} ({label})")
            return
    problems.append(f"{path}: no artifact named like {artifact_substring} ({label})")


wf = f"{root}/.github/workflows"
# Candidate-only artifacts: 30 days.
require_days(f"{wf}/build-and-deploy.yml", "candidate evidence bundle", 30, "candidate-artifact=30")
require_days(f"{wf}/build-and-deploy.yml", "candidate-artifact-id", 30, "candidate-artifact=30")
# Staging-failure diagnostics: existing shorter operational retention (14 days).
require_days(f"{wf}/build-and-deploy.yml", "staging failure diagnostics", 14, "staging-failure-diagnostics=14")
# Release result / snapshot records: 14 days (operational retention). The
# greenfield promotion/rollback workflows own these artifacts now.
require_days(f"{wf}/promote-release-greenfield.yml", "promotion-snapshot", 14, "rollback-result=14")
require_days(f"{wf}/rollback-release-greenfield.yml", "rollback-evidence", 14, "rollback-result=14")
require_days(f"{wf}/rollback-release-greenfield.yml", "rollback-snapshot", 14, "rollback-result=14")
# The decision layer knows the same classes as the workflows configure.
for name, expected in classes.items():
    if name in ("candidate-artifact", "staging-failure-diagnostics", "rollback-result"):
        continue
    if expected != "indefinite":
        problems.append(f"class {name} must be indefinite, got {expected}")
if problems:
    print("\n".join(problems))
    sys.exit(1)
PY

# ---------------------------------------------------------------------------
echo "[ 9/10] Static scan: mandatory profile/region, identity preflight, no secrets"
for script in \
  "$RELEASE/bin/audit-retention-window.sh" \
  "$RELEASE/bin/preview-retention-policy.sh" \
  "$RELEASE/bin/apply-retention-policy.sh"; do
  # shellcheck disable=SC2016  # literal pattern
  grep -q 'AWS_ARGS=(--profile "$PROFILE" --region "$REGION")' "$script" \
    || fail "$(basename "$script") must default AWS_ARGS to dpm-profile/eu-north-1"
  # shellcheck disable=SC2094  # read-only scan; $script is only read, never written
  while IFS= read -r line; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    # Match only real invocations (line-start or $() command substitution),
    # never `aws` mentioned inside an echo/diagnostic string.
    if [[ "$line" =~ ^[[:space:]]*aws[[:space:]] ]] || [[ "$line" =~ \$\(aws[[:space:]] ]]; then
      # shellcheck disable=SC2016
      [[ "$line" == *'${AWS_ARGS[@]}'* ]] || fail "$(basename "$script") aws call missing AWS_ARGS: $line"
    fi
  done < "$script"
  grep -q "get-caller-identity" "$script" \
    || fail "$(basename "$script") must run the mandatory identity preflight"
  grep -q "release_contract.retention" "$script" \
    || fail "$(basename "$script") must consume the retention decision layer"
done
if rg -n 'PGPASSWORD|password.*[=:]|s3cr3t|plaintext-secret|ghp_[A-Za-z0-9]' \
  "$RELEASE"/bin/audit-retention-window.sh "$RELEASE"/bin/preview-retention-policy.sh \
  "$RELEASE"/bin/apply-retention-policy.sh "$POLICY"; then
  fail "a secret-looking value appears in the retention tooling"
fi
# The audit script must prove its live reads are read-only (identity preflight
# before any other AWS call is the audit script's own contract).
grep -q "identity preflight" "$RELEASE/bin/audit-retention-window.sh" \
  || fail "audit-retention-window.sh must document the identity preflight"

# ---------------------------------------------------------------------------
echo "[10/10] lint"
if command -v ruff >/dev/null 2>&1; then
  (cd "$RELEASE" && ruff check src tests) || fail "ruff lint failed"
  (cd "$RELEASE" && ruff format --check src tests) || fail "ruff format check failed"
else
  echo "ruff not found; skipping (report this)"
fi
if command -v shellcheck >/dev/null 2>&1; then
  shellcheck \
    "$RELEASE"/bin/audit-retention-window.sh \
    "$RELEASE"/bin/preview-retention-policy.sh \
    "$RELEASE"/bin/apply-retention-policy.sh \
    "${BASH_SOURCE[0]}" || fail "shellcheck failed"
else
  echo "shellcheck not found; skipping (report this)"
fi
for script in \
  "$RELEASE/bin/audit-retention-window.sh" \
  "$RELEASE/bin/preview-retention-policy.sh" \
  "$RELEASE/bin/apply-retention-policy.sh"; do
  bash -n "$script" || fail "bash -n failed for $(basename "$script")"
done
bash -n "${BASH_SOURCE[0]}" || fail "bash -n failed for the gate"
if ! git -C "$REPO_ROOT" diff --check >/dev/null 2>&1; then
  fail "git diff --check reports whitespace errors"
fi

echo "Retention tests passed."
