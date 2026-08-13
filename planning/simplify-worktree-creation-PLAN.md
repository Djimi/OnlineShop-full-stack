# Simplify Worktree Creation — PLAN

## Goal

Single-command worktree creation with **unique claims and observed-free ports at
generation time**:

```bash
scripts/create-worktree.sh <path|name> -b <new-branch> [base-ref=main]
# exit 0 ⇒ slot uniquely claimed in this clone AND its 20-port block observed free
```

Replaces the current multi-step flow (`git worktree add` + `dev-env.sh` + `--check` guard,
knowledge scattered across AGENTS.md + two docs) with one definitive command.

## Decisions (locked with the user)

1. **Allocation design: flock + lock→validate→write.**
   Under `flock($GIT_COMMON/dev-env-allocation.lock)`: scan other worktrees' `.env`
   claims + bind-check the complete 20-port block → bump until clean → single
   atomic `.env` write.
   - Closes the **staggered-generation** hole (claim scan sees *stopped* stacks; today's
     bind-check only sees running ones).
   - Closes the **concurrent-generation race** (lock serializes allocators; kernel-held,
     so a crashed holder never deadlocks).
   - Single write after clean validation ⇒ `.env` is never poisoned by a mid-loop crash
     (no CLAIM/SLOT two-phase machinery needed — that was only required for the lock-free
     Dekker variant, which was considered and rejected).
2. **Structure: wrapper + readable engine/library split.**
   - `scripts/create-worktree.sh` (NEW) — creation orchestration, run from any existing
     checkout, operates on another directory.
   - `scripts/dev-env.sh` (MOD) — readable CLI + lifecycle modes
     (`--check/--regenerate/--exports/--set-slot`), run from inside a worktree, operates
     on self.
   - `scripts/lib/worktree-port-allocation.sh` (NEW) — sourceable allocation mechanics: validated
     claims, clone-wide lock, listener checks, slot search, and atomic write.
3. **Slot scheme:** 631 slots × 20-port blocks from 20000; complete maximum
   reserved port 32639 < ephemeral floor 32768. Hash of worktree basename =
   starting candidate only. Offsets 0–9 are assigned and 10–19 are reserved.
4. **Test gate:** `tests/scripts/worktree_creation_test.sh`, isolated temp-repo
   fixtures, no containers or Docker daemon work (`docker compose config` only).

## Algorithm (canonical — will live in the dev-env.sh header)

```
hash(basename) → candidate slot (1..631)
under flock($GIT_COMMON/dev-env-allocation.lock, 30s timeout):
    loop (≤631 tries):
        claimed by another worktree's .env?   (slot_claimed_by_other)
        OR any port in the slot's 20-port block bound?  (block_has_listener)
        → next slot; hard error (exit 1) when all slots exhausted
    write .env once (tmp + atomic mv)
new-allocation exit 0 ⇒ slot unique across registered worktrees in this clone AND
                        ports observed free immediately before the claim write
```

Claim registry = other worktrees' `.env` files, enumerated via the NUL-delimited
`git worktree list --porcelain -z` format (arbitrary paths supported). Claims
are released by `git worktree remove`.

## Tasks

### Phase 0 — Bootstrap
- [x] Create worktree `../OnlineShop-full-stack-worktrees/simplify-worktree-creation`,
      branch `feature/simplify-worktree-creation` from `main` (old way — the new script
      cannot bootstrap itself)
- [x] Allocate own dev ports via legacy `scripts/dev-env.sh`
- [x] Persist this plan

### Phase 1 — Engine: `scripts/dev-env.sh`
- [x] Compute `GIT_COMMON` once; acquire `flock -w 30` on
      `$GIT_COMMON/dev-env-allocation.lock` (fd 9) in generate, regenerate, and
      explicit-slot modes;
      loud `exit 1` on timeout. `--check`/`--exports` stay lock-free read-only.
- [x] New validated claim registry:
      - enumerate via `git worktree list --porcelain -z`
      - skip: own `$ROOT` and unmanaged `.env` files; warn for nonexistent paths
        (deleted-not-pruned), never crash
      - fail closed on malformed managed blocks; `DEV_ENV_SLOT`, project name, and all
        ten ports must describe one coherent slot
