# Production Deployer policy (desired state — Phase 5)

Boundary 3 of AD-17. This is the **desired state** document for the live pass:
the role `arn:aws:iam::799111666795:role/github-actions-production` is created
and its OIDC trust validated in the consolidated verification pass. The
greenfield workflow `promote-release-greenfield.yml` references only the role
ARN; nothing here is claimed as applied live yet.

## Scope

| Area | Grants |
|---|---|
| ECR | read (`BatchGetImage`, `DescribeImages`) + `PutImage` on the three backend repositories only |
| ECS | deploy scoped to the production cluster, the three production services, and the three task-definition families; read-only `DescribeTasks` additionally scoped to the task ARN `task/onlineshop-cluster/*` (ECS authorizes it against the task, not the cluster/service) |
| RDS | read-only `DescribeDBInstances` on the production DB instance ARN only (snapshot compatibility-fingerprint input) |
| IAM | `PassRole` to the ECS execution role and the three production task roles, with `iam:PassedToService=ecs-tasks.amazonaws.com` |
| S3 | `PutObject`/`GetObject`/`HeadObject`/`ListBucket` on the frontend bucket only |
| CloudFront | `CreateInvalidation`/`GetInvalidation`/`GetDistribution` on the production distribution only |
| ELB | read-only `DescribeLoadBalancers`/`DescribeTargetHealth` for the read-only verification journeys |

## Explicitly never granted

- no RDS mutation actions (Create/Modify/Delete/Start/Stop/Reboot/Tag/
  Snapshot/... are all absent) — the delivery engine physically cannot
  mutate the production database (AD-15 / OP-DB-02 enforcement); the only
  RDS grant is the read-only `DescribeDBInstances` scoped to the production
  DB instance ARN;
- no staging resources (cluster, services, RDS, ALB);
- no `logs:*`, no `secretsmanager:*` (secrets are only referenced as full-ARN
  `secrets[].valueFrom` entries in task definitions, never read);
- no `ecr:GetAuthorizationToken` (the engine never pulls) and no layer-upload
  actions (`InitiateLayerUpload`, `UploadLayerPart`, `CompleteLayerUpload`,
  `BatchCheckLayerAvailability`) — images are never rebuilt or pushed, only
  existing manifests are re-tagged server-side;
- `iam:PassRole` carries the `ecs-tasks.amazonaws.com` service condition and
  exact role ARNs.

## Release-tag naming (documented residual risk)

IAM has no condition key on the *name* of the tag minted by `ecr:PutImage`, so
a role holding `PutImage` could in principle mint any tag. The engine enforces
naming instead, and this is testable offline:

- `finalize` mints exactly the tag `release-NNNN` where `NNNN` is the freshly
  allocated next release id (AD-07), and only after a passed preflight under
  the production lock;
- the minted tag must resolve to the exact candidate digest (immediate
  `batch_get_image` read-back); an existing tag with a different digest fails
  closed (never overwritten);
- the candidate-build boundary never mints `release-*` tags, and the staging
  boundary has no `PutImage` at all.

## Live-pass verification checklist

1. Role exists with this policy attached (or a structurally equivalent
   inlined policy) and the OIDC trust matches the protected
   `production` environment subject.
2. `DescribeTaskDefinition` of the three production families confirms the
   task-role ARNs used in `PassRole`; update the resource list if the live
   roles differ.
3. IAM Access Analyzer policy validation passes.
