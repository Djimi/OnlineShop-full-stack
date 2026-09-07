# Phase 2 — Build the `delivery/` Python Package

**Plan:** `DELIVERY-SYSTEM-IMPLEMENTATION-PLAN.md` §2
**Status:** ✅ Complete (3 chunks implemented, 1 review round, all findings resolved)

## What this phase was about

The delivery engine itself — a strict Python + Boto3 package that replaces the old
shell-script release framework. Everything later (workflows, staging, promotion,
rollback) is a thin wrapper around this package.

```text
delivery/  (src layout, ~310 unit tests)
│
├── models/      candidate · staging · snapshot · release · rollback · evidence
├── validation/  schema + cross-identity checks (fail-closed)
├── serialization  canonical JSON (deterministic bytes → stable SHA-256)
├── github.py      exact run/attempt authority (rejects hostile shapes)
├── aws/           ECR · ECS · RDS · S3 · CloudFront · Secrets
│                  every mutation → immediate checked read-back
└── cli.py         19 commands (exit 0/1/2, ERROR <code> output)
```

## The core ideas (one line each)

| Idea | Why it matters |
|---|---|
| **One record = one authority** | The candidate manifest IS the candidate; ECR digests are the bytes; nothing else counts |
| **Fail-closed everywhere** | A failed AWS read is an ERROR (`READ_ERROR`), never "absent" — absence and failure are different classes |
| **Every mutation must be proven** | `mutate_and_read_back()`: change → read → compare → else `MUTATION_VERIFY` error |
| **Bounded everything** | Waiters have timeouts; retries only for throttling; no blind mutation retries |
| **No secrets, no shell** | Secrets are ARNs only; untrusted input is validated data, never executed (0 hits for `subprocess`/`eval` in the package) |

## How a flow looks through the package

```text
snapshot production (read-only):
  identity preflight (account 799111666795, eu-north-1)
    → describe 3 ECS services → deployment ids + RUNNING task digests
    → read frontend live marker + checksum from S3
    → describe RDS (engine/version/class) → sanitized config fingerprint
    → validate record internally → write canonical JSON
    → anything unverifiable = exit 1, no snapshot file
```

## What the review caught (and was fixed)

| Severity | Finding | Fix |
|---|---|---|
| MEDIUM | Snapshot never validated internally | `validate()` before writing; test: empty running digests → rejected |
| MEDIUM | ECS reads leaked raw ClientError → traceback | Wrapped → `READ_ERROR`; tests for throttle/absent/error |
| MEDIUM | Identifiers JSON types partially unchecked | All 10 keys type-checked (accountId `\d{12}`, services, repos); 14-case test |
| LOW ×6 | run/attempt accepted 0/negative; hostile branch names; stale README; raw ClientError in CLI; `fromReleaseId` pattern; caches committable | All fixed (`PositiveInt`, charset-rejecting branch parser, `delivery/.gitignore`, etc.) |

## Verification

- 308 tests green (`pytest delivery/tests`), `ruff check delivery` clean.
- 0 tracked files outside `delivery/` touched; nothing committed.

## What's next

**Phase 3 — CI + candidate publication**: `ci.yml` + `_java-service.yml` workflows that
validate and publish complete candidates (one SHA → 4 artifacts → manifest owned by the
exact run/attempt), SBOMs, and the offline gates (actionlint, zizmor, shellcheck).