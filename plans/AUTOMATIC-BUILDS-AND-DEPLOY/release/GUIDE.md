# OnlineShop Release Machinery — Operator's Guide

This file explains **what is in `release/`**, **how the pieces call each other**,
and **how the release flows work end to end** (Pass 3: build evidence →
promotion → rollback → traceability → retention). Read this before reading the
[README.md](./README.md) detail sections.

---

## 1. The mental model: three layers

Everything in Pass 3 follows one strict architecture:

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. WORKFLOWS  (.github/workflows/*.yml)                             │
│     Orchestration only: jobs, approvals, concurrency, artifacts.    │
│     They string shell scripts together; they contain no logic.      │
├─────────────────────────────────────────────────────────────────────┤
│  2. SHELL WRAPPERS  (release/bin/*.sh)                               │
│     "Gather" the live world (aws CLI / gh CLI / jq / files) and     │
│     "call" the decision layer with argument arrays or JSON.         │
│     They contain NO security decisions — only data collection,      │
│     read-backs after mutations, and exit-code propagation.          │
├─────────────────────────────────────────────────────────────────────┤
│  3. PYTHON DECISION LAYER  (release/src/release_contract/*.py)      │
│     Pure, fixture-tested logic: schema validation, cross-field      │
│     rules, mint/reuse/fail-closed decisions.                        │
│     Invoked as `python3 -m release_contract.<module> <command>`.    │
│     No AWS/GitHub access ever — it only reasons about inputs.       │
└─────────────────────────────────────────────────────────────────────┘
```

**Why three layers?** Security-sensitive JSON is never parsed with regex or
ad-hoc shell string concatenation. Shell only *gathers* and *verifies*; every
"may we?" question is answered by the tested Python engine. This is the
contract spelled out at the top of `README.md`.

---

## 2. The artifact at the center: the release manifest

Everything orbits one JSON document, validated against
`schema/release-manifest.schema.json`:

| Concept | Meaning |
|---|---|
| One `version` (SemVer) | = one monorepo commit = all 4 components (`auth`, `items`, `apiGateway`, `frontend`) |
| `sourceSha` (40-char) | authoritative identity; SemVer and mutable tags are never deployment inputs |
| Digests | ECR `sha256:` per backend + frontend archive SHA-256 — the only bytes ever deployed |
| Two states | `candidate` (evidence recorded, not yet deployed) vs `official` (deployed + verified + published) |
| Determinism | `gitTag == "v"+version`, tags/prefixes derived from SHA/version, no extra fields |
| Immutability | official manifest never edited; rollback writes a **separate** result record |

`bin/validate-manifest.sh` (→ `release_contract.cli`) is the gatekeeper:
exit 0 valid / 1 invalid / 2 usage. Every flow below starts by validating its
manifest.

---

## 3. The universal script pattern

Every `bin/*.sh` follows the same shape (see `promotion-preflight.sh` for the
cleanest example):

```
bin/xxx.sh --flags
   │
   ├─ source release-input.sh            # rl_assert_semver, rl_assert_full_sha, ...
   │    (validate every dispatch input BEFORE use; pass values only via
   │     "$@" / env, never interpolated into command strings)
   │
   ├─ jq / aws / gh ...                  # gather live state → JSON files
   │
   ├─ PYTHONPATH=src python3 -m release_contract.<module> <command> \
   │      (feed gathered state → the module DECIDES: ok or {code,field,message})
   │
   ├─ FAIL-CLOSED branch                 # non-zero code → exit 1 with the issue codes
   │
   └─ mutation + read-back               # every aws/gh mutation is immediately
                                          # describe/get'ed back and compared
```

Global invariants (enforced by the gates):
- every `aws` call carries `--profile dpm-profile --region eu-north-1`
  (non-overridable);
- an `sts get-caller-identity` preflight runs before any AWS work;
- "fail closed" everywhere: an AWS read error is an error, never treated as
  "resource missing" or "success".

---

## 4. Flow A — Every `main` push → candidate evidence (subphase 3.2)

**Trigger:** push/PR CI in `.github/workflows/build-and-deploy.yml`.

```
 build-and-deploy.yml (per push to main)
 │
 ├── auth ──┐            each backend job:
 ├── items ─┼──┐           1. docker build (sha-<full-sha> tag)
 ├── api-gateway ─┤         2. publish-candidate-image.sh ──► release_contract.candidate decide
 │              │                "missing → push / trusted producer → reuse / else fail closed"
 │              │           3. image-labels.sh ──► OCI producer labels from image config
 ├── frontend  ─┤
 ├── e2e-staging┘  (resume staging → deploy candidate → cloud E2E → pause,
 │                  serialized per-main-push, teardown owned by the same run)
 │
 └── candidate-evidence  (runs ONLY if all five above succeeded)
       ├─ aws ecr describe-images            → resolve the three sha-<sha> digests
       ├─ verify-producer-set.sh             → release_contract.candidate set-check
       │                                        (same producer run, revision==SHA, common rev==SHA)
       ├─ image-labels.sh                    → producer run id/attempt (label read)
       ├─ npm build (VITE_API_URL='')        → reproducible build
       ├─ package-frontend.sh                → normalized tar.gz + sorted checksum manifest
       ├─ generate-sbom.sh (×4)              → SPDX for frontend + 3 registry digests
       ├─ emit-candidate-evidence.sh         → release_contract.artifact verify
       │                                        writes candidate-evidence.json + checksums.txt
       ├─ upload-artifact (30 days)          → candidate-evidence-<sha>-<attempt>
       └─ record-artifact.sh                 → artifact-id.json pointer (bundle cannot
                                                contain its own future ID — circular)
```

**Result:** one immutable evidence bundle per successful main push. No SemVer
yet — the version is assigned *later* at promotion time (`emit-candidate-manifest.sh`).

---

## 5. Flow B — Promotion: staging → production (subphase 3.4)

**Trigger:** manual dispatch of `promote-release.yml` with `version` + candidate `run_id`.

```
 Operator dispatches: version=vX.Y.Z, run_id=<candidate run>
 │
 ▼
┌─ preflight JOB (NO AWS access, NO approval needed) ──────────────────┐
│ release-input.sh (validate version + run_id + optional sha)          │
│ gh api runs/artifacts ─► resolve EXACT run attempt (never latest)    │
│ gh run download --attempt                                            │
│ emit-candidate-manifest.sh ─► candidate-manifest.json (schema-valid) │
│ validate-manifest.sh                                                 │
└──────────────────────────────────────────────────────────────────────┘
 │
 ▼  GitHub requires owner approval on the protected "production" Environment
 │
┌─ promote JOB (approved; shared non-cancelling concurrency "production-mutation") ─┐
│ 1. re-download evidence for the SAME attempt + re-render manifest                │
│ 2. promotion-preflight.sh ──► release_contract.promotion                         │
│      dispatch → run → ancestry → preflight (incl. Decision-8 DB review)          │
│    check-release-identity.sh ──► release_contract.releaseid                      │
│      (git tag / ECR release tags / frontend marker: free, resume, or fail-closed)│
│ 3. unpack-frontend.sh (safe extract + checksum verify)                           │
│ 4. snapshot-production.sh ──► promotion snapshot        → production-snapshot    │
│ 5. deploy-production.sh ──► promotion plan + waiter                               │
│      per backend: sanitize-task-definition.sh (image-only digest pin)            │
│                   validate-task-definition.sh (hardening rules)                  │
│      register TD revision → update service → wait bound deployment                │
│      → deployment-manifest.json (candidate bytes + new TD ARNs)                  │
│ 6. publish-frontend.sh ──► promotion frontend                                     │
│      assets-first/index-last, no --delete, immutable _releases/v<version>/        │
│      prefix + live-root marker, CloudFront invalidation                           │
│ 7. render official-manifest.json                                                 │
│      approvedBy = gh api runs/{run}/approvals  (never github.actor!)             │
│ 8. verify-production.sh ──► promotion verify (running digests, TD ARNs,          │
│      frontend marker, ALB health)                                                │
│ 9. finalize-release.sh ──► promotion finalize                                    │
│      promote-image-digest.sh ──► release_contract.ecr (server-side mint of        │
│        release-<version> from sha-<sha> bytes — batch-get-image + put-image)     │
│      publish git tag v<version> + GitHub Release with manifests/SBOMs/checksums  │
│      (refused unless PROMOTION_PRODUCTION_VERIFIED=true)                         │
└──────────────────────────────────────────────────────────────────────────────────┘
 │ on promote failure (automatic, no new approval)
 ▼
┌─ compensate JOB ──────────────────────────────────────────────────────┐
│ compensate-production.sh --changed '["frontend","auth","items",       │
│   "apiGateway"]' ──► promotion compensate                              │
│ restores the exact pre-promotion snapshot in reverse order             │
└───────────────────────────────────────────────────────────────────────┘
```

**Key properties:** exact candidate bytes are consumed, never rebuilt; the
post-approval preflight re-validates with a fresh snapshot (time-of-check race
closure); the reward (official release) is only minted after verification.

---

## 6. Flow C — Rollback to an existing official release (subphase 3.6)

**Trigger:** manual dispatch of `rollback-release.yml` with the `version` of an
existing official release (never a tag/digest/SHA).

```
 Operator dispatches: version=vX.Y.Z (+ requester, schema-change inputs)
 │
 ▼
┌─ preflight JOB (read-only AWS: ECR describes + S3 marker reads) ─────┐
│ release-input.sh         (semver + GitHub login)                     │
│ rollback-preflight.sh ──► release_contract.rollback                  │
│      select: latest 10 COMPLETE official sets (ECR tag → digest,     │
│              frontend prefix marker present + matching)              │
│      schema: Decision-8 DB compatibility guard                       │
│ emits rollback-target-manifest.json (validated target)               │
└──────────────────────────────────────────────────────────────────────┘
 │  ▼ owner approval on "production" Environment
┌─ rollback JOB (shared "production-mutation" lock) ───────────────────┐
│ 1. re-run rollback-preflight.sh against a FRESH snapshot              │
│    cmp -s against the pre-approval manifest — byte-diff fails closed  │
│ 2. snapshot-production.sh                        → production-snapshot│
│ 3. deploy-rollback.sh ──► rollback plan + waiter                      │
│      sanitize-task-definition.sh + validate-task-definition.sh        │
│      (digest-pinned revision, service update, bound waiter)           │
│      NO ECR tag minting, NO rebuild, NO new release                   │
│ 4. restore-frontend.sh ──► rollback frontend-restore                  │
│      live root restored from retained immutable _releases/v<version>/ │
│      marker/index last, no --delete, CloudFront invalidation          │
│ 5. verify-rollback.sh ──► rollback verify (paused env fails closed)   │
│ 6. record-rollback-result.sh ──► rollback result                      │
│      requester (from preflight) + approver (from approval evidence,   │
│      never actor) + from/to digests + outcome → audit artifact        │
└──────────────────────────────────────────────────────────────────────┘
 │ on rollback failure
 ▼
└─ compensate JOB — same as Flow B, restores pre-rollback snapshot.
   The DATABASE IS NEVER REVERSED (Decision 8).
```

---

## 7. Read-only operator queries — traceability (subphase 3.7)

`bin/trace.sh` is the only CLI you run from your laptop:

```
 trace.sh ──► release_contract.traceability (index + observed → decision)
   │
   ├─ index    = GitHub Release release-manifest.json assets (gh api)
   │             or a local fixtures/traceability/index.json
   ├─ observed = live read-only AWS state (ECR describe-images,
   │             ECS describe-tasks/services, S3 get-object markers)
   │
   ├─ commit  --sha <sha>      → candidate run + digests + releases
   ├─ release --version <v>    → source SHA + components + prefix-marker cross-check
   ├─ running                  → task-defs + RUNNING digests + frontend identity
   ├─ digest  --digest <sha>   → ECR tags + OCI revision + release identity
   └─ audit   [--version]      → full manifest↔ECR↔ECS↔frontend consistency audit
```

Exit `0` only when found AND consistent; `NOT_FOUND`/`*_MISMATCH` → 1;
usage → 2. `--human` adds a readable view. Paused production is reported
honestly (TD digests, no fabricated running digests).

---

## 8. Retention & rollback-window enforcement (subphase 3.8)

```
 ecr/lifecycle-policy.json  (desired state: keep-10 release-*, 30-day
   │                          sha-/main-latest-/branch- expiry, 14-day untagged)
   │
 bin/audit-retention-window.sh ──► release_contract.retention audit + coverage
   │   (read-only: exact rollback-window releases; missing/mismatch fails closed)
 bin/preview-retention-policy.sh ──► retention evaluate (offline model)
   │                              ──► retention validate-preview (live dry-run)
 bin/apply-retention-policy.sh ──► retention validate-policy
   │   --dry-run = preview only; --apply refuses offline
   │   (needs ONLINESHOP_RETENTION_LIVE_APPLY=1, set only by the live pass)
   └── put-lifecycle-policy → get-lifecycle-policy byte-for-byte read-back
```

Protected digests (window, currently deployed, previous known-good) can never
be in the expiring set — that fails closed.

---

## 9. The remaining supporting flows

### 9.1 ECR immutability (3.3)
`apply-immutable-repositories.sh` / `verify-immutable-repositories.sh` set and
read back `IMMUTABLE_WITH_EXCLUSION` (exclusions exactly `main-latest`,
`branch-*`) on the three backend repos. `release_contract.iam` validates the
per-purpose IAM/OIDC policy documents (the actual role split is applied in the
consolidated live pass).

### 9.2 Production hardening (3.5)
`release/bin/validate-task-definition.sh` and `sanitize-task-definition.sh`
enforce the release-task-definition contract; `scripts/inventory-production.sh`,
`verify-production-staging-separation.sh`, `verify-frontend-oac.sh`,
`migrate-frontend-oac.sh`, `verify-cloudtrail-coverage.sh` are read-only/mutating
ops tooling backed by `release_contract.ecs_config`, `.sanitize`,
`.environments`, `.frontend_hosting`, `.cloudtrail`.

---

## 10. How the offline gates prove all of this

```
 tests/scripts/<subphase>_test.sh   (repo root, run manually or in CI)
   │
   ├── runs release/tests/test_*.py        (Python unit tests of decision modules)
   ├── static checks of the *.yml workflows (pins, env gates, no-secrets,
   │     teardown ownership, approvedBy derivation, retention-days, ...)
   ├── CLI checks: bin/*.sh against fixtures/ + a STATEFUL AWS/gh STUB
   │     (proves the gather path and fail-closed behavior without real AWS)
   └── lint: ruff + shellcheck + bash -n + git diff --check
```

| Gate | Proves |
|---|---|
| `release_contract_test.sh` | 3.1 manifest contract + validator |
| `candidate_evidence_test.sh` | 3.2 evidence bundle flow + workflow static checks |
| `ecr_release_tagging_test.sh` | 3.3 ECR mint/reuse/identity/IAM |
| `promotion_test.sh` | 3.4 full promotion + compensate |
| `production_hardening_test.sh` | 3.5 TD/service config, inventory, separation, OAC, CloudTrail |
| `rollback_test.sh` | 3.6 rollback + result + compensate |
| `release_traceability_test.sh` | 3.7 all lookups + audit |
| `retention_test.sh` | 3.8 policy model, preview, audit, coverage |

Everything marked "deferred live check" (real AWS mutations, real approvals,
real GitHub Releases, OAC migration, lifecycle-policy apply, live trace
smoke test) happens once in the consolidated Pass 3 verification pass — the
offline gates deliberately do not claim them.

---

## 11. Cheat sheet: who calls whom

| Shell wrapper | Decision module | Role |
|---|---|---|
| `validate-manifest.sh` | `cli` (validate.py/crossrules.py) | schema + cross-field validation |
| `release-input.sh` | — (pure shell) | strict input assertions |
| `publish-candidate-image.sh` / `verify-producer-set.sh` / `emit-candidate-manifest.sh` | `candidate` | push-or-reuse, canonical set, manifest builder |
| `emit-candidate-evidence.sh` | `artifact` | evidence bundle assembly + verification |
| `package-frontend.sh` / `unpack-frontend.sh` | `frontend` | reproducible package / safe extract |
| `generate-sbom.sh` | — (syft) | SPDX SBOMs |
| `promote-image-digest.sh` | `ecr` | release-tag mint/reuse/verify |
| `check-release-identity.sh` | `releaseid` | collision/resume preflight |
| `apply/verify-immutable-repositories.sh` | — (aws) | repo mutability settings |
| `promotion-preflight.sh` | `promotion` (dispatch/run/ancestry/preflight) | candidate gate |
| `snapshot-production.sh` | `promotion` (snapshot) | compensation source |
| `deploy-production.sh` / `deploy-rollback.sh` | `promotion`/`rollback` (plan, waiter) | digest-pinned deploy |
| `sanitize-task-definition.sh` / `validate-task-definition.sh` | `sanitize` / `ecs_config` | TD transform + hardening |
| `publish-frontend.sh` / `restore-frontend.sh` | `promotion`/`rollback` (frontend) | S3 prefix publication / restore |
| `verify-production.sh` / `verify-rollback.sh` | `promotion`/`rollback` (verify) | post-deploy proof |
| `finalize-release.sh` | `promotion` (finalize) + `ecr` | tags + GitHub Release |
| `compensate-production.sh` | `promotion` (compensate) | automatic restore |
| `rollback-preflight.sh` | `rollback` (select, schema) | target resolution |
| `record-rollback-result.sh` | `rollback` (result) | audit annotation |
| `trace.sh` | `traceability` | read-only lookups |
| `audit/preview/apply-retention-policy.sh` | `retention` | window audit + policy |
| `image-labels.sh` | — (docker buildx) | OCI label read |
| shared helpers | `components`, `checksums`, `semver` | tag derivation, digests, versions |

---

## 12. Suggested reading order

1. This guide (you are here).
2. `schema/release-manifest.schema.json` — the contract in grammar form.
3. `bin/promotion-preflight.sh` — the cleanest "gather → decide → fail-closed"
   example (~300 lines).
4. `.github/workflows/promote-release.yml` then `rollback-release.yml` — how
   approval + concurrency wrap the scripts.
5. `src/release_contract/promotion.py` — the heart: one decision function per
   lifecycle stage.
6. `README.md` for the full decision rationale, then `../03_RELEASE_TRACEABILITY.md`
   for the numbered decisions (1–13).