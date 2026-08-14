# Simplify Worktree Creation — PLAN

## Goal

Provide one readable command that creates a development worktree and its Docker
Compose port configuration:

```bash
scripts/create-worktree.py <path-or-name> -b <new-branch> [base-ref]
```

The implementation should read from top to bottom like the operation it
performs. It replaces the Bash wrapper, maintenance CLI, allocation library,
and legacy modes rather than maintaining compatibility with them.

## Design

- One standard-library Python file owns the complete operation.
- `main()` shows the complete high-level flow before any implementation detail.
- A clone-wide `fcntl` lock protects claim discovery, slot selection, and the
  `.env` write.
- Registered worktrees' `.env` files are the claim registry.
- Every claim must contain the Compose project and ten ports implied by its
  slot; malformed or inconsistent claims fail closed.
- Each slot reserves 20 ports; ten are written for current Compose services and
  ten remain available for future services.
- The checked-out base must consume every generated Compose value; no allocator
  version negotiation or legacy fallback is provided.
- The worktree name hashes to a starting slot. Claimed blocks and blocks with a
  bound local port are skipped.
- `.env` is replaced atomically while every value outside the managed block is
  preserved.
- If allocation fails after Git creation, visible state remains and explicit
  removal instructions are printed.

## Tasks

- [x] Replace the Bash scripts and allocation library with
      `scripts/create-worktree.py`.
- [x] Remove maintenance-only modes (`--check`, `--regenerate`, `--exports`,
      `--set-slot`, and `--volumes`).
- [x] Rename the managed slot value to the explicit `WORKTREE_SLOT`.
- [x] Keep unique stopped-worktree claims and concurrent allocation safety.
- [x] Check the complete 20-port block before writing the claim.
- [x] Preserve unrelated `.env` values and use an atomic replace.
- [x] Replace the large Bash scenario gate with nine focused Python tests
      covering creation, stopped and inconsistent claims, listeners,
      concurrency, base compatibility, Compose defaults, and recovery.
- [x] Update root, service, workflow, troubleshooting, and port documentation.
- [x] Complete independent code review and apply only comments that improve
      correctness or readability without restoring removed complexity.

## Issues register

| Issue | Status |
|---|---|
| Concurrent creators could choose the same slot | ✅ Fixed by the clone-wide lock |
| A stopped worktree could lose its port claim | ✅ Fixed by reading registered `.env` claims |
| Future services could collide in unused offsets | ✅ Fixed by checking all 20 ports |
| A hand-edited claim could point Compose at another worktree's ports | ✅ Fixed by validating the project and all ten ports against the slot |
| An older base ref could ignore the generated `.env` values | ✅ Fixed by verifying its checked-out Compose contract before allocation |
| A main Compose default could enter the worktree range | ✅ Fixed by a focused contract test; Kafka remains on host port 9092 |
| Allocation can fail after Git creates visible state | ✅ Worktree is retained and exact cleanup is printed |
| Another process can bind a port after allocation | ✅ Accepted boundary; stop that process before Compose starts |
| Separate clones can choose the same ports | ✅ Accepted boundary; the registry is intentionally clone-local |

## Deliberately removed

- Independent environment setup or repair commands.
- Explicit slot selection and regeneration.
- Host-run environment export generation.
- Allocator version negotiation and legacy fallbacks.
- Shell-specific fallback paths and multi-file internal APIs.
- Exhaustive tests for removed modes and implementation details.
