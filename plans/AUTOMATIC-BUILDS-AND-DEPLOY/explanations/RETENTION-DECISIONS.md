# Retention and Rollback-Window Decisions (Pass 3, subphase 3.8)

This document records the v1 retention contract, the ECR lifecycle semantics it
relies on, and the explicit limitations accepted by subphase 3.8. It is
**not** a claim of live AWS state — the desired policy, the decision layer, and
the audit/preview/apply tooling are implemented and offline-tested; live policy
preview/apply/read-back and the live read-only retention audit belong to the
consolidated Pass 3 verification pass (`apply-retention-policy.sh --apply`
requires `ONLINESHOP_RETENTION_LIVE_APPLY=1`, set only by that pass).

---

## 1. The retention contract

| Store | Artifact | Retention |
|---|---|---|
| ECR (per backend repo) | `release-*` images | newest **10** (immediate rollback window) |
| ECR (per backend repo) | `sha-*` candidate images | ~30 days after push |
| ECR (per backend repo) | `main-latest` / `branch-*` convenience tags | ~30 days after push |
| ECR (per backend repo) | untagged images | 14-day grace period |
| S3 frontend | `_releases/v<version>/` prefixes | newest 10 versions; never the currently deployed or previous known-good |
| GitHub | Release, final manifest, SBOMs, checksums, sanitized audit/test evidence | indefinite |
| GitHub Actions | candidate-only artifacts | 30 days |
| GitHub Actions | staging-failure diagnostics, snapshot/result records | 14 days (operational) |

The ECR side is expressed as one desired lifecycle policy,
`release/ecr/lifecycle-policy.json`, applied identically to all three backend
repositories:

1. `rulePriority 1` — keep the newest 10 `release-*` images
   (`tagged` + `tagPrefixList: ["release-"]` + `imageCountMoreThan 10`).
2. `rulePriority 2` — expire `sha-*` candidates 30 days after push.
3. `rulePriority 3` — expire the mutable `main-latest` convenience tag 30 days
   after push.
4. `rulePriority 4` — expire mutable `branch-*` convenience tags 30 days after
   push.
5. `rulePriority 5` — expire untagged images after 14 days.

The repositories stay `IMMUTABLE_WITH_EXCLUSION` (subphase 3.3): `sha-*` and
`release-*` tags can never be overwritten, `latest` stays absent, and the only
mutable tags are the convenience exclusions the 30-day rule expires.

---

## 2. ECR lifecycle evaluator semantics (verified against the AWS user guide)

The desired policy and the `release_contract.retention` evaluation model encode
ECR's documented behavior:

- **An image is expired by exactly one or zero rules.** All rules are evaluated
  and then applied by priority; an image that matches a higher-priority rule's
  tagging requirements can **never** be expired by a lower-priority rule — even
  when the higher-priority rule *keeps* the image.
- **First-match-wins is the protection.** An official digest carries both
  `sha-<sha>` and `release-<version>`. The keep-10 rule (priority 1) claims
  every `release-*` image, so a retained release image inside the newest 10 is
  never selected by the 30-day candidate rules — regardless of how old it is.
  This is why the keep-10 rule MUST have the highest priority; the decision
  layer rejects any policy where it is not first
  (`POLICY_RELEASE_RULE_NOT_FIRST`).
- **There is no negative/exclusion filter.** ECR's schema requires every
  `tagStatus: tagged` rule to carry an explicit `tagPrefixList`/`tagPatternList`
  (a bare `tagged` + `sinceImagePushed` rule is rejected by the service), so
  "expire everything except `release-*`" is **not expressible**. The candidate
  families (`sha-`, `main-latest`, `branch-`) must be enumerated, and the
  protection of official images comes purely from rule ordering. Any tag
  outside the declared families is retained (fail-safe): the policy never
  guesses.
- **Every tagged rule selects exactly one tag prefix.** AWS documents that a
  multi-entry `tagPrefixList`/`tagPatternList` selects only images carrying
  **all** the listed tags ("If you specify multiple tags, only the images with
  all specified tags are selected" — lifecycle policy properties; the user
  guide's worked example "multiple tag patterns on a single rule" shows an
  image matching only one of two patterns is NOT selected). A merged
  `["main-latest", "branch-"]` rule would therefore select no real image (none
  carries both a `main-latest*` and a `branch-*` tag), silently disabling the
  30-day convenience-tag expiry while the model's any-match selection would
  predict expirations ECR never performs. The v1 policy therefore splits each
  family into its own single-prefix rule (2, 3, 4) — unambiguous under the
  documented semantics under either reading — and the validator rejects any
  merged multi-prefix tagged rule (`POLICY_TAGPREFIX_MULTI`).
