#!/usr/bin/env bash
set -euo pipefail

# Read-only release traceability lookups and consistency audit (Pass 3,
# subphase 3.7). Operator queries answered in both directions:
#
#   trace.sh commit  --sha <full-sha>         commit SHA -> candidate run, ECR
#                                             digests, and any official releases
#   trace.sh release --version <semver>       version -> source SHA, components,
#                                             evidence, SBOMs, artifacts (+ live
#                                             ECR/frontend cross-check)
#   trace.sh running                          production -> task-definition ARNs,
#                                             RUNNING image digests
#                                             (tasks[].containers[].imageDigest,
#                                             never only the task-definition
#                                             tag/URI), release identity + approver
#                                             + deployment run, and the frontend
#                                             identity from the deployed
#                                             immutable release.json marker
#                                             (never cache headers). Paused
#                                             production is reported honestly with
#                                             selected task-definition digests and
#                                             last verified deployment evidence;
#                                             a running digest is never fabricated.
#   trace.sh digest  --digest sha256:<hex>    image digest -> ECR tags, OCI
#                                             revision, candidate run, release identity
#   trace.sh audit   [--version <semver>]     manifest <-> ECR digest/tags <->
#                                             ECS running digest <-> frontend
#                                             checksum consistency (read-only; a
#                                             single --version audits only that
#                                             official release, otherwise all)
#
# Every lookup prints machine-readable JSON on stdout and exits 0 only when the
# mapping is found AND consistent; missing, ambiguous, or contradictory
# mappings exit 1 with deterministic {code, field, message} issues; usage/IO
# errors exit 2. `--human` adds a concise human view on stderr.
#
# Live AWS reads require the MANDATORY identity preflight and the mandatory
# --profile dpm-profile --region eu-north-1 (both are non-overridable). The
# manifest index is taken from `--index` (a JSON index {repository,
# manifests: [...]}, or a single manifest) or, when omitted, fetched read-only
# from the GitHub Releases of $GITHUB_REPOSITORY via `gh`. `--observed <file>`
# supplies a pre-built observed-state JSON instead of gathering it live
# (offline/fixture mode; skips the AWS preflight). The live smoke test is the
# same command with neither --observed nor --index, run against real AWS.

RELEASE="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd -- "$RELEASE/../../.." && pwd)"
# shellcheck source=release-input.sh
# shellcheck disable=SC1091  # path is computed at runtime; file is linted explicitly
source "$RELEASE/bin/release-input.sh"

usage() {
  sed -n '2,34p' "${BASH_SOURCE[0]}" >&2
}

SUB=""
SHA=""
VERSION=""
DIGEST=""
INDEX=""
OBSERVED=""
PROFILE="dpm-profile"
REGION="eu-north-1"
HUMAN=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    commit|release|running|digest|audit) SUB="$1"; shift ;;
    --sha) SHA="${2:-}"; shift 2 ;;
    --version) VERSION="${2:-}"; shift 2 ;;
    --digest) DIGEST="${2:-}"; shift 2 ;;
    --index) INDEX="${2:-}"; shift 2 ;;
    --observed) OBSERVED="${2:-}"; shift 2 ;;
    --profile) PROFILE="${2:-}"; shift 2 ;;
    --region) REGION="${2:-}"; shift 2 ;;
    --human) HUMAN="--human"; shift ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

# The mandatory profile/region are non-overridable (AGENTS.md rule).
if [ "$PROFILE" != "dpm-profile" ] || [ "$REGION" != "eu-north-1" ]; then
  echo "ERROR: --profile must be dpm-profile and --region must be eu-north-1 (mandatory)" >&2
  exit 2
fi
AWS_ARGS=(--profile "$PROFILE" --region "$REGION")

case "$SUB" in
  commit) [ -n "$SHA" ] || { usage; exit 2; }; rl_assert_full_sha "$SHA" || exit 2 ;;
  release) [ -n "$VERSION" ] || { usage; exit 2; }; rl_assert_semver "$VERSION" || exit 2 ;;
  running) ;;
  digest) [ -n "$DIGEST" ] || { usage; exit 2; }; rl_assert_image_digest "$DIGEST" || exit 2 ;;
  audit) [ -n "$VERSION" ] && { rl_assert_semver "$VERSION" || exit 2; } ;;
  *) usage; exit 2 ;;
esac

TMP="$(mktemp -d)"
if [ "${TRACE_KEEP_TMP:-}" = "1" ]; then
  echo "trace.sh working directory: $TMP" >&2
  trap '' EXIT
