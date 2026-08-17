# CI/CD and Release Architecture Specification

**Status:** Draft — deferred implementation choices resolved (SPEC §4.5); Ready pending VR-READY-01 evidence  
**Date:** 2026-08-14  
**Audience:** repository owner, delivery-system implementers, reviewers, and operators  
**Decision authority:** repository owner

This is the readable entry point and architectural authority for the target
OnlineShop delivery system. Observable records, lifecycle procedures, and
acceptance evidence are authoritative in the linked companion documents.

The target supersedes conflicting recommendations in
`GREENFIELD-SIMPLIFICATION-PLAN.md`, which remains historical input. Existing
workflows, scripts, AWS resources, and operational notes describe current state
until cutover; they do not override this target.

---

## Contents

1. [How to use this package](#1-how-to-use-this-package)
2. [Executive summary](#2-executive-summary)
3. [Customer problem and target experience](#3-customer-problem-and-target-experience)
4. [Scope, constraints, assumptions, and open items](#4-scope-constraints-assumptions-and-open-items)
5. [System context and architectural flow](#5-system-context-and-architectural-flow)
6. [Authoritative architecture decisions](#6-authoritative-architecture-decisions)
7. [Ownership and trust boundaries](#7-ownership-and-trust-boundaries)
8. [Quality attributes and limitations](#8-quality-attributes-and-limitations)
9. [Alternatives, risks, and readiness](#9-alternatives-risks-and-readiness)

## 1. How to use this package

### 1.1 Package navigation and normative ownership

Each rule has one authoritative home; other documents link to it.

| Document | Authoritative for | Question answered |
|---|---|---|
| **`SPEC.md`** | scope, context, decisions (`AD-*`), trust boundaries, rationale, risk | Why this architecture? |
| [`CONTRACTS.md`](./CONTRACTS.md) | observable records, identities, and authorities (`CT-*`) | What must be recorded or observed? |
| [`OPERATIONS.md`](./OPERATIONS.md) | sequencing, state transitions, failures, recovery, retention, cutover (`OP-*`) | What happens and in what order? |
| [`VERIFICATION.md`](./VERIFICATION.md) | scenarios, evidence, and readiness gates (`VR-*`) | How is it proven? |

**Must**, **must not**, **required**, and **prohibited** are binding. **Should**
requires an explicit reviewed exception. If documents appear to conflict, the
topic owner above controls; fix the owner rule and every reference rather than
choosing convenient wording.

### 1.2 Suggested reading paths

New readers start with this document and the staging/promotion diagrams in
`OPERATIONS.md`. Implementers follow the relevant `AD -> CT -> OP -> VR`
references. Reviewers and operators start from the selected candidate or
release contract and follow its operation and evidence links.

## 2. Executive summary

OnlineShop needs a release system that one learning operator can understand,
inspect, and recover without maintaining a bespoke release platform:

```text
source -> complete immutable candidate -> exact-candidate staging
       -> owner-approved production -> observed deployment
       -> official release -> complete-release rollback
```

One release identity covers Auth, Items, API Gateway, and frontend. Successful
`feature/*` and protected-`main` pushes publish complete candidates; feature
candidates are staging-only, while only protected-`main` candidates can enter
production.

ECR owns backend bytes. The exact Actions run/attempt owns candidate metadata,
frontend bytes, and evidence. After production verification, a GitHub Release
becomes the official human-readable record and points to exact ECR digests and
an immutable production-S3 frontend identity.

One isolated staging environment uses a persistent but normally stopped RDS.
Production and rollback require owner approval. The current release plus at
least three previous complete releases remain rollback-capable; application
rollback never reverses production data or schema.

## 3. Customer problem and target experience

### 3.1 Customer and operator

The repository owner is developer, release approver, and production operator.
Future contributors and reviewers must understand the release contract without
reverse-engineering workflow YAML or scripts.

### 3.2 Problem

Current behavior is spread across workflows, scripts, validators, fixtures, and
state models. The replacement must answer directly:

1. What source and immutable artifacts did this build produce?
2. Which branch produced them, and how do two builds relate?
3. Which exact candidate is staged or running?
4. What evidence authorized production?
5. Which complete prior releases can be restored?
6. What happens on staging, deployment, finalization, or recovery failure?

### 3.3 Target experience

From one workflow run, the operator can identify branch, full SHA, build order,
test results, backend digests, and frontend checksum, then stage that exact
candidate. Promotion deploys the same bytes without rebuilding.

After successful production verification, a sequential GitHub Release such as
`release-0007` identifies the source, approval, exact artifacts, previous
release, and verification evidence. Measurable outcomes are in
[`VERIFICATION.md`](./VERIFICATION.md).

## 4. Scope, constraints, assumptions, and open items

### 4.1 In scope

- Validation on `feature/*`, pull-request, and `main` events, including local
  and cloud E2E gates.
- Complete feature and `main` candidates with branch/history visibility.
- One serialized, isolated, normally stopped staging environment and manual
  feature previews.
- Manual protected production promotion of one atomic application release.
- GitHub official releases; ECR/S3 retention for current plus three prior
  complete releases; approved complete-release rollback.
- Minimal adjacent-release compatibility, three AWS permission boundaries, and
  greenfield cutover with a live rollback/restoration drill.

### 4.2 Non-goals for the first iteration

- Independent service versions, selective builds, component reuse, or direct
  feature-to-production promotion.
- Per-branch or always-on staging, recreate-RDS-per-promotion, or a custom lock.
- Continuous production deployment, database rollback, destructive schema
  migration, or high-availability/zero-downtime claims.
- A custom release database, traceability product, retention simulator, or
  infrastructure-as-code migration.
- Application API/ownership redesign or prescribing the delivery engine's
  implementation language and internal structure (decided by the repository
  owner, see [§4.5](#45-resolved-implementation-choices)).

### 4.3 Hard constraints

- Current deployment context is AWS account `799111666795`, region
  `eu-north-1`; [OP-GEN-04](./OPERATIONS.md#op-gen-04-mutation-verification-and-secret-safety)
  owns identity, read-back, and secret-handling procedure.
- The deployables are Auth, Items (including `common` from the same source),
  API Gateway, and frontend.
- [Production/staging isolation](#72-environment-isolation) is mandatory, and
  production data is not disposable.
- Repository `AGENTS.md` and
  [`docs/CI_CD_GOTCHAS.md`](../../docs/CI_CD_GOTCHAS.md) remain binding.

### 4.4 Assumptions to verify before cutover

- GitHub supports the chosen artifact retention and protected production
  Environment approval; candidate artifacts expose stable run/attempt,
  branch/SHA, time, and digest identity.
- ECR can enforce required candidate/official immutability; production and
  staging services meet health and rolling-deployment contracts.
- No versioned production migration owner or tested backup/restore currently
  exists, so schema changes remain blocked.
- Staging RDS stop/start preserves required configuration.
- Production S3/CloudFront supports private immutable frontend objects and an
  observable release marker.
- Protected `main` disallows force-push.

### 4.5 Resolved implementation choices

These items were the Draft-blocking deferred choices; the repository owner has
decided each. One line states the decision; the linked rule is its
authoritative home.

1. **Delivery engine:** Python + Boto3, packaged as a new `delivery/` package
   at the repository root. This supersedes the §4.2 non-goal wording; the
   decision belongs to the repository owner in this section.
2. **Candidate metadata retention:** 30 days —
   [OP-RET-02](./OPERATIONS.md#op-ret-02-retention-classes).
3. **Production smoke tests:** backend health, frontend marker/content observed
   through CloudFront, and read-only `GET /items` through the gateway —
   [CT-PROD-03](./CONTRACTS.md#ct-prod-03-read-only-application-verification).
4. **Staging compatibility:** previous official frontend against candidate
   backends, then candidate-frontend cloud E2E; bootstrap exception when no
   prior official release exists —
   [AD-15](#ad-15-block-schema-changes-until-migration-recovery-is-ready).
5. **SBOMs:** pinned Syft-generated SPDX JSON, permanently attached to official
   GitHub Releases with SHA-256 hashes in the release manifest —
   [CT-REL-01](./CONTRACTS.md#ct-rel-01-minimum-release-manifest).
6. **Runtime compatibility:** record a sanitized configuration fingerprint per
   release; runtime/task-definition, schema, or network changes invalidate
   incompatible rollback targets —
   [AD-16](#ad-16-keep-a-four-release-complete-rollback-window).
7. **Ownerless staging RDS reconciliation:** scheduled GitHub Actions job every
   15 minutes using the staging role —
   [OP-STG-05](./OPERATIONS.md#op-stg-05-cost-reconciliation).

These decisions do not alter the release model. Each rule has one
authoritative home; other documents link to it.

## 5. System context and architectural flow

### 5.1 C4 Level 1 — system context

```text
[Repository owner]
        | pushes, selects, approves, rolls back
        v
(OnlineShop Delivery System in GitHub)
 source | validation | candidates | approvals | official releases
        |
        | OIDC + immutable identities
        v
(OnlineShop AWS Platform)
 ECR | isolated staging | production ECS/RDS | S3/CloudFront
        |
        v
 [Application user]
```

The owner alone expresses production intent. GitHub executes that intent; AWS
stores and runs selected bytes. Neither a push nor AWS state chooses an
official release.

### 5.2 C4 Level 2 — delivery containers

```text
Source -> Validation/Build -> Actions candidate record
                         \-> ECR backend digests

Actions candidate record + ECR
          -> Staging control -> isolated shared staging
          -> Production control --approval--> production ECS/S3/CloudFront
                                            -> official GitHub Release
```

GitHub holds source, orchestration, approvals, candidate evidence, and official
metadata. AWS holds deployable bytes and observed environment state.

### 5.3 End-to-end architectural journey

This orientation is informative; `OPERATIONS.md` owns ordering and failures.

```text
feature push -> complete staging-only candidate --manual--> shared staging
PR           -> required validation only; no AWS publication from PR event
main push    -> complete production-eligible candidate
                    -> exact shared-staging gate
                    -> production approval
                    -> ordered observed deployment
                    -> read-only verification
                    -> official release
                    -> retained complete-set rollback
```

## 6. Authoritative architecture decisions

This section alone owns architectural choices. Companion documents own their
observable, operational, and evidentiary realization.

### AD-01 — Optimize for learning and operational simplicity

The first iteration **must** use one understandable release model and ordinary
GitHub/AWS primitives. A custom release database, selective-build resolver,
retention simulator, and distributed lock are prohibited initially. Redundant
build work is accepted to simplify provenance and recovery reasoning.

### AD-02 — Publish one complete candidate for every eligible push

Every successful `feature/*` and protected-`main` push **must** publish Auth,
Items, API Gateway, and frontend from one SHA/run; Items includes `common`
from that SHA. Partial/selective candidates and component reuse are prohibited.
PR events validate without AWS publication; an internal feature push may
independently publish the same SHA.

### AD-03 — Branch class determines eligibility

Feature candidates are staging-only. Only a trusted protected-`main` rebuild
is production-eligible; a feature candidate cannot be relabeled after merge.
Fork pull requests never publish candidates.

### AD-04 — Release the application as one atomic, build-once unit

One candidate/release covers all four deployables and is one approval,
intended final state, and rollback unit. Runtime replacement may still roll
through adjacent versions. Promotion/rollback consume recorded immutable bytes:
no rebuild, mutable-tag identity, hand-written image URI, or missing-component
substitution.

### AD-05 — The exact workflow run/attempt is candidate authority

The exact Actions run/attempt and immutable candidate artifact are pre-release
authority; branch names and ECR tags alone are insufficient. An expired
candidate cannot deploy even if images remain and must be rebuilt. Shape and
visibility are owned by [CT-CAND](./CONTRACTS.md#3-candidate-contract-ct-cand).

### AD-06 — A verified GitHub Release is official authority

Only a post-verification GitHub Release plus its exact
`release-manifest.json` is official. It points to immutable ECR/S3 identities.
The record remains historical after referenced bytes leave the rollback
window; current rollback capability still requires matching retained bytes.
[CT-REL](./CONTRACTS.md#4-official-release-contract-ct-rel) owns its shape.

### AD-07 — Allocate sequential release identity after verification

Official IDs are monotonic `release-NNNN`, allocated under the production
lock after verification, never reused, and official only after finalization.
Exact partial finalization may resume; otherwise the deployment is not official
and the previous official application release is restored.

### AD-08 — Use one isolated, normally stopped staging environment

One staging environment is isolated from production. RDS persists but normally
stops; each use starts, resets/seeds/verifies data, deploys one selected
candidate, captures evidence, and stops/verifies the environment. Feature
staging is explicit, never push-triggered. Per-branch, always-on, and
recreate-per-run variants are deferred.

### AD-09 — Serialize staging and make it a mandatory production gate

All staging mutation uses one queued, non-canceling repository concurrency
group; at most one workflow owns staging, queue order is not guaranteed, and
fresh state is revalidated after acquisition. No second lock is introduced.
Every promoted candidate must pass exact-candidate cloud E2E and verified
cleanup; cleanup failure blocks promotion. [OP-STG](./OPERATIONS.md#4-shared-staging-state-machine-op-stg)
owns transitions.

### AD-10 — Require explicit approval and serialized production mutation

Promotion and rollback require protected-Environment owner approval; push/merge
never implies intent. All production application mutation shares one queued,
non-canceling concurrency group. After approval and lock acquisition, fresh
preflight must pass before mutation.

### AD-11 — Permit an older selected `main` candidate only with proof and warning

When a newer candidate exists, the selected one remains promotable only if it
is reachable from current protected `main`, not older than production, and
passes every gate. Approval shows its exact SHA and a newer-candidate warning.

### AD-12 — Deploy backends before frontend and verify observed bytes read-only

Deploy Auth/Items, then Gateway, then frontend; Auth/Items may run concurrently
when independent. Frontend changes last to preserve adjacent-version
compatibility. Verification is read-only and observes actual running digests
and frontend checksum/marker; task-definition text or exit zero is insufficient.
Mutating E2E is staging-only. [OP-PRO](./OPERATIONS.md#5-production-promotion-state-machine-op-pro)
owns sequence.

### AD-13 — Recover automatically only from defined, unambiguous failures

Capture the current official complete release before mutation. Defined
deployment, health, frontend, or finalization failures restore and verify it.
Ambiguous symptoms stop with evidence for manual decision. Recovery failure
reports both failures and never reports success.

### AD-14 — Roll back complete application artifacts, never production data

Rollback accepts only a complete retained official release, protected approval,
and the production lock. It changes the whole application set, never accepts
arbitrary identifiers or hand-written manifests, never edits official history,
and never reverses database schema or data.

### AD-15 — Block schema changes until migration recovery is ready

All production schema changes remain blocked until versioned migration
ownership, tested backup/restore, and compatibility rules exist. Then only
additive changes enter this initial gate; destructive changes remain prohibited.
Candidate backends must pass one read-only previous-official-frontend staging
journey — the previous official frontend served against the candidate backends
— before the candidate-frontend cloud E2E, or record a bootstrap exception when
no prior official release exists. Broader compatibility matrices are deferred.

### AD-16 — Keep a four-release complete rollback window

Retain and audit the current plus at least three previous complete releases.
Official GitHub records remain unless explicitly deleted; older AWS bytes may
expire. Official ECR tags are retention/operator anchors, never deployment
inputs. Rollback capability requires matching artifacts compatible with current
database/runtime configuration; each release records a sanitized configuration
fingerprint, and runtime/task-definition, schema, or network changes invalidate
incompatible rollback targets. Candidate retention is separate and 30 days.
[OP-RET](./OPERATIONS.md#9-retention-op-ret) owns policy.

### AD-17 — Separate three AWS trust boundaries

1. **Artifact Publisher:** eligible branch-push jobs publish/read back declared
   candidate ECR repositories only.
2. **Staging Deployer:** manual staging jobs read candidates and mutate declared
   staging resources only.
3. **Production Deployer:** protected production jobs perform declared
   promotion, recovery, and rollback operations only.

Permissions are job-scoped. Validation/fork-PR jobs have no AWS OIDC,
long-lived AWS credentials are absent, and production pass-role is restricted
to exact ECS roles and purpose.

### AD-18 — Cut over beside the legacy path, then retire it after a live drill

Build the replacement beside, but never import/source/execute, the old release
framework. Old and new never mutate production concurrently. Disable before
deletion and keep the legacy path recoverable until a live promotion and
`N -> N-1 -> N` drill pass. [OP-CUT](./OPERATIONS.md#11-legacy-cutover-op-cut)
owns order.

## 7. Ownership and trust boundaries

### 7.1 Data ownership

| Data/artifact | Authority |
|---|---|
| Source/history | GitHub repository |
| Candidate manifest/frontend/evidence | exact Actions run/attempt |
| Backend bytes | ECR digest |
| Staging state | environment plus operation evidence |
| Production frontend/marker | S3 identity/checksum observed through CloudFront |
| Official release | GitHub Release manifest |
| Running backend identity | ECS running-task digests |
| Production business data | production RDS; never reversed by releases |

[`CONTRACTS.md`](./CONTRACTS.md) owns field and observation rules.

### 7.2 Environment isolation

Production and staging do not share VPC, RDS, secrets, service namespace,
services, or environment security groups. Shared source/artifact stores do not
transfer mutation authority. Delivery introduces no domain API/event and owns
no Auth or Items business data.

## 8. Quality attributes and limitations

### 8.1 Understandability

One candidate/release model covers all deployables; workflows follow the
operation diagrams; failures expose phase and expected/observed identity; and
evidence is discoverable without a custom traceability product.

### 8.2 Reliability and fail-closed posture

Unverifiable state is never success. [CT-GEN-03](./CONTRACTS.md#ct-gen-03-validation-and-fail-closed-reads)
and [CT-GEN-04](./CONTRACTS.md#ct-gen-04-exact-match-resume) own validation and
resume semantics; [OP-GEN](./OPERATIONS.md#1-global-operation-rules-op-gen) and
[OP-REC](./OPERATIONS.md#8-failure-recovery-and-rollback-op-rec) own execution
and recovery.

### 8.3 Security

[AD-17](#ad-17-separate-three-aws-trust-boundaries) and
[Section 7.2](#72-environment-isolation) own trust boundaries;
[CT-GEN-02](./CONTRACTS.md#ct-gen-02-no-executable-or-secret-bearing-inputs)
owns safe record content; [OP-GEN-04](./OPERATIONS.md#op-gen-04-mutation-verification-and-secret-safety)
owns operational secret handling; [VR-SEC](./VERIFICATION.md#4-security-acceptance-vr-sec)
owns proof.

### 8.4 Cost and availability posture

Staging normally stops and feature publication does not start it automatically
([AD-08](#ad-08-use-one-isolated-normally-stopped-staging-environment),
[OP-STG-04](./OPERATIONS.md#op-stg-04-failure-and-cleanup),
[OP-STG-05](./OPERATIONS.md#op-stg-05-cost-reconciliation)).
Desired-count-one Fargate Spot may remain in production; this provides neither
high availability nor zero downtime. Safe rolling protects deployment, not
Spot reclaim or single-task interruption.

## 9. Alternatives, risks, and readiness

### 9.1 Rejected alternatives and reversal evidence

| Alternative | Rejected now because | Reconsider when |
|---|---|---|
| Always-on staging | idle RDS cost | deployment frequency justifies it |
| Recreate RDS per run | slower, more lifecycle complexity | reset proves unreliable |
| Per-feature environments | cost/cleanup sprawl | measured queue demand requires them |
| Direct feature production | weak main trust boundary | governance changes |
| Selective builds | provenance complexity | measured cost becomes material |
| Independent releases | compatibility/operator complexity | teams/cadence separate |
| Durable candidate database | duplicate authority | Actions retention/discovery fails |
| Every candidate as Release | pollutes official catalog | GitHub adds a suitable candidate type |
| Custom distributed lock | unnecessary for one GitHub mutator | out-of-band mutators require leases |
| Mutating production E2E | data risk | isolated synthetic tenancy exists |
| SemVer initially | promotion lacks product meaning | public compatibility promises require it |

### 9.2 Principal risks and guardrails

| Risk | Guardrail / reconsideration |
|---|---|
| Candidate expires | reject/rebuild; add durable storage if frequent |
| Publisher-role abuse | ECR-only least privilege and immutable identity |
| Ownerless restarted RDS costs money | reconcile/alert; redesign if unreliable |
| Shared staging queues grow | add environments only after measured demand |
| Minimal compatibility misses a defect | expand matrix after escaped defect |
| Official record outlives AWS bytes | audit protected complete window |
| Runtime changes invalidate rollback | mark incompatible; version configuration |
| GitHub unavailable during rollback | retain recent validated evidence; mirror if required |
| Spot/single-task interruption | accept posture or add on-demand/redundancy |
| Finalization partially fails | exact resume or restore previous official release |

### 9.3 Strongest challenge

Actions artifacts are bounded and GitHub-dependent. A durable immutable
candidate store would be more independent, but adds authority and operations.
Use it only if expired-candidate rebuilds, discovery, or artifact reliability
becomes a recurring failure.

### 9.4 Readiness status

The Section 4.5 implementation choices are resolved (see
[§4.5](#45-resolved-implementation-choices)). The package remains **Draft**
until Section 4 assumptions are verified and
[VR-READY](./VERIFICATION.md#9-readiness-gate-vr-ready) evidence is produced
or the remaining items are explicitly accepted.
