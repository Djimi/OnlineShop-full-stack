"""Controlled staging-to-production promotion decisions (Pass 3, subphase 3.4).

Subphase 3.4 is the approved, approval-gated promotion of one verified monorepo
snapshot from staging to production. The plan's rules are encoded here as pure,
fixture-tested decision functions; shell wrappers gather live state (GitHub
runs/compare via ``gh``, ECS/ECR/S3/ALB via ``aws``) and pass validated JSON
files into this module. Nothing security-sensitive is parsed with regex or
ad-hoc shell string concatenation.

The decisions cover:

- ``dispatch_issues``            — dispatch inputs (version + candidate run id)
                                   before any AWS/GitHub mutation.
- ``run_evidence_issues``        — the selected run is a successful ``push`` on
                                   ``refs/heads/main`` at the exact SHA with a
                                   successful cloud staging E2E job.
- ``ancestry_issues``            — the candidate SHA is a descendant of the last
                                   official release and is reachable from the
                                   current ``main`` (Decision 9, monotonic).
- ``preflight_issues``           — the combined read-only preflight: schema-valid
                                   manifest, run evidence, ancestry, staging
                                   gate, release-name uniqueness, and the
                                   Decision 8 database-change review.
- ``snapshot_issues``            — the pre-promotion snapshot captures every
                                   field needed for compensation/resume.
- ``deployment_plan_issues``     — deploy ordering (auth+items, then
                                   api-gateway, then frontend) plus the safe
                                   rolling / circuit-breaker parameters.
- ``waiter_verified``            — a deployment is bound to the task-definition/
                                   deployment started by this run, is COMPLETED,
                                   and is running the exact digests.
- ``frontend_publication_issues``— assets-first/index-last publication, no
                                   ``--delete``, per-release immutable prefix.
- ``verification_issues``        — running digests, service task-definition ARNs,
                                   frontend version marker/checksum, and ALB
                                   target health all match the manifest.
- ``finalization_decision``      — only after production verification: mint the
                                   three ``release-<version>`` tags and publish
                                   ``v<version>``; idempotently resumable, any
                                   collision or missing verification fails closed.
- ``compensation_steps``         — on a later-component failure, the exact
                                   reverse-order restore plan to the snapshot.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from typing import Any

from . import components as rc
from .semver import compare as compare_semver
from .semver import is_valid as is_valid_semver
from .validate import validate_data

# Canonical production service name -> manifest component key. The ECS services
# are named after the microservice (auth/items/api-gateway); the manifest keys
# use apiGateway for the gateway.
_SERVICE_TO_COMPONENT = {
    "onlineshop-auth": "auth",
    "onlineshop-items": "items",
    "onlineshop-api-gateway": "apiGateway",
}

# ECS container name -> manifest component key (containers are named after the
# microservice: auth, items, api-gateway).
_CONTAINER_TO_KEY = {
    "auth": "auth",
    "items": "items",
    "api-gateway": "apiGateway",
    "apiGateway": "apiGateway",
}

# Canonical deploy order (Decision 1 / 3.4 deploy ordering): backends first
# (auth + items), then the gateway (owns the ALB target group), then the
# frontend (assets-first/index-last).
DEPLOY_ORDER = ("auth", "items", "apiGateway", "frontend")

# Reverse deploy order: the compensation order for changed components.
COMPENSATION_ORDER = tuple(reversed(DEPLOY_ORDER))


def _issue(code: str, field: str, message: str) -> dict[str, str]:
    return {"code": code, "field": field, "message": message}


@dataclass
class Decision:
    """Result of a promotion decision.

    ``valid`` is False when any issue exists. Some decisions carry an ``action``
    (e.g. ``publish``/``resume``/``fail-closed``) for the caller to branch on;
    an issue always means fail closed regardless of the action. ``steps``
    carries the ordered restore steps on a compensation decision.
    """

    valid: bool
    action: str = ""
    issues: list[dict[str, str]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the machine-readable CLI output shape."""
        output: dict[str, Any] = {
            "valid": self.valid,
            "action": self.action,
            "issues": self.issues,
        }
        if self.steps:
            output["steps"] = self.steps
        return output


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and value >= 1:
        return value
    if isinstance(value, str) and value.isdigit() and int(value) >= 1:
        return int(value)
    return None


# ---------------------------------------------------------------------------
# Dispatch input validation
# ---------------------------------------------------------------------------


def dispatch_issues(version: Any, run_id: Any) -> Decision:
    """Validate the manual promotion dispatch inputs.

    ``version`` must be canonical v1 SemVer; ``run_id`` must be a positive
    integer naming a successful candidate run. An image tag or digest is never
    accepted as an input (Decision 2: SHA and digest are authoritative and are
    never typed by hand).
    """
    issues: list[dict[str, str]] = []
    if not is_valid_semver(version):
        issues.append(_issue("INVALID_VERSION", "version", f"invalid canonical SemVer {version!r}"))
    if _positive_int(run_id) is None:
        issues.append(
            _issue(
                "INVALID_RUN_ID",
                "runId",
                f"candidate run id must be a positive integer, got {run_id!r}",
            )
        )
    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Candidate run evidence
# ---------------------------------------------------------------------------


