# AWS Testing Environment — Detailed Implementation Contract

**Status:** Owner authorized inline execution of the [implementation plan](../../../planning/aws-testing-environment-PLAN.md)
on 2026-10-04, then delegated continuation without further approvals. Cleanup
and capacity/cost decisions are recorded in the runbook; ownership/security
constraints and live GitHub/AWS proofs remain mandatory.

Start with the [owner-facing design and walkthroughs](./2026-10-03-aws-testing-environment-design.md).
This companion preserves the exact requirements without putting them in the main
reading path. Section numbering continues from that document; IDs remain stable.

Owner-agreed choices are **DECIDED**. Mechanisms labeled **IMPLEMENTATION DETAIL**
are agent choices within the rules below. Execution has started; verified
operational commands and evidence are recorded incrementally in the runbook.

## 4. Implementation contract: resources and identities

**Binding owner correction:** the account MUST remain on the Free plan, without
upgrade. Capacity must be verified Free-Tier-eligible and offered in the target
AZ before apply. Selected replacement: `m7i-flex.large`, 2 x86_64 vCPUs/8 GiB,
verified in `eu-north-1a`; actual launch and bounded full-stack capacity proofs
remain required. This supersedes the earlier paid-only `c7i.xlarge` selection,
not the isolation, state, timing or E2E requirements.

**Open gate-provenance issue:** expected-source App selection is not workflow
identity. The no-custom-App approach must not be treated as sufficient until
same-repository check-writer spoofing is addressed/proved. The binding outcome
remains authentic validation of the exact candidate, not merely a check with the
right name. See [operational evidence/issues](../../TESTING-ENVIRONMENT.md).

The following rules are the implementation contract after written-spec approval.
**Must** is mandatory. Agents may choose mechanisms labeled **IMPLEMENTATION DETAIL**
only when they satisfy the observable contract; they cannot waive a constraint.

### 4.1 Resource boundary

- **R1:** One disposable Terraform root configuration owns a dedicated VPC/subnet/routes,
  internet gateway, security group, EC2 host, and encrypted disposable disk/data.
  Do not reuse an unowned default VPC or allocate retained disks/addresses accidentally.
- **R2:** A separate versioned bootstrap configuration and state own OIDC configuration,
  automation roles, host role/profile, private image repositories, and dedicated
  secret resources. A protected backend configuration owns the state bucket.
  Routine automation cannot change/delete bootstrap/backend resources or their state.
- **R3:** Do not add a NAT gateway, load balancer, managed database, DNS/domain, or
  public admin UI. Such changes need a revised architecture approval.
- **R4:** The host has outbound internet connectivity and no inbound security-group
  rules. Application ports needed by inspection are bound to host loopback only;
  backing services stay on the private container network. No local bind mounts
  substitute host source files for the pinned application images.
- **R5:** Record environment identity, Terraform root/backend key, account, region,
  ownership markers, and bootstrap inventory. AWS calls verify target account/region before mutation.
- **R6:** Choose supported host/runtime versions, measured CPU/memory/disk capacity,
  and image-compatible architecture. Account for simultaneous app and E2E memory.
  Document estimates for live and disposed states before owner approval to provision.
- **R7:** Image storage uses a bounded lifecycle policy. Preserve currently deployed
  digests through validation/inspection; record retention settings in the runbook.
- **R8:** Use `dpm-profile` for local inventory/bootstrap/recovery and explicitly use
  `eu-north-1` (Stockholm). Do not fall back to the invalid default-profile region.
  Confirm caller identity/account before deletion or provisioning; the profile name
  alone is not proof of target identity. GitHub uses OIDC, not local profile credentials.
- **R9:** Use On-Demand EC2; no Spot or other capacity-reclaimable purchase model.
  Optimize measured CPU/memory/disk capacity and optional retained storage, not by
  under-sizing the combined application/E2E workload. No numeric budget cap has been
  provided: show hourly/live-monthly and disposed-state cost estimates before first
  provisioning. No reservations/long-term commitments or unapproved extra services.
  On-Demand does not promise resilience to unlimited traffic, memory exhaustion,
  maintenance, or outages; automatic scaling/high availability remain out of scope.

