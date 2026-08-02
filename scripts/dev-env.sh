#!/usr/bin/env bash
set -euo pipefail

[ "${BASH_VERSINFO[0]}" -ge 4 ] || { echo "ERROR: bash 4+ required (declare -A support)" >&2; exit 2; }

# ====================================================================
# dev-env.sh — multi-worktree port isolation for docker compose
# ====================================================================
# Usage:
#   scripts/dev-env.sh              Create/refresh managed .env block
#   scripts/dev-env.sh --check      Guard: exit 0 if safe to "up", 1 if not
#   scripts/dev-env.sh --regenerate Down old stack, bump slot, rewrite
#   scripts/dev-env.sh --exports    Print export variables for host-run dev
#   scripts/dev-env.sh --set-slot N Force a specific slot (1-631)
# ====================================================================

SLOT_COUNT=631
BLOCK_SIZE=20
BASE_PORT=20000
MARKER_START="# >>> dev-env (managed by scripts/dev-env.sh — do not edit between the markers)"
MARKER_END="# <<< dev-env"

# Default ports for slot 0 (main checkout)
declare -A OFFSETS=(           # service -> [offset, default]
  [GATEWAY_PORT]="0:10000"
  [ITEMS_PORT]="1:9000"
  [AUTH_PORT]="2:9001"
  [FRONTEND_PORT]="3:5173"
  [ITEMS_DB_PORT]="4:5432"
  [AUTH_DB_PORT]="5:5433"
  [PGADMIN_PORT]="6:5051"
  [REDIS_PORT]="7:6379"
  [KAFKA_HOST_PORT]="8:29092"
  [KAFKA_UI_PORT]="9:8080"
)

# -------------------------------------------------------------------
# helpers
# -------------------------------------------------------------------

get_root() {
  local root
  root="$(git rev-parse --show-toplevel 2>/dev/null)" || {
    echo "ERROR: not inside a git repository" >&2
    exit 2
  }
  echo "$root"
}

is_main_checkout() {
  local root="$1"
  local git_common real_git_common first_worktree porcelain_root
  git_common="$(git -C "$root" rev-parse --git-common-dir 2>/dev/null)" || {
    echo "ERROR: cannot determine git common dir for $root" >&2
    exit 2
  }
  real_git_common="$(realpath "$git_common")"
  # Main ⇔ git-common-dir resolves to $root/.git
  if [ "$real_git_common" = "$root/.git" ]; then
    # Cross-check with git worktree list first entry
    first_worktree="$(git -C "$root" worktree list --porcelain 2>/dev/null | grep '^worktree ' | head -1 | cut -d' ' -f2-)"
    if [ -z "$first_worktree" ]; then
      echo "ERROR: git worktree list returned unexpected output" >&2
      exit 2
    fi
    porcelain_root="$(realpath "$first_worktree")"
    if [ "$porcelain_root" != "$root" ]; then
      echo "ERROR: git-common-dir implies main checkout but worktree list disagrees ($porcelain_root != $root)" >&2
      exit 2
    fi
    return 0
  fi
  return 1
}

