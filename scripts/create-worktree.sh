#!/usr/bin/env bash
set -euo pipefail

# Create a ready-to-start development worktree in one command:
#
#   1. Resolve and validate the target path, new branch, and base commit.
#   2. Create the Git worktree and its new branch.
#   3. Allocate one unique, observed-free port slot and write its managed .env.
#   4. Print the ports and the command that starts the stack.
#
# If step 3 fails, the new worktree and branch are deliberately retained and
# the script prints recovery and removal commands.

usage() {
  cat >&2 <<'USAGE'
Usage: scripts/create-worktree.sh <path|name> -b <new-branch> [base-ref]

Examples:
  scripts/create-worktree.sh payments -b feature/payments
  scripts/create-worktree.sh ../review-123 -b review/123 origin/main

Ordered actions and artifacts:
  1. Resolve the target (a bare name goes under sibling <repo>-worktrees/).
  2. Create a new Git branch from base-ref (default: main) and check it out
     as the target worktree directory.
  3. Create <target>/.env with a managed Compose project name, slot number,
     and all ten host ports; preserve any pre-existing unmanaged values.
  4. Print the port table and next command.

If allocation fails, the worktree and branch remain for recovery.
No services, containers, or volumes are started by this command.
USAGE
}

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

print_recovery() {
  local root="$1" target="$2" branch="$3"
  local quoted_root quoted_target quoted_branch
  printf -v quoted_root '%q' "$root"
  printf -v quoted_target '%q' "$target"
  printf -v quoted_branch '%q' "$branch"

  cat >&2 <<RECOVERY

The Git worktree was left in place at:
  $target

After correcting the problem, allocate from any directory:
  (cd $quoted_target && scripts/dev-env.sh)

The new branch '$branch' is also retained. To discard both, run these from a
directory outside the failed worktree:
  git -C $quoted_root worktree remove $quoted_target
  git -C $quoted_root branch -D $quoted_branch

If removal refuses because another process created valuable untracked files,
inspect them first; add --force only when you deliberately want to delete them.
RECOVERY
}

find_main_root() {
  local root="$1" git_dir git_common records_file first_record
  local -a records=()

  git_dir="$(git -C "$root" rev-parse --path-format=absolute --git-dir 2>/dev/null)" || return 1
  git_common="$(git -C "$root" rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || return 1
  git_dir="$(realpath -m "$git_dir")" || return 1
  git_common="$(realpath -m "$git_common")" || return 1

  # In the main checkout these paths are equal, including repositories made
  # with --separate-git-dir. Porcelain reports the Git directory rather than
  # the checkout path for that special layout, so prefer the known root.
  if [ "$git_dir" = "$git_common" ]; then
    echo "$root"
    return 0
  fi

  records_file="$(mktemp)" || return 1
  if ! git -C "$root" worktree list --porcelain -z > "$records_file"; then
    rm -f "$records_file"
    return 1
  fi
  if ! mapfile -d '' -t records < "$records_file"; then
    rm -f "$records_file"
    return 1
  fi
  rm -f "$records_file" || return 1

  first_record="${records[0]:-}"
  [[ "$first_record" == "worktree "* ]] || return 1
  realpath "${first_record#worktree }"
}

parse_arguments() {
  if [ "$#" -eq 1 ] && { [ "$1" = --help ] || [ "$1" = -h ]; }; then
    usage
    exit 0
  fi

  [ "$#" -ge 1 ] || { usage; return 2; }
  REQUESTED_TARGET="$1"
  shift

  if [ "$#" -lt 2 ] || [ "$1" != -b ]; then
    usage
    return 2
  fi
  BRANCH="$2"
  shift 2

  [ "$#" -le 1 ] || { usage; return 2; }
  BASE_REF="${1:-main}"
}

discover_repository() {
  ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" ||
    fail "run this command from an existing checkout"
  ROOT="$(realpath "$ROOT")" || fail "cannot resolve the current checkout path"
  MAIN_ROOT="$(find_main_root "$ROOT")" || fail "cannot determine the main checkout"
}

validate_request() {
  local base_engine

  git check-ref-format --branch "$BRANCH" >/dev/null 2>&1 ||
    fail "invalid branch name: $BRANCH"
  BASE_COMMIT="$(git -C "$ROOT" rev-parse --verify --end-of-options "${BASE_REF}^{commit}" 2>/dev/null)" ||
    fail "base ref does not resolve to a commit: $BASE_REF"
  base_engine="$(git -C "$ROOT" show "${BASE_COMMIT}:scripts/dev-env.sh" 2>/dev/null)" ||
    fail "base ref $BASE_REF lacks allocator version 2; refusing a legacy, bind-only allocation"
  grep -Fxq 'DEV_ENV_ENGINE_VERSION=2' <<< "$base_engine" ||
    fail "base ref $BASE_REF lacks allocator version 2; refusing a legacy, bind-only allocation"
  git -C "$ROOT" cat-file -e "${BASE_COMMIT}:scripts/lib/worktree-port-allocation.sh" 2>/dev/null ||
    fail "base ref $BASE_REF lacks allocator version 2; refusing a legacy, bind-only allocation"
}

resolve_target_path() {
  local main_parent main_name

  if [[ "$REQUESTED_TARGET" == */* ]]; then
    TARGET="$(realpath -m "$REQUESTED_TARGET")" ||
      fail "cannot resolve target path: $REQUESTED_TARGET"
  else
    main_parent="$(dirname "$MAIN_ROOT")"
    main_name="$(basename "$MAIN_ROOT")"
    TARGET="$main_parent/${main_name}-worktrees/$REQUESTED_TARGET"
  fi

  if [ -e "$TARGET" ] || [ -L "$TARGET" ]; then
    fail "target path already exists: $TARGET"
  fi
}

create_git_worktree() {
  mkdir -p "$(dirname "$TARGET")" || fail "cannot create the target parent directory"
  echo "Creating $TARGET on new branch $BRANCH from $BASE_REF..."
  git -C "$ROOT" worktree add "$TARGET" -b "$BRANCH" "$BASE_COMMIT"
}

verify_target_allocator() {
  local target_engine="$TARGET/scripts/dev-env.sh"

  if [ ! -x "$target_engine" ] ||
     ! grep -Fxq 'DEV_ENV_ENGINE_VERSION=2' "$target_engine" ||
     [ ! -f "$TARGET/scripts/lib/worktree-port-allocation.sh" ]; then
    echo "ERROR: target branch does not contain the current worktree allocator; refusing a legacy, bind-only allocation" >&2
    print_recovery "$ROOT" "$TARGET" "$BRANCH"
    return 1
  fi
}

allocate_dev_environment() {
  if ! (cd "$TARGET" && scripts/dev-env.sh); then
    echo "ERROR: worktree creation succeeded, but port allocation failed" >&2
    print_recovery "$ROOT" "$TARGET" "$BRANCH"
    return 1
  fi
}

print_next_steps() {
  local quoted_target
  printf -v quoted_target '%q' "$TARGET"
  cat <<NEXT_STEPS
Worktree creation is complete.

Next steps:
  cd $quoted_target
  docker compose up -d --build
NEXT_STEPS
}

# The orchestration stays intentionally short. Each function above owns one
# decision or side effect, so this reads as the complete creation story.
main() {
  parse_arguments "$@"
  discover_repository
  validate_request
  resolve_target_path
  create_git_worktree
  verify_target_allocator
  allocate_dev_environment
  print_next_steps
}

main "$@"
