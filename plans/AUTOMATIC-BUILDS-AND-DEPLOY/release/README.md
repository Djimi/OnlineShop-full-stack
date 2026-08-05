# OnlineShop Release Contract — Local Validation Foundation

Subphases **3.1 + 3.2 + 3.3 + 3.5 + 3.7** of [03_RELEASE_TRACEABILITY.md](../03_RELEASE_TRACEABILITY.md).
This directory is the source-controlled release contract consumed by every
later phase (candidate evidence, promotion, rollback, traceability, retention).
It contains the versioned manifest JSON Schema, a deterministic local
validator, valid/invalid fixtures, strict shell input helpers, the candidate
build-evidence tooling, the ECR release-tagging/immutability and IAM
least-privilege tooling, the release traceability lookups and consistency
audit, automated tests, and this documentation.

> **Security rule (from the plan):** security-sensitive JSON is never parsed
> with regex or ad-hoc shell string concatenation. All manifest parsing and
> validation is performed by the Python validator (`json.load` + the pinned
> `jsonschema` engine). Shell scripts only pass validated values through
> argument arrays (`"$@"` / `ARGS=("$manifest" ...)`) or environment variables.

---

## Layout

| Path | What it is | Source-controlled |
|---|---|---|
| `schema/release-manifest.schema.json` | Versioned JSON Schema (Draft-07) encoding the manifest contract and the candidate/official state rules | ✅ yes |
| `fixtures/valid/*.json` | Manifest documents the validator must accept | ✅ yes |
| `fixtures/invalid/*.json` | Manifest documents the validator must reject, one per failure category | ✅ yes |
| `fixtures/invalid/EXPECTED.md` | Authoritative fixture → primary error code table (the tests parse it) | ✅ yes |
| `fixtures/candidate/*.json` | Existing-image, expected-context, canonical-set, manifest-builder fixtures (3.2) | ✅ yes |
| `fixtures/artifact/*.json` | GitHub artifacts-listing fixtures (3.2) | ✅ yes |
| `fixtures/serialization/*.json` | Staging serialization event timelines (3.2) | ✅ yes |
| `src/release_contract/*.py` | Python validator: schema engine, cross-field rules, deterministic error normalization | ✅ yes |
| `src/release_contract/candidate.py` | Canonical-producer reuse decision, canonical-set check, candidate-manifest builder + CLI | ✅ yes |
| `src/release_contract/artifact.py` | GitHub artifact identity resolution + evidence bundle verification + CLI | ✅ yes |
| `src/release_contract/frontend.py` | Safe tar.gz validation, sorted checksum-manifest verification + CLI | ✅ yes |
| `src/release_contract/serialization.py` | Staging mutation serialization model (offline proof) | ✅ yes |
| `src/release_contract/ecr.py` | Server-side `release-<version>` mint / reuse / fail-closed decision + post-mutation digest verification (3.3) | ✅ yes |
| `src/release_contract/releaseid.py` | Release-identity collision / interrupted-promotion resume decision (3.3) | ✅ yes |
| `src/release_contract/iam.py` | IAM least-privilege + OIDC trust policy validation (3.3) | ✅ yes |
| `ecr/immutable-repositories.json` | Desired ECR `IMMUTABLE_WITH_EXCLUSION` repository state (3.3) | ✅ yes |
| `bin/apply-immutable-repositories.sh` | Apply immutable-tag repository config with immediate read-back (3.3) | ✅ yes |
| `bin/verify-immutable-repositories.sh` | Read-only read-back of the immutable-tag config, fail-closed on drift (3.3) | ✅ yes |
| `bin/promote-image-digest.sh` | Server-side digest-preserving `sha-*` → `release-*` promotion + read-back (3.3) | ✅ yes |
| `bin/check-release-identity.sh` | Read-only GitHub tag / ECR release tag / frontend marker collision preflight (3.3) | ✅ yes |
| `fixtures/ecr/*.json` | Promotion decision fixtures (mint / reuse / conflict / missing) (3.3) | ✅ yes |
| `fixtures/releaseid/*.json` | Release-identity observed-state fixtures (3.3) | ✅ yes |
| `fixtures/iam/*.json` | Invalid IAM policy / trust fixtures (3.3) | ✅ yes |
| `bin/validate-manifest.sh` | Shell CLI wrapper over the Python validator (argv-only, strict input checks) | ✅ yes |
| `bin/release-input.sh` | Strict input-validation helpers for dispatch inputs (SemVer, SHA, URL, login, …) | ✅ yes |
| `bin/package-frontend.sh` | Reproducible `frontend-dist.tar.gz` packaging + sorted per-file checksum manifest | ✅ yes |
| `bin/unpack-frontend.sh` | Safe extraction (rejects traversal/links/devices) + checksum verification | ✅ yes |
| `bin/generate-sbom.sh` | SPDX JSON SBOM generation with a pinned, checksum-verified Syft binary | ✅ yes |
| `bin/publish-candidate-image.sh` | Idempotent `sha-<sha>` publishing decision (push / reuse / fail closed) | ✅ yes |
| `bin/image-labels.sh` | Reads an ECR image's digest + OCI labels by tag | ✅ yes |
| `bin/verify-producer-set.sh` | Verifies the three backends form one canonical producer set | ✅ yes |
| `bin/emit-candidate-evidence.sh` | Assembles the candidate evidence bundle (facts index + checksums) | ✅ yes |
| `bin/emit-candidate-manifest.sh` | Renders a schema-valid candidate manifest from evidence + owner-assigned SemVer | ✅ yes |
| `bin/record-artifact.sh` | Records the GitHub artifact ID, URL, and service-reported digest from the `actions/upload-artifact@v4` step outputs | ✅ yes |
| `tests/*.py` | Python unit + validation tests (stdlib `unittest`) | ✅ yes |
| `requirements.txt` | Pinned Python dependencies (`jsonschema`, `PyYAML`) | ✅ yes |
| `../../tests/scripts/release_contract_test.sh` | Repo-level verification gate for the 3.1 contract | ✅ yes |
| `../../tests/scripts/candidate_evidence_test.sh` | Repo-level verification gate for the 3.2 candidate evidence (incl. workflow static checks) | ✅ yes |
| `../../tests/scripts/ecr_release_tagging_test.sh` | Repo-level verification gate for the 3.3 ECR tagging / immutability / least privilege | ✅ yes |
| `bin/validate-task-definition.sh` | Shell wrapper over `release_contract.ecs_config validate-td` (3.5) | ✅ yes |
| `bin/sanitize-task-definition.sh` | Digest-pin transform + image-only diff, secrets stay in `valueFrom` (3.5) | ✅ yes |
| `src/release_contract/ecs_config.py` | Task-definition + service-config hardening validation (3.5) | ✅ yes |
| `src/release_contract/sanitize.py` | Sanitized task-definition transform and drift-proof diff (3.5) | ✅ yes |
| `src/release_contract/frontend_hosting.py` | S3 REST origin + CloudFront OAC verify + migration plan + no-lockout preconditions (3.5) | ✅ yes |
| `src/release_contract/cloudtrail.py` | CloudTrail management-event coverage audit (3.5) | ✅ yes |
| `src/release_contract/environments.py` | Production/staging separation, topology overlap, inventory drift (3.5) | ✅ yes |
| `fixtures/production/**` | Task-definition, service-config, sanitize, OAC, CloudTrail, environment fixtures (3.5) | ✅ yes |
| `../../scripts/inventory-production.sh` | Read-only production inventory + config-consistency (3.5) | ✅ yes |
| `../../scripts/verify-production-staging-separation.sh` | Read-only prod/staging separation (identity + topology) (3.5) | ✅ yes |
| `../../scripts/verify-frontend-oac.sh` | Read-only S3 REST + OAC hardening verification (3.5) | ✅ yes |
| `../../scripts/migrate-frontend-oac.sh` | S3 REST + OAC migration tool, per-step read-back, fail closed (3.5) | ✅ yes |
| `../../scripts/verify-cloudtrail-coverage.sh` | Read-only CloudTrail management-event coverage audit (3.5) | ✅ yes |
| `../../scripts/lib/identifiers.sh` | Shared non-secret identifier/topology gatherers (3.5) | ✅ yes |
| `../../tests/scripts/production_hardening_test.sh` | Repo-level verification gate for the 3.5 hardening | ✅ yes |
| `src/release_contract/traceability.py` | Traceability lookups + consistency audit decision layer and CLI (3.7) | ✅ yes |
| `bin/trace.sh` | Read-only operator CLI: `commit`/`release`/`running`/`digest`/`audit` (3.7) | ✅ yes |
| `fixtures/traceability/*.json` | Manifest index + consistent / paused / drift observed-state fixtures (3.7) | ✅ yes |
| `tests/test_traceability.py` | Python unit tests for the lookups + audit (3.7) | ✅ yes |
| `../../tests/scripts/release_traceability_test.sh` | Repo-level verification gate for the 3.7 traceability queries | ✅ yes |

