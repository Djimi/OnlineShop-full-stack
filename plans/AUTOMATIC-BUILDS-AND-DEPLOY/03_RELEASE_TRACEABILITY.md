# Pass 3 — Release, Traceability & Promotion

**Goal:** Establish an official release identity, promote one verified monorepo
snapshot from staging to production, retain audit evidence, and provide a safe
application rollback path.

**Prerequisite:** Pass 2 is complete: protected `main`, immutable SHA candidate
tags, blocking PR validation, isolated on-demand staging, and successful cloud
E2E validation. The existing production playground is the production
environment for v1; Pass 3 hardens it rather than creating a second production
stack.

**Exit criteria:** A manually selected, successful `main` candidate can be
promoted exactly once under an owner-approved semantic release label. The exact
container digests validated in staging and the checksummed frontend candidate
artifact are deployed to production without rebuilding. A GitHub Release retains
the manifest, SBOMs, checksums, CI/staging evidence, and approval/deployment result
under the repository's release-retention policy. Release assets are durable audit
records, not write-once storage, so checksums, permissions, and consistency audits
must make later alteration or deletion detectable.
The latest 10 official releases can be selected for owner-approved application
rollback, and all forward and reverse traceability queries below are verified.

---

## Decisions Locked for v1

These are implementation decisions, not options for each subphase agent to
reinterpret.

1. **Atomic monorepo release set.** One SemVer input such as `1.2.1` identifies
   one source commit and all four deployable components. Human identities are
   component-scoped (`auth/1.2.1`, `items/1.2.1`,
   `api-gateway/1.2.1`, `frontend/1.2.1`), while the GitHub release/git tag is
   `v1.2.1`. Independently versioned components can be introduced later without
   changing the manifest schema.
2. **SHA and digest are authoritative.** The full 40-character monorepo commit
   SHA identifies source. ECR image digests identify the three backend
   artifacts. A SHA-256 checksum identifies the packaged frontend artifact.
   SemVer and mutable convenience tags are never deployment inputs.
3. **Manual SemVer assignment, no source version file.** The owner enters the
   next SemVer during `workflow_dispatch`. The workflow validates syntax,
   uniqueness, strict monotonic ordering, and that the candidate is newer than
   the previous official release. `semantic-release`/`release-please` is not
   added in v1.
4. **Promote; never rebuild.** Release, production, and rollback consume the
   digests/frontend archive emitted by one successful `main` workflow run. A
   release workflow must not check out a different ref and rebuild artifacts.
5. **GitHub is the release and approval control plane.** A protected GitHub
   Environment named `production` has the sole owner as required reviewer.
   Because the project has one owner, prevent-self-review must remain disabled.
   GitHub Release assets are the indefinitely retained release record.
6. **Official publication follows production verification.** Preparation and
   approval do not create an official release. The workflow creates/publishes
   `v<version>` only after the exact approved artifacts are healthy in
   production. Failed promotions retain diagnostic workflow evidence but are
   not official releases.
7. **Frontend is not an ECR image.** It remains a separately hosted static S3 /
   CloudFront artifact as required by `03_REQUIREMENTS_HOSTING.md`. It is
   packaged once, checksummed, retained with the GitHub Release, and deployed
   assets-first/index-last. The three Java services use ECR.
8. **Application rollback is not database rollback.** Rollback changes ECS task
   definitions and frontend content only. A release containing a schema change
   must not be promoted until a forward/backward-compatible migration and
   recovery procedure has been reviewed. Pass 3 must never run the destructive
   staging bootstrap against production.
9. **Monotonic promotion.** Promoting an older `main` candidate after a newer
   official release is rejected. This keeps SemVer, git ancestry, and ECR
   `pushedAt` retention order aligned. Older releases are reached only through
   rollback, not by minting a new release label for old bits.
10. **One production mutation at a time.** Promotion and rollback share the
    same GitHub Actions concurrency group with `cancel-in-progress: false`.
    Superseding or cancelling an in-progress production mutation is an explicit
    operator action, never automatic concurrency behavior.
11. **Canonical artifact producer.** The first trusted, successful `main` push
    that publishes `sha-<full-sha>` owns those bytes. A rerun may revalidate and
    reuse them but must not rebuild and overwrite them or claim to have produced
    them. Evidence records both the artifact-producing run/attempt and the run
    that performed staging validation. Existing SHA tags produced from any other
    event/ref fail closed.
