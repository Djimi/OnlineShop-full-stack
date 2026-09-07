# Phase 6 — Recovery, Rollback, Retention

**Plan:** `DELIVERY-SYSTEM-IMPLEMENTATION-PLAN.md` §6
**Status:** ✅ Complete (offline) — 1 new workflow
(`rollback-release-greenfield.yml`), 1 expanded workflow
(`promote-release-greenfield.yml`), 3 CLI commands (`recover`,
`rollback preflight|execute`, `retention audit|preview|apply`), a shared
frontend-restore helper, and the desired ECR lifecycle policy asset; all
gates green. Live AWS exercise deferred to the consolidated verification
pass (see "Known limitations" below).

## What this phase was about

Closing the delivery loop: defined failures heal automatically from the
pre-mutation snapshot, deliberate rollbacks are owner-approved and
evidence-bound, and the four-release rollback window is enforced and
audited — without ever reversing production data.

Built in three chunks:

## Chunk 6A — Automatic recovery

`recover` restores ONLY the components the failed promotion actually
mutated, and the promote workflow gains an automatic, approval-free
`compensate` job.

```text
promote-release-greenfield.yml (expanded in 6A)
  promote job:
    snapshot uploaded BEFORE any mutation (survives hard kills)
    each mutation step publishes its completion (AD-13)
      └─ ambiguous mid-step kill → no output → never guessed
        │ failure() && (backends|gateway|frontend == 'success')
        ▼
  compensate (automatic, approval-free, production concurrency group)
    recover --changed <only-completed components> from the snapshot
    verify production --snapshot (read-only, fail-flip on failed verify)
    recovery evidence uploaded (retention 14d)
```

- `recover --snapshot <file> --changed <file>` — the exact `--changed`
  array (`auth`, `items`, `gateway`, `frontend`) is the only authority for
  what gets restored; image-only digest-pinned task-definition
  re-registration (secrets stay full-ARN `secrets[].valueFrom`), bounded
  waiters, running-digest verification against the snapshot digests.
- Frontend: live-root dist files restored from the snapshot release's
  retained immutable prefix, aggregate content checksum proven BEFORE the
  live switch, `index.html` last, official marker restored last,
  invalidation read back.
- Ambiguous or inconsistent snapshot internals stop with evidence, never
  guess; AWS read errors are read errors, never absence. The database is
  never touched (OP-DB-02).
- A `RecoveryResult` records the original failure and the recovery outcome
  separately; a failed recovery is reported as failed, never success.

## Chunk 6B — Owner-approved rollback

```text
rollback-release-greenfield.yml (workflow_dispatch: version only)
  │
  job A `preflight` — read-only, preflight role, NO concurrency group
  │   fresh snapshot → rollback preflight (target exists/complete/
  │   non-current/in-window/fingerprint-compatible, schema guard)
  ▼
  owner approves the protected `production` Environment
  │
  job B `rollback` — environment: production,
  │  concurrency group: production
  │    guard → inputs → fresh snapshot → re-run FULL preflight
  │    (approval-identity drift aborts pre-mutation) → deploy the
  │    complete target set: backends → gateway → frontend from the
  │    retained immutable prefix (checksum before live switch,
  │    index.html last, marker names the official target) →
  │    verify production (read-only) → RollbackResult
  ▼
  job C `compensate` — automatic on failure, restores exactly the
  `passed` components from the pre-rollback snapshot
```

- `rollback preflight` (OP-REC-03): the target must be a published official
  release, must not be the currently running release, must sit in the
  current + 3 previous window, and must be complete — every backend ECR
  `release-<NNNN>` tag resolves to the manifest's exact digest, the
  frontend prefix marker exists AND its content names the target, and the
  recorded `compatibilityFingerprint` matches the current runtime
  fingerprint (`INCOMPATIBLE` → rejected). `--schema-change present` always
  fails closed (OP-DB-02).
- `rollback execute` (OP-REC-04): re-runs the FULL preflight with
  approval-identity drift abort, and the consumed manifest must equal the
  GitHub-hosted official manifest (CT-GEN-04). Deploys the complete
  application set without minting/moving tags, creating releases, editing
  history, or touching the database.
- Separate `RollbackResult` with mandatory requester + approver (from
  approval evidence, never the run actor), from/to identities with exact
  digests/checksum, semantically validated before write; the failed result
  is written first, then the job fails.

## Chunk 6C — Retention window enforcement

```text
retention audit (read-only) → retention preview (read-only) → retention apply (env-guarded)
```

- `retention audit` (AD-16, OP-RET-01): current + ≥3 previous complete
  releases verified against their official manifests — ECR tag→digest
  anchors, frontend prefix-marker existence AND content identity-equivalence
  (`PREFIX_MARKER_MISMATCH`, distinct from absence and read errors), and
  `compatibilityFingerprint` vs the current runtime fingerprint. Fail-closed
  with distinct per-entry kinds; read errors are never absence; incomplete
  older sets are listed but never counted in the window.
- `retention preview` (OP-RET-03): live ECR lifecycle-policy preview
  (start + get, bounded waiter) vs the local first-match-wins model;
  `PREVIEW_DISAGREEMENT` and `PROTECTED_IMAGE_EXPIRING` (a window release
  tag or any `release-*` inside the newest-10 keep margin) fail closed; a
  no-policy-yet evaluation is honestly labeled as a modeled preview.