**Ephemeral workflow output — never source-controlled:** candidate evidence
bundles, generated manifests, SBOMs, frontend archives, checksum files, and
GitHub artifact IDs are produced by subphase 3.2+ workflows and retained in
GitHub Actions artifacts / S3, not committed here.

---

## The manifest contract

One canonical SemVer (for example `1.2.1`) identifies one monorepo commit and
all four deployable components (`auth`, `items`, `apiGateway`, `frontend`).
The full 40-character monorepo SHA and ECR/frontend SHA-256 digests are
authoritative; SemVer and mutable tags are never deployment inputs.

The schema enforces (in addition to per-field formats):

- **Atomic identity (Decision 1):** `release.gitTag == "v" + version`, every
  component `sourceSha` and `items.commonSourceSha` equals
  `release.sourceSha`, each `identity == "<component>/<version>"`, backend
  `repository` matches the canonical `onlineshop-<service>` map, and
  `candidateTag` / `releaseTag` / `releasePrefix` are deterministic derivations
  of the SHA/version.
- **Two states (Decisions 5/6):** a `candidate` manifest must not contain
  `release.promotionWorkflow` or any backend `taskDefinitionArn`; an `official`
  manifest requires both. Only the promotion workflow (subphase 3.4) may
  convert a validated candidate to official.
- **No silent fields:** `additionalProperties: false` at every level. Adding a
  field requires bumping `schemaVersion` and updating fixtures, lookup tools,
  and this documentation.
- **Unsafe input rejection:** strings containing control characters (including
  escaped `\u0000`) are rejected before any schema validation.

---

## Validating a manifest

```bash
RELEASE=plans/AUTOMATIC-BUILDS-AND-DEPLOY/release

# Shell wrapper (recommended in workflows)
bash "$RELEASE/bin/validate-manifest.sh" manifest.json --human

# Or direct Python CLI
PYTHONPATH="$RELEASE/src" python3 -m release_contract.cli manifest.json
```

