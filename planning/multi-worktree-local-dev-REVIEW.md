# Multi-Worktree Local Dev — REVIEW (3-agent ruthless comparison)

**Date:** 2026-08-02
**Subject of review:** `multi-worktree-local-dev-PLAN.md` (Plan B) vs `local-worktree-port-isolation-PLAN.md` (Plan A, in worktree `/home/dpm/CodingProjects/OnlineShop-port-isolation`)
**Method:** 3 independent subagent reviews — (1) technical soundness, (2) DX/simplicity, (3) devil's advocate. All claims verified against the actual code and compose files.
**Verdict:** **Plan B wins 3/3** — correctly sized, idiomatic (stock compose + `.env` + project names), zero workflow change. Plan A is the better *analysis* but prescribes a bespoke 8-subcommand launcher CLI (~50 tasks, locked allocator, state in `~/.local/state`, PowerShell parity, deliberately breaking raw `docker compose up`) — wildly over-engineered for a learning project needing 3–4 worktrees.

**However: Plan B as written is NOT ready to implement.** All three reviewers converged on one critical bug (F1) plus several required fixes below. The plan must be revised to address every item in this review before implementation starts.

---

## CRITICAL — must fix before implementation

### F1. Port math is broken: cross-service collision class (all 3 reviewers found this)

`host port = base + slot×100` collides **across services** whenever two bases share the same mod-100 residue:

- `gateway(slot s) = 10000 + 100s`
- `items(slot s+10) = 9000 + 100(s+10) = 10000 + 100s` → **identical port**

This is not theoretical. Running the plan's own hash over the worktrees that exist on disk today:

- `demo-csp` → slot 1 → gateway on **10100**
- `demo-token-storage` → slot 11 → items on **10100**

→ two real worktrees collide **today**, and the free-port check only catches it if the victim stack happens to be running at generation time. The plan's collision math ("~12% with 4 worktrees") only models same-slot collisions and silently ignores this whole class. 39 such slot pairs exist.

**Fix (agreed by all reviewers): one contiguous 100-port block per worktree, services at fixed intra-block offsets.** No cross-service collision can exist by construction. Recommended scheme (finalize in plan):

| Service | Intra-block offset | Slot 0 (main, legacy) | Slot N (N≥1) = `20000 + N×100 + offset` |
|---|---|---|---|
| api-gateway | +0 | 10000 | e.g. slot 25 → 22500 |
| items-service | +1 | 9000 | 22501 |
| auth-service | +2 | 9001 | 22502 |
| frontend | +3 | 5173 | 22503 |
| items-postgres | +4 | 5432 | 22504 |
| auth-postgres | +5 | 5433 | 22505 |
| pgadmin | +6 | 5051 | 22506 |
| redis | +7 | 6379 | 22507 |
| kafka (PLAINTEXT_HOST) | +8 | 29092 | 22508 |
| kafka-ui | +9 | 8080 | 22509 |
| kafka controller (9093) | — | — | **do not publish at all** (see F6) |

- Slot 0 keeps today's exact legacy ports → main checkout byte-identical (this invariant must be preserved).
- Blocks at 20000–24999 stay clear of all legacy ports (incl. 29092) and the Linux ephemeral range (32768+).
- After this fix, the only remaining collision class is same-slot — the plan's math must be corrected to state the honest same-slot probability (see F7).

### F2. Hash spec contradicts its own worked example

The plan says `slot = md5(dir_name) mod 49 + 1`, but the printed table (demo-cors→47, demo-xss→17, multi-worktree-local-dev→25) only reproduces when truncating the md5 to its **first 8 hex chars**. Over the full 128-bit digest the same inputs give 13/22/47. "Deterministic, same slot on any machine" is therefore false **as specified** — two implementers get different slots. The revised plan must pin the exact algorithm in one unambiguous line (e.g. `slot = (16#$(echo -n "$dir" | md5sum | cut -c1-8)) mod 49 + 1`) and a recomputed, verified example table.

### F3. `--regenerate` orphans containers and volumes

Bumping the slot changes `COMPOSE_PROJECT_NAME` → the old project's containers/volumes become invisible to `docker compose down` with the new `.env`. The revised plan must require: read the OLD `.env` first, `docker compose down` (optionally `-v`) the old project, THEN rewrite the block. Also drop the claim "same slot on any machine" — after any bump, the slot depends on machine port state; document slot overrides explicitly.

