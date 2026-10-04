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
- Shared concurrency group `aws-testing-environment`, `queue: max`, `cancel-in-progress: false`; one job holds it through reconciliation, deployment, tests, evidence, and outcome.
- Separate backend/bootstrap/environment Terraform roots and keys; `use_lockfile = true`; no DynamoDB lock table or routine bootstrap-state access.
- Apply the inspected saved plan; no silent replan, empty-state overwrite, automatic force-unlock, arbitrary target/root, or `-lock=false`.
- On-Demand only; measured combined app/E2E capacity; no NAT gateway, load balancer, managed DB, public admin UI, or permanent GitHub AWS keys.
- No ingress rules; loopback-only inspection ports; no container metadata access, cloud/GitHub credentials, Docker socket, or host-network/privileged execution.
- Fresh dedicated Auth/Items DB, Redis, Kafka, and application data each run; retain secrets/state/bootstrap and the app for unreserved inspection afterward.
- Initial stage bounds: provision 20 min, reset/deploy 10, readiness 5, E2E 15, diagnostics 5, disposal 20. Evidence retention at least 14 days.
- Run Maven wrappers at their module roots; update affected service docs independently; follow [script guidelines](../docs/SCRIPT_GUIDELINES.md) and [testing strategy](../docs/TESTING_STRATEGY.md).

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
- [ ] Present the explicit deletion/preservation list to the owner, separating suspicious ownership/access/security resources for clarification. Preserve account, billing, organization and account-level access/security settings.
- [ ] Execute the approved cleanup in dependency order, preferring owning-stack deletion; wait for completion and enumerate orphaned leftovers.
- [ ] Repeat the covered inventory and record preserved baseline, remaining resources and gaps. Task passes only when approved deletions are verified; unresolved suspicious resources block relevant deletions, not unrelated documentation work.
- [ ] Review and commit sanitized documentation after link/format checks: `docs(docs): record AWS cleanup baseline`.

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
- [ ] Commit tested controller/proof record: `feat(e2e): resolve manually requested AWS candidates`.

### Task 3: Package exact candidate without credentials

**Files:** Create `e2e-tests/Dockerfile`, `.dockerignore`; extend controller tests and
`verify-build`/`publish`; update testing strategy and affected module docs.
**Consumes:** Task 2 request and existing four application Dockerfiles.
**Produces:** Five immutable image archives plus build manifest; verified ECR digest manifest.

- [ ] Write failing stories for wrong candidate/run/attempt/checksum, unexpected image names, archive traversal/symlinks, oversized archives, tag mutation, and candidate changing before publication. Assert no publication on rejection.
- [ ] Run focused automation tests and confirm new rejection stories initially fail.
- [ ] Define trusted build orchestration: separate credential-free job checks out pinned controller and candidate in different directories, runs only controller orchestration, and builds candidate Auth/Gateway/frontend at service contexts and Items at repository context. PR Dockerfiles may run only inside this unprivileged build job.
- [ ] Add E2E image with pinned Java 25/Maven-compatible base and Dockerfile lint compliance. Run its wrapper at `/workspace/e2e-tests`; resolve dependencies/compile tests at build time; runtime default runs `./mvnw --batch-mode clean test` with `E2E_BASE_URL`. Include candidate tests; never bake secrets into images.
- [ ] Implement manifest/archive verification and fixed-repository publication, with no candidate hooks, command substitution, or unsafe extraction. Validate ECR digest after push, not just tag existence. Publisher has no environment/SSM permissions.
- [ ] Verify with focused stories, `hadolint e2e-tests/Dockerfile`, existing Compose builds, and actual isolated E2E against the local stack. Run the E2E wrapper from `e2e-tests/`; expect current three API tests to pass and reports to exist.
- [ ] Commit packaging and independent module documentation: `feat(e2e): package digest-pinned AWS validation images`.

### Task 4: Establish Terraform roots and protected bootstrap

**Files:** Create the mapped `infra/aws` Terraform files, README and provider locks;
update `.gitignore` and operational record.
**Consumes:** Task 1 baseline, verified account, repository/Environment identity.
**Produces:** Approved nonsensitive bootstrap identifiers and recreatable environment root.