### 4.2 Validation record

Each attempt records: repository and PR number; head commit; base commit; tested
merge-candidate commit; source CI run/attempt; controller revision; build artifact
identifiers/checksums; published image digests; validation run/attempt; environment
generation; remote command IDs; timestamps; stage outcomes; reports/diagnostics links.

The **environment generation** identifies the attempt currently owning or last
mutating the environment. Assign it before the first AWS mutation, including failed first
provisioning. Persist it independently of the host in a protected
bootstrap record. Never infer it only from successful runs or a PR branch name.

**IMPLEMENTATION DETAIL:** Names, record serialization, and storage mechanism are
agent choices. They must permit reconciliation when the host/job is missing and
must not introduce another paid subsystem without documenting its cost.

### 4.3 Terraform state and ownership

- **F1:** Use distinct backend, bootstrap, and disposable-environment roots and state
  keys. The disposable root cannot manage bootstrap/backend resources. Supply only
  required nonsensitive bootstrap identifiers to the disposable root; do not grant
  routine CI access to full bootstrap state through `terraform_remote_state`.
- **F2:** Store state in a dedicated S3 bucket with blocked public access, encryption,
  TLS-only access, versioning, and least-privilege access to exact state/lock paths.
  Use native S3 locking (`use_lockfile = true`), not a new DynamoDB lock table.
  Pin a supported Terraform version with this feature and commit provider lock files.
  Terraform automatically reads/writes state and acquires/releases the lock. S3
  atomically creates the `.tflock` object using `If-None-Match: *`; only one owner
  succeeds. The lock is released per operation, not across separate plan/apply calls.
- **F3:** Routine infrastructure roles may read/write the environment state and
  acquire/release its lockfile only. They cannot delete the state object/bucket,
  read/change bootstrap/backend state, disable protection, or access secrets via it.
  Protect both current state and prior versions; state/plan files may contain secrets
  even when Terraform marks values sensitive.
- **F4:** The initial state bucket is an owner-authorized bootstrap operation, not a
  PR action. Any temporary local backend-creation state must be protected and migrated
  to its dedicated remote key before routine CI. Document migration/restore steps in
  the plan/runbook; do not commit local state or publish raw state/saved plans.
- **F5:** Keep secret values out of ordinary Terraform outputs, logs, user-data,
  backend configuration, and artifacts. Prefer managing secret metadata in Terraform
  and initializing/reading values through protected runtime operations. Do not treat
  Terraform's `sensitive` flag as encryption or protection from state readers.
- **F6:** Apply/destroy only the trusted environment configuration with approved
  account/region/backend key and recorded inputs. Plans must stay inside the managed
  environment boundary. Unexpected resources/replacements stop for review; no arbitrary
  root paths, workspaces, user-supplied resource targets, or `-lock=false` workaround.
- **F7:** The S3 state lock supplements, not replaces, the full-session GitHub lock.
  Stale-lock recovery is an owner-reviewed procedure after verifying no active Terraform
  process remains. Do not automatically force-unlock because a GitHub run was canceled.
  Native S3 locks have no automatic lease expiry. Recovery uses the verified lock
  identity; do not edit state or delete old-looking lock objects as routine automation.
- **F8:** Recover interrupted apply/destroy by reconciling state and actual AWS resources.
  Do not overwrite lost/corrupt state with an empty state and recreate duplicates.
  Use restricted restore/import only with reviewed ownership evidence; verify final
  deletion against both state and recorded AWS resource identities.
