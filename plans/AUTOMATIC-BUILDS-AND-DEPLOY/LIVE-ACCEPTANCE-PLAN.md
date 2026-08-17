# Phase 7 — Cutover + Live Acceptance Plan

**Plan:** `DELIVERY-SYSTEM-IMPLEMENTATION-PLAN.md` §7 / `OPERATIONS.md` OP-CUT-01/02
**Status:** Phase 4 COMPLETE (2026-08-17, run `32055042796` — full staging lifecycle COMPLETE: database reset/seed/access passed, deploy digests converged, COMPATIBILITY bootstrap-exception, cloud E2E passed, cleanup passed, RDS stopped, services 0/0/0). Next: Phase 5 production start/stop proof.
**Who acts:** repository owner (GitHub approvals + local AWS commands); GitHub Actions (workflow execution). Steps are written for both actors.
**Normative inputs:** `SPEC.md` (esp. §4.4, AD-17/18), `OPERATIONS.md` (OP-CUT), `VERIFICATION.md` (VR-READY-01/02, VR-SEC-01, VR-STG-02, VR-PRO, VR-REC-03, VR-OPS), phase reports PHASE-04/05/06 "known limitations / live-pass deferrals", repo `AGENTS.md`, `docs/CI_CD_GOTCHAS.md`

---

## Top-down flow

```text
 PHASE 0  READ-ONLY preconditions + inventories + owner sign-off
   |
 PHASE 1  GitHub settings        (owner)        branch protection / production Environment
   |
 PHASE 2  IAM roles + OIDC trust + ECR immutability   (owner, local AWS)
   |
 PHASE 3  MERGE feature/cicd-release-redesign -> main  (owner; IRREVERSIBLE)
   |        +- makes all greenfield workflows dispatchable (workflow_dispatch
   |           is indexed from the default branch only - AGENTS.md rule)
   |
 PHASE 4  LIVE STAGING: greenfield/** push -> complete feature candidate
   |        -> stage-candidate.yml full lifecycle -> COMPLETE + cleanup verified
   |        (bootstrap exception expected: no official release exists yet)
   |
 PHASE 5  Production start/stop proof  (legacy pause/resume playground scripts)
   |
 PHASE 6  Neutralize legacy production-mutation workflows      (owner; revertable)
   |        +- required by the greenfield bring-up guards, which refuse
   |           while promote-release.yml contains finalize-release.sh or
   |           rollback-release.yml contains deploy-rollback.sh
   |
 PHASE 7  TRIGGER SWAP: legacy push triggers off + e2e-staging removed,
   |        ci.yml push triggers greenfield/** -> main + feature/**   (owner; revertable)
   |        +- merge push itself fires ci.yml on main -> FIRST MAIN CANDIDATE
   |
 PHASE 8  FIRST LIVE PROMOTION -> release-0001   (owner approves production Environment)
   |
 PHASE 9  SECOND PROMOTION -> release-0002      (small main change; owner approval)
   |
 PHASE 10 ROLLBACK DRILL  release-0002 -> release-0001 -> release-0002
   |        (two owner approvals; separate rollback results; DB never touched)
   |
 PHASE 11 Retention apply + ECR tag->digest anchors + artifact completeness
   |        + read-only production journeys + separation/CloudTrail read-backs
   |
 PHASE 12 Observe stable operation: feature/** push path, live reconcile
   |        (no-op + ownerless-RDS stop), legacy inertness proof
   |
 PHASE 13 DELETE LEGACY  (owner approves deletion inventory; IRREVERSIBLE)
   |        +- legacy workflows, release/ tree, legacy scripts/policies/docs,
   |           guard removal, workflow renames
   |
 PHASE 14 Documentation update + VR-READY-01/02 end-state verification
```

**Sequencing decision (why this order):** `OPERATIONS.md` OP-CUT-02 places "disable legacy" *after* the live drill, but `DELIVERY-SYSTEM-IMPLEMENTATION-PLAN.md` §7 places "disable legacy triggers → verify no legacy run can mutate AWS" *before* the live promotion. The §7 order is the only executable one, for two hard reasons:

1. **Bring-up guards fail closed.** Both greenfield production workflows refuse (exit 1, before any AWS credentials) while the legacy `promote-release.yml` contains the `finalize-release.sh` marker or `rollback-release.yml` contains `deploy-rollback.sh`. The legacy `production-mutation` concurrency group does **not** serialize with the greenfield `production` group, so a live greenfield promotion is impossible while those legacy mutation paths exist.
2. **Main-class candidates only exist after the trigger swap.** `ci.yml` classifies a push as `main` only for `refs/heads/main`, and its push trigger currently matches only `greenfield/**` (those classify as `feature`, staging-only per AD-03). A production-eligible candidate therefore requires expanding `ci.yml` to `main` — forbidden while legacy `build-and-deploy.yml` still owns main pushes (identical `sha-<fullsha>` tags would be pushed twice into the same immutable ECR repositories on one commit, OP-CUT-01).

Recoverability under AD-18 is preserved because Phases 6–7 are **git-revertable edits, not deletions**: legacy files remain in the repository, inert, until the separately owner-approved deletion (Phase 13). The legacy push/staging path stays fully alive through Phase 7, so application delivery has a working fallback while the greenfield production path is proven live. Every irreversible step is flagged with an owner checkpoint.

---

## Global execution rules

1. **Identity preflight.** Every local AWS phase begins with `aws sts get-caller-identity --profile dpm-profile --region eu-north-1` and every command carries `--profile dpm-profile --region eu-north-1`. If the session is expired, stop and ask the owner to re-authenticate (never retry).
2. **Read-backs.** Every AWS create/put/delete is immediately followed by a describe/get/list confirming the change (AGENTS.md). Steps list the expected read-back.
3. **Bounded waits.** Use `aws ecs wait services-stable`, `aws rds wait db-instance-available|stopped`, or loops bounded to < 2 minutes per bash invocation. No open-ended sleep loops (AGENTS.md operational rule 1).
4. **No secrets** anywhere in commands, logs, or records; secrets stay as `secrets[].valueFrom` full-ARN references (AGENTS.md operational rule 2).
5. **Concurrency discipline.** Greenfield `staging` and `production` concurrency groups serialize greenfield runs. Legacy runs have their own groups — the plan sequences them in time so old and new never overlap (OP-CUT-01). Never dispatch a greenfield staging run while a legacy `e2e-staging` run is active; never dispatch a greenfield production run while a legacy production-mutation path exists (guards + Phase 6 enforce the latter).
6. **Step markers.** `[READ-ONLY]` no mutation; `[MUTATION]` reversible change; `[IRREVERSIBLE]` cannot be undone (marked *revertable in git* where applicable); `[OWNER]` repository owner must act; `[ACTIONS]` GitHub Actions executes.
7. **Retention coupling.** Candidate artifacts live 30 days; staging-record artifacts 14 days. Therefore: stage a candidate within 30 days of its run, and promote within 14 days of its staging run (the promotion consumes the `staging-record-<run>-<attempt>` artifact). Rollback consumes GitHub Release assets (indefinite retention) — never candidate artifacts.
8. **Reference table (real identifiers):**