### F4. Forgot-to-generate footgun (blast radius lands on main)

`.env` is gitignored, so a fresh worktree has none; a bare `docker compose up` there grabs the **canonical ports**. If main is down, the worktree hijacks them — and main's next `up` fails. The plan promised to protect main; this failure mode does the opposite. Mitigations to specify in the plan (at minimum the first two):

1. Docs: "first action in any new worktree = `scripts/dev-env.sh`" — in AGENTS.md and `docs/MULTI_WORKTREE.md`.
2. A guard: e.g. compose file sets `name: ${COMPOSE_PROJECT_NAME:-onlineshop}` **and** the generator is the only thing that writes port vars — combined with a loud pre-up check script or a make-style wrapper for worktrees. Decide one concrete mechanism and specify it; "document it" alone is not enough for agent-driven workflows.
3. Optional: a `docker compose config`-based pre-commit/pre-up check that fails if a non-main worktree has no managed block.

### F5. Host-run frontend silently talks to the WRONG gateway

`--exports` prints only Spring/Redis/Kafka exports. Host-run Vite (`npm run dev` outside Docker) does not get `VITE_API_URL`, and the frontend API client falls back to hardcoded `http://localhost:10000` → the worktree's frontend **silently hits main's gateway** — a silent cross-worktree data-plane mixup, the worst possible failure mode (worse than a loud bind error). Fix: add `VITE_API_URL` and `SERVER_PORT`/`AUTH_PORT` etc. to `--exports`, and document the host-run flow end-to-end.

### F6. Kafka controller port 9093 omitted (worktree #2 fails to start)

Current compose publishes `9093:9093` (KRaft controller). The plan parameterizes 9092 and adds 29092 but never mentions 9093 → literal implementation leaves a fixed `9093:9093` and the second worktree's Kafka fails to start. Fix: **stop publishing 9093 entirely** (nothing on the host consumes the controller port; Plan A's inventory caught this). Related verified context: publishing 9092 today is actively misleading (advertises unresolvable `kafka:9092` to host clients) — host tooling should use only the `PLAINTEXT_HOST` listener (29092); consider whether 9092 needs host publishing at all. Note: no service currently uses Kafka (verified: no kafka dependency in any pom), so this is tooling-only — still fix it now while the file is open.

---

## MAJOR — correctness/honesty fixes to the plan text

### F7. Collision math is understated — state honest numbers

With 49 slots, same-slot birthday probability is ~12% for 4 worktrees but **~45–50% for the 8–9 worktrees currently on disk** (`git worktree list`). After F1's port-block fix this is the only remaining class. It's acceptable (bind check + regenerate recover), but the plan must: (a) state the real numbers, (b) acknowledge the bind check only sees **currently running** stacks (staggered generation → late bind error at `up` time, recoverable via `--regenerate`), (c) acknowledge the concurrent-generation TOCTOU race (two agents generating at once) as an accepted, recoverable risk.

### F8. Baseline mismatch: the plan is written against the wrong compose file

The worktree sits at `a941e9d` (= origin/main): **9** `container_name` lines, **no frontend service**, `api.ts` hardcodes `localhost:10000`, gateway CORS fixed to ports 5173/5174/3000 with credentials. The plan's tasks ("remove all 10 `container_name` lines", add `FRONTEND_PORT`/`VITE_API_URL` compose env) describe the **unmerged branch** `build_and_release_first_iteration` (10 names, containerized frontend, `VITE_API_URL` in `api.ts`, wildcard CORS). The revised plan must:

- State its baseline explicitly (which branch/commit it targets) and rebase/retarget if needed.
- Make the CORS dependency explicit: per-worktree frontend ports work cross-origin **only because the branch wildcarded CORS**. If the plan merges before/regardless of that branch, it needs its own CORS task (or a Vite dev proxy). No silent dependencies.

### F9. "Byte-identical for main" is an overclaim — correct it

