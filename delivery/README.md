# delivery

Python delivery engine for the OnlineShop CI/CD release system, implementing
the contracts in `plans/AUTOMATIC-BUILDS-AND-DEPLOY/` (SPEC, CONTRACTS,
OPERATIONS, VERIFICATION).

The engine is strict by design: fail-closed validation, canonical JSON
serialization, exact workflow run/attempt authority, immutable record
identity, and no secrets in records.

Artifact discovery selects only exact deterministic names for the validated
run/attempt. Unrelated GitHub artifacts are ignored without parsing their
fields; every selected record must be unique, non-expired, and fully valid.

## Layout

- `src/delivery/errors.py` — exception hierarchy with stable machine-readable codes
- `src/delivery/serialization.py` — canonical JSON and SHA-256 helpers
- `src/delivery/github.py` — exact workflow run/attempt authority, artifact/asset download, run listing, commit comparison, release publication
- `src/delivery/frontend.py` — shared candidate-frontend archive verification
- `src/delivery/live_marker.py` — production frontend live-marker model and identity rules
- `src/delivery/models/` — pydantic v2 records (candidate, staging, snapshot, release, promotion, rollback, recovery, retention, evidence)
- `src/delivery/retention/` — desired ECR lifecycle policy asset (`ecr-lifecycle-policy.json`), fail-closed policy validator, and the first-match-wins expiration model used by `retention preview|apply`
- `src/delivery/validation.py` — schema and cross-identity validation rules
- `tests/` — unit test suite and JSON fixtures

## Running tests and lint

```bash
python -m pytest
python -m ruff check .
```

## Entry point

Run the CLI from the repository root with the package on `PYTHONPATH`
or installed (or, inside the tests, via the configured `pythonpath`):

```bash
PYTHONPATH=delivery/src python -m delivery <command> [options]
```

`python -m delivery.cli <command> [options]` is equivalent. The entry
points parse argv, dispatch to the command handler, and print
machine-readable diagnostics on failure; the process exit code is the
CLI exit code (see below).

### Commands

