# CI/CD Gotchas — Quick Reference

> Read before working on any CI/CD or AWS infra task. Condensed from actual debugging runs (see [plans/AUTOMATIC-BUILDS-AND-DEPLOY/AWS_COMMANDS_GUIDE.md](../plans/AUTOMATIC-BUILDS-AND-DEPLOY/AWS_COMMANDS_GUIDE.md) for the full narrative).

---

## Pre-flight Checks

1. **Identify yourself:** `aws sts get-caller-identity` — always first in any terminal.
2. **Confirm region:** `aws configure set region eu-north-1` or pass `--region eu-north-1` to every command.
3. **Check existing state:** `aws ecr describe-repositories --region eu-north-1`, `aws iam list-roles --query "..."` before creating anything.

---

## Pass 3R.1 — CI security and promotion handoff repair (offline)

Pass 3R.1 hardens the existing release-v1 workflows and wrappers. It does not
change the manifest schema or staging design, and its gates are offline only.
The three in-scope workflows set workflow-level `permissions: contents: read`;
job-level permissions add only the required `pull-requests`, `actions`,
`deployments`, or OIDC permissions. The existing backend jobs still combine PR
validation with branch-push publication, so they remain OIDC-capable at job
scope; PR credential/publication steps are guarded off and the role trust does
not admit a `pull_request` subject. Pass 3R.2/3R.3 performs the structural job
split, and Pass 3R.9 applies the purpose-specific role cutover.

Never embed an untrusted GitHub expression in a `run:` script. Transfer event,
ref, SHA, dispatch inputs, run/attempt IDs, repository names, and actor values
through that step's `env`, validate them for the event-specific shape, and pass
only quoted shell variables/argv. `rl_assert_ci_ref` accepts only `main` or a
well-formed `feature/**` ref; `rl_assert_ci_pr_ref` accepts only
`refs/pull/<positive-int>/merge`. The security gate proves hostile quotes,
spaces, command substitutions, backticks, separators, redirection, and
newlines cannot create a marker command/file.

Promotion is an explicit handoff:

1. The exact candidate run/attempt and optional `source_sha` are validated;
   `source_sha`, when supplied, must equal the downloaded evidence exactly.
2. GitHub workflow-run reads use the attempt-scoped REST shape
   `actions/runs/{run}/attempts/{attempt}` and
   `actions/runs/{run}/attempts/{attempt}/jobs`; the API returns bare
   `head_branch: "main"`, which is normalized to `refs/heads/main`. The
   response `id` and `run_attempt` must be positive JSON numbers matching the
   requested values. Never consume the unscoped/latest attempt.
3. `deploy-production.sh` accepts a schema-valid candidate manifest plus a
   read-only production snapshot. The candidate cannot contain task-definition
   ARNs; the snapshot supplies and validates the current service ARNs. The
   script emits a deployment manifest with the newly registered ARNs, and only
   that output is rendered as official after production verification.
4. The snapshot fails closed unless the live marker has canonical version,
   source SHA, and frontend SHA-256 identity; the immutable
   `_releases/v<version>/` marker and `index.html` match it; the live index has
   an S3 full-object `ChecksumSHA256`; and the exact canonical `v<version>`
   GitHub tag resolves to the same source SHA (including annotated-tag
   peeling). It never chooses a newer tag merely because it sorts higher.
5. `publish-frontend.sh`, `restore-frontend.sh`, and compensation pass
   `--checksum-algorithm SHA256` on every S3 writer. Snapshot decodes the
   service-reported full-object checksum from base64 to canonical SHA-256 hex
   and never falls back to an ETag.

Run the 3R.1 offline gates:

```bash
bash tests/scripts/ci_security_contract_test.sh
bash tests/scripts/promotion_handoff_test.sh
bash tests/scripts/promotion_test.sh
bash tests/scripts/rollback_test.sh
```

These stateful stubs/static checks do not claim live AWS, staging, GitHub
approval, role-split, or release-publication verification.

---

## Release contract (Pass 3, subphase 3.1)

The versioned release-manifest JSON Schema, deterministic local validator,
valid/invalid fixtures, strict dispatch-input helpers, and tests live in
`plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/` (see its `README.md`).

- **Validate any candidate/official manifest** (never parse security-sensitive
  JSON with regex or ad-hoc shell concatenation):
  ```bash
  RELEASE=plans/AUTOMATIC-BUILDS-AND-DEPLOY/release
  bash "$RELEASE/bin/validate-manifest.sh" manifest.json --human
  ```
  Exit `0` = valid, `1` = invalid, `2` = usage/IO error. Issues are emitted as
  deterministic `{code, field, message}` JSON; `--check-checksum <sha256>`
  guards the canonical manifest checksum.
- **Gate:** `bash tests/scripts/release_contract_test.sh` runs the Python
  suite, CLI fixture checks, determinism/checksum checks, and (when
  present) `ruff` + `shellcheck`. Pin the validator dependency with
  `pip install -r "$RELEASE/requirements.txt"` (`jsonschema==4.26.0`,
  `PyYAML==6.0.3`).
- **ShellCheck fallback:** `shellcheck` is not always preinstalled; install the
  bundled binary with `pip install shellcheck-py`, or download from
  https://www.shellcheck.net/. If unavailable, the gate reports it explicitly
  instead of silently passing.
