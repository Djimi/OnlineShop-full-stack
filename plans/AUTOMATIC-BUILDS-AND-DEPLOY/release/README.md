# OnlineShop Release Contract — Local Validation Foundation

Subphase **3.1** of [03_RELEASE_TRACEABILITY.md](../03_RELEASE_TRACEABILITY.md).
This directory is the source-controlled release contract consumed by every later
phase (candidate evidence, promotion, rollback, traceability, retention). It
contains the versioned manifest JSON Schema, a deterministic local validator,
valid/invalid fixtures, strict shell input helpers, automated tests, and this
documentation.

> **Security rule (from the plan):** security-sensitive JSON is never parsed
> with regex or ad-hoc shell string concatenation. All manifest parsing and
> validation is performed by the Python validator (`json.load` + the pinned
> `jsonschema` engine). Shell scripts only pass validated values through
> argument arrays (`"$@"` / `ARGS=("$manifest" ...)`) or environment variables.

---

## Layout

| Path | What it is | Source-controlled |
|---|---|---|
| `schema/release-manifest.schema.json` | Versioned JSON Schema (Draft-07) encoding the manifest contract and the candidate/official state rules | ✅ yes |
| `fixtures/valid/*.json` | Manifest documents the validator must accept | ✅ yes |
| `fixtures/invalid/*.json` | Manifest documents the validator must reject, one per failure category | ✅ yes |
| `fixtures/invalid/EXPECTED.md` | Authoritative fixture → primary error code table (the tests parse it) | ✅ yes |
| `src/release_contract/*.py` | Python validator: schema engine, cross-field rules, deterministic error normalization | ✅ yes |
| `bin/validate-manifest.sh` | Shell CLI wrapper over the Python validator (argv-only, strict input checks) | ✅ yes |
| `bin/release-input.sh` | Strict input-validation helpers for dispatch inputs (SemVer, SHA, URL, login, …) | ✅ yes |
| `tests/*.py` | Python unit + validation tests (stdlib `unittest`) | ✅ yes |
| `requirements.txt` | Pinned Python dependencies (`jsonschema`) | ✅ yes |
| `../../tests/scripts/release_contract_test.sh` | Repo-level verification gate (runs everything above) | ✅ yes |

**Ephemeral workflow output — never source-controlled:** candidate evidence
bundles, generated manifests, SBOMs, frontend archives, checksum files, and
GitHub artifact IDs are produced by subphase 3.2+ workflows and retained in
GitHub Actions artifacts / S3, not committed here.

---

## The manifest contract

One canonical SemVer (for example `1.2.1`) identifies one monorepo commit and
all four deployable components (`auth`, `items`, `apiGateway`, `frontend`).
The full 40-character monorepo SHA and ECR/frontend SHA-256 digests are
authoritative; SemVer and mutable tags are never deployment inputs.

The schema enforces (in addition to per-field formats):

- **Atomic identity (Decision 1):** `release.gitTag == "v" + version`, every
  component `sourceSha` and `items.commonSourceSha` equals
  `release.sourceSha`, each `identity == "<component>/<version>"`, backend
  `repository` matches the canonical `onlineshop-<service>` map, and
  `candidateTag` / `releaseTag` / `releasePrefix` are deterministic derivations
  of the SHA/version.
- **Two states (Decisions 5/6):** a `candidate` manifest must not contain
  `release.promotionWorkflow` or any backend `taskDefinitionArn`; an `official`
  manifest requires both. Only the promotion workflow (subphase 3.4) may
  convert a validated candidate to official.
- **No silent fields:** `additionalProperties: false` at every level. Adding a
  field requires bumping `schemaVersion` and updating fixtures, lookup tools,
  and this documentation.
- **Unsafe input rejection:** strings containing control characters (including
  escaped `\u0000`) are rejected before any schema validation.

---

## Validating a manifest

```bash
RELEASE=plans/AUTOMATIC-BUILDS-AND-DEPLOY/release

# Shell wrapper (recommended in workflows)
bash "$RELEASE/bin/validate-manifest.sh" manifest.json --human

# Or direct Python CLI
PYTHONPATH="$RELEASE/src" python3 -m release_contract.cli manifest.json
```

The validator prints machine-readable JSON to stdout:

```json
{
  "valid": false,
  "file": "manifest.json",
  "schemaVersion": 1,
  "issues": [
    {
      "code": "INVALID_FORMAT",
      "field": "release.version",
      "message": "invalid value '1.2'; canonical MAJOR.MINOR.PATCH, ..."
    }
  ],
  "checksum": "b5d66951...",
  "errorCount": 1
}
```

- Exit `0` = valid, `1` = invalid, `2` = usage/IO error.
- Error `code` + `field` are stable machine identifiers; `message` is
  human-readable. Output is deterministic for a given document.
- `--check-checksum <sha256>` verifies the canonical manifest checksum and
  fails when the document was altered.
- `checksum` is the SHA-256 of the canonical (sorted-key) JSON encoding, so it
  is independent of file formatting but detects any content change.

### Manifest checksums

`src/release_contract/checksums.py` provides:

- `sha256_file(path)` — generic file digest (frontend archives, SBOMs).
- `manifest_checksum(obj)` — deterministic checksum of a manifest object.
- `manifest_checksum_file(path)` — parse + checksum in one step.

### Component/repository mapping

`src/release_contract/components.py` is the single source of truth for
`identity_for`, `repository_for`, `candidate_tag_for`, `release_tag_for`,
`git_tag_for`, and `release_prefix_for`. Later subphases (promotion, rollback,
retention) must use these helpers rather than re-deriving tag names.

### Dispatch inputs (subphase 3.1 rule)

`bin/release-input.sh` validates every dispatch input **before** use:
`rl_assert_semver`, `rl_assert_full_sha`, `rl_assert_sha256_hex`,
`rl_assert_positive_integer`, `rl_assert_github_login`, `rl_assert_http_url`,
`rl_assert_regular_file`. Validated inputs are passed downstream only as
environment variables or argument-array entries, never interpolated into shell,
JSON, GitHub CLI, or AWS CLI command strings.

---

## Prerequisites and running the tests

The Python validator requires Python 3.10+ (the type-alias syntax in
`crossrules.py` is evaluated at import time) and the pinned `jsonschema`
package; the verification gate additionally uses `jq` (preinstalled on GitHub
`ubuntu-latest` runners):

```bash
python3 -m pip install -r requirements.txt   # jsonschema==4.26.0
```

Run the complete verification gate (Python tests, CLI fixture checks,
determinism, checksum guard, shell input helpers, and optional lint):

```bash
bash tests/scripts/release_contract_test.sh
```

Optional linters (run automatically when present):
- `shellcheck` — installable as `pip install shellcheck-py` (bundles the
  `shellcheck` binary) or from https://www.shellcheck.net/
- `ruff` — `pip install ruff` (Python format/lint)

If a linter is absent the gate reports it explicitly instead of silently
passing; install it and re-run to satisfy the full gate.