The validator prints machine-readable JSON to stdout:

```json
{
  "valid": false,
  "file": "manifest.json",
  "schemaVersion": 1,
  "issues": [
    {
      "code": "INVALID_FORMAT",
      "field": "release.version",
      "message": "invalid value '1.2'; canonical MAJOR.MINOR.PATCH, ..."
    }
  ],
  "checksum": "b5d66951...",
  "errorCount": 1
}
```

- Exit `0` = valid, `1` = invalid, `2` = usage/IO error.
- Error `code` + `field` are stable machine identifiers; `message` is
  human-readable. Output is deterministic for a given document.
- `--check-checksum <sha256>` verifies the canonical manifest checksum and
  fails when the document was altered.
- `checksum` is the SHA-256 of the canonical (sorted-key) JSON encoding, so it
  is independent of file formatting but detects any content change.

### Manifest checksums

`src/release_contract/checksums.py` provides:

- `sha256_file(path)` — generic file digest (frontend archives, SBOMs).
- `manifest_checksum(obj)` — deterministic checksum of a manifest object.
- `manifest_checksum_file(path)` — parse + checksum in one step.

### Component/repository mapping

`src/release_contract/components.py` is the single source of truth for
`identity_for`, `repository_for`, `candidate_tag_for`, `release_tag_for`,
`git_tag_for`, and `release_prefix_for`. Later subphases (promotion, rollback,
retention) must use these helpers rather than re-deriving tag names.

### Dispatch inputs (subphase 3.1 rule)

`bin/release-input.sh` validates every dispatch input **before** use:
`rl_assert_semver`, `rl_assert_full_sha`, `rl_assert_sha256_hex`,
`rl_assert_positive_integer`, `rl_assert_github_login`, `rl_assert_http_url`,
`rl_assert_regular_file`. Validated inputs are passed downstream only as
environment variables or argument-array entries, never interpolated into shell,
JSON, GitHub CLI, or AWS CLI command strings.

---

## Candidate build evidence (subphase 3.2)

The successful `main` push workflow emits exactly one candidate evidence bundle
after Auth, Items, API Gateway, frontend, and the cloud staging E2E job all
pass. See `03_RELEASE_TRACEABILITY.md` for the full decisions; the key points:

- **Canonical producer (Decision 11).** A `sha-<full-sha>` image is produced
  once by the first trusted successful `main` push. `publish-candidate-image.sh`
  decides per image: the tag is missing → push; the tag exists and its OCI
  producer labels (`org.onlineshop.producer.*`) plus a GitHub-API-verified
  successful producer run identify a trusted main push → reuse; anything else
  → fail closed. Reruns revalidate and reuse; they never rebuild and overwrite.
  `verify-producer-set.sh` then proves all three backends form one canonical
  producer set (same producer run id, revision == SHA, Items `common`
  revision == SHA).
- **OCI labels.** All backend images carry `org.opencontainers.image.revision`,
  `.source`, `.created`, `.title` plus project labels
  `org.onlineshop.component`, `org.onlineshop.build-run`, and
  `org.onlineshop.producer.*`. Items also records the same monorepo SHA as
  `org.onlineshop.common-revision` (the included `common` library revision).
  `.created`/`build-run` are dynamic, so digests are never reproduced by a
  rerun — canonical bytes are reused, not rebuilt. Labels are read from the
  image config blob via the lightweight `docker buildx imagetools inspect
  --format '{{json .Image}}'` (`docker manifest inspect --verbose` only
  references the config, it does not return it); a label read failure fails
  closed and is never treated as "tag absent".
- **Frontend archive.** `package-frontend.sh` builds `frontend-dist.tar.gz`
  with normalized metadata (sorted members, uid/gid 0, epoch mtime, `gzip -n`)
  so two builds of the same source are byte-identical, plus a sorted per-file
  checksum manifest and an archive SHA-256. Links/device files are rejected.
  `unpack-frontend.sh` (consumed by promotion) rejects traversal, links, and
  device entries *before* extraction and verifies the sorted manifest.
- **SBOMs.** `generate-sbom.sh` generates SPDX JSON with a pinned Syft version
  whose binary archive SHA-256 is verified before execution (set `SYFT_TOOL`
  to inject a preinstalled binary, e.g. in tests).
- **Evidence bundle.** `emit-candidate-evidence.sh` writes
  `candidate-evidence.json` (facts: run id/attempt, event, ref, full SHA,
  actor, per-job conclusions, ECR digests, frontend checksum, staging
  validation) plus the frontend archive/manifest, four SBOMs, and a sorted
  `checksums.txt`, then verifies the bundle. The bundle is uploaded as a
  GitHub artifact (retention 30 days); `actions/upload-artifact@v4` returns
  `artifact-id`, `artifact-url`, and `artifact-digest` (the GitHub
  service-reported SHA-256 of the uploaded archive) as step outputs, and
  `record-artifact.sh` records `{runId, runAttempt, artifactId, artifactUrl,
  artifactDigest, name}` in a separate pointer artifact — the bundle's own
  identity can never be embedded inside the bundle it describes (circular
  self-checksum). Promotion consumes the bundle by exact run id/attempt/
  artifact id/name, rejects duplicates/expired, and verifies the service
  digest against the downloaded archive plus the checksummed contents. The
  bundle attributes the bytes correctly on reruns: `candidateWorkflow`
  records the artifact-producing run (read from the images' producer labels
  via `--producer-run-id/--producer-run-attempt`, defaulting to the current
  run) while `artifactWorkflow` records the current run that performed staging
  validation and emitted the evidence. Emission is refused unless all five job
  conclusions are `success`.