- `candidate validate --manifest <file>` — validate a candidate manifest
- `snapshot production --out <file> [--profile NAME] [--region REGION] [--environment ENV] [--identifiers FILE]` — capture and internally validate the production snapshot
- `staging lifecycle --candidate <file> --frontend-archive <file> --repo-path DIR --out <file> [--e2e-url-out FILE]` — first invocation of the staging lifecycle (through E2E-prepared)
- `staging lifecycle --continue --e2e-conclusion <passed|failed> --repo-path DIR --out <file>` — second invocation: record the real cloud E2E conclusion, stop/verify cleanup, release the ownership marker, complete the record
- `staging apply --candidate <file> --repo-path DIR --out <file>` — deploy exact candidate digests to a running staging environment (no start/stop)
- `staging reconcile --out <file>` — OP-STG-05 ownerless-RDS reconciliation (stops ownerless running staging RDS and exits non-zero to surface the event; a genuinely absent staging DB is a no-op success)
- `deploy backends|gateway|frontend` — digest-pinned production deployment with image-only task-definition diffs, bounded deployment waiters, running-digest observation, immutable frontend prefix publication with checksum-before-switch, and read-back everywhere (Phase 5)
- `verify production` — read-only CT-PROD-01..04 verification (running digests, live marker, public CloudFront identity, health/items/index journeys) against an official manifest (`--manifest`), a candidate (`--candidate`), or a pre-mutation production snapshot (`--snapshot`, post-compensation)
- `promote preflight` — read-only OP-PRO-02 preflight: candidate eligibility (AD-03/05), exact staging gate (AD-09), ECR digests, fresh production snapshot, AD-11 newer-candidate reachability + warning, OP-DB migration-ownership gate, post-approval drift comparison (`--previous-report`)
- `finalize` — OP-FIN-01: allocate the next never-reused `release-NNNN`, mint ECR `release-*` tags from recorded manifest bytes, protect the immutable frontend prefix, prepare the CT-REL manifest (per-component SBOM hashes), switch the live marker to the official identity with public verification, publish the GitHub Release with manifest + 4 SBOM assets, and audit the rollback window; exact-match resume for partial finalization (OP-FIN-02)
- `recover --snapshot <file> --changed <file> [--out <file>] [--original-failure TEXT] [--dry-run]` — automatic compensation (AD-13, OP-REC-02): restore the changed components (`auth`, `items`, `gateway`, `frontend`) from the pre-mutation production snapshot only — re-register the snapshot's exact digest-pinned task-definition revision (image-only diff, secrets stay full-ARN `secrets[].valueFrom`), update the service, bound the deployment waiter, verify observed running digests equal the snapshot digests; for the frontend, restore the live-root dist files from the snapshot release's retained immutable prefix (aggregate content checksum proven before the live switch, `index.html` last), restore the live marker from the snapshot identity last, invalidate CloudFront, and read back. Ambiguous or inconsistent snapshot internals and AWS read errors stop with evidence and never guess; the database is never touched or reversed (OP-DB-02). Writes a recovery result recording the original failure and the recovery outcome separately; a failed recovery is reported as failed, never success.
- `rollback preflight --release-id <release-NNNN> --snapshot <file> [--repository OWNER/NAME] [--schema-change present|absent] [--migration-reviewed true|false] [--previous-report FILE] --out <file> --manifest-out <file>` — read-only owner-rollback preflight (OP-REC-03), run in BOTH workflow jobs (informational before approval, authorizing after the lock): the target must be a published official GitHub Release carrying `release-manifest.json` (downloaded by the engine, never by hand), must not be the currently running release (observed from the live snapshot), and must sit in the advertised rollback window (current + the three most recent previous releases). Completeness reuses the retention window audit entry (chunk 6C): every backend ECR `release-<NNNN>` tag resolves to the manifest's exact digest, the immutable frontend prefix marker exists AND names the target identity, and the recorded `compatibilityFingerprint` matches the current runtime fingerprint (mismatch → `ERROR INCOMPATIBLE`, rejected). The fresh live-marker read must equal the snapshot and the snapshot's official release identity must agree with its marker (internally consistent live state). `--schema-change present` always fails closed — rollback never reverses database schema or data (OP-DB-02); the flag pair exists for the future additive-migration path and grants nothing today. The report's `approvalIdentity` is the SHA-256 of the byte-stable identity subset (target digests/frontend identity/fingerprint + snapshot release identity + current task-definition ARNs), compared byte-for-byte by `--previous-report` and by `execute`.
- `rollback execute --manifest <file> --snapshot <file> --preflight-report <file> --approval <file> [--workflow-run-id N] [--workflow-run-attempt N] [--repository OWNER/NAME] --out <file> [--dry-run]` — approval-gated rollback mutation (OP-REC-04): consumes the target release manifest + the fresh pre-mutation snapshot + the pre-approval report + the approval evidence; re-runs the FULL preflight first and requires the approval identity to match byte-for-byte (post-approval drift aborts before mutation), and the consumed manifest must equal the GitHub-hosted official manifest (CT-GEN-04). Deploys the COMPLETE target set from the release manifest's exact digests: backends (Auth+Items) → gateway → frontend restored from the retained immutable prefix (aggregate content checksum proven BEFORE the live switch; files copied with `index.html` last; the live marker names the official target release; CloudFront invalidated), then runs read-only production verification against the release manifest and writes a SEPARATE rollback result (requester + approver mandatory from the approval evidence — never defaulted to the run actor, from/to release identities with exact digests/checksum, workflow run/attempt, timestamps, per-component conclusions, outcome). Never creates a GitHub Release, never edits any manifest, never mints/moves ECR tags (existing digests only), never touches RDS. A failure writes the failed result first, then fails; the workflow's automatic `compensate` job restores exactly the fully-completed (`passed`) components from the pre-mutation snapshot.
- `retention audit --snapshot <file> [--repository OWNER/NAME] [--human]` — read-only four-release rollback-window audit (AD-16, OP-RET-01): lists official GitHub Releases newest-first (draft/prerelease filtered), takes the currently running release from the live production snapshot, and verifies the current + up to three previous complete releases against their official manifests — every backend ECR `release-<NNNN>` tag resolves to the manifest's exact digest (tags are retention/operator anchors, never deployment inputs), the immutable frontend prefix marker exists in production S3 AND its content is identity-equivalent to the official marker derivable from the release manifest (`PREFIX_MARKER_MISMATCH` on a wrong-content marker, distinct from absence and read errors), and each release's `compatibilityFingerprint` matches the current runtime fingerprint from the snapshot. Missing/mismatched/read-error state fails closed with distinct per-entry failure kinds (never silent drift); incomplete older sets are historical, listed but not audited, and never counted in the window. Prints a machine-readable JSON report (`--human` appends a view); exit 0 only when the window is complete and consistent.
- `retention preview --snapshot <file> [--policy FILE] [--reference-date ISO] [--repository OWNER/NAME]` — read-only lifecycle policy preview (OP-RET-03): after a successful window audit, per backend repository either compares ECR's live lifecycle preview (start + get, bounded waiter) against the local first-match-wins model — disagreement fails closed (`PREVIEW_DISAGREEMENT`) — or, when no policy is applied yet (or the applied policy differs from the resolved one), evaluates the resolved policy locally and labels the result honestly as a modeled preview. Any protected image expiring — a window release tag or any `release-*` tag inside the newest-10 keep margin — fails closed (`PROTECTED_IMAGE_EXPIRING`). The modeled path is validation only, never a replacement for a live preview at apply time.
- `retention apply --apply|--dry-run --snapshot <file> [--policy FILE] [--reference-date ISO] [--out FILE] [--repository OWNER/NAME]` — apply the desired ECR lifecycle policy to the three backend repositories (OP-RET-02/03). `--apply` is refused without `DELIVERY_RETENTION_LIVE_APPLY=1` (set explicitly by the consolidated live pass; `--dry-run` runs the full preview path only). `--reference-date` is honored only by `preview` and `apply --dry-run`; a real `--apply` rejects it fail-closed (`VALIDATION`) because ECR's lifecycle evaluator uses its own clock. The audit + preview must pass first, then each repository policy is put with an immediate byte-for-byte `get-lifecycle-policy` read-back (fail-closed drift); an already identical policy is left unchanged. A mid-loop failure writes the partial apply report (repositories processed so far plus the failed repository with its failure detail) to `--out FILE` before re-raising, so a partial application is never silent. A post-apply window audit is recorded in the report. Retention never deletes images itself: ECR lifecycle handles delayed expiration (up to 24 hours), we only configure. Frontend `_releases/` prefix retention (S3 lifecycle configuration) is a live-pass item and is not applied by this CLI.

