# Code Review — 2026-08-15 (Phase 4)

Working-tree review of the Phase-4 shared-staging delivery
(`delivery/src/delivery/commands/staging.py`, `staging_marker.py`,
`serving.py`, `aws/sqlrunner.py`, `aws/elb.py`, `github.py`,
`.github/workflows/stage-candidate.yml` + `reconcile-staging.yml`,
`scripts/config/staging-identifiers.json`,
`delivery/staging-iam/staging-deploy-policy.json`). Process: one build agent,
one review agent (report + gates), three parallel fix agents (clusters
A: IAM policy, B: engine correctness, C: workflows + guard), one verify agent.
Legacy machinery untouched. Findings resolved to zero MEDIUM+; gates green
at every stage.

## Findings and resolution

### HIGH

| # | Finding | Resolution |
|---|---------|------------|
| H1 | `staging-deploy-policy.json` granted NO ECR read while the engine revalidates candidate digests via `ecr:BatchGetImage` — a policy test even asserted the gap, so live staging would have failed every revalidation | Fixed: `RevalidateCandidateEcrDigests` Sid with `ecr:BatchGetImage` + `ecr:DescribeImages` scoped to the three staging repositories; no `PutImage` anywhere |
| H2 | SQL reset sources were unreachable under wheel install (layout assumption `REPO_ROOT=parents[4]`); the engine would have failed mid-lifecycle with the DB already mutated | Fixed: `--repo-path` is a required argument; `_resolve_sql_sources` resolves all seven SQL files from the checkout and fails closed BEFORE any staging mutation; regression tests prove both the resolution and the pre-mutation failure |

### MEDIUM

| # | Finding | Resolution |
|---|---------|------------|
| M1 | Cleanup ran on unverified ownership: a marker read error before acquisition could stop a foreign owner's environment | Fixed: cleanup only under verified ownership (marker acquired OR mutation began by this operation); otherwise recorded `cleanup skipped / ownership unverified` |
| M2 | Draft/prerelease GitHub releases could become the AD-15 "previous official frontend" | Fixed: only published (`!draft && !prerelease`) releases qualify; the newest qualifying release without a manifest fails closed — never silently falls back to an older one |
| M3 | Legacy `build-and-deploy.yml` `e2e-staging` job mutates staging without a marker — the reconcile cron could stop a legacy run's RDS mid-E2E | Fixed: bring-up guard step no-ops reconcile (exit 0, before any AWS credentials) while the legacy workflow declares `e2e-staging`; removed at cutover (OP-CUT-02) |
| M4 | Policy RunTask/TD resources were wildcards; `iam:PassRole` on the shared execution role was unconstrained | Fixed: staging cluster + `ecs:task-definition-family` condition on RunTask, family-scoped TD actions, `PassRole` limited to `ecsTaskExecutionRole` with `iam:PassedToService: ecs-tasks.amazonaws.com` |
| M5 | 60-min workflow timeout could not cover the worst-case bounded lifecycle | Fixed: `timeout-minutes: 90` in `stage-candidate.yml` + per-step and per-log bounded SQL waits |

### LOW

| # | Finding | Resolution |
|---|---------|------------|
| L1 | Marker owner unrestricted → tag value could exceed RDS limits | Fixed: owner ≤39 (GitHub login max); marker tag ≤256 proven by test |
| L2 | `run_attempt` not validated against the API response | Fixed: `parse_workflow_run`/artifact responses must match the requested attempt; mismatch fails closed |
| L3 | `not-run` E2E conclusion could be recorded as passed | Fixed: `not-run` is preserved truthfully, never coerced to success |
| L4 | `Source.repository` unconstrained | Fixed: strict pattern for the GitHub API repository |
| L5 | Floating SQL runner image | Fixed: pinned `postgres:18.1-alpine` (tag; digest pinning deferred) |
| L6 | Connectivity verification counted nothing | Fixed: framed count markers the SQL itself emits only on match |
| L7 | Reconcile could treat a read error as "absent DB" | Fixed: proven absence is a no-op success; read errors fail visibly |
| L8 | `GITHUB_OUTPUT` injection path | Fixed: multiline-delimiter form, hostile values can't inject |
| L9 | Workflow bash steps unexercised | Fixed: hostile-input bash execution tests for both new workflows |
| L10 | Unused AWS calls in the engine | Fixed: pruned; the policy's `ecs:DescribeClusters`/`ecs:TagResource` were never called and were removed (see skips) |

### Deliberately skipped

| Finding | Reason |
|---|---|
| Reconcile does not check GitHub run liveness | Marker TTL (3h) + 15-min cron bounds the leak; checking runs would require `GITHUB_TOKEN` in the reconcile role, widening its surface |
| RDS tag read-back is a single read (eventual consistency) | Fail-closed in both directions; worst case is a self-inflicted 3h lockout via the marker TTL |
| Reconcile ignores leftover ECS services | OP-STG-05 names RDS only; services are reclaimed by the next lifecycle's STARTING phase |
| Marker keeps microsecond timestamps | The owner bound alone keeps the tag ≤256 (proven by test); truncation buys nothing |
| `ecs:DescribeClusters`/`ecs:TagResource` removed from policy | The engine never calls them; absence is enforced by a discipline test |

## Verification

Verify agent re-examined all fixes against the normative docs
(OPERATIONS.md OP-STG-01..05, AD-08/09/15, CT-STG-02): all 2 HIGH and
5 MEDIUM verified, no regressions. Final gates:

- `pytest delivery/tests -q` → 667 passed
- `ruff check delivery` → clean
- `actionlint stage-candidate.yml reconcile-staging.yml` → clean
- `zizmor stage-candidate.yml reconcile-staging.yml` → no findings

Outstanding: live AWS exercise — the real `github-actions-staging` role and
its OIDC trust, a real lifecycle run, and a real reconcile event — is deferred
to the consolidated verification pass, as PHASE-04 now records.