- **Dispatch inputs:** validate them with
  `source "$RELEASE/bin/release-input.sh"` (`rl_assert_semver`,
  `rl_assert_full_sha`, `rl_assert_sha256_hex`, `rl_assert_positive_integer`,
  `rl_assert_github_login`, `rl_assert_http_url`, `rl_assert_regular_file`),
  then pass them to downstream commands only as environment variables or
  argument-array entries — never interpolated into shell/JSON/GitHub-CLI/AWS
  CLI command strings.

---

## Candidate build evidence (Pass 3, subphase 3.2)

The successful `main` push workflow emits one candidate evidence bundle after
Auth, Items, API Gateway, frontend, and the cloud staging E2E job pass. The
reusable scripts/tests live in `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/`
and the offline gate is:

```bash
bash tests/scripts/candidate_evidence_test.sh
```

| Gotcha | Why It Happens | Rule |
|--------|---------------|------|
| Staging teardown could race the next main push | Workflow-level `concurrency` with `cancel-in-progress: true` cancelled the older run mid-staging | The `e2e-staging` job owns resume→deploy→E2E→teardown in ONE job with job-level `concurrency: {group: ${{ github.workflow }}-staging-${{ github.ref }}, cancel-in-progress: false}`; teardown is `if: always()` in the same job |
| SHA-tag reruns rebuilt different bytes | Rebuilding on a rerun overwrites the canonical image and loses the original digest | `publish-candidate-image.sh` decides push / reuse / fail-closed from the existing image's OCI producer labels + a GitHub-API-verified successful producer run; never push rebuilt bytes over a trusted canonical image |
| A pre-existing SHA tag from a feature/manual run looked canonical | Labels prove identity | Images carry `org.onlineshop.producer.event`/`.ref`/`.run-id`; reuse requires `event=push`, `ref=refs/heads/main`, revision == current SHA, and a `success` producer conclusion — anything else fails closed |
| A manual dispatch on `main` at an already-built SHA pushed different bytes | Non-push events used to always push, overwriting the canonical image | Feature branches still always push, but a `workflow_dispatch` on `main` only pushes when the `sha-<full-sha>` tag is absent; an existing tag is reused when trusted or fails closed (never overwritten) |
| `image-labels.sh` could not find the OCI labels in a real ECR image | `docker manifest inspect --verbose` returns only the manifest; the labels live in the image *config* blob it merely references | Read the config with the lightweight `docker buildx imagetools inspect --format '{{json .Image}}'` (no layer pull); `docker/setup-buildx-action` runs before every call. A failed label read fails closed — it is never treated as "tag absent" (exit 3 is reserved for genuinely missing tags) |
| Frontend archive was not reproducible | tar mtime/owner/gzip header vary per build | `package-frontend.sh` normalizes metadata (`--sort=name`, uid/gid 0, `--mtime=@0`, `gzip -n`); the gate builds twice and asserts identical archive SHA-256 |
| Extraction could be a security hole | Malicious tar entries (`../`, symlinks, devices) | `unpack-frontend.sh` validates via the Python `tarfile`-based checker and rejects traversal/links/device entries *before* extracting |
| Artifact ID/digest were thought to be unknowable from `upload-artifact` | `@v3` returned no outputs; `@v4` does | The pinned `actions/upload-artifact@v4` returns `artifact-id`, `artifact-url`, and `artifact-digest` (GitHub service-reported SHA-256 of the uploaded archive) as step outputs. Give the upload step an `id`, then `record-artifact.sh` records `{runId, runAttempt, artifactId, artifactUrl, artifactDigest, name}` — no post-upload artifacts-API query needed (and the API listing has no digest field) |
| The bundle's own artifact ID/digest could not be recorded inside the bundle | Recording an artifact's identity inside the artifact it describes is a circular self-checksum | The bundle records immutable facts; the identity record lives in a separate pointer artifact `candidate-artifact-id-<sha>-<attempt>`; promotion consumes the bundle by exact run id/attempt/artifact id/name and rejects duplicates/expired |
| Candidate evidence could be emitted for a failed staging run | A dependent job with its own `if:` can run even when a needed job failed | The `candidate-evidence` job requires every `needs.*.result == 'success'` AND `emit-candidate-evidence.sh` refuses to emit unless all five conclusions are `success` |
| A rerun's evidence misattributed the produced bytes | The rerun reuses, it does not produce | Evidence records `candidateWorkflow` = artifact-producing run (from the images' producer labels) and `artifactWorkflow` = the current staging-validation run; `emit-candidate-evidence.sh` takes `--producer-run-id/--producer-run-attempt` |
| Candidate manifest needed a version at build time | The owner assigns SemVer at promotion (Decision 3) | The bundle records immutable facts (`candidate-evidence.json`); `emit-candidate-manifest.sh` renders a schema-valid candidate manifest when the version is supplied |
| Release-critical Actions drifting by mutable tag | `@v4`-style tags move | All release-critical third-party Actions in `build-and-deploy.yml` are pinned by full commit SHA with a version comment; the gate enforces it |
| Item images missing the `common` revision | Items embeds `common` at the monorepo SHA | Items images additionally carry `org.onlineshop.common-revision=<sha>`; the canonical-set check requires it |

Live ECR label read-back, real digests, real artifact IDs and service-reported
artifact digests, and real Syft scans are deferred to the consolidated
verification pass — the offline gate does not claim them.

