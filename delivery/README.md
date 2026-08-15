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
- `src/delivery/github.py` — exact workflow run/attempt parsers
- `src/delivery/models/` — pydantic v2 records (candidate, staging, snapshot, release, rollback, evidence)
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
- `staging lifecycle|apply` — staging operations (planned)
- `deploy backends|gateway|frontend` — deploy components (planned)
- `verify production|staging` — verify a deployed environment (planned)
- `finalize --manifest <file> --evidence-dir <dir>` — finalize an approved release (planned)
- `recover --snapshot <file> --changed <file>` — compensate changed components (planned)
- `rollback preflight --release-id <release-NNNN>|execute` — owner-approved rollback (planned)
- `retention audit|preview|apply` — ECR lifecycle retention (planned)

Commands marked *planned* fail closed with `ERROR NOT_IMPLEMENTED` until their
mutation phase is wired in.

### Exit codes and error output

- `0` — success
- `1` — any delivery failure, printed to stderr as `ERROR <code>: <message>`
  (codes: `VALIDATION`, `READ_ERROR`, `NOT_FOUND`, `MUTATION_VERIFY`,
  `WAITER_TIMEOUT`, `AMBIGUOUS`, `NOT_IMPLEMENTED`)
- `2` — argparse usage errors

Raw `botocore.exceptions.ClientError` raised by a handler is mapped to
`ERROR READ_ERROR` (exit 1), never a traceback.