def run_evidence_issues(run: Any, source_sha: Any) -> Decision:
    """Confirm the selected GitHub Actions run produced the candidate.

    ``run`` is a GitHub run record:
    ``{runId, runAttempt, url, event, ref, headSha, conclusion,
      jobs: {<job-name>: <conclusion>, ...}}``. The run must be a successful
    ``push`` on ``refs/heads/main`` at the exact ``source_sha``, and must
    include a successful ``e2e-staging`` job (the staging gate). Any mismatch
    fails closed — a rerun or a different run can never be substituted.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(run, dict):
        return Decision(False, "", [_issue("RUN_MISSING", "run", "no run record was provided")])

    run_id = _positive_int(run.get("runId"))
    run_attempt = _positive_int(run.get("runAttempt"))
    if run_id is None or run_attempt is None:
        issues.append(
            _issue(
                "RUN_IDENTITY_MISSING",
                "run",
                f"run must carry a positive runId/runAttempt, got "
                f"{run.get('runId')!r}/{run.get('runAttempt')!r}",
            )
        )

    if run.get("event") != rc.TRUSTED_EVENT:
        issues.append(
            _issue(
                "RUN_EVENT_MISMATCH",
                "run.event",
                f"candidate run event {run.get('event')!r} must be {rc.TRUSTED_EVENT!r}",
            )
        )
    if run.get("ref") != rc.TRUSTED_REF:
        issues.append(
            _issue(
                "RUN_REF_MISMATCH",
                "run.ref",
                f"candidate run ref {run.get('ref')!r} must be {rc.TRUSTED_REF!r}",
            )
        )
    head_sha = run.get("headSha")
    if not isinstance(source_sha, str) or len(source_sha) != 40:
        issues.append(_issue("INVALID_SHA", "sourceSha", f"invalid source SHA {source_sha!r}"))
    elif head_sha != source_sha:
        issues.append(
            _issue(
                "RUN_SHA_MISMATCH",
                "run.headSha",
                f"run head {head_sha!r} does not match the candidate SHA {source_sha!r}",
            )
        )
    if run.get("conclusion") != "success":
        issues.append(
            _issue(
                "RUN_UNSUCCESSFUL",
                "run.conclusion",
                f"candidate run conclusion {run.get('conclusion')!r} must be success",
            )
        )

    jobs = run.get("jobs") if isinstance(run, dict) else None
    if not isinstance(jobs, dict):
        issues.append(
            _issue("RUN_JOBS_MISSING", "run.jobs", "run record must carry its job conclusions")
        )
        jobs = {}
    staging = jobs.get("e2e-staging")
    if staging != "success":
        issues.append(
            _issue(
                "RUN_STAGING_UNSUCCESSFUL",
                "run.jobs.e2e-staging",
                f"cloud staging E2E job conclusion {staging!r} must be success "
                "(the staging gate is the successful Pass 2 staging run)",
            )
        )

    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Ancestry / monotonic promotion
# ---------------------------------------------------------------------------


def ancestry_issues(ancestry: Any) -> Decision:
    """Verify the candidate is newer than the last official release.

    ``ancestry``:
    ``{lastOfficialVersion, lastOfficialSha, candidateSha,
      descendantOfOfficial: {status, aheadBy, behindBy},
      reachableFromMain: {status, aheadBy, behindBy}}``.
    ``descendantOfOfficial`` is the GitHub compare of
    ``<last-official-sha>...<candidate-sha>``; ``reachableFromMain`` the compare
    of ``main...<candidate-sha>``. The candidate must be a strict descendant of
    the last official release (``behindBy == 0``, ``aheadBy >= 1``), must be
    reachable from the current ``main`` (``behindBy == 0``), and must be a
    strictly higher SemVer (Decision 9). When there is no previous official
    release, the ancestry is vacuous and only reachability applies.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(ancestry, dict):
        return Decision(False, "", [_issue("ANCESTRY_MISSING", "ancestry", "no ancestry record")])

    last_version = ancestry.get("lastOfficialVersion")
    last_sha = ancestry.get("lastOfficialSha")
    candidate_sha = ancestry.get("candidateSha")
    if candidate_sha and (not isinstance(candidate_sha, str) or len(candidate_sha) != 40):
        issues.append(_issue("INVALID_SHA", "ancestry.candidateSha", "invalid candidate SHA"))

    descendant = ancestry.get("descendantOfOfficial")
    if last_version is not None or last_sha is not None:
        if not isinstance(descendant, dict):
            issues.append(
                _issue(
                    "DESCENDANT_MISSING",
                    "ancestry.descendantOfOfficial",
                    "no compare of the last official release to the candidate was provided",
                )
            )
        else:
            behind = descendant.get("behindBy")
            ahead = descendant.get("aheadBy")
            if behind != 0:
                issues.append(
                    _issue(
                        "CANDIDATE_BEHIND_OFFICIAL",
                        "ancestry.descendantOfOfficial",
                        f"candidate is behind the last official release by {behind} commit(s); "
                        "promoting an older candidate is rejected (Decision 9)",
                    )
                )
            elif ahead < 1:
                issues.append(
                    _issue(
                        "CANDIDATE_IS_OFFICIAL",
                        "ancestry.descendantOfOfficial",
                        "candidate SHA equals the last official release; monotonic "
                        "promotion requires strictly newer bytes",
                    )
                )
        if (
            is_valid_semver(last_version)
            and is_valid_semver(ancestry.get("candidateVersion"))
            and compare_semver(ancestry["candidateVersion"], last_version) <= 0
        ):
            issues.append(
                _issue(
                    "VERSION_NOT_INCREASING",
                    "ancestry.candidateVersion",
                    f"candidate version {ancestry['candidateVersion']!r} must be strictly "
                    f"newer than the last official {last_version!r} (Decision 9)",
                )
            )

    main_compare = ancestry.get("reachableFromMain")
    if not isinstance(main_compare, dict):
        issues.append(
            _issue(
                "REACHABLE_MISSING",
                "ancestry.reachableFromMain",
                "no compare of main to the candidate was provided",
            )
        )
    elif main_compare.get("behindBy") != 0:
        issues.append(
            _issue(
                "CANDIDATE_NOT_ON_MAIN",
                "ancestry.reachableFromMain",
                f"candidate is {main_compare.get('behindBy')} commit(s) behind the current "
                "main; the candidate must be reachable from main",
            )
        )

    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Combined preflight
