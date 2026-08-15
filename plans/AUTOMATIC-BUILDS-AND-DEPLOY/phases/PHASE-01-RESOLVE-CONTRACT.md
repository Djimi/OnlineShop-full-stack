# Phase 1 — Resolve the Target Contract

**Plan:** `DELIVERY-SYSTEM-IMPLEMENTATION-PLAN.md` §1
**Status:** ✅ Complete (implemented, reviewed, all findings resolved)

## What this phase was about

The four normative specs (`SPEC.md`, `CONTRACTS.md`, `OPERATIONS.md`, `VERIFICATION.md`)
were stuck in **Draft** status because seven implementation choices were deliberately
left open ("deferred"). Before any code or workflow can be written, those choices must
be fixed — otherwise the whole design is ambiguous and nothing can be called "done".

```text
Before                                    After
─────────────────                        ─────────────────
SPEC   §4.5 "Deferred choices"   →        "Resolved implementation choices"
       (7 open items)                      (7 decided items, each with a rule to point to)
                                            |
                                            v
                                    Status: Draft → "decided; Ready pending evidence"
```

## The seven decisions (and where each now lives)

Each decision has **one normative home** (the document that owns that topic); the other
documents only link to it — no duplicated rules.

| # | Decision | Normative home |
|---|----------|----------------|
| 1 | Delivery engine = **Python + Boto3** in a new `delivery/` package | SPEC §4.5 (supersedes the old "language is a non-goal" note in §4.2) |
| 2 | Candidate metadata retention = **30 days** | OP-RET-02 (+ AD-16) |
| 3 | Production smoke tests = **backend health, frontend marker/content via CloudFront, read-only `GET /api/v1/items`** | CT-PROD-03 |
| 4 | Staging compatibility = **previous official frontend vs candidate backends**, then **candidate frontend E2E**; bootstrap exception if no prior release | AD-15 + OP-STG-03 |
| 5 | SBOMs = **pinned Syft SPDX JSON, permanent on official Releases, SHA-256 in manifest** | CT-REL-01 + OP-FIN-01 + OP-RET-02 |
| 6 | Runtime compatibility = **sanitized config fingerprint per release**; runtime/task-definition/schema/network changes invalidate rollback targets | AD-16 + OP-RET-01 + OP-REC-03 + CT-REL-01 |
| 7 | Ownerless staging RDS = **scheduled GitHub Actions job every 15 min, staging role** | OP-STG-05 (+ VR-STG-03) |

## How the flow reads now

A reader following the documents can now answer concrete questions:

```text
"Can I roll back to release-N-2?"
   └─ OP-RET-01: must be one of the 4 protected complete sets
        └─ OP-REC-03: its compatibilityFingerprint must still match
             current runtime/task-definition/schema/network config
                  → no match = NOT rollback-capable (fail closed)

"Staging RDS restarted by AWS at 3am with nobody using it?"
   └─ OP-STG-05: scheduled job (every 15 min) detects running RDS
        with no owner marker → stops it → verifies stopped
             → read error? fail visibly, never assume absence
```

## Changes made

- **SPEC.md** — §4.2 non-goal reconciled; §4.5 retitled and filled with the seven
  resolved decisions; AD-15 (exact staging-compatibility journey), AD-16 (30 days +
  fingerprint invalidation), §9.4 readiness note; status line updated.
- **CONTRACTS.md** — CT-REL-01 gained `artifacts.sbom` (with SHA-256) and
  `compatibilityFingerprint`; CT-PROD-03 now names the exact production smoke journeys.
- **OPERATIONS.md** — OP-STG-03 (exact compatibility journey), OP-STG-05 (mechanism
  selected, no longer "readiness gap"), OP-FIN-01 (attach SBOMs), OP-REC-03 (fingerprint
  in preflight), OP-RET-01/02 (incompatibility + permanent SBOMs).
- **VERIFICATION.md** — VR-STG-03 now references the scheduled-job mechanism; status
  lines unified across all four files.

## Verification

- Reviewer subagent: **no HIGH/MEDIUM findings**; all 60+ cross-document anchor links
  verified resolvable; no contradictions; no stale "deferred/provisional/open/gap"
  wording remains (grep-checked).
- One LOW nit fixed after review: SPEC §8.4 compound link `OP-STG-04/05` now links
  both sections properly.
- No files outside the four specs were touched; nothing committed.

## What's next

**Phase 2 — Build the `delivery/` Python package** (models, canonical JSON, validation,
run/attempt authority, AWS adapters, CLI) against these now-fixed contracts.