else
  trap 'rm -rf "$TMP"' EXIT
fi

# --- Identity preflight (live mode only) ------------------------------------
trace_verify_identity() {
  local account
  account=$(aws sts get-caller-identity "${AWS_ARGS[@]}" --query 'Account' --output text)
  [ "$account" = "799111666795" ] || {
    echo "ERROR: identity preflight failed: unexpected AWS account $account (expected 799111666795)" >&2
    exit 1
  }
}

# --- Manifest index ----------------------------------------------------------
trace_gather_index() {
  if [ -n "$INDEX" ]; then
    rl_assert_regular_file "$INDEX" || exit 2
    if jq -e 'type == "object" and has("manifests")' "$INDEX" >/dev/null 2>&1; then
      cp "$INDEX" "$TMP/index.json"
    elif jq -e 'type == "object"' "$INDEX" >/dev/null 2>&1; then
      # A single manifest is wrapped into an index.
      jq --arg repository "${GITHUB_REPOSITORY:-unknown}" \
        '{repository: $repository, manifests: [.]}' "$INDEX" > "$TMP/index.json"
    else
      echo "ERROR: --index is neither a manifest index nor a single manifest" >&2
      exit 2
    fi
    return 0
  fi
  [ -n "${GITHUB_REPOSITORY:-}" ] || {
    echo "ERROR: no --index supplied and GITHUB_REPOSITORY is unset" >&2
    exit 2
  }
  command -v gh >/dev/null 2>&1 || {
    echo "ERROR: no --index supplied and gh is unavailable" >&2
    exit 2
  }
  local release_tags
  set +e
  release_tags=$(gh api "repos/${GITHUB_REPOSITORY}/releases" --paginate --jq '.[].tag_name' 2>"$TMP/gh.err")
  local gh_rc=$?
  set -e
  if [ "$gh_rc" -ne 0 ]; then
    echo "ERROR: cannot list GitHub releases (read-only):" >&2
    cat "$TMP/gh.err" >&2 || true
    exit 1
  fi
  jq -n --arg repository "$GITHUB_REPOSITORY" '{repository: $repository, manifests: []}' \
    > "$TMP/index.json"
  [ -n "$release_tags" ] || { echo "WARNING: no GitHub releases found for $GITHUB_REPOSITORY" >&2; return 0; }
  local tag assets_json asset_id manifest_path
  while IFS= read -r tag; do
    [ -n "$tag" ] || continue
    manifest_path="$TMP/release-manifest-${tag}.json"
    if ! assets_json=$(gh api "repos/${GITHUB_REPOSITORY}/releases/tags/${tag}" \
      --jq '.assets[] | {name, id}' 2>"$TMP/gh.err"); then
      echo "WARNING: cannot read release $tag metadata; skipping" >&2
      continue
    fi
    # The canonical release manifest asset is named release-manifest.json
    # (defined by the 3.4 publication contract); any other asset that merely
    # contains "manifest" (e.g. a checksums file) must never be consumed.
    asset_id=$(printf '%s' "$assets_json" | jq -r 'select(.name == "release-manifest.json") | .id' | head -1)
    [ -n "$asset_id" ] && [ "$asset_id" != "null" ] || {
      echo "WARNING: release $tag has no release-manifest.json asset; skipping" >&2
      continue
    }
    if ! gh api "repos/${GITHUB_REPOSITORY}/releases/assets/${asset_id}" \
      -H "Accept: application/octet-stream" > "$manifest_path" 2>"$TMP/gh.err"; then
      echo "ERROR: cannot download the manifest asset of release $tag (read-only):" >&2
      cat "$TMP/gh.err" >&2 || true
      exit 1
    fi
    jq -e 'type == "object"' "$manifest_path" >/dev/null 2>&1 || {
      echo "ERROR: release $tag manifest asset is not a JSON object" >&2
      exit 1
    }
    jq --argjson manifest "$(cat "$manifest_path")" '.manifests += [$manifest]' \
      "$TMP/index.json" > "$TMP/index.next.json"
    mv "$TMP/index.next.json" "$TMP/index.json"
  done <<< "$release_tags"
}

# --- Live observed state (read-only AWS reads) -------------------------------
trace_load_config() {
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
  [ "$LC_ACCOUNT_ID" = "799111666795" ] || {
    echo "ERROR: scripts/config/production.env account drift" >&2
    exit 1
  }
}