- **Version is assigned at promotion.** The evidence bundle records immutable
  facts only — no SemVer. The owner assigns the next SemVer when dispatching
  promotion (Decision 3), and `emit-candidate-manifest.sh` (or
  `release_contract.candidate build-manifest`) renders a **schema-valid
  candidate manifest** from the evidence + version. The fixture tests prove
  that flow produces exactly the 3.1 valid candidate fixture.
- **Staging serialization.** The `e2e-staging` job owns resume → deploy → E2E
  → teardown in one job with `cancel-in-progress: false` job-level concurrency
  keyed on `refs/heads/main`. `serialization.py` models these semantics; the
  fixtures prove a newer `main` push is queued and can never race an older
  run's cleanup, and that teardown ownership cannot be taken by another run.

### Candidate evidence gate

```bash
bash tests/scripts/candidate_evidence_test.sh
```

Runs the Python tests (candidate/artifact/frontend/serialization), static
workflow YAML checks (serialization config, teardown ownership, OCI labels,
evidence job, SHA-pinned Actions), reproducible frontend packaging, safe
extraction, publish/reuse/fail-closed decisions, SBOM stub flow, the
evidence→candidate-manifest fixture flow, artifact identity/digest recording,
and lint (ruff + shellcheck).

### Deferred live checks

These are intentionally **not** claimed by the offline gate and are verified in
the consolidated verification pass against real AWS/GitHub state: ECR label
read-back of real pushed images, three real ECR digests, a real GitHub
artifact ID and its service-reported digest from a real workflow run, real
Syft scans of the registry digests, and a live rerun proving reuse instead of
rebuild.

---

## ECR release tagging, immutability, and least privilege (subphase 3.3)

The 3.3 offline gate is:

```bash
bash tests/scripts/ecr_release_tagging_test.sh
```

### Immutable repositories with narrow mutable exclusions

ECR tag mutability is repository-scoped, but supports exclusions: the three
backend repositories are defined with desired state
**`IMMUTABLE_WITH_EXCLUSION`** with exclusion filters exactly `main-latest`
and `branch-*` (see `ecr/immutable-repositories.json`). Every tag is immutable
by default — `sha-*` and `release-*` can never be overwritten — while the two
convenience tags may advance. `latest` stays absent for v1 (Decision 4). The
live `put-image-tag-mutability` mutation is applied in the consolidated
verification pass. `verify-immutable-repositories.sh`
is a read-only read-back (`aws ecr describe-repositories`) that fails closed on
any drift; `apply-immutable-repositories.sh` mutates via
`aws ecr put-image-tag-mutability` and immediately reads each repository back.

### Server-side digest-preserving promotion

`promote-image-digest.sh` mints `release-<version>` from the candidate tag
**server-side**: `ecr:batch-get-image` returns the exact manifest bytes already
stored under `sha-<full-sha>` and `ecr:put-image` re-tags them. Nothing is
pulled, rebuilt, or re-uploaded. The fixture-tested `release_contract.ecr`
module decides:

- `mint` — release tag absent and candidate tag resolves to the recorded digest;
- `reuse` — release tag already resolves to the recorded digest (idempotent
  resume of an interrupted promotion);
- anything else **fails closed**: release tag at different bytes, candidate tag
  missing, or candidate digest mismatch. Immutable tags are never overwritten.

After a mint the script reads both tags back and runs
`release_contract.ecr verify` to prove both resolve to the recorded digest.
`--dry-run` decides without mutating.

### Release identity collisions and resume

`check-release-identity.sh` is a read-only preflight that checks the GitHub
`v<version>` tag, the ECR `release-<version>` tag per backend, and the frontend
release-prefix `release.json` marker, then feeds the state to the
fixture-tested `release_contract.releaseid` module: `proceed` when nothing
exists, `resume` when every existing partial object exactly matches the
validated manifest, and fail-closed on any collision (a git tag at a different
SHA, a release tag at different bytes, or a frontend marker with a mismatched
version/SHA/checksum).

### Least privilege by job purpose

The per-purpose IAM policy documents and OIDC trust policy live in
`../github-actions-{candidate-build,promotion,production-deploy,rollback}-policy.json`
and `../github-actions-oidc-trust-policy.json` (see
`../github-actions-role-layout.md` for the job → role → policy map). The
`release_contract.iam` module validates them structurally: ECR actions scoped
to the three repository ARNs, `ecr:GetAuthorizationToken` only on `*`, scoped
`iam:PassRole` with the `ecs-tasks.amazonaws.com` service condition, no mutating
action on `*`, and an OIDC trust that requires `sts.amazonaws.com` plus the
`main`/`feature/*` refs and the protected `environment:production` subject. The
promotion policy deliberately has **no layer-upload actions** and the rollback
policy deliberately has **no `ecr:PutImage`**. `aws iam validate-policy` (IAM
Access Analyzer) runs before any live application in the consolidated pass.

### Deferred live checks

These are **not** claimed by the offline gate and are verified in the
consolidated pass against real AWS/GitHub state: ECR repository settings read
back as intended, real `put-image-tag-mutability` / `batch-get-image` /
`put-image` behavior (including attempts to overwrite SHA/release tags failing),
convenience tags advancing in real ECR, the actual OIDC `sub` decoded from a
production-environment job's JWT, and the IAM Access Analyzer run.

