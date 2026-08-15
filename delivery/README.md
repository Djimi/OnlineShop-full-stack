# delivery

Python delivery engine for the OnlineShop CI/CD release system, implementing
the contracts in `plans/AUTOMATIC-BUILDS-AND-DEPLOY/` (SPEC, CONTRACTS,
OPERATIONS, VERIFICATION).

The engine is strict by design: fail-closed validation, canonical JSON
serialization, exact workflow run/attempt authority, immutable record
identity, and no secrets in records.

## Layout

- `src/delivery/errors.py` — exception hierarchy with stable machine-readable codes
- `src/delivery/serialization.py` — canonical JSON and SHA-256 helpers
- `src/delivery/github.py` — exact workflow run/attempt authority, artifact/asset download, run listing, commit comparison, release publication
- `src/delivery/frontend.py` — shared candidate-frontend archive verification
- `src/delivery/live_marker.py` — production frontend live-marker model and identity rules
- `src/delivery/models/` — pydantic v2 records (candidate, staging, snapshot, release, promotion, rollback, evidence)
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
- `verify production` — read-only CT-PROD-01..04 verification (running digests, live marker, public CloudFront identity, health/items/index journeys) against an official manifest (`--manifest`) or a candidate (`--candidate`)
- `promote preflight` — read-only OP-PRO-02 preflight: candidate eligibility (AD-03/05), exact staging gate (AD-09), ECR digests, fresh production snapshot, AD-11 newer-candidate reachability + warning, OP-DB migration-ownership gate, post-approval drift comparison (`--previous-report`)
- `finalize` — OP-FIN-01: allocate the next never-reused `release-NNNN`, mint ECR `release-*` tags from recorded manifest bytes, protect the immutable frontend prefix, prepare the CT-REL manifest (per-component SBOM hashes), switch the live marker to the official identity with public verification, publish the GitHub Release with manifest + 4 SBOM assets, and audit the rollback window; exact-match resume for partial finalization (OP-FIN-02)
- `verify staging` — verify staging against a candidate (planned)
- `recover --snapshot <file> --changed <file>` — compensate changed components (planned)
- `rollback preflight --release-id <release-NNNN>|execute` — owner-approved rollback (planned)
- `retention audit|preview|apply` — ECR lifecycle retention (planned)

All staging commands take `--environment staging --identifiers
scripts/config/staging-identifiers.json` (plus optional `--profile`/`--region`);
the staging identifiers shape is validated separately from production.
`staging lifecycle` and `staging apply` additionally take the required
`--repo-path DIR`: the checkout containing the reset SQL sources
(`scripts/sql/*.sql`, `Auth/init-db/*.sql`, `Items/init-db/*.sql`). The
engine is wheel-installable, so it never derives the repository location
from the source-tree layout — a missing or unreadable required SQL source
fails closed with `ERROR VALIDATION` before any staging mutation.
Commands marked *planned* fail closed with `ERROR NOT_IMPLEMENTED` until their
mutation phase is wired in.

### Exit codes and error output

- `0` — success
- `1` — any delivery failure, printed to stderr as `ERROR <code>: <message>`
  (codes: `VALIDATION`, `READ_ERROR`, `NOT_FOUND`, `MUTATION_VERIFY`,
  `WAITER_TIMEOUT`, `AMBIGUOUS`, `NOT_IMPLEMENTED`, `STG_MARKER_CONFLICT`,
  `CLEANUP_FAILED`, `E2E_FAILED`, `OWNERLESS_STOPPED`)
- `2` — argparse usage errors

Raw `botocore.exceptions.ClientError` raised by a handler is mapped to
`ERROR READ_ERROR` (exit 1), never a traceback.