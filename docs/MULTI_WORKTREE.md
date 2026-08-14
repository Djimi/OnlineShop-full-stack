# Multi-Worktree Local Development

## Create and start a worktree

Run the single supported creation command from any checkout in the clone:

```bash
scripts/create-worktree.py payments -b feature/payments
```

A bare name is placed under the main checkout's sibling
`<repository>-worktrees/` directory. An explicit path and base ref also work:

```bash
scripts/create-worktree.py ../review-123 -b review/123 origin/main
```

The selected base must contain `docker-compose.yml` with the project and ten
port variables shown below. An older base without that contract is left as an
incomplete worktree and reported with exact cleanup commands.

The command performs these steps in order:

1. Validate the new branch, base ref, and target path.
2. Create the Git branch and worktree.
3. Lock allocation for the whole clone.
4. Verify that the checked-out Compose file consumes the generated values.
5. Find a slot that no registered worktree claims and whose ports are free.
6. Write the worktree's Docker Compose values to `.env`.
7. Print the allocated ports and start command.

It does not start containers or create volumes. After it succeeds:

```bash
cd ../OnlineShop-full-stack-worktrees/payments
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

The worktree name hashes to the first candidate. Under a clone-wide file lock,
the command reads claims from every registered worktree's `.env`, checks all
20 candidate ports, and advances until it finds a free block. The lock prevents
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
git worktree remove ../OnlineShop-full-stack-worktrees/payments
```

Removing the worktree removes its gitignored `.env` and releases the claim.
Use `-v` only when deleting development data is intentional.

For a concise execution trace, see
[MULTI_WORKTREE_WORKFLOW.md](./MULTI_WORKTREE_WORKFLOW.md).
