# Multi-Worktree Workflow — Step-by-Step Walkthrough

## Full trace: creating a new worktree and starting its stack

```
YOU: "Create a new worktree called 'feature/payment-integration'"

┌─────────────────────────────────────────────────────────────────┐
│ STEP 1: Agent creates the git worktree                           │
│                                                                 │
│   $ git worktree add ~/CodingProjects/demo-payment \            │
│            -b feature/payment-integration main                  │
│                                                                 │
│   Result:                                                        │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ ~/CodingProjects/demo-payment/                         │     │
│   │   ├── docker-compose.yml   ← SAME file as main checkout│     │
│   │   ├── Auth/                 ← but NO .env file yet!     │     │
│   │   ├── Items/                                           │     │
│   │   ├── api-gateway/                                     │     │
│   │   ├── frontend/                                        │     │
│   │   ├── scripts/dev-env.sh   ← ready to run              │     │
│   │   └── ...                                              │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   At this point, the worktree has NO .env.                       │
│   If you ran "docker compose up" right now:                     │
│     → project name = "onlineshop"                               │
│     → ports = 10000, 9000, 9001, 5432, ... (legacy)            │
│     → COLLISION with main checkout if it's running              │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ STEP 2: Agent reads AGENTS.md — sees mandatory guard            │
│                                                                 │
│   AGENTS.md says:                                                │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │ ## Starting Services Locally                             │   │
│   │                                                          │   │
│   │ ### First time in a worktree (required before `up`)      │   │
│   │                                                          │   │
│   │   scripts/dev-env.sh --check  # exits 1 if you forgot    │   │
│   │                                                          │   │
│   │ ### First-time setup (run once):                         │   │
│   │                                                          │   │
│   │   scripts/dev-env.sh           # Assigns ports, writes .env│  │
│   │                                                          │   │
│   └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ STEP 3: Agent runs --check (guard — exits 1)                    │
│                                                                 │
│   $ cd ~/CodingProjects/demo-payment                            │
│   $ scripts/dev-env.sh --check                                  │
│                                                                 │
│   What happens inside:                                           │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ is_main_checkout($ROOT)?                                │     │
│   │   → git rev-parse --git-common-dir                     │     │
│   │   → /home/dpm/.../OnlineShop-full-stack/.git           │     │
│   │   → /home/dpm/.../OnlineShop-full-stack/.git           │     │
│   │     ≠ $ROOT/.git (= demo-payment/.git)                 │     │
│   │   → NO, this is a worktree                             │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ Does .env exist with DEV_ENV_SLOT=?                     │     │
│   │   → .env does NOT exist at all                         │     │
│   │   → NO managed block                                   │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │   WARNING: This worktree has NO managed .env block.    │     │
│   │   Run 'scripts/dev-env.sh' before 'docker compose up'  │     │
│   │   — a bare 'up' here grabs main's canonical ports.     │     │
│   │                                                        │     │
│   │   exit 1                                               │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   Agent now knows: "I must run dev-env.sh before docker compose"│
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ STEP 4: Agent runs dev-env.sh (slot assignment)                  │
│                                                                 │
│   $ scripts/dev-env.sh                                          │
│                                                                 │
│   What happens inside:                                           │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ is_main_checkout → NO (worktree)                       │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ --set-slot given? → NO                                 │     │
│   │                                                        │     │
│   │ .env has DEV_ENV_SLOT? → NO (.env doesn't exist)       │     │
│   │                                                        │     │
│   │ → HASH-BASED INITIAL SLOT:                             │     │
│   │                                                        │     │
│   │   basename = "demo-payment"                            │     │
│   │   md5("demo-payment") = "a3f8c1d2..."                 │     │
│   │   first 8 hex = a3f8c1d2                              │     │
│   │   0xa3f8c1d2 = 2,748,858,834                          │     │
│   │   2,748,858,834 % 619 = 312                            │     │
│   │   312 + 1 = 313    ◄── SLOT                             │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ BIND-CHECK:                                             │     │
│   │                                                        │     │
│   │   Slot 313 ports:                                      │     │
│   │     20000 + 313 × 20 + 0 = 26260 (gateway)            │     │
│   │     20000 + 313 × 20 + 1 = 26261 (items)              │     │
│   │     ...                                                │     │
│   │     20000 + 313 × 20 + 9 = 26269 (kafka-ui)           │     │
│   │                                                        │     │
│   │   For each port → port_is_taken()                      │     │
│   │     1. ss -tlnH "sport = :26260" → nothing             │     │
│   │     2. /dev/tcp connect → refused                      │     │
│   │     → FREE ✓                                           │     │
│   │     ... all 10 ports free                              │     │
│   │                                                        │     │
│   │   No bump needed. Slot 313 confirmed.                  │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ EPHEMERAL CHECK:                                        │     │
│   │   /proc/sys/net/ipv4/ip_local_port_range = 32768 60999 │     │
│   │   max worktree port = 20000 + 619×20 + 9 = 32389      │     │
│   │   32389 < 32768 → OK, no overlap                       │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ write_managed_block → creates .env:                     │     │
│   │                                                        │     │
│   │   # >>> dev-env (managed by ...)                       │     │
│   │   COMPOSE_PROJECT_NAME=onlineshop-wt313                │     │
│   │   DEV_ENV_SLOT=313                                    │     │
│   │   GATEWAY_PORT=26260                                   │     │
│   │   ITEMS_PORT=26261                                     │     │
│   │   AUTH_PORT=26262                                      │     │
│   │   FRONTEND_PORT=26263                                  │     │
│   │   ITEMS_DB_PORT=26264                                  │     │
│   │   AUTH_DB_PORT=26265                                   │     │
│   │   PGADMIN_PORT=26266                                   │     │
│   │   REDIS_PORT=26267                                     │     │
│   │   KAFKA_HOST_PORT=26268                                │     │
│   │   KAFKA_UI_PORT=26269                                  │     │
│   │   # <<< dev-env                                        │     │
│   │                                                        │     │
│   │   Written atomically: .env.tmp.$$ → mv .env            │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ OUTPUT (stdout):                                        │     │
│   │                                                        │     │
│   │   ============================================         │     │
│   │     Worktree dev environment — slot 313                │     │
│   │   ============================================         │     │
│   │                                                        │     │
│   │     Frontend:   http://localhost:26263                 │     │
│   │     API Gateway: http://localhost:26260                │     │
│   │     pgAdmin:    http://localhost:26266                 │     │
│   │     Kafka UI:   http://localhost:26269                │     │
│   │     ...                                                │     │
│   │     Project: onlineshop-wt313                         │     │
│   │   ============================================         │     │
│   └───────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ STEP 5: Agent builds and starts the stack                        │
│                                                                 │
│   $ docker compose up -d --build                                │
│                                                                 │
│   What docker compose does:                                      │
│                                                                 │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ 1. Reads .env → COMPOSE_PROJECT_NAME=onlineshop-wt313 │     │
│   │                                                        │     │
│   │ 2. Compose file line 1:                                │     │
│   │    name: ${COMPOSE_PROJECT_NAME:-onlineshop}           │     │
│   │    → evaluates to: name: onlineshop-wt313             │     │
│   │                                                        │     │
│   │ 3. Port mappings from .env variables:                  │     │
│   │    "127.0.0.1:${ITEMS_DB_PORT:-5432}:5432"            │     │
│   │    → "127.0.0.1:26264:5432"                           │     │
│   │    "127.0.0.1:${GATEWAY_PORT:-10000}:10000"           │     │
│   │    → "127.0.0.1:26260:10000"                          │     │
│   │    ... all 10 services use slot 313 ports              │     │
│   │                                                        │     │
│   │ 4. Container names (no container_name directive):      │     │
│   │    onlineshop-wt313-items-postgres-1                  │     │
│   │    onlineshop-wt313-auth-postgres-1                   │     │
│   │    onlineshop-wt313-items-service-1                   │     │
│   │    ...                                                │     │
│   │                                                        │     │
│   │ 5. Volume names:                                       │     │
│   │    onlineshop-wt313_items-postgres-data               │     │
│   │    onlineshop-wt313_auth-postgres-data                │     │
│   │    ...  fully isolated from main checkout              │     │
│   │                                                        │     │
│   │ 6. Network:                                            │     │
│   │    onlineshop-wt313_onlineshop-network                 │     │
│   └───────────────────────────────────────────────────────┘     │
│                                                                 │
│   Frontend VITE_API_URL:                                         │
│   ┌───────────────────────────────────────────────────────┐     │
│   │ docker-compose.yml line 207:                            │     │
│   │   VITE_API_URL: http://localhost:${GATEWAY_PORT:-10000}│     │
│   │   → http://localhost:26260                             │     │
│   │                                                        │     │
│   │ Vite dev server reads this at startup.                 │     │
│   │ Frontend calls CORRECT worktree gateway,               │     │
│   │ NOT main's gateway at 10000.                           │     │
│   └───────────────────────────────────────────────────────┘     │
└─────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────┐
│ FINAL STATE: Two stacks running simultaneously                   │
│                                                                 │
│   MAIN CHECKOUT (slot 0)           WORKTREE (slot 313)           │
│   ┌─────────────────────────┐      ┌─────────────────────────┐  │
│   │ Project: onlineshop      │      │ Project: onlineshop-wt313│  │
│   ├─────────────────────────┤      ├─────────────────────────┤  │
│   │ gateway  : 10000         │      │ gateway  : 26260         │  │
│   │ items    : 9000          │      │ items    : 26261         │  │
│   │ auth     : 9001          │      │ auth     : 26262         │  │
│   │ frontend : 5173          │      │ frontend : 26263         │  │
│   │ items-db : 5432          │      │ items-db : 26264         │  │
│   │ auth-db  : 5433          │      │ auth-db  : 26265         │  │
│   │ pgadmin  : 5051          │      │ pgadmin  : 26266         │  │
│   │ redis    : 6379          │      │ redis    : 26267         │  │
│   │ kafka    : 29092         │      │ kafka    : 26268         │  │
│   │ kafka-ui : 8080          │      │ kafka-ui : 26269         │  │
│   ├─────────────────────────┤      ├─────────────────────────┤  │
│   │ Volumes: onlineshop_*    │      │ Volumes: onlineshop-wt313_*│
│   │ Network: ...default      │      │ Network: ...wt313_...     │  │
│   └─────────────────────────┘      └─────────────────────────┘  │
│                                                                 │
│   ◄── Zero port collisions. Zero container name collisions.      │
│        Zero network/volume collisions.                           │
└─────────────────────────────────────────────────────────────────┘
```