hash_slot() {
  local basename="$1"
  local hash
  if command -v md5sum &>/dev/null; then
    hash="$(printf '%s' "$basename" | md5sum | cut -c1-8)"
  elif command -v sha256sum &>/dev/null; then
    hash="$(printf '%s' "$basename" | sha256sum | cut -c1-8)"
  elif command -v cksum &>/dev/null; then
    local cksum_val
    cksum_val="$(printf '%s' "$basename" | cksum | cut -d' ' -f1)"
    hash="$(printf '%08x' "$cksum_val")"
    hash="${hash:0:8}"
  else
    echo "ERROR: no hashing tool available (md5sum, sha256sum, or cksum)" >&2
    exit 1
  fi
  local slot
  slot=$(( (16#$hash % SLOT_COUNT) + 1 ))
  echo "$slot"
}

port_for_slot() {
  local slot="$1" offset="$2"
  if [ "$slot" -eq 0 ]; then
    echo ""  # slot 0 uses defaults from compose
  else
    echo $(( BASE_PORT + slot * BLOCK_SIZE + offset ))
  fi
}

port_is_taken() {
  local port="$1"
  if command -v ss &>/dev/null; then
    if ss -tlnH "sport = :$port" 2>/dev/null | grep -q ":$port "; then
      return 0
    fi
  fi
  # Fallback: /dev/tcp connect
  ( timeout --foreground 0.5 bash -c "echo >/dev/tcp/127.0.0.1/$port" ) 2>/dev/null && return 0 || return 1
}

any_port_taken() {
  local slot="$1"
  local port
  for var in "${!OFFSETS[@]}"; do
    IFS=':' read -r offset _default <<< "${OFFSETS[$var]}"
    port="$(port_for_slot "$slot" "$offset")"
    if port_is_taken "$port"; then
      return 0
    fi
  done
  return 1
}

# -------------------------------------------------------------------
# managed block read/write
# -------------------------------------------------------------------

read_env_slot() {
  local env_file="$1"
  if [ -f "$env_file" ]; then
    local slot_line slot_val
    slot_line="$(grep '^DEV_ENV_SLOT=' "$env_file" 2>/dev/null || true)"
    if [ -n "$slot_line" ]; then
      slot_val="${slot_line#DEV_ENV_SLOT=}"
      if [ "$slot_val" = "0" ]; then
        echo "ERROR: DEV_ENV_SLOT=0 in .env is invalid — slot 0 is reserved for main checkout. Fix .env or re-run scripts/dev-env.sh" >&2
        exit 1
      fi
      echo "$slot_val"
    fi
  fi
}

write_managed_block() {
  local env_file="$1" slot="$2"
  local prefix="" suffix="" new_block inside=0 has_marker=0

  if [ -f "$env_file" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      if [ "$line" = "$MARKER_START" ]; then
        inside=1
        has_marker=1
        continue
      elif [ "$line" = "$MARKER_END" ]; then
        inside=2
        continue
      fi
      case "$inside" in
        0) prefix+="$line"$'\n' ;;
        2) suffix+="$line"$'\n' ;;
      esac
    done < "$env_file"
    # Strip trailing newlines
    prefix="$(printf '%s' "$prefix")"
    suffix="$(printf '%s' "$suffix")"
  fi

  # Build managed block
  new_block="$MARKER_START"$'\n'
  new_block+="COMPOSE_PROJECT_NAME=onlineshop-wt${slot}"$'\n'
  new_block+="DEV_ENV_SLOT=${slot}"$'\n'

  local port
  for var in GATEWAY_PORT ITEMS_PORT AUTH_PORT FRONTEND_PORT ITEMS_DB_PORT AUTH_DB_PORT PGADMIN_PORT REDIS_PORT KAFKA_HOST_PORT KAFKA_UI_PORT; do
    IFS=':' read -r offset _default <<< "${OFFSETS[$var]}"
    port="$(port_for_slot "$slot" "$offset")"
    new_block+="${var}=${port}"$'\n'
  done
  new_block+="$MARKER_END"

  # Assemble file
  {
    if [ -n "$prefix" ]; then
      echo "$prefix"
    fi
    if [ "$has_marker" -eq 0 ] && [ -n "$prefix" ]; then
      echo ""
    fi
    echo "$new_block"
    if [ -n "$suffix" ]; then
      echo "$suffix"
    fi
  } > "${env_file}.tmp.$$" && mv "${env_file}.tmp.$$" "$env_file"
}

# -------------------------------------------------------------------
# exports
# -------------------------------------------------------------------

print_exports() {
  local slot="$1"

  local GATEWAY_PORT ITEMS_PORT AUTH_PORT FRONTEND_PORT
  local ITEMS_DB_PORT AUTH_DB_PORT PGADMIN_PORT REDIS_PORT KAFKA_HOST_PORT KAFKA_UI_PORT

  for var in "${!OFFSETS[@]}"; do
    IFS=':' read -r offset default <<< "${OFFSETS[$var]}"
    if [ "$slot" -eq 0 ]; then
      eval "$var=$default"
    else
      eval "$var=$(port_for_slot "$slot" "$offset")"
    fi
  done

  cat <<EXP
# ============================================================
# Host-run dev exports for slot $slot
# Usage: source <(scripts/dev-env.sh --exports)
# ============================================================

# --- items-service ---
export SERVER_PORT="${ITEMS_PORT}"
export ITEMS_SERVER_PORT="${ITEMS_PORT}"
export SPRING_DATASOURCE_URL="jdbc:postgresql://localhost:${ITEMS_DB_PORT}/items"
export SPRING_DATASOURCE_USERNAME="items"
export SPRING_DATASOURCE_PASSWORD="itemspassword"
export ITEMS_DATASOURCE_URL="jdbc:postgresql://localhost:${ITEMS_DB_PORT}/items"
export ITEMS_DATASOURCE_USERNAME="items"
export ITEMS_DATASOURCE_PASSWORD="itemspassword"

# --- auth-service ---
export SERVER_PORT="${AUTH_PORT}"
export AUTH_SERVER_PORT="${AUTH_PORT}"
export SPRING_DATASOURCE_URL="jdbc:postgresql://localhost:${AUTH_DB_PORT}/auth"
export SPRING_DATASOURCE_USERNAME="auth"
export SPRING_DATASOURCE_PASSWORD="authpassword"
export AUTH_DATASOURCE_URL="jdbc:postgresql://localhost:${AUTH_DB_PORT}/auth"
export AUTH_DATASOURCE_USERNAME="auth"
export AUTH_DATASOURCE_PASSWORD="authpassword"

# --- api-gateway ---
export GATEWAY_AUTH_SERVICE_URL="http://localhost:${AUTH_PORT}"
export GATEWAY_ITEMS_SERVICE_URL="http://localhost:${ITEMS_PORT}"
export SERVER_PORT="${GATEWAY_PORT}"
export GATEWAY_SERVER_PORT="${GATEWAY_PORT}"

# --- infrastructure ---
export SPRING_DATA_REDIS_HOST="localhost"
export SPRING_DATA_REDIS_PORT="${REDIS_PORT}"
export SPRING_KAFKA_BOOTSTRAP_SERVERS="localhost:${KAFKA_HOST_PORT}"

# --- frontend (host-run) ---
export VITE_API_URL="http://localhost:${GATEWAY_PORT}"
export FRONTEND_PORT="${FRONTEND_PORT}"
# WARNING: if VITE_API_URL is NOT set, api.ts falls back to http://localhost:10000
#          which is main's gateway — a silent cross-worktree data-plane mixup.
#          Always 'source <(scripts/dev-env.sh --exports)' before 'npm run dev'.

# Run frontend:  npm run dev -- --port "\$FRONTEND_PORT"
EXP
}

# -------------------------------------------------------------------
# table output
# -------------------------------------------------------------------

print_table() {
  local slot="$1"

  local GATEWAY_PORT ITEMS_PORT AUTH_PORT FRONTEND_PORT
  local ITEMS_DB_PORT AUTH_DB_PORT PGADMIN_PORT REDIS_PORT KAFKA_HOST_PORT KAFKA_UI_PORT

  for var in "${!OFFSETS[@]}"; do
    IFS=':' read -r offset default <<< "${OFFSETS[$var]}"
    if [ "$slot" -eq 0 ]; then
      eval "$var=$default"
    else
      eval "$var=$(port_for_slot "$slot" "$offset")"
    fi
  done

  cat <<TABLE

============================================================
  Worktree dev environment — slot ${slot}
============================================================

  Frontend:   http://localhost:${FRONTEND_PORT}
  API Gateway: http://localhost:${GATEWAY_PORT}
  pgAdmin:    http://localhost:${PGADMIN_PORT}  (admin@onlineshop.com / admin)
  Kafka UI:   http://localhost:${KAFKA_UI_PORT}

  Service ports:
    api-gateway    : ${GATEWAY_PORT}
    items-service  : ${ITEMS_PORT}
    auth-service   : ${AUTH_PORT}
    frontend       : ${FRONTEND_PORT}
    items-postgres : ${ITEMS_DB_PORT}
    auth-postgres  : ${AUTH_DB_PORT}
    pgadmin        : ${PGADMIN_PORT}
    redis          : ${REDIS_PORT}
    kafka (external): ${KAFKA_HOST_PORT}
    kafka-ui       : ${KAFKA_UI_PORT}

  Project: onlineshop-wt${slot}
============================================================

TABLE
}

# ====================================================================
# main
# ====================================================================

ROOT="$(get_root)"
ENV_FILE="$ROOT/.env"
MODE="generate"
EXPLICIT_SLOT=""
DOWN_VOLUMES=""

# parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --check)     MODE="check"; shift ;;
    --regenerate) MODE="regenerate"; shift ;;
    --exports)   MODE="exports"; shift ;;
    --set-slot)
      EXPLICIT_SLOT="$2"
      if ! [[ "$EXPLICIT_SLOT" =~ ^[1-9][0-9]*$ ]] || [ "$EXPLICIT_SLOT" -lt 1 ] || [ "$EXPLICIT_SLOT" -gt "$SLOT_COUNT" ]; then
        echo "ERROR: --set-slot must be between 1 and $SLOT_COUNT" >&2
        exit 1
      fi
      shift 2
      MODE="generate"
      ;;
    --volumes)  # passthrough for --regenerate to also down -v
      DOWN_VOLUMES="-v"
      shift
      ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      echo "Usage: scripts/dev-env.sh [--check|--regenerate|--exports|--set-slot N] [--volumes]" >&2
      exit 1
      ;;
  esac
