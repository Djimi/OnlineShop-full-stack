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

- [x] Serialize the current singleton staging mutation/teardown path with
  `cancel-in-progress: false`, clear teardown ownership, and tests proving a
  newer `main` push cannot race an older run's cleanup.
- [x] Extend the successful `main` build to emit one candidate evidence bundle
  only after Auth, Items, API Gateway, frontend, and cloud staging E2E all pass.
- [x] Record the exact run ID/attempt, event, `refs/heads/main`, full SHA, actor,
  artifact-producing run/attempt, staging-validation run/attempt, test
  conclusions, ECR repository/digest for each backend, and frontend archive
  checksum. Do not infer digests or producer identity from tags later.
- [x] Add standard OCI labels to all backend images:
  `org.opencontainers.image.revision`, `.source`, `.created`, `.title`, and a
  project build-run label. For Items, record the same monorepo SHA as the
  included `common` revision.
- [x] Make SHA publishing idempotent under immutable ECR tags: if
  `sha-<full-sha>` already exists, do not push rebuilt bytes. Reuse it only when
  its source and producer labels identify a trusted successful `main` push, all
  three backends form one canonical producer set, and recorded digests match;
  otherwise fail closed. This preserves dynamic `.created`/build-run labels
  without pretending a rerun can reproduce the old digest.
- [x] Package `frontend/dist` reproducibly as `frontend-dist.tar.gz`, generate
  it with `VITE_API_URL=''`, generate a sorted per-file checksum manifest plus
  archive SHA-256, and upload it with candidate evidence. Normalize archive
  metadata and reject traversal, links, or device-file entries before extraction.
- [x] Record the GitHub artifact ID and service-reported digest. Consume by exact
  run ID, attempt, artifact ID, and name; reject expired/duplicate artifacts and
  verify both the service digest and checksummed bundle contents.
- [x] Generate SPDX JSON SBOMs with a pinned Syft version (or an equivalently
  pinned established tool) from the resolved container digests and frontend
  artifact. Pin release-critical third-party Actions by full commit SHA with a
  version comment.
- [x] Retain non-official candidate evidence for 30 days. The promotion phase
  copies the selected evidence into GitHub Release assets for indefinite
  retention.

**Verification gate:** (offline part implemented and green — see
`tests/scripts/candidate_evidence_test.sh`: schema-valid candidate bundle
fixtures, checksums, workflow serialization/teardown-ownership/OCI-label/SHA-pin
static checks, publish-reuse-failclosed decisions, reproducible packaging, safe
extraction, artifact identity/digest recording. The live half of the gate — real
ECR label read-back, three real digests, a real GitHub artifact ID and its
service-reported digest, real Syft scans, and a live rerun reusing rather than
rebuilding — is **deferred** to the consolidated verification pass and is not
claimed here.)

**Commit:** `feat(ci): publish immutable release candidate evidence`

### 3.3 ECR release tagging, immutability, and least privilege

- [x] Change each backend repository to immutable tags with narrowly scoped
  mutable exclusions only for `main-latest` and `branch-*`. SHA and
  `release-*` tags must be immutable. *(offline: desired-state config
  `ecr/immutable-repositories.json` + apply/verify scripts + gate green; the
  live `put-image-tag-mutability` mutation is deferred to the consolidated
  verification pass.)*
- [x] Implement server-side promotion of the already recorded image manifest
  from `sha-<full-sha>` to `release-<version>`; never pull/rebuild an image to
  release it. Verify both tags resolve to the exact recorded digest.
- [x] Reject an existing GitHub `v<version>`, ECR `release-<version>`, frontend
  release prefix, or manifest identity before making any mutation. Unexpected
  collisions fail closed and are not overwritten. An interrupted promotion may
  resume only when every existing partial object exactly matches the validated
  manifest and the workflow records the recovery path.
- [x] Keep `latest` absent for v1. If added later, configure it as an explicit
  mutable exclusion and update it only after the official GitHub Release is
  published and production verification succeeds.
- [x] Split/limit GitHub OIDC permissions by job purpose where practical. Scope
  ECR operations to the three repository ARNs; keep only inherently unscopable
  actions such as `ecr:GetAuthorizationToken` on `Resource: "*"`. Scope
  `iam:PassRole` to the ECS execution/task roles with
  `iam:PassedToService=ecs-tasks.amazonaws.com`. *(offline: per-purpose policy
  documents, role-layout map, and structural validation are done; the roles are
  not created yet and the workflow still assumes the single
  `github-actions-onlineshop` role — creation and switch-over are deferred to
  the consolidated verification pass.)*
- [x] Give validation jobs no AWS or repository-write permissions. Give the
  production job only required ECS/ECR/S3/CloudFront access and the publication
  job only `contents: write`; untrusted build steps must never retain production
  credentials or release-write permission.
- [x] Update the OIDC trust policy for the exact protected environment subject
  used by the production job, in addition to the required `main` subject.
  Validate the actual OIDC `sub`; do not guess it. *(the trust policy document
  is updated offline; the actual `sub` is decoded from a real production
  job's JWT and applied live in the consolidated verification pass — it is not
  claimed here.)*
- [x] Run IAM Access Analyzer policy validation (or equivalent AWS validation)
  before applying policy changes. *(not run yet: the offline gate performs
  structural least-privilege validation of the source-controlled documents;
  `aws iam validate-policy` runs in the consolidated verification pass before
  any policy is applied live.)*

**Verification gate:** (offline part implemented and green — see
`tests/scripts/ecr_release_tagging_test.sh`: immutable-repository desired-state
config, `IMMUTABLE_WITH_EXCLUSION` apply + read-back with drift fail-closed,
digest-preserving server-side `promote-image-digest.sh` mint/reuse/conflict/
dry-run behavior, release-identity proceed/resume/collision fixtures, IAM
least-privilege and OIDC-trust policy validation against the real policy
documents, workflow job-permission and tag-family static checks, and the
mandatory profile/region + read-back static scan. The live half of the gate —
ECR repository settings read back against the real repositories, real
`put-image-tag-mutability`/`batch-get-image`/`put-image` behavior, attempts to
overwrite SHA/release tags failing in real ECR, a real OIDC environment subject
verified from an actual job's JWT, and the IAM Access Analyzer
`aws iam validate-policy` run — is **deferred** to the consolidated
verification pass and is not claimed here.)