12. **Restricted v1 release labels.** v1 accepts canonical stable SemVer only:
    `MAJOR.MINOR.PATCH`, with no leading zeroes, prerelease identifiers, build
    metadata, or optional leading `v`. This keeps Git, GitHub, ECR, S3, and JSON
    identities identical and avoids Docker-tag encoding and SemVer precedence
    ambiguity. Broader SemVer support requires an explicit version-to-storage
    encoding contract.
13. **Recoverable monorepo mutation.** ECS circuit-breaker rollback protects one
    service, not the four-component release set. Promotion and rollback therefore
    snapshot the exact pre-operation ECS/frontend state, record progress, and
    compensate to that snapshot when a later component fails. A mixed deployment
    is an incident, never a successful or official release.

---

## Release Manifest Contract

Add a versioned JSON Schema and validate every candidate/release manifest
against it. The minimum contract is:

```json
{
  "schemaVersion": 1,
  "release": {
    "version": "1.2.1",
    "gitTag": "v1.2.1",
    "status": "official",
    "createdAt": "<RFC-3339 UTC>",
    "sourceSha": "<full-40-character-sha>",
    "repository": "<owner/repository>",
    "candidateWorkflow": {
      "runId": 123456789,
      "runAttempt": 1,
      "url": "<github-run-url>",
      "event": "push",
      "ref": "refs/heads/main",
      "conclusion": "success"
    },
    "artifactWorkflow": {
      "runId": 123456789,
      "runAttempt": 1,
      "url": "<github-run-url>",
      "event": "push",
      "ref": "refs/heads/main",
      "conclusion": "success"
    },
    "stagingValidation": {
      "job": "e2e-staging",
      "conclusion": "success",
      "validatedAt": "<RFC-3339 UTC>"
    },
    "promotionWorkflow": {
      "runId": 123456790,
      "actor": "<github-login>",
      "approvedBy": "<github-login>",
      "approvedAt": "<RFC-3339 UTC>",
      "deployedAt": "<RFC-3339 UTC>"
    }
  },
  "components": {
    "auth": {
      "identity": "auth/1.2.1",
      "sourceSha": "<full-sha>",
      "repository": "onlineshop-auth",
      "imageDigest": "sha256:<digest>",
      "candidateTag": "sha-<full-sha>",
      "releaseTag": "release-1.2.1",
      "sbom": "auth.spdx.json",
      "taskDefinitionArn": "<production-task-definition-arn>"
    },
    "items": {
      "identity": "items/1.2.1",
      "sourceSha": "<full-sha>",
      "commonSourceSha": "<same-full-sha>",
      "repository": "onlineshop-items",
      "imageDigest": "sha256:<digest>",
      "candidateTag": "sha-<full-sha>",
      "releaseTag": "release-1.2.1",
      "sbom": "items.spdx.json",
      "taskDefinitionArn": "<production-task-definition-arn>"
    },
    "apiGateway": {
      "identity": "api-gateway/1.2.1",
      "sourceSha": "<full-sha>",
      "repository": "onlineshop-api-gateway",
      "imageDigest": "sha256:<digest>",
      "candidateTag": "sha-<full-sha>",
      "releaseTag": "release-1.2.1",
      "sbom": "api-gateway.spdx.json",
      "taskDefinitionArn": "<production-task-definition-arn>"
    },
    "frontend": {
      "identity": "frontend/1.2.1",
      "sourceSha": "<full-sha>",
      "artifact": "frontend-dist.tar.gz",
      "sha256": "<artifact-checksum>",
      "sbom": "frontend.spdx.json",
      "releasePrefix": "_releases/v1.2.1/",
      "versionMarker": "release.json"
    }
  }
}
```

The implementation may add fields but must not silently rename or remove these
fields without incrementing `schemaVersion` and updating fixtures, lookup tools,
and documentation.

---

## Implementation Subphases

Each subphase has an independent verification gate and commit boundary. An
agent must not tick a task or commit until its gate passes. All AWS CLI commands
must include `--profile dpm-profile --region eu-north-1`; every AWS mutation
must be followed immediately by a read-back. Never print secret values.

### 3.1 Release contract and local validation foundation

- [x] Add the release manifest JSON Schema, valid/invalid fixtures, and a local
  validator with deterministic error messages.
- [x] Add local helpers for SemVer validation/comparison, full-SHA validation,
  manifest checksums, and component/repository mapping. Do not parse security-
  sensitive JSON with regex or ad-hoc shell string concatenation.
- [x] Pass dispatch inputs through environment variables or argument arrays only
  after strict validation. Never interpolate them directly into shell, JSON,
  GitHub CLI, or AWS CLI commands.
