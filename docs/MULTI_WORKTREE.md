# Multi-Worktree Local Development

## Create a worktree

Run one command from any existing checkout:

```bash
scripts/create-worktree.sh payments -b feature/payments
```

This creates the worktree from `main`, allocates a unique development-port
slot, and writes its managed `.env` block. A bare name such as `payments` is
placed under the main checkout's sibling `<repository>-worktrees/` directory.

Use an explicit path or base ref when needed:

```bash
scripts/create-worktree.sh ../review-123 -b review/123 origin/main
```

Success means:

- no registered worktree in this clone claims the selected slot;
- none of the slot's complete 20-port block was listening during allocation;
- the new worktree contains one internally consistent, atomically written
  `.env` claim.

The command fails rather than falling back when the target base ref predates
the current allocator. If Git created the worktree but allocation failed, the
worktree and its new branch remain in place, and the command prints exact
recovery or removal instructions for both.

## Start and stop the stack

```bash
cd ../OnlineShop-full-stack-worktrees/payments
scripts/dev-env.sh --check
docker compose up -d --build

# Later
docker compose down
```

`--check` validates the complete managed block and confirms that no other
worktree claims the same slot. It does not require the ports to be free because
this worktree's own stack may already be running.

## Allocation model

Each non-main worktree receives one of 631 slots. A slot owns a 20-port block
starting at port 20000. The ten currently published ports use offsets 0–9;
offsets 10–19 are already reserved and checked for future services.

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

The worktree basename hashes to the first candidate slot. Allocation then runs
under a clone-wide `flock`: it reads every registered worktree's validated
`.env` claim, checks all 20 ports in the candidate block, bumps when necessary,
and writes once. Stopped stacks therefore retain their slots, concurrent
creators cannot select the same slot, and offsets 10–19 remain safe for future
services.

All main-checkout ports must stay outside the complete worktree range
`20020–32639`. Kafka therefore uses the standard host port `9092`; Compose maps
that to the dedicated `PLAINTEXT_HOST` listener on container port `29092`.

A claim is released when its worktree is removed. Stale Git metadata for a
manually deleted directory is ignored with a warning and can be removed with
`git worktree prune`.

## Maintenance commands

```bash
scripts/dev-env.sh              # Show/reuse the existing claim; allocate only if absent
scripts/dev-env.sh --check      # Validate this worktree's unique claim
scripts/dev-env.sh --regenerate # Stop the old Compose project and move to the next free slot
scripts/dev-env.sh --exports    # Print variables for host-run development
scripts/dev-env.sh --set-slot N # Allocate one specific free slot
```

Use `--regenerate --volumes` only when the old development volumes should also
be deleted. `--set-slot` does not stop a stack on the previous slot; normally
`--regenerate` is the safer recovery command.

## Host-run development

Load the worktree-specific service, database, Kafka, Redis, and frontend
addresses before starting a component outside Compose:

```bash
source <(scripts/dev-env.sh --exports)

# Examples
cd Items
SERVER_PORT="$ITEMS_SERVER_PORT" \
SPRING_DATASOURCE_URL="$ITEMS_DATASOURCE_URL" \
SPRING_DATASOURCE_USERNAME="$ITEMS_DATASOURCE_USERNAME" \
SPRING_DATASOURCE_PASSWORD="$ITEMS_DATASOURCE_PASSWORD" \
./run-dev.sh

cd frontend && npm run dev -- --port "$FRONTEND_PORT"
```

The frontend export is important: without `VITE_API_URL`, Vite falls back to
the main checkout's gateway at port 10000.

For E2E tests, run the Maven wrapper from the E2E module as required by the
project testing rules:

```bash
cd e2e-tests
E2E_BASE_URL=http://localhost:<GATEWAY_PORT> ./mvnw clean test
```

## Boundaries of the guarantee

- The claim registry covers worktrees of one Git clone. A separate clone has a
  separate Git common directory and allocation lock.
- A non-participating application can bind a selected port after allocation.
  The allocator observes the complete block during allocation; it cannot keep
  all 20 ports bound for Docker indefinitely.
- Linux tooling is required (`flock`, `ss`, and `md5sum`).

For a concise execution trace and recovery examples, see
[MULTI_WORKTREE_WORKFLOW.md](./MULTI_WORKTREE_WORKFLOW.md).

## Teardown

```bash
docker compose down -v
git worktree remove ../OnlineShop-full-stack-worktrees/payments
```

Removing the worktree deletes its untracked `.env` and therefore releases its
slot claim. Use `-v` only when deleting the development data is intentional.
