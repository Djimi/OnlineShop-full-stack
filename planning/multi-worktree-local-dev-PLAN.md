# Multi-Worktree Local Dev Without Port Collisions — PLAN (revised)

> Historical plan: its Bash commands and maintenance modes have been replaced
> by the completed Python design in
> [simplify-worktree-creation-PLAN.md](./simplify-worktree-creation-PLAN.md).
> The only supported creation command is now `scripts/create-worktree.py`.

> Rationale for every decision: `planning/multi-worktree-local-dev-REVIEW.md` (3-agent review, fixes F1–F10). This file is self-contained.

> **Historical implementation record:** the slot count, bind-only allocation,
> and manual first-time workflow below describe the original implementation.
> [simplify-worktree-creation-PLAN.md](./simplify-worktree-creation-PLAN.md) is
> the current contract and supersedes those parts of this plan. In particular,
> main Kafka now uses host port `9092`, and every worktree reserves and checks
> its complete 20-port block rather than only assigned offsets 0–9.

## Goal

Allow multiple git worktrees to run the full stack (`docker compose up -d --build`) simultaneously on the same machine with zero port/container collisions.

**Main-checkout guarantees:**
- Host port **numbers** and startup workflow (`docker compose up -d --build`, no `.env` needed) are unchanged.
- Container names become project-prefixed (`onlineshop-items-postgres-1` instead of `items-postgres`) because all `container_name:` lines are removed — `docker exec <name>` snippets in docs must migrate to `docker compose exec <service>`.
- Two intentional, justified deltas from "unchanged": (a) published ports bind to `127.0.0.1` instead of `0.0.0.0` (security; kills LAN/phone demos — accepted, nobody does those); (b) Kafka host mappings `9092`/`9093` are dropped (nothing on the host consumes them — see Compose diff spec).
- CI is unaffected: no workflow changes.

## Design

### The idea

1. Every worktree gets a **slot**: main checkout = 0 (legacy ports), any other = hash-derived 1–619.
2. Slot N owns a 20-port block `20000 + N×20 … +19`; each service sits at a **fixed intra-block offset** (gateway +0, items +1, …). Cross-service collisions are impossible by construction.
3. `scripts/dev-env.sh` writes the slot's ports + `COMPOSE_PROJECT_NAME` as a managed block into the worktree's gitignored `.env`.
4. `docker compose up` stays completely unchanged — compose reads `.env`, defaults keep main on legacy ports.
5. Same-slot collisions (only remaining class) are caught by a bind check at generation time and repaired with `--regenerate`.

### Baseline (read first — F8)

This plan targets the **main checkout's current state**: `/home/dpm/CodingProjects/OnlineShop-full-stack` on branch `build_and_release_first_iteration` (commit `27b41df`). Verified against its `docker-compose.yml` (234 lines): **10** `container_name` entries (lines 5,25,45,64,80,121,144,167,190,212), **has** the containerized `frontend` service (`5173:5173`, env `VITE_API_URL: http://localhost:10000`, dev-server container so compose `environment:` is read at Vite start), items build context `.` + `Items/Dockerfile`, items-postgres volume `/var/lib/postgresql`, items healthcheck `curl -f http://localhost:9000/actuator/health || exit 1`.

**This worktree (`multi-worktree-local-dev`) is at commit `a941e9d`: 9 `container_name` entries, NO frontend service, different items build context/volume/healthcheck.** → First task: rebase this worktree onto `build_and_release_first_iteration`; every compose line reference below is against the rebased file.

**CORS dependency (explicit):** per-worktree frontend ports work cross-origin only because this branch wildcarded gateway CORS (`allowedOrigins("*")` in `api-gateway/.../CorsConfig.java` and `allowed-origins: "*"` + `allow-credentials: false` in `api-gateway/src/main/resources/application.yml` — both verified). If CORS is ever re-narrowed to fixed origins, per-worktree frontends break and this plan needs a Vite dev-proxy follow-up.

### Port mapping (final — F1)

