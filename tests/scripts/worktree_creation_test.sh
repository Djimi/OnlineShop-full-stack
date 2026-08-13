#!/usr/bin/env bash
set -euo pipefail

# Black-box contract tests for worktree creation and port allocation.
#
# Each test creates its own Git repository. That isolation is intentional: a
# claim, branch, stale worktree, or malformed .env from one scenario must not
# influence another. Read the test_* functions from top to bottom as the user
# journeys supported by the two scripts.

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
TEST_ROOT="$(mktemp -d)"
TEST_NUMBER=0

cleanup() {
  chmod -R u+w "$TEST_ROOT" 2>/dev/null || true
  rm -rf "$TEST_ROOT"
}
trap cleanup EXIT

# Assertions and domain calculations ----------------------------------------

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

assert_equals() {
  local actual="$1" expected="$2"
  [ "$actual" = "$expected" ] || fail "expected '$expected', got '$actual'"
}

assert_contains() {
  local value="$1" expected="$2"
  [[ "$value" == *"$expected"* ]] || fail "expected output to contain '$expected'"
}

assert_file_contains() {
  local file="$1" expected="$2"
  grep -Fqx "$expected" "$file" || fail "$file does not contain '$expected'"
}

wait_for_file() {
  local file="$1" attempt
  for ((attempt = 0; attempt < 100; attempt++)); do
    [ -e "$file" ] && return 0
    sleep 0.02
  done
  fail "timed out waiting for $file"
}

assert_command_fails_with() {
  local expected="$1"
  shift
  local output status

  set +e
  output=$("$@" 2>&1)
  status=$?
  set -e
  [ "$status" -ne 0 ] || fail "expected failure: $*"
  assert_contains "$output" "$expected"
}

env_value() {
  local worktree="$1" key="$2"
  sed -n "s/^${key}=//p" "$worktree/.env"
}