---

## ECR release tagging, immutability, and least privilege (Pass 3, subphase 3.3)

The offline gate is:

```bash
bash tests/scripts/ecr_release_tagging_test.sh
```

| Gotcha | Why It Happens | Rule |
|--------|---------------|------|
| ECR tag mutability is repository-scoped, so `sha-*` and `release-*` cannot be immutable while `main-latest`/`branch-*` advance | Legacy ECR had only `MUTABLE`/`IMMUTABLE` per repository | Use the newer **`IMMUTABLE_WITH_EXCLUSION`** setting with `imageTagMutabilityExclusionFilters` = exactly `main-latest` and `branch-*`; read back via `describe-repositories` |
| A release tag must be created without pulling/rebuilding the image | Docker pull + retag changes nothing but costs time and can re-upload different bytes | `promote-image-digest.sh` uses `ecr:batch-get-image` (returns the manifest + service-reported digest) then `ecr:put-image` with that exact manifest — pure server-side re-tagging |
| A rerun of promotion could re-mint or overwrite an immutable release tag | `put-image` on an existing immutable tag fails, but a *different* existing digest must never be assumed resumable | `release_contract.ecr` decides mint/reuse/fail-closed: reuse only when the existing `release-*` tag resolves to the **recorded** digest; any other digest fails closed |
| Colliding release identity (GitHub tag / ECR release tag / frontend prefix) could be silently overwritten | Each store is checked independently, if at all | `check-release-identity.sh` + `release_contract.releaseid` require `proceed` (nothing exists) or `resume` (every existing object exactly matches the manifest) before any mutation; anything else fails closed |
| `latest` could be pushed and then never move on an immutable repository | A floating `latest` on an immutable repo freezes at the first push | Decision 4: `latest` is absent for v1; the offline gate asserts the build workflow never computes a `latest` tag |
| A single broad GitHub role lets any job touch staging/production | One role = one policy = blast radius | Per-purpose roles/policies: `github-actions-candidate-build`, `-promotion`, `-production`, `-rollback` (see `github-actions-role-layout.md`); validation jobs get `permissions: {contents: read}` and no `id-token: write` |
| `ecr:GetAuthorizationToken` is unscopable but every other ECR action is scoped to repo ARNs | IAM has no image-tag-prefix condition for `ecr:PutImage` | Scope ECR actions to the three repository ARNs; keep `ecr:GetAuthorizationToken` on `Resource: "*"`; document that tag-prefix control is enforced by the scripts/workflow, not IAM |
| `iam:PassRole` with `*` would let a compromised deploy role assume anything | PassRole is not scoped by default | `iam:PassRole` to `ecsTaskExecutionRole` only, with `StringEquals iam:PassedToService=ecs-tasks.amazonaws.com` |
| OIDC trust could omit the protected environment subject and still let `main` assume the prod role | The build role and the production role are different subjects | Trust policy requires `:ref:refs/heads/main`, `:ref:refs/heads/feature/*`, **and** `:environment:production`; decode the real JWT `sub` (never guess) before relying on it live |
| IAM policy documents could drift from least privilege without an AWS call | There is no live Access Analyzer run in the offline gate | `release_contract.iam` structurally validates every source-controlled policy (ECR scoped, GetAuthorizationToken only on `*`, PassRole scoped + conditioned, no mutating action on `*`, promotion has no layer upload, rollback has no `ecr:PutImage`); `aws iam validate-policy` runs before live application |
| `latest`-style mutable tags could be minted by the promotion script | The script is the release authority | `promote-image-digest.sh` requires the release tag to be `release-<version>` (validated SemVer) and the candidate tag `sha-<full-sha>`; anything else is a usage error |

Live repository-settings read-back, real put-image behavior, real convenience
tag advancement, the real OIDC environment `sub`, and the IAM Access Analyzer
run are deferred to the consolidated verification pass — the offline gate does
not claim them.

---

## Controlled staging-to-production promotion (Pass 3, subphase 3.4)

The offline gate is:

```bash
bash tests/scripts/promotion_test.sh
```