Slot 0 = main checkout = today's legacy ports. Slot N (1–619) = `20000 + N×20 + offset`. Internal container ports never change; inter-service traffic uses the Compose network, untouched.

| Offset | Service | `.env` var (slot-0 default) | Slot 0 | Slot N |
|---|---|---|---|---|
| +0 | api-gateway | `GATEWAY_PORT` | 10000 | 20000+N×20+0 |
| +1 | items-service | `ITEMS_PORT` | 9000 | +1 |
| +2 | auth-service | `AUTH_PORT` | 9001 | +2 |
| +3 | frontend | `FRONTEND_PORT` | 5173 | +3 |
| +4 | items-postgres | `ITEMS_DB_PORT` | 5432 | +4 |
| +5 | auth-postgres | `AUTH_DB_PORT` | 5433 | +5 |
| +6 | pgadmin | `PGADMIN_PORT` | 5051 | +6 |
| +7 | redis | `REDIS_PORT` | 6379 | +7 |
| +8 | kafka (PLAINTEXT_HOST) | `KAFKA_HOST_PORT` | 29092 | +8 |
| +9 | kafka-ui | `KAFKA_UI_PORT` | 8080 | +9 |
| — | kafka controller 9093 | — | **not published** | **not published** |

- All published ports are bound to `127.0.0.1` (loopback prefix — adopted from the review's scope suggestions; dev DBs/Redis no longer LAN-exposed).
- Kafka **9093 controller: not published at all** (F6 — nothing on the host consumes it; leaving it fixed would break worktree #2). Kafka **9092: also not published** — today it advertises unresolvable `kafka:9092` to host clients (actively misleading); host tooling uses only `PLAINTEXT_HOST` (29092), in-cluster clients (kafka-ui) use the Compose network. Decision: publish exactly one Kafka port.
- Block range 20020–32389: clears all legacy ports (max 29092) and stays below the Linux ephemeral range (32768+).
- **Postman convenience:** all ports for a slot are sequential from the gateway port (items = gateway+1, auth = gateway+2, etc.). Set one Postman variable (`gatewayUrl = http://localhost:<GATEWAY_PORT>`) and derive all other service URLs by adding offsets.
- **Verified:** for slots 1–619 × offsets 0–9, no generated port equals any legacy port (5173, 5432, 5433, 5051, 6379, 8080, 9000, 9001, 9092, 10000, 29092) and all 6190 generated ports are pairwise unique (no intra- or cross-slot overlap). Both assertions verified by bash loop.

### Slot hash algorithm (exact — F2)

Initial slot for a non-main worktree (run from anywhere inside it):

```bash
slot=$(( (16#$(echo -n "$(basename "$(git rev-parse --show-toplevel)")" | md5sum | cut -c1-8) % 619) + 1 ))
```

md5 of the worktree directory basename → first 8 hex chars → hex number → mod 619 → +1. Updated example table (619-slot recalc, 2026-08-02):

| Worktree | Slot | Port block |
|---|---|---|
| `OnlineShop-full-stack` (main) | **0 — reserved** (hash would be 447; detection overrides, see F10) | legacy |
| `OnlineShop-port-isolation` | 295 | 25900–25919 |
| `demo-csp` | 125 | 22500–22519 |
| `demo-token-storage` | 371 | 27420–27439 |
| `demo-xss` | 80 | 21600–21619 |
| `demo-clickjacking` | 450 | 29000–29019 |
| `demo-csrf` | 158 | 23160–23179 |
| `multi-worktree-local-dev` | 47 | 20940–20959 |
| `demo-cors` | 489 | 29780–29799 |

Worked example (`multi-worktree-local-dev`, slot 47): gateway 20940, items 20941, auth 20942, frontend 20943, items-db 20944, auth-db 20945, pgadmin 20946, redis 20947, kafka 20948, kafka-ui 20949.

**Explicit slot assignment:** `scripts/dev-env.sh --set-slot N` forces a specific slot (1–619) instead of hash-derived assignment. The bind check still runs; if the forced slot's ports are occupied, the command fails (no auto-bump). This gives users explicit control for Postman collection management — set a known slot and all port numbers become predictable.

**Slot stability, stated honestly:** the hash only picks the *initial* slot. A bind conflict at generation time bumps the slot, and the bumped value is persisted in the `.env` managed block (`DEV_ENV_SLOT`) — from then on the slot is re-derived from the block, not re-hashed. After any bump the slot depends on this machine's port state; it is NOT guaranteed identical on another machine.

### Risks (honest numbers — F7)

After the block scheme, the only remaining collision class is **same-slot**. Exact birthday probabilities over 619 slots (computed):
- 4 worktrees → **0.97%**
- **8 worktrees (on disk today) → 4.4%**
- 9 worktrees → **5.6%**
- 20 worktrees → **26.6%**

> **Superseded:** The race acceptance below records the original implementation.
> [simplify-worktree-creation-PLAN.md](./simplify-worktree-creation-PLAN.md)
> replaces it with registered claims and a clone-wide allocation lock. These
> three failure modes are no longer accepted behavior.

Originally accepted because detection + recovery existed:

1. **Same-slot collision** — caught at generation time by the bind check IF the twin stack is running; otherwise surfaces as a bind error at `docker compose up`. Recovery: `scripts/dev-env.sh --regenerate`.
2. **Bind-check blind spot** — the check only sees *currently listening* ports. Two worktrees generated at different times with the same slot while both were down → late bind error at `up` time. Accepted; recoverable via `--regenerate` (down-first, no orphans).
3. **TOCTOU race** — two agents generating `.env` concurrently in same-slot worktrees can both pass the bind check. Accepted (low probability, learning project); loser gets a bind error at `up` and runs `--regenerate`.

All three are loud failures with a one-command recovery — no silent data-plane mixups.

## Working with worktrees (day-to-day)

- **Current first-time setup (supersedes this plan's original flow):**
  `scripts/create-worktree.sh <path-or-name> -b <new-branch> [base-ref]` creates
  the worktree and its port claim as one operation.
- **Daily work — plain compose, no wrapper:** `docker compose up -d --build`, `docker compose down`, `docker compose logs -f items-service`, `docker compose ps`. The `.env` does all the work.
- **URL/port discovery:** `scripts/dev-env.sh` re-run prints the table (idempotent — reuses `DEV_ENV_SLOT`); or read the managed block in `.env`; or `docker compose ps`.
- **Collision at `up` (bind error):** `scripts/dev-env.sh --regenerate` → **first runs `docker compose down` using the OLD `.env`** (the old project name/ports are still on disk, so the old stack is reachable; add `--volumes` to also `down -v`), **then** bumps to the next free slot, rewrites the block, prints the new table. Never rewrite `.env` before the old project is down — that orphans its containers/volumes under the old project name (F3).
- **Teardown:** `docker compose down` (add `-v` to drop volumes) BEFORE `git worktree remove` — after removal the `.env` is gone and the project becomes hard to address.
- **Host-run dev mode** (service on host via `./mvnw spring-boot:run`, infra in Docker): `source <(scripts/dev-env.sh --exports)` first. `--exports` prints variables computed from the effective slot (block values if a managed block exists, else slot-0 defaults on main), so the same command works on main and worktrees. Exports include:

  ```
  # --- items-service / auth-service ---
  SPRING_DATASOURCE_URL=jdbc:postgresql://localhost:<ITEMS_DB_PORT>/items   # items
  SPRING_DATASOURCE_URL=jdbc:postgresql://localhost:<AUTH_DB_PORT>/auth     # auth
  SPRING_DATASOURCE_USERNAME=<service>
  SPRING_DATASOURCE_PASSWORD=<password>
  SPRING_DATA_REDIS_HOST=localhost
  SPRING_DATA_REDIS_PORT=<REDIS_PORT>
  SPRING_KAFKA_BOOTSTRAP_SERVERS=localhost:<KAFKA_HOST_PORT>
  SERVER_PORT=<ITEMS_PORT | AUTH_PORT | GATEWAY_PORT>   # per service

  # --- api-gateway (host-run) ---
  GATEWAY_AUTH_SERVICE_URL=http://localhost:<AUTH_PORT>
  GATEWAY_ITEMS_SERVICE_URL=http://localhost:<ITEMS_PORT>

  # --- frontend (host-run) ---
  VITE_API_URL=http://localhost:<GATEWAY_PORT>
  FRONTEND_PORT=<FRONTEND_PORT>
  ```

  **`VITE_API_URL` is not optional:** `frontend/src/services/api.ts:5` has `const API_BASE_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:10000'` — when unset it falls back to main's gateway (10000). Without `--exports`, a host-run worktree frontend would **silently hit main's gateway** — a silent cross-worktree data-plane mixup, worse than a bind error (F5). Host-run Vite also needs `npm run dev -- --port "$FRONTEND_PORT"` to avoid colliding with a containerized frontend on 5173.
- **E2E against a worktree:** `E2E_BASE_URL=http://localhost:<GATEWAY_PORT> ./mvnw clean test` in `e2e-tests/`. Currently `e2e-tests/src/test/java/com/onlineshop/e2e/BaseTest.java:12` hardcodes `protected static final String BASE_URL = "http://localhost:10000"` — must be changed to read `System.getenv().getOrDefault("E2E_BASE_URL", "http://localhost:10000")` (Task 3).

## Compose diff spec (exhaustive — every line-level change)

Against the rebased `docker-compose.yml` (baseline above). Nothing else changes: internal container ports, healthchecks, `depends_on`, networks, volumes, and all other env stay byte-identical.

1. **New line 1 (before `services:`):** `name: ${COMPOSE_PROJECT_NAME:-onlineshop}` — explicit project name; default `onlineshop` for main, `onlineshop-wt<slot>` per worktree from the managed block. (Follow with a blank line before `services:`.)
2. **items-postgres (line 5):** delete `container_name: items-postgres`; `"5432:5432"` → `"127.0.0.1:${ITEMS_DB_PORT:-5432}:5432"`.
3. **auth-postgres (line 25):** delete `container_name: auth-postgres`; `"5433:5432"` → `"127.0.0.1:${AUTH_DB_PORT:-5433}:5432"`.
4. **pgadmin (line 45):** delete `container_name: pgadmin`; `"5051:80"` → `"127.0.0.1:${PGADMIN_PORT:-5051}:80"`.
5. **redis (line 64):** delete `container_name: redis`; `"6379:6379"` → `"127.0.0.1:${REDIS_PORT:-6379}:6379"`.
6. **kafka (lines 80, 82–84, 93):** delete `container_name: kafka`; replace BOTH port mappings `"9092:9092"` and `"9093:9093"` with the single mapping `"127.0.0.1:${KAFKA_HOST_PORT:-29092}:29092"`; `KAFKA_ADVERTISED_LISTENERS` value → `PLAINTEXT://kafka:9092,PLAINTEXT_HOST://localhost:${KAFKA_HOST_PORT:-29092}`. `KAFKA_LISTENERS` stays as-is (binds `0.0.0.0` inside the container on all three ports — we stop publishing 9092/9093 to the host but the internal listeners are needed for in-cluster consumers). `KAFKA_CONTROLLER_QUORUM_VOTERS: 1@kafka:9093` stays (in-cluster).
7. **kafka-ui (line 121):** delete `container_name: kafka-ui`; `"8080:8080"` → `"127.0.0.1:${KAFKA_UI_PORT:-8080}:8080"`.
8. **auth-service (line 144):** delete `container_name: auth-service`; `"9001:9001"` → `"127.0.0.1:${AUTH_PORT:-9001}:9001"`.
9. **items-service (line 167):** delete `container_name: items-service`; `"9000:9000"` → `"127.0.0.1:${ITEMS_PORT:-9000}:9000"`.
10. **api-gateway (line 190):** delete `container_name: api-gateway`; `"10000:10000"` → `"127.0.0.1:${GATEWAY_PORT:-10000}:10000"`.
11. **frontend (line 212):** delete `container_name: frontend`; `"5173:5173"` → `"127.0.0.1:${FRONTEND_PORT:-5173}:5173"`; env `VITE_API_URL: http://localhost:10000` → `VITE_API_URL: http://localhost:${GATEWAY_PORT:-10000}`.

That is all 10 `container_name` removals, all 10 kept port parameterizations, 2 dropped Kafka mappings, 1 Kafka env change, 1 frontend env change, 1 new `name:` line.

## Script spec — `scripts/dev-env.sh` (new; `scripts/` does not exist yet)

Bash, `set -euo pipefail`, runnable from any directory inside the repo. No state outside the repo — the worktree's `.env` is the only state.

- **Root detection:** `root=$(git rev-parse --show-toplevel)`; hard-fail (exit 2, stderr) if not in a git repo. All file ops use `$root`.
- **Main-checkout detection (F10 — NOT basename-based):** `common=$(realpath "$(git -C "$root" rev-parse --git-common-dir)")`; main ⇔ `[ "$common" = "$root/.git" ]` (verified: main prints `.git` → resolves to `$root/.git`; a linked worktree prints the absolute path to the main checkout's `.git`, e.g. `/home/dpm/CodingProjects/OnlineShop-full-stack/.git`, which ≠ `$root/.git`). Cross-check with first entry of `git worktree list --porcelain` (its `worktree` line should equal `$root`); if the two disagree or git output is unexpected → fail loudly (exit 2), never guess. Main → print "main checkout: slot 0, defaults apply, no `.env` written" and exit 0. (Eliminates the "second clone literally named `OnlineShop-full-stack` silently gets slot 0" footgun.)
- **Slot selection:** if `.env` contains a managed block with `DEV_ENV_SLOT` → reuse it (idempotent re-runs; bumped slots persist). Else initial slot = the hash one-liner above.
- **Bind check:** a port counts as taken if `ss -tlnH "sport = :<port>"` shows a listener; fall back to bash `/dev/tcp` connect if `ss` is unavailable. Check all 10 candidate ports; on any hit → try next slot (wrap around 1–619), max 619 attempts, then fail loudly. Blind spot documented in Risks.
- **Managed block format (exact):**
  ```
  # >>> dev-env (managed by scripts/dev-env.sh — do not edit between the markers)
  COMPOSE_PROJECT_NAME=onlineshop-wt47
  DEV_ENV_SLOT=47
  GATEWAY_PORT=20940
  ITEMS_PORT=20941
  AUTH_PORT=20942
  FRONTEND_PORT=20943
  ITEMS_DB_PORT=20944
  AUTH_DB_PORT=20945
  PGADMIN_PORT=20946
  REDIS_PORT=20947
  KAFKA_HOST_PORT=20948
  KAFKA_UI_PORT=20949
  # <<< dev-env
  ```
  (ports shown are slot 47's; generate from `20000 + slot×20 + offset`.)
- **Secrets preservation (hard requirement):** if `.env` exists, replace ONLY the lines strictly between `# >>> dev-env` and `# <<< dev-env` (append the block if absent); every other byte of the file stays untouched. Mandatory test: fixture `.env` containing `POSTGRES_AWS_*` lines → run script → run script again → assert the secret lines are byte-identical and exactly one managed block exists.
- **Output:** human table of frontend URL, gateway, pgAdmin, kafka-ui, DB/Redis/Kafka ports.
- **`--regenerate` (F3, down-first — exact order):** (1) if a managed block exists, run `docker compose --project-directory "$root" down` using the CURRENT `.env` (old project/ports) — `--volumes` flag additionally passes `-v`; (2) compute next slot = bump from the OLD `DEV_ENV_SLOT` (not a re-hash), bind-check; (3) only then rewrite the managed block; (4) print new table.
- **`--exports`:** print to stdout the export variables listed in "Host-run dev mode" above, computed from the effective slot (block values if a managed block exists, else slot-0 defaults on main), so `source <(scripts/dev-env.sh --exports)` works on main and worktrees alike. Comments in the output must call out the `VITE_API_URL` wrong-gateway failure mode.
- **`--check` (F4 guardrail — the chosen mechanism):** main checkout → exit 0. Non-main worktree without a managed block in `.env` → print a loud stderr warning ("run `scripts/dev-env.sh` before `docker compose up` — a bare `up` here grabs main's canonical ports") and exit 1; with a block → exit 0. This is the mandated pre-up command for agent-driven workflows (documented in AGENTS.md + `docs/MULTI_WORKTREE.md`). Combined with: compose `name:` defaulting to `onlineshop` (explicit project), the generator being the only writer of port vars, and the docs mandate. Residual risk (human bypasses `--check` with a bare `up` in a fresh worktree) is documented, not eliminated.
- **`--set-slot N` (explicit slot — Postman-friendly):** force a specific slot (1–619) instead of hash-derived assignment. Bind check still runs; if the slot's ports are occupied, fail with a clear message (no auto-bump). This lets users pin known slot numbers to their Postman collections.
- **Also on every generation:** if a bind conflict forced a bump, print loudly that the slot is no longer the hash-derived one.

## Non-goals (tracked, not silently dropped)

- **Perf stack** (`tests/performance/docker-compose.perf.yml`): fixed `5433:5432` + `9001:9001` and `container_name: perf-auth-postgres` / `perf-auth-service` / `perf-k6` — collides with the main stack even today. Not fixed here; follow-up task below applies the same `${VAR:-default}` + no-`container_name` treatment.
- **Devcontainer** (`.devcontainer/docker-compose.yml:28`): host-global `container_name: onlineshop-workspace` — two devcontainers can't coexist. Not fixed here; follow-up task below.
- **Ephemeral ports for infra** (`"5432"` with no host side, discovered via `docker compose port`): reconsidered and rejected — only gateway+frontend need stable host ports in full-container mode, but stable ports keep pgAdmin/kafka-ui bookmarks, curl workflows, and host-run dev sane. Stable per-slot ports stay.
- No bespoke launcher CLI, no state outside the repo, no 4-stack verification (~16–20 GB RAM is unrealistic) — per the review's "What NOT to do".

## Tasks

### 0. Rebase onto the baseline (F8 — prerequisite)
- [x] Merged `build_and_release_first_iteration` (commit `27b41df`) into this worktree. Result: 10 `container_name` entries, frontend service present, `VITE_API_URL` fallback in api.ts, items build context `.` + `Items/Dockerfile`, items-postgres volume `/var/lib/postgresql`, items healthcheck `curl -f ... /actuator/health`.

### 1. `docker-compose.yml` parameterization (per Compose diff spec — exactly those edits)
- [x] Add `name: ${COMPOSE_PROJECT_NAME:-onlineshop}` as line 1 (blank line between it and `services:`)
- [x] Remove all 10 `container_name:` lines (items-postgres, auth-postgres, pgadmin, redis, kafka, kafka-ui, auth-service, items-service, api-gateway, frontend)
- [x] Replace the 10 kept port mappings with the `127.0.0.1:${VAR:-default}` forms listed; drop `"9092:9092"` and `"9093:9093"`
- [x] Kafka `KAFKA_ADVERTISED_LISTENERS` → `PLAINTEXT://kafka:9092,PLAINTEXT_HOST://localhost:${KAFKA_HOST_PORT:-29092}`
- [x] Frontend env `VITE_API_URL: http://localhost:10000` → `VITE_API_URL: http://localhost:${GATEWAY_PORT:-10000}`

### 2. `scripts/dev-env.sh` (per Script spec)
- [x] Root + main-checkout detection (`git-common-dir` realpath, cross-checked with `git worktree list --porcelain`; fail loudly on ambiguity)
- [x] Hash initial slot (mod 619); `DEV_ENV_SLOT` reuse; bind-check bump loop (max 619); `ss` with `/dev/tcp` fallback
- [x] Managed-block write/replace preserving all non-managed lines; secrets-preservation test with a `POSTGRES_AWS_*` fixture (run twice, assert byte-identical outside markers)
- [x] URL table output; loud notice when slot deviates from hash
- [x] `--regenerate` (down-first, then bump+rewrite; `--volumes` passthrough)
- [x] `--exports` (full list incl. `VITE_API_URL`, `SERVER_PORT` per-service values, `FRONTEND_PORT`, DB URLs, Redis, Kafka — comment the `VITE_API_URL` wrong-gateway failure mode)
- [x] `--check` (F4 guardrail, exit codes: 0 main or block-present, 1 no-block-in-worktree)
- [x] `--set-slot N` (explicit slot assignment for Postman collection management)

### 3. Host-run & e2e parity
- [x] `e2e-tests/src/test/java/com/onlineshop/e2e/BaseTest.java:12`: changed to `System.getenv().getOrDefault("E2E_BASE_URL", "http://localhost:10000")` — works as static field (Java 8+ Map.getOrDefault).
- [x] Documented `source <(scripts/dev-env.sh --exports)` + `npm run dev -- --port "$FRONTEND_PORT"` host-run flow in `docs/MULTI_WORKTREE.md`, incl. the silent-wrong-gateway failure mode.

### 4. Documentation sync (per AGENTS.md recursive update rule)
- [x] Root `AGENTS.md` — "Starting Services Locally": first action in a new worktree = `scripts/dev-env.sh --check`; `--check` as mandated agent pre-up; link `docs/MULTI_WORKTREE.md`
- [x] New `docs/MULTI_WORKTREE.md` — full guide: port table, recovery procedures, host-run dev, Postman setup, daily work
- [x] `frontend/AGENTS.md` — per-worktree `VITE_API_URL` behavior (containerized + host-run)
- [x] `Items/AGENTS.md` — run-dev section: `--exports` flow for multi-worktree host-run
- [x] `Auth/AGENTS.md` — same as Items: host-run with `--exports`
- [x] `api-gateway/AGENTS.md` — same as Items: host-run with `--exports`
- [x] `docs/DEBUG_INFO.md` — port-in-use troubleshooting, how to find your worktree's ports, `--regenerate` recovery, `--check` guard
- [x] `commands.txt` — replaced `docker run --name pgadmin --network ...` with `docker compose up -d pgadmin`
- [x] `Auth/queries.md:7` — replaced `docker logs auth-service` with `docker compose logs auth-service`

### 5. Verification (2 concurrent stacks max — realistic DoD)
- [x] Main regression: `docker compose config` renders project `onlineshop`, published ports exactly the legacy set plus `127.0.0.1` binding, no 9092/9093.
- [x] Forgot-to-generate guard: `--check` exits 1 without `.env`, exits 0 after `scripts/dev-env.sh`.
- [x] Two-stack concurrency: verified ports are disjoint and project names are distinct.
- [ ] Frontend B → gateway B: requires full stack up (RAM-intensive); deferred.
- [ ] Regenerate-orphan test: requires full stack up; deferred.
- [ ] e2e tests: requires full stack up; deferred.
- [x] Port math: no legacy overlaps, all 6190 unique (bash-verified).
- [x] Secrets preservation: POSTGRES_AWS_* lines preserved byte-for-byte across regenerations.
- [x] Compose config: parses cleanly, defaults produce legacy ports, env vars override correctly.

## Issues

- [x] ✅ Port collision between worktrees — fixed: per-slot 20-port block + all `container_name` removed + per-worktree `COMPOSE_PROJECT_NAME` + 619 slots (this plan).
- [x] ✅ Cross-service collision class — fixed by construction: fixed intra-block offsets in disjoint 20-port blocks; verified all 6190 generated ports unique.
- [x] ✅ Same-slot hash collision: was 45.3% @ 8 worktrees with 49 slots; now **4.4%** @ 8 worktrees with 619 slots (20-port blocks). — fixed: increased from 49→619 slots, tightened block size from 100→20.
- [x] ✅ Hash spec contradicted its own example (full-digest vs 8-hex truncation) — fixed: one pinned one-liner (first 8 hex chars), example table recalculated for 619-slot mod.
- [x] ✅ `--regenerate` orphaned containers/volumes — fixed: spec mandates `docker compose down` with the OLD `.env` BEFORE rewriting the block.
- [x] ✅ Forgot-to-generate worktree hijacks main's ports — mitigated: `name:` default + generator-only port vars + `--check` guard + docs mandate; residual human-bypass risk documented.
- [x] ✅ Host-run frontend silently hit main's gateway (`api.ts:5` fallback `?? 'http://localhost:10000'`) — fixed: `VITE_API_URL` (and `SERVER_PORT`/service ports) added to `--exports`; failure mode documented.
- [x] ✅ Kafka controller 9093 never parameterized (worktree #2 would fail to start) — fixed: 9093 not published at all; 9092 host mapping also dropped (advertised unresolvable `kafka:9092`); only `PLAINTEXT_HOST` 29092 is published, parameterized as `KAFKA_HOST_PORT`.
- [x] ✅ Plan was written against the wrong compose baseline (worktree `a941e9d`: 9 names, no frontend) — fixed: baseline pinned to main checkout's `build_and_release_first_iteration` branch (234 lines, 10 container_name); rebase is Task 0; CORS-wildcard dependency stated explicitly.
- [x] ✅ "Byte-identical for main" overclaim — corrected in Goal: ports/startup unchanged; container names become project-prefixed; loopback binding + Kafka mapping removal declared as the two intentional deltas.
- [x] ✅ Slot 0 keyed on exact basename (a second clone named `OnlineShop-full-stack` silently collided with main) — fixed: main detection via `git rev-parse --git-common-dir` realpath cross-checked with `git worktree list --porcelain`; fails loudly on ambiguity. Verified on 2026-08-02: main `realpath(.../.git)` = `/home/dpm/CodingProjects/OnlineShop-full-stack/.git`; worktree `realpath(.../.git)` = `/home/dpm/CodingProjects/OnlineShop-full-stack/.git` ≠ `$root/.git`.
- [x] ✅ Kafka `PLAINTEXT_HOST://localhost:29092` was never published — fixed: it is now the single published Kafka port (`${KAFKA_HOST_PORT:-29092}`).
- [x] ✅ Same-slot hash collision: was 45.3% @ 8 worktrees with 49 slots; now **4.4%** @ 8 worktrees with 619 slots (20-port blocks). — fixed: increased from 49→619 slots, tightened block size from 100→20.
- [x] ✅ Bind-check blind spot for stopped worktrees — superseded by
      [simplify-worktree-creation-PLAN.md](./simplify-worktree-creation-PLAN.md):
      registered `.env` claims are checked even when stacks are stopped.
- [x] ✅ Concurrent-generation TOCTOU race — superseded by the clone-wide
      `flock` + validate + atomic-write allocator.
- [x] ✅ Fresh worktrees missing `.env` — superseded by the single
      `scripts/create-worktree.py` creation command; creation and allocation are
      now one fail-closed operation.
- [x] ✅ `.env` is gitignored — the generator manages only the marked block and never clobbers existing secret lines (e.g., `POSTGRES_AWS_*`) — verified by fixture test.
- [ ] Perf stack collides with main even today (fixed 5433/9001 + `perf-*` names ×3) — open; follow-up Task 6.
- [ ] Devcontainer host-global name `onlineshop-workspace` — open; follow-up Task 6.
- [ ] CORS re-narrowing would silently break per-worktree frontends — open watch item; if gateway CORS is ever restricted again, add a Vite dev-proxy follow-up before merging that change.
