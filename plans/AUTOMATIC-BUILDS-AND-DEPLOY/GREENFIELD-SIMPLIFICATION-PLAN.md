# Greenfield CI/CD Simplification Plan

**Status:** proposal only — no implementation is authorized by this document  
**Last updated:** 2026-08-14  
**Goal:** replace the current bespoke release platform with the smallest safe,
readable delivery system suitable for one developer and four deployables.

## 1. Executive summary

Build the replacement beside the existing delivery system, from new code and
new tests. Do not import or call the old release implementation. Existing
scripts may be read only to recover verified AWS resource identifiers, command
semantics and operational lessons.

The proposed system is:

```text
feature push / pull request
        |
        v
      ci.yml
  tests + local Compose E2E
  no AWS credentials, no ECR

main push
        |
        v
      ci.yml
  test -> build every component -> publish immutable artifacts
        |
        v
private S3 candidate
  candidates/<commit>/candidate.json

manual promotion
        |
        v
   promote.yml
  candidate -> ephemeral staging -> cloud E2E -> destroy staging
        |
        v
protected production approval
  deploy -> verify -> publish release
        |
        +-- failure -> restore previous release

manual rollback
        |
        v
   rollback.yml
  protected approval -> deploy older release -> verify
```

Four workflow files, one deployment language, one test runner, two compact
manifest types, and no selective-build/reuse engine.

## 2. Why this is a greenfield replacement

The current implementation distributes the same release state machine across
GitHub Actions YAML, Bash, Python, `jq`, JSON Schema, generated AWS/GitHub CLI
stubs and hundreds of fixtures. Incrementally simplifying it would require
understanding and preserving those layers, which defeats the goal.

Greenfield rules:

1. Create a new clean branch/worktree from the agreed base, not from the dirty
   `feature/cicd-release-redesign` implementation worktree.
2. New delivery code MUST NOT source, import or execute the old release code.
3. Old scripts are reference material only for verified AWS calls, mandatory
   read-backs, resource identifiers and known failure modes.
4. Keep the old production path available until the replacement passes its
   staging proof and the user authorizes cutover.
5. Delete the old framework only after the replacement promotion and rollback
   drill succeed.

This is a logical rewrite, not a destructive big-bang deletion.

## 3. Decisions requiring user approval

The recommended choices are marked **recommended**. Phase 0 must record the
user's answers before implementation begins.

- [ ] **Rebuild all deployables on every trusted `main` push — recommended.**
  Do not implement selective builds, base-candidate ancestry or artifact reuse.
- [ ] **Run full local Docker Compose E2E on every feature push and PR — recommended.**
- [ ] **Run cloud staging only for a manually selected promotion — recommended.**
  Main pushes create candidates but do not create RDS/ALB/ECS staging.
- [ ] **Use a private S3 release-artifact bucket as candidate/release authority — recommended.**
  Do not use GitHub run-attempt artifact discovery as the release database.
- [ ] **Use Python + Boto3 for delivery logic — recommended.**
  No AWS CLI or `jq` business logic inside workflows.
- [ ] **Keep separate `promote.yml` and `rollback.yml` entry workflows — recommended.**
  They share one deployment engine but retain clear inputs and permissions.
- [ ] **Rely on GitHub Environment history for approval audit — recommended.**
  Do not call GitHub APIs merely to duplicate the approver into a manifest.
- [ ] **Keep infrastructure-as-code migration out of this rewrite — recommended.**
  Existing infrastructure is consumed through explicit configuration.

## 4. Scope

### In scope

- Feature/PR validation and local Docker Compose E2E.
- Trusted-main build and immutable candidate publication.
- Candidate and official release manifests.
- Ephemeral staging only during promotion.
- Production promotion, verification and automatic recovery.
- Owner-approved rollback to an existing immutable official release.
- Minimal IAM/OIDC separation for validation, candidate, staging and production.
- Replacement tests, workflow linting and one concise delivery guide.
- Removal of superseded release machinery after cutover.

### Explicitly out of scope

- Per-component SemVer.
- Selective builds or reuse of artifacts from older candidates.
- Custom traceability query products.
- Custom ECR retention simulation.
- GitHub approval API scraping.
- Database rollback.
- Schema-changing releases before versioned migrations and restore testing.
- Rewriting unrelated application or infrastructure architecture.
- IaC migration.