# ---------------------------------------------------------------------------


def preflight_issues(manifest: Any, observed: Any) -> Decision:
    """The combined read-only preflight run before any AWS mutation.

    ``observed``:
    ``{run: {..}, ancestry: {..}, identity: {action, issues},
      databaseChange: {present, migrationReviewed}}``.
    Requires: schema-valid manifest; valid SemVer + run evidence; ancestry;
    staging gate success; release identity free/resumable; and the Decision 8
    database-change review gate. In the workflow this runs in the approved
    ``promote`` job after the ``production`` Environment approval and lock
    acquisition (time-of-check race closure); the caller decides when the run
    authorizes mutation. A read-only pre-approval job validates only the
    dispatch inputs and the candidate manifest contract.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(observed, dict):
        return Decision(False, "", [_issue("OBSERVED_MISSING", "observed", "no observed state")])

    if not isinstance(manifest, dict):
        issues.append(_issue("MANIFEST_MISSING", "manifest", "no manifest was provided"))
        return Decision(False, "", issues)
    result = validate_data(manifest)
    if not result.valid:
        issues.append(
            _issue(
                "MANIFEST_INVALID",
                "$",
                f"manifest failed schema validation: {json.dumps(result.issues)}",
            )
        )
        return Decision(False, "", issues)

    version = manifest.get("release", {}).get("version")
    source_sha = manifest.get("release", {}).get("sourceSha")

    dispatch = dispatch_issues(version, observed.get("run", {}).get("runId"))
    issues.extend(dispatch.issues)

    run = run_evidence_issues(observed.get("run"), source_sha)
    issues.extend(run.issues)

    ancestry = ancestry_issues(observed.get("ancestry"))
    issues.extend(ancestry.issues)

    staging = manifest.get("release", {}).get("stagingValidation", {})
    if staging.get("job") != "e2e-staging" or staging.get("conclusion") != "success":
        issues.append(
            _issue(
                "STAGING_GATE_MISSING",
                "release.stagingValidation",
                "the manifest must record a successful e2e-staging staging gate",
            )
        )

    identity = observed.get("identity")
    if isinstance(identity, dict):
        if identity.get("action") not in ("proceed", "resume"):
            issues.append(
                _issue(
                    "RELEASE_IDENTITY_BLOCKED",
                    "identity.action",
                    f"release identity action {identity.get('action')!r} must be proceed or resume",
                )
            )
        for identity_issue in identity.get("issues", []):
            if isinstance(identity_issue, dict) and identity_issue.get("code"):
                issues.append(dict(identity_issue))
    else:
        issues.append(
            _issue(
                "IDENTITY_CHECK_MISSING",
                "identity",
                "the release-identity collision check result is required",
            )
        )

    db_change = observed.get("databaseChange")
    if (
        isinstance(db_change, dict)
        and db_change.get("present")
        and db_change.get("migrationReviewed") is not True
    ):
        issues.append(
            _issue(
                "SCHEMA_CHANGE_UNREVIEWED",
                "databaseChange",
                "the candidate includes a database/schema change but the Decision 8 "
                "migration review has not been recorded; schema-changing releases "
                "are blocked until a reviewed forward/backward-compatible migration "
                "and recovery procedure exist",
            )
        )

    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Pre-promotion snapshot
# ---------------------------------------------------------------------------


def snapshot_issues(snapshot: Any, manifest: Any) -> Decision:
    """Validate the pre-promotion snapshot required for compensation/resume.

    ``snapshot``:
    ``{paused: bool, services: {<service>: {desiredCount, capacityProviderStrategy,
      taskDefinitionArn, runningDigest, loadBalancers, deployments}},
      frontend: {marker, indexSha256}, officialRelease: {version, gitTag, sourceSha}}``.
    Missing required fields fail closed so a compensation/resume run can never
    guess the pre-operation state.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(snapshot, dict):
        return Decision(False, "", [_issue("SNAPSHOT_MISSING", "snapshot", "no snapshot provided")])

    services = snapshot.get("services")
    if not isinstance(services, dict):
        issues.append(
            _issue("SNAPSHOT_SERVICES_MISSING", "snapshot.services", "services map is required")
        )
        services = {}

    expected_services = list(_SERVICE_TO_COMPONENT)
    for service in expected_services:
        entry = services.get(service)
        if not isinstance(entry, dict):
            issues.append(
                _issue(
                    "SNAPSHOT_SERVICE_MISSING",
                    f"snapshot.services.{service}",
                    f"pre-promotion snapshot must record service {service}",
                )
            )
            continue
        for required in (
            "desiredCount",
            "capacityProviderStrategy",
            "taskDefinitionArn",
            "runningDigest",
            "deployments",
        ):
            if required not in entry or entry[required] in (None, [], ""):
                issues.append(
                    _issue(
                        "SNAPSHOT_MISSING_FIELD",
                        f"snapshot.services.{service}.{required}",
                        f"pre-promotion snapshot must record {service}.{required}",
                    )
                )
        # The ALB wiring is only meaningful on the gateway (auth/items use
        # Service Connect, not the load balancer), but the field must be present
        # as a list so the snapshot is complete and can prove no drift.
        if "loadBalancers" not in entry or not isinstance(entry["loadBalancers"], list):
            issues.append(
                _issue(
                    "SNAPSHOT_MISSING_FIELD",
                    f"snapshot.services.{service}.loadBalancers",
                    f"pre-promotion snapshot must record {service}.loadBalancers",
                )
            )

    frontend = snapshot.get("frontend")
    if not isinstance(frontend, dict):
        issues.append(
            _issue("SNAPSHOT_FRONTEND_MISSING", "snapshot.frontend", "frontend state is required")
        )
        frontend = {}
    for required in ("marker", "indexSha256"):
        if required not in frontend or frontend[required] in (None, "", {}):
            issues.append(
                _issue(
                    "SNAPSHOT_MISSING_FIELD",
                    f"snapshot.frontend.{required}",
                    f"pre-promotion snapshot must record frontend.{required}",
                )
            )

    official = snapshot.get("officialRelease")
    if not isinstance(official, dict) or not official.get("version"):
        issues.append(
            _issue(
                "SNAPSHOT_OFFICIAL_MISSING",
                "snapshot.officialRelease",
                "the current official release identity must be recorded for resume",
            )
        )

    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Deployment plan and waiters