**Commit:** `feat(release): enforce immutable ECR release tags`

### 3.4 Controlled staging-to-production promotion workflow

- [x] Add a dedicated manual promotion workflow on the default branch. Inputs
  are `version` and a successful candidate `run_id` (or full SHA plus an
  unambiguous run lookup); never accept an image tag or digest typed by hand.
  *(offline: `.github/workflows/promote-release.yml` — `workflow_dispatch` with
  `version` + `run_id` inputs; `release_contract.promotion dispatch` rejects an
  image tag/digest; the workflow is static-checked and not executed during this
  substep.)*
- [x] Preflight before AWS mutation:
  - validate SemVer and manifest schema;
  - confirm the selected run is a successful `push` run on `main` at the exact
    SHA and contains successful cloud staging E2E evidence;
  - confirm SHA is a descendant of the last official release and is reachable
    from current `main`;
  - verify all digests, checksums, OCI revisions, SBOMs, and release-name
    uniqueness;
  - reject any production database/schema change without the migration review
    required by Decision 8.
  *(offline: `release_contract.promotion preflight` + `promotion-preflight.sh`
  — the fixture-tested decision layer covers every bullet; `SCHEMA_CHANGE_
  UNREVIEWED` blocks an unreviewed DB change.)*
- [x] Run an early read-only preflight, then repeat all identity, ancestry,
  artifact-existence, uniqueness, compatibility, and current-production checks
  after environment approval and concurrency-lock acquisition. Only this second
  snapshot authorizes mutation, closing approval/queue time-of-check races.
  *(offline: the workflow runs a read-only `preflight` job before the protected
  Environment and the `promote` job runs the full preflight against a fresh
  production snapshot after approval/lock; `snapshot-production.sh` is
  read-only.)*
- [x] Treat the successful Pass 2 staging job for the exact candidate run as
  the staging gate. Do not spend money by rebuilding and redeploying the same
  candidate merely to repeat the gate.
  *(offline: the preflight run-evidence decision requires the manifest's
  `stagingValidation.job == e2e-staging` with `conclusion == success` and the
  selected GitHub run's `e2e-staging` job conclusion `success`; the workflow
  consumes the candidate evidence artifact by the exact producing run attempt
  and never invokes a build/push action — a static check proves
  `publish-candidate-image.sh` / `build-push-action` never appear in the
  promotion workflow.)*
- [x] Put the production mutation job behind the `production` Environment and
  shared non-cancelling production concurrency group. Validate that repository
  plan/visibility supports required reviewers before relying on the gate;
  restrict it to `main`, disable bypass where supported, verify configuration by
  API, and derive `approvedBy` from GitHub deployment evidence rather than user
  input or `github.actor`.
  *(offline: the `promote` job uses `environment: production`, the workflow
  uses the shared `production-mutation` concurrency group with
  `cancel-in-progress: false`, and the official-manifest step derives
  `approvedBy` from `actions/runs/{run}/approvals` (state `approved` on the
  `production` environment), failing closed if unresolvable; the gate statically
  checks all three. The live required-reviewer entitlement check is deferred to
  the consolidated verification pass.)*
- [x] Snapshot exact pre-promotion desired counts, capacity strategy, service and
  task-definition ARNs, running digests, ALB wiring, frontend marker/checksum,
  and official release. Record each completed mutation for deterministic resume
  or compensation.
  *(offline: `snapshot-production.sh` (read-only) + `release_contract.promotion
  snapshot` validate the required fields; the snapshot is uploaded as a
  workflow artifact so a failed promotion can compensate/resume.)*
- [x] Handle the repository's normal paused-production state explicitly. Do not
  call `resume-playground.sh` blindly because it starts old task definitions.
  Recreate/verify ALB wiring if needed, register digest-pinned definitions before
  scaling, and restore prior cost state only after evidence is finalized.
  *(offline: the snapshot records `paused` honestly and `verify-production.sh`
  fails closed on a paused environment (`RUNNING_TASKS_MISSING`) rather than
  fabricating success; the live resume logic is deferred to the consolidated
  verification pass.)*
- [x] Register new production task definition revisions by copying the current
  definitions and replacing only the intended container image with
  `<registry>/<repository>@sha256:<digest>`. Validate the sanitized diff so
  secrets remain in `secrets[].valueFrom`, no secret becomes plaintext, and
  unrelated runtime configuration cannot drift. Preserve distinct execution-
  role/task-role duties and enable/verify container `versionConsistency`.
  *(offline: `deploy-production.sh` copies the current definition, runs
  `sanitize-task-definition.sh` (image-only diff, full-ARN `secrets[].valueFrom`,
  no plaintext) and `validate-task-definition.sh` (digest-pinned, `version-
  Consistency=enabled`, distinct roles, circuit breaker) before registering, and
  reads the registration back; the gate exercises the dry-run path with the AWS
  stub.)*
- [x] Deploy Auth and Items, wait for health, then API Gateway, wait for ALB
  health, then frontend. Configure/verify ECS deployment circuit breaker with
  rollback, `minimumHealthyPercent=100`, `maximumPercent=200`, appropriate JVM
  health-check grace, and Fargate platform `LATEST`/`1.4.0`.
  *(offline: `deploy-production.sh` validates the canonical order
  auth+items → api-gateway → frontend via `release_contract.promotion plan`
  (`PLAN_ORDER_INVALID`) and the safe-rolling parameters before any update;
  `validate-task-definition.sh` enforces the health check/grace contract.)*
- [x] Bind each waiter to the task definition/deployment started by this run. A
  generically stable service or circuit-breaker rollback is not success; verify
  the intended deployment is `COMPLETED`, healthy, and running exact digests.
  *(offline: `release_contract.promotion waiter` fails closed on
  `DEPLOYMENT_ID_MISMATCH`, `WAITER_TD_MISMATCH`, `DEPLOYMENT_NOT_COMPLETED`,
  and `WAITER_DIGEST_MISMATCH`; `deploy-production.sh` binds the waiter to the
  deployment id this run started.)*
