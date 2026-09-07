# Automatic Builds & Deploy — PLAN

<!-- Pass 3R redesign plan is appended below; preserve prior passes as execution history. -->

## Strategy: Iterative, Fast Feedback

This plan is split into **4 passes**, each building on the previous one. The guiding principle is **deploy to AWS as fast as possible**, then harden and polish incrementally.

| Pass | Name | What You Get |
|------|------|--------------|
| 1 | **MVP: Running on AWS** | All services live on AWS. Manual deploys. Proves the system works in the cloud. |
| 2 | **CI Pipeline Hardening & Staging** | Automated CI on push/PR. Test gates. Selective builds. Branch protection. Staging environment. |
| 3 | **Release, Traceability & Promotion** | Official release model. Staging → production promotion with approval. Rollback. Full traceability. |
| 4 | **Operational Maturity** | Notifications. Dashboards. Runbooks. Nightly validation. Merge queue. Cost guardrails. |

After Pass 1 you have a **working deployment**. After Pass 4 you satisfy **every v1 requirement**.

---

## Subplans

1. [01_MVP_DEPLOY.md](./01_MVP_DEPLOY.md) — AWS account, ECR, minimal GH Actions, ECS Fargate, S3+CloudFront, databases
2. [02_CI_PIPELINE_HARDENING.md](./02_CI_PIPELINE_HARDENING.md) — Branch protection, selective builds, test gates, Docker tagging, caching, staging
3. [03_RELEASE_TRACEABILITY.md](./03_RELEASE_TRACEABILITY.md) — Release identity, promotion flow, production env, rollback, traceability chain, ECR retention
4. [04_OPERATIONAL_MATURITY.md](./04_OPERATIONAL_MATURITY.md) — Notifications, dashboards, audit, merge queue, nightly builds, runbooks, cost monitoring, PR/branch-protection policy review
5. [05_FUTURE_IMPROVEMENTS.md](./05_FUTURE_IMPROVEMENTS.md) — Non-mandatory improvements for later (Dependabot, etc.)

---

## Requirements Source

All requirements come from these three documents in this directory:

- [01_REQUIREMENTS_BUILD.md](./01_REQUIREMENTS_BUILD.md) — CI, test gating, versioning, tagging, traceability, retention
- [02_REQUIREMENTS_DEPLOY.md](./02_REQUIREMENTS_DEPLOY.md) — Staging/production, approvals, rollback, notifications
- [03_REQUIREMENTS_HOSTING.md](./03_REQUIREMENTS_HOSTING.md) — Frontend hosting, domains, HTTPS, dashboards

---

## Cost Trajectory

**Note:** The original Pass 1 estimate (~$2-4) was overly optimistic — it assumed Fargate Spot pricing but did not account for the ALB ($24.19/month) or the minimum baseline cost of Secrets + ECR + Cloud Map (~$1.25/month; KMS keys are AWS-managed = free). After switching to Spot on 2026-07-25, the real costs are:

| After Pass | Estimated Monthly Cost | Notes |
|---|---|---|
| 1 — MVP (running 24/7 Spot) | ~$17–42 | Spot + ALB 24/7 = $49.00; Spot + ALB daily pause = ~$17 |
| 2 — + Staging | ~$20–45 | Staging adds duplicate infra when active |
| 3 — + Production + Release infra | ~$22–47 | Production adds ALB + extra tasks when active |
| 4 — + Monitoring/notifications | ~$22–47 | No incremental AWS cost |

The original $5/month ceiling required both Spot AND pausing the ALB when idle. See [COST-EXPLANATION.md](./explanations/COST-EXPLANATION.md) for detailed analysis.

Cost control strategies:
- Fargate Spot pricing (switched 2026-07-25 — saves ~60% on compute)
- Pause scripts: `pause-playground.sh` / `resume-playground.sh` (cuts idle cost to ~$1.25/month)
- Making staging on-demand (scale to 0 or tear down when idle)
- Leveraging RDS Free Tier (12 months, expires July 2027)
- Using GitHub Actions free tier (2000 min/month)

---

## Key Decisions to Make During Implementation

| Decision | When | Options |
|---|---|---|
| AWS region | Pass 1 | `eu-north-1` (Stockholm) vs `eu-central-1` (Frankfurt) — pick cheapest |
| Database: RDS vs containerized PG | Pass 1 | RDS Free Tier preferred; containerized PG if post-Free-Tier cost is a concern |
| Routing: ALB vs Service Connect | Pass 1 | ALB is simpler; Service Connect is cheaper — evaluate during implementation |
| Staging lifecycle model | Pass 2 | Scale-to-zero vs on-demand teardown |
| Release label strategy | Pass 3 | Auto-generated (semantic-release) vs manually assigned during promotion |

---

## Execution Traceability

Every step executed in this plan **MUST** update [`executed/INFO.md`](./executed/INFO.md) with:
- Every AWS resource created (ARNs, IDs, security groups, policies, secrets)
- Every command run (with full parameters)
- Every configuration change (files, env vars, overrides)
- Every issue encountered and its resolution
- Every credential/secret placeholder (never the actual secret value)

**Purpose:** When the entire plan is executed, `INFO.md` must contain everything needed to replicate the environment from scratch — pipelines, infrastructure, databases, networking, and all. No tribal knowledge, no forgotten steps.

## Cross-Plan Maintenance Contract

> **Every plan** that touches deployment or infrastructure (e.g., `plans/DDDItemsImprovement/PLAN.md`) **MUST** update these files whenever making changes that affect runtime behavior:

| File | When to Update |
|------|---------------|
| [`WHAT-WAS-DONE.md`](./WHAT-WAS-DONE.md) | Any infrastructure change, deployment fix, configuration update, or code change that affects runtime |
| [`scripts/config/production.env`](../../scripts/config/production.env) | If production identifiers, services, ports, or security groups change |
| [`scripts/config/staging.env`](../../scripts/config/staging.env) | If staging identifiers, services, database, ports, or security groups change |
| [`scripts/lib/lifecycle.sh`](../../scripts/lib/lifecycle.sh) | If shared ALB, ECS, RDS, waiter, readiness, or verification behavior changes |

**Why:** These files are the source of truth for automation. Drift = broken automation = manual work.

---

## Progress