| Gotcha | Why It Happens | Rule |
|--------|---------------|------|
| A hand-typed image tag/digest could be promoted | The operator might paste the "latest" convenience tag or a digest they typed | Dispatch takes only `version` (SemVer) + a successful candidate `run_id` (optionally a full SHA); `release_contract.promotion dispatch` rejects a non-SemVer version and a non-numeric run id |
| An approval could sit in a queue while main advances | A pre-approval read could be stale by promote time | The read-only `preflight` job validates dispatch inputs and the candidate manifest contract before the protected `production` Environment; the approved `promote` job runs the **full** preflight (run evidence, ancestry, release-identity, DB review) with a fresh snapshot after approval + lock acquisition; only that second run authorizes mutation |
| The "staging gate" cost money by rebuilding and redeploying the candidate | Rebuilding the same SHA to repeat the gate is pure waste | The successful Pass 2 `e2e-staging` job of the exact candidate run is the staging gate; the workflow consumes the evidence artifact of that exact run attempt and never invokes a build/push action (a static check proves no `build-push-action`/`publish-candidate-image.sh`) |
| Two promotions could run concurrently and clobber each other | GitHub queues workflows independently | All production mutation shares the `production-mutation` concurrency group with `cancel-in-progress: false`; superseding is an explicit operator action, never automatic |
| The promoted SHA was not actually a descendant of the last release | Branch pushes can skip/rewrite main | `release_contract.promotion ancestry` requires the candidate SHA to be a descendant of the last official release and reachable from current `main` (`CANDIDATE_BEHIND_OFFICIAL`/`CANDIDATE_NOT_ON_MAIN`/`VERSION_NOT_INCREASING`) |
| An unreviewed DB/schema change could slip into production | A candidate can include migration SQL with no owner review | `promotion-preflight.sh` requires `--db-change` + `--migration-reviewed`; an unreviewed change fails closed with `SCHEMA_CHANGE_UNREVIEWED` (Decision 8 / Flyway gate) |
| A "successful" service was really a stale or circuit-breaker-rolled-back deployment | A generically stable service is not proof the intended deployment landed | `deploy-production.sh` binds its waiter to the deployment id/task-definition this run started and requires `COMPLETED` + exact running digests; `release_contract.promotion waiter` fails closed on `DEPLOYMENT_ID_MISMATCH`/`WAITER_TD_MISMATCH`/`DEPLOYMENT_NOT_COMPLETED`/`WAITER_DIGEST_MISMATCH` |
| A promote failure left a mixed state and a missing snapshot | No record of what existed before meant compensation was guesswork | `snapshot-production.sh` (read-only) records desired counts, capacity strategy, service/TD ARNs, running digests, ALB wiring, and the frontend marker/checksum; it is uploaded as an artifact so the `compensate` job restores exactly those bytes in reverse order, including the frontend live root from the previous immutable prefix |
| The official GitHub release was published before production was verified | The tag mint and release happen in one "finalize" step | `finalize-release.sh` refuses publication unless `PROMOTION_PRODUCTION_VERIFIED=true` after `verify-production.sh`; `release_contract.promotion finalize` fails closed on `PUBLICATION_BEFORE_VERIFICATION` and reconciles partial objects only for exact digest matches (`RELEASE_TAG_CONFLICT`) |
| A rerun could mint a different version for already-deployed bits | Resuming after a partial failure must not invent new identity | Finalization is idempotently resumable only for exact partial objects (`action=resume`); any mismatch fails closed — never call an unrecorded deployment official |
| `approvedBy` was taken from `github.actor` or user input | The dispatcher is not the approver | `approvedBy` is derived from the GitHub environment-approval evidence for the promotion run via `actions/runs/{run}/approvals` (state `approved` on the `production` environment); the workflow never uses `github.actor` for `approvedBy` and fails closed if the approval record is unresolvable |

The live owner-approved promotion — the real `production` Environment approval
and required-reviewer check, real ECR/ECS/S3/CloudFront mutations and read-backs,
and the real GitHub Release publication — is deferred to the consolidated
verification pass; the offline gate does not claim it.

---

## Production hardening (Pass 3, subphase 3.5)

The offline gate is:

```bash
bash tests/scripts/production_hardening_test.sh
```