## 5. Target repository structure

```text
.github/workflows/
├── ci.yml
├── promote.yml
├── rollback.yml
└── _java-service.yml

delivery/
├── pyproject.toml
├── config/
│   ├── staging.json
│   └── production.json
├── src/delivery/
│   ├── models.py
│   ├── config.py
│   ├── aws.py
│   ├── environment.py
│   ├── deploy.py
│   └── cli.py
└── tests/
    ├── test_models.py
    ├── test_candidate.py
    ├── test_deploy.py
    ├── test_promotion.py
    └── test_rollback.py

docs/
└── DELIVERY.md
```

Estimated target budget, including tests:

| Area | Target |
|---|---:|
| Workflow YAML | 350–550 lines |
| Delivery Python | 1,200–2,000 lines |
| Delivery tests | 1,200–2,000 lines |
| Fixtures | 20–40 compact files |
| Total delivery surface | 3,500–5,000 lines |

The budget is a design constraint. Exceeding it requires a user-approved reason,
not silent framework growth.

## 6. Workflow design

### 6.1 `ci.yml`

Triggers:

- `push` to `feature/**`;
- `pull_request` to `main`;
- `push` to `main`.

Workflow-level permissions are `contents: read`.

Feature/PR path:

```text
Java tests (matrix: auth, items, gateway) ─┐
Frontend lint/build ──────────────────────┼─> local Docker Compose E2E
Delivery tooling tests + workflow lint ──┘
```

- No job reachable from a PR receives `id-token: write`.
- No AWS credential action, AWS API, ECR login or publication exists in the
  validation jobs.
- The full Compose API E2E suite runs even when a path filter says only one
  component changed. Path filters may skip unrelated unit jobs, not E2E.
- Fork PRs remain supported.

Trusted-main path:

```text
all validation succeeds
        |
        v
build Auth, Items and Gateway concurrently + build frontend
        |
        v
push sha-<full-main-sha> images + upload frontend archive
        |
        v
read back ECR digests and S3 checksum
        |
        v
write candidates/<sha>/candidate.json
```

Every main candidate contains all four components. There is no change resolver.

### 6.2 `_java-service.yml`

Closed input: `auth`, `items` or `gateway`. It MUST NOT accept shell commands.

The workflow owns this metadata:

| Component | Maven directory | Docker context | Dockerfile | Extra dependency |
|---|---|---|---|---|
| Auth | `Auth/` | `Auth/` | `Auth/Dockerfile` | none |
| Items | `Items/` | repository root | `Items/Dockerfile` | install `common` first |
| Gateway | `api-gateway/` | `api-gateway/` | `api-gateway/Dockerfile` | none |

The caller grants OIDC only for the trusted-main publication call. Validation
calls have `contents: read` only.

### 6.3 `promote.yml`

Manual inputs:

- `candidate_sha`: exact full main commit SHA;
- `version`: canonical `MAJOR.MINOR.PATCH`.

Jobs:

```text
preflight (read-only candidate validation)
        |
        v
staging (staging role)
  create -> deploy candidate -> cloud E2E -> always destroy
        |
        v
production (protected environment + production lock)
  capture current release -> deploy -> verify -> publish official manifest
        |
        +-- failure -> restore captured previous release -> verify
```

Staging is created at most once for an actual promotion. It is destroyed before
the workflow finishes, including on E2E or deployment failure.

### 6.4 `rollback.yml`

Manual input: one existing canonical official version.

The workflow loads `releases/v<version>/release.json`. It never accepts image
tags, digests, arbitrary URLs or hand-written manifests.

```text
read-only target validation
        |
        v
protected production approval + shared production lock
        |
        v
capture current release -> deploy target -> verify
        |
        +-- failure -> restore captured release -> verify
```

Rollback and promotion call the same deployment engine.

## 7. Artifact and release authority

Use a dedicated private S3 bucket, for example
`onlineshop-release-artifacts-<account>`, with public access blocked, encryption,
versioning and a simple candidate lifecycle rule.

```text
candidates/<commit>/candidate.json
candidates/<commit>/frontend.tar.gz
releases/v<version>/release.json
```