- [x] Encode the atomic release identity decisions above, including the rule
  that `items.commonSourceSha == release.sourceSha`, every component SHA matches
  it, and component identities, versions, repositories, and tags agree.
- [x] Define candidate and official manifest states. Only the promotion
  workflow may convert a validated candidate record to `official`; enforce
  state-specific required/forbidden fields in the schema.
- [x] Add tests for malformed SemVer, duplicate/non-increasing versions,
  abbreviated/invalid SHAs, missing component fields, cross-field mismatches,
  digest/checksum errors, prerelease/build-metadata labels, unsafe input
  characters, and unsupported schema versions.
- [x] Document the exact generated files and which are source-controlled versus
  ephemeral workflow output.

**Verification gate:** schema validation tests pass; every valid fixture is
accepted; each invalid fixture fails for the intended reason; ShellCheck (and
format/lint tooling used by the selected implementation language) passes.

**Commit:** `feat(release): define release manifest contract`

### 3.2 Candidate build evidence and immutable artifacts

- [ ] Serialize the current singleton staging mutation/teardown path with
  `cancel-in-progress: false`, clear teardown ownership, and tests proving a
  newer `main` push cannot race an older run's cleanup.
- [ ] Extend the successful `main` build to emit one candidate evidence bundle
  only after Auth, Items, API Gateway, frontend, and cloud staging E2E all pass.
- [ ] Record the exact run ID/attempt, event, `refs/heads/main`, full SHA, actor,
  artifact-producing run/attempt, staging-validation run/attempt, test
  conclusions, ECR repository/digest for each backend, and frontend archive
  checksum. Do not infer digests or producer identity from tags later.
- [ ] Add standard OCI labels to all backend images:
  `org.opencontainers.image.revision`, `.source`, `.created`, `.title`, and a
  project build-run label. For Items, record the same monorepo SHA as the
  included `common` revision.
- [ ] Make SHA publishing idempotent under immutable ECR tags: if
  `sha-<full-sha>` already exists, do not push rebuilt bytes. Reuse it only when
  its source and producer labels identify a trusted successful `main` push, all
  three backends form one canonical producer set, and recorded digests match;
  otherwise fail closed. This preserves dynamic `.created`/build-run labels
  without pretending a rerun can reproduce the old digest.
- [ ] Package `frontend/dist` reproducibly as `frontend-dist.tar.gz`, generate
  it with `VITE_API_URL=''`, generate a sorted per-file checksum manifest plus
  archive SHA-256, and upload it with candidate evidence. Normalize archive
  metadata and reject traversal, links, or device-file entries before extraction.
- [ ] Record the GitHub artifact ID and service-reported digest. Consume by exact
  run ID, attempt, artifact ID, and name; reject expired/duplicate artifacts and
  verify both the service digest and checksummed bundle contents.
- [ ] Generate SPDX JSON SBOMs with a pinned Syft version (or an equivalently
  pinned established tool) from the resolved container digests and frontend
  artifact. Pin release-critical third-party Actions by full commit SHA with a
  version comment.
- [ ] Retain non-official candidate evidence for 30 days. The promotion phase
  copies the selected evidence into GitHub Release assets for indefinite
  retention.

**Verification gate:** a feature-branch-safe workflow test (temporary `push`
trigger if needed) creates a schema-valid candidate bundle; checksums verify;
OCI labels and all three ECR digests read back correctly; the bundle names the
successful staging E2E job and canonical producer; artifact IDs/digests verify;
rerunning reuses rather than rebuilds canonical artifacts; concurrent-main
fixtures prove staging serialization.
Remove any temporary trigger before commit.

**Commit:** `feat(ci): publish immutable release candidate evidence`

### 3.3 ECR release tagging, immutability, and least privilege

- [ ] Change each backend repository to immutable tags with narrowly scoped
  mutable exclusions only for `main-latest` and `branch-*`. SHA and
  `release-*` tags must be immutable.
- [ ] Implement server-side promotion of the already recorded image manifest
  from `sha-<full-sha>` to `release-<version>`; never pull/rebuild an image to
  release it. Verify both tags resolve to the exact recorded digest.
- [ ] Reject an existing GitHub `v<version>`, ECR `release-<version>`, frontend
  release prefix, or manifest identity before making any mutation. Unexpected
  collisions fail closed and are not overwritten. An interrupted promotion may
  resume only when every existing partial object exactly matches the validated
  manifest and the workflow records the recovery path.
