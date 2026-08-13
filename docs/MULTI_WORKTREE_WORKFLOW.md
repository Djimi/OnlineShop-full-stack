# Multi-Worktree Workflow

## Normal path

From any checkout in the clone:

```bash
scripts/create-worktree.sh payments -b feature/payments
```

The command performs one fail-closed sequence:

```text
validate arguments, base ref, and target path
        │
        ▼
verify the base commit contains allocator version 2
        │
        ▼
git worktree add
        │
        ▼
verify the checked-out target still contains allocator version 2
        │
        ▼
lock the clone-wide allocation file
        │
        ▼
scan and validate other worktree claims
        │
        ▼
hash basename ──► candidate slot
        │
        ▼
claimed slot or listener in its 20-port block? ── yes ──► try next slot
        │ no
        ▼
atomically write the target .env
        │
        ▼
print ports and next commands
```

After success:

```bash
cd ../OnlineShop-full-stack-worktrees/payments
scripts/dev-env.sh --check
docker compose up -d --build
```

There is no separate first-time `dev-env.sh` setup step. Worktree creation and
port allocation are one operation.

## Two worktrees start from the same hash slot

The hash is only a deterministic starting point. Suppose worktrees A and B
both hash to slot 313:

1. A takes the clone-wide allocation lock, validates the registry, and writes
   its slot-313 claim.
2. B waits for the lock.
3. B reloads the registry, sees A's claim even if A's stack is stopped, and
   advances to slot 314.

Both concurrent and staggered creation are covered. All 20 offsets are checked,
including the ten currently unassigned offsets reserved for future services.

## Existing worktree maintenance

Running `scripts/dev-env.sh` again reuses an existing valid claim. It does not
report its ports as free, because the worktree's own containers may be using
them.

If a claim becomes duplicated or a later external process takes a port:

```bash
scripts/dev-env.sh --regenerate
docker compose up -d --build
```

Regeneration stops the old Compose project before entering the short allocation
critical section. It then revalidates the claim under the lock, selects the
next free slot, and atomically updates `.env`.

## Creation failure after Git succeeds

The wrapper deliberately leaves the worktree visible. It prints both recovery
paths:

```bash
# Correct the cause and allocate from inside the new worktree
cd <new-worktree>
scripts/dev-env.sh

# Or remove the worktree explicitly
git -C <existing-checkout> worktree remove <new-worktree>
git -C <existing-checkout> branch -D <new-branch>
```

Automatic deletion would hide useful state and could remove files created by a
hook or another process after `git worktree add`. If explicit removal refuses
because the directory contains untracked files, inspect them first and add
`--force` only when their deletion is intentional.

## Day-to-day commands

```bash
scripts/dev-env.sh --check
docker compose up -d --build
docker compose logs -f items-service
docker compose down
```

Host-run mode:

```bash
source <(scripts/dev-env.sh --exports)
cd Items
SERVER_PORT="$ITEMS_SERVER_PORT" \
SPRING_DATASOURCE_URL="$ITEMS_DATASOURCE_URL" \
SPRING_DATASOURCE_USERNAME="$ITEMS_DATASOURCE_USERNAME" \
SPRING_DATASOURCE_PASSWORD="$ITEMS_DATASOURCE_PASSWORD" \
./run-dev.sh
```

Teardown and claim release:

```bash
docker compose down -v
git worktree remove <worktree-path>
```

The complete port table, command reference, and guarantee boundaries are in
[MULTI_WORKTREE.md](./MULTI_WORKTREE.md).
