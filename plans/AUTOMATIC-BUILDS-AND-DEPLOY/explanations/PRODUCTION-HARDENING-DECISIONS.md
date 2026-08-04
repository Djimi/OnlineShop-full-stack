# Production Hardening Decisions (Pass 3, subphase 3.5)

This document records the explicit v1 tradeoffs and limitations accepted by
subphase 3.5 production hardening. It is **not** a claim of live AWS state —
the hardening tooling is implemented and offline-tested; live read-back and
mutations are applied in the consolidated Pass 3 verification pass.

---

## 1. Fargate Spot is the explicit v1 cost tradeoff — this is not HA

Production backend services run on Fargate **Spot** with **desired count 1**
(`capacityProviderStrategy=FARGATE_SPOT,weight=1`). This is a deliberate cost
choice: Spot is ~60% cheaper than On-Demand and is what keeps the playground
near its cost target. The consequence is explicit and must never be glossed
over:

- **desired count 1 + Spot is not a high-availability SLA.** AWS can reclaim a
  Spot task with as little as a 2-minute warning. During reclaim the service
  has **zero** capacity until ECS launches a replacement on a Spot-capable
  instance. A single-task Spot service tolerates crashes and reboots but not
  instant zero-downtime failover.
- The safe rolling deployment parameters
  (`minimumHealthyPercent=100`, `maximumPercent=200`, deployment circuit
  breaker with rollback) protect **deployments** from shipping a bad revision;
  they do **not** protect against Spot capacity reclaim.
- `desired count 1` means `minimumHealthyPercent=100` still allows the
  deployment model ECS uses: during a rolling update ECS starts the replacement
  (running 1 + pending 1 within the 200% ceiling) before stopping the old task,
  so a healthy task is serving throughout the rollout.

**Decision:** accept Spot + desired-1 for v1; keep the circuit breaker and
safe rolling parameters; document that this is not an HA SLA. Raising to
desired count ≥2 on On-Demand (or enabling capacity providers with a base of
On-Demand) is the documented path to an HA claim.

---

## 2. Backup limitation — no schema-changing release before Flyway

The **current** production database backup posture is a known limitation:

- Production RDS automated backups and the restore procedure have **not been
  validated end-to-end** for the production instance (the staging lifecycle
  deliberately creates and deletes a fresh RDS per run with
  `backup-retention-period 0`).
- **Application rollback is not database rollback** (Pass 3, Decision 8).
  ECS/frontend rollback changes task definitions and static content only.
- There is **no versioned database migration tool** in the codebase today.
  Schemas are applied by `Auth/init-db/*` and `Items/init-db/*` during staging
  bootstrap and were applied manually to production during Pass 1.

**Gate:** **no schema-changing production release may be promoted** until:

1. a versioned migration tool such as **Flyway** (or an equivalent with the
   same guarantees) is adopted and owns the production schema,
2. forward/backward-compatible migration rules are defined and reviewed,
3. a tested backup/restore and recovery procedure exists for the production RDS
   instance, and
4. the release workflow never improvises SQL against production.

SQL execution against private RDS must go through the sanctioned
`scripts/ecs-run-sql.sh` path (Secrets Manager injection, `--verify` read-back,
one-off helper task-definition cleanup) — never ad-hoc psql from a runner.

---

## 3. Frontend delivery — S3 REST origin + CloudFront OAC (not applied yet)

The v1 frontend is served from a public S3 **website** origin through
CloudFront. Subphase 3.5 replaces that model:

- **Target:** S3 **REST** origin (`onlineshop-frontend-799111666795.s3.
  eu-north-1.amazonaws.com`) + CloudFront **Origin Access Control**; the bucket
  public access block fully enabled; the bucket policy restricted to the
  `cloudfront.amazonaws.com` service principal with
  `aws:SourceArn = arn:aws:cloudfront::799111666795:distribution/EPS8MI3FV3B7X`;
  the S3 website configuration removed; SPA fallback preserved through the
  distribution's 404 → 200 `/index.html` custom error response.
- **Constraint check:** CloudFront is a global service with a single global
  endpoint (`cloudfront.amazonaws.com`, signing region `us-east-1`), so the
  mandatory `--profile dpm-profile --region eu-north-1` flags are harmless on
  CloudFront commands (the CLI still signs for `us-east-1`).
- **Not applied in 3.5.** The migration tool `scripts/migrate-frontend-oac.sh`
  is implemented and offline-tested (`--dry-run` plans, `--apply` mutates with
  an immediate read-back after every step and fails closed). It runs in the
  consolidated verification pass **after** `scripts/verify-frontend-oac.sh`
  passes and the current public-read state is confirmed. Two outage-safety
  properties are enforced by the tool itself: (1) a **no-lockout precondition
  gate** — `--apply` refuses to start unless the current bucket policy already
  grants public read or the CloudFront OAC, so switching the origin to a
  private REST endpoint can never lose CloudFront access; and (2) the apply
  waits (bounded) for the **asynchronous CloudFront deployment** to reach
  `Deployed` before tightening the bucket policy, so success is not claimed
  while the edge still serves the old origin. If a live constraint blocks the
  migration, the explicit v1 exception is recorded here with the compensating
  control (CloudFront custom error response + `aws:SourceArn` policy
  verification remain mandatory before any release).

---

## 4. Secrets and task definitions

- Production task definitions inject credentials **only** through
  `secrets[].valueFrom` with **full** Secrets Manager ARNs (the
  `:json-key::` selector form requires the full ARN). No password ever appears
  in `environment` or `command`. Subphase 3.4's `sanitize-task-definition.sh`
  proves the digest-pin transform changes only the `image` field and keeps
  secrets in `valueFrom`.
- Execution role and task role duties stay separate (execution role
  `ecsTaskExecutionRole` for image pull + Secrets Manager read; application
  task roles scoped to the services' own needs).
- Release task definitions are digest-pinned (`@sha256:...`), use `awsvpc`,
  Fargate, named Service Connect port mappings, `awslogs`, container health
  checks, positive `stopTimeout`, and `versionConsistency=enabled`. Validation:
  `release/bin/validate-task-definition.sh`.

---

## 5. Audit correlation

CloudTrail management events must be logged by a multi-region trail so global
IAM/CloudFront events are captured; delivery is proven by a configured target
**and** a confirmed `LatestDeliveryTime` with no delivery error
(`scripts/verify-cloudtrail-coverage.sh`, read-only). Management-event
selectors cover *all* control-plane APIs — they are not a per-service
enumeration. Sanitized AWS request IDs from `run-task`/`update-service`/
`register-task-definition`/`put-object`/`create-invalidation` calls are
retained with the GitHub evidence during promotion so the two audit planes can
be correlated; capturing those request IDs is a promotion-phase behaviour, not
part of this read-only audit.