GitHub Actions and GitHub Releases may link to these records but are not the
authority. This removes run-attempt discovery, artifact-name ambiguity and
approval API coupling from the deployment engine.

### Candidate manifest

```json
{
  "schemaVersion": 1,
  "commit": "<40-char-main-sha>",
  "createdAt": "<UTC timestamp>",
  "workflowRun": "<trace URL>",
  "images": {
    "auth": {"repository": "onlineshop-auth", "digest": "sha256:<64-hex>"},
    "items": {"repository": "onlineshop-items", "digest": "sha256:<64-hex>"},
    "gateway": {"repository": "onlineshop-api-gateway", "digest": "sha256:<64-hex>"}
  },
  "frontend": {
    "object": "candidates/<commit>/frontend.tar.gz",
    "sha256": "<64-hex>"
  }
}
```

### Official release manifest

```json
{
  "schemaVersion": 1,
  "version": "1.2.3",
  "candidateCommit": "<40-char-main-sha>",
  "previousVersion": "1.2.2",
  "promotedAt": "<UTC timestamp>",
  "images": {
    "auth": {"repository": "onlineshop-auth", "digest": "sha256:<64-hex>"},
    "items": {"repository": "onlineshop-items", "digest": "sha256:<64-hex>"},
    "gateway": {"repository": "onlineshop-api-gateway", "digest": "sha256:<64-hex>"}
  },
  "frontend": {
    "prefix": "_releases/v1.2.3/",
    "sha256": "<64-hex>"
  }
}
```

Rules:

- Candidate and release keys are immutable after successful creation.
- Reuse of an existing key succeeds only when the bytes match exactly.
- ECS always receives `repository@sha256:...`, never a tag.
- Optional `release-<version>` ECR tags exist only for retention/operator
  visibility and are never deployment inputs.
- Keep official manifests indefinitely. Candidate objects may expire after 30
  days. Keep at least the latest 10 official image tags and frontend prefixes.
- Do not build a custom retention simulator; apply a small policy and verify it
  with direct read-back.

## 8. Deployment engine

One Python package owns all deployment behavior. GitHub workflows contain only
orchestration and typed arguments.

Proposed CLI:

```text
python -m delivery candidate validate <candidate.json>
python -m delivery environment up staging
python -m delivery environment down staging
python -m delivery deploy --environment staging|production --manifest <file>
python -m delivery verify --environment staging|production --manifest <file>
python -m delivery restore --environment production --manifest <previous-release>
```

Generic backend deployment:

1. Describe the ECS service and its current task definition.
2. Copy the registered definition in memory.
3. Replace only the intended container image with the manifest digest URI.
4. Register the new revision.
5. Immediately describe the revision and compare the changed fields.
6. Update the service with circuit-breaker rollback enabled.
7. Wait for the deployment started by this command.
8. Describe tasks and verify the observed running image digest.

Auth and Items deploy concurrently. Gateway deploys after both pass. Frontend
publishes immutable assets first and the live `index.html`/release marker last.

Promotion and rollback differ only in manifest selection. They do not have
separate deploy implementations.

Recovery:

- Read the current production `release.json` before mutation.
- Load its immutable official manifest.
- If the new deployment fails after mutation, deploy the previous manifest
  through the same engine and verify it.
- If recovery fails, stop and report both failures. Never fabricate success.

Task-definition configuration is not versioned by this application release
engine. The engine copies current configuration and changes only images. Future
task-definition/infrastructure changes require a separate explicit mechanism.

## 9. AWS and configuration rules

- GitHub Actions uses OIDC environment credentials and Boto3's default
  credential chain. It never creates or references `dpm-profile`.
- Local/operator commands always include
  `--profile dpm-profile --region eu-north-1` and begin with the mandatory STS
  identity check.
- Every AWS create/put/delete has an immediate describe/get/list read-back.
- Environment-specific non-secret identifiers live only in
  `delivery/config/{staging,production}.json`, not workflow YAML.
- Secrets remain in Secrets Manager and enter ECS through full-ARN
  `secrets[].valueFrom`; they never enter manifests, logs or task-definition
  plaintext environment values.
- Start with three roles: candidate publication, staging deployment and
  protected production mutation. PR jobs have no OIDC permission.
- Promotion and rollback may share the production mutation role because both
  require the same ECS/S3 deployment capabilities. Split them only if a real
  least-privilege difference is demonstrated.

