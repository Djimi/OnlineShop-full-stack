# Phase 5 — Production Promotion

**Plan:** `DELIVERY-SYSTEM-IMPLEMENTATION-PLAN.md` §5
**Status:** ✅ Complete (offline) — 1 workflow, 5 engine commands, shared
marker/frontend modules, GitHub API extensions, IAM desired policy; all gates
green. Live AWS exercise deferred to the consolidated verification pass (see
"Known limitations" below).

## What this phase was about

Approval-gated promotion of one exact verified candidate to production and its
publication as a sequential official GitHub Release — with zero rebuilds, one
observed deployment, and honest failure semantics.

```text
promote-release-greenfield.yml (workflow_dispatch:
  candidate run id + attempt, staging run id)
  │  job A `preflight` — read-only, NO concurrency group, NO environment
  │    guard -> validate inputs -> download exact-run/attempt candidate
  │    artifacts -> snapshot production (read-only) -> engine preflight
  │    (candidate eligibility, exact staging gate, ECR digests, fresh
  │    snapshot consistency, AD-11 newer-candidate reachability + warning,
  │    OP-DB migration-ownership gate) -> approval summary printed
  ▼
  owner approves the protected `production` Environment (AD-10)
  │
  ▼
  job B `promote` — environment: production,
  │  concurrency group: production (queued, cancel-in-progress: false)
  │    guard -> validate inputs -> download candidate artifacts ->
  │    download job A's report -> fresh snapshot -> FULL preflight with
  │    --previous-report (post-approval drift aborts pre-mutation) ->
  │    deploy backends (Auth+Items) -> deploy gateway -> deploy frontend
  │    (immutable prefix, checksum proven BEFORE live switch, live marker
  │    names the CANDIDATE) -> verify production read-only ->
  │    approver from actions/runs/{run}/approvals (never github.actor) ->
  │    finalize (OP-FIN-01) -> evidence artifacts uploaded (retention 14d)
  ▼
  official release-NNNN: ECR release tags + immutable S3 prefix +
  GitHub Release with manifest + 4 pinned SBOMs + window audit
```

## Key mechanics

| Rule | How it's enforced |
|---|---|
| Stable dispatch inputs (D2) | Only `candidate_run_id`, `candidate_run_attempt`, `staging_run_id`; env-indirected + digit-regex-validated in shell AND revalidated in the engine; digests/tags/ARNs/manifests/URLs are never entered by hand |
| Exact staging gate | The engine resolves the staging run's authoritative attempt via the GitHub API and downloads `staging-record-<run>-<attempt>` itself (Phase-4 convention); the record must be COMPLETE with E2E passed and cleanup passed for the same candidate |
| Preflight twice (OP-PRO-02) | Job A informational, job B authorizing; job B passes `--previous-report` and the recomputed `approvalIdentity` (SHA-256 of the identity-relevant subset) must match byte-for-byte — any drift aborts before mutation |
| AD-11 older candidate | `list_main_candidate_runs` (newer successful main runs with a complete candidate artifact set, bounded); reachability via the GitHub compare API; reachable → warning in the approval summary; diverged/ahead → reject; selected older than the running production release → reject |
| OP-DB gate (honest minimal) | The engine scans the checkout for migration-ownership markers (flyway/liquibase/db/migration file paths). None exist today, and the production IAM boundary grants NO RDS mutation, so schema changes are physically blocked (AD-15/OP-DB-01); the gate must be replaced with an additive-only diff when migration ownership is introduced |
| Ordered deployment (OP-DEP-01) | backends (Auth+Items registered+updated before either waits) → gateway → frontend last; each with image-only TD diff vs the OBSERVED snapshot revision, full-ARN `secrets[].valueFrom` assertion, deployment-bound waiter, running-digest comparison, idempotent skip when already running the candidate digest |
| Frontend (OP-DEP-03) | Immutable `_releases/<release-NNNN>/` prefix (provisional id) with the Phase-4 bundle convention `frontend.tar.gz` + prefix marker naming the candidate; the S3 aggregate content checksum is recomputed from full-object checksums BEFORE the live entry point is touched; live switch writes files first, `index.html` last, marker last; CloudFront invalidation read back |
| Read-only verification (CT-PROD-01..04) | Running digests vs expected, live marker content + object checksum, public CloudFront-visible marker/index, gateway health, read-only `GET /api/v1/items`; any mismatch writes the failed report first, then fails |
| Finalization (OP-FIN-01) | Allocate next never-reused `release-NNNN` (must equal the provisional id), mint ECR `release-*` tags from recorded manifest bytes (`batch_get_image` → `put_image`, never pull/rebuild) with digest read-back, prepare the CT-REL manifest (per-component SBOM SHA-256, sanitized compatibilityFingerprint from the snapshot), switch to the identity-equivalent official marker and verify it publicly, publish the GitHub Release with manifest + 4 SBOM assets, audit the window |
| Exact-match resume (OP-FIN-02) | Re-running finalize detects every existing component (manifest file, ECR tags, prefix, live marker, release object, assets) and resumes byte-identically or fails closed listing the differences; a duplicated/interrupted release id never overwrites |
| Rollback window (Phase 5 scope) | Read-only audit: current + up to 3 previous releases, each complete (manifest asset parses, all three ECR `release-*` tags resolve, frontend prefix marker exists); `rollbackCapableAtPublication` is recorded honestly and the command fails when the window is incomplete at publication time; full retention policy enforcement is Phase 6 |
| approvedBy | Derived from `actions/runs/{run}/approvals` (state approved + environment production), regex-validated, written with `jq -n --arg`; `github.actor` is only the requester |
| Bring-up guard (OP-CUT-01) | Both jobs refuse with exit 1 BEFORE any AWS credentials while the legacy `promote-release.yml` still declares its `finalize-release.sh` production path; removed at cutover |
| Failure honesty (D7) | No automatic compensation in Phase 5: on failure the pre-mutation snapshot, reports, and finalization evidence are uploaded (`if: always()`, retention 14 days) for Phase 6 recovery/rollback; the database is never mutated |