- [ ] Write `tests/boundary.tftest.hcl` in each root with mock-provider plan assertions for separate resource boundaries, encryption, empty ingress, delete-on-termination disk, IMDSv2, no Spot and no forbidden paid services. Check routine-role policy boundaries locally and prove actual denies after bootstrap.
- [ ] Pin supported Terraform/provider versions and record compatibility with native S3 locking; run `terraform fmt -check -recursive infra/aws`, root-specific `init -backend=false`/`validate`, and `terraform test` against plan-only assertions. Confirm intentionally unsafe fixtures fail checks.
- [ ] Implement backend root with private versioned encrypted TLS-only S3 storage. Create it only after owner authorization; protect temporary local state with restrictive permissions and migrate it using `terraform init -migrate-state` into `state/backend.tfstate`. Verify remote state and versions before securely removing temporary copies.
- [ ] Implement bootstrap root with Environment-subject OIDC trust, separate ECR publisher/operator roles, exact host role/profile, fixed private ECR repositories, bounded image retention and secret metadata. Initialize generated secret values via protected runtime APIs, never Terraform secret-value resources/outputs. Inspect actual GitHub OIDC subject before trusting it.
- [ ] Implement disposable environment root with dedicated VPC/public subnet/outbound route/IGW, no ingress rules, On-Demand host and encrypted disposable storage. Use a pinned supported x86_64 Linux AMI with SSM; host role only pulls approved images, reads named secrets and supports SSM. Supply explicit bootstrap IDs without remote-state reads.
- [ ] Measure representative full Compose+E2E CPU/memory/disk use locally; include current Auth connection-pool demand. Compare regional On-Demand sizes (including burstable-credit cost if considered), disk, public IPv4, ECR, S3/secrets/log retention costs. Obtain owner approval of host/AMI/disk and live-hourly/live-monthly/disposed estimates before first apply.
- [ ] Bootstrap and provision using refreshed saved plans with restrictive file permissions. Inspect permitted changes before `terraform apply SAVED_PLAN`; record exact inputs/root/key/tool versions and resource identities. Never publish plan JSON or state as artifacts.
- [ ] Prove actual role denies, S3 state locking contention, saved-plan staleness, drift detection, missing-state recovery refusal and protected state-version access. Preserve restore/import/verified-force-unlock instructions in `infra/aws/README.md`.
- [ ] Commit configuration, provider locks and sanitized evidence after checks: `feat(e2e): provision isolated AWS testing infrastructure`.

### Task 5: Implement reset, bounded remote runtime and recovery ledger

**Files:** Create `infra/aws/runtime/{compose.yml,host-setup.sh,run-stack.py}`,
`tests/scripts/aws_runtime_test.py`; extend controller ledger/reconciliation;
update each affected module's AGENTS and operational flows.
**Consumes:** Bootstrap identifiers, fixed runtime definition and five image digests.
**Produces:** `run-stack.py --generation ID --images FILE` exit code, stage record and reports; controller reconciliation used by validation/disposal.

- [ ] Write failing runtime stories: preseeded DB/cache/broker state is absent next run; both independent schemas initialize; failed readiness cannot run E2E; old remote process or unknown operation blocks reset; report loss/invalid reports prevents success.
- [ ] Run `python3 -m unittest -v tests/scripts/aws_runtime_test.py`; observe missing runtime behavior failures. Keep API fakes at process boundaries; use disposable Compose for actual reset/isolation stories.
- [ ] Implement AWS-only Compose without local source mounts/admin UIs/build directives: four application digests, repository-aligned backing versions, named dedicated volumes, loopback gateway/frontend ports. Package each candidate's service-owned `init-db` scripts as verified data, preserving Auth/Items ownership; never source fixture content in the controller shell.
- [ ] Inject generated DB/application test credentials through restricted runtime files. Audit committed seed scripts for embedded credentials and provide supported test-only fixtures where needed; do not use local committed passwords. Frontend uses documented forwarded gateway URL `http://localhost:10000`, with frontend forwarding at `5173`.
- [ ] Implement host setup and `run-stack.py` top-down flow: host process lock -> verify project/generation -> stop writers/tests -> remove only project data -> initialize/start -> bounded readiness -> isolated E2E -> bounded sanitized evidence. Preserve app afterward; remove test container and secret temporary files safely.
- [ ] Block container IMDS access with host firewall rules for IPv4/IPv6 and forwarded/container paths; require IMDSv2. Restrict capabilities/mounts and network modes. Verify Docker restart/reboot retains isolation; host SSM/ECR/secret access must still work.
- [ ] Persist operation intent before launch, IDs immediately afterward, and terminal outcomes. Name/tag remote operations by generation; discover operations in launch/record crash gaps. Use bounded SSM execution plus a host process lock and termination verification; reconciliation checks Terraform locks/state, EC2 transitions, SSM and detached tests before mutation.
- [ ] Run local runtime tests and real-host metadata/network/credential probes from app and E2E containers. Prove cancellation recovery by interrupting SSM/controller, then verify a subsequent run cannot overlap. Bounds: provision 20/reset-deploy 10/readiness 5/E2E 15/diagnostics 5 minutes.
- [ ] Run module-root Maven verification for any changed Java/config/fixtures (install `common` before Items), frontend lint/build if affected, E2E and changed-Dockerfile hadolint; commit: `feat(e2e): reset and validate AWS runtime safely`.