- [x] Unified clean-candidate predicate `! slot_claimed_by_other && ! any_port_taken`
      used by: hash-derived generate, `--regenerate`, `--set-slot` (forcing a claimed
      slot fails loudly, naming the claimant worktree)
- [x] `--check` hardening: foreign claim on our slot ⇒ exit 1 + "run --regenerate" guidance
- [x] Header rewrite = canonical algorithm doc: lock semantics, claim registry semantics
      (released by `git worktree remove`), failure modes/exit codes, the
      "external apps above 20k = operator responsibility" caveat
- [x] Keep the allocation critical section short: Docker shutdown happens before
      locking; regeneration re-reads its claim under the lock before writing
- [x] Extract allocation mechanics into `scripts/lib/worktree-port-allocation.sh`; keep the
      user-facing engine focused on CLI modes and their ordered orchestration
- [x] Validate the port configuration at startup: service offsets are unique and
      fit in one block; main ports are unique and outside `20020–32639`
- [x] Reserve and bind-check all 20 offsets so future services can use offsets
      10–19 without invalidating existing worktree claims

### Phase 2 — Wrapper: `scripts/create-worktree.sh` (NEW)
- [x] Usage: `scripts/create-worktree.sh <path|name> -b <new-branch> [base-ref=main]`
- [x] Validation: `git check-ref-format` for branch; base ref exists
      (`git rev-parse --verify`); target path absent
- [x] Bare name (no `/`) resolves to sibling `<main-dirname>-worktrees/<name>`
      (derived from `git worktree list` first entry, not hardcoded)
