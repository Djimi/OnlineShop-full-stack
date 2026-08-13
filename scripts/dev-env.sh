#!/usr/bin/env bash
set -euo pipefail

[ "${BASH_VERSINFO[0]}" -ge 4 ] || {
  echo "ERROR: bash 4+ is required" >&2
  exit 2
}

# Sentinel used by scripts/create-worktree.sh. A target branch without this
# exact engine cannot provide the allocation guarantee and is rejected.
# Read as a sentinel by create-worktree.sh.
# shellcheck disable=SC2034
DEV_ENV_ENGINE_VERSION=2

# Purpose and modes:
#
# This script manages the current checkout's development-port configuration.
# It never creates Git worktrees and it never starts the application stack.
# On the main checkout it reports established defaults and writes nothing. In
# a linked worktree the default mode validates and reuses its managed .env
# claim, or allocates one when absent. --check validates without writing,
# --exports prints host-run values, and --regenerate is the one mode that stops
# the old Compose project before moving its claim.
#
# High-level flow for a new allocation:
#
#   1. Identify this checkout and Git's clone-wide worktree registry.
#   2. Lock the registry and validate every managed worktree .env.
#   3. Find an unclaimed slot whose complete 20-port block has no listeners.
#   4. Atomically add or replace this worktree's managed .env block.
#   5. Release the lock, then print the selected ports.
#
# Therefore a successful NEW allocation is unique among worktrees of this
# clone and its complete 20-port block was observed free immediately before
# the write. Ten ports are assigned today; ten remain reserved for expansion.
# Reusing an existing claim and --check deliberately do not require the ports
# to be free because this worktree's own stack may already be running.
# A claim lasts until its worktree is removed. Separate clones are separate
# registries, and another process can still bind a port after this check.
# Exit 1 means a safe operational refusal; exit 2 means invalid input, missing
# tooling, or unreadable/inconsistent registry state.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/lib/worktree-port-allocation.sh
source "$SCRIPT_DIR/lib/worktree-port-allocation.sh"

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/dev-env.sh [MODE] [--volumes]

Modes (choose at most one):
  --check        Verify that this worktree has one valid, unique claim
  --regenerate   Stop the old Compose project and allocate the next free slot
  --exports      Print host-run environment exports
  --set-slot N   Allocate a specific free slot (1-631)

With no mode, reuse a valid existing claim or allocate a new one.
--volumes is valid only with --regenerate.
USAGE
}

set_mode() {
  local requested="$1"
  if [ "$MODE" != default ]; then
    dev_env_error "choose only one mode"
    usage
    exit 2
  fi
  MODE="$requested"
}

print_table() {
  local slot="$1" key
  local -A ports=()
  for key in "${DEV_ENV_PORT_KEYS[@]}"; do
    ports[$key]="$(dev_env_value_for_slot "$slot" "$key")"
  done

  cat <<TABLE

============================================================
  Worktree dev environment — slot ${slot}
============================================================

  Frontend:    http://localhost:${ports[FRONTEND_PORT]}
  API Gateway: http://localhost:${ports[GATEWAY_PORT]}
  pgAdmin:     http://localhost:${ports[PGADMIN_PORT]}
  Kafka UI:    http://localhost:${ports[KAFKA_UI_PORT]}

  Service ports:
    api-gateway     : ${ports[GATEWAY_PORT]}
    items-service   : ${ports[ITEMS_PORT]}
    auth-service    : ${ports[AUTH_PORT]}
    frontend        : ${ports[FRONTEND_PORT]}
    items-postgres  : ${ports[ITEMS_DB_PORT]}
    auth-postgres   : ${ports[AUTH_DB_PORT]}
    pgadmin         : ${ports[PGADMIN_PORT]}
    redis           : ${ports[REDIS_PORT]}
    kafka (external): ${ports[KAFKA_HOST_PORT]}
    kafka-ui        : ${ports[KAFKA_UI_PORT]}

  Compose project: onlineshop-wt${slot}
============================================================

TABLE
}