- **F9:** Within the same GitHub locked session, create a saved apply/destroy plan,
  inspect its permitted changes, and apply that exact plan with the same trusted
  configuration, provider versions, backend, and inputs. A plain `apply` generates
  a new plan and is not a substitute for executing the inspected saved plan.
  Changed state lineage/serial makes the saved plan invalid; fail the attempt rather
  than silently replan/apply. Treat the saved file as sensitive and keep it private.
  State-version validation does not detect all direct AWS drift: use fresh default
  refresh-enabled planning, coordinate external mutations, and reconcile any drift.
  Terraform owns infrastructure settings; deployment/SSM owns containers and test data.
  Read-only AWS inspection is allowed. Intentional emergency infrastructure changes
  require aligning desired configuration/state before routine automation resumes.

File/module layout, nonsensitive input transport, and encryption implementation are
**IMPLEMENTATION DETAIL**. They must preserve F1–F9 without introducing another
paid state-management service. Backend/bootstrap administration remains separate
from the automated disposable environment's apply/destroy permissions.

## 5. Revision, manual request, and required-result contract

### 5.1 Trusted controller and unprivileged build

- **G1:** Keep current local checks. Separate PR image/test packaging (no AWS access
  or repository write permissions) from a controller using code already on `main`.
- **G2:** The AWS validation workflow has only `workflow_dispatch` as a trigger,
  takes a PR number, and runs trusted controller code from `main`. Reject other refs,
  unauthorized dispatch/re-run actors, invalid PR inputs, closed/conflicting PRs,
  other target branches, and forks. No push, PR, CI-completion, schedule, or automatic
  retry event may start this AWS workflow. Existing local CI remains automatic.
  Treat Dependabot/fork-equivalent requests as unsupported unless reviewed separately.
- **G3:** Resolve PR identities through GitHub's API and source CI evidence; do not
  assume the controller's `GITHUB_SHA` is the PR commit—it identifies default-branch
  controller code. Resolve and freeze head/base/merge candidate early, before build
  or environment queueing. Find a successful local CI PR run for that exact candidate,
  not merely a green branch tip or successful push run. Pending/missing/failed CI stops
  the request with instructions to retry manually later; do not auto-resume/deploy.
  Missing/ambiguous identities stop without AWS mutation.
- **G4:** Build application and E2E runner images from the source CI's exact merge
  candidate, outside any privileged context. Validate source run, attempt, repository,
  candidate, artifact identity, and checksum before trusted publication. Artifacts
  are data, not controller scripts; do not execute their hooks during publication.
  Artifact labels/checksums prove identity, not build provenance. Use trusted build
  orchestration in a separate unprivileged job, or verify that the source workflow's
  packaging logic matches the trusted definition; PR-edited provenance claims alone
  are insufficient.
- **G5:** Infrastructure templates, runtime Compose definition, SSM commands, and
  policy configuration come from pinned trusted controller code. PR changes to
  those control-plane files are not deployed by their own validation. This increment
  validates application changes, not arbitrary proposed infrastructure changes.

The precise artifact transfer/build layout is **IMPLEMENTATION DETAIL**. Agents
may reuse the image build or add an unprivileged packaging job; AWS application
images must correspond to the locally verified candidate, not a later branch tip.

### 5.2 Manual authorization and result publication

- **G6:** The authorized **Run workflow** action is the manual authorization; do not
  require a second Environment-reviewer approval or automatically queue approval jobs.
  Show requested PR/head/base/candidate, dispatch/re-run actor, and run identity in
  the initial summary before mutation. Restrict the GitHub testing Environment to
  default-branch controller jobs. No AWS exchange/publication from automatic PR/CI
  jobs; manual actor authorization must be verified before AWS access.
- **G7:** Publish a distinct required result named **AWS validation** against the
  tested PR candidate using the GitHub status/check API. A default-branch workflow's
  own job check is not sufficient. Link the result to its validation record.
- **G8:** The required result remains non-success until explicit provision/reset,
  deployment, readiness, and E2E success. Missing/skipped/rejected/failed prerequisites
  cannot produce success. Mark an accepted request non-success before mutation so
  a retry does not reuse an earlier success while this attempt is pending/failing.
  Final evaluation runs even after prerequisite failures;
  if cancellation prevents reporting, a missing/pending result still blocks merge.