# ---------------------------------------------------------------------------


def deployment_plan_issues(plan: Any) -> Decision:
    """Validate the deployment plan ordering and safe-rolling parameters.

    ``plan``: ``{components: [..], circuitBreaker: {enable, rollback},
    minimumHealthyPercent, maximumPercent}``. Order must be auth+items first,
    then api-gateway, then frontend (backends before the ALB gateway before the
    frontend). Circuit breaker must be enabled with rollback,
    ``minimumHealthyPercent == 100`` and ``maximumPercent == 200``.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(plan, dict):
        return Decision(False, "", [_issue("PLAN_MISSING", "plan", "no deployment plan")])

    components = plan.get("components")
    if not isinstance(components, list):
        issues.append(
            _issue("PLAN_COMPONENTS_MISSING", "plan.components", "components order is required")
        )
        components = []
    ordered = [c for c in components if c in DEPLOY_ORDER]
    if set(ordered) != set(DEPLOY_ORDER):
        issues.append(
            _issue(
                "PLAN_COMPONENT_SET",
                "plan.components",
                f"plan must cover exactly {list(DEPLOY_ORDER)}",
            )
        )
    else:
        # Backends (auth, items) must come before the gateway; gateway before
        # frontend. Enforce the canonical relative order.
        position = {component: index for index, component in enumerate(components)}
        if position.get("auth", 0) > position.get("apiGateway", 0):
            issues.append(
                _issue(
                    "PLAN_ORDER_INVALID",
                    "plan.components",
                    "auth must deploy before api-gateway",
                )
            )
        if position.get("items", 0) > position.get("apiGateway", 0):
            issues.append(
                _issue(
                    "PLAN_ORDER_INVALID",
                    "plan.components",
                    "items must deploy before api-gateway",
                )
            )
        if position.get("apiGateway", 0) > position.get("frontend", 0):
            issues.append(
                _issue(
                    "PLAN_ORDER_INVALID",
                    "plan.components",
                    "api-gateway must deploy before frontend",
                )
            )

    breaker = plan.get("circuitBreaker")
    if not isinstance(breaker, dict):
        issues.append(
            _issue(
                "CIRCUIT_BREAKER_DISABLED",
                "plan.circuitBreaker",
                "deployment circuit breaker must be configured",
            )
        )
    else:
        if breaker.get("enable") is not True:
            issues.append(
                _issue(
                    "CIRCUIT_BREAKER_DISABLED",
                    "plan.circuitBreaker.enable",
                    "deployment circuit breaker must be enabled",
                )
            )
        if breaker.get("rollback") is not True:
            issues.append(
                _issue(
                    "ROLLBACK_DISABLED",
                    "plan.circuitBreaker.rollback",
                    "deployment circuit breaker rollback must be enabled",
                )
            )
    if plan.get("minimumHealthyPercent") != 100:
        issues.append(
            _issue(
                "MIN_HEALTHY_PERCENT",
                "plan.minimumHealthyPercent",
                "minimumHealthyPercent must be 100",
            )
        )
    if plan.get("maximumPercent") != 200:
        issues.append(
            _issue(
                "MAX_PERCENT",
                "plan.maximumPercent",
                "maximumPercent must be 200",
            )
        )

    return Decision(not issues, "", issues)


def waiter_verified(waiter: Any, expected: Any) -> Decision:
    """Verify one component's deployment is the one started by this run.

    ``waiter``: ``{component, deploymentId, taskDefinitionArn, rolloutState,
    runningDigest}`` (the deployment observed for the component). ``expected``:
    ``{component, deploymentId, taskDefinitionArn, imageDigest}`` — the
    deployment this run started and the digest from the validated manifest. A
    generically stable service or a circuit-breaker rollback is not success: the
    deployment must be bound to the exact deployment/task-definition started by
    this run, be ``COMPLETED``, and be running the exact digest.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(waiter, dict) or not isinstance(expected, dict):
        return Decision(False, "", [_issue("WAITER_MISSING", "waiter", "waiter state missing")])

    component = waiter.get("component") or expected.get("component")
    if waiter.get("deploymentId") != expected.get("deploymentId"):
        issues.append(
            _issue(
                "DEPLOYMENT_ID_MISMATCH",
                f"waiter.{component}.deploymentId",
                f"deployment {waiter.get('deploymentId')!r} is not the deployment this run "
                f"started ({expected.get('deploymentId')!r}); a pre-existing or rolled-back "
                "deployment is never success",
            )
        )
    if waiter.get("taskDefinitionArn") != expected.get("taskDefinitionArn"):
        issues.append(
            _issue(
                "WAITER_TD_MISMATCH",
                f"waiter.{component}.taskDefinitionArn",
                f"waiter task definition {waiter.get('taskDefinitionArn')!r} does not match "
                f"the release task definition {expected.get('taskDefinitionArn')!r}",
            )
        )
    if waiter.get("rolloutState") != "COMPLETED":
        issues.append(
            _issue(
                "DEPLOYMENT_NOT_COMPLETED",
                f"waiter.{component}.rolloutState",
                f"deployment rollout state {waiter.get('rolloutState')!r} must be COMPLETED",
            )
        )
    if waiter.get("runningDigest") != expected.get("imageDigest"):
        issues.append(
            _issue(
                "WAITER_DIGEST_MISMATCH",
                f"waiter.{component}.runningDigest",
                f"running digest {waiter.get('runningDigest')!r} does not match the release "
                f"digest {expected.get('imageDigest')!r}",
            )
        )
    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Frontend publication