| Item | Value |
|---|---|
| Account / region | `799111666795` / `eu-north-1` |
| OIDC provider | `arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com` |
| Repo subject base | `repo:Djimi@8793507/OnlineShop-full-stack@1097550215` |
| Roles | `github-actions-candidate-build` (verify exists), `github-actions-staging`, `github-actions-production`, `github-actions-production-preflight` (create) |
| ECR repositories | `onlineshop-auth`, `onlineshop-items`, `onlineshop-api-gateway` |
| Production | cluster `onlineshop-cluster`; services `onlineshop-auth`, `onlineshop-items`, `onlineshop-api-gateway`; DB `onlineshop-postgres-db`; bucket `onlineshop-frontend-799111666795`; live marker `release.json`; prefix `_releases/`; distribution `EPS8MI3FV3B7X`; ALB `onlineshop-alb`; TG `arn:aws:elasticloadbalancing:eu-north-1:799111666795:targetgroup/onlineshop-gateway-tg/29ba79a624079a04` |
| Staging | cluster `onlineshop-staging-cluster`; services `onlineshop-{auth,items,api-gateway}-staging`; DB `onlineshop-staging-postgres`; ALB `onlineshop-staging-v2-alb`; TG `onlineshop-staging-tg-v2/8a9b0471c381e60b`; SQL runner family `onlineshop-staging-sql-runner` |
| Greenfield workflows | `ci.yml`, `_java-service.yml`, `stage-candidate.yml`, `reconcile-staging.yml`, `promote-release-greenfield.yml`, `rollback-release-greenfield.yml` |
| Legacy workflows | `build-and-deploy.yml`, `promote-release.yml`, `rollback-release.yml` |
| Legacy mutation markers | `finalize-release.sh` (legacy promote), `deploy-rollback.sh` (legacy rollback), `e2e-staging` (legacy staging) |
| Concurrency groups | greenfield `staging`, greenfield `production`; legacy `production-mutation` |
| Artifact names | `candidate-manifest-<run>-<attempt>`, `frontend-archive-<run>-<attempt>`, `sboms-<run>-<attempt>`, `test-results-<run>-<attempt>`, `staging-record-<run>-<attempt>`, `preflight-report-<run>-<attempt>`, `promotion-snapshot-<run>-<attempt>`, `promotion-evidence-<run>-<attempt>`, `rollback-snapshot-<run>-<attempt>`, `rollback-evidence-<run>-<attempt>`, `recovery-result-<run>-<attempt>`, `reconcile-record-<run>-<attempt>` |

---

## Phase 0 — Preconditions, read-only inventory, owner sign-off

**Goal:** prove the repository and platform are in the assumed state before any mutation, and get owner sign-off on the sequencing.

**Prerequisites:** none (this is the entry phase).

**Steps:**

1. `[READ-ONLY]` Working tree: commit the pending Phase-6 compensation-verification fixes to the two greenfield workflows (`git status` must be clean) and re-run the offline gates: `pytest delivery/tests -q`, `ruff check delivery`, `actionlint` on all greenfield workflows, `zizmor` on `promote-release-greenfield.yml` + `rollback-release-greenfield.yml`, plus the affected service tests per `docs/TESTING_STRATEGY.md`.
2. `[READ-ONLY]` AWS identity preflight (owner).
3. `[READ-ONLY]` Platform inventory (every command with `--profile dpm-profile --region eu-north-1`):
   - `aws iam list-roles` → which of the four `github-actions-*` roles already exist; for each, `aws iam get-role`, `aws iam list-attached-role-policies`, `aws iam get-role-policy` (trust read-back).
   - `aws iam get-open-id-connect-provider --open-id-connect-provider-arn arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com`.
   - `aws ecr describe-repositories --repository-names onlineshop-auth onlineshop-items onlineshop-api-gateway` → current `imageTagMutability` (expected today: not yet `IMMUTABLE_WITH_EXCLUSION` — the AGENTS.md live-pass item).
   - `aws ecs describe-clusters` for both clusters; `aws ecs describe-services` for the six service names; `aws ecs describe-task-definition` for current production task definitions → confirm task-role ARNs `onlineshop-{auth,items,gateway}-task` (production-iam README checklist item 2).
   - `aws rds describe-db-instances` for `onlineshop-postgres-db` and `onlineshop-staging-postgres` → state (available/stopped); `PubliclyAccessible` must be false.
   - `aws elbv2 describe-load-balancers --names onlineshop-alb onlineshop-staging-v2-alb` → both exist (the staging ALB is persistent infra the lifecycle expects).
   - `aws s3api get-bucket-policy --bucket onlineshop-frontend-799111666795`; `aws s3api list-objects-v2 --bucket onlineshop-frontend-799111666795 --prefix _releases/ --max-items 20` → current frontend state and legacy markers.
   - `aws cloudfront get-distribution --id EPS8MI3FV3B7X` → origin config and enabled state.