Removing `container_name` renames every main-stack container (`items-postgres` → `<project>-items-postgres-1`), so `docker exec <container>` snippets in `commands.txt` / `Auth/queries.md` break (the doc task exists — good — but the claim in the Goal section must be corrected to "ports and startup behavior unchanged; container names become project-prefixed"). Amusingly, `commands.txt` already references a stale project-derived network name — this repo has already been bitten once by implicit project naming; explicit `COMPOSE_PROJECT_NAME` per worktree is overdue.

### F10. Slot 0 is keyed on exact directory basename

Any second clone literally named `OnlineShop-full-stack` silently gets slot 0 → total collision with main, zero detection. Document the limitation; make the generator detect "am I the main checkout?" more robustly (e.g. `git worktree list` first entry / `git rev-parse --git-common-dir` comparison) or fail loudly on ambiguity.

---

## Conscious scope decisions — state as non-goals or follow-ups (don't silently ignore)

- **Perf stack** (`tests/performance/docker-compose.perf.yml`): fixed ports 5433/9001 + `container_name: perf-*` ×3 — it collides with the main stack **even today**, let alone across worktrees. Plan A covered this; Plan B doesn't. Decide: fix now (cheap: same `${VAR:-default}` treatment) or declare an explicit non-goal with a follow-up task.
- **Devcontainer** (`.devcontainer/docker-compose.yml`): host-global `container_name: onlineshop-workspace` → two devcontainers can't coexist. Same decision needed.
- **Loopback binding** (stolen from Plan A, cheap and worth it): today Postgres/Redis with weak dev creds are exposed on `0.0.0.0` (LAN). Prefixing published ports with `127.0.0.1:` is a one-line-per-port security win and prevents worktree stacks from being reachable off-host. Recommend adopting; note it breaks LAN/phone demos (acceptable — nobody does those).
- **Ephemeral ports for infra** (considered and rejected by the plan — rejection stands, but note the middle ground for the record): in full-container mode only gateway + frontend strictly need stable host ports; DBs/Redis/Kafka could be `"5432"` (no host side) discovered via `docker compose port`. Keep stable ports per the plan (pgAdmin/kafka-ui bookmarks, curl workflows, host-run dev) — no change required, just document that this was reconsidered.

---

## What the revised plan must contain (definition of "implementable by another agent")

1. **The idea in ≤10 lines**: slot per worktree → 100-port block → `.env` managed block → `docker compose up` unchanged.
2. **Exact, unambiguous hash algorithm** (F2) + recomputed example table verified by actually running the command.
3. **Final port-mapping table** (F1): every service, slot-0 legacy port, intra-block offset, worked example for 2–3 real worktrees.
4. **Day-to-day workflow story** ("how we switch to worktrees"):
   - First-time setup in a new worktree (one command).
   - Daily up/down/logs/ps — must be plain `docker compose …` (the `.env` does the work).
   - How to discover a worktree's URLs/ports.
   - What to do on collision (`--regenerate`, with F3's down-first behavior).
   - Teardown: `docker compose down [-v]` before `git worktree remove`.
   - Host-run dev mode (F5) and e2e against a worktree (`E2E_BASE_URL`).
5. **Compose diff spec**: every changed line listed (ports, `container_name` removals, Kafka listeners incl. dropping 9093, `VITE_API_URL`, loopback prefix if adopted) — an implementer should not need to re-derive anything.
6. **Script spec**: flags (`--regenerate`, `--exports`), managed-block format, secrets-preservation requirement with a test against a `.env` containing `POSTGRES_AWS_*` lines, slot-bump behavior incl. F3, main-checkout detection (F10), guardrail for F4.
7. **Honest risk section** (F7) and explicit non-goals (perf stack, devcontainer) with follow-up tasks.
8. **Verification section** updated: 2-stack concurrent run, frontend B → gateway B, main regression (`docker compose config` diff), forgot-to-generate guard test, regenerate-orphan test.
9. Keep AGENTS.md planning conventions: checkboxes for tasks; Issues list where solved issues get ✅ + brief how-fixed notes.

## What NOT to do (from Plan A's mistakes)

- No bespoke launcher CLI replacing `docker compose` — the `.env` mechanism keeps stock commands working.
- No state outside the repo (`~/.local/state`) — worktree state belongs in the worktree (`.env`).
- No factual claims without code verification (Plan A's CORS premise was false on the active branch).
- No 4-stack verification fantasy (≈16–20 GB RAM) — 2 concurrent stacks is the realistic DoD.