## What if the agent forgets dev-env.sh?

```
Agent runs:     $ docker compose up -d --build
                (without running dev-env.sh first)

What happens:
   .env does NOT exist
   → COMPOSE_PROJECT_NAME not set → defaults to "onlineshop"
   → all ports use ${VAR:-default} fallbacks:
       10000, 9000, 9001, 5432, 5433, 5051, 6379, 29092, 8080, 5173

   → COLLISION! Two stacks compete for the same ports.
   → docker compose up FAILS with "port is already allocated"

   Recovery:
     $ scripts/dev-env.sh             # generates .env
     $ docker compose up -d --build   # now works on worktree ports
```

## What if two worktrees hash to the same slot?

```
Worktree A runs dev-env.sh → gets slot 313, .env written, stack up.
Worktree B (different basename) also hashes to 313 → runs dev-env.sh:

   ┌───────────────────────────────────────────────────────┐
   │ hash basename → slot 313                               │
   │                                                        │
   │ any_port_taken(313)?                                    │
   │   ss sees port 26260 LISTENING (worktree A is up)      │
   │   → YES, taken!                                        │
   │                                                        │
   │ BUMP: 313 → 314                                        │
   │   any_port_taken(314)? → free ✓                        │
   │                                                        │
   │   "Slot bumped from hash-derived 313 to 314            │
   │    due to port conflicts"                               │
   │                                                        │
   │   Writes slot 314 to .env.                             │
   └───────────────────────────────────────────────────────┘

   Worktree B now uses 26280-26289 — no collision.
```