- **G9:** GitHub's merge/head-check selection must be verified on a real trial PR
  before enabling protection. Preserve the candidate SHA association through this
  test; do not replace it with a default-branch check or silently report to another SHA.
- **G10:** Recheck PR open state and head/base/candidate identities before AWS access,
  after lock acquisition, and immediately before success publication. A mismatch
  marks the attempt superseded, never successful for the current candidate.
- **G11:** Retries require an authorized manual dispatch/re-run and use a distinct
  run/attempt record, with newly verified candidate/CI identities. An older
  attempt cannot overwrite a newer result for the same candidate. Detect duplicate
  requests before mutation; only the current authorized attempt may publish success.
- **G12:** Configure `main` to require existing CI results and **AWS validation**,
  up-to-date branches, and restrictions applying to administrators. Do not enable
  the new required result until its success/negative-case wiring has been proven.

No custom GitHub App or permanent write token is required by this design. The API
choice and minimum GitHub token permissions are **IMPLEMENTATION DETAIL**; agents
must demonstrate candidate association and fail-closed protection, not assume them.

## 6. Concurrency and race conditions

- **C1:** All validation and disposal mutations share one fixed repository-wide
  concurrency group. Do not key it by PR, workflow name, branch, or candidate.
- **C2:** Configure `queue: max` and do not cancel active sessions automatically.
  Up to 100 runs may wait; overflow/cancellation remains non-success. Queue order
  follows when requests start waiting, not necessarily the owner's click order.
- **C3:** One locked session spans target verification, operation reconciliation,
  provisioning, reset, deployment, readiness, E2E, diagnostics, and final outcome.
  Do not split deployment and tests into independently locked jobs.
- **C4:** Publication/build outside the lock may not change the environment or prune
  active images. Local per-PR CI cancellation must not cancel an active AWS session.
- **C5:** Before mutation, reconcile recorded Terraform processes/state locks,
  AWS operations, SSM commands, and any detached tests/processes.
  GitHub lock release is not proof they stopped.
  Verify terminal state or cancel and verify termination; otherwise stop the new run.
- **C6:** A PR closed, changed, or made conflicting while queued performs no reset
  or deployment. A change during testing prevents success for the new candidate;
  evidence can still describe the old attempt. Do not auto-deploy the new revision.
- **C7:** Disposal compares expected generation only after obtaining the same lock.
  Unknown/mismatched identity refuses deletion. Retries cannot steal another session.
- **C8:** Restrict manual AWS mutation to reviewed recovery procedures. GitHub
  concurrency does not protect against independent console/CLI actions; record such
  interventions and reconcile state before resuming automation.

**IMPLEMENTATION DETAIL:** Persistent operation ledger and host process-lock mechanism
are agent choices. Remote operations must have bounded lifetimes and leave enough
evidence for C5 even if the GitHub runner disappears.

## 7. Clean-state, application, and E2E contract

```text
locked session -> reconcile -> create environment if absent -> verify ownership
  -> stop previous app/tests -> remove owned data -> initialize/start candidate
  -> readiness -> isolated API E2E -> collect evidence -> publish outcome
```

- **T1:** Only the dedicated testing Compose project is reset. Stop writers first,
  then delete its Auth/Items PostgreSQL, Redis, Kafka, and application-owned persistent
  data volumes. No global Docker/host wipe and no deletion of bootstrap secrets/images.
- **T2:** Initialize empty databases with each service's supported schema/fixture
  process. Services own their schemas independently; the controller cannot make
  Items depend on Auth's internal database or vice versa. Deterministic fixtures are
  not leftovers from another run. Verify the process from empty volumes.
- **T3:** Start Auth, Items, API Gateway, frontend, and required backing containers
  using pinned application digests and repository-aligned third-party versions.
  Keep AWS-only configuration separate from local Compose defaults. No pgAdmin/Kafka UI.
