# AGENTS.md - Project Guide for agents

## Project Overview

Microservices-based e-commerce learning platform


## AWS CLI Commands — MANDATORY

**ALL AWS CLI commands MUST include `--profile dpm-profile --region eu-north-1`. NO exceptions — even for seemingly harmless commands like `sts get-caller-identity`.**

```bash
# Always verify identity before any AWS work:
aws sts get-caller-identity --profile dpm-profile --region eu-north-1
```

- The profile uses IAM user credentials (not SSO). Without `--profile dpm-profile`, commands fail with "session expired" or unauthorized errors.
- If commands fail with `Your session has expired`, tell the user to re-authenticate. Do NOT retry.
- Every `create`/`put`/`delete` MUST be followed by a `describe`/`get`/`list` to confirm the change took effect.

## Your role

You are staff engineer with a lot of experience and always propose modern architectural and technological approaches. When there are multiple solutions which are all great, you explain them and ask which one should be used.

## Core values
- Always be super skeptical of my ideas — be devil's advocate. Always ask why, how, and whether there is a better way. Always propose alternatives. Be ruthless and direct, not a yes man.
- Keep secrets and sensitive information safe. Never expose them in code, logs, documentation, or interactive sessions. If you need to use a secret internally, do so; but when showing anything to me that involves it, always substitute a placeholder (e.g., `<db-admin-password>`).
- Before you act, think first! Check what the user asked for — answer if it is only a question, act if it is a command. If you are not sure, ask for clarification. Always ask for clarification if the request is ambiguous or unclear.


## Documentation Maintenance

**When making ANY changes to the project, treat each microservice as a separate module.
On each change you MUST update the files related to the respective microservice, so they are ABSOLUTELY independent and only know about each other on an architectural level.**

1. This file (`AGENTS.md`) if the change affects project-wide documentation
2. All referenced documentation files affected by the change (see sections below)
3. All files referenced by those files (recursive update through the entire reference chain)
4. All service-level `AGENTS.md` files in each microservice directory (e.g., `Auth/AGENTS.md`, `Items/AGENTS.md`, etc.)

**Documentation must always stay in sync. Propagate updates through the entire documentation tree.**

---

## Maven usage
When using Maven commands you MUST use the Maven wrapper (`./mvnw`) inside the service's folder you are working on — never from a parent or sibling directory. Always run from the target service's root folder (e.g., `Items/`, `Auth/`).

## Before Committing
**ALWAYS run tests first** — see [docs/TESTING_STRATEGY.md](./docs/TESTING_STRATEGY.md) for which tests to run. Never commit without passing tests.
1. Run unit + integration tests for the affected service from its directory: `./mvnw clean test`
2. If available, also run E2E tests from `e2e-tests/`: `./mvnw clean test`
3. Only commit if ALL tests pass.

## Quick Reference

### Project Identity

| Property | Value |
|----------|-------|
| AWS Account ID | `799111666795` |
| AWS Region | `eu-north-1` (Stockholm) |
| OIDC Role | `arn:aws:iam::799111666795:role/github-actions-onlineshop` |
| ECR Registry | `799111666795.dkr.ecr.eu-north-1.amazonaws.com` |
| ECR Naming | `onlineshop-<service>` (auth, items, api-gateway) |

### Services & Ports

| Service | Port | Java Version | Maven Wrapper | Depends On |
|---------|------|-------------|---------------|------------|
| Auth | 9001 | 25 | Yes | — |
| Items | 9000 | 25 | Yes | common |
| API Gateway | 10000 | 25 | Yes | — |
| Common | — | 25 | Yes | — |
| Frontend | 5173 | — | No | — |
| E2E Tests | — | — | Yes | — |

## Starting Services Locally

### Create a worktree

```bash
scripts/create-worktree.py <path-or-name> -b <new-branch> [base-ref]
```

This is the only supported creation path. In order, it validates the request,
creates the branch and worktree directory, verifies that the selected base uses
the worktree Compose variables, atomically writes a managed `.env` block with
the Compose project, slot, and ten unique host ports, then prints the start
command. The allocator checks the slot's complete 20-port block, so ten
additional offsets remain reserved for future services. It does not start
containers or create volumes. Do not create development worktrees with a bare
`git worktree add`; that bypasses port allocation.

### Multi-worktree guide

See [docs/MULTI_WORKTREE.md](./docs/MULTI_WORKTREE.md) for port isolation,
failure recovery, and teardown.