## 10. Test strategy

Use one test runner: `pytest`.

```text
pytest
├── manifest validation
├── environment configuration
├── deployment plan
├── Boto3 adapter calls/read-backs
├── promotion success
├── partial failure -> restore previous release
└── rollback success/failure

actionlint  -> workflow syntax and expressions
zizmor      -> GitHub Actions security
ruff        -> Python lint/format
mypy/pyright (optional after value is proven) -> type checks
shellcheck  -> only remaining small operator shell scripts
```

Adapter testing:

- Use Botocore `Stubber` for AWS request/response contracts or one small typed
  in-process `FakeAws` adapter for state transitions.
- Do not generate fake `aws` or `gh` executables.
- Do not embed Python heredocs in Bash tests.
- Do not repeat the complete Python suite from multiple shell gates.
- Keep three high-value end-to-end offline scenarios: promotion success,
  deployment failure with successful restore, and rollback.
- Add focused cases only for an invariant that could cause a real unsafe
  mutation. Avoid fixture accumulation for formatting trivia.

## 11. Documentation strategy

- `docs/DELIVERY.md` is the single cross-cutting delivery guide.
- Root `AGENTS.md` contains only mandatory rules and a link to that guide.
- Service `AGENTS.md` files contain only service-specific build inputs,
  dependency/context differences and local test commands. They link to the
  central delivery guide instead of duplicating the entire release process.
- Historical Pass 3 documentation is retained only until cutover, then archived
  or deleted with the old implementation.
- Any revision to the current recursive documentation rule requires explicit
  user approval during the cleanup phase.

## 12. Execution and agent budget

The previous review loop consumed disproportionate model usage. This rewrite
uses a bounded process:

1. One implementation owner per phase; use a subagent only for a concrete,
   independent task.
2. One consolidated reviewer after the phase is complete.
3. Fix all review findings together.
4. One delta review only; a third review requires user approval.
5. Do not use max reasoning by default. Reserve it for the deployment/recovery
   engine or a concrete hard blocker.
6. Stop after every phase for user review and manual testing.
7. Never start the next phase without explicit acceptance.

Each handoff reports files changed, commands run, claims not verified live, and
exact manual test instructions.

## 13. Phased implementation plan

### Status dashboard

| Phase | Product | Implementation | Automated | Review | Manual acceptance |
|---|---|---:|---:|---:|---:|
| 0 | Decisions, clean worktree and baseline | pending | n/a | pending | pending |
| 1 | Greenfield package and minimal manifests | pending | pending | pending | pending |
| 2 | Feature/PR validation workflow | pending | pending | pending | pending |
| 3 | Trusted-main candidate publication | pending | pending | pending | pending |
| 4 | Generic deploy/verify/restore engine | pending | pending | pending | pending |
| 5 | Promotion and ephemeral-staging workflow | pending | pending | pending | pending |
| 6 | Rollback workflow | pending | pending | pending | pending |
| 7 | One live staging proof | pending | pending | pending | pending |
| 8 | Production cutover, rollback drill and deletion | pending | pending | pending | pending |

### Phase 0 — Decisions and clean start

Deliverables:

- Record the user decisions from Section 3.
- Resolve the exact source commit/branch for the rewrite.
- Create a new clean worktree/branch, suggested name
  `feature/delivery-v2-greenfield`.
- Record current required branch-protection check names.
- Inventory old files as `delete`, `retain operationally`, or `reference only`.
- Record existing non-secret AWS identifiers without copying old code.
- Prevent the legacy feature-branch workflow from running costly AWS work while
  the greenfield branch is tested; use the smallest explicit trigger change.

Automated checks: clean worktree, `git diff --check`, no AWS.

Manual checkpoint: user reviews decisions, target tree, inventory and trigger
isolation. Stop.

### Phase 1 — Package and manifests

Deliverables:

- Create `delivery/` with pinned minimal dependencies.
- Implement candidate/release dataclasses and strict validation.
- Implement canonical JSON serialization and collision-safe file/S3 key rules
  without contacting AWS.
- Add 10–20 concise pytest cases.
- Add Ruff and pytest commands.

Automated checks:

```bash
cd delivery
python -m pytest
python -m ruff check .
```

