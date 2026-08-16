# Code Review — 2026-08-15

Working-tree review of the new delivery system (`delivery/` package,
`.github/workflows/ci.yml` + `_java-service.yml`, plan docs in this directory's
parent). Process: one review agent (report + gates), fix agents per cluster,
one verification agent, one micro-fix agent. Legacy machinery untouched.

## Findings and resolution

### HIGH

| # | Finding | Resolution |
|---|---------|------------|
| H1 | `python -m delivery.cli` was a silent no-op (no `__main__`/guard): workflow validation/emission steps exited 0 unconditionally, publish could "succeed" with no candidate manifest | Fixed: `delivery/src/delivery/__main__.py` + `__main__` guard in `cli.py`; subprocess regression tests (`test_cli_entrypoint.py`); `if-no-files-found: error` on the manifest upload (ci.yml); README invocation corrected |
| H2 | ci.yml push triggers (`main`, `feature/**`) overlapped legacy `build-and-deploy.yml` → concurrent mutation of the same immutable `sha-*` ECR tags on next push (OP-CUT-01 violation) | Fixed: push triggers isolated to `greenfield/**` (+ ref→class mapping, tag `branch-greenfield-*` in the `branch-*` family); bring-up/cutover documented in DELIVERY-SYSTEM-IMPLEMENTATION-PLAN §3/§7 and PHASE-03 |

### MEDIUM

| # | Finding | Resolution |
|---|---------|------------|
| M1 | `running_digests` read a `tasks` field DescribeServices never returns (fabricated API shape; fakes masked it) | Fixed: real `list_tasks` → `describe_tasks` flow, fail-closed throughout; fakes model the real API |
| M2 | Rollback CLI accepted SemVer; contract allows only `release-NNNN` (OP-REC-03/AD-14) | Fixed: `--release-id` with `^release-\d{4}$`, rejection tests |
| M3 | Production snapshot hardcoded `manifestSha256=None`; no official manifest identity (CT-AUDIT-01) | Fixed: `ReleaseIdentity.status` (`official`/`none`), pattern-constrained ids, consistency validator; absence recorded honestly (GitHub-owned manifest not readable by the AWS-only flow, per CT-AUTH) |
| M4 | `frontend.artifactDigest` unconstrained (32-hex fixture vs `sha256:<64hex>` both validated) | Fixed: `^sha256:[0-9a-f]{64}$` in model + validator; fixtures canonicalized; negative tests |
| M5 | `batch_get_image_digests` normalized missing tags to absence (CT-GEN-03) | Fixed: fails closed naming the missing tags (`AbsentResourceError`) |
| M6 | New gate read the legacy workflow file to derive build contexts (legacy coupling, OP-CUT-01) | Fixed: inline `EXPECTED_BUILD_CONTEXTS` derived from Dockerfiles; zero legacy-file reads in delivery tests |

### LOW

| # | Finding | Resolution |
|---|---------|------------|
| L1 | Unused `get_secret_value` invited misuse | Fixed: removed; discipline guard test added |
| L2 | Candidate record claimed `tests.frontend: "passed"` (gate is lint+build) | Fixed: records `"lint+build"` + test |
| L3 | `deploy` required a *production* snapshot even for staging | Fixed: snapshot environment must match `--environment`; `ProductionSnapshot.environment` required; guard extended to `recover` and `rollback execute` |
| L4 | `staging reconcile` collided with OP-STG-05 semantics (ownerless-RDS job) | Fixed: renamed `staging apply`; `reconcile` left free for the future OP-STG-05 job |
| L5 | `--reference-date` accepted naive datetimes | Fixed: rejects naive and non-UTC offsets (matches `UtcDateTime`) |
| L6 | Tautological error-code test | Fixed: introspection-based invariants (format, uniqueness) |
| L7 | Snapshot health took arbitrary `deployments[0]` | Fixed: shared `primary_deployment()`; fail-closed without PRIMARY |
| L8 | Deploy-snapshot env guard missed `recover`/`rollback execute` (found in verification) | Fixed: parametrized guard over all three, fires before NOT_IMPLEMENTED stubs |
| L9 | `valid_snapshot.json` had `liveMarker`/`immutableIdentity` semantics inverted vs producer (found in verification) | Fixed: fixture matches producer; semantics pinned by test |
| L10 | Stale generated `delivery/build/`, `delivery/src/delivery.egg-info/` (found in verification) | Fixed: deleted; `*.egg-info/`/`build/` gitignored |

### Deliberately skipped

| Finding | Reason |
|---|---|
| Publish job keeps `actions: read` | Same-run artifact API download may require it; removing risks breaking publish. Read-only, job-scoped; verify at live bring-up. |
| Range-pinned deps, no lockfile | Lock strategy is a deferred delivery-infra decision; requirement stems from the superseded GREENFIELD plan, not CONTRACTS. |

## Verification

Second review agent re-examined all fixes against the normative docs: all 15
verified, no MEDIUM/HIGH regressions. Final gates:

- `pytest delivery/tests -q` → 436 passed
- `ruff check delivery` → clean
- `actionlint ci.yml _java-service.yml` → clean
- `zizmor ci.yml _java-service.yml` → no findings

Outstanding: live bring-up on a `greenfield/**` branch (push path, real ECR
mutation) remains to be proven — offline gates only, as PHASE-03 now records.