done

if [ -n "${DOWN_VOLUMES:-}" ] && [ "$MODE" != "regenerate" ]; then
  echo "WARNING: --volumes has no effect in ${MODE} mode (only meaningful with --regenerate)" >&2
fi

# --- MODE: check --------------------------------------------------
if [ "$MODE" = "check" ]; then
  if is_main_checkout "$ROOT"; then
    exit 0
  fi
  if [ -n "${COMPOSE_PROJECT_NAME:-}" ]; then
    echo "WARNING: COMPOSE_PROJECT_NAME is set in your shell to '${COMPOSE_PROJECT_NAME}'." >&2
    echo "This overrides the project name in .env and may cause container/volume collisions." >&2
    echo "Run 'unset COMPOSE_PROJECT_NAME' before 'docker compose up'." >&2
  fi
  if [ -f "$ENV_FILE" ] && grep -q '^DEV_ENV_SLOT=' "$ENV_FILE" 2>/dev/null; then
    exit 0
  fi
  echo "WARNING: This worktree has NO managed .env block." >&2
  echo "Run 'scripts/dev-env.sh' before 'docker compose up' —" >&2
  echo "a bare 'up' here grabs main's canonical ports." >&2
  exit 1
fi

# --- MODE: regenerate --------------------------------------------
if [ "$MODE" = "regenerate" ]; then
  if is_main_checkout "$ROOT"; then
    echo "Main checkout — no managed block; docker compose stays on defaults." >&2
    echo "Use 'docker compose down${DOWN_VOLUMES:+ $DOWN_VOLUMES}' directly if needed." >&2
    exit 0
  fi

  OLD_SLOT="$(read_env_slot "$ENV_FILE")"
  if [ -z "$OLD_SLOT" ]; then
    echo "ERROR: no managed block found in .env — nothing to regenerate from." >&2
    echo "Run 'scripts/dev-env.sh' first, then 'docker compose up -d --build'." >&2
    exit 1
  fi

  # Down the OLD stack first
  echo "Taking down old project onlineshop-wt${OLD_SLOT}..."
  if ! docker compose --project-directory "$ROOT" --project-name "onlineshop-wt${OLD_SLOT}" down ${DOWN_VOLUMES:-}; then
    echo "WARNING: 'docker compose down' failed (may already be down) — proceeding anyway" >&2
  fi

  if any_port_taken "$OLD_SLOT"; then
    echo "WARNING: Ports from old slot $OLD_SLOT are still in use. Lingering containers may exist." >&2
    echo "Run: docker ps --filter 'name=onlineshop-wt${OLD_SLOT}' to find them." >&2
    echo "Press Ctrl+C to abort, or wait 5s to proceed anyway..." >&2
    sleep 5
  fi

  # Bump slot
  NEW_SLOT="$OLD_SLOT"
  TRIES=0
  while [ "$TRIES" -lt "$SLOT_COUNT" ]; do
    NEW_SLOT=$(( (NEW_SLOT % SLOT_COUNT) + 1 ))
    TRIES=$((TRIES + 1))
    if ! any_port_taken "$NEW_SLOT"; then
      break
    fi
    if [ "$TRIES" -eq "$SLOT_COUNT" ]; then
      echo "ERROR: all $SLOT_COUNT slots have ports in use — cannot recover" >&2
      exit 1
    fi
  done

  write_managed_block "$ENV_FILE" "$NEW_SLOT"
  echo "Slot bumped: ${OLD_SLOT} → ${NEW_SLOT}"
  print_table "$NEW_SLOT"
  exit 0