### Build and start

```bash
# Build every application image from the current source and start the full stack.
docker compose up -d --build
```

`docker build` builds one image only. Use the Compose command above for the complete local stack: it builds Auth, Items, API Gateway, and frontend, then starts those containers plus the database and infrastructure containers.

All services, databases, Redis, Kafka, and the frontend are defined in `docker-compose.yml`. The Auth, Items, and API Gateway Dockerfiles compile their applications in a Maven build stage; the frontend image installs its dependencies and copies the current source. The frontend auto-connects to the API gateway via `VITE_API_URL=http://localhost:<GATEWAY_PORT>` (set by Compose `environment:`).

`--build` uses Docker's cache for unchanged layers and rebuilds layers affected by source changes. It is not a test command; run the service Maven or npm tests separately when needed. For a simple stop/restart without changes, use `docker compose down` / `docker compose up -d`.

> **Build contexts:** Items uses the repository root as its context because it builds the `common` library first. Auth and API Gateway use their service directories. `common` is a library, not a separate deployable Compose service.

## Script Development

All new and changed repository automation must follow
[docs/SCRIPT_GUIDELINES.md](./docs/SCRIPT_GUIDELINES.md). Scripts must expose a
short top-down flow, use plain domain names, and avoid indirection or legacy
compatibility without a current requirement. Treat growing size and shell
complexity as signals to simplify the design or choose a more readable
language, not as reasons to add layers.

## Dockerfile Conventions

1. **Self-contained application builds** — Java service Dockerfiles use multi-stage builds and run Maven inside Docker, eliminating a host-side `./mvnw package` prerequisite. Use the repository root as the context when a service depends on another project (e.g., Items → common).
2. **Cache mounts** — Always use `--mount=type=cache,target=/root/.m2,id=maven-repo` on RUN lines that invoke Maven. Use an explicit `id=` so mounts are shared across RUN steps.
3. **Base image tags** — Pin to a specific Alpine version (e.g., `eclipse-temurin:25.0.1_8-jre-alpine-3.23`), not a floating tag.
4. **COPY granularity** — When a RUN step processes an entire directory tree, use `COPY dir/ dir/` (directory-level) not file-level COPY. File-level COPY is only justified when it creates a distinct layer that can be cached independently of sibling RUN steps. If all files feed a single RUN, use the simplest COPY possible.
5. **Healthchecks** — Use `curl -f <actuator-endpoint> || exit 1`, not raw `curl` and not business endpoints.
6. **`hadolint`** — Run `hadolint` on any changed Dockerfile before committing. Configuration is in `.hadolint.yaml`.

## CI/CD & AWS Infrastructure

See [docs/CI_CD_GOTCHAS.md](./docs/CI_CD_GOTCHAS.md) for the full pitfall checklist. Always read that file before working on CI/CD or AWS infra.

### Release contract (Pass 3, subphase 3.1)