- `retention apply` (OP-RET-02/03): `--apply` refused without
  `DELIVERY_RETENTION_LIVE_APPLY=1`; `--reference-date` rejected on a real
  apply; per-repo put with immediate byte-for-byte
  `get-lifecycle-policy` read-back; mid-loop failure writes a partial
  apply report; retention never deletes images itself.
- Desired policy asset `src/delivery/retention/ecr-lifecycle-policy.json`:
  rule 1 keeps the newest 10 `release-*` images (highest priority),
  rules 2–4 expire the `sha-*`/`main-latest`/`branch-*` candidate families
  after 30 days (one single-prefix rule each), rule 5 expires untagged
  after 14 days; the validator rejects merged `tagPrefixList` entries
  (`POLICY_TAGPREFIX_MULTI`) and the model implements first-match-wins.

## Key mechanics

| Rule | How it's enforced |
|---|---|
| Recovery authority (6A) | Only the exact `--changed` array of actually-completed mutation-step outputs is restored (AD-13); no output → not compensated (ambiguous mid-step kills stop for manual investigation, OP-REC-01) |
| Compensation safety (6A) | Snapshot uploaded BEFORE the first mutation; recover re-registers the snapshot's exact digest-pinned revision (image-only diff, full-ARN secrets), bounded waiters, running-digest equality; frontend checksum proven before the live switch, `index.html` last, marker last |
| Post-recovery honesty (6A) | `verify production --snapshot` re-runs read-only after recover; a failed verification fails the compensate job (never a silent "recovered") |
| Rollback target validity (6B) | Published official release only; non-current; current + 3 window; complete set (ECR tag→digest anchors + prefix-marker CONTENT + fingerprint match); `--schema-change present` always rejected |
| Approval binding (6B) | Preflight twice (informational before approval, authorizing under the production lock); `approvalIdentity` SHA-256 compared byte-for-byte; approver/requester mandatory in the RollbackResult from approval evidence |
| Rollback scope (6B) | Complete target set from the release manifest's exact digests; no tag mints/moves, no release creation, no manifest edits, no RDS mutation (OP-DB-02) |
| Window audit (6C) | Current release observed from the live snapshot (never GitHub order); each entry fail-closed with distinct kinds; read errors never absence; marker content identity-equivalence |
| Policy safety (6C) | Env-guarded apply; byte-for-byte read-back per repository; protected-image expiry and preview disagreement fail closed; modeled fallback honestly labeled; never deletes images |

## Review findings (all resolved)

One review round: 1 HIGH, 3 MEDIUM, 6 LOW — all fixed. Full detail in
[`CODE-REVIEW-2026-08-16-PHASE-06.md`](./CODE-REVIEW-2026-08-16-PHASE-06.md).

| Sev | Finding | Fix |
|---|---|---|
| HIGH | `recover` restored only the live marker while live-root dist files stayed the failed candidate's (hybrid frontend reported "completed") | Shared `deploy_support` helper restores retained-prefix dist files with the aggregate checksum proven before the live switch; rollback switched to it |
| MEDIUM | Compensate jobs never re-ran read-only verification | `verify production --snapshot` + verification steps in both compensate jobs with fail-flip |
| MEDIUM | Retention audit checked prefix-marker existence only | Content identity-equivalence with a distinct `PREFIX_MARKER_MISMATCH` kind |
| MEDIUM | Finalize's private window audit diverged from the shared one | Reuses `retention.audit_entry(verify_marker_content=True)` with a bespoke current-entry check |

## Known limitations / live-pass deferrals

Stated honestly — none of these are claimed as verified:

- Live promotion/rollback drills (real `production` Environment approval,
  real ECR/ECS/S3/CloudFront mutations + read-backs, real GitHub Release
  publication) have not run yet.
- IAM role creation and OIDC environment-subject trust read-backs for the
  greenfield roles (mutation + read-only preflight) are deferred to the
  consolidated live pass.
- ECR lifecycle evaluation is delayed (up to 24 h); a post-apply audit
  never claims policy effect.
- Frontend `_releases/` prefix retention (S3 lifecycle configuration) is a
  live-pass item — not applied by the CLI.
- All evidence is offline: stateful AWS/GitHub fakes and static workflow
  checks; no live AWS behavior is claimed.

## Verification

- `pytest delivery/tests -q` → 1027 passed; `ruff check delivery` → clean.
- `actionlint` on all greenfield workflows → clean. `zizmor` on
  `promote-release-greenfield.yml` and `rollback-release-greenfield.yml`
  → 0 findings.
- Committed as `731cc0f` on top of the chunk commits `0d223b0` (6A + 6C)
  and `7a38a8d` (6B).

## What's next

**Phase 7 — Cutover + live acceptance** (plan §7, OP-CUT-02, VR-READY):
IAM roles live, live staging lifecycle, live owner-approved promotion,
live N → N-1 → N rollback drill, start/stop environment, read-only
production journeys, artifact checks, legacy trigger disable + deletion
inventory — then the legacy release machinery is deleted.