- [x] **Pass 1** — MVP: Running on AWS (DONE — ECS + RDS + CI/CD + S3 + CloudFront + frontend deployed; ALB active during verification, now paused)
- [x] **Pass 2** — CI Pipeline Hardening & Staging (workflow with change detection + test gates + selective builds + Docker tagging; staging infra provisioned + smoke tested; branch protection applied via `gh api`; post-merge IAM scope correction is tracked below)
- [ ] **Pass 3** — Release, Traceability & Promotion
  - [x] 3.1 Release contract and local validation foundation
  - [x] 3.2 Candidate build evidence and immutable artifacts (offline implementation + gate green in `tests/scripts/candidate_evidence_test.sh`; live ECR/GitHub/Syft verification deferred to the consolidated pass)
  - [x] 3.3 ECR release tagging, immutability, and least privilege (offline implementation + gate green in `tests/scripts/ecr_release_tagging_test.sh`; live ECR settings read-back, real put-image behavior, OIDC environment subject, and IAM Access Analyzer deferred to the consolidated pass)
  - [x] 3.4 Controlled staging-to-production promotion workflow (offline implementation + gate green in `tests/scripts/promotion_test.sh`: the `release_contract.promotion` decision layer — dispatch/run/ancestry/preflight/snapshot/plan/waiter/frontend/verify/finalize/compensate; `promote-release.yml` static checks incl. attempt-pinned candidate-evidence consumption and `approvedBy` from `actions/runs/{run}/approvals` (never `github.actor`); `promotion-preflight.sh`/`snapshot-production.sh`/`deploy-production.sh`/`verify-production.sh`/`publish-frontend.sh`/`finalize-release.sh`/`compensate-production.sh` offline runs with a stateful AWS + `gh` stub, incl. immutable prefix-marker publication and frontend restoration; mandatory profile/region + mutation read-back + no-secrets scan; ruff/shellcheck/`git diff --check`. The live owner-approved promotion, the real `production` Environment approval, real ECR/ECS/S3/CloudFront mutations and read-backs, and the real GitHub Release publication are deferred to the consolidated pass)
  - [x] 3.5 Production hardening (offline implementation + gate green in `tests/scripts/production_hardening_test.sh`: task-definition/service-config validation, sanitized task-definition transforms, read-only inventory + production/staging separation + CloudTrail coverage tooling, frontend S3 REST + OAC migration tool, lifecycle environment guards, and the Fargate Spot/backup-limitation decisions in `explanations/PRODUCTION-HARDENING-DECISIONS.md`; live inventory read-back, OAC migration, CloudTrail read-back, and service/SG/IAM tightening deferred to the consolidated pass)
  - [x] 3.7 Release traceability (offline implementation + gate green in `tests/scripts/release_traceability_test.sh`: the four read-only lookups — commit/release/running/digest — plus the manifest↔ECR↔ECS-running-digest↔frontend consistency audit via `release/bin/trace.sh` + `release_contract.traceability`, offline fixtures for consistent/paused/drift state, a stateful AWS-stub proof of the live gather path (identity preflight + read-only), the read-only GitHub Releases index auto-fetch, and mandatory profile/region fail-closed semantics; the read-only live smoke test against real AWS/GitHub deferred to the consolidated pass)
  - [x] 3.6 Owner-approved rollback (offline implementation + gate green in `tests/scripts/rollback_test.sh`: the `release_contract.rollback` decision layer — dispatch/select/schema/frontend-restore/result plus the reused snapshot/plan/waiter/verify/compensate promotion contract; `rollback-release.yml` static checks incl. `production` Environment, shared non-cancelling `production-mutation` concurrency, pre-approval read-only preflight + post-approval full revalidation, `approvedBy` from `actions/runs/{run}/approvals` (never `github.actor`), run-pinned target-manifest consumption, automatic compensation, and no rebuild/tag-minting/release-publication; `rollback-preflight.sh`/`deploy-rollback.sh`/`restore-frontend.sh`/`verify-rollback.sh`/`record-rollback-result.sh` offline runs with a stateful AWS + `gh` stub incl. pre-approval current-vs-target summary, digest-pinned ECS revisions via sanitize/validate, no-`--delete` frontend restoration from the retained immutable prefix, paused-environment fail-closed verification, and idempotent rollback-result write/resume/conflict; mandatory profile/region + mutation read-back + no-secrets + no-tag-minting scan; ruff/shellcheck/`git diff --check`. The real owner-approved rollback, the real `production` Environment approval, real ECR/ECS/S3/CloudFront mutations and read-backs, real frontend restoration, and the real rollback-result artifact are deferred to the consolidated pass)
  - [x] 3.8 Retention and rollback-window enforcement (offline implementation + gate green in `tests/scripts/retention_test.sh`: the desired ECR lifecycle policy `release/ecr/lifecycle-policy.json` (keep-10 `release-*` first, enumerated `sha-`/`main-latest`/`branch-` candidate families at 30 days, 14-day untagged grace) proven against multi-tag fixtures; the `release_contract.retention` decision layer — first-match-wins evaluation model, ECR lifecycle-policy-preview validation (disagreement/protected-expiring fail closed), read-only rollback-window audit (exact 10 or all, missing/mismatched artifacts fail closed, older metadata-only releases never claimed), keep-10 push-order coverage (`POLICY_WINDOW_GAP`), frontend prefix retention, GitHub retention classes; `audit-retention-window.sh` (read-only) + `preview-retention-policy.sh` (offline model + live `start/get-lifecycle-policy-preview` dry-run) proven with a stateful AWS stub; `apply-retention-policy.sh` `--dry-run` mutating nothing and `--apply` refused offline (`ONLINESHOP_RETENTION_LIVE_APPLY=1` gate for the consolidated live pass) with immediate `get-lifecycle-policy` read-back; GitHub retention-days static checks (candidate 30, staging-failure 14, snapshot/result records 14); mandatory profile/region + identity preflight + no-secrets scans; ruff/shellcheck/`bash -n`/`git diff --check`. The live policy preview/apply/read-back, the live read-only retention audit, and the real S3/frontend retention are deferred to the consolidated pass)
- [ ] **Pass 4** — Operational Maturity
- [ ] **Pass 5** — Future Improvements (non-mandatory)

### CI staging permission incident — 2026-08-08

