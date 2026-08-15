# Phase 4 — Shared Staging

**Plan:** `DELIVERY-SYSTEM-IMPLEMENTATION-PLAN.md` §4
**Status:** ✅ Complete (offline) — 2 workflows, 3 CLI commands, engine modules,
IAM desired policy; all gates green. Live AWS exercise deferred to the
consolidated verification pass (see "Known limitations" below).

## What this phase was about

One shared staging environment owned by exactly one candidate at a time. The
owner is proven by an RDS tag on the staging DB (the ownership marker, D1);
the staging record file is visibility only. The workflow never enters digests
by hand — the only inputs are the exact candidate run id and attempt.

```text
stage-candidate.yml (workflow_dispatch: candidate run id + attempt)
  │  concurrency group: staging (queued, cancel-in-progress: false)
  │  role: arn:aws:iam::799111666795:role/github-actions-staging
  v
QUEUED → OWNED ────────────── marker acquire on staging RDS (fail-closed
  │       revalidate: GitHub artifacts of the EXACT run/attempt +
  │       ECR digests via ecr:BatchGetImage (read-only)
  v
STARTING → RESETTING ──────── RDS start; tenant DB reset through the ECS
  │       SQL runner: schema + grants + seed + connectivity proof,
  │       every step with mandatory read-back (SQL sources resolved
  │       from the checkout via --repo-path, fail-closed)
  v
DEPLOYING ─────────────────── digest-pinned image-only TD diff,
  │       ordered auth+items → gateway, bounded waiters,
  │       running-digest observation
  v
COMPATIBILITY ─────────────── AD-15: previous-official-frontend read-only
  │       journey, or bootstrapException when no official release exists
  │       (draft/prerelease filtered; newest without manifest fails
  │       closed); then candidate-frontend journeys via the local
  │       static server + /api proxy
  v
E2E ───────────────────────── workflow runs the real Maven e2e-tests
  │       against the staging ALB (E2E_BASE_URL); the record holds the
  │       truthful conclusion (pending → passed|failed|not-run) across
  │       the two-invocation continuation machine
  v
EVIDENCE → STOPPING ────────── services to 0 + RDS stop, verified
  v
CLEANUP_VERIFY → marker release → COMPLETE
```

```text
reconcile-staging.yml (cron */15 + workflow_dispatch)
  running RDS + no/expired marker → stop → verify stopped → FAIL VISIBLY
  proven-absent DB               → no-op success
  any AWS read error             → fail (never treated as absence)
  production                     → never touched
```

## Trigger isolation & bring-up guard

- `stage-candidate.yml` is `workflow_dispatch`-only — staging is explicit,
  never push-triggered (AD-08).
- `reconcile-staging.yml` carries a bring-up guard step: while legacy
  `build-and-deploy.yml` still declares the `e2e-staging` job (a staging
  mutator with no ownership marker), reconcile no-ops with exit 0 **before
  any AWS credentials are used**. The guard is removed at cutover
  (plan §7 / OP-CUT-02). Manual dispatch of a staging run while a legacy
  staging run is active is operator responsibility until cutover.

## Key mechanics

| Rule | How it's enforced |
|---|---|
| Exact run/attempt authority | Dispatch inputs are digits-regex-validated; the engine revalidates the exact run/attempt through the GitHub API and the four `-<run>-<attempt>` artifact names (CT-CAND-03); ECR digest read-back proves the images still exist |
| Ownership marker (D1) | RDS tag `onlineshop:staging-owner` (canonical JSON, TTL 3h) on the staging DB; acquire fails closed on any valid owner; release read-back must prove absence |
| Record is visibility only (CT-STG-02) | Continuation re-reads the live marker and asserts operation/run/attempt identity before any second-invocation mutation; the record never grants ownership |
| Two-invocation machine | Invocation 1 runs through E2E(pending) and emits the E2E URL; invocation 2 records the real conclusion, cleans up, completes; a decided E2E conclusion is never overwritten |
| Digest-pinned deployment | Image-only container replacement (the diff is proven image-only), register + update-service, bounded waiter, running digests compared to the candidate's |
| Reset proof (OP-STG-02) | Each SQL step carries its own verify SQL; framed count markers prove the app roles' connectivity to their tenant DB; cross-tenant access is proven rejected |
| Failure semantics (OP-STG-04) | Diagnostics captured before destructive cleanup; cleanup runs ONLY under verified ownership; cleanup failure is a distinct visible failure; E2E success + cleanup failure is never success |
| Serialization (AD-09 / OP-GEN-01) | Both workflows share concurrency group `staging`, `cancel-in-progress: false`; fresh state is revalidated after acquisition (queue order is not guaranteed) |
| Cost reconciliation (OP-STG-05) | cron `*/15`: ownerless running RDS is stopped + verified + surfaced as a visible failed run; read errors fail visibly, never as absence; staging identifiers only, never production |