- [x] Upload frontend assets to an immutable release prefix first, verify
  checksums, and retain it as rollback source. Because current Vite output uses
  root `/assets/...` URLs, publish content-addressed assets to the live root
  without `--delete`, then publish root `release.json` and `index.html` last.
  Preserve old hashed assets, invalidate SPA entry paths (`/*` is one acceptable
  wildcard), and verify uncached and CloudFront-served marker, SPA, asset, and
  API health.
  *(offline: `publish-frontend.sh` + `release_contract.promotion frontend` —
  `FRONTEND_DELETE_FORBIDDEN`, `FRONTEND_PREFIX_MISSING`, `FRONTEND_ORDER_-
  INVALID` (assets-first/index-last), and `FRONTEND_INVALIDATION_MISSING`; the
  plan requires the immutable prefix, the no-`--delete` live root, and a
  CloudFront invalidation.)*
- [x] Verify running ECS task `imageDigest` values, service task-definition
  ARNs, frontend checksum/version marker, ALB health, and production E2E/smoke
  tests before publication.
  *(offline: `verify-production.sh` + `release_contract.promotion verify` fail
  closed on `RUNNING_DIGEST_MISMATCH`/`SERVICE_TD_MISMATCH`/`FRONTEND_MARKER_-
  MISMATCH`/`ALB_UNHEALTHY`; the gate exercises the read-only path with a
  stateful AWS stub.)*
- [x] After production verification, create the three immutable
  `release-<version>` tags server-side from the validated manifests and verify
  they resolve to the running digests. This step is idempotently resumable only
  for exact digest matches; a different existing digest fails closed.
  *(offline: `finalize-release.sh` calls `promote-image-digest.sh` (server-side
  mint/reuse/fail-closed) and `release_contract.promotion finalize` —
  `RELEASE_TAG_CONFLICT` fails closed; `action=resume` on exact partial
  objects.)*
- [x] Publish `v<version>` at the selected SHA only after production succeeds.
  Attach the final manifest, schema version, three container SBOMs, frontend
  SBOM/archive, checksum file, sanitized test evidence, and deployment result.
  Record dispatcher, environment approver, timestamps, and workflow URLs.
  *(offline: `finalize-release.sh` refuses publication unless
  `PROMOTION_PRODUCTION_VERIFIED=true` (`PUBLICATION_BEFORE_VERIFICATION`) and
  attaches `release-manifest.json`, the four SBOMs, `frontend-dist.tar.gz`, and
  `checksums.txt` from the candidate evidence; the live `gh release create`
  is deferred.)*
- [x] On failure, capture diagnostics and leave the previous official release
  identifiable. Compensate changed ECS services and frontend root to the exact
  snapshot in reverse order and verify restored digests/checksum and health. If
  compensation fails, stop with a mixed-state incident record. Do not publish
  an official release, delete forensic evidence, or mutate the database.
  *(offline: the workflow has a `compensate` job (`if: failure()` on the
  `promote` job) that reads the snapshot artifact and calls
  `compensate-production.sh`; `release_contract.promotion compensate` builds
  the reverse-order plan and fails closed when the snapshot cannot restore a
  changed component.)*
- [x] Make finalization resumable. If ECR release tagging or GitHub Release
  publication fails after production health succeeds, reconcile partial objects
  only against the recorded SHA/digests; never mint a different version for the
  already deployed bits or call an unrecorded deployment official.
  *(offline: `release_contract.promotion finalize` reconciles existing ECR
  release tags / git tag / frontend prefix marker against the recorded
  SHA/digests and returns `action=resume` only when every existing partial
  object exactly matches; any mismatch fails closed.)*

**Verification gate:** (offline part implemented and green — see
`tests/scripts/promotion_test.sh`: 51 Python unit tests for the promotion
decision layer; the decision-layer CLI exercised against valid/invalid fixtures
for dispatch/run/ancestry/preflight/snapshot/plan/waiter/frontend/verify/
finalize/compensate; promote-release.yml static checks (dispatch inputs,
`production` Environment, shared non-cancelling `production-mutation`
concurrency group, no rebuild, preflight repeated post-approval, compensate on
failure, SHA-pinned Actions); shell-script runs against a stateful AWS + `gh`
stub (preflight passes/fails closed, snapshot read-only, verify passes and
fails closed on digest/marker/ALB drift, finalize dry-run + production-verified
gate, compensate reverse-order plan, deploy dry-run with sanitize); mandatory
profile/region + mutation read-back + no-secrets static scan; and
ruff/shellcheck/git diff --check. The live half of the gate — the actual
owner-approved promotion against real AWS/GitHub, the real `production`
Environment approval and required-reviewer check, real ECR/ECS/S3/CloudFront
mutations and read-backs, and the real GitHub Release publication — is
**deferred** to the consolidated verification pass and is not claimed here.)

**Commit:** `feat(release): add approved production promotion`

#### Pass 3R.1 repair overlay (offline contract)

The 3R.1 repair tightens the existing v1 promotion handoff without changing
the manifest schema or staging design. The workflow boundary is
workflow-level `contents: read` with explicit job-scoped permission opt-ins;
untrusted GitHub contexts are transferred through step `env`, validated for
event-specific refs/full SHAs/IDs, and passed to shell only as quoted
variables/argv. Pull-request paths do not bootstrap AWS credentials or publish
ECR images. The structural PR/trusted-job split is Pass 3R.2/3R.3 and the
purpose-specific role cutover is Pass 3R.9.

Promotion now consumes a schema-valid candidate manifest **without** current
task-definition ARNs plus a read-only production snapshot. The snapshot is the
sole source of current service ARNs. `deploy-production.sh` emits a deployment
manifest containing the newly registered ARNs; only that output is rendered as
official, and production verification still precedes finalization. The
optional `source_sha` selector must match the downloaded candidate evidence.

Candidate run evidence is bound to the exact run and attempt. The gather path
uses GitHub's attempt-scoped workflow-run and jobs endpoints, validates the
response `id` and `run_attempt` as matching positive JSON numbers, and
normalizes the REST response's bare `head_branch: "main"` to
`refs/heads/main`. The unscoped/latest jobs endpoint is not accepted.