- [x] Investigated [main run 31259210183, `e2e-staging` job 93107532753](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/31259210183/job/93107532753): staging resume failed before deployment because `rds:CreateDBInstance` was denied on `subgrp:onlineshop-staging-db-subnets`.
- [x] Corrected `github-actions-staging-deploy-policy.json` by adding only the isolated staging DB subnet-group ARN to `ManageEphemeralStagingDatabase`; added an IAM regression test so the required DB and subnet-group scopes cannot drift apart.
- [x] Applied the corrected inline policy to the live `github-actions-onlineshop` role and read it back after each change. The first post-auth run exposed additional least-privilege gaps (`DescribeInternetGateways`, `DescribeAvailabilityZones`, `DescribeAccountAttributes`, `GetSecurityGroupsForVpc`, and target-group attributes); each was added to the source policy and live policy, with the target-group attribute read scoped to `Resource: "*"` because ELBv2 rejected the exact target-group ARN.
- [x] Fixed the next staging failure: `resume-staging.sh` was starting old ECS tasks before `ci-deploy-staging.sh` installed the candidate image, so CI now provisions clean RDS/ALB infrastructure with `--defer-services`, deploys the candidate, then starts services. This is covered by PR #39 and its merged `main` verification run 31265257478.
- [x] Verified the API-gateway timeout fix in merged-main run [31267620402, `e2e-staging` job 93128495549](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/31267620402/job/93128495549): staging resumed with ECS deferred, the candidate deployed successfully, all 3 cloud E2E tests passed (including invalid-token → 401), and teardown ran. The gateway uses the annotation-backed 5-second `TimeLimiterRegistry` and unwraps `CompletionException`/`ExecutionException`.
- [x] Fixed the follow-on candidate-evidence failure in run 31267620402: the job invoked the release validator without installing its pinned Python requirements, so `referencing` was missing. The workflow now sets up Python and installs `release/requirements.txt` before validation; the local candidate-evidence gate passes.
- [x] Corrected the setup-python action pin exposed by merged-main run 31269541080: candidate evidence stopped at job setup because the copied SHA was not resolvable. The workflow now uses the verified `actions/setup-python@v5` commit SHA `a26af69be951a213d495a4c3e4e4022e16d87065`.
- [x] Verified the complete merged-main path in [run 31271458491](https://github.com/Djimi/OnlineShop-full-stack/actions/runs/31271458491): staging resume, ordered candidate deployment, all cloud E2E tests, teardown, and candidate-evidence job `93142263971` passed; setup-python and the pinned release requirements both completed successfully.

---

## Known Issues & Resolutions

### Resolved ✅

| Issue | Resolution |
|-------|-----------|
| ✅ Private RDS unreachable for SQL ops (no public access, private subnets) | One-off Fargate psql task pattern, codified in `scripts/ecs-run-sql.sh` (see [AWS_COMMANDS_GUIDE.md Part D](./AWS_COMMANDS_GUIDE.md)) |
| ✅ Schema apply reported exit 0 but tables missing (`missing table [sessions]` crash loop) | Caused by JSON-escaping bugs in hand-built container commands + no read-back verification. Fixed by `ecs-run-sql.sh` (base64 SQL transport) + mandatory `--verify` in the same run |
| ✅ Service Connect `SC service is already used` / `portName does not refer to any named PortMapping` | SC portName must match container `portMappings[].name` AND be unique per Cloud Map namespace. Staging TDs use `*-staging-port` names |
| ✅ `launch type and capacity provider strategy` conflict on service create | Mutually exclusive flags — staging/prod services use `--capacity-provider-strategy FARGATE_SPOT` only |
| ✅ Container `secrets` rejected without `executionRoleArn` | All TDs include `executionRoleArn: ecsTaskExecutionRole` |
| ✅ ECS stopped launching tasks after crash loop (`desired:1, running:0`) | ECS deployment circuit-breaker behavior; resolved with `--force-new-deployment` after root-cause fix |
| ✅ Master DB password plaintext in 11 helper TD revisions; staging passwords in session logs (2026-08-02) | Master creds moved to `onlineshop/rds/master` secret; exec role policy extended; both staging passwords rotated (verified by connecting as each service account); 11 revisions permanently purged via `delete-task-definitions` |
| ✅ Long blocking poll loops hit shell timeouts (120s + 600s lost) | Rule added to `AGENTS.md`: no blocking polls; use `aws ecs wait services-stable` or short bounded loops |

### Open

| Issue | Notes |
|-------|-------|
| ✅ Duplicate `build-and-push.yml` workflow | Removed; `build-and-deploy.yml` is the single active CI/CD workflow after merge |
| ✅ Staging remained billable after E2E | Main CI tears staging down in an `always()` step: ECS→0, ALB deletion, and RDS deletion without snapshot retention |
| ✅ Snapshot restoration preserved drift and stale test data | Every staging start now creates empty RDS, applies repository schemas/seeds, verifies grants and data as restricted users, then deploys services |
| ✅ Main CI staging resume lacked the RDS subnet-group resource scope | Added the exact subnet-group ARN to the source and live policies; the corrected workflow reached clean bootstrap and deployment |
| ✅ Staging CI started stale images before candidate deployment | Added `resume-staging.sh --defer-services`; CI provisions RDS/ALB with ECS stopped, then deploys the candidate before scaling services |
| ✅ Uncached invalid-token E2E returned 502 on cold staging Auth lookup | Gateway registry wiring and wrapped-exception classification are fixed and unit-tested; merged-main run 31267620402 passed the invalid-token E2E with 401 |
| ✅ Candidate evidence failed after green staging E2E because `referencing` was absent on the runner | Candidate-evidence installs the pinned release-contract requirements before producer-set validation, uses the verified setup-python pin, and was confirmed end-to-end by merged-main run 31271458491 |
---

# Pass 3R — CI/CD and Release Redesign

## Why this pass exists

Pass 3 produced valuable release-safety foundations, but the current implementation is too large and has not completed its live promotion/rollback proof. The active workflow repeats backend jobs, rebuilds every component on every `main` merge, mixes orchestration with environment-specific shell/AWS logic, and spends about 37 minutes in ephemeral staging even though the cloud E2E tests themselves take under one minute.

Pass 3R replaces that path incrementally. Completed Pass 1–3 records above remain historical evidence. The unfinished consolidated Pass 3 live verification is superseded by 3R.10 and MUST NOT be run against the old promotion path.

Target behavior:

```text
feature/** push or pull request
    ├── detect affected components
    ├── run affected unit/integration jobs in parallel
    ├── run frontend lint/build when applicable
    └── run the complete local Docker Compose API E2E suite
             └── no AWS credentials and no ECR publication

main merge
    ├── resolve changes since the latest successfully staged ancestor candidate
    ├── build only affected components
    ├── reuse verified immutable artifacts for unaffected components
    ├── assemble one complete four-component candidate set
    ├── create clean ephemeral staging
    ├── deploy the exact backend digests and run cloud E2E
    └── always destroy staging

manual promotion (initial 3R behavior)
    ├── protected production approval
    ├── deploy only artifacts that differ from production
    ├── verify exact running digests/checksum
    └── publish one official application release
```

## Locked decisions

- One application SemVer identifies one complete Auth + Items + API Gateway + frontend release set.
- A component is independently built/reused, but it does not get an independent SemVer.
- `common/**` changes always mark Items as affected.
- Every `feature/**` push runs local Docker Compose E2E. Pull requests also run it, including fork PRs.
- Feature/PR validation never assumes an AWS role and never publishes ECR images. Draft PRs are the normal early-feedback mechanism; no separate manual snapshot-image workflow is added until a real need exists.
- Main merges create verified candidates automatically. Production promotion remains manually versioned and approval-gated during Pass 3R.
- Staging remains clean and ephemeral per candidate. A warm/stopped staging database is not introduced.
- New candidates and official releases use a clean manifest contract v2. Existing v1 data is not migrated and is not a rollback target after v2 cutover.
- Promotion never rebuilds. Unchanged artifacts are reused by digest/checksum.
- Local/operator AWS commands require `--profile dpm-profile --region eu-north-1`. GitHub Actions uses temporary OIDC environment credentials with explicit region/account verification and no named profile.
- Durable infrastructure-as-code migration remains deferred and is tracked as architectural debt.

## Mandatory execution protocol

This protocol applies to every 3R subphase and is part of the deliverable.

1. Work only in the dedicated `feature/cicd-release-redesign` worktree.
2. Before implementation, read root `AGENTS.md`, this Pass 3R section, `docs/CI_CD_GOTCHAS.md`, `docs/TESTING_STRATEGY.md`, and the files named by the subphase.
3. One subphase equals one independently reviewable product. Do not mix work from the next subphase.
4. Implementation loop:
   - Luna/max implementation agent reads this file from a clean context and implements/tests the subphase.
   - Terra/high reviewer performs a read-only correctness, security, maintainability, and scope review.
   - Luna/max challenger independently looks for counterexamples, missing tests, unsafe assumptions, and needless complexity.
   - If either reports actionable findings, a Luna/max implementation pass fixes them; repeat review/challenge until both return no actionable findings.
   - Luna/max documentation agent checks the recursive documentation contract and fixes all affected docs.
   - Root agent runs the required gates, reviews the diff, updates this plan and produces the user handoff.
5. Mark a subphase complete only when implementation, automated gates, both reviews, documentation audit, and user manual acceptance are complete.
6. At the end of a subphase, stop. Give the user a short result summary, changed-file map, automated evidence, and exact manual instructions. Do not start the next subphase until the user explicitly accepts or requests changes.
7. Record review outcomes and commands in the subphase evidence block. Never claim a live check from an offline stub.
8. Do not commit or push unless the user separately requests it.

### AWS/staging budget

- Implementation and review agents MUST NOT call live AWS unless the current subphase explicitly requires a live acceptance run and the user has reached that checkpoint.
- Review/challenge/documentation agents never start staging.
- Combine cumulative staging changes into the live checkpoints in 3R.5b, 3R.7 and 3R.10. Subphases 3R.4 and 3R.5a use offline/stateful stubs only.
- Before any live AWS work, run `aws sts get-caller-identity --profile dpm-profile --region eu-north-1`. If it reports `Your session has expired`, stop and ask the user to re-authenticate; do not retry.
- Every AWS create/put/delete must be followed by describe/get/list read-back.
- A staging-owning run always tears down ECS, ALB and RDS. It retains a snapshot only through the explicit diagnostic exception.

## Status dashboard

| Subphase | Product | Implementation | Review | Challenge | Docs | Automated | Manual acceptance |
|---|---|---:|---:|---:|---:|---:|---:|
| 3R.0 | Plan, worktree and baseline contract | complete | pending | pending | pending | n/a | pending |
| 3R.1 | Critical CI security and promotion handoff repair | complete | complete | complete | complete | complete | pending |
| 3R.2 | Feature/PR validation and reusable Java matrix | pending | pending | pending | pending | pending | pending |
| 3R.3 | Trusted-main reusable candidate workflow | pending | pending | pending | pending | pending | pending |
| 3R.4 | Digest-pinned staging interface and timing evidence | pending | pending | pending | pending | pending | pending |
| 3R.5a | Three-task database bootstrap | pending | pending | pending | pending | pending | pending |
| 3R.5b | Parallel ECS deploy and measured health tuning | pending | pending | pending | pending | pending | pending |
| 3R.6 | Release-set v2 contract and resolver | pending | pending | pending | pending | pending | pending |
| 3R.7 | Selective main builds and v2 candidate staging | pending | pending | pending | pending | pending | pending |
| 3R.8 | Changed-only promotion, rollback and traceability | pending | pending | pending | pending | pending | pending |
| 3R.9 | OIDC/IAM role cutover | pending | pending | pending | pending | pending | pending |
| 3R.10 | Live v2 cutover, rollback drill and legacy cleanup | pending | pending | pending | pending | pending | pending |

## Shared interfaces and invariants

### Workflow boundaries

The target files are small orchestration workflows plus one reusable Java workflow:

```text
.github/workflows/validation.yml       feature/** push + pull_request
.github/workflows/candidate.yml        trusted main push
.github/workflows/_java-service.yml    workflow_call job implementation
.github/workflows/promote-release.yml  protected manual promotion
.github/workflows/rollback-release.yml protected manual rollback
```

`_java-service.yml` accepts a closed `component` value (`auth`, `items`, `apiGateway`) and a boolean `publish`. Component metadata inside the reusable workflow derives working directory, report path, Docker context/file and ECR repository. It MUST NOT accept caller-supplied shell commands.

Items behavior is explicit:

```text
component=items
    ├── cache key: Items/pom.xml + common/pom.xml
    ├── common/: ./mvnw install -DskipTests
    ├── Items/: ./mvnw verify
    ├── Docker context: repository root
    └── Dockerfile: Items/Dockerfile
```

### Component plan

The pure resolver writes deterministic `component-plan.json`:

```json
{
  "schemaVersion": 1,
  "assemblySha": "<current-main-sha>",
  "baseCandidate": {
    "sourceSha": "<last-successful-staged-ancestor-sha>",
    "runId": 123,
    "runAttempt": 1,
    "manifestSha256": "<sha256>"
  },
  "components": {
    "auth": {"action": "build", "reason": "Auth/** changed"},
    "items": {"action": "reuse", "reason": "unaffected"},
    "apiGateway": {"action": "reuse", "reason": "unaffected"},
    "frontend": {"action": "reuse", "reason": "unaffected"}
  }
}
```

Rules:

- Diff from the latest successfully staged candidate whose assembly SHA is an ancestor of the current main SHA. Never rely only on the immediately previous commit.
- If the base is absent, expired, invalid, incomplete or not an ancestor, rebuild all four components.
- Changes accumulated through failed main runs are included in the next candidate.
- `common/**` implies Items.
- Workflow/release/build configuration changes conservatively affect the components they can change; ambiguous shared build changes affect all components.
- Documentation-only and E2E-test-only changes may reuse all application artifacts, but the complete set still runs in cloud staging.
- Every component has exactly one `build` or `reuse` decision.

### Candidate evidence and release manifest v2

Candidate evidence is versionless and independently consumable. It includes the frontend archive/checksum/SBOM and all backend SBOMs even when copied from a prior candidate.

Each component records:

- `sourceSha` of the artifact;
- `mode`: `built` or `reused`;
- backend repository, immutable `sha-<component-sourceSha>` tag and digest, or frontend archive checksum;
- original producer workflow run/attempt;
- SBOM name and checksum;
- immediate base-candidate identity for reused artifacts.

`release.sourceSha` is the assembly/main commit tested as a set. Component source SHAs may differ. Built components must use the assembly SHA. Reused components must match their validated base component record byte-for-byte. Items always has `commonSourceSha == items.sourceSha`.

One official SemVer creates `release-<version>` membership tags for all three selected backend digests, including reused images. This is server-side tagging, not a rebuild. An official frontend gets a complete immutable `_releases/v<version>/` prefix even when its bytes are reused.

### Deployment and compensation

- Resolve tags once and deploy `repository@sha256:...` everywhere.
- Build, staging and production compare component digest/checksum, not top-level release SHA.
- Prepare task definitions first; deploy Auth and Items concurrently; deploy Gateway after both; publish frontend assets before the live marker.
- Unchanged production backends retain their task-definition ARNs.
- Maintain a mutation journal after every successful mutation. Compensation consumes that journal in reverse order; workflows never pass a hard-coded all-components list.
- Compensation stays in the already approved production job so a failure path does not need a new unprotected AWS role assumption.
- Database rollback remains forbidden. Schema-changing production releases remain blocked until versioned forward/backward-compatible migrations and restore testing exist.

## 3R.0 — Plan and baseline

### Deliverable

- [x] Create `feature/cicd-release-redesign` in an isolated worktree.
- [x] Append Pass 3R without erasing previous execution history.
- [x] Encode agent loop, user checkpoint, AWS budget and locked decisions.
- [ ] Run a plan-only Terra review and Luna challenge.
- [ ] Run a documentation-scope audit.
- [x] Record current successful-main timing baseline from existing evidence (no new staging run): about 37m32s total; resume about 14m55s; deploy about 17m39s; E2E about 25s; teardown about 4m26s.
- [ ] User accepts the phase boundaries and manual-checkpoint flow.

Manual acceptance:

1. Read this Pass 3R summary, locked decisions and status dashboard.
2. Confirm feature pushes explicitly run local Docker Compose E2E without AWS/ECR.
3. Confirm live staging is limited to 3R.5b, 3R.7 and 3R.10.
4. Confirm no next subphase starts before the user accepts the current handoff.

## 3R.1 — Critical CI security and promotion handoff repair

### Deliverable

- [x] Transfer every untrusted GitHub context used by shell (`workflow_dispatch` inputs, ref names and similar values) through step `env`; validate before use and pass only quoted variables/argv.
- [x] Add hostile-input tests for single/double quotes, spaces, `$()`, backticks, semicolons, redirection and newlines. Prove no marker command/file can be created.
- [x] Change `deploy-production.sh` to accept the schema-valid candidate manifest plus the production snapshot. Source current task-definition ARNs from the snapshot, replace only intended images, and emit a deployment manifest with final task-definition ARNs.
- [x] Make the workflow convert only that deployment manifest into an official manifest.
- [x] Add one stateful integration test matching the real workflow handoff: candidate → snapshot → deploy → deployment manifest → official manifest → verify → finalize.
- [x] Bind promotion evidence to the exact requested run id, run attempt, source SHA and attempt-scoped jobs endpoint using the real GitHub REST response shape.
- [x] Bind the production snapshot to the actually live frontend marker, its exact canonical Git tag/source SHA, matching immutable prefix marker/index, and full-object S3 SHA-256 before any mutation.
- [x] Make promotion, compensation and rollback frontend writers establish the SHA-256 object-checksum contract required by the next snapshot.
- [x] Replace workflow-level permissions with `contents: read` and job-specific opt-ins where this can be done without the later role migration.
- [x] Do not redesign release v1 or staging in this subphase.

Automated acceptance:

```bash
bash tests/scripts/ci_security_contract_test.sh
bash tests/scripts/promotion_handoff_test.sh
bash tests/scripts/promotion_test.sh
bash tests/scripts/rollback_test.sh
bash tests/scripts/release_contract_test.sh
git diff --check
```

Manual acceptance (offline; no AWS/staging):

1. Run `bash tests/scripts/ci_security_contract_test.sh`; expect zero failures.
2. Run `bash tests/scripts/promotion_handoff_test.sh`; confirm all eight candidate → snapshot → deployment → official → verify → finalize stages pass.
3. Run `bash plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/deploy-production.sh --help | less`; confirm it documents candidate manifest + snapshot input.
4. Run `bash plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/validate-manifest.sh plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/fixtures/valid/candidate-v1.2.1.json`; expect `"valid": true`. Then run `jq '[.components.auth,.components.items,.components.apiGateway] | map(.taskDefinitionArn // null)' plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/fixtures/valid/candidate-v1.2.1.json`; expect three `null` values.
5. Inspect lines 400–423 of `tests/scripts/promotion_handoff_test.sh`; confirm the real deploy script is called and each final backend ARN is required to be present and different from its snapshot source ARN.
6. Run the hostile-input case printed by the security gate; confirm it fails validation and no marker file exists.
7. Review the workflow diff and confirm no untrusted GitHub expression is embedded inside a `run:` script.

Rollback: revert only the 3R.1 diff. No live resources are changed.

### 3R.1 implementation evidence — 2026-08-13

- Worktree/branch: `/home/dpm/CodingProjects/OnlineShop-full-stack-worktrees/cicd-release-redesign` on `feature/cicd-release-redesign`.
- Implementation: Luna/max agents implemented the initial repair and bounded remediation passes. No agent called live AWS, started staging, mutated GitHub, committed or pushed.
- Review: Terra/high (`phase_3r1_review`, then bounded clearance passes through `phase_3r1_clearance3`) found and drove fixes for rollback compensation permissions/YAML, PR ref behavior, workflow-dispatch selection, PR path-filter permissions, exact source/run/attempt binding, real GitHub API shapes, actual-live frontend snapshot identity, and S3 checksum wire/writer contracts. Final result: `no actionable findings`.
- Challenge: Luna/max (`phase_3r1_challenge_last`) independently found the exact-attempt endpoint/identity gaps, bare `head_branch` API shape, wrong newest-tag snapshot assumption, incomplete snapshot compensation preconditions, and remaining checksum writers. Each finding received a fixture/stateful regression. Final result: `no actionable findings`.
- Documentation: Luna/max `phase_3r1_docs` audited the root and service documentation tree; Luna/max `phase_3r1_docs_clearance` corrected the final unscoped-discovery versus attempt-scoped-authority wording. Root corrected the phase boundary: current combined backend jobs remain OIDC-capable at job scope, PR credential/publication steps are guarded and the source trust excludes `pull_request`, structural separation is 3R.2/3R.3, role cutover is 3R.9, and live proof is 3R.10. `common/` and `e2e-tests/` have no service-level `AGENTS.md`; no component behavior changed there.
- Authoritative serial offline gates, all passing:

  ```bash
  bash tests/scripts/ci_security_contract_test.sh
  bash tests/scripts/promotion_handoff_test.sh
  bash tests/scripts/promotion_test.sh
  bash tests/scripts/rollback_test.sh
  bash tests/scripts/release_contract_test.sh
  bash tests/scripts/candidate_evidence_test.sh
  bash tests/scripts/ecr_release_tagging_test.sh
  git diff --check
  ```

  The shared Python suite reported 446 passing tests; the gates also ran their relevant ShellCheck and Ruff checks.
- Scope proof: the two Claude workflows have no diff. No live AWS, staging, production, protected-environment approval or GitHub Release claim is made.
- Tracked boundary, not a 3R.1 acceptance blocker: because GitHub permissions cannot be event-conditional, the existing mixed PR/branch-push backend jobs retain job-scoped `id-token: write`. The role trust does not admit a `pull_request` subject. Pass 3R.2/3R.3 removes the token structurally by separating validation from trusted publication; Pass 3R.9 verifies/applies the live role split.
- User manual acceptance: pending. Do not start 3R.2 until the user accepts this checkpoint or requests changes.

## 3R.2 — Feature/PR validation and reusable Java matrix

### Deliverable

- [ ] Add `validation.yml` for both `push: feature/**` and `pull_request: main`.
- [ ] Run the full Docker Compose API E2E job on every feature push and every PR, even when only one component changed.
- [ ] Avoid duplicate push/PR work where possible without weakening fork-PR coverage; document the chosen GitHub event/concurrency behavior in the implementation evidence.
- [ ] Add `_java-service.yml` with closed component metadata and a matrix containing affected Java services.
- [ ] Preserve Items/common behavior described in Shared Interfaces.
- [ ] Run frontend `npm ci`, lint and build when frontend is affected.
- [ ] Give all validation jobs `contents: read` only; prohibit OIDC, AWS CLI credential setup, ECR login and image publication.
- [ ] Add stable `validation-required` aggregation job for branch protection.
- [ ] Keep all third-party actions SHA-pinned and add Dependabot weekly GitHub Actions pin maintenance.

Automated acceptance:

```bash
bash tests/scripts/reusable_workflow_test.sh
bash tests/scripts/ci_security_contract_test.sh
git diff --check
```

Manual acceptance (GitHub only; no AWS):

1. Push an Auth-only commit to `feature/**`: Auth validation and Docker Compose E2E run; no ECR/AWS steps appear.
2. Push a `common/**` change: Items validation and Docker Compose E2E run.
3. Push a frontend-only change: frontend lint/build and Docker Compose E2E run; backend unit jobs may skip.
4. Open/update a PR and confirm the intended validation event behavior recorded by the implementation.
5. Inspect job permissions and confirm there is no `id-token: write`.
6. After `validation-required` exists on a real PR, update branch protection, read it back, and then retire obsolete required-check names.

Rollback: restore previous required checks before reverting the workflow.

## 3R.3 — Trusted-main reusable candidate workflow

### Deliverable

- [ ] Add `candidate.yml` for trusted `push: main` only.
- [ ] Reuse `_java-service.yml`; initially build all backends so behavior stays equivalent.
- [ ] Preserve frontend build/package, immutable SHA tags, SBOMs, candidate evidence and staging gate.
- [ ] Keep singleton staging concurrency with `cancel-in-progress: false`.
- [ ] Add stable `candidate-ready` aggregation status.
- [ ] Disable main handling in the monolithic workflow only after the replacement produces one successful main candidate.

Automated acceptance:

```bash
bash tests/scripts/reusable_workflow_test.sh
bash tests/scripts/candidate_evidence_test.sh
bash tests/scripts/ci_security_contract_test.sh
git diff --check
```

Manual acceptance (one main run; staging is the existing behavior):

1. Merge the workflow PR once prior phases are accepted.
2. Confirm only one main candidate workflow starts.
3. Confirm the three backend jobs run concurrently and frontend runs independently.
4. Confirm candidate evidence appears only after cloud staging succeeds.
5. Confirm teardown succeeds.
6. Rerun the same workflow attempt and confirm trusted immutable SHA images are reused, not overwritten.

Rollback: restore the old main trigger and revert `candidate.yml`.

## 3R.4 — Digest-pinned staging interface and timing evidence

### Deliverable

- [ ] Replace `ci-deploy-staging.sh <one-tag>` with `ci-deploy-staging.sh --set <validated-json>` containing repository + digest per backend.
- [ ] Verify each tag/digest once and register `repository@sha256:...`.
- [ ] Reuse the hardened image-only task-definition sanitizer; preserve volumes, roles, runtime platform, proxy/ephemeral storage, logs, health and secrets references.
- [ ] Move staging identifiers into `scripts/config/staging.env`.
- [ ] Make scripts emit staging URL/deployment results so workflow YAML contains orchestration, not AWS/jq policy logic.
- [ ] Emit a timing JSON artifact for RDS, bootstrap, ALB, each ECS service, E2E and teardown.
- [ ] Document honestly that cloud E2E validates the backend API set; frontend is linted/built/checksummed but not browser-tested in AWS.

Automated acceptance only; do not start staging in this subphase:

```bash
bash tests/scripts/staging_deployment_test.sh
bash tests/scripts/lifecycle_test.sh
bash tests/scripts/production_hardening_test.sh
git diff --check
```

Manual acceptance (offline): inspect a stub-generated task definition and confirm only the selected container image differs; inspect the timing/deployment JSON schema; verify missing/malformed/mutable inputs fail closed.

## 3R.5a — Three-task database bootstrap

### Deliverable

- [ ] Add a validated declarative SQL-runner plan interface. It contains ordered SQL files/commands, credential aliases and a required verification after every mutation; it never contains secret values.
- [ ] Use three Fargate tasks: platform roles/databases; all Auth schema/seed/grants/restricted-user verification; all Items schema/seed/grants/restricted-user verification.
- [ ] Run Auth and Items tasks concurrently after the platform task succeeds.
- [ ] Delete every helper task-definition revision after use.
- [ ] Preserve the existing single-operation runner mode as a temporary fallback until live acceptance.

Automated acceptance only; do not start staging:

```bash
bash tests/scripts/sql_runner_test.sh
bash tests/scripts/lifecycle_test.sh
git diff --check
```

Required failures: missing verification, unknown credential alias, plaintext secret, intermediate SQL failure, restricted-user verification failure and helper cleanup failure.

## 3R.5b — Parallel ECS deployment and measured health tuning

### Deliverable

- [ ] Prepare task definitions before service mutation.
- [ ] Update/wait Auth and Items concurrently; update Gateway only after both are stable.
- [ ] Capture task pull, task start, container health, target health and deployment completion timestamps.
- [ ] Keep circuit breaker rollback and safe rolling settings.
- [ ] After two successful cold runs, calculate health start/grace timing as `ceil(max observed startup-to-healthy / 15s) * 15s + 30s`, clamped to 60–180 seconds. Do not reduce it if either run has a health/start failure.
- [ ] Do not parallelize the three REST E2E tests; their runtime is negligible.

Automated acceptance:

```bash
bash tests/scripts/staging_deployment_test.sh
bash tests/scripts/sql_runner_test.sh
bash tests/scripts/lifecycle_test.sh
git diff --check
```

Manual acceptance (two cold cycles plus one post-tuning cycle; this is the cumulative live checkpoint for 3R.4/5a/5b):

1. Run the mandatory local AWS identity preflight once.
2. For each cycle, resume staging with `--on-demand --defer-services`, deploy one validated set, verify selected digests and run cloud E2E, then pause staging.
3. Confirm exactly three SQL tasks and concurrent Auth/Items timing.
4. Confirm Gateway begins after both backends stabilize.
5. Confirm helper task definitions are deleted and final ECS/ALB/RDS absence is verified.
6. Apply the deterministic health formula only after two clean observations, then run one final cycle.
7. Acceptance target: median staging job at or below 28 minutes and at least 20% faster than the 37m32s baseline. If AWS control-plane time prevents this, retain ephemeral staging and record the bottleneck; do not silently introduce a warm environment.

Rollback: retain the sequential deploy and single-operation SQL fallback until the cumulative checkpoint passes.

## 3R.6 — Release-set v2 contract and pure resolver

### Deliverable

- [ ] Implement v2 schema/validators and deterministic pure resolver without changing live workflows.
- [ ] Implement the component-plan and candidate evidence interfaces defined above.
- [ ] Remove all-components-same-SHA and same-producer-run assumptions from v2.
- [ ] Make missing/invalid base fail safe to full rebuild; make tampered reuse fail closed.
- [ ] Keep v1 code temporarily only for the old live path; no v1 migration or new v1 emission.

Fixtures: first/all build, Auth-only, Gateway-only, frontend-only, common→Items, all reused, failed-main gap, absent/expired/non-ancestor/incomplete base, digest/checksum/SBOM mismatch, mixed trusted producers, mutable tag attempt and deterministic repetition.

Automated acceptance:

```bash
bash tests/scripts/release_set_v2_test.sh
bash tests/scripts/release_contract_test.sh
bash tests/scripts/candidate_evidence_test.sh
git diff --check
```

Manual acceptance (offline): run the resolver over each fixture, inspect all four decisions, verify failed-run accumulation, validate mixed-source v2, tamper with one reused digest and compare two outputs byte-for-byte.

## 3R.7 — Selective main builds and v2 candidate staging

### Deliverable

- [ ] Switch `candidate.yml` to the v2 resolver.
- [ ] Build matrix contains only `action=build` entries; an empty build matrix is valid.
- [ ] Exchange unique per-component artifacts instead of unsafe shared matrix outputs.
- [ ] Reused components are reverified and copied into the new independently consumable evidence bundle.
- [ ] Stage the exact selected backend digests and run full cloud API E2E.
- [ ] Ignore v1 evidence when choosing the v2 base.

Automated acceptance:

```bash
bash tests/scripts/release_set_v2_test.sh
bash tests/scripts/candidate_evidence_test.sh
bash tests/scripts/staging_deployment_test.sh
bash tests/scripts/reusable_workflow_test.sh
git diff --check
```

Manual acceptance (one first-v2 run and one Auth-only run; each staging lifecycle is owned by CI):

1. First v2 candidate has no v2 base and builds all components.
2. Merge an Auth-only change; confirm only Auth builds/pushes.
3. Confirm Items/Gateway/frontend retain original source SHAs and trusted producer evidence.
4. Confirm staging runs the exact selected digest set and E2E passes.
5. Download the candidate bundle and confirm all four artifacts/SBOMs are independently present.
6. Confirm teardown. Rerun once and confirm no immutable bytes are overwritten.

Rollback: temporarily restore v1 candidate emission; do not delete old production workflows.

## 3R.8 — Changed-only promotion, rollback, traceability and retention

### Deliverable

- [ ] Promotion consumes exact candidate run/attempt and computes differences by digest/checksum.
- [ ] Register/deploy only changed backends; preserve unchanged task-definition ARNs.
- [ ] Mint application-version membership tags for all selected backend digests without rebuilding.
- [ ] Publish a complete frontend prefix and update marker for every official version.
- [ ] Use a durable mutation journal for compensation.
- [ ] Rollback selects complete retained v2 releases and changes only differing artifacts; it never writes ECR or reverses the database.
- [ ] Trace commit queries return component-scoped matches; retention keeps the newest ten complete v2 sets.
- [ ] Remove the unused candidate artifact-pointer mechanism unless it becomes an actually verified input.

Automated acceptance:

```bash
bash tests/scripts/promotion_test.sh
bash tests/scripts/rollback_test.sh
bash tests/scripts/release_traceability_test.sh
bash tests/scripts/retention_test.sh
bash tests/scripts/release_set_integration_test.sh
git diff --check
```

Manual acceptance is offline: Auth-only promotion plan, frontend-only rollback plan, mixed production state, tag collision, candidate tamper and controlled verification failure/compensation. No production mutation occurs in this phase.

## 3R.9 — OIDC/IAM role cutover

### Deliverable

- [ ] Update root/service documentation to distinguish local profile rules from CI OIDC rules.
- [ ] Use a small SHA-pinned local composite action for OIDC bootstrap, account allowlist and identity output; do not copy credentials into `~/.aws`.
- [ ] Create/use candidate-build, staging, production-deploy, promotion and rollback roles with exact trust subjects.
- [ ] Scope `iam:PassRole` to exact ECS roles with `iam:PassedToService=ecs-tasks.amazonaws.com`.
- [ ] Candidate role cannot mutate staging/production. Promotion cannot upload layers. Rollback cannot write ECR. Validation has no role.
- [ ] Validate source policies and Access Analyzer findings before apply; apply/read back one role at a time.
- [ ] Keep the old shared role intact but unused until 3R.10 completes.

Automated acceptance:

```bash
bash tests/scripts/ecr_release_tagging_test.sh
bash tests/scripts/ci_permissions_test.sh
bash tests/scripts/reusable_workflow_test.sh
git diff --check
```

Manual acceptance (one IAM session, no staging): identity preflight; validate policies; apply/read back each trust/permission document; inspect a PR/candidate role identity; use policy simulation to prove forbidden operations; inspect CloudTrail role sessions.

## 3R.10 — Live v2 cutover, rollback drill and legacy cleanup

### Deliverable

- [ ] Produce/promote the first all-built v2 official release.
- [ ] Produce/promote one single-component v2 release and prove changed-only mutation.
- [ ] Roll back to the first v2 release and then forward to the second through the rollback workflow.
- [ ] Complete live ECR, ECS, frontend/OAC, OIDC/IAM, CloudTrail, traceability and retention read-backs.
- [ ] Make the first v2 release the rollback floor; do not import v1 releases.
- [ ] Delete v1-only workflows, schema, fixtures and scripts only after the round trip passes.
- [ ] Consolidate duplicate release docs and remove repeated environment identifiers/unused scripts.
- [ ] Remove the old shared IAM role only after every new role is proven and read back.
- [ ] Leave IaC migration as an explicit open issue.

Automated acceptance: run every release, candidate, staging, lifecycle, promotion, rollback, hardening, traceability, retention, permissions and reusable-workflow gate, followed by `git diff --check`.

Manual acceptance:

1. Promote an all-built v2 candidate with protected approval; verify exact ECS digests, frontend marker/checksum, ECR membership tags and GitHub Release manifest.
2. Promote an Auth-only candidate; confirm only Auth gets a new task definition/service deployment.
3. Query by commit, version, digest and running state.
4. Roll back to the first v2 release; verify exact prior artifacts and rollback-result evidence.
5. Return to the newer release through the same rollback workflow.
6. Verify staging resources are absent and all live read-backs are recorded.
7. Only then remove legacy paths and the shared role.

## Pass 3R final acceptance criteria

- [ ] Every `feature/**` push and every PR runs local Docker Compose E2E with no AWS/ECR access.
- [ ] Auth-only main change builds/pushes only Auth; `common` rebuilds Items.
- [ ] Every candidate/release is a complete immutable four-component set with provenance.
- [ ] Staging deploys digest-pinned images, median runtime is at most 28 minutes and at least 20% below baseline, and teardown always verifies absence.
- [ ] Promotion/rollback mutate only differing artifacts and never rebuild.
- [ ] CI uses OIDC temporary credentials without a named AWS profile; local commands keep the mandatory profile/region.
- [ ] No untrusted GitHub expression is interpolated directly into shell code.
- [ ] Workflow YAML owns orchestration; scripts/config own AWS operations, jq policy logic and environment identifiers.
- [ ] One real v2 promotion and rollback round trip succeeds before legacy deletion.

## Deferred Pass 4 task — automatic releases

- [ ] After at least five consecutive successful manual v2 promotions and one rollback drill, adopt `release-please` for one application release PR based on Conventional Commits. Merging that PR assigns SemVer and automatically promotes the newest exact verified candidate set. It must not create independent component versions or rebuild during promotion.
