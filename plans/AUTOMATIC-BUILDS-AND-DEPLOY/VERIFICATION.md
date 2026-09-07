# CI/CD Architecture Verification Specification

**Status:** Draft — deferred implementation choices resolved (SPEC §4.5); Ready pending VR-READY-01 evidence  
**Parent architecture:** [`SPEC.md`](./SPEC.md)  
**Normative owner:** success measures, acceptance scenarios, evidence coverage, and readiness gates (`VR-*`)

This document states proof scenarios, not architecture, record, or procedure
requirements. Those remain authoritative in `AD-*`, `CT-*`, and `OP-*`.

---

## Contents

1. [Evidence model](#1-evidence-model-vr-gen)
2. [Outcome success measures](#2-outcome-success-measures-vr-out)
3. [Candidate acceptance](#3-candidate-acceptance-vr-cand)
4. [Security acceptance](#4-security-acceptance-vr-sec)
5. [Staging acceptance](#5-staging-acceptance-vr-stg)
6. [Promotion acceptance](#6-promotion-acceptance-vr-pro)
7. [Recovery and rollback acceptance](#7-recovery-and-rollback-acceptance-vr-rec)
8. [Operability acceptance](#8-operability-acceptance-vr-ops)
9. [Readiness gate](#9-readiness-gate-vr-ready)

## 1. Evidence model (VR-GEN)

### VR-GEN-01 — Evidence levels

| Level | Use |
|---|---|
| Document/static | ownership/links, schemas/policies, forbidden-path absence |
| Offline executable | validators, fixtures, hostile inputs, state/failure injection |
| Repository/platform inventory | GitHub settings, OIDC/IAM, ECR/S3/ECS configuration |
| Live lifecycle | staging, observed production identity, promotion, rollback/restoration |

Use the cheapest sufficient level, but never substitute simulation for a live
platform fact. Evidence records requirement IDs, level, run/time, conclusion,
and limitations; offline success is never described as live verification.

### VR-GEN-02 — Negative and failure-path evidence

Each mutation path includes invalid-input rejection and its material partial/
false-success failure transitions. Exercise cleanup, recovery, fail-closed
reads, and exact resume where required by CT-GEN and OP-REC.

### VR-GEN-03 — No destructive verification shortcut

Production scenarios follow AD-10, AD-12, OP-GEN, and OP-PRO/REC controls.
Application verification is read-only; mutating E2E runs in staging.

## 2. Outcome success measures (VR-OUT)

| ID | Capability | Requirements | Proof |
|---|---|---|---|
| VR-OUT-01 | complete feature/main candidates | AD-02, CT-CAND, OP-CAND | successful feature/main plus failed-component case |
| VR-OUT-02 | end-to-end candidate trace | AD-05, CT-CAND | manifest-to-backing-store identity check |
| VR-OUT-03 | build order distinct from ancestry | CT-CAND-04/05 | identical, ancestor, diverged, superseded, expired cases |
| VR-OUT-04 | fork PR has no AWS path | AD-03/17, OP-CAND-01 | permissions/static review and hostile fork case |
| VR-OUT-05 | feature cannot reach production | AD-03, OP-SEL/PRO/REC | rejected transition cases |
| VR-OUT-06 | exact staged bytes reach production | AD-04/09, OP-PRO | no-build check plus identity chain |
| VR-OUT-07 | owner-approved production/rollback | AD-10/14 | Environment inventory and live approval evidence |
| VR-OUT-08 | observed, read-only production proof | AD-12, CT-PROD, OP-DEP-04 | digest/marker/checksum/journey evidence |
| VR-OUT-09 | four complete rollback-capable releases | AD-16, OP-RET | complete-set compatibility audit |
| VR-OUT-10 | live `N -> N-1 -> N` before deletion | AD-18, OP-CUT | owner-approved live drill |
| VR-OUT-11 | explainable without implementation code | AD-01 | owner walkthrough of flows, gates, failures, recovery |

## 3. Candidate acceptance (VR-CAND)

### VR-CAND-01 — Complete feature and main records

**Scenario/evidence:** run one successful feature push and one protected
`main` push; validate each record and backing bytes against AD-02/03/05,
CT-CAND-01/03, and OP-CAND.

### VR-CAND-02 — Completeness and immutability failures

**Scenario/evidence:** exercise component failure, interrupted publication,
byte-identical rerun, identity-mismatched rerun, and missing/expired artifact;
assert CT-GEN-04 and OP-CAND-02/03 outcomes.

### VR-CAND-03 — Comparison and visibility

**Scenario/evidence:** use fixtures or branch history covering source-identical,
ancestor, diverged, reachable-old, rebased-away, and expired candidates; assert
CT-CAND-04/05 and OP-SEL-01.

## 4. Security acceptance (VR-SEC)

### VR-SEC-01 — Credential and mutation boundaries

**Scenario/evidence:** inspect workflow permissions, actual OIDC trust/IAM
attachments, and denied operations for fork/validation, Publisher, Staging, and
Production roles; assert AD-17 and Section 7.2. Include production pass-role
scope and protected-Environment reachability.

### VR-SEC-02 — Secret and untrusted-input safety

**Scenario/evidence:** inspect source, transforms, fixtures, manifests, and
captured logs; inject hostile quotes, whitespace, substitutions, separators,
redirection, and newlines through shell-facing GitHub inputs; assert
CT-GEN-02/03 and OP-GEN-04.

### VR-SEC-03 — Dependency integrity

**Scenario/evidence:** static gate rejects mutable references for every
release-critical third-party Action.

## 5. Staging acceptance (VR-STG)

### VR-STG-01 — Serialization and fresh ownership

**Scenario/evidence:** start/model two staging requests; assert AD-09,
OP-GEN-01, and CT-STG-02 for mutual exclusion, non-cancellation, and post-lock
revalidation. Do not assert queue order.

### VR-STG-02 — Observable lifecycle

**Scenario/evidence:** run one live OP-STG lifecycle and validate CT-STG-01,
including reset/access proof, exact artifacts, AD-15 compatibility/bootstrap,
cloud E2E, and cleanup.

### VR-STG-03 — Cleanup and cost control

**Scenario/evidence:** inject a staging-phase failure and then cleanup failure;
assert OP-STG-04 and CT-STG-02. Separately prove the
[OP-STG-05](./OPERATIONS.md#op-stg-05-cost-reconciliation) ownerless-RDS
reconciliation: the scheduled every-15-minutes job using the staging role
detects running staging RDS without an active staging owner, stops it, verifies
the stopped state, fails visibly on read errors (never assuming absence), and
never touches production resources.

## 6. Promotion acceptance (VR-PRO)

### VR-PRO-01 — Selection and gates

**Scenario/evidence:** cover feature rejection, valid older-`main` selection,
newer-candidate warning, exact staging evidence, approval/lock revalidation,
post-approval drift, and absence of build steps; assert AD-03/04/09/10/11 and
OP-PRO-01/02.

### VR-PRO-02 — Ordered observed deployment

**Scenario/evidence:** controlled deployment plus stale-service and
circuit-breaker rollback injections; assert AD-12, OP-DEP-01/02/03, and
CT-PROD-01/02 using actual running/public identities.

### VR-PRO-03 — Read-only verification and finalization

**Scenario/evidence:** verify CT-PROD/OP-DEP-04 read-only evidence, then
OP-FIN-01 ordering and sequential identity. Inject partial finalization and
assert OP-FIN-02/OP-REC transition.

## 7. Recovery and rollback acceptance (VR-REC)

### VR-REC-01 — Automatic recovery

**Scenario/evidence:** inject a defined post-mutation failure, recovery failure,
and ambiguous symptom; assert AD-13/14 and OP-REC-01/02, including separate
original/recovery outcomes and no database reversal.

### VR-REC-02 — Rollback input and completeness

**Scenario/evidence:** test one valid target and reject arbitrary identifier,
current/incomplete/mismatched/incompatible/expired targets before mutation;
assert AD-14, CT-REL, and OP-REC-03.

### VR-REC-03 — Live rollback/restoration drill

**Scenario/evidence:** before legacy deletion, run owner-approved
`N -> N-1 -> N`; validate OP-REC-04, OP-RET, immutable release history,
separate rollback results, and unchanged schema/data.

## 8. Operability acceptance (VR-OPS)

### VR-OPS-01 — Observed-state discipline

**Scenario/evidence:** map every AWS create/put/delete to its immediate checked
read-back; exercise true absence and read failure; assert CT-GEN-03,
CT-PROD-04, and OP-GEN-04.

### VR-OPS-02 — Bounded execution

**Scenario/evidence:** inspect/inject waits, throttling, stale deployment, and
ambiguous mutation response; assert OP-GEN-02/03.

### VR-OPS-03 — Evidence discoverability

**Scenario/evidence:** starting independently from a candidate run and an
official release, locate the CT-AUDIT source/artifact, staging, approval,
production, and recovery/rollback chain without a custom query product.

### VR-OPS-04 — Outcome traceability matrix

| Outcome | Architecture/contract | Procedure | Scenario |
|---|---|---|---|
| Understand build | AD-02/05, CT-CAND | OP-CAND | VR-CAND-01/03 |
| Preview feature | AD-03/08/09 | OP-SEL-01, OP-STG | VR-STG-02 |
| Promote exact bytes | AD-04/12, CT-PROD | OP-PRO/DEP | VR-PRO-01/02 |
| Control production intent | AD-10/17 | OP-GEN-01 | VR-SEC-01, VR-PRO-01 |
| Control staging cost | AD-08/09 | OP-STG-04/05 | VR-STG-01/03 |
| Recover deployment | AD-13 | OP-REC-01/02 | VR-REC-01 |
| Restore release | AD-14/16 | OP-REC-03/04, OP-RET | VR-REC-02/03 |
| Preserve data | AD-14/15 | OP-DB | VR-REC-01/03 |

## 9. Readiness gate (VR-READY)

### VR-READY-01 — Draft-to-Ready conditions

Ready requires evidence that:

1. [`SPEC.md` Section 4.4](./SPEC.md#44-assumptions-to-verify-before-cutover)
   is verified without unauthorized mutation;
2. [Section 4.5](./SPEC.md#45-resolved-implementation-choices) is decided or
   explicitly accepted with an owner;
3. candidate/release contract examples are independently implementable;
4. every VR scenario has an owner, evidence level, and execution stage; and
5. all four documents pass link, ID, ownership, and contradiction review.

### VR-READY-02 — Cutover completion is stronger than design readiness

Ready means implementable, not deletable. Legacy deletion additionally requires
all live evidence and owner approval in
[OP-CUT-02](./OPERATIONS.md#op-cut-02-cutover-sequence).