---

## Controlled staging-to-production promotion (subphase 3.4)

The offline 3.4 gate is:

```bash
bash tests/scripts/promotion_test.sh
```

Subphase 3.4 is the approved, approval-gated promotion of one verified monorepo
snapshot from staging to production. The rule set lives in
`src/release_contract/promotion.py` (pure, fixture-tested):

- `dispatch` — SemVer + numeric candidate run id; a hand-typed image tag/
  digest is rejected (`INVALID_VERSION`/`INVALID_RUN_ID`).
- `run` evidence — a successful `push` on `refs/heads/main` at the exact SHA
  with a successful cloud staging `e2e-staging` job
  (`RUN_EVENT_MISMATCH`/`RUN_REF_MISMATCH`/`RUN_SHA_MISMATCH`/
  `RUN_UNSUCCESSFUL`/`RUN_STAGING_UNSUCCESSFUL`).
- `ancestry` — the candidate SHA is a descendant of the last official release
  and reachable from current `main` (`CANDIDATE_BEHIND_OFFICIAL`/
  `CANDIDATE_NOT_ON_MAIN`/`VERSION_NOT_INCREASING`).
- `preflight` — manifest schema, run evidence, ancestry, staging gate,
  release-name uniqueness, and the Decision 8 database-change review
  (`SCHEMA_CHANGE_UNREVIEWED`).
- `snapshot` — every field needed for compensation/resume
  (`SNAPSHOT_MISSING_FIELD`).
- `plan` — canonical auth+items → api-gateway → frontend order and the safe
  rolling / circuit-breaker parameters (`PLAN_ORDER_INVALID`,
  `CIRCUIT_BREAKER_DISABLED`, `ROLLBACK_DISABLED`, `MIN_HEALTHY_PERCENT`,
  `MAX_PERCENT`).
- `waiter` — a deployment bound to the task-definition/deployment started by
  this run, COMPLETED, running the exact digests (`DEPLOYMENT_ID_MISMATCH`,
  `WAITER_TD_MISMATCH`, `DEPLOYMENT_NOT_COMPLETED`, `WAITER_DIGEST_MISMATCH`).
- `frontend` publication — assets-first/index-last, no `--delete`, immutable
  per-release prefix, CloudFront invalidation (`FRONTEND_DELETE_FORBIDDEN`,
  `FRONTEND_PREFIX_MISSING`, `FRONTEND_ORDER_INVALID`,
  `FRONTEND_INVALIDATION_MISSING`).
- `verify` — running digests, service task-definition ARNs, frontend
  marker/checksum, ALB health (`RUNNING_DIGEST_MISMATCH`,
  `SERVICE_TD_MISMATCH`, `FRONTEND_MARKER_MISMATCH`, `ALB_UNHEALTHY`).
- `finalize` — mint the three `release-<version>` tags and publish
  `v<version>` only after production verification; idempotently resumable,
  any collision fails closed (`PUBLICATION_BEFORE_VERIFICATION`,
  `RELEASE_TAG_CONFLICT`).
- `compensate` — the exact reverse-order restore plan to the pre-promotion
  snapshot.

The workflow `.github/workflows/promote-release.yml` runs a read-only
`preflight` job before the protected `production` Environment that validates
the dispatch inputs and the candidate manifest contract; the approved `promote`
job then runs the full preflight with a fresh snapshot after approval/lock
acquisition (closing the time-of-check race) before mutating, and a
`compensate` job (`if: failure()` on `promote`, not approval-gated so a
failing promotion restores itself automatically) restores the recorded
snapshot.
The candidate evidence artifact is consumed by the exact producing run attempt
(`gh run download --attempt`, duplicate/ambiguous bundles fail closed), and
`approvedBy` is derived from the environment-approval evidence
(`actions/runs/{run}/approvals`), never from `github.actor`. The shell wrappers
(`bin/promotion-preflight.sh`, `bin/snapshot-production.sh`,
`bin/deploy-production.sh`, `bin/verify-production.sh`,
`bin/publish-frontend.sh`, `bin/finalize-release.sh`,
`bin/compensate-production.sh`, `bin/check-release-identity.sh`) gather live
state with `gh`/`aws` and delegate all decisions to the fixture-tested module;
every `aws` call carries the mandatory non-overridable
`--profile dpm-profile --region eu-north-1` and every mutation is read back.
`publish-frontend.sh` writes the immutable per-release prefix marker
(`_releases/v<version>/release.json`) alongside the live-root marker.
`finalize-release.sh` mints the release tags server-side via
`promote-image-digest.sh` and refuses publication unless
`PROMOTION_PRODUCTION_VERIFIED=true`. On failure `compensate-production.sh`
restores the changed ECS services and the frontend live root (from the previous
immutable prefix), failing closed when the snapshot cannot be restored.

### Deferred live checks

**Not** claimed by the offline gate: the real owner-approved promotion against
live AWS/GitHub — the actual `production` Environment approval and
required-reviewer entitlement check, real ECR/ECS/S3/CloudFront mutations and
read-backs, the real GitHub Release publication, and switching the workflow to
the per-purpose roles.

---

## Production hardening (subphase 3.5)

The offline 3.5 gate is:

```bash
bash tests/scripts/production_hardening_test.sh
```

### Release task definitions and service configuration