- **T4:** Dedicated generated test credentials are injected through protected runtime
  configuration. Do not reuse committed local credentials. The bootstrap secrets
  survive data reset; application data does not.
- **T5:** Readiness requires required backing services, gateway `/actuator/health`,
  and frontend `/` to respond successfully within bounds. It is not E2E success.
- **T6:** Run candidate E2E in a short-lived container on the private application
  network, without AWS/GitHub credentials, host networking, privileged mode, instance
  metadata access, or the Docker socket. Use its Maven wrapper from `e2e-tests/`
  and `E2E_BASE_URL` for the deployed gateway. Keep controller AWS credentials on
  the GitHub side and host-role access outside candidate containers.
- **T7:** SSM launches trusted bounded commands; the E2E command and runtime privileges
  are fixed by the controller. Candidate test code is input inside the isolated
  container, not a shell script sourced by the credential-bearing controller.
- **T8:** Save E2E reports and stage results on success/failure; diagnostics are
  bounded and sanitized. Report retrieval cannot execute test-produced content.
  Missing/corrupt required test evidence fails validation, even if a command exits zero.
- **T9:** Preserve the environment for inspection after success or failure; release the
  lock after evidence collection. Do not reserve it for manual inspection.

**IMPLEMENTATION DETAIL:** E2E runner packaging, Java/Maven cache, report transport,
and fixture commands are agent choices under T1–T9. This resolves execution-location
and credential-isolation requirements without requiring public gateway ingress or
running candidate Maven plugins beside infrastructure credentials.

Initial maximum execution stages: provisioning 20 minutes; reset/deployment 10;
readiness 5; E2E 15; diagnostics 5; disposal 20. Queue wait is separate.
Agents may tune bounds from measured startup behavior, document them, and retain a
finite whole-session timeout. Timeout is failure, not successful background work.

## 8. Security boundaries

### 8.1 AWS and GitHub privileges

- **S1:** Use GitHub OIDC with temporary role sessions. Match exact audience and
  repository/Environment subject in AWS trust policy; inspect the actual subject
  format instead of assuming legacy names or broad repository wildcards.
- **S2:** Environment/default-branch protection enforces trusted workflow provenance.
  Do not assume AWS can enforce unsupported custom OIDC claims. Scope GitHub
  `id-token` and API write permissions to jobs that need them, never candidate builds.
- **S3:** Separate image publication from environment/host operation permissions. Routine
  roles cannot change IAM/OIDC policies, delete bootstrap repositories/secrets, or
  perform account-wide cleanup; backend permissions follow F3.
  Bootstrap/cleanup administration is a separate owner-authorized activity,
  not a permanent AdministratorAccess CI role.
- **S4:** Allow infrastructure operations only for the managed testing boundary.
  Scope any `iam:PassRole` to the exact host role; host role grants required image
  pull, named test-secret access, and SSM connectivity, not cloud administration.
- **S5:** Terraform apply/destroy must recreate/remove only the disposable environment
  using retained roles without creating arbitrary identities. Routine roles cannot
  manage bootstrap/backend configurations or their IAM policies. No separate
  infrastructure-management service role is required.
- **S6:** No fork deployment or unreviewed PR script execution in privileged contexts,
  including `pull_request_target`/`workflow_run`. A successful upstream run or owner
  click alone is not evidence that artifacts/scripts are safe controller code.

### 8.2 Host, network, and secrets