Manual checkpoint: inspect the two manifest examples and deliberately reject
one malformed digest/version. No AWS or staging. Stop.

### Phase 2 — Feature/PR validation

Deliverables:

- Create `_java-service.yml` and validation jobs in `ci.yml`.
- Preserve Items -> common build behavior.
- Add frontend lint/build.
- Run full local Compose E2E on feature pushes and PRs.
- Add stable required-check aggregation.
- Add `actionlint` and `zizmor` with pinned setup/actions.
- Prove validation jobs have `contents: read` only and no AWS steps.

Automated checks: actionlint, zizmor, service tests, frontend tests and local
Compose E2E. No AWS.

Manual checkpoint: user reviews the workflow diagram and runs the same local
commands. If desired, push only after legacy-trigger isolation is confirmed.
Stop.

### Phase 3 — Trusted-main candidates

Deliverables:

- Add main-only build/publish jobs with job-scoped OIDC.
- Build all components in parallel with BuildKit cache.
- Publish immutable SHA image tags and frontend candidate archive.
- Read back exact ECR digests and S3 checksum.
- Write one immutable candidate manifest under the main SHA.
- Add candidate publication role/policy and offline policy tests.

Automated checks use Boto3 stubs; no live AWS in the implementation phase.

Manual checkpoint: inspect a locally rendered candidate. A live candidate is
deferred until the explicit live checkpoint. Stop.

### Phase 4 — Generic deploy, verify and restore

Deliverables:

- Implement the AWS adapter and generic task-definition image replacement.
- Deploy Auth/Items concurrently and Gateway afterward.
- Implement frontend immutable publication and live-marker-last behavior.
- Implement observed digest/health/frontend verification.
- Implement previous-manifest recovery through the same engine.
- Test promotion success, partial failure/recovery and rollback behavior.

Automated checks use Stubber/FakeAws only. No live AWS or staging.

Manual checkpoint: run CLI dry-runs against fixtures and inspect the exact AWS
operation plan/read-backs. Stop.

### Phase 5 — Promotion and ephemeral staging

Deliverables:

- Rewrite only the necessary staging lifecycle operations from scratch using
  verified AWS semantics as reference.
- Create `promote.yml` with candidate/version inputs.
- Add staging OIDC job, cloud E2E, unconditional teardown, protected production
  job and non-cancelling production lock.
- Ensure teardown owns ECS, ALB and RDS cleanup and never silently retains a
  database snapshot.
- Keep all production behavior dry-run/offline in this phase.

Automated checks: pytest scenarios, workflow linters and teardown-plan tests.
No live staging.

Manual checkpoint: user reviews the complete promotion workflow and teardown
plan. Stop.

### Phase 6 — Rollback

Deliverables:

- Create `rollback.yml` accepting only an existing official version.
- Reuse the generic deploy/verify/recovery engine.
- Prove target-not-current, immutable manifest and retained frontend prefix.
- Use the protected production Environment and shared mutation lock.

Automated checks are offline. No live AWS.

Manual checkpoint: run rollback fixture scenarios and inspect the workflow.
Stop.

### Phase 7 — One live staging proof

Requires explicit user approval.

1. Run the mandatory STS identity command with profile and region.
2. Publish one controlled candidate through the new main candidate path.
3. Create staging once.
4. Deploy the exact candidate digests/archive.
5. Run cloud E2E and verify observed digests/checksum.
6. Tear staging down immediately.
7. Read back teardown state.

If the AWS session is expired, stop and request re-authentication. Do not retry.

Manual checkpoint: user reviews timing, AWS read-backs, logs and teardown proof.
No production change. Stop.

### Phase 8 — Cutover and deletion

Requires separate explicit user approval for production mutation and deletion.

- Switch required checks to the new CI workflow.
- Run one protected production promotion.
- Verify exact running digests, health and frontend marker.
- Run a controlled release N -> N-1 -> N rollback drill.
- Disable legacy workflows first; observe before deleting.
- Delete superseded release scripts, Python contracts, shell gates, fixtures and
  repeated documentation only after a reviewed inventory proves no retained
  operator path references them.
- Keep useful pause/resume and diagnostic commands only when they remain
  independent of the old release framework.
- Update the status dashboard and produce the final concise delivery guide.