All staging commands take `--environment staging --identifiers
scripts/config/staging-identifiers.json` (plus optional `--profile`/`--region`);
the staging identifiers shape is validated separately from production.
`staging lifecycle` and `staging apply` additionally take the required
`--repo-path DIR`: the checkout containing the reset SQL sources
(`scripts/sql/*.sql`, `Auth/init-db/*.sql`, `Items/init-db/*.sql`). The
engine is wheel-installable, so it never derives the repository location
from the source-tree layout — a missing or unreadable required SQL source
fails closed with `ERROR VALIDATION` before any staging mutation.

### Exit codes and error output

- `0` — success
- `1` — any delivery failure, printed to stderr as `ERROR <code>: <message>`
  (codes: `VALIDATION`, `READ_ERROR`, `NOT_FOUND`, `MUTATION_VERIFY`,
  `WAITER_TIMEOUT`, `AMBIGUOUS`, `NOT_IMPLEMENTED`, `STG_MARKER_CONFLICT`,
  `CLEANUP_FAILED`, `E2E_FAILED`, `OWNERLESS_STOPPED`,
  `WINDOW_INCOMPLETE`, `PROTECTED_IMAGE_EXPIRING`, `PREVIEW_DISAGREEMENT`,
  `LIVE_APPLY_REFUSED`, `POLICY_INVALID`, `POLICY_TAGPREFIX_MULTI`,
  `INCOMPATIBLE`)
- `2` — argparse usage errors

Raw `botocore.exceptions.ClientError` raised by a handler is mapped to
`ERROR READ_ERROR` (exit 1), never a traceback.

## Retention

The desired ECR lifecycle policy
(`src/delivery/retention/ecr-lifecycle-policy.json`) keeps the newest 10
`release-*` images per backend repository (rule 1, HIGHEST priority — the
immediate rollback window), expires the `sha-*`, `main-latest`, and
`branch-*` candidate families after 30 days (rules 2-4, one single-prefix
rule each), and expires untagged images after 14 days (rule 5). The
first-match-wins model treats an image whose tags match a higher-priority
rule's selection as claimed by that rule: a multi-tag release image
(`sha-*` + `release-*`) inside the newest 10 is retained by rule 1 and the
candidate rules never apply to it. ECR documents that a multi-entry
`tagPrefixList` selects only images carrying ALL listed tags, so merged
prefix lists would silently select nothing — the validator rejects them
(`POLICY_TAGPREFIX_MULTI`) and every tagged rule carries exactly one
explicit prefix.

Design decisions worth knowing:

- **Current release comes from the live snapshot.** `retention` takes a
  fresh production snapshot (`delivery snapshot production`) so the current
  release identity and the runtime configuration fingerprint are observed
  state, never assumed from GitHub order. A snapshot without an official
  release identity fails closed (`VALIDATION`).
- **Honest minimal fingerprint check.** Re-deriving a release's
  compatibility fingerprint offline is not feasible (it covers historical
  task-definition/schema state), so the audit compares each window
  release's recorded `compatibilityFingerprint` against the current runtime
  fingerprint from the snapshot and records both; a mismatch is
  `FINGERPRINT_MISMATCH` and the release is not rollback-capable.
- **Young systems.** When fewer than three previous official releases
  exist, all existing previous releases must be complete; the window is
  current + up to three previous. Releases outside the window are listed
  but never audited and never errors.
- **Reference date.** `--reference-date` controls the local model
  evaluation only (`retention preview`, and `retention apply --dry-run`);
  a real `retention apply` rejects it because ECR's live evaluator always
  evaluates at "now", so a stale reference date against an applied policy
  surfaces `PREVIEW_DISAGREEMENT` (fail-closed) near age boundaries.
- **Boundary-day drift.** The local model expires an image once the full
  age threshold has elapsed; ECR's evaluator may round differently at
  boundary instants. A disagreement near a boundary is resolved by
  re-running the preview — never by guessing.
- **Repository resolution.** `--repository OWNER/NAME` defaults to
  `$GITHUB_REPOSITORY`; GitHub reads additionally require `GITHUB_TOKEN`.