The production snapshot fails closed unless the actual live frontend marker,
matching immutable `_releases/v<version>/` marker/index, full-object S3
`ChecksumSHA256`, and exact canonical `v<version>` Git tag/source SHA agree;
annotated tags are peeled before comparison. Frontend publication, rollback
restore, and compensation request `--checksum-algorithm SHA256` on every S3
write; snapshot decodes canonical full-object checksum metadata and never
falls back to an ETag.

The offline evidence is:

```bash
bash tests/scripts/ci_security_contract_test.sh
bash tests/scripts/promotion_handoff_test.sh
bash tests/scripts/promotion_test.sh
bash tests/scripts/rollback_test.sh
```

These static/stateful checks do not claim live AWS, staging, GitHub approval,
role cutover, or GitHub Release publication; those remain deferred to the
explicit Pass 3R live checkpoints, culminating in Pass 3R.10.

### 3.5 Existing production environment hardening

- [x] Inventory the existing production VPC, ECS cluster/services, Service
  Connect namespace, ALB/target group, RDS, Secrets Manager references, log
  groups, task/execution roles, ECR repositories, and frontend S3/CloudFront
  resources. Update the explicit non-secret production config; do not create
  duplicate prod.
  *(offline: read-only `scripts/inventory-production.sh` compares every
  configured non-secret identifier to live state — including execution-role
  existence, ECR repository existence, and RDS non-public accessibility — and
  fails closed on drift; an AWS read that fails is reported as `error` (never
  disguised as a missing resource). `scripts/config/production.env` now carries
  the explicit namespace, log groups, secrets, execution role, ECR
  repositories, frontend bucket and CloudFront distribution. The live run
  against the real production account is deferred to the consolidated
  verification pass.)*
- [x] Prove production and staging use separate VPCs, clusters, RDS instances,
  security groups, namespaces, secrets, services, target groups, and lifecycle
  entry points. Remove any stale documentation that says they share resources.
  *(offline: `scripts/verify-production-staging-separation.sh` compares the two
  non-secret configs AND live observed state (identifier identity + VPC/Cloud
  Map topology) and fails closed on any shared resource; the stale
  shared-cluster Pass 2 narrative is already marked historical. The live run is
  deferred to the consolidated verification pass.)*
- [x] Tighten security groups and IAM to observed needs. Keep database private,
  use Secrets Manager `secrets[].valueFrom` with full ARNs where JSON keys are
  selected, and keep execution role and task role responsibilities separate.
  *(offline: the task-definition validator enforces full-ARN
  `secrets[].valueFrom`, and `sanitize-task-definition.sh` proves secrets never
  become environment/command plaintext. Security-group/IAM hardening mutations
  are deferred to the consolidated pass.)*
- [x] Replace the public S3 website origin with an S3 REST origin plus CloudFront
  Origin Access Control, then block direct public bucket access while preserving
  SPA fallback through CloudFront. If a verified constraint blocks migration,
  record the explicit v1 exception and compensating controls. *(offline: S3
  REST + OAC migration/hardening tooling `scripts/migrate-frontend-oac.sh`
  (mutation + immediate read-back, fail closed) and read-only
  `scripts/verify-frontend-oac.sh` are implemented and stub-tested. The apply
  run starts with a no-lockout precondition gate — the current bucket policy
  must already grant public read or the CloudFront OAC, so the origin switch
  can never create an outage window — and waits (bounded) for the asynchronous
  CloudFront deployment to reach `Deployed` before tightening the bucket policy.
  The live migration is NOT applied here — it runs in the consolidated pass
  after a fail-closed verify gate. No verified constraint currently blocks it;
  the constraint record lives in
  `explanations/PRODUCTION-HARDENING-DECISIONS.md`.)*
- [x] Enable/verify ECS circuit-breaker rollback and safe rolling parameters on
  all production services. Keep Fargate Spot as the explicit v1 cost tradeoff;
  document that desired count 1 plus Spot is not a high-availability SLA.
  *(offline: `release_contract.ecs_config` validates circuit breaker
  enable+rollback, `minimumHealthyPercent=100`, `maximumPercent=200`, capacity
  provider strategy, and Service Connect port names; the Spot tradeoff is
  documented in `explanations/PRODUCTION-HARDENING-DECISIONS.md`. Live
  service read-back is deferred to the consolidated pass.)*
- [x] Validate task CPU/memory combinations, `awsvpc`, named Service Connect
  ports, log configuration, health checks, graceful termination, and no
  floating image references in the newly registered release task definitions.
  *(offline: `release/bin/validate-task-definition.sh` + the
  `release_contract.ecs_config` fixture suite cover all of these; digest-pinned
  images and `versionConsistency=enabled` are enforced. Live TD read-back is
  deferred to the consolidated pass.)*
- [x] Verify CloudTrail management-event coverage for ECS, ECR, S3, CloudFront,
  IAM, and Secrets Manager mutations; retain sanitized AWS request IDs with the
  GitHub evidence so both audit planes can be correlated. *(offline:
  `scripts/verify-cloudtrail-coverage.sh` + `release_contract.cloudtrail` audit
  management-event/multi-region/logging/delivery coverage against fixtures;
  management selectors cover all control-plane APIs and are not a per-service
  enumeration, and "delivery" is proven by a configured target plus a
  confirmed `LatestDeliveryTime` with no delivery error. The live read-back of
  the real trail is deferred to the consolidated pass; retaining the request
  IDs is a promotion-phase behaviour, not part of this read-only audit.)*
- [x] Ensure production lifecycle helpers cannot call clean-staging database
  creation/bootstrap/deletion paths. Add tests for environment guards and
  sanitized task-definition transforms. *(offline: the staging-only helpers in
  `scripts/lib/lifecycle.sh` now fail fast (`lc_require_environment staging ||
  return 1`) so they can never reach a mutation even in a conditional call
  context; the gate statically proves the production entry points never invoke
  them and tests both guard and sanitize behaviour. This fixes a pre-existing
  unsafe assumption.)*
- [x] Record the current backup limitation explicitly. Before the first schema-
  changing production release, adopt a versioned migration tool such as Flyway
  and define backup/restore and compatibility gates; do not improvise SQL from
  the release workflow. *(offline: `explanations/PRODUCTION-HARDENING-
  DECISIONS.md` documents the backup limitation and the Flyway/migration gate;
  no SQL is ever improvised — the sanctioned `scripts/ecs-run-sql.sh` path is
  the only way to reach private RDS.)*

