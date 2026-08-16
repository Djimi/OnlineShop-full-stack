# Phase 3 — CI + Candidate Publication

**Plan:** `DELIVERY-SYSTEM-IMPLEMENTATION-PLAN.md` §3
**Status:** ✅ Complete (2 chunks implemented, 1 review round, all findings resolved)

## What this phase was about

The build pipeline: every eligible push produces ONE complete, immutable candidate —
all four components from one SHA, owned by the exact run/attempt — and PRs only validate
(read-only, no AWS).

```text
push greenfield/** (bring-up)               pull_request (incl. forks)
         │                                        │
         v                                        v
test-auth ─┐                              test-auth ─┐
test-items ├─ _java-service.yml           test-items ├─ same validation
test-gateway┘  (mvnw clean test)          test-gateway┘   NO AWS credentials
test-frontend (npm lint+build)            test-frontend
e2e (docker compose, no AWS)              e2e (docker compose)
         │                                        │
         v                                        v
publish (OIDC Artifact-Publisher role)     DONE — nothing else runs
  1. build+push 3 images  → tags: sha-<fullsha> (+ main-latest / branch-*)
  2. read back ECR digests (batch-get-image)  ← digests, never tags
  3. frontend archive + SHA-256 checksum
  4. 4× pinned-Syft SPDX SBOMs
  5. delivery candidate manifest --class main|feature
  6. delivery candidate validate --max-age-days 30
  7. upload artifacts named <name>-<run-id>-<attempt>
```

## Trigger isolation (bring-up status)

Push triggers are `greenfield/**` only: legacy `build-and-deploy.yml` still
owns `main` and `feature/**` pushes, and both workflows pushing the same
`sha-<fullsha>` tags into the same immutable-for-sha ECR repositories would
collide (OP-CUT-01). Stated honestly:

- The live push path (publish job, real ECR pushes) is proven on a
  `greenfield/**` branch — `main` pushes do NOT run this workflow yet.
- `greenfield/*` pushes map to candidate class `feature` (mutable tag
  `branch-greenfield-*`); the `main` mapping is offline-tested but not
  exercised live yet.
- PR validation (`pull_request: branches: [main]`) is live and read-only.
- Expansion to `main` + `feature/**` happens only at cutover, after legacy
  triggers are disabled (DELIVERY-SYSTEM-IMPLEMENTATION-PLAN §7 / OP-CUT-02).

## Key mechanics

| Rule | How it's enforced |
|---|---|
| Exact run/attempt ownership | All artifact names embed `github.run_id` + `github.run_attempt` |
| Immutable identity | Images tagged `sha-<fullsha>`; candidate manifest carries only digests/checksums |
| No relabeling | `--class` derived from branch (main → main, feature/* and greenfield/* → feature); feature can never be production-eligible |
| No selective builds | All four always build/tested from the same `github.sha` |
| PR has no AWS path | `publish` guarded by `github.event_name == 'push'`; only it has `id-token: write` |
| Least privilege | Workflow `contents: read`; OIDC role `github-actions-candidate-build` (per role layout) |
| 30-day candidate expiry | `validate --max-age-days 30` — expired candidates can't deploy later |

New `delivery` CLI surface added in this phase: **`candidate manifest`** (builds a strict
candidate record from structured inputs: run/attempt shape enforced, `commonSourceSha ==
fullSha` enforced, canonical JSON out) and **`is_expired()`** in validation.

## Review findings (all resolved)

| Sev | Finding | Fix |
|---|---|---|
| MEDIUM | publish job built the frontend with the runner's default Node | `setup-node` v24 pinned, `package-manager-cache: false` (also silences zizmor cache-poisoning audit) |
| MEDIUM | Static gates didn't assert tag scheme / expiry flag / digest read-back / class mapping | 8 new tests; the tags-step bash script is actually executed for `main` + `feature/*` refs |
| LOW | Branch-tag sanitization only handled `/` | lowercase + map everything outside `[a-z0-9._-]` to `-` (ECR tag rules) |
| LOW | validate step lacked `--class` (defense in depth) | added |
| LOW | Partial artifact set risk on upload failure | documented: safe only because Phase-4 staging gate enforces full-set existence (CT-CAND-03) |

## Verification

- 363 tests green (`pytest delivery/tests`), ruff clean.
- actionlint 1.7.12: clean on both workflows. zizmor 1.29.0: 0 findings.
- All 9 third-party actions pinned by 40-hex SHA + version comment (verified against
  real release tags via `git ls-remote`).
- Java 25 cross-checked across poms, Dockerfiles, and setup-java.

## What's next

**Phase 4 — Shared staging**: `stage-candidate.yml` (manual dispatch, serialized via
non-canceling concurrency, full OP-STG lifecycle with ownership marker) +
`reconcile-staging.yml` (every 15 min, stops ownerless staging RDS) + the delivery CLI
staging lifecycle + apply commands. Note: the workflow already fails closed if the
staging commands don't exist yet — implement them in this phase.