4. `[READ-ONLY]` GitHub inventory (owner, `gh api` or UI):
   - Branch protection on `main` (require PR; force-push disallowed — SPEC §4.4).
   - `gh api repos/:owner/:repo/environments` → `production` exists and its reviewers list (legacy workflows already reference it).
   - `gh release list` → baseline numbering: the engine allocates the next never-reused `release-NNNN`; record the highest existing number (if legacy releases `release-0001..0006` exist, the first greenfield release is expected to be `release-0007`; SPEC's example uses `release-0007`).
   - `gh workflow list` → legacy + greenfield workflows present on `main`.
   - Actions settings: fork PR runs get the read-only default token; no repository secrets carry AWS long-lived credentials (OP-GEN-04).
5. Map SPEC §4.4 assumptions to the evidence above (each gets its VR-READY-01 row in Phase 14).

**Expected evidence + read-backs:** inventory notes recorded against this plan (worktree-local, no secrets). Any deviation from the reference table is a blocking finding resolved before Phase 1.

**Failure behavior:** expired AWS session → owner re-authenticates. Missing staging ALB → restore via legacy `scripts/resume-staging.sh` (allowed pre-Phase-6; note it recreates staging RDS, which the lifecycle resets anyway). Surprise role/policy drift → owner decision before Phase 2.

**Owner checkpoint 0:** owner signs off on this plan's sequencing (including the risk decisions in the final section) and confirms AWS re-authentication.

---

## Phase 1 — GitHub settings (owner)

**Goal:** satisfy SPEC §4.4 platform assumptions: protected `main`; protected `production` Environment with owner-only required reviewers.

**Prerequisites:** Phase 0 complete.

**Steps:**

1. `[OWNER] [MUTATION]` Branch protection on `main`: require a pull request before merging, require owner review, disallow force-push (SPEC §4.4 "Protected main disallows force-push"; the protected-main push requirement is what makes the `main` candidate class trusted per AD-03).
2. `[OWNER] [MUTATION]` Environment `production`: required reviewers = owner only (AD-10). Both legacy and greenfield promotion/rollback workflows reference this environment; after Phase 6 the legacy users are inert.
3. `[OWNER] [READ-ONLY]` Verify Actions settings: fork-PR workflows run with read-only `GITHUB_TOKEN` by default; no AWS long-lived credentials in repository secrets.
4. `[READ-ONLY]` Read-backs: `gh api repos/:owner/:repo/branches/main/protection` (expect `allow_force_pushes.enabled == false`, `required_pull_request_reviews.required_approving_review_count >= 1`) and `gh api repos/:owner/:repo/environments/production` (expect `protection_rules[].reviewers` containing the owner).

**Expected evidence:** the two API read-backs.

**Failure behavior:** settings changes are reversible via the same UI/API; nothing downstream starts until they are confirmed.

**Owner checkpoint 1:** settings confirmed by the owner.

---

## Phase 2 — IAM roles, OIDC trust, ECR immutability

**Goal:** create the AD-17 trust boundaries live from the delivery-owned desired-state policies (`delivery/staging-iam/staging-deploy-policy.json`, `delivery/production-iam/production-deploy-policy.json` — authoritative), with OIDC trust mirroring `github-actions-role-layout.md` subject patterns; make candidate/official ECR tags immutable.

**Prerequisites:** Phases 0–1.

### 2.1 Trust-policy subject matrix

Base: `repo:Djimi@8793507/OnlineShop-full-stack@1097550215`. Principal: `arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com`.

| Role | Subjects | Why |
|---|---|---|
| `github-actions-candidate-build` (verify existing) | `:ref:refs/heads/main`, `:ref:refs/heads/feature/*`, `:ref:refs/heads/greenfield/*` | ci.yml push-publish job; `greenfield/*` required **during bring-up** (Phase 4), removable after Phase 13 |
| `github-actions-staging` | `:ref:refs/heads/main`, `:ref:refs/heads/feature/*` | `stage-candidate.yml` dispatch + `reconcile-staging.yml` schedule/dispatch, both resolved from the default branch |
| `github-actions-production` | `:ref:refs/heads/main`, `:ref:refs/heads/feature/*`, `:environment:production` | `promote`/`rollback` jobs (environment subject) **and** `compensate` jobs (no environment key → ref subject) |
| `github-actions-production-preflight` | `:ref:refs/heads/main`, `:ref:refs/heads/feature/*` | `preflight` jobs have **no** environment key → ref subject only; never add the environment subject here |

The environment subject is immutable-format and validated live, never guessed: the first successful assumption of each role by its workflow (Phases 4 and 8) IS the live trust read-back, and `aws sts get-caller-identity` inside the job proves the role identity.

### 2.2 Steps

1. `[READ-ONLY]` Verify `github-actions-candidate-build` exists (Phase 3 already exercised it on a `greenfield/**` push). Compare its attached policy against the desired scope: `ecr:GetAuthorizationToken` on `*` only; `ecr:BatchCheckLayerAvailability`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, `ecr:PutImage`, `ecr:BatchGetImage`, `ecr:DescribeImages` on the three repository ARNs; **no** `ecs:`/`s3:`/`cloudfront:`/`rds:`/`iam:` actions. On drift, `[MUTATION]` owner aligns it + read-back.
2. `[MUTATION]` Create `github-actions-staging` with inline policy from `delivery/staging-iam/staging-deploy-policy.json`. Read-backs: `aws iam get-role`, `aws iam get-role-policy` (byte-compare), `aws accessanalyzer validate-policy` on both documents.
3. `[MUTATION]` Create `github-actions-production` with inline policy from `delivery/production-iam/production-deploy-policy.json`. Before attach: confirm the live production task-role ARNs (`onlineshop-{auth,items,gateway}-task`, Phase 0 step 3) match the PassRole resources; update the policy resource list if they differ (production-iam README checklist item 2). Read-backs as above.
4. `[MUTATION]` Create `github-actions-production-preflight` with an inline read-only policy derived from the `promote-release-greenfield.yml` `PRODUCTION_PREFLIGHT_ROLE_ARN` comment — exactly the production-deploy scope **minus every mutation action**:

   | Area | Actions (read-only) | Resource |
   |---|---|---|
   | ECR | `ecr:BatchGetImage`, `ecr:DescribeImages` | three repository ARNs |
   | ECS | `ecs:DescribeServices`, `ecs:ListTasks`, `ecs:DescribeTasks`, `ecs:DescribeTaskDefinition` | production cluster/service/task/TD-family ARNs |
   | RDS | `rds:DescribeDBInstances` | production DB ARN |
   | S3 | `s3:GetObject`, `s3:HeadObject`, `s3:ListBucket` | frontend bucket |
   | CloudFront | `cloudfront:GetDistribution`, `cloudfront:GetInvalidation` | `EPS8MI3FV3B7X` |
   | ELB | `elasticloadbalancing:DescribeLoadBalancers`, `elasticloadbalancing:DescribeTargetHealth` | ALB/TG ARNs |

   Zero mutation actions (no `ecs:UpdateService`, no `ecr:PutImage`, no `s3:PutObject`, no `cloudfront:CreateInvalidation`, no `iam:*`, no `secretsmanager:*`). Read-backs + Access Analyzer as above.
5. `[MUTATION]` ECR immutability (AGENTS.md live-pass item; SPEC §4.4): set `imageTagMutability: IMMUTABLE_WITH_EXCLUSION` with exclusion filters exactly `main-latest` (WILDCARD) and `branch-*` (WILDCARD) on the three repositories — the same contract the greenfield system needs (`sha-*` and `release-*` never overwritten; ci.yml legitimately pushes mutable `main-latest`/`branch-*`). Per repo:
   `aws ecr put-image-tag-mutability --repository-name <repo> --image-tag-mutability IMMUTABLE_WITH_EXCLUSION --exclusion-patterns 'WILDCARD=main-latest' 'WILDCARD=branch-*' --profile dpm-profile --region eu-north-1`
   Read-back: `aws ecr describe-repositories --repository-names <repo>` shows `imageTagMutability: IMMUTABLE_WITH_EXCLUSION` + the exclusions.

**Expected evidence:** four roles with `get-role`/`get-role-policy` read-backs; Access Analyzer findings empty; ECR describe-repositories read-back per repo.

**Failure behavior:** any create fails → fix the document, re-create (roles are not yet referenced by anything; idempotent re-runs fine). Trust-policy mismatch only manifests at Phase 4/8 first assumption — the run fails with `sts:AssumeRoleWithWebIdentity` denial; fix the trust subject and re-run the workflow (no AWS mutation happened).

**Owner checkpoint 2:** owner reviews and executes (or approves execution of) every create; the production PassRole resource list is confirmed against live task definitions.

---

## Phase 3 — Merge the feature branch to main (IRREVERSIBLE)

**Goal:** make all greenfield workflows dispatchable (workflow_dispatch is indexed from the default branch only — AGENTS.md GitHub Actions rule 2) while ci.yml stays `greenfield/**`-only and legacy keeps owning `main` + `feature/**` pushes.

**Prerequisites:** Phases 0–2.

**Steps:**

1. `[OWNER] [IRREVERSIBLE]` Open the PR `feature/cicd-release-redesign → main` (contains the Phase-0 committed compensation fixes). The PR itself runs ci.yml validation jobs (read-only, no AWS). Owner merges after review.
2. `[READ-ONLY]` Post-merge verification:
   - `gh workflow list` shows the six greenfield workflows on `main` with `workflow_dispatch` triggers.
   - The merge push fired legacy `build-and-deploy.yml` on `main` (`e2e-staging` + `candidate-evidence` jobs). **Wait for that run to fully complete, including staging teardown**, before Phase 4 — the legacy staging path and the greenfield `staging` concurrency group do not serialize; time-sequencing is operator responsibility until Phase 7.
   - `reconcile-staging.yml` cron (`*/15`) starts running on `main`; its bring-up guard no-ops (`exit 0` before AWS credentials) while `build-and-deploy.yml` still contains `e2e-staging`.

**Expected evidence:** merge commit on `main`; `gh workflow list` output; the legacy main-push run completes; reconcile runs show the guard notice in their logs.

**Failure behavior:** if the legacy staging run fails, diagnose with its artifacts (`staging-failure-<sha>-<attempt>`) before any greenfield staging. The merge can be undone only via a revert PR (owner decision).

**Owner checkpoint 3:** merge approval (branch protection review) — the first irreversible step.

---

## Phase 4 — Live candidate publication + staging lifecycle

**Goal:** prove the greenfield push path (VR-CAND-01) and one complete OP-STG lifecycle (VR-STG-02) on real AWS, including the AD-15 bootstrap exception.

**Prerequisites:** Phase 3; the merge-triggered legacy staging run has fully completed.

**Steps:**

1. `[ACTIONS] [MUTATION-ECR]` Owner pushes a real branch `greenfield/live-acceptance-1` from `main` HEAD (or with a trivial change). ci.yml runs: service/frontend tests → local Compose E2E → publish job builds Auth/Items/Gateway/frontend from one SHA, pushes `sha-<fullsha>` + `branch-greenfield-live-acceptance-1` tags, reads back ECR digests, generates 4 pinned-Syft SPDX SBOMs, emits + validates the candidate manifest (`--class feature`), uploads the four artifacts.
   - Record `CANDIDATE_RUN_ID` / `CANDIDATE_RUN_ATTEMPT` from the run URL.
   - Live trust read-back: the publish job's successful `configure-aws-credentials` + `aws sts get-caller-identity` inside the job prove the `github-actions-candidate-build` trust for `:ref:refs/heads/greenfield/*`.
2. `[READ-ONLY]` Verify candidate completeness for the exact run/attempt (VR-OUT-02): the four artifacts `candidate-manifest-<run>-<attempt>`, `frontend-archive-<run>-<attempt>`, `sboms-<run>-<attempt>`, `test-results-<run>-<attempt>` exist; manifest digests match `aws ecr batch-get-image --image-ids imageTag=sha-<fullsha>` read-backs; frontend `artifactDigest`/`contentChecksum` recompute.
3. `[OWNER] [MUTATION-staging]` Dispatch `stage-candidate.yml` with `candidate_run_id`/`candidate_run_attempt` from step 1. The lifecycle runs: ownership marker acquire on `onlineshop-staging-postgres` (RDS tag `onlineshop:staging-owner`, TTL 3h) → RDS start + schema/grants/seed reset through the `onlineshop-staging-sql-runner` family with per-step SQL read-backs → digest-pinned deployment to the `onlineshop-*-staging` services → COMPATIBILITY (expected: `bootstrapException` — no official release exists yet; drafts/prereleases filtered, newest-without-manifest fails closed) → cloud E2E against the staging ALB (`E2E_BASE_URL`) → EVIDENCE → stop services/RDS → CLEANUP_VERIFY → COMPLETE.
4. `[READ-ONLY]` Verify the staging record: artifact `staging-record-<run>-<attempt>` has `phase: COMPLETE`, `e2e.conclusion: passed`, `cleanup.conclusion: passed`, `compatibility.conclusion: bootstrap-exception`. Then read-backs: `aws rds describe-db-instances --db-instance-identifier onlineshop-staging-postgres` → `DBInstanceStatus: stopped`; `aws ecs describe-services --cluster onlineshop-staging-cluster --services onlineshop-auth-staging onlineshop-items-staging onlineshop-api-gateway-staging` → `runningCount: 0`.
5. `[READ-ONLY]` Cost observation: no running staging RDS/ECS after cleanup (the next reconcile cron run confirms via its record).

**Expected evidence:** complete feature candidate + complete staging record + stopped-state read-backs. This is the live half of VR-CAND-01 and VR-STG-02.

**Failure behavior (OP-STG-04):** any phase failure joins the evidence-and-cleanup path — diagnostics captured before destructive cleanup; cleanup runs only under verified ownership; `StagingCleanupFailure` is a distinct, visible failure and blocks promotion. A hard kill strands the ownership marker: TTL 3h + reconcile cron is the recovery; the candidate stays unpromotable until a clean COMPLETE record exists.

**Owner checkpoint 4:** owner dispatches the staging run and reviews the record (no GitHub approval — staging is not approval-gated).

---

## Phase 5 — Production start/stop proof

**Goal:** prove production pause/resume works before any greenfield promotion, using the established (retained) playground scripts.

**Prerequisites:** Phases 0–4; production currently running.

**Steps (owner, local):**

1. `[MUTATION-production]` `bash scripts/pause-playground.sh` → ECS services scaled to 0, production ALB deleted. Read-backs: `aws ecs describe-services --cluster onlineshop-cluster --services onlineshop-auth onlineshop-items onlineshop-api-gateway` → `runningCount: 0`; `aws elbv2 describe-load-balancers --names onlineshop-alb` → not found (expected after pause).
2. `[MUTATION-production]` `bash scripts/resume-playground.sh` → ALB recreated, services scaled back, DB running, health verified by the script. Read-backs: describe-services `runningCount: 1` (desired-count-one Spot posture), `aws elbv2 describe-load-balancers --names onlineshop-alb`, gateway health check.
3. `[READ-ONLY]` Sequencing rule: pause/resume never overlaps greenfield promotion/rollback (different lock domains). This phase completes before Phase 8.

**Expected evidence:** script progress logs (UTC timestamped) + the read-backs. Typical durations: pause 1–2 min, resume 3–8 min (AGENTS.md).

**Failure behavior:** a failed resume is diagnosed with the script's numbered step logs; production promotion stays blocked until production is running and healthy.

**Owner checkpoint 5:** owner decides timing; no GitHub approval required (established scripts), but this mutates production.

---

## Phase 6 — Neutralize legacy production-mutation workflows (revertable in git)

**Goal:** satisfy the greenfield bring-up guards and eliminate the OP-CUT-01 concurrent-mutation risk on the production path, while keeping the files in git for revert-recoverability (AD-18).

**Prerequisites:** Phases 0–5.

**Steps:**

1. `[OWNER] [MUTATION]` PR (owner review + merge): replace the mutation jobs in the two legacy workflows so the marker strings disappear and no AWS path remains:
   - `.github/workflows/promote-release.yml` → remove the step invoking `release/bin/finalize-release.sh` (and the whole mutation job); leave a minimal inert file (name + retired notice, no triggers).
   - `.github/workflows/rollback-release.yml` → remove the step invoking `release/bin/deploy-rollback.sh` (and the whole mutation job); same minimal inert file.
   - Keep the legacy files present (not deleted) — the guards are fail-closed on file *content*, and deletion is a separate Phase-13 action.
2. `[READ-ONLY]` Verification:
   - `grep -c 'finalize-release.sh' .github/workflows/promote-release.yml` → `0`; `grep -c 'deploy-rollback.sh' .github/workflows/rollback-release.yml` → `0`.
   - Repo-wide: `grep -rn 'finalize-release.sh\|deploy-rollback.sh' .github/workflows/` → only the (already neutralized) legacy files or nothing.
   - Dispatch the legacy workflows once → they fail/refuse without any AWS call (CloudTrail spot-check optional).
   - Re-dispatch a greenfield `preflight` job (e.g., a dry promote preflight with any syntactically valid inputs — it will fail later in the engine, but must pass the bring-up guard step first) → guard step prints "legacy production-mutation paths inactive; preflight proceeds".
3. `[READ-ONLY]` Note: `build-and-deploy.yml` remains fully active (push builds + `e2e-staging`) — the legacy fallback for application delivery until Phase 7.

**Expected evidence:** the two greps return 0; guard pass observed in a greenfield run log; a legacy dispatch attempt fails without AWS mutation.

**Failure behavior:** if a greenfield guard still refuses, a marker string remains somewhere — find it with the grep and fix; nothing else was mutated.

**Owner checkpoint 6:** owner approves this neutralization explicitly — the legacy production fallback is now inert (restorable via `git revert`).

---

## Phase 7 — Trigger swap: legacy push triggers off, ci.yml expands to main + feature/** (revertable in git)

**Goal:** make `main` and `feature/**` pushes produce greenfield candidates; per OP-CUT-01 both trigger sets must never match the same push while either can mutate AWS.

**Prerequisites:** Phase 6 (legacy production-mutation paths neutralized).

**Steps:**

1. `[OWNER] [MUTATION]` PR (owner review + merge) changing exactly two workflows:
   - `.github/workflows/ci.yml`: `push.branches: ['greenfield/**']` → `['main', 'feature/**']`; update the trigger-isolation comment block.
   - `.github/workflows/build-and-deploy.yml`: remove the `push` triggers and remove the `e2e-staging` + `candidate-evidence` jobs (their needs chains included); leave the file as an inert stub (retained in git until Phase 13). This also flips `reconcile-staging.yml`'s bring-up guard (its marker string `e2e-staging` disappears) so reconcile goes **live** from this point.
   - Optionally include one small application change (e.g., a version string or README line) in the same PR so the first production release carries real application content, not only YAML.
2. `[ACTIONS]` The merge push to `main` fires the expanded `ci.yml` → **FIRST MAIN CANDIDATE** (`--class main`, mutable tag `main-latest`, immutable `sha-<fullsha>`). Record its `CANDIDATE_RUN_ID`/`CANDIDATE_RUN_ATTEMPT`.
3. `[READ-ONLY]` Verification:
   - `gh run list --workflow ci.yml` shows the main-push run; all validation jobs + publish succeeded.
   - The four artifacts exist for the exact run/attempt; `aws ecr batch-get-image --image-ids imageTag=sha-<fullsha>` resolves on all three repositories exactly once (no collision — legacy triggers are gone).
   - `gh run list --workflow build-and-deploy.yml` shows no run for this push.
   - No legacy workflow has any remaining `push`/`schedule` trigger (`grep -A4 '^on:' .github/workflows/build-and-deploy.yml`).
4. `[READ-ONLY]` Record that legacy `workflow_dispatch` on `build-and-deploy.yml` is gone too (the stub has no triggers); the legacy path can no longer mutate ECR/staging at all — revert is the only revival.

**Expected evidence:** first main candidate with full artifact set; ECR sha-tag uniqueness; no legacy run for the push.

**Failure behavior:** ci.yml main run fails → diagnose like any CI failure; no production impact; re-push after fix (a new SHA → a new candidate; the immutable sha-tag is never overwritten). If something must be undone, `git revert` the PR restores legacy triggers; ci.yml then reverts to `greenfield/**`-only (do this only with owner approval, and never while the other side can mutate the same refs).

**Owner checkpoint 7:** owner approves the swap — the legacy application-delivery fallback is now inert (restorable via `git revert`). No greenfield production promotion was possible before this point because no main candidate could exist.

---

## Phase 8 — First live promotion → first official release (owner approval)

**Goal:** OP-PRO-01 end-to-end: exact-candidate staging gate → protected owner approval → ordered observed deployment → read-only verification → official finalization → first greenfield `release-NNNN`.

**Prerequisites:** Phase 7 main candidate (recorded run/attempt); production running (Phase 5); roles live (Phase 2).

**Steps:**

1. `[OWNER] [MUTATION-staging]` Dispatch `stage-candidate.yml` with the **main** candidate's run id/attempt → full staging lifecycle for the exact candidate (bootstrap exception still expected — no official release exists yet). Record the staging run id; verify `staging-record-<run>-<attempt>` is COMPLETE with E2E passed + cleanup passed.
2. `[OWNER] [ACTIONS]` Dispatch `promote-release-greenfield.yml` with inputs `candidate_run_id`, `candidate_run_attempt`, `staging_run_id` (digits-only — the workflow regex-validates them; digests/tags/ARNs are never entered by hand).
3. `[ACTIONS]` Job A `preflight` (read-only, `github-actions-production-preflight`, no environment, no concurrency group): downloads the exact run/attempt artifacts, captures the read-only production snapshot, runs engine preflight (candidate eligibility, exact staging gate, ECR digest revalidation, snapshot consistency, AD-11 newer-candidate warning, OP-DB migration-ownership gate), prints the **approval summary with the exact SHA**. Live trust read-back: successful role assumption by the preflight job proves the preflight-role trust for the `refs/heads/main` subject.
4. `[OWNER]` Owner reviews the approval summary (exact SHA, staging evidence, warnings) and **approves the protected `production` Environment** (required-reviewer approval; this is the AD-10 control point).
5. `[ACTIONS]` Job B `promote` (environment `production`, concurrency group `production`, `github-actions-production`): fresh snapshot under the lock → FULL preflight with `--previous-report` (approval-identity drift aborts pre-mutation) → deploy backends (Auth+Items) → deploy gateway → deploy frontend (immutable `_releases/<provisional>/` prefix, checksum proven BEFORE live switch, marker names the candidate) → `verify production` read-only → approver/approval-timestamp from `actions/runs/{run}/approvals` (never `github.actor`) → `finalize`: allocate next never-reused `release-NNNN`, mint ECR `release-*` tags from recorded manifest bytes with digest read-back, switch to the identity-equivalent official marker, publish the GitHub Release with manifest + 4 pinned SBOMs, audit the rollback window.
6. `[READ-ONLY]` Post-promotion verification (operator, local):
   - `gh release view release-NNNN` → manifest + 4 SBOM assets attached.
   - `aws ecr batch-get-image` on `release-NNNN` tags in all three repos → digests equal the manifest's digests (ECR tag→digest anchors).
   - `aws s3api get-object --bucket onlineshop-frontend-799111666795 --key release.json` → official marker names `release-NNNN`; `_releases/release-NNNN/release.json` prefix marker exists; fetch the marker through CloudFront (`curl https://<distribution-domain>/release.json`).
   - Read-only journeys: gateway health, frontend marker/content via CloudFront, `GET /api/v1/items`.
   - Window audit result in `finalize-report.json`: young-system rule — current + up to 3 previous; zero previous must be complete (trivially true) — `rollbackCapableAtPublication` recorded honestly.

**Expected evidence:** promotion run green; GitHub Release `release-NNNN`; `promotion-evidence-<run>-<attempt>` artifact (snapshot, preflight, verification, approval evidence, release-manifest, finalize report); all read-backs above.

**Failure behavior:** pre-mutation failure (guard/inputs/preflight drift) → job fails, evidence artifacts uploaded, nothing mutated; re-dispatch after fixing the cause. Post-mutation defined failure → `compensate` job (automatic, approval-free, same production group) restores exactly the completed components from the pre-mutation snapshot and re-verifies read-only; ambiguous mid-step kill → evidence preserved, manual decision (OP-REC-01), **do not guess**. Partial finalization → exact-match resume via re-running `finalize` with identical inputs (OP-FIN-02), never a fresh release id.

**Owner checkpoint 8:** the `production` Environment approval itself (mandatory, recorded as `approvedBy`).

---

## Phase 9 — Second promotion → release-NNNN+1

**Goal:** create the second official release so the N → N-1 drill has a target; also exercises the AD-15 previous-official-frontend journey for the first time.

**Prerequisites:** Phase 8 complete; within 30 days of the Phase-8 candidate (candidate retention) and 14 days of its staging record (not relevant here — each release has its own).

**Steps:**

1. `[ACTIONS]` Owner pushes a small change to `main` (e.g., a version bump or README line) → ci.yml produces **main candidate #2** (record run/attempt). This also re-proves the main push path post-swap.
2. `[OWNER] [MUTATION-staging]` Dispatch `stage-candidate.yml` for candidate #2. Now `release-NNNN` exists, so COMPATIBILITY runs the real AD-15 journey: previous official frontend (`release-NNNN` frontend from the retained prefix) against candidate #2 backends, then candidate-frontend cloud E2E. Verify the staging record's `compatibility.conclusion` is not `bootstrap-exception`.
3. `[OWNER] [ACTIONS]` Dispatch `promote-release-greenfield.yml` (candidate #2 run/attempt + staging run id) → preflight → **owner approves** → promote → finalize → `release-NNNN+1`.
4. `[READ-ONLY]` Verify window audit now lists current `release-NNNN+1` + previous `release-NNNN`, both complete.

**Expected evidence:** second release with the same artifact/read-back set as Phase 8; staging compatibility evidence for the previous-frontend journey.

**Failure behavior:** as Phase 8; staging compatibility failure (e.g., previous frontend breaks against new backends) blocks promotion per AD-15 — diagnose, fix, new candidate.

**Owner checkpoint 9:** Environment approval for the second promotion.

---

## Phase 10 — Rollback drill N → N-1 → N (two owner approvals)

**Goal:** VR-REC-03 live: owner-approved rollback of the current release to the previous official release and back, proving OP-REC-03/04, separate rollback results, unchanged DB, restored frontend.

**Prerequisites:** **two** official releases exist (Phases 8–9). With N = `release-NNNN+1` current and `release-NNNN` the target.

**Steps:**

1. `[OWNER] [ACTIONS]` Dispatch `rollback-release-greenfield.yml` with `version: release-NNNN` (the only input — regex `^release-[0-9]{4}$`; digests/tags/ARNs never entered by hand).
2. `[ACTIONS]` Job A `preflight` (read-only, preflight role): downloads the target manifest via the engine from the GitHub Release asset, runs rollback preflight (target exists/published/non-current/in-window/complete — ECR `release-<NNNN>` tags resolve to manifest digests, frontend prefix marker content identity, `compatibilityFingerprint` matches current runtime, `--schema-change absent` enforced) → prints approval summary.
3. `[OWNER]` Owner approves the `production` Environment for this run.
4. `[ACTIONS]` Job B `rollback` (environment `production`, group `production`, production role): fresh snapshot → `rollback execute` re-runs FULL preflight with byte-for-byte approval-identity match → deploys the complete target set (backends → gateway → frontend restored from the retained immutable prefix, checksum before live switch, `index.html` last, marker names the target) → read-only verification against the release manifest → separate `RollbackResult` (requester + approver mandatory, from/to digests/checksum). No release created, no manifest edited, no ECR tag minted/moved, no RDS action.
5. `[READ-ONLY]` Verify: running digests equal `release-NNNN` manifest digests; live marker names `release-NNNN` (CloudFront-visible); `rollback-evidence-<run>-<attempt>` artifact holds the rollback result; CloudTrail lookup for RDS mutation events during the rollback window returns nothing (`aws cloudtrail lookup-events --lookup-attributes AttributeKey=EventName,AttributeValue=ModifyDBInstance` — expect zero).
6. `[OWNER] [ACTIONS]` Dispatch `rollback-release-greenfield.yml` with `version: release-NNNN+1` → preflight → **owner approves** → rollback restores N. Verify as in step 5 with the N identities.
7. `[READ-ONLY]` Confirm the official release list is unchanged (two releases, no new ones) and both rollback results are separate artifacts.

**Expected evidence:** two green rollback runs, two `RollbackResult` artifacts, restored running identities at each step, no RDS mutation events, unchanged release catalog.

**Failure behavior:** preflight rejection (incomplete/incompatible target) → fix retention/config first, never force. Post-mutation defined failure → automatic `compensate` job restores exactly the `passed` components from the pre-rollback snapshot and re-verifies; ambiguous → manual with preserved evidence. The DB is never reversed (OP-DB-02).

**Owner checkpoints 10a/10b:** one Environment approval per rollback dispatch (two approvals total).

---

## Phase 11 — Retention, artifact checks, read-only journeys, hardening read-backs (operator)

**Goal:** close out the deferred live-pass items from AGENTS.md/PHASE-04/05/06 and produce the VR-OPS evidence bundle: retention apply, ECR anchors, candidate artifact completeness, production read-only journeys, separation + CloudTrail coverage.

**Prerequisites:** Phase 10 complete (two releases exist).

**Steps (owner, local; identity preflight first; every command with `--profile dpm-profile --region eu-north-1`):**

1. `[READ-ONLY]` Candidate artifact completeness for the exact runs/attempts used in Phases 4/8/9: `gh run view <run-id> --attempt <n>` + `gh api repos/:owner/:repo/actions/runs/<run>/attempts/<attempt>/artifacts` → the four artifacts each exist; spot-check `candidate-manifest-<run>-<attempt>` digests against ECR.
2. `[READ-ONLY]` ECR tag→digest anchors: for each of `release-NNNN`, `release-NNNN+1` and the three `sha-<fullsha>` candidates, `aws ecr batch-get-image --image-ids imageTag=<tag>` on all three repositories and compare digests to the manifests.
3. `[READ-ONLY]` Production read-only journeys: gateway health (`curl <alb-dns>/actuator/health`), frontend marker + content through CloudFront (`curl https://<distribution-domain>/release.json` and `/index.html`), `GET /api/v1/items` (read-only). Record responses.
4. `[READ-ONLY]` Window audit as the operator: `python -m delivery.cli snapshot production --environment production --identifiers scripts/config/production-identifiers.json --out snapshot.json` then `python -m delivery.cli retention audit --snapshot snapshot.json --repository Djimi@8793507/OnlineShop-full-stack@1097550215 --human` → exit 0, window = current + 1 previous, both complete.
5. `[MUTATION-ECR-policy] [OWNER]` Retention policy apply (Phase-6 deferred item): first `python -m delivery.cli retention preview --snapshot snapshot.json --policy delivery/src/delivery/retention/ecr-lifecycle-policy.json` (expect agreement or honest modeled label), then `DELIVERY_RETENTION_LIVE_APPLY=1 python -m delivery.cli retention apply --apply --snapshot snapshot.json --policy delivery/src/delivery/retention/ecr-lifecycle-policy.json` — per-repository `put-lifecycle-policy` with immediate byte-for-byte `get-lifecycle-policy` read-back; a post-apply window audit is recorded. ECR lifecycle evaluation is delayed up to 24h — never claim policy effect immediately.
6. `[MUTATION-S3] [OWNER]` Frontend `_releases/` prefix retention (Phase-6 live-pass item): `aws s3api put-bucket-lifecycle-configuration` on `onlineshop-frontend-799111666795` expiring `_releases/` objects after a conservative TTL (recommend ≥ 90 days; GitHub Release assets remain the long-term source, and the newest-10 rollback window is far younger than the TTL). Read-back: `aws s3api get-bucket-lifecycle-configuration`. Owner decides the TTL.
7. `[READ-ONLY]` Separation + hardening read-backs (retained Pass 3.5 scripts, read-only): `bash scripts/verify-production-staging-separation.sh`, `bash scripts/verify-cloudtrail-coverage.sh`. Optional owner-approved `[MUTATION]` frontend OAC: `bash scripts/verify-frontend-oac.sh` then `bash scripts/migrate-frontend-oac.sh --dry-run` → `--apply` with per-step read-backs; re-run the Phase-8 journeys after (marker/content must remain CloudFront-visible).

**Expected evidence:** audit JSON (`--human` view), lifecycle-policy read-backs per repository, S3 lifecycle read-back, journey responses, separation/CloudTrail pass output. This is the VR-OPS-01/02/03 live evidence bundle.

**Failure behavior:** audit exit 1 (incomplete window/marker mismatch) → fix retention state before claiming rollback capability. `PREVIEW_DISAGREEMENT`/`PROTECTED_IMAGE_EXPIRING` → stop, never apply. Policy read-back drift → fail closed, re-apply from the desired state.

**Owner checkpoint 11:** owner approves the retention apply + S3 TTL (mutations); everything else is read-only.

---

## Phase 12 — Observe stable operation (recoverably, before deletion)

**Goal:** OP-CUT-02 "observe recoverably": prove the fully-expanded greenfield path is stable in normal operation, including the live reconcile path and legacy inertness — while every legacy file is still in git (revert still possible).

**Prerequisites:** Phase 11.

**Steps:**

1. `[ACTIONS] [MUTATION-ECR]` Owner pushes a `feature/live-acceptance-observe` branch → ci.yml now handles `feature/**`: complete feature candidate published (class feature, staging-only). Optionally stage it once (dispatch `stage-candidate.yml`) to prove the feature preview journey post-expansion.
2. `[READ-ONLY]` Reconcile live tests:
   - No-op path: with staging RDS stopped, dispatch `reconcile-staging.yml` → exit 0, record "no ownerless staging RDS" (or the cron run shows the same).
   - Negative path: `[MUTATION-staging]` start staging RDS without a marker (`aws rds start-db-instance --db-instance-identifier onlineshop-staging-postgres`), wait for `available`, then dispatch `reconcile-staging.yml` → the run detects the ownerless RDS, stops it, verifies stopped, and **fails visibly** (exit non-zero, `reconcile-record-<run>-<attempt>` artifact). Read-back: `aws rds describe-db-instances` → stopped. This is the live OP-STG-05 proof (VR-STG-03).
3. `[READ-ONLY]` Legacy inertness proof: attempt to dispatch each of the three legacy workflows → none can run (no triggers) or fails without AWS; `grep -rn 'finalize-release.sh\|deploy-rollback.sh\|e2e-staging' .github/workflows/` returns nothing actionable.
4. `[READ-ONLY]` Watch at least a few reconcile cron cycles and one full ci.yml feature run; record observations (UTC timestamps).
5. `[OWNER]` Owner declares the observation window complete (recommend ≥ 2–3 days covering ≥ 2 reconcile cycles and ≥ 1 main-push-free period; owner may shorten).

**Expected evidence:** feature candidate run; two reconcile records (no-op + ownerless-stop failure); legacy dispatch refusals; observation notes.

**Failure behavior:** any greenfield run failure here blocks Phase 13 — the legacy files are still present, so the owner may still revert Phases 6–7 and fall back (that is the point of observing recoverably).

**Owner checkpoint 12:** owner declares observation complete and authorizes the deletion inventory review.

---

## Phase 13 — Delete the legacy system (IRREVERSIBLE; separate owner approval)

**Goal:** OP-CUT-02 final step: delete the legacy implementation and finish the greenfield renames — only after the drill passed (Phase 10) and observation passed (Phase 12).

**Prerequisites:** Phases 10 and 12 both complete.

**Steps:**

1. `[OWNER]` Owner approves the **deletion inventory** (below). Deletion is one reviewed PR; nothing else rides in it.
2. `[IRREVERSIBLE]` Delete/rename (one PR, owner merge):
   - Delete workflows: `.github/workflows/build-and-deploy.yml`, `.github/workflows/promote-release.yml`, `.github/workflows/rollback-release.yml`.
   - Rename: `promote-release-greenfield.yml` → `promote-release.yml`; `rollback-release-greenfield.yml` → `rollback-release.yml` (per their header comments); remove the three "Bring-up guard" steps from the renamed promote/rollback workflows and the guard step + note from `reconcile-staging.yml`; update `ci.yml`'s trigger-isolation comments (greenfield references).
   - Delete the legacy release framework: `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/` (~289 files: `bin/`, `ecr/`, `fixtures/`, `schema/`, `src/`, `tests/`, plus `pyproject.toml`, `requirements.txt`, `README.md`, `GUIDE.md`).
   - Delete legacy policies/design docs: `plans/AUTOMATIC-BUILDS-AND-DEPLOY/github-actions-oidc-trust-policy.json`, `github-actions-candidate-build-policy.json`, `github-actions-promotion-policy.json`, `github-actions-production-deploy-policy.json`, `github-actions-rollback-policy.json`, `github-actions-staging-deploy-policy.json`, `github-actions-role-layout.md`, `ecs-task-secrets-policy.json`, `GREENFIELD-SIMPLIFICATION-PLAN.md`, and superseded Pass 3 documentation (`03_RELEASE_TRACEABILITY.md`, `04_OPERATIONAL_MATURITY.md`, `05_FUTURE_IMPROVEMENTS.md` — owner review).
   - Delete legacy scripts: `scripts/ci-deploy-staging.sh`, `scripts/capture-staging-diagnostics.sh`, `scripts/bootstrap-staging-db.sh`, `scripts/setup-staging-env.sh`, `scripts/resume-staging.sh`, `scripts/pause-staging.sh` (staging lifecycle now owns start/stop), `scripts/ecs-run-sql.sh` (owner decision: the engine runs the SQL runner itself).
   - Delete legacy tests: `tests/scripts/{candidate_evidence_test.sh, ci_security_contract_test.sh, ecr_release_tagging_test.sh, production_hardening_test.sh, promotion_handoff_test.sh, promotion_test.sh, release_contract_test.sh, release_traceability_test.sh, retention_test.sh, rollback_test.sh, lifecycle_test.sh}`.
   - **Retain:** `scripts/pause-playground.sh`, `scripts/resume-playground.sh`, `scripts/config/*`, `scripts/lib/lifecycle.sh` (used by pause/resume), `scripts/sql/*` + `Auth/init-db/*` + `Items/init-db/*` (staging reset SQL sources), `scripts/create-worktree.py`, read-only hardening scripts until replaced (`verify-production-staging-separation.sh`, `verify-cloudtrail-coverage.sh`, `verify-frontend-oac.sh`, `migrate-frontend-oac.sh`, `inventory-production.sh` — owner decision).
3. `[READ-ONLY]` Post-deletion audit: `grep -rn 'release/bin\|finalize-release\|deploy-rollback\|github-actions-onlineshop\|production-mutation\|e2e-staging' .github/ scripts/ docs/` → **zero** executable references to the legacy release system (VR-READY-02 requires zero remaining executable references); `gh workflow list` shows the renamed workflows; `actionlint` + `zizmor` on the renamed workflows.
4. `[READ-ONLY]` Re-run the greenfield offline gates and service tests (implementation plan: before final cutover, run affected service Maven tests, frontend tests, E2E suite).

**Expected evidence:** the deletion PR; zero-reference grep; green offline gates; workflow list with final names.

**Failure behavior:** a grep hit for a legacy reference after deletion → fix in a follow-up PR before Phase 14; the deletion PR itself is the point of no return for the legacy code (recoverable only from git history — hence the separate owner approval and the prior observation window).

**Owner checkpoint 13:** explicit owner approval of the deletion inventory and the deletion PR — separate from all previous approvals (OP-CUT-02: "deletion is a separate owner-approved action").

---

## Phase 14 — Documentation update + VR-READY end-state verification

**Goal:** propagate the new delivery system through the documentation tree (AGENTS.md self-improving rules) and close the VR-READY-01/02 readiness gate.

**Prerequisites:** Phase 13.

**Steps:**

1. `[MUTATION]` Update documentation (one PR, owner review):
   - Root `AGENTS.md`: replace the Pass 3 release-contract/3R sections with the new delivery flow (candidate → staging → approved promotion → official release → rollback), the new workflows, the new roles, and the new offline gates (`pytest delivery/tests`, `ruff`, `actionlint`, `zizmor`); keep the AWS operational rules and Maven rules.
   - Each service-level `AGENTS.md` (`Auth/`, `Items/`, `api-gateway/`, `frontend/`): update CI/CD sections to the new flow.
   - `docs/CI_CD_GOTCHAS.md`: rewrite for the greenfield system (OIDC role subjects incl. `:environment:production`, trigger-swap history, retention coupling, reconcile behavior).
   - `docs/TESTING_STRATEGY.md`: replace legacy gate references with the delivery gates.
   - New `docs/DELIVERY.md`: central operator guide — the top-down flow of this plan's phases 4–12 as the steady-state procedure (push → candidate → stage → promote → rollback → retention audit), with role/environment/artifact references.
   - Tick off this plan's phases in-place (AGENTS.md planning convention) as executed.
2. `[READ-ONLY]` VR-READY-01 checklist mapping (evidence table — fill with the run ids/read-backs from the phases):
   1. SPEC §4.4 verified without unauthorized mutation → Phase 0/1/2/5/8 evidence rows.
   2. §4.5 decided → all seven decisions implemented (this plan documents their live proof).
   3. Contract examples independently implementable → Phases 4/8/9 produced real manifests validated by the engine.
   4. Every VR scenario has owner/evidence/stage → VR-CAND (Phase 4), VR-SEC (Phases 2/4/8: policies, trust read-backs, approvals), VR-STG (Phases 4/12), VR-PRO (Phases 8/9), VR-REC (Phase 10 + offline), VR-OPS (Phase 11).
   5. Four documents pass link/ID/ownership review → offline gates + this plan's reference check.
3. `[READ-ONLY]` VR-READY-02: cutover completion = live evidence + owner approvals above + zero executable legacy references (Phase 13 step 3).
4. `[READ-ONLY]` Owner's "tested in practice" end-state checklist:
   - deploy ✓ Phases 8/9 — tests ✓ Phase 4/9 (unit+integration+E2E per run) — staging ✓ Phase 4 — release on prod ✓ Phases 8/9 — start/stop env ✓ Phase 5 (+ staging stop/start inside every lifecycle) — prod requests ✓ Phase 11 — artifact checks ✓ Phase 11 — rollback ✓ Phase 10 — retention audit ✓ Phase 11.

**Expected evidence:** the documentation PR; the filled VR-READY evidence table; the end-state checklist all green.

**Owner checkpoint 14:** owner approves the documentation PR and signs the VR-READY-01/02 completion.

---

## Owner approval checkpoint index (every point the owner must act)

| # | Phase | Action | Kind |
|---|---|---|---|
| 0 | 0 | Sign off on sequencing + AWS re-auth | sign-off |
| 1 | 1 | Branch protection + `production` Environment reviewers | GitHub settings |
| 2 | 2 | IAM role creates + policy alignment + ECR immutability | AWS mutation |
| 3 | 3 | Merge `feature/cicd-release-redesign` → main | merge (IRREVERSIBLE) |
| 4 | 4 | Dispatch stage-candidate + review record | dispatch |
| 5 | 5 | Timing of production pause/resume | mutation timing |
| 6 | 6 | Neutralize legacy production-mutation workflows | merge (revertable) |
| 7 | 7 | Trigger swap PR (legacy triggers off, ci.yml expansion) | merge (revertable) |
| 8 | 8 | Approve `production` Environment for promotion #1 | Environment approval |
| 9 | 9 | Approve `production` Environment for promotion #2 | Environment approval |
| 10a | 10 | Approve rollback N → N-1 | Environment approval |
| 10b | 10 | Approve rollback N-1 → N | Environment approval |
| 11 | 11 | Retention apply + S3 TTL (+ optional OAC) | AWS mutation |
| 12 | 12 | Declare observation window complete | sign-off |
| 13 | 13 | Approve deletion inventory + deletion PR | merge (IRREVERSIBLE) |
| 14 | 14 | Approve documentation PR + VR-READY completion | merge |

---

## Risk decisions the orchestrator must present to the owner

1. **Legacy-disable ordering (OP-CUT-02 vs §7).** OP-CUT-02 says disable legacy *after* the drill; §7 says *before* the live promotion. The bring-up guards (hard fail-closed on `finalize-release.sh`/`deploy-rollback.sh` markers) and the absence of any main-class candidate pre-expansion make the §7 order the only executable one. **Recommendation:** follow §7 (neutralize legacy production-mutation workflows in Phase 6, swap triggers in Phase 7), keep every legacy file in git until Phase 13 so `git revert` remains the fallback, and require explicit owner checkpoints 6/7/13. This is the single biggest sequencing risk in the plan.
2. **First production release = the trigger-swap commit.** The first main candidate is unavoidably produced by the merge that expands ci.yml (or a subsequent main push), so release-0001's content is delivery-infra YAML unless an app change rides along. **Recommendation:** include one small application change in the Phase-7 PR so release-0001 exercises real code, and treat release-0002 (Phase 9) as the "normal cadence" proof.
3. **Production preflight role policy has no delivery-owned policy file.** The scope is derived from the `promote-release-greenfield.yml` comment (read-only production deploy scope, zero mutation actions). Drift between that scope and the engine's actual read calls would fail the first preflight run. **Recommendation:** build the preflight policy exactly from the Phase-2.2 step-4 table, then let the first live preflight (Phase 8 job A) be the acceptance test — a successful snapshot + preflight under that role proves the scope; any AccessDenied is fixed in the role (read-only) and re-run, never by widening the production role.

Secondary watch items: ECR `IMMUTABLE_WITH_EXCLUSION` must be live before the first `release-*` tag mint (Phase 2 step 5); staging ALB must pre-exist (Phase 0 check); the 14-day staging-record retention couples staging→promotion timing (Global rule 7).
