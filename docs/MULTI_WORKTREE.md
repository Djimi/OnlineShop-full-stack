# Multi-Worktree Local Development

## Create and start a worktree

Run the persisted Bash function from any checkout in the clone:

```bash
wtc feature/payments
```

The name defaults to both the new branch and the worktree directory. `wtc`
changes the calling shell into the new worktree by default; pass `false` as the
second argument to stay where you are:

```bash
wtc feature/payments false
```

A relative directory name resolves against the main checkout's sibling
`<repository>-worktrees/` directory, so `--name ../shared` creates a sibling of
that directory; an absolute path is used as-is. A branch name containing `/`
nests the directory the same way. Override the base ref (commit or branch name,
default `main`), branch, and directory when needed:

```bash
wtc feature/payments -b origin/main
wtc feature/payments -b
wtc payments --branch feature/payments --name payments
```

A bare `-b` branches from the current branch (`.`) instead of `main`. The
underlying command is also available without the wrapper:
`scripts/create-worktree.py feature/payments false`.
If a different repository has no local script, `wtc` creates the same
`<repository>-worktrees/<name>` directory on branch `<name>` with plain Git
(default base `main`; `-b` selects another base) and neither allocates Compose
ports nor fast-forwards the checkout.

Before creating anything, the command runs `git pull --ff-only` on the current
checkout's configured upstream. It stops if the checkout has no upstream or
cannot be fast-forwarded; it never merges or rebases. Existing local branches
and target paths are rejected, so a name collision cannot silently create a
different worktree. The selected base must contain `docker-compose.yml` with
the project and ten port variables shown below. An older base without that
contract is left as an incomplete worktree and reported with exact cleanup
commands.

The command performs these steps in order:

1. Lock the clone and fast-forward the current checkout from its upstream.
2. Validate the new branch, base ref, and target path.
3. Create the Git branch and worktree.
4. Verify that the checked-out Compose file consumes the generated values.
5. Lock allocation for the whole clone.
6. Find a slot that no registered worktree claims and whose ports are free.
7. Write the worktree's Docker Compose values to `.env`.
8. Print the allocated ports and start command; `wtc` then enters the target
   unless `false` was passed.

It does not start containers or create volumes. After it succeeds with
`false`, or when using the Python command directly:

```bash
cd ../OnlineShop-full-stack-worktrees/feature/payments
docker compose up -d --build
```

Docker Compose automatically reads `.env`. The file contains one managed block
with `COMPOSE_PROJECT_NAME`, `WORKTREE_SLOT`, and the ten host-port variables
used by `docker-compose.yml`. Values outside that block are preserved.

## Port allocation

Each worktree owns one of 631 slots. Slot `N` starts at `20000 + N×20`, so slot
1 is `20020–20039` and the complete worktree range is `20020–32639`. Offsets
0–9 are assigned today; offsets 10–19 are reserved and checked so future
services can use them safely.

| Offset | Service | Main checkout | Worktree slot N |
|---:|---|---:|---:|
| 0 | API gateway | 10000 | `20000 + N×20` |
| 1 | Items | 9000 | gateway + 1 |
| 2 | Auth | 9001 | gateway + 2 |
| 3 | Frontend | 5173 | gateway + 3 |
| 4 | Items PostgreSQL | 5432 | gateway + 4 |
| 5 | Auth PostgreSQL | 5433 | gateway + 5 |
| 6 | pgAdmin | 5051 | gateway + 6 |
| 7 | Redis | 6379 | gateway + 7 |
| 8 | Kafka host listener | 9092 | gateway + 8 |
| 9 | Kafka UI | 8080 | gateway + 9 |

The worktree directory name hashes to the first candidate. Under a clone-wide
file lock, the command reads claims from every registered worktree's `.env`,
checks all 20 candidate ports, and advances until it finds a free block. The lock prevents
concurrent creators from choosing the same slot. A stopped stack keeps its
claim until its worktree is removed. Each existing claim must contain the
expected Compose project and ten ports for its slot; inconsistent claims stop
allocation rather than being ignored.

## Failure and recovery

If Git succeeds but allocation fails, the command leaves the new worktree and
branch in place for inspection and prints their exact removal commands. Fix the
reported cause, remove the incomplete worktree and branch, then run the same
creation command again.

If `docker compose up` later reports a bind error, another application took a
claimed port after allocation. Stop that application and retry Compose. The
allocator can only observe ports while it runs; it cannot reserve sockets for
Docker indefinitely.

The claim registry and lock cover one Git clone. Separate clones do not see one
another. The allocator is intentionally Linux-only because it uses Python's
`fcntl` file locking.

## Teardown

```bash
docker compose down -v
git worktree remove ../OnlineShop-full-stack-worktrees/feature/payments
```

Removing the worktree removes its gitignored `.env` and releases the claim.
Use `-v` only when deleting development data is intentional.

For a concise execution trace, see
[MULTI_WORKTREE_WORKFLOW.md](./MULTI_WORKTREE_WORKFLOW.md).