- [ ] Keep `latest` absent for v1. If added later, configure it as an explicit
  mutable exclusion and update it only after the official GitHub Release is
  published and production verification succeeds.
- [ ] Split/limit GitHub OIDC permissions by job purpose where practical. Scope
  ECR operations to the three repository ARNs; keep only inherently unscopable
  actions such as `ecr:GetAuthorizationToken` on `Resource: "*"`. Scope
  `iam:PassRole` to the ECS execution/task roles with
  `iam:PassedToService=ecs-tasks.amazonaws.com`.
- [ ] Give validation jobs no AWS or repository-write permissions. Give the
  production job only required ECS/ECR/S3/CloudFront access and the publication
  job only `contents: write`; untrusted build steps must never retain production
  credentials or release-write permission.
- [ ] Update the OIDC trust policy for the exact protected environment subject
  used by the production job, in addition to the required `main` subject.
  Validate the actual OIDC `sub`; do not guess it.
- [ ] Run IAM Access Analyzer policy validation (or equivalent AWS validation)
  before applying policy changes.

**Verification gate:** repository settings read back as intended; attempts to
overwrite SHA/release tags fail; convenience tags can advance; a dry fixture or
disposable non-release tag resolves to the expected digest without minting an
official `release-*` tag; OIDC succeeds only from intended refs/environment;
all mutation read-backs are captured without secrets.

**Commit:** `feat(release): enforce immutable ECR release tags`

### 3.4 Controlled staging-to-production promotion workflow

- [ ] Add a dedicated manual promotion workflow on the default branch. Inputs
  are `version` and a successful candidate `run_id` (or full SHA plus an
  unambiguous run lookup); never accept an image tag or digest typed by hand.
- [ ] Preflight before AWS mutation:
  - validate SemVer and manifest schema;
  - confirm the selected run is a successful `push` run on `main` at the exact
    SHA and contains successful cloud staging E2E evidence;
  - confirm SHA is a descendant of the last official release and is reachable
    from current `main`;
  - verify all digests, checksums, OCI revisions, SBOMs, and release-name
    uniqueness;
  - reject any production database/schema change without the migration review
    required by Decision 8.
- [ ] Run an early read-only preflight, then repeat all identity, ancestry,
  artifact-existence, uniqueness, compatibility, and current-production checks
  after environment approval and concurrency-lock acquisition. Only this second
  snapshot authorizes mutation, closing approval/queue time-of-check races.
- [ ] Treat the successful Pass 2 staging job for the exact candidate run as
  the staging gate. Do not spend money by rebuilding and redeploying the same
  candidate merely to repeat the gate. A deliberate `revalidate` input may
  recreate staging and rerun E2E without changing artifact identity.
- [ ] Put the production mutation job behind the `production` Environment and
  shared non-cancelling production concurrency group. Validate that repository
  plan/visibility supports required reviewers before relying on the gate;
  restrict it to `main`, disable bypass where supported, verify configuration by
  API, and derive `approvedBy` from GitHub deployment evidence rather than user
  input or `github.actor`.
- [ ] Snapshot exact pre-promotion desired counts, capacity strategy, service and
  task-definition ARNs, running digests, ALB wiring, frontend marker/checksum,
  and official release. Record each completed mutation for deterministic resume
  or compensation.
- [ ] Handle the repository's normal paused-production state explicitly. Do not
  call `resume-playground.sh` blindly because it starts old task definitions.
  Recreate/verify ALB wiring if needed, register digest-pinned definitions before
  scaling, and restore prior cost state only after evidence is finalized.
- [ ] Register new production task definition revisions by copying the current
  definitions and replacing only the intended container image with
  `<registry>/<repository>@sha256:<digest>`. Validate the sanitized diff so
  secrets remain in `secrets[].valueFrom`, no secret becomes plaintext, and
  unrelated runtime configuration cannot drift. Preserve distinct execution-
  role/task-role duties and enable/verify container `versionConsistency`.
- [ ] Deploy Auth and Items, wait for health, then API Gateway, wait for ALB
  health, then frontend. Configure/verify ECS deployment circuit breaker with
  rollback, `minimumHealthyPercent=100`, `maximumPercent=200`, appropriate JVM
  health-check grace, and Fargate platform `LATEST`/`1.4.0`.
- [ ] Bind each waiter to the task definition/deployment started by this run. A
  generically stable service or circuit-breaker rollback is not success; verify
  the intended deployment is `COMPLETED`, healthy, and running exact digests.