### Task 6: Wire manual validation and trustworthy evidence

**Files:** Create `.github/workflows/aws-validation.yml`; complete controller
`validate` and automation stories; update root/module docs and testing strategy.
**Consumes:** Task 2 request, Task 3 verified archives/digests, Task 4 roots and Task 5 runtime/ledger.
**Produces:** Manually requested AWS result plus 14-day evidence independent of host lifetime.

- [ ] Write failing stories for skipped prerequisites, failed apply/reset/readiness/E2E, corrupt/missing/empty reports, cancelled finalizer, stale candidate at each checkpoint, duplicate request and older completion. Assert success requires every stage and recognized executed tests, not just exit zero.
- [ ] Run focused controller/runtime tests; confirm new outcome stories fail before wiring.
- [ ] Implement workflow jobs: request (`contents/pull-requests/actions: read`, `checks: write`, no OIDC) -> build (read-only, no OIDC/write) -> publisher (Environment, publisher OIDC role) -> one validation job (Environment, operator OIDC role, check-write).
- [ ] Give only the validation job shared `aws-testing-environment` concurrency with `queue: max` and no active cancellation. Keep publication outside lock without pruning images. Set validation timeout 65 minutes (55-minute stage sum plus orchestration); queue waiting is separate. Pin actions by reviewed commit SHA.
- [ ] Implement locked `validate`: recheck identities -> verify account/state/ownership -> reconcile -> assign/persist generation -> saved plan/apply -> reset/deploy/readiness/E2E -> retrieve reports -> finalize only this attempt's check. Recheck candidate before AWS exchange, after lock and immediately before success.
- [ ] Validate reports as bounded inert data: known suite names, expected current test count (`ItemsE2ETest`: three API tests; `RestAssuredLoggingTest`: one logging regression), no failures/errors and no all-skipped outcome; reject unsafe paths/links/XML external entities, malformed results or false zero-test success. Upload only sanitized records/reports/bounded logs with at least 14-day retention; never credentials/state/plans or raw environment dumps.
- [ ] Add always-run outcome/evidence handling for rejected build/publication/deployment stages; failure before locked job still completes accepted request non-success. Cancellation leaving pending must block merge and leave reconciliation evidence. New attempt's check supersedes prior success without old finalization changing it.
- [ ] Run focused suites, `actionlint` with support for current `queue` syntax, Terraform checks and an actual manual trial. Push/update/CI completion must start no AWS workflow. Trial success/failure must attach to the candidate from Task 2, without a second reviewer click.
- [ ] Commit workflow/controller/docs: `feat(e2e): add manual AWS validation workflow`.

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

- [x] Manual opt-in vs mandatory merging resolved: manual dispatch produces a separately required candidate result; existing CI stays automatic.
- [x] Pending-request replacement resolved in the design: current GitHub documentation supports `queue: max`; both mutation jobs share one fixed group.
- [x] Fresh schema ownership identified: `Auth/init-db/` and `Items/init-db/` independently initialize databases; no cross-service schema dependency needed.
- [ ] Scoped account inventory is recorded; deletion-list approval and ambiguous SSH/RDS-role ownership remain unresolved — Task 1.
- [ ] Exact candidate-check selection/newest-attempt behavior on GitHub needs live proof before deployment/gate enablement — Task 2.
- [ ] Host size/AMI/disk and regional running/retained costs require measurements and owner approval — Task 4.
- [ ] OIDC subject/Environment restrictions and IAM action/resource support require actual verification — Task 4.
- [ ] Candidate fixture credentials, image-retention protection and container metadata blocking need implementation evidence — Tasks 3, 5, 8.
- [ ] Cancellation launch/record gaps and state recovery require interruption trials — Tasks 5, 7.
- [x] Owner authorized inline execution on 2026-10-04; no AWS mutation has been performed.
- [x] Existing CI SHA ambiguity identified and addressed with a separate trusted merge-checkout evidence artifact.
- [x] Hosted merge-checkout packaging proved on PR #72/run 37210137790; artifact identity and digest match live PR metadata. All six CI jobs and three API tests plus one logging regression passed. This does not complete the trusted-main check-selection proof.
- [ ] Candidate-request foundation and proof harness must reach trusted `main` before live manual trials. The foundation PR cannot validate its own new controller; merging/review integration remains an owner boundary.

## Plan handoff

Owner selected **native/inline** execution on 2026-10-04. Tasks 1–2 are partially
implemented; do not mark either complete from local tests alone. Continue from
the recorded cleanup review and live GitHub proof gates. AWS account cleanup and
first provisioning retain their explicit owner-review steps.