**Verification gate:** (offline part implemented and green — see
`tests/scripts/production_hardening_test.sh`: Python suites for the task-
definition/service/secret-sanitize/OAC/CloudTrail/environment-separation
decision layers; valid/invalid task-definition and service-config fixtures
(including execution-role/task-role distinctness); sanitize diff proving
image-only changes and full-ARN `secrets[].valueFrom`; stateful AWS-stub runs
of the read-only inventory, production/staging separation (identity +
topology), frontend OAC verify, and CloudTrail coverage scripts — including
that an AWS read failure fails closed as a read `error` and is never reported
as a missing resource, and that RDS public accessibility is rejected; the
mutation OAC migration tool with per-step read-back, a no-lockout
precondition gate that refuses to mutate when the current bucket policy would
lock out CloudFront, a bounded wait for the asynchronous CloudFront deployment,
and fail-closed drift; lifecycle environment-guard tests proving the
staging-only DB helpers make no AWS call after their guard fails (even in
conditional-call contexts); a static scan for mandatory profile/region (with
the config files themselves asserted to carry exactly `dpm-profile`/`eu-north-1`),
mutation read-backs, and no secrets; and ruff/shellcheck/git diff --check. The
live half of the gate — the real production inventory read-back, the real OAC
migration, real CloudTrail read-back, live service/task-definition
verification, and security-group/IAM tightening — is **deferred** to the
consolidated verification pass and is not claimed here.)

**Commit:** `fix(deploy): harden production release target`

### 3.6 Owner-approved rollback

- [x] Add a separate manual rollback workflow that selects an existing official
  `v<version>`, never arbitrary tags/digests. Fetch and schema/checksum-validate
  its release assets and confirm all required ECR digests/frontend archive
  still exist before approval.
  *(offline: `.github/workflows/rollback-release.yml` — `workflow_dispatch`
  with a `version` input; `release_contract.rollback dispatch` rejects image
  tags/digests/SHAs; the read-only `preflight` job runs `rollback-preflight.sh`
  BEFORE the protected Environment and schema-validates the target manifest
  (`validate_data` + `TARGET_MANIFEST_INVALID`) while `select` cross-checks the
  exact ECR `release-*` digests and frontend prefix marker
  (`TARGET_ARTIFACT_MISSING`/`TARGET_ARTIFACT_MISMATCH`); the workflow is
  static-checked and never executed in this substep.)*
- [x] Resolve targets only from the intersection of the latest 10 complete
  official sets across all backend repositories and frontend prefixes. Reject
  metadata-only, partially retained, draft, or tampered releases.
  *(offline: `release_contract.rollback select` +
  `latest_complete_officials` resolve the target from the newest 10 complete
  official sets ordered by numeric version; drafts are excluded from the
  GitHub index fetch, tampered manifests fail `validate_data`, and
  `TARGET_NOT_FOUND`/`TARGET_NOT_OFFICIAL`/`TARGET_ARTIFACT_MISSING`/
  `TARGET_ARTIFACT_MISMATCH`/`TARGET_OUTSIDE_ROLLBACK_WINDOW`/
  `TARGET_IS_CURRENT` fail closed — covered by unit tests (incl. a generated
  12-release window fixture) and the gate CLI checks.)*
- [x] Show a pre-approval summary of current versus target component identities,
  digests, task definitions, frontend checksum, source SHAs, and database-
  compatibility warning.
  *(offline: `rollback-preflight.sh` prints the current-versus-target summary —
  version/gitTag/sourceSha, per-backend digests, task-definition ARNs, frontend
  checksum + prefix, the observed current release marker + its ECR digests, and
  the Decision 8 database-compatibility line; the gate asserts the exact
  summary lines.)*
- [x] Use the same protected `production` Environment and non-cancelling
  production concurrency group as forward promotion.
  *(offline: the `rollback` job uses `environment: production` and the workflow
  uses the shared `production-mutation` group with `cancel-in-progress: false`;
  the gate statically checks all three.)*
- [x] Repeat target/current-state validation after approval and lock acquisition,
  derive the approver from GitHub evidence, snapshot pre-rollback state for
  compensation, and handle paused production exactly as forward promotion does.
  *(offline: the `rollback` job re-runs the full `rollback-preflight.sh` against
  a fresh observed snapshot after approval/lock (time-of-check race closure),
  derives `approvedBy` from `actions/runs/{run}/approvals` (state `approved` on
  the `production` environment) failing closed when unresolvable — never
  `github.actor` — and snapshots via the shared `snapshot-production.sh`
  (records `paused` honestly); `verify-rollback.sh` fails closed on a paused
  environment with `RUNNING_TASKS_MISSING`, never fabricating success; the gate
  tests the paused fail-closed path.)*
- [x] Register new task-definition revisions pinned to the selected official
  digests and restore frontend from the retained immutable archive/prefix. Do
  not move or depend on mutable tags and do not create a new official release.
  *(offline: `deploy-rollback.sh` copies the current (pre-rollback) definitions
  from the snapshot and replaces only the intended container image via
  `sanitize-task-definition.sh` (image-only diff, full-ARN
  `secrets[].valueFrom`, no plaintext) + `validate-task-definition.sh`
  (digest-pinned, `versionConsistency=enabled`, distinct roles, circuit
  breaker) before registering, with immediate read-back;
  `restore-frontend.sh` re-points the live root from the retained immutable
  `_releases/v<version>/` prefix (marker + index.html last, no `--delete`,
  CloudFront invalidation, read-back); the rollback IAM policy has no ECR
  image-write permission and no rollback tool ever mints a tag or creates an
  official release — a static scan proves `promote-image-digest.sh`/
  `gh release create`/image-write actions never appear.)*