trace_gather_ecr() {
  jq -n '{}' > "$TMP/ecr.json"
  local repo images rc err
  for repo in "${LC_ECR_REPOSITORIES[@]:-}"; do
    set +e
    images=$(aws ecr describe-images "${AWS_ARGS[@]}" --repository-name "$repo" \
      --filter tagStatus=TAGGED --query 'imageDetails[]' --output json 2>"$TMP/ecr.err")
    rc=$?
    set -e
    images=${images:-[]}
    if [ "$rc" -eq 0 ]; then
      jq --arg repo "$repo" --argjson images "$images" \
        '. + {($repo): {images: $images}}' "$TMP/ecr.json" > "$TMP/ecr.next.json"
    else
      err=$(head -1 "$TMP/ecr.err" 2>/dev/null || true)
      [ -n "$err" ] || err="describe-images exited $rc"
      jq --arg repo "$repo" --arg err "$err" \
        '. + {($repo): {images: [], error: $err}}' "$TMP/ecr.json" > "$TMP/ecr.next.json"
    fi
    mv "$TMP/ecr.next.json" "$TMP/ecr.json"
  done
}

trace_gather_ecs() {
  jq -n --arg cluster "${LC_CLUSTER:-}" '{cluster: $cluster, running: [], services: {}, taskDefinitions: {}}' \
    > "$TMP/ecs.json"
  local task_arns rc err count
  set +e
  task_arns=$(aws ecs list-tasks "${AWS_ARGS[@]}" --cluster "${LC_CLUSTER:-}" \
    --query 'taskArns' --output json 2>"$TMP/ecs.err")
  rc=$?
  set -e
  task_arns=${task_arns:-[]}
  if [ "$rc" -ne 0 ]; then
    err=$(head -1 "$TMP/ecs.err" 2>/dev/null || true)
    [ -n "$err" ] || err="list-tasks exited $rc"
    jq --arg err "$err" '.error = $err' "$TMP/ecs.json" > "$TMP/ecs.next.json"
    mv "$TMP/ecs.next.json" "$TMP/ecs.json"
    return 0
  fi
  count=$(printf '%s' "$task_arns" | jq 'length')
  if [ "$count" -gt 0 ]; then
    local tasks running_json
    mapfile -t task_arns_list < <(printf '%s' "$task_arns" | jq -r '.[]')
    set +e
    tasks=$(aws ecs describe-tasks "${AWS_ARGS[@]}" --cluster "${LC_CLUSTER:-}" \
      --tasks "${task_arns_list[@]}" --query 'tasks[]' --output json 2>"$TMP/ecs.err")
    rc=$?
    set -e
    tasks=${tasks:-[]}
    if [ "$rc" -ne 0 ]; then
      err=$(head -1 "$TMP/ecs.err" 2>/dev/null || true)
      [ -n "$err" ] || err="describe-tasks exited $rc"
      jq --arg err "$err" '.error = $err' "$TMP/ecs.json" > "$TMP/ecs.next.json"
      mv "$TMP/ecs.next.json" "$TMP/ecs.json"
      return 0
    fi
    running_json=$(printf '%s' "$tasks" | jq '[.[] | {taskArn, taskDefinitionArn, lastStatus, containers: [.containers[]? | {name, imageDigest}]}]')
    jq --argjson running "$running_json" '.running = $running' "$TMP/ecs.json" > "$TMP/ecs.next.json"
    mv "$TMP/ecs.next.json" "$TMP/ecs.json"
  fi
  local services_json services_map svc
  set +e
  services_json=$(aws ecs describe-services "${AWS_ARGS[@]}" --cluster "${LC_CLUSTER:-}" \
    --services "${LC_SERVICES[@]:-}" --query 'services[].{serviceName: serviceName, taskDefinition: taskDefinition}' \
    --output json 2>"$TMP/ecs.err")
  rc=$?
  set -e
  services_json=${services_json:-[]}
  if [ "$rc" -ne 0 ]; then
    err=$(head -1 "$TMP/ecs.err" 2>/dev/null || true)
    [ -n "$err" ] || err="describe-services exited $rc"
    jq --arg err "$err" '.error = $err' "$TMP/ecs.json" > "$TMP/ecs.next.json"
    mv "$TMP/ecs.next.json" "$TMP/ecs.json"
    return 0
  fi
  services_map=$(printf '%s' "$services_json" | jq '[.[] | {key: .serviceName, value: {taskDefinition: .taskDefinition}}] | from_entries')
  jq --argjson services "$services_map" '.services = $services' "$TMP/ecs.json" > "$TMP/ecs.next.json"
  mv "$TMP/ecs.next.json" "$TMP/ecs.json"
  # Every configured production service must be described with a task
  # definition; a service the API omitted (wrong name, not found, partial
  # response) or returned without a taskDefinition is recorded as an error
  # marker so the lookup fails closed instead of silently losing that
  # service's task-definition evidence.
  while IFS= read -r svc; do
    [ -n "$svc" ] || continue
    if ! jq -e --arg s "$svc" '.services[$s]?.taskDefinition != null' "$TMP/ecs.json" >/dev/null 2>&1; then
      jq --arg s "$svc" \
        '.services[$s] = {taskDefinition: null, error: "service not returned by describe-services or has no taskDefinition"}' \
        "$TMP/ecs.json" > "$TMP/ecs.next.json"
      mv "$TMP/ecs.next.json" "$TMP/ecs.json"
    fi
  done <<< "$(printf '%s\n' "${LC_SERVICES[@]:-}")"
  # Task-definition digests are resolved only when nothing is running, so a
  # paused environment never fabricates a running digest.
  if [ "$count" -eq 0 ]; then
    local svc td famrev image digest
    for svc in "${LC_SERVICES[@]:-}"; do
      td=$(printf '%s' "$services_map" | jq -r --arg s "$svc" '.[$s].taskDefinition // ""')
      [ -n "$td" ] || continue
      famrev=$(printf '%s' "$td" | sed 's#.*task-definition/##')
      set +e
      image=$(aws ecs describe-task-definition "${AWS_ARGS[@]}" --task-definition "$td" \
        --query 'taskDefinition.containerDefinitions[0].image' --output text 2>"$TMP/ecs.err")
      rc=$?
      set -e
      if [ "$rc" -ne 0 ]; then
        err=$(head -1 "$TMP/ecs.err" 2>/dev/null || true)
        [ -n "$err" ] || err="describe-task-definition exited $rc"
        jq --arg key "$famrev" --arg err "$err" \
          '.taskDefinitions[$key] = {imageDigest: null, error: $err}' "$TMP/ecs.json" > "$TMP/ecs.next.json"
        mv "$TMP/ecs.next.json" "$TMP/ecs.json"
        continue
      fi
      digest=$(printf '%s' "$image" | sed -n 's#.*@sha256:\([0-9a-f]\{64\}\).*#sha256:\1#p')
      if [ -z "$digest" ]; then
        jq --arg key "$famrev" '.taskDefinitions[$key] = {imageDigest: null}' "$TMP/ecs.json" > "$TMP/ecs.next.json"
      else
        jq --arg key "$famrev" --arg d "$digest" '.taskDefinitions[$key] = {imageDigest: $d}' "$TMP/ecs.json" > "$TMP/ecs.next.json"
      fi
      mv "$TMP/ecs.next.json" "$TMP/ecs.json"
    done
  fi
}