- **Only one rule may select untagged images**, and tag prefixes must be unique
  across rules — both enforced by the decision layer
  (`POLICY_UNTAGGED_RULE_COUNT`, `POLICY_PREFIX_OVERLAP`).
- **Expiration is ordered by `imagePushedAt`**, older images first; the model
  and the evaluator fixtures use an explicit reference date so the offline
  preview is deterministic.

## 3. Delayed evaluation

ECR applies a lifecycle policy through its periodic evaluator: after
`put-lifecycle-policy`, the first evaluation can take **up to 24 hours** to
run, and subsequent evaluations run on ECR's own schedule. Consequences,
all reflected in the tooling:

- `preview-retention-policy.sh` uses ECR's **preview** APIs
  (`start-lifecycle-policy-preview` + `get-lifecycle-policy-preview`), which
  evaluate the policy text immediately WITHOUT deleting anything — the preview
  is a dry-run and never a mutation. The offline gate proves the live preview
  path issues exactly those read-only calls.
- The read-only retention audit checks the **current** state and never assumes
  an immediate policy effect (`delayedEvaluation` is part of its output).
- After `put-lifecycle-policy`, `apply-retention-policy.sh` reads the policy
  back immediately (`get-lifecycle-policy`) and compares it byte-for-byte
  against the desired document — exit 0 of `put` is not proof.

## 4. Manifest-list and referrer behavior

Lifecycle expiration operates on image manifests. An image referenced by a
**manifest list** (multi-architecture index) cannot be expired without the
manifest list being deleted first, and reference artifacts (OCI referrers such
as attestations/SBOMs attached to an image) are automatically expired within
24 hours of the subject image's deletion. The v1 policy therefore expires
top-level manifests only; child manifests and referrers are not selected and
remain (they die with their subject). The gate does not claim otherwise.

## 5. Keep-10 by push order vs. the rollback window by version order

ECR's `imageCountMoreThan` keep-10 protects the 10 most recently **pushed**
`release-*` images, while the 3.6 rollback window and the 3.8 audit are the 10
newest **versions**. With in-order promotion (3.4) the two sets coincide. An
out-of-order push (e.g. a backport re-pushing an older version) can push a
window release outside the 10 most recent pushes; the audit's coverage check
(`POLICY_WINDOW_GAP`) fails closed in that case instead of silently letting
the policy expire a window release. The dedicated fixture
(`window-observed-gap.json`) models exactly that backport scenario.

## 6. Untagged grace period (14 days)

Untagged images appear only after a tag deletion or after a rule expired an
image's only tag. 14 days is short enough to keep the repository from
accumulating orphaned manifests and long enough for a human to re-tag a
wrongly untagged image. It is deliberately shorter than the 30-day candidate
window because untagged images are by definition not referenced by any tag.

## 7. Frontend prefix retention

S3 has **no primitive** for "keep the prefixes of the currently deployed and
previous known-good releases" — a prefix-based S3 lifecycle rule cannot know
what the live root points at. The v1 design therefore does **not** automate
frontend prefix deletion: `release_contract.retention frontend-retention` is a
review-gated decision that marks exactly the prefixes outside the newest-10
window (and outside the current/known-good pair) as expirable and fails closed
on any protected deletion (`FRONTEND_PROTECTED_DELETE`) or unknown prefix
(`FRONTEND_UNKNOWN_PREFIX`). GitHub Release assets (the `frontend-dist.tar.gz`
archive with its checksum) remain the long-term source after the immediate
S3/ECR window expires, so an expirable prefix is recoverable from GitHub
before deletion.

## 8. GitHub retention classes

GitHub Releases, the final release manifests, SBOMs, checksums, and sanitized
audit/test evidence are retained indefinitely (they are the long-term store).
Candidate-only artifacts are configured for 30 days and staging-failure
diagnostics plus the promotion/rollback snapshot and result records for 14
days — the exact `retention-days` values already present in
`build-and-deploy.yml`, `promote-release.yml`, and `rollback-release.yml`,
which the gate asserts statically. No live GitHub setting was changed in 3.8.

## 9. What 3.8 does NOT do (explicitly deferred to the consolidated live pass)

- Apply the lifecycle policy to real ECR repositories
  (`apply-retention-policy.sh --apply` refuses offline; the live pass sets
  `ONLINESHOP_RETENTION_LIVE_APPLY=1` and every `put-lifecycle-policy` is
  followed by an immediate `get-lifecycle-policy` read-back).
- Run the live read-only retention audit against real production state
  (`audit-retention-window.sh` without `--observed`).
- Run the live ECR preview against real repositories.
- Delete any S3 frontend prefix or GitHub artifact.
