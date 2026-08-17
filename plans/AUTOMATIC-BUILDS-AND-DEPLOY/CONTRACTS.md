# CI/CD Observable Contracts

**Status:** Draft — deferred implementation choices resolved (SPEC §4.5); Ready pending VR-READY-01 evidence  
**Parent architecture:** [`SPEC.md`](./SPEC.md)  
**Normative owner:** candidate, release, environment-state, observation, and evidence contracts (`CT-*`)

This document owns observable shapes and authority only. Lifecycle order and
failure transitions belong to [`OPERATIONS.md`](./OPERATIONS.md).

---

## Contents

1. [Contract conventions](#1-contract-conventions-ct-gen)
2. [Authority model](#2-authority-model-ct-auth)
3. [Candidate contract](#3-candidate-contract-ct-cand)
4. [Official release contract](#4-official-release-contract-ct-rel)
5. [Staging state contract](#5-staging-state-contract-ct-stg)
6. [Production observed-state contract](#6-production-observed-state-contract-ct-prod)
7. [Audit and diagnostic evidence](#7-audit-and-diagnostic-evidence-ct-audit)
8. [Application boundary](#8-application-boundary-ct-app)

## 1. Contract conventions (CT-GEN)

### CT-GEN-01 — Stable and inspectable identity

Records connect source, run, artifacts, operation, and result without mutable
identity: full SHA, exact workflow run/attempt, backend digests, and frontend
checksums where applicable. Times are UTC; algorithms and canonical
representations are explicit; human labels never replace machine identity.

### CT-GEN-02 — No executable or secret-bearing inputs

Records exclude credentials, secret values, plaintext task secrets, arbitrary
operator URLs/command fragments, environment credentials, and mutable values
presented as deployment identity. Untrusted GitHub context is validated data,
never executable text.

### CT-GEN-03 — Validation and fail-closed reads

A consumed record must pass schema/version and cross-identity validation. An
unknown version, malformed/inconsistent field, or backing-store read failure is
an error; read failure is never normalized to absence, `false`, or clean drift.

### CT-GEN-04 — Exact-match resume

An existing record/object is reusable only when its complete immutable identity
matches the intended identity. Collision, partial state, or byte mismatch fails
closed. `OPERATIONS.md` owns which lifecycle states permit resume.

## 2. Authority model (CT-AUTH)

| Question | Authority | Not sufficient |
|---|---|---|
| Candidate source | manifest in exact Actions run/attempt | branch, short SHA, tag |
| Backend bytes | ECR service-reported digest | tag text |
| Candidate frontend bytes | Actions artifact identity + content checksum | artifact name |
| Running backend bytes | ECS running-task image digests | task definition or service stability |
| Deployed frontend | immutable S3 identity/checksum + public marker | URL, ETag, upload success |
| Official release | published GitHub Release + exact manifest | deployed bytes alone |
| Current rollback capability | official manifest + matching retained ECR/S3 bytes + compatibility audit | historical release record |
| Production business state | production RDS | release/rollback record |

## 3. Candidate contract (CT-CAND)

### CT-CAND-01 — Minimum candidate manifest

```text
schemaVersion
candidateId
candidateClass: feature | main
source: repository, branch, ref, fullSha
build: workflowRunId, workflowRunAttempt, workflowUrl, createdAt, completedAt
artifacts.auth: repository, digest
artifacts.items: repository, digest, commonSourceSha
artifacts.gateway: repository, digest
artifacts.frontend: artifactId, artifactDigest, contentChecksum
tests: unit, integration, frontend, localE2E conclusions
productionEligible
```

All four artifacts share one source SHA/run; `commonSourceSha` equals that
SHA. Feature class implies `productionEligible=false`; only trusted protected
`main` may set it true ([AD-03](./SPEC.md#ad-03-branch-class-determines-eligibility)).
Production task-definition ARNs are excluded because they are observed
deployment state.

### CT-CAND-02 — Candidate identifier and tags

`candidateId` is readable and collision-resistant. Convenience tags may encode
branch, workflow sequence, attempt, and short SHA, but neither tag nor
`candidateId` replaces digests and the complete manifest.

### CT-CAND-03 — Candidate authority and deployability

The exact run/attempt jointly owns the manifest, frontend archive, and evidence;
ECR owns the three recorded backend digests. Missing, expired, partial, or
cross-identity-mismatched members make the candidate undeployable; remaining
images do not reconstruct authority.

### CT-CAND-04 — Build-order and source-history comparison

Comparison exposes both:

| Dimension | Values | Basis |
|---|---|---|
| Build order | `before`, `after`, `same build` | run/build identity and time |
| Source relation | `ancestor`, `descendant`, `identical`, `diverged`, `unknown` | Git history |

Build order never implies ancestry.

### CT-CAND-05 — Candidate visibility state

| State | Meaning |
|---|---|
| `current` | newest successful candidate for branch head |
| `previous` | older candidate reachable from branch head |
| `superseded` | retained candidate no longer reachable after history rewrite |
| `expired` | candidate metadata/frontend no longer deployable |

A retained `superseded` feature candidate remains explicitly stageable with a
warning; `expired` is never stageable or promotable.

## 4. Official release contract (CT-REL)

### CT-REL-01 — Minimum release manifest

```text
schemaVersion, releaseId, candidateId
source: fullSha, branch=main
previousReleaseId, promotedAt, requester
approval evidence, workflow URL
artifacts.auth/items/gateway: repository + digest
artifacts.frontend: immutable S3 identity + checksum
artifacts.sbom: pinned Syft SPDX JSON asset identity + SHA-256 hash per component
compatibilityFingerprint: sanitized configuration fingerprint (never secrets)
staging evidence identity + conclusion
production verification identity + conclusion
rollbackCapableAtPublication
```

Artifact identity equals the promoted candidate; verification identifies the
same observed deployment. SBOM assets are pinned Syft-generated SPDX JSON and
attach permanently to the official release
([OP-FIN](./OPERATIONS.md#7-official-finalization-op-fin),
[OP-RET-02](./OPERATIONS.md#op-ret-02-retention-classes)). The
`compatibilityFingerprint` records sanitized configuration only — never
secrets, per [CT-GEN-02](#ct-gen-02-no-executable-or-secret-bearing-inputs).
The record becomes official only as defined by
[AD-06](./SPEC.md#ad-06-a-verified-github-release-is-official-authority).

### CT-REL-02 — Immutable history and partial publication

Published manifests are immutable. Existing official objects are resumable
only on complete identity equality; mismatch fails closed. Rollback has a
separate result and never edits history. `rollbackCapableAtPublication` is
historical, not a claim of current capability after retained bytes expire.

### CT-REL-03 — Sequential identity

`releaseId` is monotonic `release-NNNN`; candidates consume no number. The
record includes `previousReleaseId`. [OP-FIN](./OPERATIONS.md#7-official-finalization-op-fin)
owns allocation and failure timing.

## 5. Staging state contract (CT-STG)

### CT-STG-01 — Operation record

```text
candidateId, branch, fullSha, workflow run/attempt
owner, acquiredAt, current phase, completedAt
database reset/seed/access verification
expected and observed backend digests/frontend checksum
compatibility conclusion or bootstrap exception
cloud E2E conclusion
cleanup conclusion
```

Phase values come from [OP-STG](./OPERATIONS.md#4-shared-staging-state-machine-op-stg).
Expected and observed identities remain distinct.

### CT-STG-02 — Visibility, not a second lock

This record provides visibility/evidence only; it has no locking authority and
cannot claim ownership after the platform lock is released. Cleanup failure
remains visible, and E2E success without cleanup success is not valid promotion
evidence. [OP-GEN-01](./OPERATIONS.md#op-gen-01-locks-approval-and-fresh-state)
owns serialization.

## 6. Production observed-state contract (CT-PROD)

### CT-PROD-01 — Backend observation

For each backend, evidence records the operation's deployment identity,
service/task-definition identity, health state, expected digest, actual
running-task digest, and equality result. Generic stability or a task-definition
URI is not observed running-byte identity.

### CT-PROD-02 — Frontend observation

Evidence records immutable S3 identity/checksum, live marker, public
CloudFront-visible identity, and equality. Before finalization the marker names
the candidate; afterward it names the official release while preserving
candidate/checksum identity. URL, ETag, cache header, or upload success alone is
insufficient.

### CT-PROD-03 — Read-only application verification

Evidence records the selected read-only production journeys and conclusions.
The journeys are backend health, frontend marker/content observed through
CloudFront, and read-only `GET /items` through the gateway (the gateway
rewrites `/items/**` to the items service's internal `/api/v1/items/**`).
They read health, release identity, and public application state but do not
mutate business data.

### CT-PROD-04 — Mutation verification

Mutation evidence includes the immediate describe/get/list read-back required
by [OP-GEN-04](./OPERATIONS.md#op-gen-04-mutation-verification-and-secret-safety)
and expected-versus-observed equality. Missing and read-error are distinct.

## 7. Audit and diagnostic evidence (CT-AUDIT)

### CT-AUDIT-01 — Common workflow evidence

Applicable operations record UTC phases; candidate/release and source identity;
run/attempt; owner/requester/approval; expected and observed artifacts;
staging/deployment/verification/recovery/rollback/cleanup conclusions; and
sanitized AWS request IDs. Failures add environment, failed phase, mutation
status, and cleanup/recovery result.

A production pre-mutation snapshot additionally records the official
release/manifest, desired runtime settings, service/deployment/task-definition
identities, running digests, immutable frontend/live marker/checksum, and
configuration needed to restore those artifacts.

### CT-AUDIT-02 — Evidence ownership and retention role

CloudWatch owns application/ECS logs; Actions owns bounded candidate and
operation diagnostics; GitHub Releases owns permanent official identity and
compact evidence. Diagnostics exist before destructive cleanup. [OP-RET](./OPERATIONS.md#9-retention-op-ret)
owns retention periods and complete-set protection.

## 8. Application boundary (CT-APP)

### CT-APP-01 — No new domain contract

Delivery introduces no public domain API/event and owns no Auth/Items business
data. It consumes health, release-identity, and selected read-only endpoints;
production verification never uses a business mutation path. Existing API
governance remains [`docs/API_DESIGN.md`](../../docs/API_DESIGN.md).