- [ ] Upload frontend assets to an immutable release prefix first, verify
  checksums, and retain it as rollback source. Because current Vite output uses
  root `/assets/...` URLs, publish content-addressed assets to the live root
  without `--delete`, then publish root `release.json` and `index.html` last.
  Preserve old hashed assets, invalidate SPA entry paths (`/*` is one acceptable
  wildcard), and verify uncached and CloudFront-served marker, SPA, asset, and
  API health.
- [ ] Verify running ECS task `imageDigest` values, service task-definition
  ARNs, frontend checksum/version marker, ALB health, and production E2E/smoke
  tests before publication.
- [ ] After production verification, create the three immutable
  `release-<version>` tags server-side from the validated manifests and verify
  they resolve to the running digests. This step is idempotently resumable only
  for exact digest matches; a different existing digest fails closed.
- [ ] Publish `v<version>` at the selected SHA only after production succeeds.
  Attach the final manifest, schema version, three container SBOMs, frontend
  SBOM/archive, checksum file, sanitized test evidence, and deployment result.
  Record dispatcher, environment approver, timestamps, and workflow URLs.
- [ ] On failure, capture diagnostics and leave the previous official release
  identifiable. Compensate changed ECS services and frontend root to the exact
  snapshot in reverse order and verify restored digests/checksum and health. If
  compensation fails, stop with a mixed-state incident record. Do not publish
  an official release, delete forensic evidence, or mutate the database.
- [ ] Make finalization resumable. If ECR release tagging or GitHub Release
  publication fails after production health succeeds, reconcile partial objects
  only against the recorded SHA/digests; never mint a different version for the
  already deployed bits or call an unrecorded deployment official.

**Verification gate:** exercise validation-only failure cases; promote a
controlled release; prove the environment approval is required and owner-only;
prove no rebuild occurs; compare candidate, task, and release digests; run
production smoke/E2E tests; inject a late-component failure to prove whole-set
compensation; test paused-production and interrupted-finalization recovery; and
inspect the complete GitHub Release assets and audit trail.

**Commit:** `feat(release): add approved production promotion`

### 3.5 Existing production environment hardening

- [ ] Inventory the existing production VPC, ECS cluster/services, Service
  Connect namespace, ALB/target group, RDS, Secrets Manager references, log
  groups, task/execution roles, and frontend S3/CloudFront resources. Update
  the explicit non-secret production config; do not create duplicate prod.
- [ ] Prove production and staging use separate VPCs, clusters, RDS instances,
  security groups, namespaces, secrets, services, target groups, and lifecycle
  entry points. Remove any stale documentation that says they share resources.
- [ ] Tighten security groups and IAM to observed needs. Keep database private,
  use Secrets Manager `secrets[].valueFrom` with full ARNs where JSON keys are
  selected, and keep execution role and task role responsibilities separate.
- [ ] Replace the public S3 website origin with an S3 REST origin plus CloudFront
  Origin Access Control, then block direct public bucket access while preserving
  SPA fallback through CloudFront. If a verified constraint blocks migration,
  record the explicit v1 exception and compensating controls.
- [ ] Enable/verify ECS circuit-breaker rollback and safe rolling parameters on
  all production services. Keep Fargate Spot as the explicit v1 cost tradeoff;
  document that desired count 1 plus Spot is not a high-availability SLA.
- [ ] Validate task CPU/memory combinations, `awsvpc`, named Service Connect
  ports, log configuration, health checks, graceful termination, and no
  floating image references in the newly registered release task definitions.
- [ ] Verify CloudTrail management-event coverage for ECS, ECR, S3, CloudFront,
  IAM, and Secrets Manager mutations; retain sanitized AWS request IDs with the
  GitHub evidence so both audit planes can be correlated.
- [ ] Ensure production lifecycle helpers cannot call clean-staging database
  creation/bootstrap/deletion paths. Add tests for environment guards and
  sanitized task-definition transforms.
- [ ] Record the current backup limitation explicitly. Before the first schema-
  changing production release, adopt a versioned migration tool such as Flyway
  and define backup/restore and compatibility gates; do not improvise SQL from
  the release workflow.

**Verification gate:** read-only inventory and config consistency checks pass;
shell tests/ShellCheck pass; each safe AWS hardening mutation is read back;
services reach steady state and health checks pass; secret values never appear
in diffs, logs, artifacts, or task-definition plaintext fields.

**Commit:** `fix(deploy): harden production release target`

### 3.6 Owner-approved rollback

- [ ] Add a separate manual rollback workflow that selects an existing official
  `v<version>`, never arbitrary tags/digests. Fetch and schema/checksum-validate
  its release assets and confirm all required ECR digests/frontend archive
  still exist before approval.