- [x] Apply the same deployment ordering, waiters, circuit breaker, health,
  E2E/smoke, diagnostics, and read-back rules as forward promotion.
  *(offline: `deploy-rollback.sh` updates auth → items → api-gateway in
  canonical order with circuit breaker + `minimumHealthyPercent=100`/
  `maximumPercent=200` enforced by `release_contract.ecs_config`, waiters bound
  to the deployment/task-definition started by this run
  (`release_contract.rollback waiter` reuses the promotion contract —
  `DEPLOYMENT_ID_MISMATCH`/`WAITER_TD_MISMATCH`/`DEPLOYMENT_NOT_COMPLETED`/
  `WAITER_DIGEST_MISMATCH` fail closed) and running-digest read-back;
  `verify-rollback.sh` verifies running `containers[].imageDigest`, service
  task-definition ARNs, frontend marker, and ALB health against the deployment
  manifest — the same verification decision as forward promotion.)*
- [x] Write a rollback result artifact recording requester, approver, from/to
  releases, exact artifacts, timestamps, workflow URL, and outcome. Annotate
  the deployment/audit record without editing the immutable original release
  manifest.
  *(offline: `record-rollback-result.sh` + `release_contract.rollback result`
  validate the record (requester/approver logins — both mandatory tool inputs,
  never defaulted to the run actor — run id, workflow URL, from/to
  identities with exact digests + frontend checksum, startedAt/completedAt,
  outcome, `productionVerified`, audit annotation) and decide
  write/resume idempotency with `RESULT_CONFLICT` fail-closed; the workflow
  uploads the record as a separate artifact — the immutable original release
  manifest is never edited.)*
- [x] If rollback fails, stop further automatic mutation, preserve diagnostics,
  compensate changed components to the pre-rollback snapshot, and report actual,
  pre-operation, and last-known-good states. If compensation also fails, leave a
  clear mixed-state incident. Never reverse the database automatically.
  *(offline: the workflow has an automatic (non-approval-gated) `compensate`
  job (`if: failure() && needs.rollback.result == 'failure'`) that consumes the
  snapshot from this run's artifact and calls the shared
  `compensate-production.sh` with all changed components including frontend;
  `release_contract.rollback compensate` reuses the promotion reverse-order
  plan and fails closed when the snapshot cannot restore a changed component;
  no rollback tool touches the database.)*

**Verification gate:** (offline part implemented and green — see
`tests/scripts/rollback_test.sh`: 391 Python unit tests; the
`release_contract.rollback` decision-layer CLI exercised against valid/invalid
fixtures for dispatch/select/schema/frontend-restore/result plus the reused
snapshot/plan/verify/compensate decisions; rollback-release.yml static checks
(dispatch inputs, `production` Environment, shared non-cancelling
`production-mutation` concurrency group, no rebuild and no tag minting or
release publication, full preflight repeated post-approval, read-only
pre-approval preflight job with job-scoped `id-token: write` for its read-only
ECR/S3 scope — any configure-aws-credentials job must declare it, `approvedBy`
from `actions/runs/{run}/approvals` never `github.actor`, target manifest
consumed from the exact producing run via download-artifact pinned to this
run's `run-id`, post-approval revalidation fail-closed byte comparison,
snapshot/restore/verify wiring, automatic compensate incl. the inline
`--changed` JSON array the workflow passes, SHA-pinned Actions); shell-script
runs against a stateful AWS + `gh` stub (preflight summary + fail-closed
schema/artifact/identity checks, deploy dry-run with sanitize + no mutation +
identity preflight, verify ok/drift/paused fail-closed + read-only proof,
frontend restore with no-`--delete` + invalidation + read-back + dry-run,
rollback-result write/resume/conflict with mandatory requester/approver); the
mandatory profile/region + mutation read-back + no-secrets + no-tag-minting
static scan; and ruff/shellcheck/git diff --check.
The live half of the gate — the actual owner-approved rollback against real
AWS/GitHub (release N → N-1 → N with exact digests and frontend checksum after
each transition, real `production` Environment approval, real ECR/ECS/S3/
CloudFront mutations and read-backs, real frontend restoration, and the real
rollback-result artifact) — is **deferred** to the consolidated verification
pass and is not claimed here. No real AWS, GitHub, workflow, deployment,
production, or staging action was executed while implementing or verifying this
subphase.)

**Commit:** `feat(release): add approved immutable rollback`

### 3.7 Traceability queries and operator evidence

- [x] Provide read-only commands/scripts for:
  - commit SHA → candidate run, digests, and any official releases;
  - release version → source SHA, components, evidence, SBOMs, and artifacts;
  - running environment → task-definition ARN, image digest, release identity,
    frontend checksum, deployment/rollback run, and approver;
  - image digest → ECR tags, OCI revision, candidate run, and release identity.
  *(offline: `release/bin/trace.sh` (`commit`/`release`/`running`/`digest`/
  `audit`) plus the fixture-tested `release_contract.traceability` decision
  layer answer all four queries in both directions against the traceability
  fixtures and the controlled official release. The live read-only smoke test —
  the same commands run against real AWS/GitHub without `--observed`/`--index`
  — is deferred to the consolidated verification pass.)*
- [x] Query ECS task `containers[].imageDigest`; do not report only the task
  definition's tag or URI. Resolve frontend identity from a deployed immutable
  version marker/checksum, not cache headers. *(offline: `trace.sh running` reads
  `describe-tasks` `containers[].imageDigest` — never the task-definition image
  URI — and the frontend identity comes from the deployed `release.json` marker;
  the gate's stateful AWS stub proves the live gather issues exactly those
  reads. Live ECS read-back is deferred to the consolidated pass.)*
- [x] When production is intentionally paused and has no tasks, report that state
  and resolve selected task-definition digests plus last verified deployment
  evidence; never fabricate a running digest. *(offline: `trace.sh running` on a
  paused observed fixture reports `paused: true`, resolves each service's
  current task-definition image digest via `describe-task-definition`, and
  reports the latest official release as last verified deployment evidence; a
  running digest is never synthesized. Live paused-state read-back is deferred.)*
- [x] Make lookup output machine-readable JSON with an optional concise human
  view. Missing, ambiguous, or contradictory mappings must exit non-zero.
  *(offline: every lookup prints JSON on stdout and exits `0` only when found
  AND consistent; `NOT_FOUND`, `AMBIGUOUS_*`, `*_MISMATCH`, and
  `OBSERVED_READ_ERROR` issues exit `1`; `--human` adds a concise view on
  stderr.)*
