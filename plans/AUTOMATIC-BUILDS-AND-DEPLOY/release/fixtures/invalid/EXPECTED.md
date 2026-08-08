# Invalid fixtures — expected primary error codes

Each file in this directory is a release manifest that must be **rejected** by
the validator. The table below records the primary error code that the
validation test asserts is present in the issue list, and why the document is
rejected. This file is authoritative: `test_validation.py` parses this table
and asserts every fixture fails with its documented code, so the documentation
and the tests cannot drift.

| Fixture | Primary code | Reason |
|---|---|---|
| `malformed-semver.json` | `INVALID_FORMAT` | `release.version` is not `MAJOR.MINOR.PATCH` |
| `semver-prerelease.json` | `INVALID_FORMAT` | version carries a prerelease suffix (`-rc.1`) |
| `semver-build-metadata.json` | `INVALID_FORMAT` | version carries build metadata (`+build.5`) |
| `semver-leading-zero.json` | `INVALID_FORMAT` | version component has a leading zero (`1.02.3`) |
| `semver-leading-v.json` | `INVALID_FORMAT` | version has an optional leading `v` |
| `unsafe-input-characters.json` | `UNSAFE_CHARACTER` | a string contains a NUL control character (`\u0000`) |
| `unsupported-schema-version.json` | `CONST_MISMATCH` | `schemaVersion` is `2`, not the supported `1` |
| `abbreviated-sha.json` | `INVALID_FORMAT` | `release.sourceSha` is abbreviated (8 hex chars) |
| `invalid-sha-chars.json` | `INVALID_FORMAT` | `release.sourceSha` contains a non-hex character |
| `missing-component-field.json` | `MISSING_FIELD` | `components.auth.sbom` is absent |
| `missing-component.json` | `MISSING_FIELD` | `components.items` is absent |
| `missing-release-field.json` | `MISSING_FIELD` | `release.sourceSha` is absent |
| `unknown-component.json` | `EXTRA_FIELD` | unknown `components.orders` entry |
| `cross-field-identity-mismatch.json` | `IDENTITY_MISMATCH` | `auth/9.9.9` does not agree with version `1.2.1` |
| `cross-field-sha-mismatch.json` | `SHA_MISMATCH` | `items.sourceSha` differs from `release.sourceSha` |
| `cross-field-common-sha-mismatch.json` | `SHA_MISMATCH` | `items.commonSourceSha` differs from `release.sourceSha` |
| `cross-field-repository-mismatch.json` | `REPOSITORY_MISMATCH` | `auth.repository` is not the canonical `onlineshop-auth` |
| `cross-field-git-tag-mismatch.json` | `GIT_TAG_MISMATCH` | `release.gitTag` is not `v1.2.1` |
| `cross-field-candidate-tag-mismatch.json` | `CANDIDATE_TAG_MISMATCH` | `apiGateway.candidateTag` is not `sha-<sourceSha>` |
| `cross-field-release-tag-mismatch.json` | `RELEASE_TAG_MISMATCH` | `auth.releaseTag` is not `release-1.2.1` |
| `cross-field-prefix-mismatch.json` | `RELEASE_PREFIX_MISMATCH` | `frontend.releasePrefix` is not `_releases/v1.2.1/` |
| `cross-field-artifact-mismatch.json` | `CONST_MISMATCH` | `frontend.artifact` is not the canonical `frontend-dist.tar.gz` |
| `digest-error.json` | `INVALID_FORMAT` | `auth.imageDigest` is not `sha256:<64 hex>` |
| `digest-wrong-algorithm.json` | `INVALID_FORMAT` | `auth.imageDigest` uses a wrong algorithm prefix |
| `checksum-error.json` | `INVALID_FORMAT` | `frontend.sha256` is not 64 hex chars |
| `candidate-with-promotion-workflow.json` | `EXTRA_FIELD` | a candidate records `promotionWorkflow` (forbidden) |
| `candidate-with-task-definition.json` | `EXTRA_FIELD` | a candidate records `auth.taskDefinitionArn` (forbidden) |
| `official-without-promotion-workflow.json` | `MISSING_FIELD` | an official manifest omits `release.promotionWorkflow` |
| `official-without-task-definition.json` | `MISSING_FIELD` | an official manifest omits `auth.taskDefinitionArn` |
| `unknown-status.json` | `INVALID_ENUM_VALUE` | `release.status` is not `candidate` or `official` |
| `bad-timestamp.json` | `INVALID_FORMAT` | `createdAt` is not RFC 3339 UTC with a trailing `Z` |
| `bad-url.json` | `INVALID_FORMAT` | `candidateWorkflow.url` is not an HTTP(S) URL |
| `invalid-run-id.json` | `INVALID_TYPE` | `candidateWorkflow.runId` is a string, not a positive integer |
| `candidate-non-push-event.json` | `CONST_MISMATCH` | candidate evidence event is not `push` |
| `candidate-non-main-ref.json` | `CONST_MISMATCH` | candidate evidence ref is not `refs/heads/main` |
| `candidate-workflow-failure.json` | `CONST_MISMATCH` | candidate workflow conclusion is not `success` |
| `staging-validation-failure.json` | `CONST_MISMATCH` | staging validation conclusion is not `success` |