- [ ] Resolve targets only from the intersection of the latest 10 complete
  official sets across all backend repositories and frontend prefixes. Reject
  metadata-only, partially retained, draft, or tampered releases.
- [ ] Show a pre-approval summary of current versus target component identities,
  digests, task definitions, frontend checksum, source SHAs, and database-
  compatibility warning.
- [ ] Use the same protected `production` Environment and non-cancelling
  production concurrency group as forward promotion.
- [ ] Repeat target/current-state validation after approval and lock acquisition,
  derive the approver from GitHub evidence, snapshot pre-rollback state for
  compensation, and handle paused production exactly as forward promotion does.
- [ ] Register new task-definition revisions pinned to the selected official
  digests and restore frontend from the retained immutable archive/prefix. Do
  not move or depend on mutable tags and do not create a new official release.
- [ ] Apply the same deployment ordering, waiters, circuit breaker, health,
  E2E/smoke, diagnostics, and read-back rules as forward promotion.
- [ ] Write a rollback result artifact recording requester, approver, from/to
  releases, exact artifacts, timestamps, workflow URL, and outcome. Annotate
  the deployment/audit record without editing the immutable original release
  manifest.
- [ ] If rollback fails, stop further automatic mutation, preserve diagnostics,
  compensate changed components to the pre-rollback snapshot, and report actual,
  pre-operation, and last-known-good states. If compensation also fails, leave a
  clear mixed-state incident. Never reverse the database automatically.

**Verification gate:** validate rejection of unknown/expired/tampered releases;
perform release N → N-1 → N in a controlled test; confirm exact backend digests
and frontend checksum after each transition; confirm approval/audit records and
production health.

**Commit:** `feat(release): add approved immutable rollback`

### 3.7 Traceability queries and operator evidence

- [ ] Provide read-only commands/scripts for:
  - commit SHA → candidate run, digests, and any official releases;
  - release version → source SHA, components, evidence, SBOMs, and artifacts;
  - running environment → task-definition ARN, image digest, release identity,
    frontend checksum, deployment/rollback run, and approver;
  - image digest → ECR tags, OCI revision, candidate run, and release identity.
- [ ] Query ECS task `containers[].imageDigest`; do not report only the task
  definition's tag or URI. Resolve frontend identity from a deployed immutable
  version marker/checksum, not cache headers.
- [ ] When production is intentionally paused and has no tasks, report that state
  and resolve selected task-definition digests plus last verified deployment
  evidence; never fabricate a running digest.
- [ ] Make lookup output machine-readable JSON with an optional concise human
  view. Missing, ambiguous, or contradictory mappings must exit non-zero.
- [ ] Add offline fixture tests and a read-only live smoke test. AWS lookup
  commands still require the mandatory profile/region and identity preflight.
- [ ] Add a consistency audit that validates GitHub Release manifest ↔ ECR
  digest/tags ↔ ECS running digest ↔ frontend checksum and reports drift
  without modifying it.

**Verification gate:** demonstrate all four lookups in both directions against
fixtures and the controlled official release; introduce fixture drift and prove
the audit fails clearly; ShellCheck/lint passes; live commands are read-only.

**Commit:** `feat(release): add release traceability queries`

### 3.8 Retention and rollback-window enforcement

- [ ] Design lifecycle rules against real multi-tag fixtures before applying
  them. An official digest has both `sha-*` and `release-*`; a broad 30-day SHA
  rule must not delete one of the newest 10 official release images.
- [ ] Give the `release-*` keep-10 rule highest priority and prove with AWS ECR
  evaluator fixtures that retained multi-tag release images cannot be selected
  by lower-priority candidate rules. Do not treat a lifecycle rule as a generic
  negative/exclusion filter.
- [ ] Keep the most recent 10 `release-*` images per backend repository, expire
  non-official SHA/branch/main candidates after approximately 30 days, and
  expire untagged images after a short documented grace period.
- [ ] Preview each ECR lifecycle policy and review the exact candidate image IDs
  before `put-lifecycle-policy`; then read back the policy. Account for ECR's
  delayed evaluation and manifest-list/referrer behavior.
- [ ] Retain GitHub Releases, final manifests, SBOMs, checksums, and sanitized
  audit/test evidence indefinitely. Configure candidate-only artifacts for 30
  days and staging-failure diagnostics according to their existing shorter
  operational retention.