The versioned release manifest contract, its deterministic local validator,
valid/invalid fixtures, strict dispatch-input helpers, and tests live in
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/` (see its `README.md`). Every later
release subphase (candidate evidence, promotion, rollback, traceability,
retention) consumes this contract. Always validate candidate/official manifests
with the bundled validator — never parse security-sensitive JSON with regex or
ad-hoc shell string concatenation — and run the gate:

```bash
bash tests/scripts/release_contract_test.sh
```

### Candidate build evidence (Pass 3, subphase 3.2)

The successful `main` push workflow emits one candidate evidence bundle (run
id/attempt, full SHA, actor, ECR digests, frontend checksum, SBOMs, staging
validation) after Auth, Items, API Gateway, frontend, and the cloud staging E2E
job all pass. Reusable scripts and fixtures live in the same `release/`
directory. The offline gate (fixtures, workflow static checks, reproducible
frontend packaging, publish/reuse/fail-closed decisions, artifact identity/
digest recording):

```bash
bash tests/scripts/candidate_evidence_test.sh
```

Live ECR/GitHub/Syft evidence is verified in the consolidated Pass 3
verification pass, not by the offline gate.

### ECR release tagging, immutability, and least privilege (Pass 3, subphase 3.3)

The three backend ECR repositories are defined with desired state
`IMMUTABLE_WITH_EXCLUSION` (exclusion filters exactly `main-latest` and
`branch-*`, see `release/ecr/immutable-repositories.json`) so `sha-*` and
`release-*` tags can never be overwritten and `latest` stays absent. Release
tags are minted server-side from the recorded candidate bytes
(`ecr:batch-get-image` + `ecr:put-image`, never pull/rebuild) by
`promote-image-digest.sh`, guarded by the `release_contract.ecr`
mint/reuse/fail-closed decision and the `check-release-identity.sh` +
`release_contract.releaseid` collision/resume preflight. GitHub OIDC access is
planned to be split by job purpose (`github-actions-role-layout.md`:
candidate-build, promotion, production-deploy, rollback; validation jobs have
no AWS access). The per-purpose roles and the immutable-repository mutation are
**not applied live yet** — the workflow still assumes the single
`github-actions-onlineshop` role, and the split + repository read-back are
applied in the consolidated Pass 3 verification pass. The offline gate:

```bash
bash tests/scripts/ecr_release_tagging_test.sh
```

Live ECR settings read-back, real put-image behavior, the real OIDC
environment subject, and the IAM Access Analyzer run are verified in the
consolidated Pass 3 verification pass, not by the offline gate.

### Controlled staging-to-production promotion (Pass 3, subphase 3.4)

The approved, approval-gated promotion of one verified candidate snapshot from
staging to production lives in `.github/workflows/promote-release.yml` (manual
dispatch with `version` + `run_id`; a read-only `preflight` job validates the
dispatch inputs and the candidate manifest contract before the protected
`production` Environment; the approved `promote` job runs the full preflight
after approval/lock with a fresh snapshot and never rebuilds; `approvedBy` is
derived from the environment-approval evidence via
`actions/runs/{run}/approvals`, never `github.actor`; the candidate evidence
artifact is consumed from the exact producing attempt, never the latest;
`compensate` restores the pre-promotion snapshot on failure (automatic, not
approval-gated), including the frontend live root from the previous immutable
prefix) and
`release/bin/promotion-preflight.sh`/`snapshot-production.sh`/
`deploy-production.sh`/`verify-production.sh`/`publish-frontend.sh`/
`finalize-release.sh`/`compensate-production.sh` with the fixture-tested
`release_contract.promotion` decision layer (dispatch, run evidence, ancestry,
preflight, snapshot, plan, waiter, frontend publication, verification,
finalization, compensation). The offline gate:

```bash
bash tests/scripts/promotion_test.sh
```

The live owner-approved promotion, the real `production` Environment approval
and required-reviewer check, real ECR/ECS/S3/CloudFront mutations and read-backs,
and the real GitHub Release publication are verified in the consolidated Pass 3
verification pass, not by the offline gate.

### Production hardening (Pass 3, subphase 3.5)

The existing isolated production environment is hardened rather than replaced.
Read-only inventory and consistency tooling plus mutation tools with mandatory
verification live in `scripts/` (inventory, production/staging separation,
frontend S3 REST + CloudFront OAC, CloudTrail coverage), the decision logic and
offline tests in `release/`, and the explicit non-secret identifiers in
`scripts/config/{production,staging}.env`. The offline gate:

```bash
bash tests/scripts/production_hardening_test.sh
```

- **Task definitions/services:** `release/bin/validate-task-definition.sh`
  enforces digest-pinned `@sha256:` images, the Fargate CPU/memory matrix,
  `awsvpc`, named Service Connect port mappings, `awslogs`, health checks,
  positive `stopTimeout`, `versionConsistency=enabled`, distinct
  execution-role/task-role duties, full-ARN `secrets[].valueFrom`, and
  circuit-breaker/safe-rolling service parameters
  (`minimumHealthyPercent=100`, `maximumPercent=200`, rollback enabled).
- **Sanitized transforms:** `release/bin/sanitize-task-definition.sh` replaces
  only the intended container image and proves the diff is image-only and that
  secrets never leave `secrets[].valueFrom`.
- **Inventory/separation:** `scripts/inventory-production.sh` and
  `scripts/verify-production-staging-separation.sh` are read-only and compare
  the explicit non-secret configs against live state (identity + VPC/Cloud Map
  topology), failing closed on any drift or shared resource. A genuinely
  absent resource is reported `missing`; an AWS read that fails is reported
  `error` (never disguised as drift or silence). Execution role and ECR
  repositories are shared infrastructure and are not separation violations.
- **Frontend OAC:** `scripts/verify-frontend-oac.sh` (read-only) and
  `scripts/migrate-frontend-oac.sh` (`--dry-run`/`--apply` with per-step
  read-back) move the frontend to an S3 REST origin behind CloudFront Origin
  Access Control and block direct public bucket access. **Not applied live in
  3.5** — application is deferred to the consolidated verification pass.
- **CloudTrail:** `scripts/verify-cloudtrail-coverage.sh` audits management-
  event coverage (multi-region, logging, delivery) for ECS/ECR/S3/CloudFront/
  IAM/Secrets Manager.
- **Lifecycle guards:** the staging-only DB helpers fail fast
  (`lc_require_environment staging || return 1`); production entry points never
  reach clean-staging database create/bootstrap/delete paths.
- **Backup/migration limitation:** no schema-changing production release until
  Flyway (or equivalent) + forward/backward-compatible rules + a tested
  backup/restore procedure exist. See
  `plans/AUTOMATIC-BUILDS-AND-DEPLOY/explanations/PRODUCTION-HARDENING-DECISIONS.md`.

Live hardening read-back (real inventory, real OAC migration, real CloudTrail,
real service/TD verification, security-group/IAM tightening) is deferred to
the consolidated Pass 3 verification pass and is not claimed by the offline
gate.

### Owner-approved rollback (Pass 3, subphase 3.6)

Approval-gated application rollback of production to an existing immutable
official release lives in `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/`
(`.github/workflows/rollback-release.yml` +
`release/bin/rollback-preflight.sh`, `deploy-rollback.sh`,
`restore-frontend.sh`, `verify-rollback.sh`, `record-rollback-result.sh` +
the fixture-tested `release_contract.rollback` decision layer, fixtures under
`release/fixtures/rollback/`). The offline gate:

```bash
bash tests/scripts/rollback_test.sh
```

- **Target selection:** `workflow_dispatch` takes only the `version` of an
  existing official release (never tags/digests/SHAs). The target must be one
  of the latest 10 **complete** official sets — every backend ECR
  `release-<version>` tag resolves to the exact manifest digest AND the
  immutable frontend prefix marker exists and matches — and must not be the
  currently running release; draft/tampered/partial/metadata-only releases and
  any missing or mismatched artifact fail closed.
- **Control plane:** the `rollback` job is behind the same protected
  `production` Environment and shared non-cancelling `production-mutation`
  concurrency group as promotion. A read-only `preflight` job (with job-scoped
  `id-token: write` for its read-only ECR/S3 scope) runs BEFORE approval; the
  `rollback` job re-runs the full preflight after approval/lock against a
  fresh snapshot and fails closed when the revalidated target manifest differs
  byte-for-byte from the approved one. `approvedBy` is derived from
  `actions/runs/{run}/approvals`, never `github.actor`.
- **Mutation:** digest-pinned task-definition revisions are registered via
  `sanitize-task-definition.sh` + `validate-task-definition.sh` (image-only
  diff, secrets stay in `secrets[].valueFrom`), services deploy in canonical
  order with circuit breaker and per-deployment waiters, and the frontend is
  restored from the retained immutable `_releases/v<version>/` prefix (no
  `--delete`, marker/index last, invalidation). No ECR tag is minted or moved
  and no official release is created.
- **Failure path:** the automatic (non-approval-gated) `compensate` job
  restores the changed components (including frontend) from this run's
  pre-rollback snapshot via the shared `compensate-production.sh` (which
  accepts the literal JSON `--changed` array the workflows pass); a typo'd
  component key fails closed. The database is never reversed.
- **Audit record:** `record-rollback-result.sh` writes the rollback result
  (requester, approver — both mandatory, never defaulted to the run actor —
  from/to releases with exact digests/checksum, run id, workflow URL,
  timestamps, outcome) as a separate artifact; the immutable original release
  manifest is never edited. Write/resume idempotency with `RESULT_CONFLICT`
  fail-closed.

The live half of the gate — the real owner-approved rollback (release N →
N-1 → N), the real `production` Environment approval, real ECR/ECS/S3/
CloudFront mutations and read-backs, real frontend restoration, and the real
rollback-result artifact — is deferred to the consolidated Pass 3
verification pass and is not claimed by the offline gate.

### Release traceability queries (Pass 3, subphase 3.7)

Read-only operator queries answered in both directions live in
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/` (`release/bin/trace.sh` +
the fixture-tested `release_contract.traceability` decision layer, index/
observed-state fixtures under `release/fixtures/traceability/`):