| Gotcha | Why It Happens | Rule |
|--------|---------------|------|
| A production task definition could carry a floating image tag or wrong CPU/memory | Nothing validated the release task definition before registration | `release/bin/validate-task-definition.sh` enforces digest-pinned `@sha256:` images, the Fargate CPU/memory matrix, `awsvpc`, named Service Connect port mappings, `awslogs`, health checks, positive `stopTimeout`, `versionConsistency=enabled`, and full-ARN `secrets[].valueFrom` |
| A digest-pin transform could silently change more than the image (or leak a secret) | Registering a copied task definition by hand risks unrelated drift | `release/bin/sanitize-task-definition.sh` proves only the `image` field changed, no container was added/removed, every secret stays in `secrets[].valueFrom` with a full ARN, and no secret reference is repeated in `environment`/`command` |
| A `secrets[].valueFrom` name (without `arn:aws:secretsmanager:`) is silently treated as an SSM parameter | ECS resolves a bare name as an SSM path, not a Secrets Manager secret | Every secret reference must be a FULL `arn:aws:secretsmanager:...` ARN; the `:json-key::` selector form requires the full ARN |
| Deployment circuit breaker / safe rolling were not configured on services | `enable`/`rollback` defaults are off | `release_contract.ecs_config validate-service` requires `deploymentCircuitBreaker.enable=true` + `rollback=true`, `minimumHealthyPercent=100`, `maximumPercent=200`, and a capacity-provider strategy |
| Desired-count-one Fargate Spot production looks like HA | Spot capacity can be reclaimed with 2-minute warning; desired 1 = zero capacity during reclaim | Explicitly document that Spot + desired 1 is **not** a high-availability SLA (`explanations/PRODUCTION-HARDENING-DECISIONS.md`); the safe-rolling/circuit-breaker settings protect deployments, not reclaim |
| Production and staging could silently share a VPC/namespace after drift | Configs diverge from reality over time | `scripts/verify-production-staging-separation.sh` compares the two non-secret configs AND live observed identifier + topology state (SG/subnet/DB-subnet-group VPCs and per-service Cloud Map namespaces) and fails closed |
| The frontend bucket stayed publicly readable through a website endpoint | v1 used S3 website hosting + public-read policy | `scripts/migrate-frontend-oac.sh` (S3 REST origin + CloudFront OAC + full public access block + removed website config) with per-step read-back. The apply run starts with a **no-lockout precondition gate** (the current bucket policy must already grant public read or the CloudFront OAC) so the origin switch cannot create an outage window, and waits (bounded) for the asynchronous CloudFront deployment to reach `Deployed` before tightening the bucket policy. `scripts/verify-frontend-oac.sh` fails closed on drift. Not applied live in 3.5 |
| An AWS read error looked like a missing resource | The inventory helpers collapsed every failed read to `missing` | A genuinely absent resource is reported as `missing`; an API read failure (auth/throttle/network) is reported as `error`, and `release_contract.environments` emits `OBSERVED_READ_ERROR`/`TOPO_READ_ERROR` so the check fails closed with an honest message instead of fake drift |
| CloudFront commands were thought to need a different region | CloudFront is a global service | The global endpoint (`cloudfront.amazonaws.com`, signing `us-east-1`) is reached regardless of `--region`, so the mandatory `--profile dpm-profile --region eu-north-1` flags still work for CloudFront commands |
| A single-region CloudTrail trail misses global IAM/CloudFront events | IAM/CloudFront management events are delivered from us-east-1 | `scripts/verify-cloudtrail-coverage.sh` requires a multi-region trail logging management events that delivers with a confirmed `LatestDeliveryTime` and no delivery error. Management selectors cover *all* control-plane APIs — they are not a per-service enumeration |
| The production DB could be publicly reachable without the inventory noticing | The inventory only checked RDS existence | `identifiers_observed` reads `PubliclyAccessible` and `release_contract.environments` rejects a public production database (`DB_PUBLIC_ACCESSIBLE`) |
| The execution role / ECR repos were named but never verified live | They were config-only identifiers | The inventory verifies execution-role existence (`iam get-role`) and each ECR repository (`ecr describe-repositories`); they are intentionally excluded from prod/staging separation because they are shared infrastructure |
| A staging-only DB helper could be invoked from a production path | `lc_require_environment` returned 1 but a conditional caller could continue past it | The staging-only helpers (`lc_create_clean_staging_db`, `lc_delete_staging_db`, `lc_staging_db_status`, `lc_staging_master_secret_arn`) now use `lc_require_environment staging || return 1` so they fail fast and can never reach a mutation; the gate proves (via a call-recording stub) that they issue NO AWS call after the guard fails, even in `if helper; then ...` conditional contexts, and statically proves the production entry points never invoke them |
| The AWS profile/region could be redirected by config drift | `--profile`/`--region` came from variables | `lc_init` refuses to run unless `LC_PROFILE=dpm-profile` and `LC_REGION=eu-north-1`, `identifiers.sh` refuses to build an `aws` call otherwise, and the gate asserts both config files carry exactly those values |
| Production RDS backup/restore was unverified and schema changes were applied by hand | No versioned migration tool exists | No schema-changing production release may be promoted until Flyway (or equivalent), forward/backward-compatible rules, and a tested backup/restore procedure exist (`explanations/PRODUCTION-HARDENING-DECISIONS.md`) |

## Release traceability queries (Pass 3, subphase 3.7)

The offline gate is:

```bash
bash tests/scripts/release_traceability_test.sh
```