# ---------------------------------------------------------------------------


def frontend_publication_issues(plan: Any) -> Decision:
    """Validate the frontend publication ordering (assets-first/index-last).

    ``plan``: ``{steps: [..], deleteFlag: bool, immutablePrefix, marker,
    indexHtml}``. The live root publish must not use ``--delete`` (old hashed
    assets are preserved), the immutable release prefix must be uploaded first
    (rollback source), and the root ``release.json`` marker + ``index.html``
    must be published last. A SPA entry-path invalidation is required.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(plan, dict):
        return Decision(
            False, "", [_issue("FRONTEND_PLAN_MISSING", "frontendPlan", "no frontend plan")]
        )

    if plan.get("deleteFlag"):
        issues.append(
            _issue(
                "FRONTEND_DELETE_FORBIDDEN",
                "frontendPlan.deleteFlag",
                "live root publication must not use --delete; old hashed assets are retained",
            )
        )
    if plan.get("immutablePrefix") is False or plan.get("immutablePrefix") in (None, ""):
        issues.append(
            _issue(
                "FRONTEND_PREFIX_MISSING",
                "frontendPlan.immutablePrefix",
                "assets must be published to the immutable release prefix first (rollback source)",
            )
        )

    steps = plan.get("steps")
    if not isinstance(steps, list):
        issues.append(
            _issue(
                "FRONTEND_STEPS_MISSING",
                "frontendPlan.steps",
                "an ordered publication step list is required",
            )
        )
        steps = []
    if "invalidate" not in steps and "invalidate" not in " ".join(str(s) for s in steps):
        issues.append(
            _issue(
                "FRONTEND_INVALIDATION_MISSING",
                "frontendPlan.steps",
                "a CloudFront invalidation of the SPA entry paths is required",
            )
        )
    # assets-first/index-last: the root release.json/index.html step must come
    # strictly after the content-addressed live-assets step.
    marker_index = next((i for i, s in enumerate(steps) if str(s) == "live-marker-index"), None)
    assets_index = next((i for i, s in enumerate(steps) if str(s) == "live-assets"), None)
    if marker_index is not None and (assets_index is None or assets_index > marker_index):
        issues.append(
            _issue(
                "FRONTEND_ORDER_INVALID",
                "frontendPlan.steps",
                "root release.json/index.html must be published after the content-addressed "
                "assets (assets-first/index-last)",
            )
        )

    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Post-deploy verification
# ---------------------------------------------------------------------------


def verification_issues(observed: Any, manifest: Any) -> Decision:
    """Verify production before publication.

    ``observed``: ``{running: [tasks with containers[].imageDigest],
    services: {service: {taskDefinition}}, frontend: {liveMarker},
    alb: {targetHealth}}``. All three backends must be running the exact
    manifest digests on the exact release task definitions, the frontend live
    marker must match the manifest, and the ALB target must be healthy.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(observed, dict) or not isinstance(manifest, dict):
        return Decision(
            False, "", [_issue("VERIFY_MISSING", "verify", "verification state missing")]
        )

    components = manifest.get("components", {})
    running = observed.get("running")
    if not isinstance(running, list) or not running:
        issues.append(
            _issue(
                "RUNNING_TASKS_MISSING",
                "verify.running",
                "no running production tasks are observable; a paused environment "
                "cannot be verified as a successful deployment",
            )
        )
        running = []

    # Collapse running containers into component -> digest (same rule as the
    # traceability running lookup; a mixed digest set fails closed).
    digest_by_component: dict[str, set[str]] = {}
    for task in running:
        if not isinstance(task, dict):
            continue
        for container in task.get("containers") or []:
            if not isinstance(container, dict):
                continue
            key = _CONTAINER_TO_KEY.get(container.get("name"))
            digest = container.get("imageDigest")
            if key is not None and isinstance(digest, str):
                digest_by_component.setdefault(key, set()).add(digest)
    for key, digests in digest_by_component.items():
        if len(digests) > 1:
            issues.append(
                _issue(
                    "RUNNING_MIXED_DIGESTS",
                    f"verify.running.{key}",
                    f"running tasks report more than one digest for {key}: {sorted(digests)!r}",
                )
            )
    expected_digests = {
        key: components.get(key, {}).get("imageDigest")
        for key in rc.BACKEND_KEYS
        if isinstance(components.get(key), dict)
    }
    for key, expected in expected_digests.items():
        running_set = digest_by_component.get(key)
        if not running_set:
            issues.append(
                _issue(
                    "RUNNING_DIGEST_MISSING",
                    f"verify.running.{key}",
                    f"no running container digest observed for {key}",
                )
            )
        elif expected not in running_set:
            issues.append(
                _issue(
                    "RUNNING_DIGEST_MISMATCH",
                    f"verify.running.{key}",
                    f"running digests {sorted(running_set)!r} do not include the release "
                    f"digest {expected!r}",
                )
            )

    services = observed.get("services")
    if isinstance(services, dict):
        for service, expected_component in _SERVICE_TO_COMPONENT.items():
            expected_td = components.get(expected_component, {}).get("taskDefinitionArn")
            observed_td = (
                services.get(service, {}).get("taskDefinition")
                if isinstance(services.get(service), dict)
                else None
            )
            if expected_td and observed_td and observed_td != expected_td:
                issues.append(
                    _issue(
                        "SERVICE_TD_MISMATCH",
                        f"verify.services.{service}",
                        f"service {service} uses task definition {observed_td}, expected "
                        f"{expected_td}",
                    )
                )
            elif expected_td and not observed_td:
                issues.append(
                    _issue(
                        "SERVICE_TD_MISSING",
                        f"verify.services.{service}",
                        f"service {service} task definition could not be read",
                    )
                )

    live = observed.get("frontend", {}).get("liveMarker")
    if isinstance(live, dict) and live.get("exists"):
        marker = live.get("marker")
        frontend = components.get("frontend", {})
        expected_marker = {
            "version": manifest.get("release", {}).get("version"),
            "sourceSha": manifest.get("release", {}).get("sourceSha"),
            "frontendSha256": frontend.get("sha256"),
        }
        if not isinstance(marker, dict) or any(
            marker.get(key) != value for key, value in expected_marker.items()
        ):
            issues.append(
                _issue(
                    "FRONTEND_MARKER_MISMATCH",
                    "verify.frontend.liveMarker",
                    f"deployed frontend marker {marker!r} does not match the manifest "
                    f"{expected_marker!r}",
                )
            )
    else:
        issues.append(
            _issue(
                "FRONTEND_MARKER_MISSING",
                "verify.frontend.liveMarker",
                "the deployed frontend release.json marker could not be read",
            )
        )

    alb = observed.get("alb", {})
    target_health = alb.get("targetHealth")
    if isinstance(target_health, list):
        if not any(
            isinstance(t, dict) and t.get("targetHealth", {}).get("state") == "healthy"
            for t in target_health
        ):
            issues.append(
                _issue(
                    "ALB_UNHEALTHY",
                    "verify.alb.targetHealth",
                    f"no healthy ALB target observed: {target_health!r}",
                )
            )
    else:
        issues.append(
            _issue(
                "ALB_TARGET_HEALTH_MISSING",
                "verify.alb.targetHealth",
                "ALB target health must be read back before publication",
            )
        )

    return Decision(not issues, "", issues)


