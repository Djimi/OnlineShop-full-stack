# AWS Testing Environment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` or `executing-plans` to implement this plan task-by-task. Steps use checkbox syntax for tracking. Obtain the owner's plan review and execution-method choice first.

**Goal:** Manually deploy an exact, locally verified PR candidate to AWS, run isolated API E2E, and require its successful result before merge.

**Architecture:** One private-access On-Demand EC2 host runs Docker Compose. Trusted `main` workflows publish digest-pinned images and serialize validation/disposal; separate Terraform backend, bootstrap, and environment roots protect recreation prerequisites. SSM controls the host and enables owner inspection.

**Tech Stack:** Terraform with native S3 locking, EC2, IAM/OIDC, ECR, SSM, Docker Compose, GitHub Actions, Python 3 standard library orchestration using AWS CLI v2/GitHub REST API, existing Java 25/Maven API E2E.

**Spec:** Read the [design](../docs/superpowers/specs/2026-10-03-aws-testing-environment-design.md) and [exact contract](../docs/superpowers/specs/2026-10-03-aws-testing-environment-requirements.md). Owner authorized inline execution on 2026-10-04. Cleanup-list review, cost approval, and live GitHub proof remain explicit gates within that contract.

## Global Constraints

- Local AWS profile `dpm-profile`; explicit region `eu-north-1`; verify account before mutation. GitHub uses OIDC.
- Only `workflow_dispatch` starts validation/disposal; trusted controller `main`; reviewed same-repository PRs targeting `main` only.
- Require **AWS validation** on the exact merge candidate and existing local CI; require up-to-date branches and enforce protection for administrators.
- Trust authorized maintainers and reviewed workflow definitions; review workflow changes. Normal Actions App identity/scoped check writes are accepted, without a dedicated App; candidate/manual/latest-attempt/failure rules remain mandatory.
- Shared concurrency group `aws-testing-environment`, `queue: max`, `cancel-in-progress: false`; one job holds it through reconciliation, deployment, tests, evidence, and outcome.
- Separate backend/bootstrap/environment Terraform roots and keys; `use_lockfile = true`; no DynamoDB lock table or routine bootstrap-state access.
- Apply the inspected saved plan; no silent replan, empty-state overwrite, automatic force-unlock, arbitrary target/root, or `-lock=false`.
- On-Demand only; measured combined app/E2E capacity; no NAT gateway, load balancer, managed DB, public admin UI, or permanent GitHub AWS keys.
- No ingress rules; loopback-only inspection ports; no container metadata access, cloud/GitHub credentials, Docker socket, or host-network/privileged execution.
- Fresh dedicated Auth/Items DB, Redis, Kafka, and application data each run; retain secrets/state/bootstrap and the app for unreserved inspection afterward.
- Initial stage bounds: provision 20 min, reset/deploy 10, readiness 5, E2E 15, diagnostics 5, disposal 20. Evidence retention at least 14 days.
- Run Maven wrappers at their module roots; update affected service docs independently; follow [script guidelines](../docs/SCRIPT_GUIDELINES.md) and [testing strategy](../docs/TESTING_STRATEGY.md).
- Batch coherent changes -> local proof -> AWS trial. Use fresh scoped workstream contexts, independent subagent review/preparation when authorized, and a fail-fast CI watcher reporting the first failed step; see [execution feedback](../docs/TESTING_STRATEGY.md#automation-execution-feedback).

## Review Focus

1. GitHub returns a different merge candidate after `main` changes: stop before mutation or success (Task 2).
2. A rerun has a different triggering actor or older completion: authorize the actual actor and preserve the newest required result (Tasks 2, 6).
3. A build/report archive contains traversal paths, links, or excessive content: reject it without executing or escaping the extraction directory (Tasks 3, 6).
4. The runner disappears between starting a remote operation and recording its ID: discover and reconcile the operation, rather than treating a missing ID as absence (Task 5).
5. Terraform state is missing while AWS resources remain: require recovery and refuse duplicate creation or false disposal success (Tasks 4, 7).

---

## Delivery flow

```text
1 inventory/cleanup -> 4 backend/bootstrap/environment
2 candidate/gate proof -> 3 isolated packaging -> 5 runtime/recovery
                                      4 + 5 -> 6 manual AWS validation
                                                 -> 7 disposal/recreation
                                                 -> 8 live acceptance + merge protection
```

Tasks 2–3 can be developed without AWS mutation. Task 4 provisioning waits for
cleanup evidence and owner-approved capacity/cost estimates. Enable the merge gate
last, after a real trial PR proves both success and blocking behavior.

## File map and interfaces

| Files | Responsibility |
| --- | --- |
| `infra/aws/backend/{main,variables,outputs,versions}.tf` | Protected state bucket; owner-only creation/migration |
| `infra/aws/bootstrap/{main,variables,outputs,versions}.tf` | OIDC, publisher/operator/host roles, ECR repositories, secret metadata |
| `infra/aws/environment/{main,variables,outputs,versions}.tf` | Dedicated network, EC2, encrypted delete-on-termination disk |
| `infra/aws/{bootstrap,environment}/backend.tf` and each root's `.terraform.lock.hcl` | Explicit state separation and pinned providers |
| `infra/aws/{backend,bootstrap,environment}/tests/boundary.tftest.hcl` | Plan-only Terraform boundary assertions |
| `infra/aws/README.md`, `.gitignore` | Root contracts, backend migration/recovery; ignore state/plans/runtime secrets |
| `infra/aws/runtime/compose.yml`, `host-setup.sh`, `run-stack.py` | Trusted host configuration, Compose lifecycle, bounded remote execution |
| `scripts/aws-validation.py` | Trusted request, artifact publication, operation ledger, validation/disposal controller |
| `tests/scripts/aws_validation_test.py`, `aws_runtime_test.py` | Observable CLI stories using fake APIs/commands and disposable local Compose |
| `e2e-tests/Dockerfile`, `e2e-tests/.dockerignore` | Candidate test image; compile/cache dependencies without running tests at build time |
| `.github/workflows/aws-validation.yml`, `aws-dispose.yml` | Manual workflows, job permissions, authorization, shared session lock |
| `.github/workflows/ci.yml`, `aws-check-proof.yml` | Credential-free merge-checkout evidence; trusted-main pending/failure association harness |
| `docs/TESTING-ENVIRONMENT.md`, `AGENTS.md`, `docs/TESTING_STRATEGY.md` | Sanitized inventory, operations, evidence, entry points and verification commands |
| `Auth/AGENTS.md`, `Items/AGENTS.md`, `api-gateway/AGENTS.md`, `frontend/AGENTS.md` | Each module's AWS runtime interface and unchanged local behavior |

Keep one controller implementation initially; split only genuinely independent
responsibilities if it becomes hard to review. Do not create a generic orchestration framework.

**Controller command contract** (all JSON inputs strictly validated):

- `python3 scripts/aws-validation.py request --pr NUMBER --output request.json`: authorize dispatch/rerun actor, capture candidate/CI identities, create attempt-specific pending GitHub check; no AWS credentials.
- `... verify-build --request request.json --manifest build.json --artifacts DIR`: verify workflow/run/attempt, exact candidate, archive paths and checksums; emit a verified manifest.
- `... publish --request request.json --manifest build.json --artifacts DIR --output images.json`: recheck candidate, publish to fixed ECR repositories without executing image code, record digests.
- `... validate --request request.json --images images.json`: within shared lock, reconcile, provision/reset/deploy/test/collect evidence/finalize check.
- `... dispose --generation ID --confirm dispose`: within same lock, authorize/reconcile/compare generation/destroy/verify.

`request.json` records repository, PR, head/base/candidate SHA, CI run/attempt,
controller SHA, actor/triggering actor, validation run/attempt, and check-run ID.
`build.json` adds fixed image names, artifact IDs, sizes and SHA-256 checksums,
plus a verified `fixtures.tar` containing only `Auth/init-db/` and `Items/init-db/` data;
`images.json` maps `auth`, `items`, `gateway`, `frontend`, `e2e` to ECR digest URIs.
Records cannot override trusted account, region, backend key, role, Compose path, or SSM target.

Use private S3 prefix `operations/` alongside—but outside—state keys for an append-only
attempt record and current-generation pointer. Only the locked operator changes
the generation pointer. Assign generation `run-RUN_ID-attempt-ATTEMPT` before any
environment mutation, including first provisioning. Initial protected keys:
`state/backend.tfstate`, `state/bootstrap.tfstate`, `state/environment.tfstate`.
Generate the globally unique bucket name from verified account ID, region, and repository
identity; record it, do not infer an existing bucket is ours by name.

### Task 1: Verify target account and remove old playground resources

**Files:** Update `docs/TESTING-ENVIRONMENT.md` cleanup/inventory sections. Keep raw
inventory/deletion manifests in protected local storage outside Git.
**Produces:** Verified account baseline, owner-reviewed deletion/preservation list,
inventory coverage and gaps; prerequisites for Task 4.

- [x] Verify identity using `aws sts get-caller-identity --profile dpm-profile --region eu-north-1`; record a sanitized account identifier without credentials.
- [x] Inventory enabled/relevant regions and global services, starting from account/service usage. Enumerate EC2/disks/snapshots/addresses/network dependencies, storage, container/DB services, owning stacks, IAM/access and other discovered resources. Record API failures and services not covered; tags alone do not prove absence. Scoped 17-region/global inventory and usage follow-up recorded in the runbook; MSK/unlisted-service gaps remain explicit.
- [x] Present the explicit deletion/preservation list; owner authorized continuation without further approvals after that list was documented. Account/access/security baseline preserved.
- [x] Execute the approved cleanup in dependency order; four exact unused playground resources deleted and individually verified absent.
- [x] Repeat the covered inventory and record preserved baseline/gaps. All 739 listed queries repeated; only the same 17 MSK subscription gaps remain.
- [x] Review and commit sanitized documentation after link/format checks: `docs(docs): record AWS cleanup baseline`.

### Task 2: Prove candidate resolution and GitHub result association

**Files:** Create `scripts/aws-validation.py`, `tests/scripts/aws_validation_test.py`;
update operational record with trial findings. No branch protection enabled yet.
**Consumes:** GitHub event context and short-lived job token.
**Produces:** `request` command and `request.json`; attempt-specific check named **AWS validation** on candidate SHA.

- [x] Write black-box request stories: unsupported actor/ref/fork/Dependabot/closed/conflicting PR rejects; CI push run or wrong candidate rejects; pending CI exits non-success without waiting; `main` changing during resolution rejects; rerun uses `github.triggering_actor`; token values never appear in output.
- [x] Run `python3 -m unittest -v tests/scripts/aws_validation_test.py`; confirm the new command stories fail for missing behavior.
- [x] Implement `request`: fetch PR and merge ref through GitHub REST, require same-repository open PR targeting `main`, freeze all SHAs, match successful `.github/workflows/ci.yml` PR run/attempt and its exact candidate. Require repository `admin` permission for both dispatch actor and actual rerun actor, with controller ref `refs/heads/main`. **Ruling:** Existing GitHub run/job SHA identifies PR head, not merge checkout. A credential-free CI identity artifact from an unchanged trusted workflow now binds the merge candidate; reject missing evidence and skipped required jobs.
- [ ] Create a new check run per accepted attempt (`external_id` = run/attempt identity), immediately non-success; complete only that check ID. An old run never updates another attempt's check. Re-query candidate identities and newest accepted attempt before final success; detect duplicates without AWS mutation.
- [x] Re-run focused tests; expected PASS with no AWS invocations for rejection stories.
- [ ] On a real trial PR, use a trusted `main` test harness with minimum `checks: write` to prove candidate association and latest-attempt selection: newer pending/failing check must defeat older success, including delayed old completion. Record API/UI evidence. If GitHub does not enforce this association, stop and revise the mechanism before implementing the deployment workflow.
- [x] Commit/integrate tested request controller and hosted proof records via PRs #72/#74; current-candidate runtime and final live-gate proof remain unfinished.

### Task 3: Package exact candidate without credentials

**Files:** Create `e2e-tests/Dockerfile`, `.dockerignore`; extend controller tests and
`verify-build`/`publish`; update testing strategy and affected module docs.
**Consumes:** Task 2 request and existing four application Dockerfiles.
**Produces:** Five immutable image archives plus build manifest; verified ECR digest manifest.

- [x] Write archive/build/publication stories for candidate/run/attempt/checksum/names/traversal/links/size/tag/dirty-checkout and superseded attempt. Missing commands were observed failing before implementation; additional existing-behavior boundary cases were characterized afterward.
- [x] Run focused automation tests and confirm missing build/verification/publication behavior fails before implementation.
- [x] Define separate read-only controller/candidate build job with fixed contexts and no OIDC/write credentials; verified by hosted manual packaging-only trial 37218248383.
- [x] Add E2E image with pinned Java 25/Maven-compatible base and Dockerfile lint compliance. Runs nonroot at `/workspace/e2e-tests`; compile/cache build does not execute tests; default is module-root wrapper `clean test`. Containerized local E2E and hadolint passed.
- [x] Implement inert archive/provenance verification and fixed-repository publication; compare remote/pushed digests. Live run 37218248383 produced all five independently verified ECR digests through publisher OIDC, with no application host.
- [x] Focused stories, hadolint, Compose builds, module-root wrapper E2E and nonroot pinned-image E2E pass. Both E2E reports contain three API tests and one logging regression, with no failures/errors/skips.
- [x] Commit packaging and independent E2E-module docs in 0df92f8/271e19e; integrated via PR #75 with hosted CI, then verified manual publisher trial.

### Task 4: Establish Terraform roots and protected bootstrap

**Files:** Create the mapped `infra/aws` Terraform files, README and provider locks;
update `.gitignore` and operational record.
**Consumes:** Task 1 baseline, verified account, repository/Environment identity.
**Produces:** Approved nonsensitive bootstrap identifiers and recreatable environment root.

- [x] Three roots have plan-only boundary assertions; IAM condition/trust regressions were observed RED->GREEN. Selected actual read denials passed OIDC run 37221612286; operator mutation/version boundaries remain part of the later live-proof step.
- [ ] Pin supported Terraform/provider versions and record compatibility with native S3 locking; run `terraform fmt -check -recursive infra/aws`, root-specific `init -backend=false`/`validate`, and `terraform test` against plan-only assertions. Confirm intentionally unsafe fixtures fail checks.
- [x] Implement protected backend root; inspected saved plan applied under owner authorization. Native state migration/version/resource verification completed before removing owned temporary local copies.
- [x] Implement/apply 19-resource bootstrap saved plan with actual immutable-ID Environment subject, separate roles, fixed repositories/retention and secret metadata. Generated values initialized solely through protected API; trust/repositories/state read back. OIDC read-boundary tests passed; mutation boundaries remain pending.
- [x] Define/provision disposable environment root with dedicated network, no ingress, pinned Free-plan On-Demand host/encrypted disposable storage and explicit profile. Actual settings, migrated host/old-storage removal and tag-only next-generation planning verified; routine role/recovery boundaries remain pending.
- [ ] Measure representative full Compose+E2E CPU/memory/disk use locally; include current Auth connection-pool demand. Compare regional On-Demand sizes (including burstable-credit cost if considered), disk, public IPv4, ECR, S3/secrets/log retention costs. Obtain owner approval of host/AMI/disk and live-hourly/live-monthly/disposed estimates before first apply.
- [ ] Bootstrap and provision using refreshed saved plans with restrictive file permissions. Inspect permitted changes before `terraform apply SAVED_PLAN`; record exact inputs/root/key/tool versions and resource identities. Never publish plan JSON or state as artifacts.
- [ ] Complete live boundaries/recovery: selected actual role read denies, native S3 locking, saved-plan staleness and tag-drift detection are proved. Routine-role mutations, missing-state recovery refusal and protected version boundaries remain pending. Preserve restore/import/verified-force-unlock instructions in `infra/aws/README.md`.
- Guarded manual operator no-change plan proof is implemented with missing/empty/
  unexpected-state refusal and pre-OIDC actor authorization; actual operator OIDC
  plan/native-lock proof passed in run `37245192463`. It cannot apply or establish EC2 mutation
  permissions; do not delete live state merely to satisfy its regression stories.
- [ ] Commit configuration, provider locks and sanitized evidence after checks: `feat(e2e): provision isolated AWS testing infrastructure`.

### Task 5: Implement reset, bounded remote runtime and recovery ledger

**Files:** Create `infra/aws/runtime/{compose.yml,host-setup.sh,run-stack.py}`,
`tests/scripts/aws_runtime_test.py`; extend controller ledger/reconciliation;
update each affected module's AGENTS and operational flows.
**Consumes:** Bootstrap identifiers, fixed runtime definition and five image digests.
**Produces:** `run-stack.py --generation ID --images FILE` exit code, stage record and reports; controller reconciliation used by validation/disposal.

- [x] Runtime stories cover missing behavior RED->GREEN for generation/process locks, readiness/report failure, scoped reset, sanitized failure evidence and unknown test termination. Actual local and AWS owner reset proofs remove DB/cache/broker markers; owner cancellation guards verified. Automatic remote recovery remains pending.
- [x] Run focused runtime stories after observed failures, with external command-boundary fakes and actual disposable Compose/reset/E2E coverage. Local isolation checks do not imply EC2 isolation proof.
- [x] Implement AWS-only Compose with four app digests, aligned backing versions, named dedicated volumes, loopback gateway/frontend ports and independent verified service-owned fixtures. No builds/source mounts/admin UIs or shell-sourced SQL.
- [x] Inject generated DB/application test credentials through restricted files; omit Auth's committed password seed, consume generated E2E registration password with random local fallback. Local and actual owner AWS reset/E2E prove generated-password login before/after reset and loopback runtime configuration. Current-candidate workflow proof remains pending.
- [ ] Implement host setup and `run-stack.py` top-down flow: host process lock -> verify project/generation -> stop writers/tests -> remove only project data -> initialize/start -> bounded readiness -> isolated E2E -> bounded sanitized evidence. Preserve app afterward; remove test container and secret temporary files safely.
- Owner live evidence: migrated host full-stack readiness/four-test E2E passed;
  seeded Auth/Items DB, Redis and Kafka reset removed markers/topic and four tests
  passed again. Sanitized reports retrieved and test/secret-file absence verified.
  These historical-candidate owner proofs do not establish current-candidate gate
  success or routine workflow recovery.
- [ ] Block container IMDS access with host firewall rules for IPv4/IPv6 and forwarded/container paths; require IMDSv2. Restrict capabilities/mounts and network modes. Verify Docker restart/reboot retains isolation; host SSM/ECR/secret access must still work.
- [x] Local controller implements immutable intent/launched/terminal events, predecessor-aware lost-ID recovery, conditional generation-pointer CAS, and recovered-aborted refusal until terminal SSM plus fresh host lock/process/test/temporary-credential absence. Tag-only existing-host saved planning; unexpected/missing/disposed state refuses. Full cloud mutation/recovery proof remains pending.
- Owner setup read-only lost-ID reconciliation is implemented/tested, including
  controlled live authoritative-record gap/discovery/CAS restoration. It never
  authorizes retry or clears unknown runtime state. Routine all-operation
  reconciliation, Terraform/EC2 transitions and detached-test recovery remain pending.
  Host `--reconcile` observation now has an observed RED-to-GREEN positive story
  and lock/foreign-record/detached-test/lookup-failure refusals. It leaves unknown
  outcomes unchanged and cannot authorize retry. Local whole-session orchestration
  now binds host transport; deployment and live cloud recovery remain pending.
  Routine `reconcile-cloud` now has fixed state/EC2/SSM observations, bounded
  snapshot/pagination/output, actual invocation-terminal checks and candidate
  rechecks. It never mutates/authorizes retry and still requires host/operation-
  ledger reconciliation. Guarded operator proof exercises it before planning;
  actual updated OIDC proof passed `37275392787`; whole-session recovery remains pending.
  Live cloud trial `37248840722` failed safely at the lock HEAD lookup. Scoped
  exact-prefix `ListObjectsV2` replaces the missing-HEAD/404 assumption without
  broadening IAM; realistic 403 story observed RED-to-GREEN. Denial/truncation
  remain refusal. Updated actual role retry passed `37275392787`.
  Corrected actual operator trial `37275392787` passed cloud observations and
  zero-change native-lock planning. Host/ledger recovery still precede mutation.
- [ ] Run local runtime tests and real-host metadata/network/credential probes from app and E2E containers. Prove cancellation recovery by interrupting SSM/controller, then verify a subsequent run cannot overlap. Bounds: provision 20/reset-deploy 10/readiness 5/E2E 15/diagnostics 5 minutes.
- [ ] Run module-root Maven verification for any changed Java/config/fixtures (install `common` before Items), frontend lint/build if affected, E2E and changed-Dockerfile hadolint; commit: `feat(e2e): reset and validate AWS runtime safely`.

### Task 6: Wire manual validation and trustworthy evidence

**Files:** Create `.github/workflows/aws-validation.yml`; complete controller
`validate` and automation stories; update root/module docs and testing strategy.
**Consumes:** Task 2 request, Task 3 verified archives/digests, Task 4 roots and Task 5 runtime/ledger.
**Produces:** Manually requested AWS result plus 14-day evidence independent of host lifetime.

- [ ] Write failing stories for skipped prerequisites, failed apply/reset/readiness/E2E, corrupt/missing/empty reports, cancelled finalizer, stale candidate at each checkpoint, duplicate request and older completion. Assert success requires every stage and recognized executed tests, not just exit zero.
- [ ] Run focused controller/runtime tests; confirm new outcome stories fail before wiring.
- [x] Implement workflow jobs: request (`contents/pull-requests/actions: read`, `checks: write`, no OIDC) -> build (read-only, no OIDC/write) -> publisher (Environment, publisher OIDC role) -> one validation job (Environment, operator OIDC role, check-write). Local workflow/controller contract covered; hosted current-candidate run pending.
- [x] Give only the validation job shared `aws-testing-environment` concurrency with `queue: max` and no active cancellation. Keep publication outside lock without pruning images. Set validation timeout 65 minutes; queue waiting is separate. Pin actions by reviewed commit SHA. Local parsed workflow contract passes; hosted runtime proof pending.
- [x] Implement locked `validate`: recheck identities -> verify account/state/ownership -> reconcile -> assign/persist generation -> saved plan/apply -> reset/deploy/readiness/E2E -> retrieve reports -> finalize only this attempt's check. Candidate/latest-attempt checks precede owned success. Implementation has local external-boundary stories only; actual mutation, host transport and current-candidate check remain pending.
  Independent `verify-publication` now authenticates this exact trusted-main
  run/attempt/three successful prerequisites/receipt artifact and digest, binds all
  request fields/five fixed image digests and fixture bytes, and rechecks candidate
  before create-only evidence. Runtime stage now consumes authenticated receipt
  and fixture through fixed host transport; historical real GitHub receipt check
  is not current-candidate authorization/runtime evidence. Deployed transport and
  live current-candidate validation remain pending.
- [ ] Validate reports as bounded inert data: known suite names, expected current test count (`ItemsE2ETest`: three API tests; `RestAssuredLoggingTest`: one logging regression), no failures/errors and no all-skipped outcome; reject unsafe paths/links/XML external entities, malformed results or false zero-test success. Upload only sanitized records/reports/bounded logs with at least 14-day retention; never credentials/state/plans or raw environment dumps.
  Independent `verify-reports` content parser and rejection stories now pass:
  uncompressed flat tar, two known suites/four unique cases, matching zero-failure/
  error/skip counters, bounded payloads and no entity/link/path acceptance. The local
  validation flow now binds report transport/stages and rechecks candidate/latest
  attempt; hosted current-candidate execution/evidence proof remains pending.
- [x] Add always-run outcome/evidence handling for rejected build/publication/deployment stages; failure before locked job completes accepted request non-success. Cancellation leaves pending/non-success and reconciliation evidence. Finalizer preserves owned completed success and cannot update another attempt. Local workflow contract only; hosted proof pending.
- [ ] Run focused suites, `actionlint` with support for current `queue` syntax, Terraform checks and an actual manual trial. Push/update/CI completion must start no AWS workflow. Trial success/failure must attach to the candidate from Task 2, without a second reviewer click.
- [ ] Commit workflow/controller/docs: `feat(e2e): add manual AWS validation workflow`.
  Coherent local batch independently reviewed; five findings observed RED-to-GREEN.
  Final combined automation: 172 tests passed in 219.656s. Actual production-path
  Compose/UID/reset/E2E passed in 91.055s; lints, isolated bootstrap mock assertions
  and documentation links passed. Hosted current-candidate proof remains pending.
  PR #93 integrated at `e0fa8db` after all six PR CI jobs passed. An inspected
  exact saved bootstrap plan applied only the two narrow host S3 transport grants;
  actual policy readback matched and Free/Active plan remained unchanged. The
  disposable trial PR is refreshed; current-candidate runtime proof is next.

### Task 7: Add generation-safe disposal and automatic recreation

**Files:** Create `.github/workflows/aws-dispose.yml`; implement controller `dispose`;
extend controller tests and operational disposal/recovery instructions.
**Consumes:** Fixed Terraform root/key, current generation pointer and Task 5 reconciliation.
**Produces:** Verified deletion/no-op outcome, preserved bootstrap and reusable recovery record.

- [ ] Write failing stories for stale generation after queueing, unknown ownership, wrong account/root, absent verified environment, missing/corrupt state with surviving resources, partial destroy and safe retry. Assert state/bootstrap/roles/images/secrets remain.
- [ ] Run focused tests; confirm disposal behavior failures before implementation.
- [ ] Add only `workflow_dispatch` with generation and confirmation inputs; require trusted `main` and authorized dispatch/rerun actor before AWS access. Use the same Environment/operator role and exact concurrency group/settings as validation; disposal timeout 30 minutes including 20-minute destroy bound.
- [ ] Implement locked disposal: reconcile -> compare generation -> verify ownership/state -> inspect saved destroy plan -> apply exact plan -> wait and verify recorded resource IDs absent -> record leftovers or successful no-op. Preserve generation/history after destroy; no account-wide sweep.
- [ ] Prove live disposal waiting behind E2E, stale queued disposal rejection, repeated verified no-op, partial-delete recovery and next manual validation's recreation with clean state. Active inspection tunnel cannot veto disposal.
- [ ] Run focused suites, workflow/Terraform checks and recreation E2E; commit: `feat(e2e): add generation-safe AWS disposal`.

  Local workstream implemented disposal and verified disposed-to-recreation with
  68 affected tests passing. Independent review identified refreshed secondary-ID/ownership
  admission, actual route IDs/creation outputs, repeated lifecycle intent handling,
  unknown destroy outcome/recorded-host reconciliation and partial-failure evidence.
  All seven findings are addressed in one consolidated local pass reporting 109
  affected stories passing. After the provider ENI tag-ID shape correction, the
  fresh coordinator suite passed 214 tests in 317.056s; changed lint/format, both
  workflow/queue checks, three isolated Terraform mock suites and documentation
  checks passed. Ready for integration; real disposal/recreation and required-check
  proofs remain pending.
  The first runtime trial's exact owner migration pointer shape is now
  covered, with unknown purpose/fields/status still rejected. The provider-proven
  ENI `Generation` tag deletion grant passed bootstrap mock assertions RED-to-GREEN;
  exact inspected saved policy apply/readback passed after PR #94's six CI jobs and
  guarded integration at `f452665`; account remains Free/Active. Task 7 hosted proofs
  remain pending. Local results and policy readback do not complete the live contract.

### Task 8: Run acceptance trials and enable required merge protection

**Files:** Update `docs/TESTING-ENVIRONMENT.md`, testing strategy, root/module AGENTS
and plan checkboxes/issues with actual evidence. Configure GitHub protection only after proofs.
**Consumes:** Tasks 1–7 and spec acceptance scenarios A1–A33.
**Produces:** Verified owner-operable environment, enforced merge gate, evidence links/runbooks.

- [ ] Record pass/fail and evidence link for every acceptance scenario A1–A33; distinguish local story tests from real GitHub/AWS proof. Run concurrent PRs/disposal, retries, identity changes, cancellation, clean-state and failure trials without introducing automatic deployment.
- [ ] Verify image retention cannot prune currently inspected/deployed digests: exclude active-retention tags from expiration and protect both previous and incoming images during replacement. Move retention protection under lock only when the recorded deployed generation actually changes, including failed validation left for inspection; release obsolete protection after reconciliation. Confirm disposed-state retention remains bounded. Record measured costs/capacity, AMI/tool versions and actual policies/settings.
- [ ] Document owner prerequisites and two `AWS-StartPortForwardingSession` examples using `dpm-profile`, region and verified instance ID for gateway `10000`/frontend `5173`. Show the current-generation record before inspection, after replacement, and after tunnel failure.
- [ ] Record recovery flows for stale state lock, corrupt/missing state, partial apply/destroy, lost command IDs and manual drift. Recovery restores operation, never synthesizes validation success from host health.
- [ ] Configure `main` required existing CI contexts plus **AWS validation**, strict up-to-date branches and administrator enforcement; no bypass/merge queue. Verify missing, pending, failed, stale, skipped and older-result cases with real trial PRs, plus success allowing merge only for the tested candidate.
- [ ] Run appropriate final checks once: automation suites, Terraform fmt/validate/test, actionlint, hadolint for changed Dockerfiles, affected module-root tests/frontend checks and final hosted E2E. Report unresolved failure evidence rather than marking delivery complete.
- [ ] Commit final operational docs/evidence index: `docs(docs): document verified AWS validation operations`; mark tasks complete only from actual evidence.

## Coverage map

| Contract | Owning tasks |
| --- | --- |
| R1–R5, F1–F8, S1–S5 | 4 (runtime/access verification also 5, 8) |
| R6–R9, F9 | 4, 8 |
| Validation record §4.2 | 2, 3, 5, 6, 7 |
| G1–G5 | 2, 3, 6 |
| G6–G12 | 2, 6, 8 |
| C1–C8 | 5, 6, 7, 8 |
| T1–T9 | 3, 5, 6 |
| S6–S12 | 2, 3, 5, 6 |
| D1–D4 | 1 |
| D5–D6 | 5, 8 |
| D7–D11 | 7, 8 |
| Failures/evidence §10, A1–A33, handoff §12 | 6, 7, 8 |

## Issues and resolutions

- [ ] **Retained-evidence action/API adapter:** trial `37382173568` passed cloud
  runtime/four tests/cleanup but refused finalization because action output is bare
  hex while API digest is `sha256:`. Its actual 14-day artifact also falls one second
  short of the strict lifetime minimum. Local regressions reproduced both failures;
  workflow prefix normalization and 15-day requested retention are GREEN without
  loosening downstream checks. Combined suite passed 215 tests in 321.545s; changed
  lint/format/workflow/whitespace passed. CI and a new hosted trial precede closure.
  The prior required AWS check remains failure.

- [x] **Operator plan proof recovered:** trial `37243793027` authorized actors/
  OIDC but failed its protected plan phase, without apply or app mutation. Initial
  diagnostics could not identify the stage. Sanitized fixed-stage/allowlisted
  failure evidence is now implemented/tested. PR #86 merged after hosted CI;
  retry `37245192463` passed actual operator native-lock/no-change planning, and
  an owner post-check found no lock. No IAM grants were widened.
  Quick state-schema reproduction passed; a targeted large-provider reproduction
  found the global log file-size limit also capped downloads. Independent bounded
  capture fixes that bug; excessive-log refusal and actual credential-free native
  init and hosted retry pass. Routine mutation/recovery boundaries remain separate.

- [x] **Credential action ignored security input:** successful operator run warned
  pinned v4.3.1 did not support `allowed-account-ids`. Exact metadata validation
  reproduced this locally. All three AWS workflows now pin reviewed Node-24
  v6.3.0 supporting configured inputs; controller identity checks remain intact.
  Updated-pin hosted proof `37246888048` passed without IAM/trust changes.

- [x] **Original host was incompatible with the Free account plan:** initial apply created
  eight network/template resources, then EC2 rejected `c7i.xlarge` as ineligible
  for Free Tier. `GetAccountPlanState` confirms `FREE`/`ACTIVE`. Partial versioned
  state was reconciled before a refreshed eligible launch. The owner
  rejected a paid-plan upgrade: account MUST remain Free. Select verified
  eligible/offered `m7i-flex.large` (2 vCPU/8 GiB/x86_64). Update Terraform/IAM
  constraints/tests are updated RED-to-GREEN; inspected policy-only and host-only
  plans applied, host/storage settings verified, Free plan unchanged. Actual full-
  stack/E2E and sampled capacity proofs pass; sustained-load guarantees remain
  separate. Never reuse the failed initial saved plan or upgrade the account.

- [x] **Check-writer trust decision (2026-10-05):** Owner accepts normal Actions App
  identity and scoped check writes, trusting authorized maintainers and reviewed
  workflows. Workflow changes require review; no dedicated App is required.
  Exact candidate/manual/latest-attempt/failure enforcement and live final-gate
  proofs remain mandatory and unfinished; this resolves only the provenance concern.

- [x] Manual opt-in vs mandatory merging resolved: manual dispatch produces a separately required candidate result; existing CI stays automatic.
- [x] Pending-request replacement resolved in the design: current GitHub documentation supports `queue: max`; both mutation jobs share one fixed group.
- [x] Fresh schema ownership identified: `Auth/init-db/` and `Items/init-db/` independently initialize databases; no cross-service schema dependency needed.
- [x] Scoped inventory and dependencies rechecked; documented unused SSH/RDS-role/firewall cleanup authorized by the continuation instruction and verified — Task 1.
- [ ] Exact candidate-check selection/newest-attempt behavior on GitHub needs live proof before deployment/gate enablement — Task 2.
- [x] Free-compatible capacity/AMI/disk and current estimates recorded; owner delegated execution without further approvals. Actual full-stack/E2E and sampled memory/disk/timing pass; sustained CPU/capacity acceptance remains separate.
- [ ] OIDC subject/Environment restrictions and IAM action/resource support require actual verification — Task 4.
- [ ] Candidate fixture credentials, image-retention protection and container metadata blocking need implementation evidence — Tasks 3, 5, 8.
- [ ] Cancellation launch/record gaps and state recovery require interruption trials — Tasks 5, 7.
- [x] Owner authorized inline execution and continuation without further approvals on 2026-10-04. Cleanup/backend/bootstrap and eligible host are verified; app runs after owner full-stack/clean-state proofs. Routine workflow/current-candidate result/disposal/final gate remain unfinished.
- [x] Existing CI SHA ambiguity identified and addressed with a separate trusted merge-checkout evidence artifact.
- [x] Hosted merge-checkout packaging proved on PR #72/run 37210137790; artifact identity and digest match live PR metadata. All six CI jobs and three API tests plus one logging regression passed. This does not complete the trusted-main check-selection proof.
- [x] Foundation PR #72 reached trusted `main`; trial PR #73 proved pending CI rejection and accepted merge-candidate check association without AWS access. Latest-attempt/protection proofs continue.

## Plan handoff

Owner selected **native/inline** execution on 2026-10-04 and delegated continued
execution without routine approvals. Task 1 is complete from actual cleanup and
repeated inventory evidence. Task 2 live selection proof and exact OIDC subject
were obtained in run 37215526810; controller finalization remains unfinished.
Backend creation/migration is verified; remaining bootstrap/runtime work continues.
Mark completion only from the full task contract, never local tests alone.