## Trust boundary

The workflow assumes only `arn:aws:iam::799111666795:role/github-actions-production`
(AD-17 boundary 3; role creation + live OIDC trust read-back are deferred to
the consolidated live pass). The desired policy artifact
`delivery/production-iam/production-deploy-policy.json` enforces:

- ECR read + `PutImage` on the three backend repositories only (no
  layer-upload actions, no `GetAuthorizationToken`); release-tag *naming* is
  enforced by the engine because IAM has no tag-name condition key
  (documented in the README);
- ECS scoped to the production cluster/services/task-definition families;
- `iam:PassRole` to the exact execution/task roles with
  `iam:PassedToService=ecs-tasks.amazonaws.com`;
- S3 on the frontend bucket, CloudFront invalidation on the production
  distribution, read-only ELB, and read-only `rds:DescribeDBInstances`
  scoped to the production DB instance ARN (snapshot compatibility
  fingerprint input);
- no RDS mutation actions, no logs or Secrets Manager actions, and no
  staging resources.

## Known limitations / live-pass deferrals

Stated honestly — none of these are claimed as verified:

- The live promotion, the real `production` Environment approval, real
  OIDC trust for the environment subject, real ECR/ECS/S3/CloudFront
  mutations + read-backs, and the real GitHub Release publication are
  deferred to the consolidated verification pass.
- Candidate revalidation follows Phase 4 semantics: `list_run_artifacts`
  requires the run object's current attempt to equal the producing attempt,
  so a candidate from a re-run's older attempt fails closed.
- Whole-workflow re-runs are not the OP-FIN-02 resume path: exact resume is
  the finalize COMMAND with identical inputs. A full workflow re-run is a
  fresh promotion (a new provisional id, a new release number — release ids
  are never reused either way).
- The OP-DB gate verifies the documented precondition (no migration
  ownership exists) — it cannot diff `init-db/*.sql` without git history;
  the production IAM boundary (no RDS mutation actions) is the real
  enforcement.
- PassRole task-role ARNs (`onlineshop-{auth,items,gateway}-task`) must be
  confirmed against the live task definitions in the live pass (README
  checklist item 2).
- The live marker layout is the greenfield convention: live root
  `release.json` document + per-release `_releases/<releaseId>/release.json`
  prefix marker. Legacy plain-text markers are still read by the snapshot
  (reported as `status: none` when unparseable), and the first promotion
  replaces them.

## Review findings (all resolved)

One review round: 2 HIGH, 5 MEDIUM, 9 LOW — all fixed. Full detail in
[`CODE-REVIEW-2026-08-15-PHASE-05.md`](./CODE-REVIEW-2026-08-15-PHASE-05.md).

| Sev | Finding | Fix |
|---|---|---|
| HIGH | Production policy granted zero `rds:` actions while `snapshot production` calls `rds:DescribeDBInstances` first in both jobs | Scoped read-only describe on the production DB ARN only; tests reworded to "no RDS mutation actions" |
| HIGH | `ecs:DescribeTasks` granted on cluster/service ARNs, but ECS evaluates it against the TASK ARN | Task ARN added to the resource scope; test pins it |
| MEDIUM | `finalize` accepted a verification report with foreign digests | Per-service `expectedDigest` must equal the candidate digest |
| MEDIUM | `validate_staging_against_candidate` ignored the record's `artifactsExpected` digests/checksum | Comparison vs the candidate added, fail-closed |
| MEDIUM | Promotion never inspected the staging record's AD-15 conclusion | Gate on `{passed, bootstrap-exception}` + honesty check |
| MEDIUM | Read-only preflight job assumed the mutation-capable production role | Separate `github-actions-production-preflight` role ARN, read-only scope |
| MEDIUM | Bring-up guard only covered legacy `promote-release.yml` | Also refuses while legacy `rollback-release.yml` mutation path exists |

## Verification

- `pytest delivery/tests` → 806 passed; `ruff check` → clean.
- `actionlint` on all five greenfield workflows → clean. `zizmor` on
  `promote-release-greenfield.yml` → 0 findings.
- Offline gates only: stateful AWS/GitHub fakes and static workflow checks.
  No live AWS behavior is claimed.

## What's next

**Phase 6 — Recovery, rollback, and retention** (plan §6): automatic
recovery from the Phase-5 snapshot on defined failures, owner-approved
`N -> N-1 -> N` rollback consuming official manifests, and the four-release
retention window enforcement — plus, in the consolidated live pass: role
creation/OIDC trust read-backs, one live promotion, and the live rollback
drill that precedes legacy deletion.