- [x] Add offline fixture tests and a read-only live smoke test. AWS lookup
  commands still require the mandatory profile/region and identity preflight.
  *(offline: `tests/scripts/release_traceability_test.sh` covers the four
  lookups, the consistency audit, drift fixtures, paused state, a stateful AWS
  stub run of the live gather path (identity preflight + read-only proof), the
  GitHub Releases index auto-fetch, and the mandatory-profile/region static
  scan. The live smoke test is the same commands run against real AWS/GitHub in
  the consolidated verification pass.)*
- [x] Add a consistency audit that validates GitHub Release manifest ↔ ECR
  digest/tags ↔ ECS running digest ↔ frontend checksum and reports drift
  without modifying it. *(offline: `trace.sh audit` (all official releases or
  one `--version`) cross-checks the manifest's ECR `sha-*`/`release-*` tags,
  the running ECS container digests (matched release only; the running
  environment can match one release at a time), and the deployed + immutable
  per-release frontend markers, and reports deterministic drift codes without
  mutating anything.)*

**Fail-closed hardening (independent 3.7 review):**
- The newest official release and the audit's newest-first ordering are computed
  from numeric version keys, never from `compare_semver`'s sign — index order
  can never change which release is "latest".
- A running digest set that reports more than one digest for a component
  (mixed in-flight deployment) or that does not cover all three backend
  components fails closed (`RUNNING_MIXED_DIGESTS`/`RUNNING_DIGEST_INCOMPLETE`)
  instead of fabricating an identity from a last-writer-wins map.
- `trace.sh release` also verifies the immutable per-release
  `_releases/v<version>/release.json` prefix marker (`FRONTEND_PREFIX_MARKER_*
`), not only the live root marker.
- `trace.sh commit` fails closed when a `sha-<sha>` tag resolves to different
  bytes than a manifest records (`ECR_SHA_DIGEST_MISMATCH`) or when manifests
  recording the SHA disagree on the candidate run (`CANDIDATE_RUN_CONFLICT`).
- The image digest lookup's OCI revision is attributed to the release manifest
  (`ociRevisionSource: "release-manifest"`), never claimed as a live label read
  (`ociRevisionObservedFromImage: false`); `describe-images` cannot read the
  image config blob.
- A configured production service omitted by `describe-services` (or returned
  without a `taskDefinition`) is recorded as a read `error` marker and fails
  closed as `OBSERVED_READ_ERROR`; malformed frontend markers are never treated
  as valid drift-free state.

**Verification gate:** (offline part implemented and green — see
`tests/scripts/release_traceability_test.sh`: Python unit suites for the
lookup/audit decision layer; all four lookups in both directions against the
traceability fixtures and the controlled official release; valid/paused state
passes and every drift fixture (ECR tag digest, ECS running digest, frontend
marker) fails closed with its intended issue code; missing/ambiguous/
contradictory mappings exit non-zero; machine-readable JSON + `--human` view;
a stateful AWS-stub run of the live gather path proving the mandatory identity
preflight, exactly the intended read-only ECR/ECS/S3 calls, and no mutating
call; the read-only GitHub Releases index auto-fetch via a `gh` stub (proving
the exact `release-manifest.json` asset is selected, never a decoy whose name
merely contains "manifest"); fail-closed partial-API handling (a configured
service omitted by `describe-services` becomes an `OBSERVED_READ_ERROR`);
newest-first ordering proven independent of index order; mixed/incomplete
running digests, `sha-*` digest mismatch, and candidate-run conflict fixtures;
by-version immutable prefix-marker verification; malformed frontend markers
failing closed instead of crashing; the mandatory profile/region static scan
and no-secrets scan; and ruff/shellcheck/git diff --check. The live half of
the gate — the read-only smoke test of all four lookups + audit against real
production AWS state and real GitHub Releases — is **deferred** to the
consolidated verification pass and is not claimed here.)

**Commit:** `feat(release): add release traceability queries`

### 3.8 Retention and rollback-window enforcement

- [x] Design lifecycle rules against real multi-tag fixtures before applying
  them. An official digest has both `sha-*` and `release-*`; a broad 30-day SHA
  rule must not delete one of the newest 10 official release images.
  *(offline: the desired policy `release/ecr/lifecycle-policy.json` is designed
  and proven against `fixtures/retention/images-multitag.json`, where every one
  of the 12 release-tagged images per repository is pushed BEFORE the 30-day
  cutoff — the newest 10 by push order are kept by rule 1, and the multi-tag
  fixture proves a 30-day-old release image inside the window is never
  selected by a candidate rule.)*
- [x] Give the `release-*` keep-10 rule highest priority and prove with AWS ECR
  evaluator fixtures that retained multi-tag release images cannot be selected
  by lower-priority candidate rules. Do not treat a lifecycle rule as a generic
  negative/exclusion filter.
  *(offline: `release_contract.retention` models ECR's real first-match-wins
  semantics — an image is expired by exactly one or zero rules and an image
  matching a higher-priority rule's tagging requirements can never be expired
  by a lower-priority rule (verified against the AWS ECR user guide); the
  keep-10 rule has priority 1 and the candidate families (`sha-`,
  `main-latest`, `branch-`) are enumerated by lower-priority age rules, never
  by an exclusion filter — ECR's schema actually rejects a bare
  `tagStatus: tagged` rule, so the generic rule is not even expressible. The
  evaluator fixtures (`evaluator-ok/-protected-expiring/-disagreement.json`)
  prove retained multi-tag digests cannot be selected and that any preview
  disagreement or protected image expiring fails closed.)*