`release_contract.ecs_config` validates what a production release task
definition and its ECS service must look like before they may be registered/
promoted:

- **Task definition:** `awsvpc`, Fargate, a valid task-level CPU/memory pair
  (the Fargate matrix), a **digest-pinned** image (never a floating tag),
  `versionConsistency=enabled`, a container health check, `awslogs`, **named**
  Service Connect `portMappings`, a positive `stopTimeout` (graceful
  termination), an execution role, and strict secret hygiene — every secret
  injected only through `secrets[].valueFrom` with a **full**
  `arn:aws:secretsmanager:...` ARN (the `:json-key::` selector requires the
  full ARN) and never repeated in `environment`/`command`. When a `taskRoleArn`
  is present it must differ from the execution role (execution-role and
  task-role duties stay separate).
- **Service configuration:** ECS rolling-update controller, deployment circuit
  breaker **enabled with rollback**, `minimumHealthyPercent=100`,
  `maximumPercent=200`, a capacity-provider strategy, and — with a task
  definition supplied — Service Connect `portName` values that resolve to named
  `portMappings`.

`bin/validate-task-definition.sh` is the file-based CLI wrapper (no AWS calls).

### Sanitized task-definition transforms

`bin/sanitize-task-definition.sh` copies the current definition and replaces
**only** the named containers' `image` with `<registry>/<repo>@sha256:<digest>`,
then proves the transform with `release_contract.sanitize`: every re-imaged
container is digest-pinned, nothing except `image` changed, no container was
added/removed, every `secrets[].valueFrom` is preserved as a full Secrets
Manager ARN, and no secret reference is repeated as plaintext in
`environment`/`command`. This is the subphase 3.4 "register by copying and
replacing only the image" contract.

### Production inventory and production/staging separation

`scripts/inventory-production.sh` (read-only) compares the explicit non-secret
identifiers in `scripts/config/production.env` (VPC, cluster, services,
namespace, ALB/TG, RDS including non-public accessibility, secrets, log
groups, execution role, ECR repositories, frontend S3/CloudFront) against live
observed state and fails closed on any drift. An AWS read that fails is
reported as `error` (never disguised as a missing resource) so an auth/
throttle/network failure fails the check with an honest message.
`scripts/verify-production-staging-separation.sh` proves production and staging
share no VPC, cluster, RDS, security groups, namespace, secrets, services, or
target group — first against the two configs, then against live observed
identifier **and** topology state (`release_contract.environments`:
`separation` + `topology`). Both require the mandatory identity preflight. The
execution role and ECR repositories are intentionally shared infrastructure and
are never separation violations.

### S3 REST origin + CloudFront OAC (implemented, not applied live)

`scripts/verify-frontend-oac.sh` is a read-only fail-closed check that the
frontend is served from an S3 **REST** origin behind a CloudFront Origin Access
Control, that the bucket public access block is fully enabled, that the bucket
policy grants only the `cloudfront.amazonaws.com` service principal (with
`aws:SourceArn ==` the production distribution ARN), and that the SPA fallback
(404 → 200 `/index.html`) is preserved. `scripts/migrate-frontend-oac.sh` is the
mutation tool (`--dry-run` plans; `--apply` performs each mutation with an
immediate read-back and fails closed on drift). The apply run starts with a
**no-lockout precondition gate** (`release_contract.frontend_hosting
preconditions`) — the current bucket policy must already grant public read or
the CloudFront OAC, so the origin switch can never create an outage window —
and waits (bounded) for the asynchronous CloudFront deployment to reach
`Deployed` before tightening the bucket policy. **It is not applied in 3.5** —
application happens in the consolidated Pass 3 verification pass after the
read-only gate passes.

### CloudTrail coverage

`scripts/verify-cloudtrail-coverage.sh` audits (read-only) that a trail logs
management events (management selectors cover *all* control-plane APIs —
including ECS, ECR, S3, CloudFront, IAM, Secrets Manager), is logging, is
multi-region (global IAM/CloudFront events), and delivers to S3/CloudWatch Logs
**with a confirmed `LatestDeliveryTime` and no delivery error** — the
prerequisite for correlating sanitized AWS request IDs with the GitHub evidence
plane. Capturing those request IDs during promotion is deferred to the
promotion phase; this audit only proves the trail would capture them.

### Deferred live checks

These are **not** claimed by the offline gate and are verified in the
consolidated pass against real AWS state: the production inventory read-back,
the real production/staging separation read-back, the live frontend OAC
migration, real CloudTrail read-back, live service/task-definition
verification, and security-group/IAM tightening mutations. The Fargate Spot
tradeoff, backup/migration limitation, and the explicit v1 OAC constraint
record live in
`../explanations/PRODUCTION-HARDENING-DECISIONS.md`.

---

## Owner-approved rollback (subphase 3.6)

Approval-gated rollback of production to an existing immutable official
release. The offline 3.6 gate:

```bash
bash tests/scripts/rollback_test.sh
```

### The decision layer

`release_contract.rollback` (`.github`-adjacent CLI
`python3 -m release_contract.rollback <command>`) adds the rollback-specific
decisions and reuses the promotion contract for the shared ones:

- `dispatch` — the dispatch input is the target `version` of an existing
  official release; image tags, digests, SHAs, and arbitrary versions are never
  accepted.
