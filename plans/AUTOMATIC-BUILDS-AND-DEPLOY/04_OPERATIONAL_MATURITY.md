# Pass 4 — Operational Maturity

**Goal:** Add notifications, dashboards, runbooks, nightly validation, merge queue, and remaining polish to satisfy all v1 requirements.

**Prerequisite:** Pass 3 complete (release promotion, traceability, rollback all working).

**Exit criteria:** All v1 requirements from the three requirements documents are satisfied. The system is fully operational, observable, and documented.

---

## Tasks

### 4.1 Notifications

- [ ] **Slack integration:**
  - Build success / failure
  - Release readiness (candidate promoted)
  - Release failure
  - Staging deployment result
  - Production approval request
  - Production deployment result
  - Rollback approval request
  - Rollback result
- [ ] **Email fallback:**
  - Production approval requests
  - Rollback approval requests
  - Critical failure alerts
- [ ] Use GitHub Actions built-in Slack integration or a lightweight webhook action

### 4.2 Dashboards & Visibility

- [ ] **GitHub-native visibility:**
  - Actions dashboard for build status and logs
  - Environments tab for staging/production deployment history
  - Releases tab for official release records
- [ ] **AWS-native visibility:**
  - ECS console for running tasks, deployed image versions, health
  - CloudWatch basic metrics for ECS (CPU, memory, task count)
  - ECR console for image inventory
  - Cost Explorer for monthly spend tracking
- [ ] Verify all dashboards are accessible from phone + laptop
- [ ] No custom dashboards required for v1 — built-in tooling only

### 4.3 Audit Trail Completeness

- [ ] Verify: every production deployment records who approved, what artifact, when
- [ ] Verify: every rollback records who approved, what artifact, when
- [ ] Verify: GitHub Actions logs + GitHub Releases form a complete audit chain
- [ ] Verify: running ECS tasks expose deployment metadata (image digest visible in task definition)

### 4.4 Merge Queue / Pre-Merge Validation

- [ ] Enable GitHub merge queue (or equivalent):
  - Validates the merge result against latest `main` (not just feature-branch head)
  - Runs the blocking e2e test suite on the merge candidate
- [ ] Confirm stale-branch builds are properly canceled

### 4.5 Nightly Full Validation

- [ ] Create a scheduled workflow (`cron`) that runs all tests for all services nightly
- [ ] Nightly run must not block releases — it's for observability only
- [ ] Notify on nightly failure (Slack)

### 4.6 Runbooks

Create version-controlled runbooks in the repository covering at minimum:
- [ ] Build failure investigation
- [ ] Deployment approval flow (staging → production)
- [ ] Rollback approval flow
- [ ] Version traceability lookup (commit → image → release → deployment)
- [ ] Cost troubleshooting (unexpected spend)
- [ ] ECS task failure investigation

### 4.7 Cost Monitoring & Guardrails

- [ ] Set up AWS Budgets alert at $5/month threshold
- [ ] Review and optimize:
  - Fargate Spot usage
  - Staging on-demand lifecycle (auto-teardown)
  - ECR lifecycle policies effectiveness
  - GitHub Actions minutes usage
- [ ] Document monthly cost breakdown

### 4.8 Optional Enhancements (If Budget Allows)

- [ ] HTTPS via CloudFront default certificate (free with CloudFront)
- [ ] Custom domain (Route 53 — ~$0.50/month for hosted zone + domain registration cost)
- [ ] CloudFront → ALB HTTPS enforcement

### 4.9 PR & Branch Protection Policy Review

Review-only task — do NOT change protection settings during this review; verify current state and record drift. Sole contributor, so GitHub hardcodes that PR authors cannot approve their own PRs.

- [ ] Verify current effective state of `main` protection via `gh api`:
  - Classic branch protection (`repos/<repo>/branches/main/protection`):
    - Required status checks: `auth`, `items`, `api-gateway`, `e2e-staging`
    - `required_approving_review_count: 1`, strict mode, `dismiss_stale_reviews: true`, `enforce_admins: false`
  - Rulesets (`repos/<repo>/rulesets`): expect only `main-restricted` (id 18557637) with
    - `pull_request` rule, `required_approving_review_count: 0`
    - `deletion` + `non_fast_forward` rules, no bypass actors, `current_user_can_bypass: never`
  - Confirm the legacy `main` ruleset (id 11444569) is still deleted