Manual checkpoint: user approves the exact deletion list and final cutover.

## 14. Deletion policy

Nothing is deleted merely because it looks old. A file is removed only when:

1. the new live path no longer invokes it;
2. repository references are removed or intentionally historical;
3. replacement behavior has automated evidence;
4. any required live behavior has passed the authorized staging/cutover proof;
5. the user approves the deletion inventory.

Likely deletion candidates after cutover:

- old build/promotion/rollback workflow files;
- `plans/AUTOMATIC-BUILDS-AND-DEPLOY/release/` executable framework;
- old release shell gates and generated stateful CLI stubs;
- redundant fixtures and duplicated release documentation;
- selective-build/reuse, custom traceability and retention-model code.

Potentially retained after explicit review:

- environment-independent operational pause/resume tools;
- private-RDS SQL runner if still used operationally;
- concise AWS troubleshooting commands;
- non-secret resource configuration until IaC replaces it.

## 15. Issues and dependencies

| Status | Issue | Resolution/next action |
|---|---|---|
| Open | Decisions in Section 3 are not approved | User confirms before Phase 0 implementation |
| Open | Exact clean base commit is not selected | Resolve from `main` at Phase 0 |
| Open | Dedicated private release-artifact bucket may not exist | Inventory read-only; define/apply only at approved live checkpoint |
| Open | Candidate/staging/production OIDC roles may not match target split | Design offline; apply and read back only in authorized phase |
| Open | Legacy workflow triggers may run AWS work on the greenfield branch | Isolate triggers before first push |
| Open | Branch protection references legacy check names | Record now; switch only during cutover |
| Open | Clean staging creation remains intrinsically slow | Run it only for selected promotion and once during proof |
| Open | Database migrations/restore are not production-ready | Continue blocking schema-changing releases |
| Open | Old operational scripts may depend on release helpers | Build a reference graph before deletion |
| Open | Recursive documentation policy causes cross-cutting duplication | Propose central-link revision for user approval in cleanup |

## 16. Safety simplifications that are forbidden

Do not reduce code by:

- accepting hand-written tags, digests, manifests or arbitrary URLs;
- deploying mutable `latest` tags;
- giving PR jobs AWS credentials;
- removing production approval or the shared production lock;
- removing observed digest/health/frontend verification;
- removing pre-mutation previous-release capture and recovery;
- deleting immutable frontend releases before a rollback-capable replacement;
- allowing database schema changes without migration/restore discipline;
- putting environment identifiers, AWS CLI programs or JSON transforms into
  workflow YAML;
- treating a successful command exit as mutation verification;
- deleting the old path before the replacement is proven.

## 17. Fresh-session continuation instructions

Start a new session in the repository and say:

```text
Read AGENTS.md and
plans/AUTOMATIC-BUILDS-AND-DEPLOY/GREENFIELD-SIMPLIFICATION-PLAN.md.
This is a proposal-only greenfield rewrite. Do not continue Pass 3R and do not
reuse/import/call the old release implementation. Start with Phase 0 only:
confirm the pending decisions with me, identify the clean base, create the new
isolated worktree only after approval, and stop at the Phase 0 checkpoint.
Do not call AWS, start staging, mutate GitHub, commit, push or delete anything
unless the phase and I explicitly authorize it. Keep agent usage bounded as
specified in Section 12.
```

The next session MUST NOT infer that the recommended decisions are accepted
merely because this file exists.

## 18. Post-review proposals awaiting decision

**Status:** discussion record only. Nothing in this section is approved. Where
this section conflicts with an earlier recommendation, Phase 0 must ask the
user which direction to adopt and then update the main body of the plan rather
than treating this appendix as an implicit override.

### 18.1 Selective component builds with complete release compositions

The original recommendation to rebuild all four deployables on every trusted
`main` push is a simplicity choice, not a technical requirement. Auth,
API Gateway and frontend can be built independently. Items is independently
deployable but its build-change boundary includes `common`.

The strongest alternative preserves complete, immutable candidates while
building only the components changed since the candidate's trusted baseline:

```text
latest successful component artifacts on main
├── Auth      -> digest A, component source SHA A
├── Items     -> digest B, component source SHA B
├── Gateway   -> digest C, component source SHA C
└── Frontend  -> checksum D, component source SHA D
                         |
                         | frontend-only change
                         v
build Frontend -> checksum E
                         |
                         v
new complete candidate composition
├── Auth      -> reuse immutable digest A
├── Items     -> reuse immutable digest B
├── Gateway   -> reuse immutable digest C
└── Frontend  -> use immutable checksum E
```

The candidate remains the atomic unit promoted to staging and production. It
is not a partial instruction to "change whatever fields are present." Promotion
and rollback always consume a complete four-component composition.

Three options must be decided explicitly:

| Option | Benefit | Cost/risk | Current assessment |
|---|---|---|---|
| Rebuild all four on every `main` push | Smallest implementation; one source SHA describes all bytes | Wastes build time and registry work for unrelated changes | Safest simplicity baseline |
| Build changed components and compose a complete candidate | Avoids rebuilding Auth for a frontend-only change while retaining deterministic promotion/rollback | Requires a small, carefully tested resolver and per-component provenance | **Recommended proposal after review** |
| Publish/deploy partial manifests | Superficially smallest selective workflow | Ambiguous staging state, unsafe recovery and non-reproducible rollback | Reject |

If the selective proposal is approved, "one release equals one monorepo commit"
is no longer strictly true. A system SemVer identifies one tested composition,
and every component entry records its own source commit and immutable artifact
identity. The main commit that assembled the candidate is recorded separately.

### 18.2 Proposed component publication and resolution contract

Suggested private S3 records:

```text
components/auth/builds/<source-sha>/artifact.json
components/items/builds/<source-sha>/artifact.json
components/gateway/builds/<source-sha>/artifact.json
components/frontend/builds/<source-sha>/artifact.json

components/auth/main.json       # mutable discovery pointer only
components/items/main.json
components/gateway/main.json
components/frontend/main.json

candidates/<assembly-main-sha>/candidate.json
releases/v<version>/release.json
```

The component `main.json` records are discovery pointers. Neither staging nor
production may deploy a pointer or mutable ECR tag. Candidate assembly resolves
each pointer to an immutable ECR digest or frontend object checksum, verifies
the bytes with AWS read-back, and freezes those exact identities in the
candidate manifest.

Example candidate shape:

```json
{
  "schemaVersion": 1,
  "assemblyCommit": "<40-char-main-sha>",
  "createdAt": "<UTC timestamp>",
  "workflowRun": "<trace URL>",
  "components": {
    "auth": {
      "sourceCommit": "<40-char-main-sha>",
      "repository": "onlineshop-auth",
      "digest": "sha256:<64-hex>"
    },
    "items": {
      "sourceCommit": "<40-char-main-sha>",
      "repository": "onlineshop-items",
      "digest": "sha256:<64-hex>"
    },
    "gateway": {
      "sourceCommit": "<40-char-main-sha>",
      "repository": "onlineshop-api-gateway",
      "digest": "sha256:<64-hex>"
    },
    "frontend": {
      "sourceCommit": "<40-char-main-sha>",
      "object": "components/frontend/builds/<source-sha>/frontend.tar.gz",
      "sha256": "<64-hex>"
    }
  }
}
```

Proposed change boundaries:

| Changed path | Required component build |
|---|---|
| `Auth/**` | Auth |
| `Items/**` | Items |
| `common/**` | Items |
| `api-gateway/**` | Gateway |
| `frontend/**` | Frontend |
| A component's Docker/build inputs | That component |
| Shared delivery or cross-component build inputs | Explicit affected set; all four when the impact cannot be proven narrower |

Rules required to keep this safe:

1. A mutable `main-latest` tag may aid human discovery but is never a candidate
   or deployment input.
2. An older workflow finishing late must not move a component pointer backward.
   Update the pointer with an S3 conditional write; on conflict, reload it and
   accept the proposed source only when it advances the current `main` ancestry.
3. Reuse is permitted only for a component proven unchanged relative to the
   selected successful baseline. A changed component must produce and verify
   its current artifact before that assembly commit can become a candidate.
4. If any component changed by one main commit fails its build, do not silently
   substitute its older artifact in a candidate for that commit. Fully
   independent partial releases are a different product contract and require a
   separate user decision.
