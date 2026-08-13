#!/usr/bin/env bash

# Port-allocation mechanics used by scripts/dev-env.sh.
#
# Predicate functions follow one convention: 0 means yes, 1 means no, and 2
# means the check itself failed. Extractors print their result and return 2 on
# failure. An empty result from dev_env_parse_claim means the .env is unmanaged.

readonly DEV_ENV_SLOT_COUNT=631
readonly DEV_ENV_BLOCK_SIZE=20
readonly DEV_ENV_BASE_PORT=20000
readonly DEV_ENV_LOCK_TIMEOUT_SECONDS=30
readonly DEV_ENV_MARKER_START="# >>> dev-env (managed by scripts/dev-env.sh — do not edit between the markers)"
readonly DEV_ENV_MARKER_END="# <<< dev-env"

declare -ar DEV_ENV_PORT_KEYS=(
  GATEWAY_PORT
  ITEMS_PORT
  AUTH_PORT
  FRONTEND_PORT
  ITEMS_DB_PORT
  AUTH_DB_PORT
  PGADMIN_PORT
  REDIS_PORT
  KAFKA_HOST_PORT
  KAFKA_UI_PORT
)

declare -Ar DEV_ENV_PORT_OFFSETS=(
  [GATEWAY_PORT]=0
  [ITEMS_PORT]=1
  [AUTH_PORT]=2
  [FRONTEND_PORT]=3
  [ITEMS_DB_PORT]=4
  [AUTH_DB_PORT]=5
  [PGADMIN_PORT]=6
  [REDIS_PORT]=7
  [KAFKA_HOST_PORT]=8
  [KAFKA_UI_PORT]=9
)

declare -Ar DEV_ENV_MAIN_PORTS=(
  [GATEWAY_PORT]=10000
  [ITEMS_PORT]=9000
  [AUTH_PORT]=9001
  [FRONTEND_PORT]=5173
  [ITEMS_DB_PORT]=5432
  [AUTH_DB_PORT]=5433
  [PGADMIN_PORT]=5051
  [REDIS_PORT]=6379
  [KAFKA_HOST_PORT]=9092
  [KAFKA_UI_PORT]=8080
)

DEV_ENV_ROOT=""
DEV_ENV_GIT_DIR=""
DEV_ENV_GIT_COMMON=""
DEV_ENV_ENV_FILE=""
DEV_ENV_LOCK_FILE=""
DEV_ENV_CLAIMS_LOADED=0
declare -a DEV_ENV_WORKTREE_RECORDS=()
declare -A DEV_ENV_CLAIMANT_BY_SLOT=()

dev_env_error() {
  echo "ERROR: $*" >&2
}

# Fail immediately when a future service or main-checkout port violates the
# slot model. Every assigned offset must fit once inside the 20-port block, and
# every main port must stay outside the complete worktree allocation range.
dev_env_validate_port_configuration() {
  local key offset main_port
  local first_worktree_port last_worktree_port
  local -A used_offsets=() used_main_ports=()

  first_worktree_port=$((DEV_ENV_BASE_PORT + DEV_ENV_BLOCK_SIZE))
  last_worktree_port=$((
    DEV_ENV_BASE_PORT + DEV_ENV_SLOT_COUNT * DEV_ENV_BLOCK_SIZE + DEV_ENV_BLOCK_SIZE - 1
  ))

  for key in "${DEV_ENV_PORT_KEYS[@]}"; do
    if [ -z "${DEV_ENV_PORT_OFFSETS[$key]+set}" ]; then
      dev_env_error "$key has no worktree slot offset"
      return 2
    fi
    offset="${DEV_ENV_PORT_OFFSETS[$key]}"
    if ! [[ "$offset" =~ ^[0-9]+$ ]] || [ "$offset" -ge "$DEV_ENV_BLOCK_SIZE" ]; then
      dev_env_error "$key offset $offset is outside the $DEV_ENV_BLOCK_SIZE-port slot block"
      return 2
    fi
    if [ -n "${used_offsets[$offset]+set}" ]; then
      dev_env_error "$key and ${used_offsets[$offset]} both use slot offset $offset"
      return 2
    fi
    used_offsets[$offset]="$key"

    if [ -z "${DEV_ENV_MAIN_PORTS[$key]+set}" ]; then
      dev_env_error "$key has no main-checkout port"
      return 2
    fi
    main_port="${DEV_ENV_MAIN_PORTS[$key]}"
    if ! [[ "$main_port" =~ ^[1-9][0-9]*$ ]] || [ "$main_port" -gt 65535 ]; then
      dev_env_error "$key main port $main_port is outside valid TCP ports 1-65535"
      return 2
    fi
    if [ "$main_port" -ge "$first_worktree_port" ] &&
       [ "$main_port" -le "$last_worktree_port" ]; then
      dev_env_error "$key main port $main_port overlaps worktree range $first_worktree_port-$last_worktree_port"
      return 2
    fi
    if [ -n "${used_main_ports[$main_port]+set}" ]; then
      dev_env_error "$key and ${used_main_ports[$main_port]} both use main port $main_port"
      return 2
    fi
    used_main_ports[$main_port]="$key"
  done
}