- [x] Keep the most recent 10 `release-*` images per backend repository, expire
  non-official SHA/branch/main candidates after approximately 30 days, and
  expire untagged images after a short documented grace period.
  *(offline: the desired policy keeps the newest 10 `release-*` images (rule 1,
  `imageCountMoreThan 10`), expires `sha-*` candidates (rule 2), the mutable
  `main-latest` (rule 3) and `branch-*` (rule 4) convenience tags after 30 days,
  and expires untagged images after a 14-day documented grace period (rule 5,
  rationale in `explanations/RETENTION-DECISIONS.md`); every tagged rule
  selects exactly ONE tag prefix because AWS documents that a multi-entry
  `tagPrefixList` selects only images carrying ALL the listed tags ("only the
  images with all specified tags are selected") — a merged `main-latest,
  branch-` rule would silently select nothing, so each family gets its own
  single-prefix rule and the validator rejects merged lists
  (`POLICY_TAGPREFIX_MULTI`); the gate statically asserts the rule order, the
  single-prefix property, the 30-day counts, and the 14-day untagged grace.)*
- [x] Preview each ECR lifecycle policy and review the exact candidate image IDs
  before `put-lifecycle-policy`; then read back the policy. Account for ECR's
  delayed evaluation and manifest-list/referrer behavior.
  *(offline: `preview-retention-policy.sh` previews the exact expiration
  candidates — offline via the modeled evaluation, live (deferred) via ECR's
  own `start/get-lifecycle-policy-preview` dry-run, which deletes nothing and
  is proven read-only with the stateful AWS stub; `apply-retention-policy.sh`
  runs `--dry-run` by default and the `--apply` path is REFUSED offline
  (requires the consolidated-pass gate env `ONLINESHOP_RETENTION_LIVE_APPLY=1`)
  with an immediate `get-lifecycle-policy` read-back compared byte-for-byte
  (fail-closed drift); ECR's delayed evaluation (a lifecycle evaluation can
  take up to 24 hours) and the manifest-list/referrer behavior are documented
  in `explanations/RETENTION-DECISIONS.md`.)*
- [x] Retain GitHub Releases, final manifests, SBOMs, checksums, and sanitized
  audit/test evidence indefinitely. Configure candidate-only artifacts for 30
  days and staging-failure diagnostics according to their existing shorter
  operational retention.
  *(offline: `release_contract.retention retention-classes` models the classes
  (releases/manifests/SBOMs/checksums/audit evidence = indefinite,
  candidate artifacts = 30 days, staging-failure diagnostics and release
  result records = 14 days) with ok/invalid fixtures, and the gate statically
  asserts the already-configured `retention-days` in `build-and-deploy.yml`
  (candidate evidence 30, staging-failure diagnostics 14) and
  `promote-release.yml`/`rollback-release.yml` (snapshot/result records 14).
  No live GitHub setting was changed.)*
- [x] Keep frontend archives/prefixes for the same latest-10 immediate rollback
  window. Never delete the currently deployed or previous known-good frontend
  artifact. GitHub Release assets remain the long-term source even after the
  immediate S3/ECR window expires.
  *(offline: `release_contract.retention frontend-retention` models the S3
  `_releases/v<version>/` prefix plan: protected versions (window + currently
  deployed + previous known-good) are never expirable, unknown or protected
  deletions fail closed, and GitHub Release assets are documented as the
  long-term store after the window expires; S3 has no "exclude currently
  deployed" primitive, so prefix deletion is a review-gated audit decision,
  never an unattended lifecycle rule — see
  `explanations/RETENTION-DECISIONS.md`.)*
- [x] Add a read-only retention audit that lists the exact 10 immediately
  rollback-capable releases and fails if any required backend/frontend artifact
  is missing. Never claim an older metadata-only release is immediately
  rollback-capable.
  *(offline: `audit-retention-window.sh` + `release_contract.retention audit`
  reuse the 3.6 complete-set model (`latest_complete_officials`/
  `release_artifacts_issues`) to list the exact 10 (or all when fewer exist)
  immediately rollback-capable releases, failing closed with
  `RETENTION_ARTIFACT_MISSING`/`RETENTION_ARTIFACT_MISMATCH`/`OBSERVED_READ_ERROR`;
  older releases are reported in `outsideWindow` and never claimed
  rollback-capable; the keep-10 coverage check (`POLICY_WINDOW_GAP`) fails
  closed when a push-order/version-order gap (e.g. a backport) would let the
  policy expire a window release.)*

**Verification gate:** (offline part implemented and green — see
`tests/scripts/retention_test.sh`: 432 Python unit tests (incl. the multi-tag
keep-10 protection, ECR-preview validation, 12-release window audit, frontend
prefix and retention-class suites); the `release_contract.retention` CLI
against the retention fixtures (validate-policy ok/order/generic/counts/
multi-prefix, evaluate first-match-wins with the exact expire set,
validate-preview ok/protected-expiring/disagreement, audit 2-release "all" and
12-release "10" cases + missing/mismatch fail-closed, coverage in-order ok +
backport gap, frontend-retention ok/protected-delete, retention-classes
ok/invalid); the
desired-state policy static checks (keep-10 rule first with highest priority,
enumerated candidate families, 30-day candidate expiry, 14-day untagged grace,
no `any` selection, no exclusion filter, every tagged rule carries an explicit
single-entry tagPrefixList); a stateful AWS-stub run of the read-only audit
live gather
(identity preflight, exactly the intended ECR describe-images + S3 marker
reads, fail-closed on missing artifacts and on a wrong account identity); the
offline preview making no AWS call at all and the live ECR
`start/get-lifecycle-policy-preview` dry-run proven read-only with the stub
(agree → pass; a preview expiring a protected release fails closed with
`PROTECTED_IMAGE_EXPIRING`); `apply-retention-policy.sh` `--dry-run` mutating
nothing and `--apply` refused offline (the gate never runs with
`ONLINESHOP_RETENTION_LIVE_APPLY=1`), with the static put→get read-back
pairing (checked on the comment-stripped script, so comments can never
satisfy the pairing) and byte-comparison drift check; the GitHub retention-days
static
checks (candidate 30, staging-failure 14, snapshot/result records 14); the
mandatory profile/region + identity preflight + no-secrets scans; and
ruff/shellcheck/`bash -n`/`git diff --check`.
The live half of the gate — the real ECR lifecycle policy preview/apply/
read-back against real AWS (only from the consolidated live pass, which sets
`ONLINESHOP_RETENTION_LIVE_APPLY=1`), the read-only live retention audit
against real production state, and the real S3/frontend retention — is
**deferred** to the consolidated verification pass and is not claimed here. No
real AWS, GitHub, workflow, deployment, production, or staging action was
executed while implementing or verifying this subphase.)

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
  running deployment entry points. *(offline part green: `trace.sh
  commit|release|digest|running|audit` against the traceability fixtures and a
  stateful AWS stub — see the 3.7 verification gate; the live run against the
  real controlled official release and real production state is deferred to the
  consolidated verification pass.)*
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