print_exports() {
  local slot="$1" key
  local -A ports=()
  for key in "${DEV_ENV_PORT_KEYS[@]}"; do
    ports[$key]="$(dev_env_value_for_slot "$slot" "$key")"
  done

  cat <<EXPORTS
# Host-run environment for worktree slot $slot

# Items service
export ITEMS_SERVER_PORT="${ports[ITEMS_PORT]}"
export ITEMS_DATASOURCE_URL="jdbc:postgresql://localhost:${ports[ITEMS_DB_PORT]}/items"
export ITEMS_DATASOURCE_USERNAME="items"
export ITEMS_DATASOURCE_PASSWORD="itemspassword"

# Auth service
export AUTH_SERVER_PORT="${ports[AUTH_PORT]}"
export AUTH_DATASOURCE_URL="jdbc:postgresql://localhost:${ports[AUTH_DB_PORT]}/auth"
export AUTH_DATASOURCE_USERNAME="auth"
export AUTH_DATASOURCE_PASSWORD="authpassword"

# API gateway and shared infrastructure
export GATEWAY_SERVER_PORT="${ports[GATEWAY_PORT]}"
export GATEWAY_AUTH_SERVICE_URL="http://localhost:${ports[AUTH_PORT]}"
export GATEWAY_ITEMS_SERVICE_URL="http://localhost:${ports[ITEMS_PORT]}"
export SPRING_DATA_REDIS_HOST="localhost"
export SPRING_DATA_REDIS_PORT="${ports[REDIS_PORT]}"
export SPRING_KAFKA_BOOTSTRAP_SERVERS="localhost:${ports[KAFKA_HOST_PORT]}"

# Frontend. Without VITE_API_URL, Vite falls back to the main gateway.
export VITE_API_URL="http://localhost:${ports[GATEWAY_PORT]}"
export FRONTEND_PORT="${ports[FRONTEND_PORT]}"

# Port aliases used by host-run commands and tooling.
export ITEMS_PORT="${ports[ITEMS_PORT]}"
export AUTH_PORT="${ports[AUTH_PORT]}"
export GATEWAY_PORT="${ports[GATEWAY_PORT]}"
export ITEMS_DB_PORT="${ports[ITEMS_DB_PORT]}"
export AUTH_DB_PORT="${ports[AUTH_DB_PORT]}"
export PGADMIN_PORT="${ports[PGADMIN_PORT]}"
export REDIS_PORT="${ports[REDIS_PORT]}"
export KAFKA_HOST_PORT="${ports[KAFKA_HOST_PORT]}"
export KAFKA_UI_PORT="${ports[KAFKA_UI_PORT]}"
EXPORTS
}

# Read-only commands ---------------------------------------------------------

run_check() {
  local slot claimant status

  if dev_env_is_main_checkout; then
    return 0
  fi

  if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
    echo "WARNING: shell COMPOSE_PROJECT_NAME overrides this worktree's .env value; unset it before Docker Compose." >&2
  fi

  slot="$(dev_env_parse_claim "$DEV_ENV_ROOT")" || return
  if [ -z "$slot" ]; then
    dev_env_error "this worktree has no managed dev-environment claim"
    echo "Create worktrees with scripts/create-worktree.sh; do not run Docker Compose here yet." >&2
    return 1
  fi

  dev_env_load_claims || return
  if claimant="$(dev_env_claimant_for_slot "$slot")"; then
    dev_env_error "slot $slot is also claimed by $claimant"
    echo "Run scripts/dev-env.sh --regenerate before Docker Compose." >&2
    return 1
  else
    status=$?
    [ "$status" -eq 1 ] || return "$status"
  fi
}

run_exports() {
  local slot

  if dev_env_is_main_checkout; then
    slot=0
  else
    slot="$(dev_env_parse_claim "$DEV_ENV_ROOT")" || return
    if [ -z "$slot" ]; then
      dev_env_error "this worktree has no managed claim; create it with scripts/create-worktree.sh"
      return 1
    fi
  fi
  print_exports "$slot"
}

# Locked allocation steps ---------------------------------------------------
#
# These small callbacks contain only work that must happen while the registry
# lock is held. dev_env_with_allocation_lock owns loading and releasing the lock.

regenerate_locked() {
  local old_slot="$1" current_slot next_slot selected_slot

  current_slot="$(dev_env_parse_claim "$DEV_ENV_ROOT")" || return
  if [ "$current_slot" != "$old_slot" ]; then
    dev_env_error "the worktree claim changed from $old_slot to $current_slot during regeneration"
    return 1
  fi

  next_slot=$(( (old_slot % DEV_ENV_SLOT_COUNT) + 1 ))
  selected_slot="$(dev_env_find_free_slot "$next_slot")" || return
  dev_env_write_claim "$selected_slot" || return
  echo "$selected_slot"
}

set_slot_locked() {
  local slot="$1" claimant status

  if claimant="$(dev_env_claimant_for_slot "$slot")"; then
    dev_env_error "slot $slot is claimed by $claimant"
    return 1
  else
    status=$?
    [ "$status" -eq 1 ] || return "$status"
  fi
  if dev_env_block_has_listener "$slot"; then
    dev_env_error "slot $slot has a listening port"
    return 1
  else
    status=$?
    [ "$status" -eq 1 ] || return "$status"
  fi

  dev_env_write_claim "$slot" || return
  echo "$slot"
}