- [ ] Re-check the effective merge policy: ruleset (0 approvals) + classic rule (1 approval) layer and the most restrictive wins → effective requirement is 1 approval. As sole contributor, 1 required approval is unsatisfiable (cannot self-approve) — effective count must stay 0 to merge own PRs
- [ ] Verify PR review practices: no external reviewers exist; merge must rely on passing required checks, not on review count
- [ ] Record current state and any drift in `executed/INFO.md` (what changed, when, and whether to adjust settings intentionally)

---

## Requirement Coverage Verification

After Pass 4, verify every requirement from the three source documents is satisfied:

| Requirement Doc | Key Sections | Status |
|---|---|---|
| 01_REQUIREMENTS_BUILD §3 | Branch/merge policy | Pass 2 + Pass 4 (4.9 policy review + merge queue) |
| 01_REQUIREMENTS_BUILD §4 | Build triggers, selective builds | Pass 2 |
| 01_REQUIREMENTS_BUILD §5 | Shared library policy | Pass 2 |
| 01_REQUIREMENTS_BUILD §6 | Test matrix | Pass 2 + Pass 4 (nightly, merge queue) |
| 01_REQUIREMENTS_BUILD §7 | CI platform, caching | Pass 1 + Pass 2 |
| 01_REQUIREMENTS_BUILD §8 | Release identity | Pass 3 |
| 01_REQUIREMENTS_BUILD §9 | Docker tagging | Pass 2 + Pass 3 |
| 01_REQUIREMENTS_BUILD §10 | Traceability | Pass 3 |
| 01_REQUIREMENTS_BUILD §11 | Retention | Pass 3 |
| 01_REQUIREMENTS_BUILD §12 | Security / OIDC | Pass 1 + Pass 2 |
| 01_REQUIREMENTS_BUILD §13 | Notifications / audit | Pass 4 |
| 01_REQUIREMENTS_BUILD §14 | Cost constraints | All passes |
| 02_REQUIREMENTS_DEPLOY §3 | Environment model | Pass 2 (staging) + Pass 3 (prod) |
| 02_REQUIREMENTS_DEPLOY §4 | ECS deployment target | Pass 1 |
| 02_REQUIREMENTS_DEPLOY §5 | Promotion rules | Pass 3 |
| 02_REQUIREMENTS_DEPLOY §6 | Rollback | Pass 3 |
| 02_REQUIREMENTS_DEPLOY §7 | Deployment order | Pass 3 |
| 02_REQUIREMENTS_DEPLOY §8 | Data & state | Pass 1 (MVP) |
| 02_REQUIREMENTS_DEPLOY §9 | Deployment notifications | Pass 4 |
| 02_REQUIREMENTS_DEPLOY §10 | Deployment audit | Pass 3 + Pass 4 |
| 02_REQUIREMENTS_DEPLOY §11 | Cost constraints | All passes |
| 02_REQUIREMENTS_DEPLOY §12 | Region selection | Pass 1 |
| 03_REQUIREMENTS_HOSTING §2 | Frontend S3+CloudFront | Pass 1 |
| 03_REQUIREMENTS_HOSTING §3 | Domain (deferred) | Pass 4 (optional) |
| 03_REQUIREMENTS_HOSTING §4 | HTTPS (preferred) | Pass 4 (optional) |
| 03_REQUIREMENTS_HOSTING §5 | Public access | Pass 1 |
| 03_REQUIREMENTS_HOSTING §6 | Dashboards | Pass 4 |
| 03_REQUIREMENTS_HOSTING §7 | Runbooks | Pass 4 |
| 03_REQUIREMENTS_HOSTING §8 | Cost constraints | All passes |

---

## Cost Impact

| Addition | Estimated Cost |
|---|---|
| Slack webhook | $0 |
| AWS Budgets | $0 |
| CloudWatch basic metrics | $0 (included) |
| Nightly GH Actions run | $0 (within free tier) |
| HTTPS (CloudFront default cert) | $0 |
| **Incremental total** | **~$0** |