- `select` — resolve the target only from the latest 10 **complete** official
  release sets: every backend ECR `release-<version>` tag must resolve to the
  exact manifest digest and the immutable frontend prefix marker must exist and
  match the manifest. Rejects unknown/non-official/outside-window/corrupt
  releases and the release currently running (`TARGET_NOT_FOUND`,
  `TARGET_NOT_OFFICIAL`, `TARGET_ARTIFACT_MISSING`, `TARGET_ARTIFACT_MISMATCH`,
  `TARGET_OUTSIDE_ROLLBACK_WINDOW`, `TARGET_IS_CURRENT`, `TARGET_MANIFEST_INVALID`).
- `schema` — the Decision 8 database-compatibility guard: a schema-changing
  rollback is blocked until the migration review is recorded
  (`SCHEMA_COMPATIBILITY_UNREVIEWED`). The database is never reversed.
- `frontend-restore` — restore-only plan: no `--delete`, `fromPrefix` required,
  live-root marker/index last, CloudFront invalidation required
  (`FRONTEND_DELETE_FORBIDDEN`, `FRONTEND_PREFIX_MISSING`,
  `FRONTEND_INVALIDATION_MISSING`, `FRONTEND_ORDER_INVALID`).
- `result` — the rollback result/audit record (requester, approver, from/to
  releases with exact digests/checksums, run id, workflow URL, timestamps,
  outcome, production-verified, audit annotation). Idempotently resumable
  (`action=write`/`action=resume`); any conflict or missing field fails closed
  (`RESULT_CONFLICT`, `RESULT_NOT_VERIFIED`, `RESULT_SAME_RELEASE`,
  `RESULT_AUDIT_NOT_ANNOTATED`, ...). The immutable original release manifest is
  never edited.
- `snapshot`/`plan`/`waiter`/`verify`/`compensate` — identical contracts to
  forward promotion (shared code).

### The shell wrappers and workflow

- `bin/rollback-preflight.sh` — read-only; fetches the official index from
  GitHub Releases (exact `release-manifest.json` assets), gathers the observed
  ECR `release-*` digests + frontend prefix markers (+ live `release.json`
  marker for the current release), runs `select` + `schema`, and prints the
  current-versus-target summary (identities, digests, task definitions,
  frontend checksum, source SHAs, database-compatibility warning). Emits the
  validated target manifest.
- `bin/deploy-rollback.sh` — registers one digest-pinned task-definition
  revision per backend (copy + `sanitize-task-definition.sh` image-only
  replace, `validate-task-definition.sh` hardening) and updates the services in
  canonical order with circuit breaker and per-deployment waiters bound to this
  run. Never mints or moves ECR tags and never creates an official release.
- `bin/restore-frontend.sh` — restores the live root from the retained
  immutable `_releases/v<version>/` prefix (marker + index.html, no `--delete`)
  and invalidates the SPA entry paths, with read-back.
- `bin/verify-rollback.sh` — read-only post-rollback verification (running
  `containers[].imageDigest`, service task-definition ARNs, frontend marker,
  ALB health) against the deployment manifest; a paused environment fails
  closed (`RUNNING_TASKS_MISSING`), never fabricated success.
- `bin/record-rollback-result.sh` — builds and validates the rollback result /
  audit annotation (JSON on stdout, diagnostics on stderr). `--requester` and
  `--approver` are mandatory (the approver is derived by the workflow from the
  GitHub environment-approval evidence, never the run actor).