## Postman collection management

```
For worktree on slot 313:
   Set one Postman variable:
     gatewayUrl = http://localhost:26260

   Derive everything else:
     items  → {{gatewayUrl}}  (replace port 26260→26261)
     auth   → {{gatewayUrl}}  (replace port → 26262)
     ...

   Or use the sequential property directly:
     basePort = 26260
     gateway  = {{basePort}}+0  = 26260
     items    = {{basePort}}+1  = 26261
     auth     = {{basePort}}+2  = 26262

   For MAIN checkout (slot 0): offsets DON'T apply.
   Set each port individually — legacy ports aren't sequential.
```

## Day-to-day after setup

```bash
# Starting work (any day):
$ cd ~/CodingProjects/demo-payment
$ scripts/dev-env.sh --check   # exits 0, you're good
$ docker compose up -d --build   # starts on slot 313 ports
$ docker compose logs -f items-service
$ docker compose down

# Running a service on the host during development:
$ source <(scripts/dev-env.sh --exports)
$ cd Items && ./mvnw spring-boot:run   # auto-connects to correct DB/Kafka

# Running e2e tests against this worktree:
$ E2E_BASE_URL=http://localhost:26260 ./mvnw clean test -f e2e-tests/pom.xml

# Teardown:
$ docker compose down -v              # before removing the worktree
$ git worktree remove ~/CodingProjects/demo-payment
```