hash_slot() {
  local name="$1" hash
  hash="$(printf '%s' "$name" | md5sum | cut -c1-8)"
  echo $(( (16#$hash % 631) + 1 ))
}

gateway_for_slot() {
  echo $((20000 + $1 * 20))
}

# Isolated repository fixture ------------------------------------------------

# Start a repository with both a legacy tag and the current allocator on main.
new_fixture() {
  local name="$1"

  CURRENT_TEST_ROOT="$TEST_ROOT/$name"
  FIXTURE_REPO="$CURRENT_TEST_ROOT/shop"
  mkdir -p "$FIXTURE_REPO/scripts/lib"

  git init -q -b main "$FIXTURE_REPO"
  git -C "$FIXTURE_REPO" config user.name "Worktree Test"
  git -C "$FIXTURE_REPO" config user.email "worktree-test@example.invalid"

  printf '%s\n' '#!/usr/bin/env bash' 'echo legacy allocator' > "$FIXTURE_REPO/scripts/dev-env.sh"
  chmod +x "$FIXTURE_REPO/scripts/dev-env.sh"
  printf '%s\n' '.env' > "$FIXTURE_REPO/.gitignore"
  # The Compose expression must remain literal in the fixture.
  # shellcheck disable=SC2016
  printf '%s\n' 'name: ${COMPOSE_PROJECT_NAME:-shop}' 'services: {}' > "$FIXTURE_REPO/docker-compose.yml"
  git -C "$FIXTURE_REPO" add .
  git -C "$FIXTURE_REPO" commit -q -m "legacy fixture"
  git -C "$FIXTURE_REPO" tag legacy-allocator

  cp "$REPO_ROOT/scripts/dev-env.sh" "$FIXTURE_REPO/scripts/dev-env.sh"
  cp "$REPO_ROOT/scripts/create-worktree.sh" "$FIXTURE_REPO/scripts/create-worktree.sh"
  cp "$REPO_ROOT/scripts/lib/worktree-port-allocation.sh" "$FIXTURE_REPO/scripts/lib/worktree-port-allocation.sh"
  chmod +x "$FIXTURE_REPO/scripts/dev-env.sh" "$FIXTURE_REPO/scripts/create-worktree.sh"
  git -C "$FIXTURE_REPO" add .
  git -C "$FIXTURE_REPO" commit -q -m "current allocator fixture"
}

create_raw_worktree() {
  local name="$1" path branch_suffix
  path="$CURRENT_TEST_ROOT/$name"
  branch_suffix="$(printf '%s' "$name" | md5sum | cut -c1-12)"
  git -C "$FIXTURE_REPO" worktree add -q "$path" -b "test/raw-$branch_suffix" HEAD
  echo "$path"
}

fixture_wrapper() {
  (cd "$FIXTURE_REPO" && scripts/create-worktree.sh "$@")
}

run_dev_env() {
  local worktree="$1"
  shift
  (cd "$worktree" && scripts/dev-env.sh "$@")
}

find_free_hash_name() {
  local prefix="$1" candidate slot gateway offset occupied
  local index=1

  while :; do
    candidate="${prefix}-${index}"
    slot="$(hash_slot "$candidate")"
    gateway="$(gateway_for_slot "$slot")"
    occupied=no
    for ((offset = 0; offset < 20; offset++)); do
      if ss -H -ltn "sport = :$((gateway + offset))" 2>/dev/null | grep -q .; then
        occupied=yes
        break
      fi
    done
    if [ "$occupied" = no ]; then
      echo "$candidate"
      return 0
    fi
    index=$((index + 1))
  done
}

find_hash_collision() {
  local prefix="$1" candidate slot index
  declare -A seen=()

  for ((index = 1; index <= 700; index++)); do
    candidate="${prefix}-${index}"
    slot="$(hash_slot "$candidate")"
    if [ -n "${seen[$slot]+set}" ]; then
      COLLISION_FIRST="${seen[$slot]}"
      COLLISION_SECOND="$candidate"
      return 0
    fi
    seen[$slot]="$candidate"
  done
  fail "could not find a deterministic hash collision"
}

# Wrapper and basic checkout journeys ---------------------------------------

test_known_hash_vector() {
  assert_equals "$(hash_slot payments)" 600
}

test_wrapper_explains_and_validates_its_contract() {
  local help_output
  new_fixture wrapper-validation

  help_output="$(fixture_wrapper --help 2>&1)"
  assert_contains "$help_output" "Ordered actions and artifacts"
  assert_contains "$help_output" "<target>/.env"
  assert_contains "$help_output" "No services, containers, or volumes are started"

  assert_command_fails_with "invalid branch name" \
    fixture_wrapper invalid -b 'bad..branch' HEAD
  assert_command_fails_with "base ref does not resolve" \
    fixture_wrapper invalid -b test/missing missing-ref
  mkdir "$CURRENT_TEST_ROOT/existing"
  assert_command_fails_with "target path already exists" \
    fixture_wrapper "$CURRENT_TEST_ROOT/existing" -b test/existing HEAD
  assert_command_fails_with "refusing a legacy" \
    fixture_wrapper "$CURRENT_TEST_ROOT/legacy-target" -b test/legacy legacy-allocator
  [ ! -e "$CURRENT_TEST_ROOT/legacy-target" ] || fail "legacy preflight created a worktree"
}

test_wrapper_creates_branch_directory_and_managed_env() {
  local output target slot
  new_fixture wrapper-success

  target="$CURRENT_TEST_ROOT/path with spaces"
  output="$(fixture_wrapper "$target" -b feature/readable HEAD)"
  slot="$(env_value "$target" DEV_ENV_SLOT)"

  assert_contains "$output" "Worktree creation is complete"
  assert_equals "$slot" "$(hash_slot "$(basename "$target")")"
  assert_equals "$(git -C "$target" branch --show-current)" feature/readable
  assert_equals "$(grep -Fxc '# >>> dev-env (managed by scripts/dev-env.sh — do not edit between the markers)' "$target/.env")" 1
  assert_equals "$(grep -Fxc '# <<< dev-env' "$target/.env")" 1
  run_dev_env "$target" --check
}

test_main_checkout_uses_defaults_without_a_claim() {
  local output exports
  new_fixture main-checkout

  output="$(run_dev_env "$FIXTURE_REPO")"
  exports="$(run_dev_env "$FIXTURE_REPO" --exports)"
  assert_contains "$output" "Main checkout — slot 0"
  assert_contains "$exports" 'export GATEWAY_PORT="10000"'
  assert_contains "$exports" 'export KAFKA_HOST_PORT="9092"'
  [ ! -e "$FIXTURE_REPO/.env" ] || fail "main checkout unexpectedly received a claim"
  run_dev_env "$FIXTURE_REPO" --check
  assert_command_fails_with "main checkout always uses slot 0" \
    run_dev_env "$FIXTURE_REPO" --set-slot 12
}

test_main_ports_cannot_overlap_the_worktree_range() {
  new_fixture main-port-overlap

  # 29092 is offset 12 in slot 454's reserved block (29080-29099).
  sed -i 's/\[KAFKA_HOST_PORT\]=9092/[KAFKA_HOST_PORT]=29092/' \
    "$FIXTURE_REPO/scripts/lib/worktree-port-allocation.sh"

  assert_command_fails_with "main port 29092 overlaps worktree range 20020-32639" \
    run_dev_env "$FIXTURE_REPO" --exports
}

# Claim lifecycle and validation --------------------------------------------

test_existing_claim_is_reused_and_exported() {
  local worktree slot output exports
  new_fixture claim-reuse
  worktree="$(create_raw_worktree reusable)"
  run_dev_env "$worktree" >/dev/null
  slot="$(env_value "$worktree" DEV_ENV_SLOT)"

  output="$(run_dev_env "$worktree")"
  exports="$(run_dev_env "$worktree" --exports)"
  assert_contains "$output" "Existing worktree claim is valid and unique"
  assert_contains "$exports" "export GATEWAY_PORT=\"$(gateway_for_slot "$slot")\""
}

test_registry_skips_self_and_unmanaged_env() {
  local owner unmanaged slot
  new_fixture registry-skips
  owner="$(create_raw_worktree owner)"
  unmanaged="$(create_raw_worktree unmanaged)"
  printf '%s\n' 'LOCAL_ONLY_VALUE=<placeholder>' > "$unmanaged/.env"

  run_dev_env "$owner" >/dev/null
  slot="$(env_value "$owner" DEV_ENV_SLOT)"
  run_dev_env "$owner" --set-slot "$slot" >/dev/null

  assert_equals "$(cat "$unmanaged/.env")" 'LOCAL_ONLY_VALUE=<placeholder>'
  run_dev_env "$owner" --check
}

test_claimed_hash_slot_bumps_without_erasing_user_values() {
  local owner target initial_slot output
  new_fixture claim-bump
  owner="$(create_raw_worktree owner)"
  target="$(create_raw_worktree bump-target)"
  initial_slot="$(hash_slot bump-target)"
  run_dev_env "$owner" --set-slot "$initial_slot" >/dev/null
  printf '%s\n' 'LOCAL_ONLY_VALUE=<placeholder>' > "$target/.env"

  output="$(run_dev_env "$target" 2>&1)"
  assert_contains "$output" "Hash candidate $initial_slot was unavailable"
  [ "$(env_value "$target" DEV_ENV_SLOT)" != "$initial_slot" ] || fail "claimed slot was not bumped"
  assert_file_contains "$target/.env" 'LOCAL_ONLY_VALUE=<placeholder>'
}

test_set_slot_rejects_another_worktrees_claim() {
  local owner target slot
  new_fixture set-slot-claimed
  owner="$(create_raw_worktree slot-owner)"
  target="$(create_raw_worktree slot-target)"
  run_dev_env "$owner" >/dev/null
  slot="$(env_value "$owner" DEV_ENV_SLOT)"

  assert_command_fails_with "slot $slot is claimed by $owner" \
    run_dev_env "$target" --set-slot "$slot"
  [ ! -e "$target/.env" ] || fail "rejected --set-slot wrote a claim"
}

test_repeated_writes_preserve_unmanaged_values() {
  local worktree before after
  new_fixture preserve-env
  worktree="$(create_raw_worktree preserve-target)"
  printf '%s\n' \
    'POSTGRES_AWS_HOST=<db-host>' \
    'POSTGRES_AWS_PASSWORD=<db-password>' > "$worktree/.env"
  before="$(grep '^POSTGRES_AWS_' "$worktree/.env")"

  run_dev_env "$worktree" >/dev/null
  run_dev_env "$worktree" --set-slot "$(env_value "$worktree" DEV_ENV_SLOT)" >/dev/null
  after="$(grep '^POSTGRES_AWS_' "$worktree/.env")"

  assert_equals "$after" "$before"
  assert_equals "$(grep -Fxc '# >>> dev-env (managed by scripts/dev-env.sh — do not edit between the markers)' "$worktree/.env")" 1
  assert_equals "$(grep -Fxc '# <<< dev-env' "$worktree/.env")" 1
}

test_foreign_malformed_claim_blocks_every_registry_consumer() {
  local malformed healthy healthy_slot
  new_fixture malformed-foreign-claim
  malformed="$(create_raw_worktree malformed)"
  healthy="$(create_raw_worktree healthy)"
  run_dev_env "$malformed" >/dev/null
  run_dev_env "$healthy" >/dev/null
  healthy_slot="$(env_value "$healthy" DEV_ENV_SLOT)"
  sed -i 's/^GATEWAY_PORT=.*/GATEWAY_PORT=9999/' "$malformed/.env"

  assert_command_fails_with "disagrees with DEV_ENV_SLOT" run_dev_env "$healthy" --check
  assert_command_fails_with "disagrees with DEV_ENV_SLOT" run_dev_env "$healthy"
  assert_command_fails_with "disagrees with DEV_ENV_SLOT" run_dev_env "$healthy" --set-slot "$healthy_slot"
}

test_malformed_markers_fail_closed() {
  local duplicate reversed
  new_fixture malformed-markers
  duplicate="$(create_raw_worktree duplicate-markers)"
  reversed="$(create_raw_worktree reversed-markers)"

  printf '%s\n' \
    '# >>> dev-env (managed by scripts/dev-env.sh — do not edit between the markers)' \
    '# >>> dev-env (managed by scripts/dev-env.sh — do not edit between the markers)' \
    '# <<< dev-env' > "$duplicate/.env"
  printf '%s\n' \
    '# <<< dev-env' \
    '# >>> dev-env (managed by scripts/dev-env.sh — do not edit between the markers)' > "$reversed/.env"

  assert_command_fails_with "malformed or duplicate" run_dev_env "$duplicate" --check
  assert_command_fails_with "markers in the wrong order" run_dev_env "$reversed" --check
}

test_listener_on_an_unassigned_offset_reserves_the_whole_block() {
  local name worktree slot port
  new_fixture listening-port
  name="$(find_free_hash_name listener-target)"
  worktree="$(create_raw_worktree "$name")"
  slot="$(hash_slot "$name")"
  # Offset 12 has no service today. It is still reserved so a future service
  # can use it without invalidating an already allocated slot.
  port=$(( $(gateway_for_slot "$slot") + 12 ))

  mkdir -p "$CURRENT_TEST_ROOT/listener-bin"
  cat > "$CURRENT_TEST_ROOT/listener-bin/ss" <<'SS_STUB'
#!/usr/bin/env bash
if [[ " $* " == *":${DEV_ENV_TEST_TAKEN_PORT} "* ]]; then
  echo "LISTEN 0 128 127.0.0.1:${DEV_ENV_TEST_TAKEN_PORT} 0.0.0.0:*"
fi
SS_STUB
  chmod +x "$CURRENT_TEST_ROOT/listener-bin/ss"

  (cd "$worktree" && PATH="$CURRENT_TEST_ROOT/listener-bin:$PATH" \
    DEV_ENV_TEST_TAKEN_PORT="$port" scripts/dev-env.sh >/dev/null)
  [ "$(env_value "$worktree" DEV_ENV_SLOT)" != "$slot" ] || fail "listening slot was not bumped"
}

test_stale_worktree_metadata_is_reported_and_skipped() {
  local stale healthy output
  new_fixture stale-metadata
  stale="$(create_raw_worktree stale-target)"
  healthy="$(create_raw_worktree healthy-target)"
  run_dev_env "$healthy" >/dev/null
  mv "$stale" "$CURRENT_TEST_ROOT/stale-target-moved"

  output="$(run_dev_env "$healthy" --check 2>&1)"
  assert_contains "$output" "ignoring missing worktree"
}

# Locking and failure behavior ----------------------------------------------

test_allocator_waits_for_the_clone_wide_lock() {
  local worktree pid
  new_fixture lock-wait
  worktree="$(create_raw_worktree lock-target)"

  exec 8>"$FIXTURE_REPO/.git/dev-env-allocation.lock"
  flock 8
  run_dev_env "$worktree" > "$CURRENT_TEST_ROOT/allocator.log" 2>&1 &
  pid=$!
  sleep 1
  kill -0 "$pid" 2>/dev/null || fail "allocator did not wait for the held lock"
  [ ! -e "$worktree/.env" ] || fail "allocator wrote its claim without owning the lock"
  flock -u 8
  exec 8>&-

  wait "$pid" || fail "allocator failed after the lock was released"
  [ -f "$worktree/.env" ] || fail "allocator did not write its claim after acquiring the lock"
}

test_concurrent_hash_collision_gets_distinct_slots() {
  local first second first_pid second_pid second_entered_early=no
  new_fixture concurrent-collision
  find_hash_collision concurrent
  first="$(create_raw_worktree "$COLLISION_FIRST")"
  second="$(create_raw_worktree "$COLLISION_SECOND")"

  mkdir -p "$CURRENT_TEST_ROOT/concurrency-bin" "$CURRENT_TEST_ROOT/concurrency-state"
  cat > "$CURRENT_TEST_ROOT/concurrency-bin/ss" <<'SS_STUB'
#!/usr/bin/env bash
state_dir="${DEV_ENV_TEST_STATE_DIR:?}"
worktree_name="$(basename "$PWD")"
touch "$state_dir/$worktree_name-entered-port-check"
if [ "$worktree_name" = "${DEV_ENV_TEST_PAUSED_WORKTREE:?}" ] &&
   [ ! -e "$state_dir/first-check-released" ]; then
  while [ ! -e "$state_dir/release-first-check" ]; do
    sleep 0.02
  done
  touch "$state_dir/first-check-released"
fi
exit 0
SS_STUB
  chmod +x "$CURRENT_TEST_ROOT/concurrency-bin/ss"

  PATH="$CURRENT_TEST_ROOT/concurrency-bin:$PATH" \
    DEV_ENV_TEST_STATE_DIR="$CURRENT_TEST_ROOT/concurrency-state" \
    DEV_ENV_TEST_PAUSED_WORKTREE="$COLLISION_FIRST" \
    run_dev_env "$first" > "$CURRENT_TEST_ROOT/first.log" 2>&1 &
  first_pid=$!
  wait_for_file "$CURRENT_TEST_ROOT/concurrency-state/$COLLISION_FIRST-entered-port-check"

  PATH="$CURRENT_TEST_ROOT/concurrency-bin:$PATH" \
    DEV_ENV_TEST_STATE_DIR="$CURRENT_TEST_ROOT/concurrency-state" \
    DEV_ENV_TEST_PAUSED_WORKTREE="$COLLISION_FIRST" \
    run_dev_env "$second" > "$CURRENT_TEST_ROOT/second.log" 2>&1 &
  second_pid=$!
  sleep 0.2
  if [ -e "$CURRENT_TEST_ROOT/concurrency-state/$COLLISION_SECOND-entered-port-check" ]; then
    second_entered_early=yes
  fi
  touch "$CURRENT_TEST_ROOT/concurrency-state/release-first-check"

  wait "$first_pid" || fail "first concurrent allocator failed"
  wait "$second_pid" || fail "second concurrent allocator failed"

  [ "$second_entered_early" = no ] || fail "second allocator entered the critical section before the first finished"
  [ "$(env_value "$first" DEV_ENV_SLOT)" != "$(env_value "$second" DEV_ENV_SLOT)" ] ||
    fail "concurrent allocators wrote the same slot"
}

test_worktree_registry_read_failure_fails_closed() {
  local worktree
  new_fixture registry-read-failure
  worktree="$(create_raw_worktree registry-failure-target)"
  run_dev_env "$worktree" >/dev/null
  mkdir -p "$CURRENT_TEST_ROOT/failing-bin"
  cat > "$CURRENT_TEST_ROOT/failing-bin/git" <<'GIT_STUB'
#!/usr/bin/env bash
if [[ " $* " == *" worktree list --porcelain -z "* ]]; then
  exit 1
fi
exec /usr/bin/git "$@"
GIT_STUB
  chmod +x "$CURRENT_TEST_ROOT/failing-bin/git"

  assert_command_fails_with "refusing to use an incomplete worktree registry" \
    env PATH="$CURRENT_TEST_ROOT/failing-bin:$PATH" bash -c "cd \"$worktree\" && scripts/dev-env.sh --check"
}

test_regenerate_stops_old_project_then_moves_claim() {
  local worktree old_slot new_slot
  new_fixture regenerate
  worktree="$(create_raw_worktree regenerating)"
  run_dev_env "$worktree" >/dev/null
  old_slot="$(env_value "$worktree" DEV_ENV_SLOT)"

  mkdir -p "$CURRENT_TEST_ROOT/bin"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$CURRENT_TEST_ROOT/bin/docker"
  chmod +x "$CURRENT_TEST_ROOT/bin/docker"
  PATH="$CURRENT_TEST_ROOT/bin:$PATH" run_dev_env "$worktree" --regenerate >/dev/null

  new_slot="$(env_value "$worktree" DEV_ENV_SLOT)"
  [ "$new_slot" != "$old_slot" ] || fail "regenerate did not move to a new slot"
  run_dev_env "$worktree" --check
}

test_atomic_write_failure_is_not_reported_as_success() {
  local worktree
  new_fixture write-failure
  worktree="$(create_raw_worktree write-failure-target)"
  mkdir -p "$CURRENT_TEST_ROOT/failing-bin"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 1' > "$CURRENT_TEST_ROOT/failing-bin/mv"
  chmod +x "$CURRENT_TEST_ROOT/failing-bin/mv"

  assert_command_fails_with "cannot atomically replace" \
    env PATH="$CURRENT_TEST_ROOT/failing-bin:$PATH" bash -c "cd \"$worktree\" && scripts/dev-env.sh"
  [ ! -e "$worktree/.env" ] || fail "failed atomic move left a completed .env claim"
}

test_wrapper_retains_failed_allocation_with_complete_recovery() {
  local target output status
  new_fixture wrapper-recovery
  target="$CURRENT_TEST_ROOT/recovery-target"
  mkdir -p "$CURRENT_TEST_ROOT/failing-bin"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 1' > "$CURRENT_TEST_ROOT/failing-bin/mktemp"
  chmod +x "$CURRENT_TEST_ROOT/failing-bin/mktemp"

  set +e
  output="$(PATH="$CURRENT_TEST_ROOT/failing-bin:$PATH" \
    fixture_wrapper "$target" -b feature/recovery HEAD 2>&1)"
  status=$?
  set -e
  [ "$status" -ne 0 ] || fail "wrapper unexpectedly succeeded when .env writing failed"
  [ -d "$target" ] || fail "wrapper removed the failed worktree"
  git -C "$FIXTURE_REPO" show-ref --verify --quiet refs/heads/feature/recovery || fail "wrapper removed the new branch"
  assert_contains "$output" "Git worktree was left in place"
  assert_contains "$output" "new branch 'feature/recovery' is also retained"
  assert_contains "$output" "git -C $FIXTURE_REPO worktree remove $target"
  assert_contains "$output" "git -C $FIXTURE_REPO branch -D feature/recovery"
}

test_wrapper_creates_distinct_claims_for_colliding_names() {
  local first second
  new_fixture wrapper-two-worktrees
  find_hash_collision wrapper
  first="$CURRENT_TEST_ROOT/$COLLISION_FIRST"
  second="$CURRENT_TEST_ROOT/$COLLISION_SECOND"

  fixture_wrapper "$first" -b feature/wrapper-first HEAD >/dev/null
  fixture_wrapper "$second" -b feature/wrapper-second HEAD >/dev/null

  [ "$(env_value "$first" DEV_ENV_SLOT)" != "$(env_value "$second" DEV_ENV_SLOT)" ] ||
    fail "wrapper-created worktrees received the same claim"
  run_dev_env "$first" --check
  run_dev_env "$second" --check
}

# Git-layout compatibility and full-project smoke ---------------------------

test_separate_git_directory_is_still_the_main_checkout() {
  local root git_dir output target
  CURRENT_TEST_ROOT="$TEST_ROOT/separate-git-dir"
  root="$CURRENT_TEST_ROOT/checkout"
  git_dir="$CURRENT_TEST_ROOT/git-data"
  mkdir -p "$CURRENT_TEST_ROOT"
  git init -q -b main --separate-git-dir "$git_dir" "$root"
  git -C "$root" config user.name "Worktree Test"
  git -C "$root" config user.email "worktree-test@example.invalid"
  mkdir -p "$root/scripts/lib"
  cp "$REPO_ROOT/scripts/dev-env.sh" "$root/scripts/dev-env.sh"
  cp "$REPO_ROOT/scripts/create-worktree.sh" "$root/scripts/create-worktree.sh"
  cp "$REPO_ROOT/scripts/lib/worktree-port-allocation.sh" "$root/scripts/lib/worktree-port-allocation.sh"
  chmod +x "$root/scripts/dev-env.sh" "$root/scripts/create-worktree.sh"
  git -C "$root" add .
  git -C "$root" commit -q -m "separate git directory fixture"

  output="$(run_dev_env "$root")"
  assert_contains "$output" "Main checkout — slot 0"
  [ ! -e "$root/.env" ] || fail "separate-git-dir main checkout received a worktree claim"

  target="$CURRENT_TEST_ROOT/checkout-worktrees/separate-wrapper"
  (cd "$root" && scripts/create-worktree.sh separate-wrapper -b feature/separate-wrapper HEAD >/dev/null)
  [ -d "$target" ] || fail "wrapper derived the wrong main checkout for a separate Git directory"
  run_dev_env "$target" --check
}

test_full_repository_wrapper_to_compose_smoke() {
  local copy archive target
  CURRENT_TEST_ROOT="$TEST_ROOT/full-repository-smoke"
  copy="$CURRENT_TEST_ROOT/shop"
  archive="$CURRENT_TEST_ROOT/working-tree.tar"
  target="$CURRENT_TEST_ROOT/shop-worktrees/smoke"
  mkdir -p "$copy"

  (cd "$REPO_ROOT" && git ls-files -co --exclude-standard -z | \
    tar --null --files-from=- -cf "$archive")
  tar -xf "$archive" -C "$copy"
  git init -q -b main "$copy"
  git -C "$copy" config user.name "Worktree Test"
  git -C "$copy" config user.email "worktree-test@example.invalid"
  git -C "$copy" add .
  git -C "$copy" commit -q -m "full working-tree fixture"

  (cd "$copy" && scripts/create-worktree.sh smoke -b feature/smoke HEAD >/dev/null)
  run_dev_env "$target" --check
  docker compose --project-directory "$target" config --quiet
  git -C "$copy" worktree remove "$target"
  git -C "$copy" branch -D feature/smoke >/dev/null
  [ ! -e "$target" ] || fail "full-repository smoke did not tear down its worktree"
}

run_test() {
  local description="$1"
  shift
  TEST_NUMBER=$((TEST_NUMBER + 1))
  printf '[%02d] %s\n' "$TEST_NUMBER" "$description"
  "$@"
}

# Executable table of contents ----------------------------------------------

run_test "known basename hash remains stable" test_known_hash_vector
run_test "wrapper documents and validates its contract" test_wrapper_explains_and_validates_its_contract
run_test "wrapper creates branch, path, and managed .env" test_wrapper_creates_branch_directory_and_managed_env
run_test "main checkout uses slot-zero defaults" test_main_checkout_uses_defaults_without_a_claim
run_test "main ports cannot overlap complete worktree blocks" test_main_ports_cannot_overlap_the_worktree_range
run_test "existing claims are reused and exported" test_existing_claim_is_reused_and_exported
run_test "registry skips the current claim and unmanaged .env files" test_registry_skips_self_and_unmanaged_env
run_test "claimed candidates bump without erasing user .env values" test_claimed_hash_slot_bumps_without_erasing_user_values
run_test "explicit slots reject another worktree's claim" test_set_slot_rejects_another_worktrees_claim
run_test "repeated writes preserve unmanaged .env values" test_repeated_writes_preserve_unmanaged_values
run_test "malformed foreign claims block all registry consumers" test_foreign_malformed_claim_blocks_every_registry_consumer
run_test "malformed marker structures fail closed" test_malformed_markers_fail_closed
run_test "unassigned offsets reserve the complete 20-port block" test_listener_on_an_unassigned_offset_reserves_the_whole_block
run_test "stale Git metadata is reported and skipped" test_stale_worktree_metadata_is_reported_and_skipped
run_test "allocator waits for the clone-wide lock" test_allocator_waits_for_the_clone_wide_lock
run_test "concurrent hash collisions receive distinct slots" test_concurrent_hash_collision_gets_distinct_slots
run_test "worktree-registry read failures fail closed" test_worktree_registry_read_failure_fails_closed
run_test "regeneration stops the old project and moves the claim" test_regenerate_stops_old_project_then_moves_claim
run_test "atomic write failures cannot report success" test_atomic_write_failure_is_not_reported_as_success
run_test "wrapper preserves failed allocation with recovery commands" test_wrapper_retains_failed_allocation_with_complete_recovery
run_test "wrapper-created colliding names receive distinct claims" test_wrapper_creates_distinct_claims_for_colliding_names
run_test "separate Git directories still identify the main checkout" test_separate_git_directory_is_still_the_main_checkout
run_test "full repository reaches valid Compose configuration" test_full_repository_wrapper_to_compose_smoke

echo "All $TEST_NUMBER worktree creation tests passed."