- [ ] Keep frontend archives/prefixes for the same latest-10 immediate rollback
  window. Never delete the currently deployed or previous known-good frontend
  artifact. GitHub Release assets remain the long-term source even after the
  immediate S3/ECR window expires.
- [ ] Add a read-only retention audit that lists the exact 10 immediately
  rollback-capable releases and fails if any required backend/frontend artifact
  is missing. Never claim an older metadata-only release is immediately
  rollback-capable.

**Verification gate:** policy fixtures prove official multi-tag protection;
live lifecycle previews match the intended set; applied policies read back;
the rollback-window audit reports 10 or all existing releases when fewer than
10 exist; a protected release is never selected by a non-official expiry rule.

**Commit:** `feat(release): enforce artifact retention policy`

---

## Dependency and Safe Parallelization Map

```text
3.1 release contract
 ├── 3.2 candidate evidence ──── 3.3 ECR identity ──┐
 │             └──────────────── 3.7 lookups ───────┼── 3.6 rollback
 └── 3.5 production hardening ──────────────────────┤
                  3.2 + 3.3 + 3.5 ─────────────── 3.4 promotion
                                      3.4 + 3.7 ────┘
                                      3.3 + 3.6 ─────── 3.8 retention
```

- `3.1` is first and exclusive because it defines contracts consumed by every
  other phase.
- After `3.1`, `3.2` and `3.5` can run in parallel only if agents own disjoint
  files and coordinate any shared documentation/config changes.
- `3.3` depends on candidate digest behavior from `3.2`.
- The offline portion of `3.7` can start after `3.1`; live lookup verification
  waits for `3.2` and `3.3`.
- `3.4` waits for `3.2`, `3.3`, and `3.5`. It owns production mutation while it
  runs; no other phase may mutate ECS/ECR/frontend production state then.
- `3.6` waits for one verified official release from `3.4` and the live lookup
  support from `3.7`.
- `3.8` is last because retention must be proven against the final tag model and
  rollback contract. It is the only phase allowed to apply lifecycle policies.
- With a shared worktree, do not run agents that edit the same workflow,
  lifecycle scripts, configs, or documentation concurrently. Parallelizable
  architecture does not make overlapping git commits safe.

---

## Integrated Verification Before Pass 3 Completion

- [ ] Run `./mvnw clean test` inside every affected Java service directory and
  `e2e-tests/`; run frontend install/lint/build/tests as defined by the project.
- [ ] Run ShellCheck and repository script tests for every changed shell script;
  validate all workflow YAML, JSON schemas/manifests, IAM policies, and ECR
  lifecycle policies.
- [ ] Run the release candidate → approval → production → rollback → forward
  recovery scenario using exact immutable artifacts.
- [ ] Verify production and staging lifecycle start/stop paths still work and
  remain isolated. Do not leave staging/RDS/ALB billable after tests.
- [ ] Run the traceability consistency audit from commit, release, digest, and
  running deployment entry points.
- [ ] Inspect `git diff --check`, changed-file scope, documentation links, and
  secret scanning. No credentials, passwords, OIDC tokens, or secret values may
  appear in git, workflow logs, release assets, or explanation examples.
- [ ] Update the plan checkboxes, `PLAN.md`, `WHAT-WAS-DONE.md`,
  `executed/INFO.md`, `AWS_COMMANDS_GUIDE.md`, `docs/CI_CD_GOTCHAS.md`, affected
  service-level `AGENTS.md` files, and every recursively referenced document.
- [ ] Write the detailed Pass 3 explanation/manual test guide only after actual
  implementation and live verification. It must include expected UI/CLI output,
  promotion/approval/rollback examples, traceability examples, failure modes,
  cost cleanup, and explicit database-rollback limitations.
- [ ] Commit the final documentation only after the full test matrix passes.

---

## Issues and Risks

### Resolved by this plan ✅