trace_gather_frontend() {
  local live_exists=0 marker_json rc err
  set +e
  aws s3api get-object "${AWS_ARGS[@]}" --bucket "${LC_FRONTEND_BUCKET:-}" \
    --key release.json "$TMP/live.json" >/dev/null 2>"$TMP/frontend.err"
  rc=$?
  set -e
  if [ "$rc" -eq 0 ]; then
    live_exists=1
    marker_json=$(cat "$TMP/live.json")
  elif ! grep -qiE 'not ?found|does not exist|NoSuchKey|not be found' "$TMP/frontend.err"; then
    err=$(head -1 "$TMP/frontend.err" 2>/dev/null || true)
    [ -n "$err" ] || err="get-object release.json exited $rc"
    jq -n --arg err "$err" '{liveMarker: {exists: false, marker: null, error: $err}}' > "$TMP/frontend.json"
    return 0
  fi
  if [ "$live_exists" -eq 1 ]; then
    if printf '%s' "$marker_json" | jq -e 'type == "object"' >/dev/null 2>&1; then
      jq -n --argjson exists true --argjson marker "$marker_json" \
        '{liveMarker: {exists: $exists, marker: $marker}}' > "$TMP/frontend.json"
    else
      # A malformed live marker is a read problem, never silent drift.
      jq -n --arg err "live release.json marker is not a JSON object" \
        '{liveMarker: {exists: true, marker: null, error: $err}}' > "$TMP/frontend.json"
      return 0
    fi
  else
    jq -n '{liveMarker: {exists: false, marker: null}}' > "$TMP/frontend.json"
  fi
  local prefix marker_name key
  while IFS=$'\t' read -r prefix marker_name; do
    [ -n "$prefix" ] && [ -n "$marker_name" ] || continue
    key="${prefix}${marker_name}"
    set +e
    aws s3api get-object "${AWS_ARGS[@]}" --bucket "${LC_FRONTEND_BUCKET:-}" \
      --key "$key" "$TMP/prefix.json" >/dev/null 2>"$TMP/frontend.err"
    rc=$?
    set -e
    if [ "$rc" -eq 0 ]; then
      if printf '%s' "$(cat "$TMP/prefix.json")" | jq -e 'type == "object"' >/dev/null 2>&1; then
        jq --arg key "$key" --argjson marker "$(cat "$TMP/prefix.json")" \
          '.prefixMarkers[$key] = {exists: true, marker: $marker}' "$TMP/frontend.json" > "$TMP/frontend.next.json"
      else
        jq --arg key "$key" --arg err "prefix release.json marker is not a JSON object" \
          '.prefixMarkers[$key] = {exists: true, marker: null, error: $err}' "$TMP/frontend.json" > "$TMP/frontend.next.json"
      fi
    elif grep -qiE 'not ?found|does not exist|NoSuchKey|not be found' "$TMP/frontend.err"; then
      jq --arg key "$key" '.prefixMarkers[$key] = {exists: false, marker: null}' "$TMP/frontend.json" > "$TMP/frontend.next.json"
    else
      err=$(head -1 "$TMP/frontend.err" 2>/dev/null || true)
      [ -n "$err" ] || err="get-object $key exited $rc"
      jq --arg key "$key" --arg err "$err" \
        '.prefixMarkers[$key] = {exists: false, marker: null, error: $err}' "$TMP/frontend.json" > "$TMP/frontend.next.json"
    fi
    mv "$TMP/frontend.next.json" "$TMP/frontend.json"
  done < <(jq -r '.manifests[] | select(.release.status == "official") | .components.frontend | [.releasePrefix, .versionMarker] | @tsv' "$TMP/index.json")
}