- [x] `git worktree add` → failure: exit 1 (git's error is already clear)
- [x] Silent-downgrade guard: preflight the base commit, then re-check the target;
      both must contain engine sentinel version 2 and its allocation library. An
      older base ref fails before creation instead of weakening success.
- [x] `(cd <new-worktree> && scripts/dev-env.sh)` — deliberately runs the *target
      branch's* engine copy. On failure: leave the worktree in place, print exact
      recovery commands (`scripts/dev-env.sh` inside it, or `git worktree remove`).
      No auto-delete of user-visible state — fail closed, raise hand.
- [x] Success: port table (engine prints it) + next steps (`cd`, `docker compose up -d --build`)
- [x] Header documents the scoped guarantee contract for a new allocation

### Phase 3 — Tests: `tests/scripts/worktree_creation_test.sh`
Conventions follow existing `tests/scripts/*.sh` gates; isolated temp-repo fixtures;
**no containers or Docker daemon operations** (`docker compose config` is allowed).
- [x] hash→slot determinism
- [x] claim scan: detects claimed slot; skips self / missing paths / unmanaged `.env`
- [x] bump on claimed slot; bump on a deterministically stubbed listening port; clean
      slot accepted (real socket creation is forbidden by the agent sandbox)
- [x] two concurrent allocations ⇒ distinct slots (lock serialization proof)
- [x] `--regenerate` excludes own old claim; `--set-slot` rejects a claimed slot
- [x] wrapper arg-validation failures (bad branch name, missing base, existing path)
- [x] target-branch version guard rejects the legacy allocator
- [x] malformed or hand-edited managed claims fail closed
- [x] end-to-end in temp repo: create two worktrees ⇒ distinct slots, correct `.env` blocks
- [x] Full-repository smoke: isolated copy of the current working tree ⇒ wrapper creates
      throwaway worktree ⇒ `--check` ⇒ `docker compose config` ⇒ verified teardown.
      The isolated copy is necessary before commit because the real branch's `HEAD`
      does not yet contain the new allocator files.

### Phase 4 — Documentation propagation
- [x] `docs/MULTI_WORKTREE.md` — Quick Start → one command; rewrite "Collision
      Probability": staggered-generation hole CLOSED (claim scan), race CLOSED (lock);
      claim lifecycle (released by `git worktree remove`)
- [x] `docs/MULTI_WORKTREE_WORKFLOW.md` — steps 1–4 collapse into the single wrapper
      call + inside-view trace; "two worktrees same slot" section rewritten
- [x] `AGENTS.md` — "Starting Services Locally": worktree creation =
      `scripts/create-worktree.sh`; `--check` guard stays
- [x] Service-level `AGENTS.md` files and `docs/DEBUG_INFO.md` propagated
- [x] This plan: tick checkboxes as tasks complete

### Phase 5 — Verify & hand back
- [x] `bash -n` + `shellcheck -x` on wrapper, engine, library, and test gate
- [x] Run `bash tests/scripts/worktree_creation_test.sh` — all pass
- [x] Current-worktree `scripts/dev-env.sh --check` + `docker compose config --quiet`
- [x] Diff summary to user

### Phase 6 — Whole-script readability and correctness review
- [x] Make Git registry reads fail closed by checking the exact
      `worktree list --porcelain -z` process status
- [x] Replace path-based main detection with Git-directory identity, including
      `--separate-git-dir` repositories
- [x] Guarantee explicit lock release after every locked operation, including
      registry, validation, and write failures
- [x] Make managed-block rendering and atomic-write failures observable even
      when Bash errexit is disabled by a caller's conditional context
- [x] Expand wrapper help with the ordered branch, directory, `.env`, Compose,
      port, and no-container outcomes; print complete retained-state recovery
- [x] Organize engine, library, and tests for progressive disclosure: command
      flow first, mechanics by responsibility, user journeys as isolated tests
- [x] Strengthen coverage for deterministic concurrency, failed registry reads,
      self/unmanaged claims, explicit-slot conflicts, idempotent `.env`
      preservation, separate Git directories, and two wrapper-created claims

### Phase 7 — Readability and future port capacity
- [x] Replace cryptic internal `DE_`/`de_` names with explicit
      `DEV_ENV_`/`dev_env_` names; ordinary function locals remain unprefixed
- [x] Document the complete `dev-env.sh` purpose and mode behavior before its
      allocation algorithm
- [x] Refactor `create-worktree.sh` into an explicit high-level `main` flow
- [x] Move main Kafka host access from `29092` (inside slot 454's reserved
      block) to standard host port `9092`; container `PLAINTEXT_HOST` remains 29092
- [x] Add permanent tests for the full-block listener check and rejection of any
      future main port inside the worktree range

## Issues register

| # | Issue | Status |
|---|-------|--------|
| 1 | A base branch lacking the new engine would silently downgrade allocation | ✅ fixed — version/library guard fails closed; no legacy fallback |
| 2 | External apps using ports >20k | ✅ accepted by operator; bind-check still catches *running* listeners at generation time; exposure window = between allocation and `docker compose up` |
| 3 | Abandoned worktrees hold slot claims forever | ✅ by design (that IS the staggered-generation protection); released by `git worktree remove`; 631 slots make leakage a non-issue |
| 4 | flock is Linux-only | ✅ accepted — the allocator explicitly requires Linux `flock`, `ss`, and `md5sum` |
| 5 | Worktrees at arbitrary paths missed by scan | ✅ porcelain enumeration used, verified against `aws-test` (lives outside `-worktrees/`) |
| 6 | A hand-edited managed block could claim one slot while Compose used another | ✅ fixed — project name and all ten ports must match `DEV_ENV_SLOT` |
| 7 | Regeneration held the global allocation lock during Docker shutdown | ✅ fixed — shutdown precedes locking; the claim is revalidated under lock |
| 8 | Registry-read and malformed-foreign-claim failures could be mistaken for an unclaimed slot | ✅ fixed — registry loading is explicit and errors remain distinct from “not claimed” |
| 9 | Bash conditional contexts could suppress `errexit` inside the atomic writer | ✅ fixed — every render, copy, append, replace, cleanup, and move is checked explicitly |
| 10 | The scripts and cumulative test scenario imposed excessive cognitive load | ✅ fixed — top-down execution map, responsibility-based sections, isolated test journeys, and executable test table of contents |
| 11 | Main Kafka host port 29092 occupied offset 12 of slot 454's future capacity | ✅ fixed — main uses 9092 and startup validation rejects every main port in `20020–32639` |

## Out of scope (deliberately)

- Dynamic slot count from `ip_local_port_range` (would give 638 vs fixed 631 — marginal
  gain, extra moving part; existing overlap warning stays)
- Auto-removal of failed worktrees (fail closed + raise hand instead)
- Worktrees of *other clones* of this repo (separate `.git`, invisible to the claim
  registry — same limitation as today, documented)