# Repository identity and worktree discovery --------------------------------

# Git's NUL-delimited porcelain format preserves spaces, newlines, and other
# unusual characters in worktree paths. Capture it in a file so the Git exit
# status is checked directly; process substitution would hide that status.
dev_env_read_worktree_records() {
  local records_file

  records_file="$(mktemp)" || {
    dev_env_error "cannot create a temporary worktree-registry file"
    return 2
  }
  if ! git -C "$DEV_ENV_ROOT" worktree list --porcelain -z > "$records_file"; then
    dev_env_error "git worktree list failed; refusing to use an incomplete worktree registry"
    rm -f "$records_file"
    return 2
  fi
  if ! mapfile -d '' -t DEV_ENV_WORKTREE_RECORDS < "$records_file"; then
    dev_env_error "cannot read the captured worktree registry"
    rm -f "$records_file"
    return 2
  fi
  if ! rm -f "$records_file"; then
    dev_env_error "cannot remove temporary worktree-registry file $records_file"
    return 2
  fi
}

dev_env_init() {
  dev_env_validate_port_configuration || return
  DEV_ENV_WORKTREE_RECORDS=()

  DEV_ENV_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    dev_env_error "not inside a git repository"
    return 2
  }
  DEV_ENV_ROOT="$(realpath "$DEV_ENV_ROOT")" || {
    dev_env_error "cannot resolve the repository root"
    return 2
  }

  DEV_ENV_GIT_DIR="$(git -C "$DEV_ENV_ROOT" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || {
    dev_env_error "cannot determine the Git directory for $DEV_ENV_ROOT"
    return 2
  }
  DEV_ENV_GIT_COMMON="$(git -C "$DEV_ENV_ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || {
    dev_env_error "cannot determine the Git common directory for $DEV_ENV_ROOT"
    return 2
  }
  DEV_ENV_GIT_DIR="$(realpath -m "$DEV_ENV_GIT_DIR")" || {
    dev_env_error "cannot resolve the Git directory"
    return 2
  }
  DEV_ENV_GIT_COMMON="$(realpath -m "$DEV_ENV_GIT_COMMON")" || {
    dev_env_error "cannot resolve the Git common directory"
    return 2
  }
  DEV_ENV_ENV_FILE="$DEV_ENV_ROOT/.env"
  DEV_ENV_LOCK_FILE="$DEV_ENV_GIT_COMMON/dev-env-allocation.lock"
}

dev_env_is_main_checkout() {
  # A linked worktree has its own git-dir below <common>/worktrees/. The main
  # checkout uses the common directory itself, including --separate-git-dir
  # repositories whose porcelain worktree path points at the Git directory.
  [ "$DEV_ENV_GIT_DIR" = "$DEV_ENV_GIT_COMMON" ]
}

# Slot and listener calculations --------------------------------------------