# ---------------------------------------------------------------------------
# Finalization (release tags + GitHub Release publication)
# ---------------------------------------------------------------------------


def finalization_decision(state: Any) -> Decision:
    """Decide whether to publish the official release identity.

    ``state``: ``{productionVerified: bool, ecr: {<repo>: {releaseDigest}},
    frontendPrefix: {markerExists, marker}, gitTag: {exists, sha},
    manifest: <official manifest>}``.

    Rules (Decision 6 / 3.4):
    - the official GitHub Release is created/published only after the exact
      approved artifacts are healthy in production (``productionVerified``);
    - the three ``release-<version>`` tags must either be absent (mint) or
      already resolve to the recorded digests (idempotent resume of an
      interrupted finalization); any other digest fails closed;
    - a GitHub ``v<version>`` tag must be absent or already point at the
      manifest's source SHA (resume); any other SHA fails closed;
    - the frontend prefix marker must be absent or match the manifest.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(state, dict) or not isinstance(state.get("manifest"), dict):
        return Decision(
            False, "", [_issue("FINALIZE_MISSING", "finalize", "finalize state missing")]
        )

    manifest = state["manifest"]
    release = manifest.get("release", {})
    components = manifest.get("components", {})
    version = release.get("version")
    source_sha = release.get("sourceSha")

    if state.get("productionVerified") is not True:
        issues.append(
            _issue(
                "PUBLICATION_BEFORE_VERIFICATION",
                "finalize.productionVerified",
                "the official release may only be published after the exact approved "
                "artifacts are healthy in production (Decision 6)",
            )
        )

    ecr = state.get("ecr")
    if not isinstance(ecr, dict):
        issues.append(
            _issue(
                "FINALIZE_ECR_MISSING",
                "finalize.ecr",
                "per-repository ECR release-tag state required",
            )
        )
        ecr = {}
    for key in rc.BACKEND_KEYS:
        component = components.get(key, {})
        repository = component.get("repository")
        expected_digest = component.get("imageDigest")
        release_tag = component.get("releaseTag")
        repo_state = ecr.get(repository)
        release_digest = repo_state.get("releaseDigest") if isinstance(repo_state, dict) else None
        if release_digest is not None and release_digest != expected_digest:
            issues.append(
                _issue(
                    "RELEASE_TAG_CONFLICT",
                    f"finalize.ecr.{repository}.{release_tag}",
                    f"release tag {release_tag} already resolves to {release_digest}, "
                    f"expected {expected_digest}; immutable tags are never overwritten",
                )
            )

    git = state.get("gitTag")
    if isinstance(git, dict) and git.get("exists") and git.get("sha") != source_sha:
        issues.append(
            _issue(
                "GIT_TAG_CONFLICT",
                "finalize.gitTag",
                f"git tag v{version} already exists at {git.get('sha')!r}, expected {source_sha!r}",
            )
        )

    prefix = state.get("frontendPrefix")
    if isinstance(prefix, dict) and prefix.get("markerExists"):
        marker = prefix.get("marker")
        expected_marker = {
            "version": version,
            "sourceSha": source_sha,
            "frontendSha256": components.get("frontend", {}).get("sha256"),
        }
        if not isinstance(marker, dict) or any(
            marker.get(key) != value for key, value in expected_marker.items()
        ):
            issues.append(
                _issue(
                    "FRONTEND_PREFIX_CONFLICT",
                    "finalize.frontendPrefix",
                    f"immutable frontend prefix marker {marker!r} does not match the manifest "
                    f"{expected_marker!r}",
                )
            )

    if issues:
        return Decision(False, "fail-closed", issues)

    anything_exists = (
        any(
            isinstance(repo_state, dict) and repo_state.get("releaseDigest") is not None
            for repo_state in (
                ecr.get(components.get(k, {}).get("repository")) for k in rc.BACKEND_KEYS
            )
        )
        or bool(isinstance(git, dict) and git.get("exists"))
        or bool(isinstance(prefix, dict) and prefix.get("markerExists"))
    )
    return Decision(True, "resume" if anything_exists else "publish", [])


# ---------------------------------------------------------------------------
# Compensation
# ---------------------------------------------------------------------------


def compensation_steps(snapshot: Any, changed: Any) -> Decision:
    """Build the reverse-order compensation plan for changed components.

    ``changed`` is a list of component keys already mutated (in deploy order).
    On a later failure the changed components are compensated to the exact
    pre-promotion snapshot in reverse deploy order (frontend first). Any
    changed component whose snapshot lacks the fields needed to restore it
    fails closed — a component is never left unreachable.
    """
    issues: list[dict[str, str]] = []
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("services"), dict):
        return Decision(
            False,
            "",
            [_issue("SNAPSHOT_MISSING", "snapshot", "a full snapshot is required to compensate")],
        )
    if not isinstance(changed, list):
        return Decision(
            False, "", [_issue("CHANGED_MISSING", "changed", "changed components list required")]
        )

    services = snapshot["services"]
    steps: list[dict[str, Any]] = []
    for component in COMPENSATION_ORDER:
        if component not in changed:
            continue
        if component == "frontend":
            frontend = snapshot.get("frontend", {})
            if not frontend.get("marker"):
                issues.append(
                    _issue(
                        "COMPENSATE_FRONTEND_UNRESTORABLE",
                        "compensation.frontend",
                        "snapshot has no frontend marker to restore",
                    )
                )
                continue
            steps.append(
                {
                    "component": "frontend",
                    "action": "restore",
                    "restore": {
                        "marker": frontend["marker"],
                        "indexSha256": frontend.get("indexSha256"),
                    },
                }
            )
            continue
        service = next(
            (name for name, key in _SERVICE_TO_COMPONENT.items() if key == component), None
        )
        entry = services.get(service) if service else None
        if not isinstance(entry, dict) or not entry.get("taskDefinitionArn"):
            issues.append(
                _issue(
                    "COMPENSATE_SERVICE_UNRESTORABLE",
                    f"compensation.{component}",
                    f"snapshot has no task definition to restore for {component}",
                )
            )
            continue
        steps.append(
            {
                "component": component,
                "action": "restore",
                "restore": {
                    "taskDefinitionArn": entry["taskDefinitionArn"],
                    "desiredCount": entry.get("desiredCount"),
                    "capacityProviderStrategy": entry.get("capacityProviderStrategy"),
                    "runningDigest": entry.get("runningDigest"),
                },
            }
        )
    return Decision(not issues, "", issues, steps)


def _print_json(value: Any) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _emit(decision: Decision) -> int:
    _print_json(decision.to_dict())
    return 0 if decision.valid else 1


def _cmd_dispatch(args: argparse.Namespace) -> int:
    return _emit(dispatch_issues(args.version, args.run_id))


def _cmd_run(args: argparse.Namespace) -> int:
    run = _read_json(args.run)
    return _emit(run_evidence_issues(run, args.source_sha))


def _cmd_ancestry(args: argparse.Namespace) -> int:
    return _emit(ancestry_issues(_read_json(args.ancestry)))


def _cmd_preflight(args: argparse.Namespace) -> int:
    return _emit(preflight_issues(_read_json(args.manifest), _read_json(args.observed)))


def _cmd_snapshot(args: argparse.Namespace) -> int:
    return _emit(snapshot_issues(_read_json(args.snapshot), _read_json(args.manifest)))


def _cmd_plan(args: argparse.Namespace) -> int:
    return _emit(deployment_plan_issues(_read_json(args.plan)))


def _cmd_waiter(args: argparse.Namespace) -> int:
    return _emit(waiter_verified(_read_json(args.waiter), _read_json(args.expected)))


def _cmd_frontend(args: argparse.Namespace) -> int:
    return _emit(frontend_publication_issues(_read_json(args.plan)))


def _cmd_verify(args: argparse.Namespace) -> int:
    return _emit(verification_issues(_read_json(args.observed), _read_json(args.manifest)))


def _cmd_finalize(args: argparse.Namespace) -> int:
    return _emit(finalization_decision(_read_json(args.state)))


def _cmd_compensate(args: argparse.Namespace) -> int:
    return _emit(compensation_steps(_read_json(args.snapshot), _read_json(args.changed)))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release_contract.promotion",
        description="Controlled staging-to-production promotion decisions (Pass 3, subphase 3.4).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    dispatch = sub.add_parser("dispatch", help="validate the manual promotion dispatch inputs")
    dispatch.add_argument("--version", required=True, metavar="SEMVER")
    dispatch.add_argument("--run-id", required=True, metavar="INT")
    dispatch.set_defaults(func=_cmd_dispatch)

    run = sub.add_parser("run", help="verify the candidate run evidence")
    run.add_argument("--run", required=True, metavar="FILE", help="GitHub run record JSON file")
    run.add_argument("--source-sha", required=True, metavar="SHA", help="candidate full commit SHA")
    run.set_defaults(func=_cmd_run)

    ancestry = sub.add_parser("ancestry", help="verify monotonic ancestry of the candidate")
    ancestry.add_argument(
        "--ancestry", required=True, metavar="FILE", help="ancestry comparison JSON file"
    )
    ancestry.set_defaults(func=_cmd_ancestry)

    preflight = sub.add_parser("preflight", help="combined read-only promotion preflight")
    preflight.add_argument("--manifest", required=True, metavar="FILE")
    preflight.add_argument("--observed", required=True, metavar="FILE")
    preflight.set_defaults(func=_cmd_preflight)

    snapshot = sub.add_parser("snapshot", help="validate the pre-promotion snapshot")
    snapshot.add_argument("--snapshot", required=True, metavar="FILE")
    snapshot.add_argument("--manifest", required=True, metavar="FILE")
    snapshot.set_defaults(func=_cmd_snapshot)

    plan = sub.add_parser("plan", help="validate the deployment plan")
    plan.add_argument("--plan", required=True, metavar="FILE")
    plan.set_defaults(func=_cmd_plan)

    waiter = sub.add_parser("waiter", help="verify one deployment waiter bound to this run")
    waiter.add_argument("--waiter", required=True, metavar="FILE")
    waiter.add_argument("--expected", required=True, metavar="FILE")
    waiter.set_defaults(func=_cmd_waiter)

    frontend = sub.add_parser("frontend", help="validate the frontend publication plan")
    frontend.add_argument("--plan", required=True, metavar="FILE")
    frontend.set_defaults(func=_cmd_frontend)

    verify = sub.add_parser("verify", help="verify production before publication")
    verify.add_argument("--observed", required=True, metavar="FILE")
    verify.add_argument("--manifest", required=True, metavar="FILE")
    verify.set_defaults(func=_cmd_verify)

    finalize = sub.add_parser("finalize", help="decide the release-tag/GitHub-release publication")
    finalize.add_argument("--state", required=True, metavar="FILE")
    finalize.set_defaults(func=_cmd_finalize)

    compensate = sub.add_parser("compensate", help="build the reverse-order compensation plan")
    compensate.add_argument("--snapshot", required=True, metavar="FILE")
    compensate.add_argument("--changed", required=True, metavar="FILE")
    compensate.set_defaults(func=_cmd_compensate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
