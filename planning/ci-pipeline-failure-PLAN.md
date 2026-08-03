# CI Pipeline Failure Fix Plan

## Tasks

- [x] Reproduce the failed `auth` job locally.
- [x] Update Auth tests to stub `findValidationProjectionByTokenHash`.
- [x] Reject sessions whose `createdAt` is after the current time.
- [x] Prevent pull-request builds from requesting AWS credentials or pushing images.
- [x] Replace the stale GitHub repository name in OIDC documentation.
- [x] Update the AWS role trust policy after AWS re-authentication.
- [ ] Rerun the workflow on a feature branch and verify ECR publishing.

## Remaining Issues

- [x] AWS profile `dpm-profile` was re-authenticated and the IAM update was applied.
- [x] The role trust policy now accepts the current repository's main and feature branch subjects.

## AWS Trust Policy Fix

Apply `plans/AUTOMATIC-BUILDS-AND-DEPLOY/github-actions-oidc-trust-policy.json`. It allows the GitHub OIDC audience `sts.amazonaws.com` and these subjects:

```text
repo:Djimi/OnlineShop-full-stack:ref:refs/heads/main
repo:Djimi/OnlineShop-full-stack:ref:refs/heads/feature/*
```

Use the profile and region required by the project guide, then read the role back immediately after the update.
