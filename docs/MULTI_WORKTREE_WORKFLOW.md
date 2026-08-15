# Multi-Worktree Workflow

## Normal path

```bash
scripts/create-worktree.py feature/payments
```

The command's control flow mirrors the code:

```text
parse and validate the request
        ↓
create the Git branch and worktree
        ↓
verify the checked-out Compose contract
        ↓
lock allocation for this clone
        ↓
read slots claimed by registered worktrees
        ↓
hash the new worktree name to a starting slot
        ↓
claimed slot or occupied port? ── yes ──► try the next slot
        │ no
        ↓
atomically write the managed .env block
        ↓
print the ports and Docker Compose command
```

After success:

```bash
cd ../OnlineShop-full-stack-worktrees/feature/payments
docker compose up -d --build
```

## Why concurrent creation is safe

Suppose two worktrees hash to the same slot. The first creator takes the lock
and writes its `.env` claim. The second waits, reloads all claims after taking
the lock, sees the first claim, and advances to the next free slot. This works
whether the first stack is running or stopped.

Claim loading also verifies the stored Compose project and all ten ports. A
hand-edited or partial managed block stops allocation with a direct error.

## Failure after Git creation

Allocation is deliberately the only step after `git worktree add`. If it
fails, the command keeps the worktree and branch so nothing created by Git or a
checkout hook is silently deleted. It prints commands to remove both. Inspect
the worktree, fix the reported setup problem, remove the incomplete state,
and rerun the creation command.

The full port table, guarantee boundaries, and teardown command are in
[MULTI_WORKTREE.md](./MULTI_WORKTREE.md).