| Gotcha | Why It Happens | Rule |
|--------|---------------|------|
| A "running" lookup reported the task-definition tag/URI instead of the actual container bytes | `describe-services`/`describe-task-definition` return the image *reference*, not the running image | `trace.sh running` reads `aws ecs describe-tasks` and reports `tasks[].containers[].imageDigest` — the running container's actual digest; it never reports only the task-definition URI |
| Frontend identity was guessed from cache headers or the URL | CloudFront TTLs make headers meaningless for identity | The frontend identity is resolved from the deployed immutable `release.json` version marker (`version`/`sourceSha`/`frontendSha256`), never cache headers |
| Paused production "looked" like a crash or was fabricated as running | With desired count 0 there are no running tasks to describe | `trace.sh running` reports `paused: true`, resolves each service's current task-definition image digest, and reports the latest official release as last verified deployment evidence — it never fabricates a running digest |
| An AWS read error looked like a missing tag/marker | A failed read was collapsed to "absent" | The shell records a failed read as an `error` marker in the observed state and `release_contract.traceability` fails closed with `OBSERVED_READ_ERROR`; a genuine not-found is `exists: false`/tag absent |
| Auditing all releases flagged older releases for "not the running digest" | The running environment can match only one release at a time | The audit's ECS leg applies only to the release the running digests actually match (`RUNNING_DIGEST_UNMATCHED` is a top-level drift issue when nothing matches); older releases are `n/a`, not failures |
| A deployed live marker "mismatched" every older release | Only the deployed release matches the live marker | The live `release.json` leg applies only to the release whose version the marker names; every release is checked against its own immutable `_releases/v<version>/release.json` prefix marker |
| An ambiguous digest/release was silently resolved | Two releases can share no source SHA by the monotonic-promotion rule | `AMBIGUOUS_DIGEST`/`AMBIGUOUS_VERSION`/`RUNNING_AMBIGUOUS` fail closed instead of picking one |
| The "latest" release depended on manifest index order | `compare_semver` returns only -1/0/1, so `max()`/`sorted()` on it pick the *first* equal-sign entry | Newest-official selection and the audit's newest-first ordering use numeric `(major, minor, patch)` version keys; index order can never change the result |
| A mixed or partial running digest set fabricated an identity | A last-writer-wins dict collapsed two in-flight task digests, and a single-component set could "match" a release | `RUNNING_MIXED_DIGESTS` (two different digests for one component) and `RUNNING_DIGEST_INCOMPLETE` (not all three backends present) fail closed; a release is matched only when the full three-backend set agrees |
| `trace.sh release` ignored the immutable per-release prefix marker | The live root `release.json` only describes the currently deployed release | `by-version` also verifies `_releases/v<version>/release.json` (`FRONTEND_PREFIX_MARKER_MISSING`/`_MISMATCH`); the prefix-marker S3 keys are derived from each manifest's `releasePrefix`/`versionMarker`, never hard-coded |
| A `sha-<sha>` tag at different bytes than a manifest records looked fine | `by-sha` checked only presence | `ECR_SHA_DIGEST_MISMATCH` fails the lookup; manifests recording one SHA but different candidate runs fail with `CANDIDATE_RUN_CONFLICT` |
| The digest lookup claimed an OCI revision it never read | `describe-images` returns tags/digests but not the image config blob, so labels are not observable there | `trace.sh digest` reports `ociRevisionSource: "release-manifest"` and `ociRevisionObservedFromImage: false` — the revision is the 3.2 build contract (`revision == sourceSha`) cross-referenced from the release manifest, never a live label read; a real label read-back is deferred to the consolidated pass |
| A configured service silently vanished from `describe-services` | The API returns `failures` and omits the service; `--query` on the survivors drops it | The shell records `services.<name>.error` and the lookup fails closed with `OBSERVED_READ_ERROR` — a partial API response is never shown as clean state |
| A malformed frontend marker crashed or was treated as valid | The marker S3 object is not guaranteed to be a JSON object | The shell records a non-object marker as a read `error`; the Python layer also fails closed (`FRONTEND_MARKER_MISMATCH`) instead of crashing on `.get()` |
| A GitHub release's manifest asset was chosen by fuzzy name matching | `select(.name | test("manifest"))` could pick a checksums/other asset | The index fetch selects the exact `release-manifest.json` asset (the canonical 3.4 publication name); the gate emits a decoy "manifest.checksums" asset first and proves it is never consumed |
| The mandatory profile/region could be overridden on a lookup | Operators might pass `--region` | `trace.sh` refuses any profile/region other than `dpm-profile`/`eu-north-1` and runs `aws sts get-caller-identity` (must be account `799111666795`) before any live read |
| Lookups appeared to be read-only but weren't | A lookup script could accidentally mutate | The gate's stateful AWS stub proves `trace.sh` issues only `describe`/`list`/`get-object` calls and no mutating call |

Live lookups against real AWS/GitHub (the read-only smoke test) are deferred to
the consolidated verification pass — the offline gate does not claim them.

## AWS Context

| Property | Value |
|----------|-------|
| Account ID | `799111666795` |
| Region | `eu-north-1` |
| OIDC Provider | `arn:aws:iam::799111666795:oidc-provider/token.actions.githubusercontent.com` |
| IAM Role | `arn:aws:iam::799111666795:role/github-actions-onlineshop` |
| ECR Registry | `799111666795.dkr.ecr.eu-north-1.amazonaws.com` |
| ECR Naming | `onlineshop-<service>` (NO SLASHES — e.g. `onlineshop-auth`, not `onlineshop-auth/api-gateway`) |

---

## GitHub Actions Workflows

| Gotcha | Why It Happens | Rule |
|--------|---------------|------|
| `workflow_dispatch` invisible on feature branch | GitHub only indexes from the default branch (`main`) | During development, temporarily add `push` trigger. Remove before merging. |
| `github.event.inputs` is `null` on push events | `.inputs` only exists for `workflow_dispatch` events | Guard with `github.event_name == 'workflow_dispatch'` before accessing `.inputs`. Use `github.event_name == 'push'` as a catch-all during development. |
| `Cache export is not supported for the docker driver` | `cache-from: type=gha` requires BuildKit, but the runner's default Docker driver doesn't support it | Always add `docker/setup-buildx-action@v3` before any `docker/build-push-action` that uses `cache-from`/`cache-to`. |
| Java version mismatch: `release version X not supported` | `java-version` in `setup-java` doesn't match `<java.version>` in `pom.xml` or the `FROM` image in `Dockerfile` | Cross-check all three sources of truth before setting the version in the workflow. |
| `Could not find or load main class ...MavenWrapperMain` | `maven-wrapper.jar` was tracked in git and got corrupted by CRLF normalisation | `maven-wrapper.jar` is in `.gitignore` and auto-downloaded. Never track it. |
| Jobs all "skipped" on push | Job `if:` condition only checked `github.event.inputs.service` which is `null` on push | Always include `github.event_name == 'push'` as an OR condition in job guards during development. |

### GitHub OIDC trust

`Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity` means the AWS role's trust policy rejected the token. This account uses the configured subject `repo:Djimi@8793507/OnlineShop-full-stack@1097550215`, scoped to the refs that publish images. Pass 3.3 adds the protected production environment subject to the **source-controlled** trust policy (`plans/AUTOMATIC-BUILDS-AND-DEPLOY/github-actions-oidc-trust-policy.json`), which is applied live with the command below during the consolidated verification pass:

```json
"StringLike": {
  "token.actions.githubusercontent.com:sub": [
    "repo:Djimi@8793507/OnlineShop-full-stack@1097550215:ref:refs/heads/main",
    "repo:Djimi@8793507/OnlineShop-full-stack@1097550215:ref:refs/heads/feature/*",
    "repo:Djimi@8793507/OnlineShop-full-stack@1097550215:environment:production"
  ]
}
```

After re-authenticating AWS, apply `plans/AUTOMATIC-BUILDS-AND-DEPLOY/github-actions-oidc-trust-policy.json`:

```bash
aws iam update-assume-role-policy \
  --role-name github-actions-onlineshop \
  --policy-document file://plans/AUTOMATIC-BUILDS-AND-DEPLOY/github-actions-oidc-trust-policy.json \
  --profile dpm-profile \
  --region eu-north-1
aws iam get-role --role-name github-actions-onlineshop --profile dpm-profile --region eu-north-1
```

Pull-request jobs deliberately do not request AWS credentials or push images.

---

## AWS CLI on Windows

PowerShell's `@'...'@` here-strings write UTF-8 with a Byte Order Mark (BOM). AWS IAM (and many other AWS services) reject JSON with a BOM because they expect pure ASCII.

**Wrong:**
```powershell
$json = @'
{"Version":"2012-10-17",...}
'@
$json | Out-File -FilePath trust-policy.json -Encoding utf8
```

**Right:**
```powershell
$json = '{"Version":"2012-10-17",...}'
[System.IO.File]::WriteAllText("trust-policy.json", $json, [System.Text.Encoding]::ASCII)
```

---

## Git Binary Safety

- **Auto-downloadable binaries belong in `.gitignore`, never in git tracking** (e.g., `maven-wrapper.jar`, `node_modules`). They can be corrupted by git's line-ending conversion if accidentally tracked.

---

## AWS ECR

