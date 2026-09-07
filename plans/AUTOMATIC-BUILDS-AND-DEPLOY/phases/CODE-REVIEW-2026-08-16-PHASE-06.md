# Code Review — 2026-08-16 (Phase 6)

Working-tree review of the Phase-6 recovery, rollback, and retention
(`delivery/src/delivery/commands/recover.py`, `rollback.py`, `retention.py`,
`deploy_support.py`, `finalize.py`, `verify.py`, `validation.py`,
`.github/workflows/promote-release-greenfield.yml`,
`.github/workflows/rollback-release-greenfield.yml`,
`delivery/src/delivery/retention/ecr-lifecycle-policy.json`). Process: three
build chunks (6A recovery, 6C retention, 6B rollback), one review agent
(report + gates), two parallel fix clusters (A: recovery/compensate
workflows; B: retention/finalize/CLI), one verify agent, one small
LOW-closing agent. Legacy machinery untouched. Findings resolved to zero
MEDIUM+; gates green at every stage.

## Findings and resolution

### HIGH

| # | Finding | Resolution |
|---|---------|------------|
| H1 | `recover` restored only the live marker while the live-root dist files stayed the failed candidate's — the hybrid frontend reported "completed" | Fixed: the shared `deploy_support` helper restores the live-root dist files from the snapshot release's retained immutable prefix with the aggregate content checksum proven BEFORE the live switch (`index.html` last, marker last); `rollback` switched to the same helper |

### MEDIUM

| # | Finding | Resolution |
|---|---------|------------|
| M2 | Both `compensate` jobs restored state but never re-ran read-only verification — a failed restore could pass silently | Fixed: `verify production --snapshot` plus verification steps in both compensate jobs, with a fail-flip so a failed verification outcome fails the compensate job |
| M3 | `retention audit` checked prefix-marker EXISTENCE only — a wrong-content marker would count a release as complete | Fixed: marker content identity-equivalence against the official marker derivable from the release manifest, with a distinct `PREFIX_MARKER_MISMATCH` kind (separate from absence and read errors) |
| M4 | `finalize`'s private publish-time window audit diverged from the shared retention audit (weaker checks, duplicated logic) | Fixed: `finalize` reuses `retention.audit_entry(verify_marker_content=True)` for the previous releases, with a bespoke current-entry check |

### LOW

| # | Finding | Resolution |
|---|---------|------------|
| L5 | `retention apply` accepted `--reference-date` on a real apply | Fixed: rejected fail-closed (`VALIDATION`) — ECR's live evaluator uses its own clock; the flag remains for `preview` and `apply --dry-run` |
| L6 | A mid-loop apply failure lost which repositories were already mutated | Fixed: partial apply report (processed repositories + the failed one with its detail) written to `--out FILE` before re-raising |
| L7 | A `verify staging` stub existed in the production-only greenfield engine | Fixed: removed |
| L8 | `uv.lock` was not gitignored | Fixed: `delivery/.gitignore` entry added |
| L9 | Produced `RecoveryResult`/`RollbackResult`/`FinalizationReport` records were written without semantic validation | Fixed: each is validated before write, so an invalid record fails instead of being persisted |
| L10 | `delivery/README.md` lagged the Phase-6 commands | Fixed: README updated to the final command set and semantics |

### Deliberately skipped

| Finding | Reason |
|---|---|
| Retention is an operator CLI, not a workflow | OP-RET is operational tooling (AD-16); no GitHub workflow exists or is planned — documented in the README |
| First `retention apply` proceeds on an honestly-labeled modeled preview when no policy exists yet | ECR `start-lifecycle-policy-preview` errors on an absent policy; the fallback applies only to the no-policy case and the result is labeled modeled, never claimed live; get-preview errors never fall back |
| ECR lifecycle evaluation is delayed up to 24 h | AWS behavior; the post-apply window audit never claims policy effect |
| Part-way-failed mutation steps are never compensated | OP-REC-01: an ambiguous mid-step kill publishes no completion output, so compensation never guesses — manual investigation is the intended path |
| Preflight IAM role (`github-actions-production-preflight`) is not created yet | Role creation + live OIDC environment-subject trust read-back are consolidated-live-pass items (Phase 7) |

## Verification

Verify agent re-examined all fixes against the normative docs
(OPERATIONS.md OP-REC-01..04, OP-RET-01..03, OP-DB-02, AD-13/16/17,
CT-GEN-04): all 1 HIGH and 3 MEDIUM verified, no regressions. Final merged
tree gates:

- `pytest delivery/tests -q` → 1027 passed
- `ruff check delivery` → clean
- `actionlint` on all greenfield workflows → clean
- `zizmor` on both greenfield promotion/rollback workflows → no findings

Offline only: stateful AWS/GitHub fakes and static workflow checks. No
live AWS behavior is claimed — live promotion/rollback drills, role
creation/OIDC trust, and real retention application are Phase 7.