| Issue | Resolution |
|---|---|
| ✅ Plan proposed creating production even though it already exists | Pass 3 inventories and hardens the existing isolated production environment. |
| ✅ Frontend was incorrectly counted as an ECR image | Frontend is a checksummed static artifact retained in GitHub/S3; only three backend components use ECR. |
| ✅ Release strategy was left as two incompatible options | v1 uses manually assigned, workflow-validated atomic SemVer without source version files. |
| ✅ Promotion could rebuild different bytes | The selected successful `main` run emits digests/archive once; all later stages consume that evidence. |
| ✅ A failed staging/deploy attempt could look official | GitHub Release publication occurs only after approved production verification. |
| ✅ Rollback depended on a human choosing a mutable tag | Rollback accepts only a validated official release manifest and deploys digests/checksums. |
| ✅ Retention ignored images carrying both SHA and release tags | Lifecycle policies require multi-tag fixtures, preview, and official-rule protection before application. |
| ✅ Parallel/cancelled workflow races were unspecified | Promotion and rollback share one non-cancelling production concurrency group. |
| ✅ SHA-tag reruns conflicted with dynamic build labels | The canonical producer is recorded separately; trusted reruns reuse its exact bytes and record their own validation identity. |
| ✅ Per-service rollback could leave a mixed monorepo release | Every production mutation snapshots pre-state and compensates the full changed set on a later failure. |
| ✅ Approval and uniqueness checks could become stale while queued | Promotion and rollback repeat authoritative preflight after approval and lock acquisition. |
| ✅ Frontend immutable-prefix wording did not match Vite root asset URLs | The immutable prefix is the rollback source; live hashed assets are copied without deletion and marker/index are published last. |
| ✅ Paused production behavior was undefined | Workflows activate digest-pinned definitions without transiently starting stale task definitions and preserve the prior cost state. |

### Open implementation risks

| Issue | Required handling |
|---|---|
| GitHub required-reviewer support varies by repository visibility/plan | Verify entitlement before implementation. If unavailable, stop and choose an auditable owner-only approval mechanism; do not silently replace approval with an unprotected dispatch. |
| Existing ECR repositories/tags may conflict with immutable-with-exclusion settings | Inventory and simulate first. Preserve existing SHA identities; never delete or overwrite merely to make migration pass. |
| A pre-existing SHA tag may have been produced by a feature/manual run | Accept only a producer proven to be a successful trusted `main` push; fail closed and require explicit remediation for any collision. |
| Existing production task definitions may contain drift or floating tags | Sanitize and diff copies; pin new revisions by digest and preserve secret references/roles/network settings. |
| There is no mature production database migration/rollback mechanism | Block schema-changing releases until Flyway (or equivalent), compatibility rules, and recovery procedure exist. Application rollback must not pretend to reverse data. |
| Desired-count-one Fargate Spot production can be interrupted | Accept explicitly for v1 cost goals, configure safe rolling/circuit breaker behavior, and document that this is not HA. |
| Frontend root replacement is not transactionally atomic | Use immutable archives/prefixes, assets-first/index-last, retain hashed assets, version marker/checksum verification, and CloudFront invalidation. |
| GitHub Releases/assets are privileged mutable objects, not WORM storage | Minimize release-write permission, checksum every asset, audit for drift/deletion, and treat cryptographic/WORM archival as later hardening. |
| Existing documentation from Pass 2 contains stale shared-cluster wording | Correct it only after the in-flight Pass 2 documentation changes settle; propagate the final isolated-environment truth recursively. |

---

## Cost Impact

Production ECS, RDS, ALB, S3, and CloudFront already exist, so Pass 3 does not
add another environment. The primary incremental costs are storage for three
backend release images/SBOMs and frontend archives, plus temporary runtime cost
during verification. GitHub Actions/Release storage should remain within the
repository's available quota but must be measured rather than assumed.

| Addition | Expected impact |
|---|---|
| Latest 10 releases × 3 backend images | Low ECR storage cost; measure actual compressed sizes |
| Latest 10 frontend archives/prefixes | Low S3 storage cost; measure actual artifact sizes |
| Manifests, SBOMs, evidence | Small GitHub Release/storage footprint |
| Staging revalidation | Temporary RDS, Fargate Spot, ALB, logs, and public IPv4 cost only when explicitly requested |
| Production verification | Existing production resources; brief extra task capacity during safe rolling deployment |

Staging and production must remain isolated. Sharing the production database
with staging is rejected: the small saving is not worth the blast radius or
invalid test evidence. Tear staging down after validation and pause production
when it is not actively used, according to the existing lifecycle scripts.

---

## Out of Scope for Pass 3

- Automated release-note/version calculation (`release-please`, Changesets, or
  `semantic-release`); v1 validates manual SemVer at workflow time
- Cryptographic image signing/transparency-log policy enforcement; SBOMs,
  checksums, immutable identities, least privilege, and audit evidence are in
  scope, while keyless signing is a future hardening step
- Automatic database downgrade or destructive schema rollback
- Independent per-component release cadence; the schema leaves room for it
- Slack/email notifications, dashboards, nightly validation, merge queue, and
  the full operational runbook set (Pass 4)
- Custom domain and HTTPS changes (Pass 4, optional under current requirements)
