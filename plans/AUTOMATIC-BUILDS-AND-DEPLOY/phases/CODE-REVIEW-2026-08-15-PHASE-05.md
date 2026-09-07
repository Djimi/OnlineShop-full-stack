# Code Review — 2026-08-15 (Phase 5)

Working-tree review of the Phase-5 production promotion
(`delivery/src/delivery/commands/promote.py`, `deploy.py`, `verify.py`,
`finalize.py`, `github.py`, `.github/workflows/promote-release-greenfield.yml`,
`delivery/production-iam/production-deploy-policy.json`). Process: one build
agent, one review agent (report + gates), three parallel fix clusters
(A: production IAM policy, B: engine gates, C: promote workflow), one verify
agent. Legacy machinery untouched. Findings resolved to zero MEDIUM+; gates
green at every stage.

## Findings and resolution

### HIGH

| # | Finding | Resolution |
|---|---------|------------|
| H1 | The desired production policy granted ZERO `rds:` actions while `snapshot production` calls `rds:DescribeDBInstances` (compatibility fingerprint) as the FIRST AWS step of both jobs — live promotion would have failed immediately | Fixed: scoped read-only `rds:DescribeDBInstances` added on the production DB ARN only; tests reworded to "no RDS mutation actions" so the describe is expected and the mutation absence stays pinned |
| H2 | `ecs:DescribeTasks` was granted on cluster/service ARNs, but ECS evaluates that action against the TASK ARN — running-digest verification would have been denied | Fixed: task ARN added to the resource scope; a dedicated test pins the task-ARN requirement |

### MEDIUM

| # | Finding | Resolution |
|---|---------|------------|
| M3 | `finalize` accepted a verification report whose per-service digests were foreign (anything that parsed) | Fixed: per-service `expectedDigest` must equal the promoted candidate digest; mismatch fails closed |
| M4 | `validate_staging_against_candidate` never compared the record's `artifactsExpected` digests/checksum against the candidate manifest | Fixed: comparison added; mismatch fails closed |
| M5 | Promotion never inspected the staging record's AD-15 compatibility conclusion | Fixed: gate on `{passed, bootstrap-exception}`, with an honesty check — a `bootstrap-exception` record whose prior official release predates the record's `completedAt` fails closed |
| M6 | The read-only `preflight` job assumed the full mutation-capable production role | Fixed: separate `arn:aws:iam::799111666795:role/github-actions-production-preflight` with a documented read-only scope; the workflow config pins the ARN |
| M7 | The bring-up guard only covered legacy `promote-release.yml` | Fixed: the guard also refuses while the legacy `rollback-release.yml` mutation path (`deploy-rollback.sh` marker) exists |

### LOW

| # | Finding | Resolution |
|---|---------|------------|
| L1 | `approvalIdentity` could not signal a newer reachable candidate | Fixed: now includes the newer-candidate warning + the OP-DB gate, so any drift aborts pre-mutation |
| L2 | Manifest published to disk after release publication (interrupt lost it) | Fixed: manifest written to disk BEFORE release publication (resume-safe) |
| L3 | Provisional frontend prefix identity could be swapped | Fixed: prefix identity guard added |
| L4 | `rolloutState: FAILED` could pass verification | Fixed: FAILED fails verification |
| L5 | Running-digest convergence accepted foreign digests | Fixed: bounded-retry convergence accepts only `{expected}` |
| L6 | Deployment-describe retries unbounded | Fixed: bounded retries |
| L7 | Snapshot docstring misdescribed its inputs | Fixed: docstring corrected |
| L8 | Staging-record identity not cross-checked against the record's embedded run/attempt | Fixed: cross-check added |
| L9 | `approvedAt` sourced from shell `date` | Fixed: taken from the approvals API response (`approved_at` // `created_at`), strict ISO-8601, no `date` fallback |

### Deliberately skipped

| Finding | Reason |
|---|---|
| OP-DB gate is a file-path scan + the real physical block (zero RDS mutation in the policy, engine has no production SQL path) | Honest minimal per AD-15/OP-DB-01; documented as replaceable with an additive-only diff when migration ownership appears |
| No automatic compensation in Phase 5 | OP-REC recovery is plan §6; on failure the pre-mutation snapshot + evidence are uploaded with `if: always()` (retention 14 days) |
| `PutImage` release-tag naming is engine-enforced | IAM has no tag-name condition key; no policy condition can express it |
| PassRole task-role ARNs (`onlineshop-{auth,items,gateway}-task`) | Pending live confirmation against the live task definitions (README checklist item 2) |
| Preflight role creation | The `github-actions-production-preflight` role creation + live OIDC trust read-back are deferred to the consolidated live pass |

## Verification

Verify agent re-examined all fixes against the normative docs
(OPERATIONS.md OP-PRO-01/02, OP-DEP-01/03, OP-FIN-01/02, OP-DB-01, AD-10/11/
15/17, CT-PROD-01..04): all 2 HIGH and 5 MEDIUM verified, no regressions.
Final gates:

- `pytest delivery/tests -q` → 806 passed
- `ruff check delivery` → clean
- `actionlint` on all greenfield workflows → clean
- `zizmor promote-release-greenfield.yml` → no findings

Outstanding: live AWS exercise — the real `production` Environment approval,
real OIDC trust read-backs, real ECR/ECS/S3/CloudFront mutations, and the real
GitHub Release publication — is deferred to the consolidated verification
pass, as PHASE-05 now records. Offline only; no live AWS behavior is claimed.