5. The full locally composed system is tested before publication, and the exact
   assembled composition is tested again in ephemeral cloud staging during
   promotion. Independent builds do not remove cross-service API, token, event
   or database compatibility risks.
6. Promotion verifies that neither the assembly commit nor an individual
   component pointer moves backward from the current official release. Old code
   is selected through rollback, not disguised as a new promotion version.
7. Rollback restores one complete previous composition, even when only one
   component changed in the intervening release.

The smallest useful proof is a three-commit offline scenario:

```text
commit A: Auth changes      -> build Auth; reuse Items/Gateway/Frontend
commit B: frontend changes  -> reuse Auth from A; build Frontend
commit C: common changes    -> build Items; reuse Auth/Gateway/Frontend
```

The proof must also run an older Auth build after the newer one and demonstrate
that the component pointer cannot move backward.

### 18.3 Safety and completeness gaps found in review

The following findings apply whether Phase 0 chooses rebuild-all or selective
component builds:

1. **S3 immutability needs an enforceable contract.** Bucket versioning alone
   does not make a stable key immutable. Candidate, component-build and release
   records need conditional create, byte-identical retry handling, role/prefix
   separation and immediate read-back. Decide whether S3 Object Lock is
   warranted or whether conditional writes plus least privilege are sufficient
   for this learning platform.
2. **Release tags and retention currently conflict.** The plan calls ECR
   `release-<version>` tags optional but relies on them to retain rollback
   images after candidate tags expire. Make release tags mandatory retention
   anchors for every official release while continuing to forbid them as
   deployment inputs.
3. **The proposed CLI is incomplete.** Add explicit component publication,
   candidate assembly/fetch, collision-safe release publication and preflight
   commands. Either add `delivery/__main__.py` for `python -m delivery` or use
   `python -m delivery.cli`; the current target tree and command examples do
   not match.
4. **The first live phase lacks infrastructure bootstrap.** Phase 7 assumes the
   private artifact bucket and candidate/staging roles already exist even
   though earlier phases test their definitions offline only. Add an explicitly
   approved bootstrap step with mandatory create/put read-backs before the
   first live candidate.
5. **Rollback preflight must prove retained bytes before mutation.** Validate
   that every manifest ECR digest exists and the immutable frontend prefix and
   marker match before production approval and repeat the proof after approval
   under the production lock.
6. **SBOM and audit evidence are an unrecorded regression.** The current path
   retains four SBOMs, staging conclusions and richer promotion evidence. The
   rewrite must either preserve small SBOM/evidence objects without rebuilding
   the old contract framework or record explicit user acceptance that they are
   being removed. GitHub Environment history alone may not be an indefinite
   audit store.
7. **Frontend cutover needs an exact commit protocol.** Specify immutable asset
   upload, live `release.json`/`index.html` ordering, CloudFront invalidation,
   origin read-back and public-endpoint verification. "Marker/index last" is
   insufficiently precise because the two live objects are not atomic.
8. **Application rollback does not restore task configuration.** Copying the
   current task definition and changing only images is deliberately an
   application-only rollback. Define compatibility rules so a later task-role,
   secret, port or environment change cannot silently invalidate releases that
   are still advertised as rollback-capable.
9. **The target workflow tree omits unrelated workflows.** "Four workflow
   files" means four delivery workflows. Existing unrelated automation such as
   assistant/review workflows is retained unless separately reviewed.

### 18.4 Required Phase 0 decisions after this review

Before implementation, Phase 0 must now record answers to these questions in
the main decision section:

- Rebuild-all candidates or selective component builds with complete
  composition manifests?
- If selective, is a main commit still atomic—meaning every component changed
  by that commit must succeed before candidate publication—or are genuinely
  independent partial releases required?
- Conditional S3 immutability or S3 Object Lock?
- Retain lightweight SBOM/promotion evidence or explicitly remove it?
- Which task-definition/infrastructure changes invalidate the advertised
  rollback window?

If selective builds are accepted, update Sections 1, 3, 6, 7, 10 and 13 before
writing implementation code. Remove the earlier "no selective-build/reuse
engine" statements, but keep the resolver deliberately small: path-to-component
rules, immutable component records, monotonic discovery pointers and complete
candidate assembly. Do not restore base-candidate artifact discovery through
GitHub run attempts or the former generated-stub/fixture framework.