- `.github/workflows/rollback-release.yml` — manual dispatch (`version` +
  requester + schema-change inputs); read-only `preflight` job before the
  protected `production` Environment (job-scoped `id-token: write` for its
  read-only ECR/S3 scope); the `rollback` job re-runs the full
  preflight post-approval under the shared non-cancelling `production-mutation`
  concurrency group — and fails closed if the revalidated manifest differs
  byte-for-byte from the approved one — then snapshots, deploys, restores the
  frontend, verifies, and derives `approvedBy` from
  `actions/runs/{run}/approvals` (never `github.actor`); automatic `compensate`
  job restores the pre-rollback snapshot on failure via the shared
  `compensate-production.sh` (which accepts the literal JSON `--changed` array
  the workflow passes; a typo'd component key fails closed). The validated
  target manifest is consumed from the exact producing run via download-artifact
  pinned to `run-id: ${{ github.run_id }}`.

### Deferred live checks

These are **not** claimed by the offline gate and are verified in the
consolidated pass against real AWS/GitHub state: the real owner-approved
rollback (release N → N-1 → N), the real `production` Environment approval,
real ECR/ECS/S3/CloudFront mutations and read-backs, real frontend restoration,
and the real rollback-result artifact. The offline gate exercises every script
against fixtures and a stateful AWS + `gh` stub only.

---

## Traceability queries and operator evidence (subphase 3.7)

Read-only operator queries answered in both directions. The offline 3.7 gate:

```bash
bash tests/scripts/release_traceability_test.sh
```

### The decision layer

`release_contract.traceability` is a pure, fixture-tested module. It consumes
two inputs and returns machine-readable results with deterministic
`{code, field, message}` issues:

- **`index`** — `{"repository": "<owner/repo>", "manifests": [<manifest>, ...]}`:
  the set of release manifests the operator knows about (GitHub Release
  manifests, or a local fixture file). Every manifest is validated against the
  release contract and at most one manifest per (version, status) is allowed.
- **`observed`** — read-only live state gathered by the shell (or a fixture):
  ECR images per repository (`imageDigest`/`imageTags`/`imagePushedAt`), ECS
  services + running tasks (`tasks[].containers[].imageDigest`), current
  task-definition image digests, and frontend version markers (the deployed
  live `release.json` and the immutable per-release
  `_releases/v<version>/release.json` — the prefix-marker keys are derived from
  each manifest's `releasePrefix`/`versionMarker`, never hard-coded). A live
  AWS read that failed is recorded as an `error` marker and fails closed as
  `OBSERVED_READ_ERROR` — never disguised as a missing resource or drift. This
  includes a configured production service that `describe-services` omitted (or
  returned without a `taskDefinition`) and a frontend marker that is not a JSON
  object.

The four lookups and the audit:

| Command | Answers |
|---|---|
| `trace.sh commit --sha <full-sha>` | commit SHA → candidate run (`candidateWorkflow`), per-backend ECR digests, and any official releases recording that SHA. Fails closed when a `sha-<sha>` tag is absent (`ECR_SHA_TAG_MISSING`), resolves to different bytes than a manifest records (`ECR_SHA_DIGEST_MISMATCH`), or manifests recording the SHA disagree on the candidate run (`CANDIDATE_RUN_CONFLICT`) |
| `trace.sh release --version <semver>` | version → source SHA, components, evidence, SBOMs, artifacts, plus a live ECR/frontend cross-check including the immutable per-release `_releases/v<version>/release.json` prefix marker (`FRONTEND_PREFIX_MARKER_*`) |
| `trace.sh running` | production → task-definition ARNs, **running** digests from `containers[].imageDigest`, release identity + approver + deployment run, frontend identity from the deployed marker. Paused production is reported honestly (TD digests + last verified deployment evidence, never a fabricated running digest). A mixed or incomplete running digest set fails closed (`RUNNING_MIXED_DIGESTS`/`RUNNING_DIGEST_INCOMPLETE`) |
| `trace.sh digest --digest sha256:<hex>` | image digest → ECR tags, OCI revision, candidate run, release identity. The OCI revision is **cross-referenced from the release manifest** (`ociRevisionSource: "release-manifest"`) and never claimed as an observed label read (`ociRevisionObservedFromImage: false`) — `describe-images` cannot read the image config blob |
| `trace.sh audit [--version <semver>]` | manifest ↔ ECR `sha-*`/`release-*` tags ↔ ECS running digest ↔ frontend checksum; reports drift without modifying anything. Releases are audited newest-first by numeric version, independent of index order |

Every lookup prints JSON on stdout and exits `0` only when found AND
consistent. `NOT_FOUND`, `AMBIGUOUS_VERSION`/`AMBIGUOUS_DIGEST`/
`RUNNING_AMBIGUOUS`, `ECR_*`/`ECS_*`/`FRONTEND_*` mismatches, and
`OBSERVED_READ_ERROR` exit `1`; usage/IO errors exit `2`. `--human` adds a
concise view on stderr. The audit's ECS leg only applies to the release the
running environment actually matches — the running environment can match one
release at a time, so an older release is never flagged for not being the
running one.

### Running the lookups

```bash
# Offline/fixture mode: point at a manifest index and a pre-built observed state.
bash plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/trace.sh running \
  --index plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/fixtures/traceability/index.json \
  --observed plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/fixtures/traceability/observed-ok.json

# Live read-only mode (the deferred smoke test): gathers the index from the
# GitHub Releases of $GITHUB_REPOSITORY (via `gh`) and observed state from AWS.
# Mandatory identity preflight + --profile dpm-profile --region eu-north-1.
export GITHUB_REPOSITORY=Djimi/OnlineShop-full-stack
bash plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/bin/trace.sh audit --human
```

Every live AWS command runs with the mandatory `--profile dpm-profile
--region eu-north-1` (non-overridable) and an `sts get-caller-identity`
preflight; the tool is strictly read-only (ECR `describe-images`, ECS
`list-tasks`/`describe-tasks`/`describe-services`/`describe-task-definition`,
S3 `get-object`, `gh api` releases). When the index is fetched from GitHub, the
release manifest asset is selected **by exact name** `release-manifest.json`
(the canonical 3.4 publication name) — an asset whose name merely contains
"manifest" (e.g. a checksums file) is never consumed. `TRACE_KEEP_TMP=1` keeps
the scratch directory for operator debugging.

### Deferred live checks

The read-only live smoke test (all four lookups + the audit against real
production AWS state and real GitHub Releases) is **not** claimed by the
offline gate and is executed in the consolidated verification pass. The gate
proves the live gather path (identity preflight, exact read-only calls, no
mutations) with a stateful AWS stub plus a `gh` stub.

---


---

## Prerequisites and running the tests

The Python validator requires Python 3.10+ (the type-alias syntax in
`crossrules.py` is evaluated at import time) and the pinned `jsonschema`
package; the verification gate additionally uses `jq` (preinstalled on GitHub
`ubuntu-latest` runners):

```bash
python3 -m pip install -r requirements.txt   # jsonschema==4.26.0
```

Run the complete verification gate (Python tests, CLI fixture checks,
determinism, checksum guard, shell input helpers, and optional lint):

```bash
bash tests/scripts/release_contract_test.sh
```

Optional linters (run automatically when present):
- `shellcheck` — installable as `pip install shellcheck-py` (bundles the
  `shellcheck` binary) or from https://www.shellcheck.net/
- `ruff` — `pip install ruff` (Python format/lint)

If a linter is absent the gate reports it explicitly instead of silently
passing; install it and re-run to satisfy the full gate.
