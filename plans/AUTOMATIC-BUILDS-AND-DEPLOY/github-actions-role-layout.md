# GitHub Actions → IAM role layout (Pass 3, subphase 3.3)

Least-privilege OIDC split by job purpose. Each AWS-facing job assumes exactly
the role whose policy it needs; validation jobs request **no** AWS credentials
and **no** repository-write permissions. All policies are source-controlled in
this directory, validated structurally by
`release/bin/../src/release_contract/iam.py` through the offline gate
`tests/scripts/ecr_release_tagging_test.sh`, and must pass IAM Access Analyzer
validation before they are applied live (consolidated Pass 3 verification
pass — never applied yet).

> **Not applied live yet.** This layout and its policies are implemented at the
> local/IaC boundary. The workflow still assumes the single
> `github-actions-onlineshop` role; it is switched to these per-purpose roles
> only after the roles are created and validated in the consolidated pass
> (IAM is deliberately out of scope for this subphase's live work).

## Roles and policies

| Purpose | Role (to create in consolidated pass) | Policy document | Grants |
|---|---|---|---|
| Candidate build (auth/items/api-gateway push) | `github-actions-candidate-build` | `github-actions-candidate-build-policy.json` | `ecr:GetAuthorizationToken` on `*` only; ECR push + read scoped to the three backend repository ARNs. Can push `sha-*`, `main-latest`, `branch-*`; must never push `latest` or `release-*`. |
| Promotion (release-tag minting) | `github-actions-promotion` | `github-actions-promotion-policy.json` | `ecr:GetAuthorizationToken` on `*` only; ECR `PutImage`/`BatchGetImage`/describe scoped to the three repository ARNs. **No layer-upload actions** — can only re-tag manifests that already exist (promote, never rebuild). |
| Production deploy (approved promotion/rollback ECS/S3/CloudFront) | `github-actions-production` | `github-actions-production-deploy-policy.json` | ECR read of the three repositories; ECS deploy scoped to the production cluster/services/task-definitions; `iam:PassRole` to `ecsTaskExecutionRole` only with `iam:PassedToService=ecs-tasks.amazonaws.com`; S3 to the frontend bucket; CloudFront invalidation to the production distribution; read-only ELB. |
| Rollback (deploy existing official digests only) | `github-actions-rollback` | `github-actions-rollback-policy.json` | Same deploy scope as production **minus `ecr:PutImage`** — rollback never mints tags or writes images; it reads the official `release-*` digests and deploys them. |
| Publication (GitHub Release) | none (GitHub token) | — | `contents: write` only; no AWS credentials. |
| Validation (frontend, e2e-pr, test-only steps) | none | — | No AWS credentials; job-level `permissions` without `id-token: write`; `contents: read` for checkout. |

## OIDC trust policy

`github-actions-oidc-trust-policy.json` requires `sts.amazonaws.com` and scopes
subjects to:

```
repo:Djimi@8793507/OnlineShop-full-stack@1097550215:ref:refs/heads/main
repo:Djimi@8793507/OnlineShop-full-stack@1097550215:ref:refs/heads/feature/*
repo:Djimi@8793507/OnlineShop-full-stack@1097550215:environment:production
```

The `:environment:production` subject (GitHub OIDC environment subject,
immutable-format `repo:owner@owner-id/repo@repo-id`) is added for the protected
production job (subphase 3.4/3.6). **The exact `sub` is validated live, never
guessed**: decode the JWT from a production-environment job's
`ACTIONS_ID_TOKEN_REQUEST_URL` token before relying on the trust policy in the
consolidated pass.

## Least-privilege invariants enforced by the offline gate

- `ecr:GetAuthorizationToken` is the only ECR action on `Resource: "*"`.
- Every other ECR action targets only the three backend repository ARNs.
- `iam:PassRole` targets the ECS execution/task role ARNs only and carries
  `StringEquals iam:PassedToService=ecs-tasks.amazonaws.com`.
- Mutating actions never use `Resource: "*"`.
- The promotion policy has no layer-upload actions
  (`ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`,
  `ecr:CompleteLayerUpload`, `ecr:BatchCheckLayerAvailability`).
- The rollback policy has no `ecr:PutImage`.
- The candidate-build policy has no `ecs:`/`s3:`/`cloudfront:`/
  `elasticloadbalancing:`/`rds:` actions.
- The production/rollback policies never mint or push images.

## Residual risk (documented, controlled elsewhere)

IAM cannot restrict the image-tag *prefix* of `ecr:PutImage` (there is no
condition key on tag names), so any role holding `ecr:PutImage` could in
principle mint a `release-*` tag. The controls are: the promotion script
`promote-image-digest.sh` fails closed unless the digest matches the recorded
evidence; the build workflow only ever computes `sha-*`/`main-latest`/`branch-*`
tags; and the repositories are `IMMUTABLE_WITH_EXCLUSION` so an existing
`release-*` tag can never be overwritten.