- `trace.sh commit --sha <full-sha>` → candidate run, ECR digests, official releases;
- `trace.sh release --version <semver>` → source SHA, components, evidence,
  SBOMs, artifacts (+ ECR release-tag and immutable per-release prefix-marker
  cross-checks);
- `trace.sh running` → task-definition ARNs, **running** digests from
  `tasks[].containers[].imageDigest` (never only the task-definition URI),
  release identity + approver, frontend identity from the deployed immutable
  `release.json` marker; paused production is reported honestly with selected
  task-definition digests and last verified deployment evidence (never a
  fabricated running digest); a mixed or incomplete running digest set fails
  closed;
- `trace.sh digest --digest sha256:<hex>` → ECR tags, OCI revision (attributed
  to the release manifest, never claimed as an observed label read), candidate
  run, release identity;
- `trace.sh audit [--version <semver>]` → manifest ↔ ECR ↔ ECS running digest ↔
  frontend checksum consistency audit (read-only, reports drift; newest-first
  by numeric version, independent of index order).

Output is machine-readable JSON (exit 0 only when found AND consistent;
`NOT_FOUND`/`AMBIGUOUS_*`/`*_MISMATCH`/`OBSERVED_READ_ERROR` exit 1;
usage errors exit 2); `--human` adds a concise view. Live AWS reads require
the mandatory identity preflight and non-overridable `--profile dpm-profile
--region eu-north-1`. A configured production service omitted by
`describe-services`, or a malformed frontend marker, is a read `error` that
fails closed as `OBSERVED_READ_ERROR` — never silent drift. The offline gate:

```bash
bash tests/scripts/release_traceability_test.sh
```

Live lookups against real AWS/GitHub (the read-only live smoke test) are
deferred to the consolidated Pass 3 verification pass and are not claimed by
the offline gate.

### Retention and rollback-window enforcement (Pass 3, subphase 3.8)

ECR/S3/GitHub retention keeping the immediate 10-release rollback window lives
in `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/`
(`release/ecr/lifecycle-policy.json` desired state +
`release/bin/audit-retention-window.sh` (read-only),
`preview-retention-policy.sh`, `apply-retention-policy.sh` +
the fixture-tested `release_contract.retention` decision layer, fixtures under
`release/fixtures/retention/`, decision notes in
`explanations/RETENTION-DECISIONS.md`). The offline gate:

```bash
bash tests/scripts/retention_test.sh
```

- **Desired policy:** rule 1 keeps the newest 10 `release-*` images with the
  HIGHEST priority; rules 2–4 expire the enumerated candidate families
  (`sha-`; `main-latest`; `branch-`) after 30 days — each as its own
  single-prefix rule, because AWS documents that a multi-entry
  `tagPrefixList` selects only images carrying ALL the listed tags ("only the
  images with all specified tags are selected") and a merged list would
  silently select nothing (the validator rejects merged lists with
  `POLICY_TAGPREFIX_MULTI`); rule 5 expires untagged
  images after a 14-day grace period. ECR's first-match-wins semantics are
  modeled — an image is expired by exactly one or zero rules, and a retained
  multi-tag release image (claimed by rule 1) can never be selected by a
  lower-priority rule. ECR's schema requires an explicit `tagPrefixList` on
  every `tagged` rule, so a generic negative/exclusion rule ("expire
  everything except releases") is not expressible and is never used.
- **Preview before apply:** `preview-retention-policy.sh` lists the exact
  candidate image IDs/tags — offline via the modeled evaluation, or live via
  ECR's read-only `start/get-lifecycle-policy-preview` dry-run — and any
  disagreement with the model or a protected digest expiring fails closed
  (`PREVIEW_DISAGREEMENT`/`PROTECTED_IMAGE_EXPIRING`).
- **Apply is refused offline:** `apply-retention-policy.sh --apply` requires
  `ONLINESHOP_RETENTION_LIVE_APPLY=1` (set only by the consolidated Pass 3
  live pass); every `put-lifecycle-policy` is immediately followed by a
  `get-lifecycle-policy` read-back compared byte-for-byte (fail-closed drift).
  The offline gates never run the apply path.
- **Read-only retention audit:** `audit-retention-window.sh` lists the exact
  10 (or all when fewer exist) immediately rollback-capable releases, reusing
  the 3.6 complete-set model; a missing/mismatched artifact fails closed
  (`RETENTION_ARTIFACT_MISSING`/`RETENTION_ARTIFACT_MISMATCH`), older
  metadata-only releases are never claimed rollback-capable, and a
  push-order/version-order keep-10 gap (backport) fails closed
  (`POLICY_WINDOW_GAP`).
- **Retention classes:** GitHub Releases/manifests/SBOMs/checksums/audit
  evidence are indefinite; candidate-only artifacts 30 days; staging-failure
  diagnostics and snapshot/result records 14 days (the gate statically checks
  the workflow `retention-days` values). Frontend `_releases/v<version>/`
  prefixes are retained for the newest-10 window and never deleted for the
  currently deployed or previous known-good release; GitHub Release assets
  remain the long-term source.
- ECR lifecycle evaluation is delayed (up to 24 hours) and images referenced
  by manifest lists/referrers are not selected — documented in
  `explanations/RETENTION-DECISIONS.md`.

The live half of the gate — the real lifecycle policy preview/apply/read-back
against real ECR (from the consolidated live pass only), the read-only live
retention audit against real production state, and real S3/frontend retention —
is deferred to the consolidated Pass 3 verification pass and is not claimed by
the offline gate.

### Before any AWS work
- Always run `aws sts get-caller-identity --profile dpm-profile --region eu-north-1` first in any new terminal session
- Always pass `--profile dpm-profile --region eu-north-1` explicitly on every command; AWS resources are region-scoped and invisible across regions
- Every `create`/`put`/`delete` MUST be followed by a `describe`/`get`/`list` to confirm the change took effect

### GitHub Actions development rules
1. **Version check always:** Before setting `java-version` in `setup-java`, cross-check `<java.version>` in `pom.xml` AND the `FROM` line in `Dockerfile`. All three must agree.
2. **Workflow dispatch testability:** `workflow_dispatch` workflows are ONLY indexed by GitHub from the default branch (`main`). During development on a feature branch, temporarily add a `push` trigger. Remove it before merging.
3. **Event context guard:** `github.event.inputs` is `null` on `push` events — it only exists for `workflow_dispatch`. Always check `github.event_name == 'workflow_dispatch'` before accessing `.inputs`.
4. **BuildKit requirement:** Any `docker/build-push-action` using `cache-from`/`cache-to` (type=gha) MUST be preceded by `docker/setup-buildx-action@v3`. The default runner Docker driver does not support cache export.
5. **Post-mutation verify:** Every AWS `create`/`put`/`delete` must be followed by a `describe`/`get`/`list` to confirm it took effect.
6. **OIDC trust subjects:** The GitHub Actions role must trust the configured subject `repo:Djimi@8793507/OnlineShop-full-stack@1097550215`, scoped to the `main` and `feature/*` branch refs. Do not reuse a stale repository subject.

### AWS operational rules (added 2026-08-02 after Pass 2 session review)
1. **No blocking poll loops** in a single bash call (a 10-min `sleep` loop hit the hard shell timeout and lost everything). Use `aws ecs wait services-stable`, or loops bounded to <2 min, then re-invoke.
2. **Secrets never enter ECS task definitions in plaintext** — no passwords in `environment` or `command`; always `secrets[].valueFrom` (with FULL secret ARN when using the `:json-key::` suffix). One-off helper TD revisions: deregister AND `delete-task-definitions` after use (deregister alone leaves them readable as INACTIVE).
3. **Private RDS:** never connect from localhost (it hangs; RDS has no public route). Use `scripts/ecs-run-sql.sh` — see [docs/CI_CD_GOTCHAS.md](./docs/CI_CD_GOTCHAS.md) → "Private RDS Access". Every SQL mutation needs a read-back `--verify` in the same run — exit 0 is not proof.

### Windows PowerShell → AWS JSON
PowerShell's default UTF-8-with-BOM encoding confuses AWS IAM. When creating JSON files for AWS:
```powershell
# DON'T use @'...'@ here-strings — they add a BOM
# DO use explicit ASCII encoding
[System.IO.File]::WriteAllText("path.json", $jsonString, [System.Text.Encoding]::ASCII)
```

## Playground Start/Stop

When not actively developing, pause the AWS playground to save ~$38/month:

```bash
# Stop the playground (scale ECS to 0 + delete ALB) — reduces to ~$1.25/month
bash scripts/pause-playground.sh

# Start the playground (recreate ALB + scale ECS to 1) — typically ~3-8 min
bash scripts/resume-playground.sh
```

Production and staging are independent environments. Staging has its own VPC,
ECS cluster, RDS instance, security groups, Cloud Map namespace, and ALB:

```bash
# Start/stop isolated staging (defaults to Fargate Spot when running)
bash scripts/resume-staging.sh
bash scripts/pause-staging.sh
```

`resume-staging.sh` creates a new empty RDS instance, applies the
version-controlled Auth/Items schemas and deterministic seeds through the ECS
SQL runner, verifies restricted application access, and only then starts ECS.
`pause-staging.sh` deletes staging RDS without a snapshot by default. Retention
is an explicit debugging/DR exception via `--retain-snapshot
onlineshop-staging-debug-<reason>`.

Lifecycle scripts emit UTC timestamped, numbered progress logs. Each step shows
an experience-based typical duration and completion reports actual total time.
Typical end-to-end ranges are: production resume 3–8 minutes, production pause
1–2 minutes, clean staging resume 10–20 minutes, and staging pause 5–12 minutes
without a snapshot (10–20 minutes when retaining one). AWS capacity and image
pulls can make individual runs slower; the estimates are guidance, not timeout
contracts.

**Cost summary:**

| State | Monthly Cost |
|---|---|
| Running (Spot 24/7) | ~$49.00 |
| Running (Spot 8hr/day + ALB paused) | ~$17-18 |
| Paused | ~$1.25 |

The four entry points are thin wrappers over `scripts/lib/lifecycle.sh`; explicit
non-secret identifiers live in `scripts/config/{production,staging}.env`. See
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/explanations/COST-EXPLANATION.md` for the
cost breakdown.

## Maven Build Dependencies & Parallel Builds

### Dependency Graph
Auth - no dependencies on other projects
api-gateway - no dependencies on other projects
Items - depends on `common` (uses shared models/utilities)
common - no dependencies on other projects
e2e-tests - not part of the build graph (contains only tests, build separately when needed)
frontend - not part of the build graph (separate React app, build separately when needed)

### Parallel Build Strategy for agents

When asked to build multiple projects, **analyze the dependency graph above** and run builds in parallel whenever possible to save time.

## Git Workflow

See [docs/GIT_WORKFLOW.md](./docs/GIT_WORKFLOW.md) for:
- Branch naming conventions
- Commit message format (Conventional Commits)
- Pull request guidelines
- Release process
- Versioning strategy (Semantic Versioning)
- Merge policies
- Tagging conventions
- Hotfix procedures
- CI/CD integration
- Code review checklist
- Issue tracking and linking
- Documentation updates
- Rollback procedures
- Changelog maintenance
- Feature branching strategy

## Architecture & API Design

See [docs/API_DESIGN.md](./docs/API_DESIGN.md) for:
- API versioning and request/response format
- Error handling (RFC 9457 Problem Details)
- Observability and metrics standards (tag-based dimensional metrics)
- Logging standards
- Gateway exception: public, unversioned info endpoints (e.g., `/api/product-info`) when no service owns the data

## Testing Strategy
ALWAYS read this file before designing or writing tests!

See [docs/TESTING_STRATEGY.md](./docs/TESTING_STRATEGY.md) for:
- When to run tests
- Testing levels (unit, integration, e2e)
- Coverage requirements
- Test data management
- Mocking and stubbing guidelines
- CI testing integration
- Performance testing
- Security testing
- Test documentation

## Debug Info

See [docs/DEBUG_INFO.md](./docs/DEBUG_INFO.md) for:
- Troubleshooting guides
- Common issues and solutions

## Future Ideas

See [docs/CONCEPTS_TO_TRY.md](./docs/CONCEPTS_TO_TRY.md) for:
- Experimental concepts to explore (which are the target for future spikes)
- Future improvements

## Planning

- Add all plans in [planning](./planning/) folder
- Use the following name pattern `<feature-name>-PLAN.md`, for example `Migrating-auth-service-to-ddd-PLAN.md`
- In each plan create tasks to be done and when done put ticks on them, so I know what is implemented, what has left, etc.
- Create list with issues also - mainly technological (closed ports, things to be set up, etc). For the issues which are solved put green tick on them and explain how they are fixed briefly. In that way I will know what are the issues which left after the implementation