dev_env_hash_slot() {
  local name="$1" hash

  command -v md5sum >/dev/null || {
    dev_env_error "md5sum is required for deterministic slot selection"
    return 2
  }
  hash="$(printf '%s' "$name" | md5sum | cut -c1-8)" || return 2
  echo $(( (16#$hash % DEV_ENV_SLOT_COUNT) + 1 ))
}

dev_env_port_for_offset() {
  local slot="$1" offset="$2"
  echo $((DEV_ENV_BASE_PORT + slot * DEV_ENV_BLOCK_SIZE + offset))
}

dev_env_port_for_slot() {
  local slot="$1" key="$2"
  dev_env_port_for_offset "$slot" "${DEV_ENV_PORT_OFFSETS[$key]}"
}

dev_env_value_for_slot() {
  local slot="$1" key="$2"
  if [ "$slot" -eq 0 ]; then
    echo "${DEV_ENV_MAIN_PORTS[$key]}"
  else
    dev_env_port_for_slot "$slot" "$key"
  fi
}

dev_env_port_is_taken() {
  local port="$1" listeners

  command -v ss >/dev/null || {
    dev_env_error "ss is required to check local ports"
    return 2
  }
  listeners="$(ss -H -ltn "sport = :$port" 2>/dev/null)" || {
    dev_env_error "ss failed while checking port $port"
    return 2
  }
  [ -n "$listeners" ]
}

dev_env_block_has_listener() {
  local slot="$1" offset port status

  for ((offset = 0; offset < DEV_ENV_BLOCK_SIZE; offset++)); do
    port="$(dev_env_port_for_offset "$slot" "$offset")"
    if dev_env_port_is_taken "$port"; then
      return 0
    else
      status=$?
      [ "$status" -eq 1 ] || return "$status"
    fi
  done
  return 1
}

# Managed .env parsing -------------------------------------------------------

# Print "managed" or "unmanaged" after validating marker shape and order.
dev_env_marker_state() {
  local env_file="$1" marker_summary
  local start_count end_count start_line end_line

  if [ ! -e "$env_file" ]; then
    echo unmanaged
    return 0
  fi
  if [ ! -f "$env_file" ]; then
    dev_env_error "$env_file exists but is not a regular file"
    return 2
  fi

  marker_summary="$(awk -v start="$DEV_ENV_MARKER_START" -v end="$DEV_ENV_MARKER_END" '
    $0 == start { start_count++; if (!start_line) start_line = NR }
    $0 == end   { end_count++;   if (!end_line) end_line = NR }
    END { print start_count + 0, end_count + 0, start_line + 0, end_line + 0 }
  ' "$env_file")" || {
    dev_env_error "cannot read $env_file"
    return 2
  }
  read -r start_count end_count start_line end_line <<< "$marker_summary"

  if [ "$start_count" -eq 0 ] && [ "$end_count" -eq 0 ]; then
    echo unmanaged
  elif [ "$start_count" -ne 1 ] || [ "$end_count" -ne 1 ]; then
    dev_env_error "$env_file has malformed or duplicate dev-env markers"
    return 2
  elif [ "$start_line" -ge "$end_line" ]; then
    dev_env_error "$env_file has dev-env markers in the wrong order"
    return 2
  else
    echo managed
  fi
}

dev_env_managed_values() {
  local env_file="$1" key="$2"
  awk -v start="$DEV_ENV_MARKER_START" -v end="$DEV_ENV_MARKER_END" -v key="$key" '
    $0 == start { inside = 1; next }
    $0 == end   { inside = 0; next }
    inside && index($0, key "=") == 1 { print substr($0, length(key) + 2) }
  ' "$env_file" || {
    dev_env_error "cannot read $env_file"
    return 2
  }
}

# Print a validated slot, or print nothing when this worktree has no managed
# claim. Malformed managed blocks fail closed.
dev_env_parse_claim() {
  local worktree="$1"
  local env_file="$worktree/.env" marker_state slot project key value expected

  marker_state="$(dev_env_marker_state "$env_file")" || return
  [ "$marker_state" = managed ] || return 0

  slot="$(dev_env_managed_values "$env_file" DEV_ENV_SLOT)" || return
  if ! [[ "$slot" =~ ^[1-9][0-9]*$ ]] || [ "$slot" -gt "$DEV_ENV_SLOT_COUNT" ]; then
    dev_env_error "$env_file must contain exactly one DEV_ENV_SLOT between 1 and $DEV_ENV_SLOT_COUNT"
    return 2
  fi

  project="$(dev_env_managed_values "$env_file" COMPOSE_PROJECT_NAME)" || return
  if [ "$project" != "onlineshop-wt${slot}" ]; then
    dev_env_error "$env_file has a COMPOSE_PROJECT_NAME that disagrees with DEV_ENV_SLOT=$slot"
    return 2
  fi

  for key in "${DEV_ENV_PORT_KEYS[@]}"; do
    value="$(dev_env_managed_values "$env_file" "$key")" || return
    expected="$(dev_env_port_for_slot "$slot" "$key")"
    if [ "$value" != "$expected" ]; then
      dev_env_error "$env_file has $key that disagrees with DEV_ENV_SLOT=$slot"
      return 2
    fi
  done

  echo "$slot"
}

# Clone-wide claim registry and candidate selection -------------------------

dev_env_register_claim() {
  local worktree="$1" normalized slot

  if [ ! -d "$worktree" ]; then
    echo "WARNING: ignoring missing worktree $worktree; run 'git worktree prune' to remove stale metadata" >&2
    return 0
  fi

  normalized="$(realpath "$worktree")" || {
    dev_env_error "cannot resolve registered worktree $worktree"
    return 2
  }
  [ "$normalized" = "$DEV_ENV_ROOT" ] && return 0

  slot="$(dev_env_parse_claim "$normalized")" || return
  [ -n "$slot" ] || return 0

  if [ -n "${DEV_ENV_CLAIMANT_BY_SLOT[$slot]+set}" ]; then
    DEV_ENV_CLAIMANT_BY_SLOT[$slot]+="; $normalized"
  else
    DEV_ENV_CLAIMANT_BY_SLOT[$slot]="$normalized"
  fi
}

dev_env_load_claims() {
  local record worktree

  [ "$DEV_ENV_CLAIMS_LOADED" -eq 0 ] || return 0
  dev_env_read_worktree_records || return

  DEV_ENV_CLAIMANT_BY_SLOT=()
  for record in "${DEV_ENV_WORKTREE_RECORDS[@]}"; do
    [[ "$record" == "worktree "* ]] || continue
    worktree="${record#worktree }"
    dev_env_register_claim "$worktree" || return
  done
  DEV_ENV_CLAIMS_LOADED=1
}

dev_env_claimant_for_slot() {
  local slot="$1"

  if [ "$DEV_ENV_CLAIMS_LOADED" -ne 1 ]; then
    dev_env_error "internal error: claim registry was not loaded"
    return 2
  fi
  [ -n "${DEV_ENV_CLAIMANT_BY_SLOT[$slot]+set}" ] || return 1
  echo "${DEV_ENV_CLAIMANT_BY_SLOT[$slot]}"
}

dev_env_slot_is_clean() {
  local slot="$1" status

  if [ "$DEV_ENV_CLAIMS_LOADED" -ne 1 ]; then
    dev_env_error "internal error: claim registry was not loaded"
    return 2
  fi
  [ -z "${DEV_ENV_CLAIMANT_BY_SLOT[$slot]+set}" ] || return 1

  if dev_env_block_has_listener "$slot"; then
    return 1
  else
    status=$?
    [ "$status" -eq 1 ] || return "$status"
  fi
  return 0
}

dev_env_find_free_slot() {
  local start="$1" attempt slot status

  for ((attempt = 0; attempt < DEV_ENV_SLOT_COUNT; attempt++)); do
    slot=$(( ((start - 1 + attempt) % DEV_ENV_SLOT_COUNT) + 1 ))
    if dev_env_slot_is_clean "$slot"; then
      echo "$slot"
      return 0
    else
      status=$?
      [ "$status" -eq 1 ] || return "$status"
    fi
  done

  dev_env_error "all $DEV_ENV_SLOT_COUNT slots are claimed or have a listening port"
  return 1
}

# Allocation lock ------------------------------------------------------------

dev_env_acquire_allocation_lock() {
  command -v flock >/dev/null || {
    dev_env_error "flock is required for safe slot allocation"
    return 2
  }

  exec 9>"$DEV_ENV_LOCK_FILE" || {
    dev_env_error "cannot open allocation lock $DEV_ENV_LOCK_FILE"
    return 2
  }
  if ! flock -w "$DEV_ENV_LOCK_TIMEOUT_SECONDS" 9; then
    dev_env_error "timed out waiting $DEV_ENV_LOCK_TIMEOUT_SECONDS seconds for $DEV_ENV_LOCK_FILE"
    echo "Another worktree allocation is running; retry when it finishes." >&2
    exec 9>&-
    return 1
  fi
}

dev_env_release_allocation_lock() {
  if ! flock -u 9; then
    dev_env_error "could not release allocation lock $DEV_ENV_LOCK_FILE"
    exec 9>&-
    return 2
  fi
  exec 9>&-
}

# Run one allocation operation with a fresh registry under the clone-wide
# lock. The lock is explicitly released on both success and failure.
dev_env_with_allocation_lock() {
  local operation="$1" operation_status=0 release_status=0
  shift

  dev_env_acquire_allocation_lock || return
  DEV_ENV_CLAIMS_LOADED=0
  DEV_ENV_CLAIMANT_BY_SLOT=()

  dev_env_load_claims || operation_status=$?
  if [ "$operation_status" -eq 0 ]; then
    "$operation" "$@" || operation_status=$?
  fi
  dev_env_release_allocation_lock || release_status=$?

  [ "$operation_status" -eq 0 ] || return "$operation_status"
  return "$release_status"
}

# Atomic claim rendering and write ------------------------------------------

dev_env_print_managed_block() {
  local slot="$1" key

  printf '%s\n' "$DEV_ENV_MARKER_START" || return 2
  printf 'COMPOSE_PROJECT_NAME=onlineshop-wt%s\n' "$slot" || return 2
  printf 'DEV_ENV_SLOT=%s\n' "$slot" || return 2
  for key in "${DEV_ENV_PORT_KEYS[@]}"; do
    printf '%s=%s\n' "$key" "$(dev_env_port_for_slot "$slot" "$key")" || return 2
  done
  printf '%s\n' "$DEV_ENV_MARKER_END" || return 2
}

dev_env_write_claim() {
  local slot="$1" marker_state output_tmp block_tmp

  marker_state="$(dev_env_marker_state "$DEV_ENV_ENV_FILE")" || return
  output_tmp="$(mktemp "${DEV_ENV_ENV_FILE}.tmp.XXXXXX")" || {
    dev_env_error "cannot create a temporary file beside $DEV_ENV_ENV_FILE"
    return 2
  }
  block_tmp="$(mktemp "${DEV_ENV_ENV_FILE}.block.XXXXXX")" || {
    dev_env_error "cannot create the managed-block temporary file"
    rm -f "$output_tmp"
    return 2
  }

  if ! dev_env_print_managed_block "$slot" > "$block_tmp"; then
    dev_env_error "cannot render the managed .env block"
    rm -f "$output_tmp" "$block_tmp"
    return 2
  fi

  if [ ! -e "$DEV_ENV_ENV_FILE" ]; then
    if ! cp "$block_tmp" "$output_tmp"; then
      dev_env_error "cannot prepare $DEV_ENV_ENV_FILE"
      rm -f "$output_tmp" "$block_tmp"
      return 2
    fi
  elif [ "$marker_state" = unmanaged ]; then
    if ! cp "$DEV_ENV_ENV_FILE" "$output_tmp"; then
      dev_env_error "cannot copy existing values from $DEV_ENV_ENV_FILE"
      rm -f "$output_tmp" "$block_tmp"
      return 2
    fi
    if [ -s "$output_tmp" ] && ! echo >> "$output_tmp"; then
      dev_env_error "cannot append to the temporary .env"
      rm -f "$output_tmp" "$block_tmp"
      return 2
    fi
    if ! cat "$block_tmp" >> "$output_tmp"; then
      dev_env_error "cannot append the managed block to the temporary .env"
      rm -f "$output_tmp" "$block_tmp"
      return 2
    fi
  elif ! awk -v start="$DEV_ENV_MARKER_START" -v end="$DEV_ENV_MARKER_END" -v block="$block_tmp" '
    function emit_block( line) {
      while ((getline line < block) > 0) print line
      close(block)
    }
    $0 == start { emit_block(); skipping = 1; next }
    skipping && $0 == end { skipping = 0; next }
    !skipping { print }
  ' "$DEV_ENV_ENV_FILE" > "$output_tmp"; then
    dev_env_error "cannot replace the managed block in $DEV_ENV_ENV_FILE"
    rm -f "$output_tmp" "$block_tmp"
    return 2
  fi

  if ! rm -f "$block_tmp"; then
    dev_env_error "cannot remove temporary file $block_tmp"
    rm -f "$output_tmp"
    return 2
  fi
  if ! mv "$output_tmp" "$DEV_ENV_ENV_FILE"; then
    dev_env_error "cannot atomically replace $DEV_ENV_ENV_FILE"
    rm -f "$output_tmp"
    return 2
  fi
}