## Failure semantics (OP-STG-04)

Every phase failure joins the evidence-and-cleanup path:

- diagnostics (redacted ECS/RDS/ALB snapshot) are captured **before** the
  destructive cleanup;
- cleanup runs only when this operation verifiably owns the environment —
  the marker was acquired or a mutation by this operation already began;
  otherwise the record says `cleanup skipped / ownership unverified` and the
  possibly foreign-owned environment is left untouched;
- cleanup failure is recorded as a distinct `StagingCleanupFailure` — a
  passing E2E with failed cleanup is still failure, never promotion evidence.

## Trust boundary

Both workflows assume `arn:aws:iam::799111666795:role/github-actions-staging`
(role creation + live trust read-back are deferred to the consolidated live
pass). The desired policy artifact
`delivery/staging-iam/staging-deploy-policy.json` enforces:

- scoped read-only ECR on the three repositories (`ecr:BatchGetImage`,
  `ecr:DescribeImages` — no `PutImage`);
- `ecs:RunTask` scoped to the staging cluster with an
  `ecs:task-definition-family` condition (sql-runner family only);
- TD register/inspect/deregister scoped to staging families;
- RDS lifecycle + tag mutation scoped to the staging DB only;
- `s3:GetObject`/`s3:HeadObject` on the production frontend bucket's
  `_releases/` prefix only (previous-frontend journey);
- no production mutation anywhere.

## Review findings (all resolved)

One review round: 2 HIGH, 5 MEDIUM, 10 LOW — all fixed. Full detail in
[`CODE-REVIEW-2026-08-15-PHASE-04.md`](./CODE-REVIEW-2026-08-15-PHASE-04.md).

| Sev | Finding | Fix |
|---|---|---|
| HIGH | Policy had no ECR read while the engine revalidates digests (a test even asserted the gap) | `RevalidateCandidateEcrDigests` Sid: scoped read-only ECR |
| HIGH | SQL reset sources unreachable under wheel install | `--repo-path` required, resolved from the checkout, fail-closed pre-mutation |
| MEDIUM | Cleanup could stop a foreign owner's environment | Cleanup only under verified ownership |
| MEDIUM | Drafts/prereleases could become the AD-15 "previous official frontend" | Published-only filter + fail-closed on missing manifest |
| MEDIUM | Legacy `e2e-staging` race with the reconcile cron | Bring-up guard (no-op before AWS credentials) |
| MEDIUM | Policy RunTask/TD wildcards + shared execution role | Cluster/family scoping + condition keys |
| MEDIUM | 60-min timeout vs lifecycle worst case | 90 min + bounded SQL waits |

## Known limitations / live-pass deferrals

Stated honestly — none of these are claimed as verified:

- Live staging lifecycle and live reconciliation (real AWS mutation + read-back)
  have not run yet; role creation and OIDC trust read-back are part of the same
  deferred pass.
- The previous-official-frontend journey can only be live-exercised after the
  first official release exists; the bootstrap exception is what runs today.
- SQL runner image `postgres:18.1-alpine` is tag-pinned, not digest-pinned.
- RDS tag read-back is a single read (eventual consistency, fail-closed in both
  directions; worst case is a self-inflicted 3h lockout via the TTL).
- Marker TTL (3h) bounds an orphaned-run cost leak to the next 15-min
  reconcile cycle plus TTL, not immediately.

## Verification

- `pytest delivery/tests` → 667 passed; `ruff check` → clean.
- `actionlint` on both new workflows → clean. `zizmor` → 0 findings.
- Offline gates only: stateful AWS/GitHub fakes and static workflow checks.
  No live AWS behavior is claimed.

## What's next

**Phase 5 — Production promotion** (plan §5): approval-gated promotion of an
official candidate snapshot, consuming the candidate manifests this phase
validates. Also pending for the consolidated live pass: the staging role and
its OIDC trust, a real staging lifecycle with a real candidate, a real
reconcile event, and — after the first official release exists — the real
AD-15 previous-frontend journey.