- **S7:** Require IMDSv2 (the host's token-protected metadata interface) and explicitly
  block application/E2E containers from reaching instance credentials. Token mode
  alone is not sufficient proof. Test the restriction from inside containers.
- **S8:** No public app/database/broker/SSH/admin ingress. Inspection uses authenticated
  owner SSM sessions only. Frontend API URL must work with the documented gateway
  and frontend local forwarded ports, not an internal hostname in the owner's browser.
- **S9:** Neither candidate container gets host administrative access, cloud tokens,
  a Docker socket, controller filesystem mounts, or privileged/host-network execution.
  Constrain capabilities and writable mounts to application/test needs.
- **S10:** Review same-repository candidate code before manual dispatch. Containers share
  the host kernel: isolation reduces credential exposure but is not a sandbox for
  arbitrary hostile code. This residual risk is why this design excludes unreviewed/fork runs.
- **S11:** Validate and safely encode all PR, artifact, image, and generation inputs.
  Use trusted runtime manifests; no candidate strings interpolated as shell commands,
  no arbitrary SSM documents/targets, and no unvalidated artifact paths or symlinks.
- **S12:** Encrypt disposable storage; use dedicated secrets and restricted runtime
  files. Reports, command output, summaries, IaC output, and documentation must not
  expose credentials. Do not rely solely on line-redaction after secrets are logged.

Exact IAM statements are **IMPLEMENTATION DETAIL**, derived from actual APIs and
verified service support. Least privilege does not mean assuming every action supports
resource-level scoping; any necessary broad discovery permission must be explained.

## 9. Account cleanup, inspection, and disposal boundaries

### 9.1 One-time playground cleanup

- **D1:** Confirm the account; inventory global resources and relevant/enabled regions.
  Record services/regions checked and failures/gaps. Include inactive disks, snapshots,
  storage, addresses, networking, and dependencies, not only running applications.
- **D2:** Review an explicit deletion/preservation list before execution. Owner has
  requested removal of all playground leftovers, not closure of the account or
  removal of access, billing, organization, or account-level security settings.
- **D3:** Flag ambiguous ownership, identities needed for access/security, and resources
  managed elsewhere. No deletion of suspicious cases without clarification. Follow
  owning stacks where practical; handle dependency ordering and orphaned leftovers.
- **D4:** Verify deletions and record the remaining baseline/coverage gaps before
  provisioning new testing resources. Do not claim "everything removed" based on
  incomplete inventory. New bootstrap resources must not be confused with leftovers.

### 9.2 Inspection

- **D5:** Document owner prerequisites and example SSM gateway/frontend port forwards
  in the runbook. No hosted self-hosted GitHub runner is introduced for inspection.
- **D6:** An inspection tunnel does not own the lock. It may disconnect or start
  reaching a newer generation after replacement; show current identity so the owner
  can detect this. Active tunnels do not veto disposal.

### 9.3 Routine disposal

- **D7:** Provide a manually dispatched **Dispose testing environment** workflow from
  `main`. Require expected generation and explicit confirmation; enforce operator
  authorization and default-branch GitHub Environment restrictions before AWS access.
  Do not add a second reviewer-approval step or automated disposal trigger.
- **D8:** After acquiring the shared lock, verify account/region/Terraform root,
  state/resource ownership, reconcile outstanding operations, and compare generation.
  Unknown or mismatched
  ownership/generation stops deletion. Absent environment with intact verified state
  is a successful no-op; missing/corrupt state is a recovery case, not proof of absence.
- **D9:** Delete only the disposable environment and its application data. Preserve the
  enumerated bootstrap/backend prerequisites, all state records, image retention,
  and GitHub evidence. Use Terraform destroy for the fixed environment root, not
  bootstrap destroy, targeted account sweeps, or arbitrary resource-name inputs.
- **D10:** Wait for verified environment/resource removal. Report partial failures and
  remaining resources, keep recovery evidence, and allow safe repeat attempts for
  the same generation. A delete API call alone is not disposal success.
- **D11:** The next validation recreates missing disposable resources without manual
  AWS setup. Partial environments require reconciliation; never create duplicate
  resources to evade a failed deletion. Historical successful validation remains evidence
  of tested code and is not invalidated merely because its environment was disposed.

## 10. Failures, evidence, and edge cases

| Condition | Observable outcome |
| --- | --- |
| No manual request, or automatic push/PR/CI completion | No AWS workflow starts; missing AWS result blocks merge for an unvalidated candidate |
| Requested candidate's CI is incomplete/failed or PR is unsupported | No AWS mutation; request explains why it cannot proceed |
| Artifact/identity validation fails | No publication/deployment; explain the mismatch |
| Candidate changes before mutation | Superseded attempt; no reset/deployment |
| Candidate changes during testing | Evidence for old attempt only; no current-candidate success |
| Provision/reset/start/readiness/E2E fails or times out | AWS result fails; retain reports and identifiable partial environment |
| Test reports are missing or collection fails | AWS result fails; do not infer tests passed |
| Controller crashes/cancels and cannot finalize result | Missing/pending result blocks merge; reconcile remote operations before next mutation |
| Reconciliation cannot prove terminal remote state | New run fails without overlapping reset/deletion |
| Disposal has stale generation, unknown ownership, or partial failure | Refuse unsafe deletion or report remaining resources; no false success |
| Queue is full or a queued request is canceled | No successful validation from that request |

Evidence records (§4.2) must be accessible from the required result/action summary.
Use bounded log excerpts and narrowly scoped test-report artifacts, not wholesale
environment dumps. Retain evidence at least 14 days, independent of environment disposal;
longer retention within repository limits is **IMPLEMENTATION DETAIL**.

No successful result is retroactively created from a recovered host's apparent
health. Recovery restores operability; a newly authorized manual run supplies fresh test evidence.

## 11. Acceptance scenarios

Both focused automation tests and real GitHub/AWS trials are required. A local
mock cannot prove GitHub branch protection, OIDC trust, or actual network isolation.

| ID | Given / action | Required observable result | Contract |
| --- | --- | --- | --- |
| A1 | Push/update PR and let local CI pass without a manual request | No AWS validation workflow starts, no AWS mutation; unvalidated PR merge blocked | G2, G6–G8 |
| A2 | Owner clicks Run workflow on main with current PR number; stages pass | No second approval required; result belongs to tested candidate and merge is eligible | G7–G12 |
| A3 | Local CI passes but AWS E2E fails | Required AWS result fails; PR cannot merge; reports available | T6–T8 |
| A4 | Invalid/unauthorized request or deploy prerequisite skipped | No successful AWS result; no unauthorized AWS access; merging remains blocked | G6, G8 |
| A5 | Push new commit after prior success | Previous result cannot satisfy new revision; local CI runs but AWS waits for another manual request | G2, G10 |
| A6 | Update `main` so PR is behind | Merge blocked until branch updated and new candidate validated | G12 |
| A7 | Change/close PR while it waits for lock | No reset or deployment for superseded/closed request | C6 |
| A8 | Change candidate during E2E | Old attempt cannot mark current candidate successful | G10 |
| A9 | Manually request two PRs during one active run | Second waits; first tests only first's images/data; no replacement cancellation | C1–C4 |
| A10 | Retry same candidate while older attempt is finishing | Older completion cannot overwrite newer attempt result | G11 |
| A11 | Seed old DB rows/cache/messages, then validate next PR | Old state absent; both DB schemas initialize and E2E passes from clean state | T1–T2 |
| A12 | Cancel job while remote command is still running | Next run waits/cancels and proves termination, or fails without mutation | C5 |
| A13 | Dispose current generation during active testing | Disposal waits; removes disposable resources only after tests finish | D7–D10 |
| A14 | Replace generation before queued disposal obtains lock | Disposal refuses to delete replacement | C7 |
| A15 | Dispose verified absent environment; repeat partial deletion | Absence is no-op; partial failure lists leftovers and safe retry removes them | D8–D10 |
| A16 | Validate after successful disposal | Stack recreated; no manual AWS changes; fresh data and E2E success | D11 |
| A17 | Probe public host and backing-service ports externally | No incoming app/database/broker/SSH/admin access | R4, S8 |
| A18 | Attempt metadata/AWS access inside app and E2E containers | No instance credentials or infrastructure access | S7–S9 |
| A19 | Fork/unauthorized job requests AWS role; artifact supplies shell input | No role/session or controller execution/deployment | G2, S1–S6, S11 |
| A20 | Test emits credential-shaped values/report collection fails | Sensitive values not exposed; missing evidence cannot pass gate | T8, S12 |
| A21 | Owner uses documented inspection forwards | Gateway/frontend reachable locally; current generation identifiable | D5–D6 |
| A22 | Review post-cleanup and post-disposal inventories | Preserved prerequisites and gaps explicit; no undocumented leftovers claimed absent | D1–D4, D9 |
| A23 | Change a mutable image tag after building approved candidate | Deployed application still uses the recorded candidate digests | G4, T3 |
| A24 | Owner or administrator tries to merge without successful AWS validation | GitHub rejects merging; protection has no administrative bypass | G12 |
| A25 | Try changing IAM/bootstrap or unrelated infrastructure using routine CI roles | Operation denied; approved managed-environment recreation still works | S3–S5 |
| A26 | Inspect live/disposed cost estimate and retained-image settings | Owner can see both costs; no active image is pruned during validation | R6–R7 |
| A27 | Run two Terraform operations against environment state | Native S3 locking refuses concurrent state mutation; full E2E session still has GitHub lock | F2, F7 |
| A28 | Dispose environment, then inspect backend/bootstrap | State bucket/versions, bootstrap state, roles/images/secrets remain; no local state was committed | F1–F4, D9 |
| A29 | Cancellation leaves a state lock or state is corrupt/missing | No automatic force-unlock or empty-state overwrite; safe reconciliation/recovery required | F7–F8 |
| A30 | Inspect region/profile and instance purchase model before apply | Local calls select dpm-profile/Stockholm, CI uses OIDC, instance is On-Demand without Spot | R8–R9 |
| A31 | Request validation before exact candidate's local CI passes | Clear non-success with no AWS mutation; later CI completion does not auto-start AWS | G2–G3 |
| A32 | Change the same Terraform state after saving a plan, then attempt apply | Saved plan rejected; no silently regenerated plan is applied | F9 |
| A33 | Change a managed AWS resource outside Terraform, then request a fresh plan | Planning detects relevant drift; intended changes are reconciled rather than relying only on state serial | F9, C8 |

Automation tests exercise observable behavior with controlled stale identities,
failures, skips, queueing, and partial deletion. Service changes follow
[TESTING_STRATEGY.md](../../TESTING_STRATEGY.md), including service-root wrappers.
Record trial evidence and actual protection settings; do not declare deployment
safe solely because the happy-path application starts.

## 12. Documentation and review handoff

The [main design](./2026-10-03-aws-testing-environment-design.md) owns the owner-facing
architecture/decisions; this companion owns the exact behavior/security contract.
[TESTING-ENVIRONMENT.md](../../TESTING-ENVIRONMENT.md) owns sanitized live inventory,
selected settings, operational commands, progress, and outcomes. Avoid duplicating
the full spec there. `AGENTS.md` links both and requires their alignment.

After approval, create a checked implementation plan in `planning/` following
`<feature-name>-PLAN.md`, with issues/resolutions and an execution-method review.
Commands, exact file layout, resource creation steps, and recovery procedures belong
in that plan/runbook—not in this owner reading path.

Maintain module independence: update affected service documentation only when
implementation changes that service. Inventory and candidate-request work do not
change service behavior or provision AWS resources.

### Authoritative technology references

- [GitHub Environment protection availability](https://docs.github.com/en/actions/reference/workflows-and-actions/deployments-and-environments)
- [GitHub OIDC with AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws)
- [Manual workflow_dispatch](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_dispatch)
- [Terraform S3 backend and native state locking](https://developer.hashicorp.com/terraform/language/backend/s3)
- [Terraform saved-plan behavior](https://developer.hashicorp.com/terraform/cli/commands/apply#saved-plan-mode)
- [Concurrency queues](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)
- [Required checks and up-to-date branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)