trace_gather_observed() {
  if [ -n "$OBSERVED" ]; then
    rl_assert_regular_file "$OBSERVED" || exit 2
    cp "$OBSERVED" "$TMP/observed.json"
    return 0
  fi
  trace_verify_identity
  trace_load_config
  trace_gather_ecr
  case "$SUB" in
    running|audit) trace_gather_ecs ;;
    *) jq -n '{cluster: "", running: [], services: {}, taskDefinitions: {}}' > "$TMP/ecs.json" ;;
  esac
  case "$SUB" in
    release|running|audit) trace_gather_frontend ;;
    *) jq -n '{liveMarker: {exists: false, marker: null}, prefixMarkers: {}}' > "$TMP/frontend.json" ;;
  esac
  jq -s '{ecr: .[0], ecs: .[1], frontend: .[2]}' "$TMP/ecr.json" "$TMP/ecs.json" "$TMP/frontend.json" \
    > "$TMP/observed.json"
}

# --- Main --------------------------------------------------------------------
trace_gather_index
trace_gather_observed

PY_SUB="$SUB"
PY_ARGS=(--index "$TMP/index.json" --observed "$TMP/observed.json")
if [ -n "$HUMAN" ]; then
  PY_ARGS+=(--human)
fi
case "$SUB" in
  commit) PY_SUB="by-sha"; PY_ARGS+=("$SHA") ;;
  release) PY_SUB="by-version"; PY_ARGS+=("$VERSION") ;;
  digest) PY_SUB="by-digest"; PY_ARGS+=("$DIGEST") ;;
  running) PY_SUB="running" ;;
  audit) PY_SUB="audit"; [ -n "$VERSION" ] && PY_ARGS+=(--version "$VERSION") ;;
esac

set +e
OUT=$(PYTHONPATH="$RELEASE/src" python3 -m release_contract.traceability "$PY_SUB" "${PY_ARGS[@]}" 2>"$TMP/python.err")
RC=$?
set -e

printf '%s\n' "$OUT"
if [ -s "$TMP/python.err" ]; then
  cat "$TMP/python.err" >&2 || true
fi
exit "$RC"