- ECR repositories are **region-scoped** — repos in `eu-north-1` are invisible in `eu-central-1`
- `delete-repository --force` is destructive and irreversible. Always run `describe-images` first to confirm the repo is empty (or you're okay losing the images).
- Use `aws ecr describe-images --repository-name <name> --region eu-north-1 --query "imageDetails[*].imageTags[0]"` to verify pushed images

---

## Verification Pattern

Every mutating AWS command should be immediately verified:

| Mutation | Verification |
|----------|-------------|
| `create-role` | `get-role --role-name <name>` |
| `put-role-policy` | `list-role-policies --role-name <name>` |
| `create-repository` | `describe-repositories --repository-name <name> --region <region>` |
| `delete-repository` | `describe-repositories --region <region>` (confirm it's gone) |
| Apply SQL via `ecs-run-sql.sh` | `--verify` flag in the SAME run (e.g. `--verify "\dt"`) — never trust exit 0 alone |

AWS CLI returns empty output on success for many commands — silence does NOT mean it worked. Verify explicitly.

---

## AWS ECS (Fargate)

Learned during staging provisioning (2026-08-02). Full narrative: [AWS_COMMANDS_GUIDE.md Part D](../plans/AUTOMATIC-BUILDS-AND-DEPLOY/AWS_COMMANDS_GUIDE.md).

| Gotcha | Why It Happens | Rule |
|--------|---------------|------|
| `SC service is already used by ...namespace...` | Service Connect maps `portName` → Cloud Map service name, which must be **unique per namespace**. Production uses `auth-port`, `items-port`, `gateway-port`; staging has its own namespace and uses `auth-staging-port`, etc. | Within a single namespace port names must be unique in BOTH the container `portMappings[].name` and the SC config. Production and staging now use separate namespaces, so cross-environment collisions are moot — the isolation is enforced by `scripts/verify-production-staging-separation.sh` |
| `portName(X) does not refer to any named PortMapping` | SC `portName` must exactly match a `portMappings[].name` in the task definition | Rename the portMapping name in the TD, not just the SC config |
| `Specifying both a launch type and capacity provider strategy is not supported` | `--launch-type` and `--capacity-provider-strategy` are mutually exclusive on `create-service`/`update-service` | Pick one. We use `--capacity-provider-strategy "capacityProvider=FARGATE_SPOT,weight=1"` |
| `you must also specify a value for 'executionRoleArn'` | Container `secrets` (Secrets Manager injection) requires an execution role | Always include `executionRoleArn` when the TD has `secrets` |
| Service stops launching tasks after repeated crashes | ECS gives up retrying a failing deployment; `desired:1, running:0`, rollout "COMPLETED" | Fix the root cause, then `update-service --force-new-deployment` |
| Task health stuck `UNKNOWN` for minutes | Container `healthCheck.startPeriod: 180` = no checks for 3 min. This is NORMAL | Don't wait blindly — check `list-tasks --desired-status STOPPED` for crash loops first |
| `describe-tasks` shows no `logStreamName` | That field isn't reliably populated | Construct it: `<awslogs-stream-prefix>/<container-name>/<task-id>` |
| Logs empty right after task stops | CloudWatch ingestion lag (seconds) | Retry a few times, or use `aws logs filter-log-events --start-time ...` for crashed tasks |
| `startedAt: null` on a stopped task | Container died during provisioning/early startup — NOT proof of image-pull or secrets failure | Check the app logs (`filter-log-events`) before theorizing |
| `taskId length should be one of [32,36]` / `Unexpected number of separators` | An empty/`None` task ARN was passed to `describe-tasks` | Guard: `[ "$TASK_ARN" != "None" ] && [ -n "$TASK_ARN" ]` before describing |
| `Invalid control character` parsing `--container-definitions` | Multi-line strings (SQL, JSON) inline in CLI params | Never inline complex JSON: build with python `json.dump` to a temp file, use `--cli-input-json file://` |
| `The Systems Manager parameter name specified for secret ... is invalid` | `secrets[].valueFrom` with the `:json-key::` suffix requires the **full ARN** (name alone is treated as an SSM parameter) | Resolve names via `describe-secret --query ARN` before building TD JSON |
| `Tags can not be empty` from `RegisterTaskDefinition` | `describe-task-definition --include TAGS` returns `tags: []`, but registration rejects an explicit empty tag list | Omit `tags` from the registration payload when the observed list is empty; preserve and pass it only when non-empty |

### ECS anti-patterns

- **Blocking poll loops** (`for i in $(seq 1 48); sleep 10; done`) in a single shell call — they burn session time and risk losing everything to a hard timeout. Prefer `aws ecs wait services-stable`, or short bounded loops (<2 min) and re-invoke.
- **Passwords in task definitions** — a plaintext `PGPASSWORD` env var or a password embedded in `command` is visible to anyone with `ecs:DescribeTaskDefinition`. Always inject via `secrets[].valueFrom`. Deregister **and** `delete-task-definitions` one-off helper revisions after use (deregister alone keeps them describable as INACTIVE).

---

## Private RDS Access

The RDS instance has `PubliclyAccessible: No` — **no route from your machine, ever** (private subnets, no IGW route). Do NOT attempt local `psql` (it hangs until timeout) and do NOT make RDS public.

**Only sanctioned pattern:** a one-off Fargate task in the ECS security group:

```bash
scripts/ecs-run-sql.sh --database <db> --file <schema.sql> --verify "\dt"
```

`scripts/ecs-run-sql.sh` handles: TD JSON via `--cli-input-json file://`, base64 SQL transport (zero quoting bugs), `ON_ERROR_STOP=1`, password injection from Secrets Manager (never plaintext), correct log-stream resolution with ingestion-lag retry, and deregister+delete of its own TD revision. Details: [AWS_COMMANDS_GUIDE.md Part D](../plans/AUTOMATIC-BUILDS-AND-DEPLOY/AWS_COMMANDS_GUIDE.md).

### SQL discipline

- **Verify in the same run** — a schema apply that exits 0 may have applied nothing (escaping bugs silently no-op'd a whole schema once). Always pass `--verify`.
- `\c dbname` psql meta-commands work in `-f` files, but prefer one script run per database — clearer logs, clearer failures.
- `CREATE TABLE IF NOT EXISTS` still errors on real problems (missing FK target), but a malformed file can no-op silently — `--verify` is the only proof.

## Deterministic Staging Database Lifecycle

- `scripts/resume-staging.sh` must start from an absent staging DB. It creates
  empty encrypted RDS with an RDS-managed master password, then runs
  `scripts/bootstrap-staging-db.sh` before scaling any application service.
- Bootstrap applies `Auth/init-db/*` and `Items/init-db/*`, creates restricted
  application roles using password values injected from Secrets Manager, and
  verifies schemas, grants, seed counts, and application-user connectivity.
- `scripts/pause-staging.sh` deletes RDS with `--skip-final-snapshot` by default.
  A snapshot is allowed only through explicit `--retain-snapshot` with an
  `onlineshop-staging-debug-*` or `onlineshop-staging-dr-*` name.
- On CI failure, capture diagnostics before teardown. CloudWatch logs remain;
  the workflow also uploads `staging-diagnostics.txt` for 14 days.
- Production and staging entry points source different config files and assert
  `LC_ENVIRONMENT` before mutations. Never source staging config from a
  production wrapper.
- `ci-deploy-staging.sh` must verify the requested immutable tag exists in Auth,
  Items, and API Gateway ECR before registering any task definition. Without
  this preflight, a missing late-service image creates a partial deployment.

### Lifecycle progress logging

- All four pause/resume entry points log numbered high-level steps with UTC
  timestamps and experience-based typical durations. Shared helpers log each
  resource mutation, no-op, waiter, readiness retry, and verification result.
- Logs from value-returning helpers go to stderr so command substitution captures
  only values such as ALB ARNs and database endpoints.
- Typical totals: production resume 3–8 minutes; production pause 1–2 minutes;
  clean staging resume 10–20 minutes; staging pause 5–12 minutes without a
  snapshot or 10–20 minutes with one. These are operational guidance, not hard
  timeouts—AWS capacity, image pulls, JVM health checks, and RDS control-plane
  load can extend them.
## ECS RunTask and RDS cleanup

`ecs:RunTask` is authorized against the task-definition ARN, not the cluster
ARN. Scope the resource to the staging SQL-runner family and constrain the
cluster with `ArnEquals` on `ecs:cluster`. For cleanup, RDS accepts
`StopDBInstance` only from `available`; wait through transient start or
configuration states, and if the instance is already `stopping`, wait for
`stopped` without issuing a duplicate stop.