reuse_or_allocate_locked() {
  local slot initial_slot claimant status

  slot="$(dev_env_parse_claim "$DEV_ENV_ROOT")" || return
  if [ -n "$slot" ]; then
    if claimant="$(dev_env_claimant_for_slot "$slot")"; then
      dev_env_error "existing slot $slot is also claimed by $claimant"
      echo "Use scripts/dev-env.sh --regenerate; the existing slot was not changed." >&2
      return 1
    else
      status=$?
      [ "$status" -eq 1 ] || return "$status"
    fi
    printf 'existing %s %s\n' "$slot" "$slot"
    return 0
  fi

  initial_slot="$(dev_env_hash_slot "$(basename "$DEV_ENV_ROOT")")" || return
  slot="$(dev_env_find_free_slot "$initial_slot")" || return
  dev_env_write_claim "$slot" || return
  printf 'allocated %s %s\n' "$initial_slot" "$slot"
}

# State-changing commands ---------------------------------------------------

run_regenerate() {
  local old_slot selected_slot

  if dev_env_is_main_checkout; then
    echo "Main checkout uses slot 0; no worktree claim is needed."
    return 0
  fi

  old_slot="$(dev_env_parse_claim "$DEV_ENV_ROOT")" || return
  if [ -z "$old_slot" ]; then
    dev_env_error "this worktree has no slot to regenerate"
    return 1
  fi

  echo "Stopping Compose project onlineshop-wt${old_slot} before changing its claim..."
  if ! docker compose --project-directory "$DEV_ENV_ROOT" --project-name "onlineshop-wt${old_slot}" down ${DOWN_VOLUMES:+-v}; then
    echo "WARNING: docker compose down failed; allocation will continue only if the next slot is clean." >&2
  fi

  selected_slot="$(dev_env_with_allocation_lock regenerate_locked "$old_slot")" || return

  echo "Moved worktree claim from slot $old_slot to $selected_slot."
  print_table "$selected_slot"
}

run_set_slot() {
  if dev_env_is_main_checkout; then
    dev_env_error "--set-slot applies only to linked worktrees; the main checkout always uses slot 0"
    return 1
  fi

  dev_env_with_allocation_lock set_slot_locked "$EXPLICIT_SLOT" >/dev/null || return
  echo "Allocated slot $EXPLICIT_SLOT: claim is unique and its 20-port block was observed free."
  print_table "$EXPLICIT_SLOT"
}

run_default() {
  local result allocation_state initial_slot slot

  if dev_env_is_main_checkout; then
    echo "Main checkout — slot 0, Compose defaults apply, no .env claim is written."
    print_table 0
    return 0
  fi

  result="$(dev_env_with_allocation_lock reuse_or_allocate_locked)" || return
  read -r allocation_state initial_slot slot <<< "$result"

  if [ "$allocation_state" = existing ]; then
    echo "Existing worktree claim is valid and unique; ports may be in use by this worktree's running stack."
    print_table "$slot"
    return 0
  fi

  if [ "$slot" != "$initial_slot" ]; then
    echo "Hash candidate $initial_slot was unavailable; allocated slot $slot."
  fi
  echo "Allocated slot $slot: claim is unique and its 20-port block was observed free."
  print_table "$slot"
}

parse_arguments() {
  MODE=default
  EXPLICIT_SLOT=""
  DOWN_VOLUMES=""

  while [ "$#" -gt 0 ]; do
    case "$1" in
      --check)
        set_mode check
        shift
        ;;
      --regenerate)
        set_mode regenerate
        shift
        ;;
      --exports)
        set_mode exports
        shift
        ;;
      --set-slot)
        set_mode set-slot
        [ "$#" -ge 2 ] || { dev_env_error "--set-slot requires a number"; usage; return 2; }
        EXPLICIT_SLOT="$2"
        if ! [[ "$EXPLICIT_SLOT" =~ ^[1-9][0-9]*$ ]] || [ "$EXPLICIT_SLOT" -gt "$DEV_ENV_SLOT_COUNT" ]; then
          dev_env_error "--set-slot must be between 1 and $DEV_ENV_SLOT_COUNT"
          return 2
        fi
        shift 2
        ;;
      --volumes)
        DOWN_VOLUMES=yes
        shift
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        dev_env_error "unknown argument: $1"
        usage
        return 2
        ;;
    esac
  done

  if [ -n "$DOWN_VOLUMES" ] && [ "$MODE" != regenerate ]; then
    dev_env_error "--volumes is valid only with --regenerate"
    return 2
  fi
}

main() {
  parse_arguments "$@" || return
  dev_env_init || return

  case "$MODE" in
    check)      run_check ;;
    regenerate) run_regenerate ;;
    exports)    run_exports ;;
    set-slot)   run_set_slot ;;
    default)    run_default ;;
  esac
}

main "$@"
