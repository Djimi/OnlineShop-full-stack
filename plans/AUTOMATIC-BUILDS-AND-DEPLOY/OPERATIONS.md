# CI/CD Lifecycle and Operations Specification

**Status:** Draft — deferred implementation choices resolved (SPEC §4.5); Ready pending VR-READY-01 evidence  
**Parent architecture:** [`SPEC.md`](./SPEC.md)  
**Normative owner:** lifecycle state machines, procedures, failure behavior, retention, migration, recovery, rollback, and cutover (`OP-*`)

This document owns state transitions and sequence. [`CONTRACTS.md`](./CONTRACTS.md)
owns record fields and observation authority; [`SPEC.md`](./SPEC.md) owns
architecture and rationale.

---

## Contents

1. [Global operation rules](#1-global-operation-rules-op-gen)
2. [Validation and candidate publication](#2-validation-and-candidate-publication-op-cand)
3. [Candidate selection](#3-candidate-selection-op-sel)
4. [Shared staging state machine](#4-shared-staging-state-machine-op-stg)
5. [Production promotion state machine](#5-production-promotion-state-machine-op-pro)
6. [Production deployment and verification](#6-production-deployment-and-verification-op-dep)
7. [Official finalization](#7-official-finalization-op-fin)
8. [Failure, recovery, and rollback](#8-failure-recovery-and-rollback-op-rec)
9. [Retention](#9-retention-op-ret)
10. [Database migration boundary](#10-database-migration-boundary-op-db)
11. [Legacy cutover](#11-legacy-cutover-op-cut)

## 1. Global operation rules (OP-GEN)

### OP-GEN-01 — Locks, approval, and fresh state

```text
staging:   acquire shared queue -> revalidate state -> mutate -> verify cleanup
production: approve -> acquire shared queue -> repeat full preflight
            -> mutate -> verify -> finalize or recover
```

Both concurrency groups are queued and non-canceling; new requests do not
replace active work, and queue order is not guaranteed. Approval does not
reserve production. Only post-lock preflight authorizes production mutation.
[CT-STG-02](./CONTRACTS.md#ct-stg-02-visibility-not-a-second-lock) keeps the
staging record informational rather than a competing lock.

### OP-GEN-02 — Idempotency and ambiguous outcomes

- Candidate publication creates one complete record, exactly reuses identical
  state, or fails closed.
- Retry only an intrinsically idempotent mutation or one freshly observed not
  to have occurred; ambiguous responses require observation before retry.
- Exact matching partial finalization may resume; mismatch never does.
- Failed reads remain errors under
  [CT-GEN-03](./CONTRACTS.md#ct-gen-03-validation-and-fail-closed-reads).

### OP-GEN-03 — Waits, retries, and rate limits

Waits identify their target and are bounded; deployment waits bind to the
deployment started by the operation. Transient reads may use bounded backoff.
Blind mutation retries, generic-service-stability proof, and long blocking poll
loops are prohibited. Exhausted GitHub/AWS throttling fails explicitly. Exact
timeouts are measured environment configuration.

### OP-GEN-04 — Mutation verification and secret safety

Every AWS create/put/delete is followed immediately by a checked
describe/get/list; command success or empty output is insufficient. Secrets
remain secret references and never enter task environment/commands, manifests,
evidence, or logs.

Local AWS work performs identity preflight and includes
`--profile dpm-profile --region eu-north-1` on every command. Actions uses
job-scoped OIDC, never that local profile or long-lived credentials.

### OP-GEN-05 — Operational observability

Operations emit numbered top-down phases, UTC timestamps, and
[CT-AUDIT-01](./CONTRACTS.md#ct-audit-01-common-workflow-evidence). Capture
diagnostics before destructive staging cleanup. Failure output states whether
mutation began and whether cleanup/recovery succeeded.

## 2. Validation and candidate publication (OP-CAND)

### OP-CAND-01 — Event paths

Tests follow [`docs/TESTING_STRATEGY.md`](../../docs/TESTING_STRATEGY.md);
Maven runs through each service's wrapper from its root.

```text
feature/* push -> service/frontend tests -> local Compose E2E
               -> build all four -> publish complete staging-only candidate

pull request   -> same required validation -> no AWS credentials/publication

protected main push -> trusted validation -> rebuild all four
                    -> publish complete production-eligible candidate
```

An internal feature push may publish a SHA also tested by a PR. Deduplication
must preserve required PR checks. Local/PR results never confer production
eligibility.

### OP-CAND-02 — Publication transaction

Publication reaches `complete` only after, in order:

1. required tests and all four builds pass for one SHA/run;
2. three backend images publish and ECR digests are read back;
3. frontend publishes and Actions artifact digest plus content checksum are recorded;
4. [CT-CAND-01](./CONTRACTS.md#ct-cand-01-minimum-candidate-manifest) validates;
5. manifest, frontend, and evidence attach to the exact run/attempt.

Earlier failure leaves no deployable candidate. Partial output is diagnostic
only. Races/reruns reuse only exact identity under
[CT-GEN-04](./CONTRACTS.md#ct-gen-04-exact-match-resume).

### OP-CAND-03 — Terminal states

| Event | Terminal state |
|---|---|
| all gates/publication pass | complete candidate |
| validation/build fails | no complete candidate |
| publication interrupts | unusable partial state |
| exact rerun | exact reuse or one complete record |
| identity mismatch | failed closed |
| candidate artifact expires | expired; rebuild through supported push |

Remaining ECR images never bypass [AD-05](./SPEC.md#ad-05-the-exact-workflow-runattempt-is-candidate-authority).

## 3. Candidate selection (OP-SEL)

### OP-SEL-01 — Feature staging selection

The owner selects an exact complete, unexpired feature run/attempt. Revalidate
its contract and artifact existence before mutation. A retained
`superseded` candidate may stage for debugging only with the
[CT-CAND-05](./CONTRACTS.md#ct-cand-05-candidate-visibility-state) warning.
There is no production transition.

### OP-SEL-02 — Main promotion selection

Select an exact complete, unexpired `main` candidate. A newer candidate does
not invalidate it if [AD-11](./SPEC.md#ad-11-permit-an-older-selected-main-candidate-only-with-proof-and-warning)
passes. The selected run/attempt and manifest remain fixed through staging,
approval, preflight, deployment, and finalization.

## 4. Shared staging state machine (OP-STG)

### OP-STG-01 — Normal state machine

Feature preview and main promotion share this lifecycle:

```text
QUEUED -> OWNED/revalidate -> STARTING -> RESETTING/seed/verify
       -> DEPLOYING exact candidate -> COMPATIBILITY -> E2E
       -> EVIDENCE -> STOPPING -> CLEANUP_VERIFY -> COMPLETE/release
```

Every phase and expected/observed identity is recorded by
[CT-STG-01](./CONTRACTS.md#ct-stg-01-operation-record).
`STARTING` starts RDS but holds every ECS service at desired zero. `DEPLOYING`
registers the selected digest-pinned revision before starting each service, so
a stale task definition, including one carrying a stale database host, can
never start before reset and candidate registration.

### OP-STG-02 — Database reset and verification

Without touching production, remove prior test state, then verify Auth/Items
schemas, grants/least-privilege roles, deterministic seed counts, restricted
application-user connectivity, and staging-only environment/secret identity.
Exit zero without read-back is not success.

### OP-STG-03 — Exact deployment and compatibility

Before staging mutation, prove all candidate artifacts exist and match; deploy
no partial set. If a previous official release exists, run the
[AD-15](./SPEC.md#ad-15-block-schema-changes-until-migration-recovery-is-ready)
read-only previous-official-frontend journey — the previous official frontend
against the candidate backends — then the candidate-frontend cloud E2E.
Otherwise record the bootstrap exception.

### OP-STG-04 — Failure and cleanup

Every start/reset/deploy/compatibility/E2E failure joins the normal evidence
and cleanup path:

```text
failure -> capture diagnostics -> stop services/RDS -> verify stopped
        -> cleanup passes: fail cleanly and release ownership
        -> cleanup fails: visible incident; promotion remains blocked
```

Success also owns cleanup. E2E success plus failed cleanup is not a successful
staging gate.

### OP-STG-05 — Cost reconciliation

Feature publication never starts staging. A scheduled GitHub Actions job every
15 minutes, using the staging role, must detect running staging RDS without an
active staging owner, stop it, verify the stopped state, and surface the event.
Read errors fail visibly and are never treated as absence; production resources
are never touched.

## 5. Production promotion state machine (OP-PRO)

### OP-PRO-01 — End-to-end promotion

```text
SELECT exact main candidate -> read-only preflight -> OP-STG exact gate
 -> require E2E + cleanup -> owner approval showing exact SHA
 -> production lock -> repeat full preflight -> CT-AUDIT snapshot
 -> OP-DEP -> OP-FIN -> OP-RET audit -> SUCCESS
```

No production transition builds. The candidate selected before staging is the
one deployed.

### OP-PRO-02 — Preflight before approval and after lock

Run preflight before approval for information and after approval/lock for sole
mutation authorization. Both validate candidate authority/eligibility
([AD-03](./SPEC.md#ad-03-branch-class-determines-eligibility),
[AD-11](./SPEC.md#ad-11-permit-an-older-selected-main-candidate-only-with-proof-and-warning),
[CT-CAND](./CONTRACTS.md#3-candidate-contract-ct-cand)); exact successful
staging including cleanup ([AD-09](./SPEC.md#ad-09-serialize-staging-and-make-it-a-mandatory-production-gate),
[CT-STG](./CONTRACTS.md#5-staging-state-contract-ct-stg)); artifact existence;
current production consistency; and the [OP-DB](#10-database-migration-boundary-op-db)
gate. Approval identifies the exact candidate and any newer-candidate warning.

Any changed result after approval aborts before mutation and requires a new
operator decision.

### OP-PRO-03 — Production snapshot

Before mutation, capture and internally validate the production snapshot
defined by [CT-AUDIT-01](./CONTRACTS.md#ct-audit-01-common-workflow-evidence).
It is recovery input and operation evidence, not a new official release.

## 6. Production deployment and verification (OP-DEP)

### OP-DEP-01 — Ordered stages

```text
1 approved additive migration, only after OP-DB readiness
2 Auth + Items (parallel only when independent)
3 API Gateway
4 immutable frontend publication + live switch
5 read-only verification
6 OP-FIN
```

Gateway waits for both backends; frontend changes last so the previous frontend
operates against backward-compatible new backends during rollout.

### OP-DEP-02 — Backend deployment proof

For each backend: register/select the exact digest-pinned revision without
unrelated task changes or secret exposure; update and record this operation's
deployment; run a bounded deployment-specific waiter; require healthy
completion; observe running-task digests; compare with the candidate. Stale or
rolled-back deployments and task-definition-only identity fail.

### OP-DEP-03 — Frontend publication and switch

Publish under immutable production identity and verify checksum before changing
the live entry point. Then observe the candidate marker/content through
CloudFront. Before OP-FIN, the marker names the candidate—not an unfinalized
`release-NNNN`.

### OP-DEP-04 — Read-only production verification

Collect and validate [CT-PROD](./CONTRACTS.md#6-production-observed-state-contract-ct-prod)
for all three backends, frontend, marker/CloudFront, and selected read-only
journeys. Any mismatch or critical failure enters [OP-REC](#8-failure-recovery-and-rollback-op-rec).
Business-data mutation is prohibited; mutating E2E runs only in staging.

## 7. Official finalization (OP-FIN)

### OP-FIN-01 — Commit sequence

After OP-DEP succeeds and under the same production lock:

1. allocate the next never-reused `release-NNNN`;
2. add backend retention/operator tags without using them for deployment;
3. protect the immutable frontend in the rollback window;
4. prepare the exact [CT-REL](./CONTRACTS.md#4-official-release-contract-ct-rel)
   manifest/evidence without publishing an official release;
5. replace the candidate marker with an identity-equivalent official marker and verify publicly;
6. publish the prepared GitHub Release as the official commit point,
   attaching the pinned SBOM assets;
7. audit current plus three previous complete releases.

Report production success only after finalization read-backs.

### OP-FIN-02 — Partial finalization

Resume only when every existing tag, frontend identity, marker, release object,
and manifest component exactly matches the intended release. Otherwise the
deployment is not official and [OP-REC-02](#op-rec-02-automatic-recovery)
restores the previous official application release.

## 8. Failure, recovery, and rollback (OP-REC)

### OP-REC-01 — Failure classification

| Failure | Transition |
|---|---|
| validation/build | no complete candidate |
| partial publication | unusable; exact-identical resume only |
| candidate missing/expired | reject; rebuild |
| staging phase | diagnose, cleanup/verify, no approval |
| staging cleanup | block promotion; visible recovery state |
| pre-production drift | abort before mutation; new decision |
| clear deployment/health/frontend failure | restore/verify previous official application |
| ambiguous production symptom | stop, preserve evidence, manual decision |
| finalization | exact resume or restore previous official application |
| automatic recovery | report original + recovery failure; never success |
| rollback | restore/verify pre-rollback application snapshot where possible |
| GitHub/AWS read | fail as read error, never absence |

### OP-REC-02 — Automatic recovery

```text
defined post-mutation failure -> stop forward progress
 -> validate pre-mutation snapshot -> restore complete backends safely
 -> restore frontend/marker -> repeat OP-DEP-04 -> record both outcomes
```

Never reverse schema/data. Ambiguous symptoms or inconsistent recovery inputs
stop rather than guess. Recovery failure precludes success.

### OP-REC-03 — Manual rollback preflight

Input is one non-current official `releaseId` in the advertised
rollback-capable window; reject arbitrary tags, digests, SHAs, URLs, or
hand-written manifests. Before approval and again after lock, validate
[CT-REL](./CONTRACTS.md#4-official-release-contract-ct-rel), retained matching
bytes, and current compatibility — including the configuration-fingerprint
check under [OP-RET](#9-retention-op-ret) — plus internally consistent live
state/snapshot.

### OP-REC-04 — Manual rollback state machine

```text
select official target -> read-only preflight -> owner approval
 -> production lock -> repeat preflight -> snapshot current production
 -> deploy complete target -> OP-DEP-04 -> separate rollback result
```

Rollback creates no official release, edits no historical manifest, and changes
no database schema/data. Failure restores and verifies the pre-rollback
application snapshot where possible.

## 9. Retention (OP-RET)

### OP-RET-01 — Immediate rollback window

```text
release-N   current       protected
release-N-1 target 1      protected
release-N-2 target 2      protected
release-N-3 target 3      protected
older       historical; AWS bytes may expire
```

ECR and production S3 retain these four complete sets. Audit each against its
official manifest before and after retention mutation. Incomplete, mismatched,
incompatible, unknown, or read-error state is never guessed deletable. A target
whose recorded configuration fingerprint
([AD-16](./SPEC.md#ad-16-keep-a-four-release-complete-rollback-window)) no
longer matches current runtime/task-definition, schema, or network
configuration is incompatible and not rollback-capable.

### OP-RET-02 — Retention classes

- Official GitHub Releases, manifests, and SBOMs persist unless the owner
  deletes them.
- Official ECR tags anchor retention; runtime still uses digests.
- Immutable frontend identities follow the same complete-release window.
- Candidate retention is independent and 30 days.
- Compact official evidence follows the official record; bounded diagnostics
  may use shorter implementation-defined retention.

Historical releases whose AWS bytes expire are no longer advertised as
rollback-capable.

### OP-RET-03 — Safe mutation

Retention mutation requires an exact preview proving current plus three remain,
then post-mutation read-back. Read error, ambiguity, protected match, or
mismatch stops deletion.

## 10. Database migration boundary (OP-DB)

### OP-DB-01 — Readiness gate

Production schema change fails preflight until the
[AD-15](./SPEC.md#ad-15-block-schema-changes-until-migration-recovery-is-ready)
prerequisites exist. After readiness, admit only additive changes through
OP-STG compatibility; destructive changes remain blocked.

### OP-DB-02 — Recovery boundary

Promotion, automatic recovery, and rollback never reverse production schema or
data. Approval displays this limitation whenever an allowed additive migration
exists.

## 11. Legacy cutover (OP-CUT)

### OP-CUT-01 — Isolation from legacy implementation

Build beside the old path. Verified identifiers, read-back lessons, and known
AWS failures may inform implementation, but the replacement never imports,
sources, or executes the old framework. Old and new never mutate production
concurrently.

### OP-CUT-02 — Cutover sequence

```text
prove validation/candidates -> prove live staging -> approved production
 -> rollback N to N-1 -> restore N -> disable legacy -> observe recoverably
 -> owner approves deletion inventory -> delete legacy
```

Legacy remains recoverable until the drill passes. Disable before deletion;
deletion is a separate owner-approved action.