fi

# --- MODE: exports -----------------------------------------------
if [ "$MODE" = "exports" ]; then
  if is_main_checkout "$ROOT"; then
    print_exports 0
  else
    SLOT="$(read_env_slot "$ENV_FILE")"
    if [ -z "$SLOT" ]; then
      echo "ERROR: no managed block in .env — run 'scripts/dev-env.sh' first" >&2
      exit 1
    fi
    print_exports "$SLOT"
  fi
  exit 0
fi

# --- MODE: generate (default) ------------------------------------
if is_main_checkout "$ROOT"; then
  echo "Main checkout — slot 0, defaults apply, no .env written."
  print_table 0
  exit 0
fi

# Non-main: determine slot
if [ -n "$EXPLICIT_SLOT" ]; then
  SLOT="$EXPLICIT_SLOT"
  if any_port_taken "$SLOT"; then
    echo "ERROR: slot $SLOT has at least one port already in use." >&2
    echo "Choose a different slot with --set-slot, or stop the competing stack." >&2
    exit 1
  fi
else
  # Try to reuse existing managed block
  SLOT="$(read_env_slot "$ENV_FILE")"
  if [ -z "$SLOT" ]; then
    # Compute hash-based initial slot
    BASENAME="$(basename "$ROOT")"
    SLOT="$(hash_slot "$BASENAME")"

    # Bind-check bump loop
    TRIES=0
    INITIAL_SLOT="$SLOT"
    while any_port_taken "$SLOT"; do
      SLOT=$(( (SLOT % SLOT_COUNT) + 1 ))
      TRIES=$((TRIES + 1))
      if [ "$TRIES" -eq "$SLOT_COUNT" ]; then
        echo "ERROR: all $SLOT_COUNT slots have ports in use" >&2
        exit 1
      fi
    done
    if [ "$SLOT" != "$INITIAL_SLOT" ]; then
      echo "Slot bumped from hash-derived ${INITIAL_SLOT} to ${SLOT} due to port conflicts"
    fi
  fi
fi

# Check ephemeral port range overlap
if [ -r /proc/sys/net/ipv4/ip_local_port_range ]; then
  ephemeral_low="$(awk '{print $1}' /proc/sys/net/ipv4/ip_local_port_range)"
  max_port=$(( BASE_PORT + SLOT_COUNT * BLOCK_SIZE + 9 ))
  if [ "$max_port" -ge "$ephemeral_low" ]; then
    echo "WARNING: worktree port range (${BASE_PORT}-${max_port}) overlaps system ephemeral port range (starts at ${ephemeral_low})" >&2
  fi
fi

# Write managed block
write_managed_block "$ENV_FILE" "$SLOT"
print_table "$SLOT"
