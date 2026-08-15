# Clean-Slate CI/CD and Release System

**Delegation file:** `plans/AUTOMATIC-BUILDS-AND-DEPLOY/DELIVERY-SYSTEM-IMPLEMENTATION-PLAN.md`

## Summary

Implement the architecture defined by `SPEC.md`, `CONTRACTS.md`, `OPERATIONS.md`, and `VERIFICATION.md` from scratch.

```text
validation
  → immutable complete candidate
  → exact shared staging
  → owner-approved production
  → observed verification
  → official GitHub Release
  → four-release rollback window
```

The existing Pass 3R release framework is disposable implementation history. The new system must not import, source, or execute it. It may remain operational only until the replacement completes the live promotion and `N → N-1 → N` rollback drill.

## Implementation changes

### 1. Resolve the target contract

Before implementation:

- Treat the four generated specifications as normative.
- Resolve their Draft items:
  - Python + Boto3 delivery engine.
  - Candidate metadata retention: 30 days.
  - Production smoke tests: backend health, frontend marker/content, and read-only `GET /api/v1/items`.
  - Staging compatibility: previous official frontend against candidate backends, followed by candidate frontend E2E.
  - SBOMs: pinned Syft-generated SPDX JSON, permanently attached to official Releases with SHA-256 hashes in the manifest.
  - Runtime compatibility: record a sanitized configuration fingerprint; runtime/task-definition, schema, or network changes invalidate incompatible rollback targets.
  - Ownerless staging RDS reconciliation: scheduled GitHub Actions job every 15 minutes using the staging role.
- Update the four specifications and linked documentation so the decisions are explicit and their status can later move from Draft to Ready.

### 2. Build the new delivery package

Create a new `delivery/` Python package with:

- Strict candidate, staging-operation, production-snapshot, official-release, rollback-result, and evidence models.
- Canonical JSON serialization and schema/cross-identity validation.
- Exact workflow run/attempt authority.
- Boto3 adapters for ECR, ECS, RDS, S3, CloudFront, Secrets Manager, and GitHub-facing metadata.
- Mandatory post-mutation read-backs, bounded waiters, retry handling, and fail-closed read errors.
- CLI commands for candidate validation, staging lifecycle, deployment, verification, snapshot, finalization, recovery, rollback, retention audit, and RDS reconciliation.
- No plaintext secrets, arbitrary shell fragments, mutable deployment identities, or production database rollback.

Stable workflow inputs:

- Staging/promotion: exact candidate workflow run ID and attempt.
- Rollback: only an official `release-NNNN` identifier.
- No hand-entered digests, tags, task-definition ARNs, URLs, or manifests.

### 3. Replace CI and candidate publication

Create new workflows:

- `ci.yml`
- `_java-service.yml`
- `stage-candidate.yml`
- `promote-release.yml`
- `rollback-release.yml`
- `reconcile-staging.yml`

Required behavior:

- Pull requests and fork PRs have `contents: read` only and no AWS path.
- Feature pushes validate and publish complete staging-only candidates.
- Protected `main` pushes build Auth, Items plus `common`, API Gateway, and frontend from one SHA.
- Candidate backend images use immutable SHA identities and service-reported ECR digests.
- Frontend archives, SBOMs, test results, and candidate manifests are owned by the exact Actions run/attempt.
- No selective builds, component reuse, candidate relabeling, or promotion rebuilds.
- Candidate publication uses the Artifact Publisher role only.
- **Trigger isolation (implemented, OP-CUT-01):** `ci.yml` push triggers cover `greenfield/**` only — legacy `build-and-deploy.yml` still owns `main` and `feature/**` pushes, and overlapping triggers would push identical `sha-<fullsha>` tags into the same immutable ECR repositories on one commit. Live push-path proof runs on a `greenfield/**` branch; the push triggers expand to `main` + `feature/**` only at cutover, after legacy triggers are disabled (§7 / OP-CUT-02).

### 4. Implement shared staging

The staging workflow must:

```text
acquire non-canceling concurrency
  → revalidate candidate and environment
  → start persistent staging RDS/services
  → reset, seed, and verify staging data
  → deploy exact candidate digests
  → run compatibility and cloud E2E
  → capture evidence
  → stop services/RDS
  → verify cleanup
```

Use a staging ownership marker containing run ID, attempt, owner, and expiry. The scheduled reconciliation workflow must:

- Detect running RDS without an active staging owner.
- Stop it using the staging role.
- Verify the stopped state.
- Fail visibly on read errors rather than assuming absence.
- Never stop production resources.

### 5. Implement production promotion

Promotion must execute:

```text
select exact main candidate
  → read-only preflight
  → exact staging gate
  → protected owner approval
  → production concurrency lock
  → repeat full preflight against fresh state
  → capture production snapshot
  → deploy Auth + Items
  → deploy API Gateway
  → publish immutable frontend and switch marker
  → read-only production verification
  → allocate never-reused release-NNNN
  → publish official manifest, SBOMs, and evidence
  → audit rollback window
```

Backend deployment changes only the intended digest-pinned image. Frontend publication must verify checksum before changing the live entry point. Official publication happens only after successful observed verification.

### 6. Implement recovery, rollback, and retention

- Defined deployment, health, frontend, and finalization failures automatically restore the pre-mutation complete application snapshot.
- Ambiguous AWS or production states stop for manual investigation.
- Rollback requires protected owner approval and a complete retained official release.
- Rollback changes application artifacts only; it never reverses production schema or data.
- Retain and audit the current release plus three previous complete releases.
- Keep GitHub Releases and manifests indefinitely.
- Expire candidate-only artifacts after 30 days.
- Use direct ECR/S3 lifecycle controls with preview and read-back; do not build a retention simulator.
- Publish immutable SBOM assets with each official release.

### 7. Cut over and remove the legacy system

After all offline gates and live acceptance pass:

```text
disable legacy triggers
  → verify no legacy run can mutate AWS
  → run live promotion
  → run approved N → N-1 rollback
  → restore N
  → observe stable operation
  → owner approves deletion inventory
  → delete legacy implementation and procedures
```

Delete the old release-specific workflows, approximately 289-file `release/` tree, old release scripts/tests/policies, superseded Pass 3 documentation, and `GREENFIELD-SIMPLIFICATION-PLAN.md`.

Expanding the `ci.yml` push triggers from `greenfield/**` to `main` + `feature/**` happens only in this phase, immediately after legacy triggers are disabled and never before — both trigger sets must never match the same push while either can mutate AWS (OP-CUT-01/OP-CUT-02).

Retain only files justified by the new system, such as application code, service tests, Dockerfiles, SQL schema sources, Docker Compose, and reusable local development tooling.

Update:

- Root `AGENTS.md`.
- Each service-level `AGENTS.md`.
- `docs/CI_CD_GOTCHAS.md`.
- `docs/TESTING_STRATEGY.md`.
- New central `docs/DELIVERY.md`.

Documentation must describe the new flow and contain no stale references to deleted release machinery.

## Test and acceptance plan

Offline gates must cover every `VR-*` requirement:

- Candidate completeness, immutability, run/attempt identity, ancestry, and expiry.
- Hostile shell/context inputs and secret leakage.
- AWS permission boundaries and mutable-action rejection.
- Staging serialization, cleanup failure, ownerless-RDS handling, and exact artifacts.
- Promotion drift, stale deployments, ordering, frontend checksum, and finalization failure.
- Automatic recovery, ambiguous outcomes, rollback completeness, and database non-reversal.
- Four-release retention and protected-artifact checks.
- True resource absence versus AWS read failure.
- Bounded waits, retries, throttling, and idempotent resume.

Required tooling:

```bash
cd delivery
python -m pytest
python -m ruff check .
actionlint
zizmor
shellcheck
```

Before final cutover, run the affected service Maven tests from each service directory, the frontend tests, and the E2E suite using the repository’s testing strategy.

Live acceptance requires:

- Mandatory AWS identity preflight with `--profile dpm-profile --region eu-north-1`.
- Read-only environment, IAM/OIDC, ECR, ECS, S3, CloudFront, and CloudTrail inventory.
- One complete staging lifecycle.
- One owner-approved production promotion.
- One owner-approved `N → N-1 → N` rollback/restoration drill.
- Verified production read-only journeys.
- Successful cleanup and rollback-window audit.

Completion requires `VR-READY-01`, `VR-READY-02`, owner approval, and zero remaining executable references to the legacy release system.

## Assumptions

- Python + Boto3 is the permanent delivery implementation.
- Existing AWS infrastructure is consumed; no infrastructure-as-code migration is introduced.
- Production data and schema are never disposable.
- No production schema-changing release is permitted until migrations, compatibility rules, and backup/restore are implemented and tested.
- The four generated specifications remain the architectural source of truth.
