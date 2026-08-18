"""promote preflight: read-only OP-PRO-02 production promotion gate.

Runs identically in the pre-approval job (informational) and in the approved
job after lock acquisition (sole mutation authorization). It validates:

- candidate authority and eligibility (AD-03/AD-05, CT-CAND): class ``main``,
  ``productionEligible``, branch ``main``, not expired, and the exact
  run/attempt owns the complete four-artifact set;
- the exact staging gate (AD-09, CT-STG): the staging-record artifact of the
  exact staging run, phase COMPLETE with E2E and cleanup both passed, for the
  same candidate;
- frontend archive byte identity and the SBOM artifact files;
- ECR digest existence (read-only);
- the fresh production snapshot (CT-AUDIT-01) plus a fresh live-marker read
  proving the snapshot matches current production;
- AD-11: newer ``main`` candidates are listed; the selected candidate must be
  reachable from current protected ``main`` and must not be older than the
  running production release; a reachable older selection produces the
  newer-candidate warning shown in the approval summary;
- OP-DB-01: no versioned migration ownership exists in the repository;
  production schema changes remain blocked (the production IAM boundary
  grants no RDS mutation, so the engine physically cannot change schema);
- post-approval drift (OP-PRO-02): when ``--previous-report`` is given, the
  recomputed approval identity must equal the pre-approval one byte for byte.

Nothing here mutates AWS. The report embeds ``approvalIdentity`` (a SHA-256
of the identity-relevant subset) so job B can prove the approved state is
unchanged before the first mutation.
"""

from __future__ import annotations

import argparse
import io
import json
import re
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from botocore.exceptions import ClientError
from pydantic import ValidationError as PydanticValidationError

from .. import frontend as frontend_utils
from .. import live_marker
from ..aws import context as aws_context
from ..aws import get_object_sha256
from ..aws.readback import absent_or_read
from ..errors import AbsentResourceError, ReadError, ValidationError
from ..github import GitHubApi
from ..models import (
    CandidateManifest,
    PreflightCandidateIdentity,
    PreflightReport,
    PreflightSnapshotSummary,
    PreflightStagingGate,
    ProductionSnapshot,
    StagingOperationRecord,
)
from ..records import load_candidate, load_snapshot, read_s3_text, write_json
from ..serialization import canonical_json, sha256_hex
from ..validation import validate as validate_record
from ..validation import validate_staging_against_candidate

_SERVICE_KEYS = ("auth", "items", "gateway")
_SBOM_FILES = {
    "auth": "auth.spdx.json",
    "items": "items.spdx.json",
    "gateway": "api-gateway.spdx.json",
    "frontend": "frontend.spdx.json",
}
_MAX_STAGING_RECORD_BYTES = 10 * 1024 * 1024

# The staging record evidence name derives from the Phase-4 convention
# (stage-candidate.yml): staging-record-<run id>-<run attempt>.
_STAGING_RECORD_NAME = re.compile(r"^staging-record-\d+-\d+$")


def preflight(args: argparse.Namespace) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("production",))
    ids = args.identifiers_data
    candidate = load_candidate(args.candidate, args.max_age_days)
    _assert_main_eligible(candidate)
    api = GitHubApi(candidate.source.repository)

    _revalidate_candidate_artifacts(api, candidate)
    staging_record, staging_identity, staging_compatibility = _load_staging_gate(
        api, candidate, args.staging_run
    )
    frontend_utils.verify_frontend_archive(args.frontend_archive, candidate)
    _verify_sbom_files(args.sbom_dir)
    _revalidate_ecr_digests(ctx, ids, candidate)

    snapshot = load_snapshot(args.snapshot, require_environment="production")
    _assert_snapshot_services(ids, snapshot)
    _assert_live_marker_matches_snapshot(ctx, ids, snapshot)

    newer = api.list_main_candidate_runs(candidate.build.workflowRunId)
    main_head = api.get_branch_head_sha("main")
    reachability = api.compare_commits(main_head, candidate.source.fullSha)
    reachability_status = (
        "reachable" if reachability["status"] == "behind" else reachability["status"]
    )
    warning = _ad11_gate(api, candidate, snapshot, newer, reachability)

    op_db_gate = _op_db_gate(Path(args.repo_path))
    warning_text = warning or "none"
    identity_summary = _approval_identity(
        candidate,
        staging_record,
        staging_identity,
        snapshot,
        warning_text,
        op_db_gate,
    )
    approval_identity = sha256_hex(canonical_json(identity_summary).encode())
    if args.previous_report is not None:
        _assert_no_post_approval_drift(args.previous_report, approval_identity)

    approval_summary = (
        f"APPROVAL SUMMARY: promote exact candidate {candidate.candidateId}\n"
        f"  full SHA: {candidate.source.fullSha}\n"
        f"  workflow run {candidate.build.workflowRunId} attempt "
        f"{candidate.build.workflowRunAttempt}\n"
        f"  staging gate: {staging_identity} (E2E passed, cleanup passed)\n"
        f"  staging compatibility: {staging_compatibility}\n"
        f"  newer-candidate warning: {warning_text}\n"
        f"  OP-DB gate: {op_db_gate}"
    )
    report = PreflightReport(
        reportId=f"pfl-{uuid4().hex[:16]}",
        producedAt=datetime.now(UTC),
        candidate=PreflightCandidateIdentity(
            candidateId=candidate.candidateId,
            candidateClass="main",
            branch="main",
            fullSha=candidate.source.fullSha,
            workflowRunId=candidate.build.workflowRunId,
            workflowRunAttempt=candidate.build.workflowRunAttempt,
            authDigest=candidate.artifacts.auth.digest,
            itemsDigest=candidate.artifacts.items.digest,
            gatewayDigest=candidate.artifacts.gateway.digest,
            frontendChecksum=candidate.artifacts.frontend.contentChecksum,
        ),
        candidateReachability=reachability_status,
        newerCandidateWarning=warning_text,
        stagingGate=PreflightStagingGate(
            evidenceIdentity=staging_identity,
            phase=staging_record.phase.value,
            e2eConclusion=staging_record.e2e.conclusion,
            cleanupConclusion=staging_record.cleanup.conclusion,
        ),
        productionSnapshot=PreflightSnapshotSummary(
            snapshotId=snapshot.snapshotId,
            serviceTaskDefinitionArns={
                key: snapshot.services[key].taskDefinitionArn for key in _SERVICE_KEYS
            },
            serviceRunningDigests={
                key: list(snapshot.services[key].runningDigests) for key in _SERVICE_KEYS
            },
            frontendMarker=snapshot.frontend.immutableIdentity,
            frontendChecksum=snapshot.frontend.checksum,
        ),
        opDbGate=op_db_gate,
        approvalSummary=approval_summary,
        approvalIdentity=approval_identity,
        workflowUrl=candidate.build.workflowUrl,
    )
    errors = validate_record(report)
    if errors:
        raise ValidationError(f"preflight report failed internal validation: {'; '.join(errors)}")
    write_json(args.out, report)
    if args.staging_record_out is not None:
        write_json(args.staging_record_out, staging_record)
    print(approval_summary)
    print(f"preflight PASSED for candidate {candidate.candidateId}; report written to {args.out}")
    return 0


# ---------------------------------------------------------------------------
# candidate / staging gate / snapshot loading
# ---------------------------------------------------------------------------


def _assert_main_eligible(manifest: CandidateManifest) -> None:
    if manifest.candidateClass != "main":
        raise ValidationError(
            f"candidate {manifest.candidateId} is class {manifest.candidateClass!r}; "
            "only main-class candidates can enter production (AD-03)"
        )
    if not manifest.productionEligible:
        raise ValidationError(
            f"candidate {manifest.candidateId} is not productionEligible; "
            "only protected-main candidates can enter production (AD-03)"
        )
    if manifest.source.branch != "main":
        raise ValidationError(
            f"candidate {manifest.candidateId} branch is {manifest.source.branch!r}, "
            "not main (AD-03)"
        )


def _revalidate_candidate_artifacts(api: GitHubApi, manifest: CandidateManifest) -> None:
    """CT-CAND-03: the exact run/attempt owns the complete four-artifact set."""
    run = manifest.build.workflowRunId
    attempt = manifest.build.workflowRunAttempt
    required = {
        f"candidate-manifest-{run}-{attempt}",
        f"frontend-archive-{run}-{attempt}",
        f"sboms-{run}-{attempt}",
        f"test-results-{run}-{attempt}",
    }
    try:
        api.list_run_artifacts(run, attempt, required)
    except ValidationError as error:
        if not str(error).startswith("missing artifacts "):
            raise
        missing = str(error).split(" for run ", 1)[0].removeprefix("missing artifacts ")
        raise ValidationError(
            f"candidate artifact set is incomplete (CT-CAND-03): "
            f"missing {missing} for run {run} attempt {attempt}"
        ) from error


def _load_staging_gate(
    api: GitHubApi, manifest: CandidateManifest, staging_run_id: int
) -> tuple[StagingOperationRecord, str, str]:
    """Load the staging record artifact of the exact staging run (AD-09).

    The artifact name embeds the staging run's own attempt; the attempt is
    resolved from the authoritative run object, so a re-run whose latest
    attempt produced no record fails closed (the operator must re-run the
    staging gate). The recorded evidence identity is derived from the
    record's embedded candidate run/attempt (CT-STG-02) so finalize can
    cross-check it against the same record instead of trusting an
    arbitrary string.
    """
    run = api.get_run(staging_run_id)
    attempt = run["run_attempt"]
    name = f"staging-record-{staging_run_id}-{attempt}"
    if not _STAGING_RECORD_NAME.fullmatch(name):
        raise ValidationError(f"unsafe staging record artifact name {name!r}")
    try:
        artifacts = api.list_artifacts_for_run(staging_run_id, attempt, {name})
    except ValidationError as error:
        if not str(error).startswith("missing artifacts "):
            raise
        raise ValidationError(
            f"staging run {staging_run_id} attempt {attempt} has no {name} artifact; "
            "the latest staging attempt is not a completed gate — re-run the staging gate"
        ) from error
    zip_bytes = api.download_artifact_zip(artifacts[0]["archive_download_url"])
    record_raw = _extract_staging_record(zip_bytes)
    try:
        record = StagingOperationRecord.model_validate(json.loads(record_raw))
    except (json.JSONDecodeError, PydanticValidationError) as error:
        raise ValidationError(f"staging record {name} is invalid: {error}") from error
    errors = validate_record(record) + validate_staging_against_candidate(record, manifest)
    if errors:
        raise ValidationError(f"staging record {name} is invalid: {'; '.join(errors)}")
    if record.phase.value != "COMPLETE":
        raise ValidationError(
            f"staging record {name} is at phase {record.phase.value}; only COMPLETE "
            "is valid promotion evidence (AD-09)"
        )
    if record.e2e.conclusion != "passed":
        raise ValidationError(
            f"staging record {name} E2E conclusion is {record.e2e.conclusion!r}; "
            "promotion requires a passed exact-candidate cloud E2E (AD-09)"
        )
    if record.cleanup.conclusion != "passed":
        raise ValidationError(
            f"staging record {name} cleanup conclusion is {record.cleanup.conclusion!r}; "
            "E2E success without verified cleanup is not a valid staging gate (CT-STG-02)"
        )
    if record.failure is not None:
        raise ValidationError(
            f"staging record {name} carries a failure entry; not valid promotion evidence"
        )
    compatibility = _staging_compatibility_conclusion(api, record)
    evidence_identity = (
        f"staging-record-{record.candidate.workflowRunId}-"
        f"{record.candidate.workflowRunAttempt}"
    )
    return record, evidence_identity, compatibility


def _staging_compatibility_conclusion(
    api: GitHubApi, record: StagingOperationRecord
) -> str:
    """AD-15/OP-STG-03: the compatibility conclusion must be ``passed`` or a
    legitimate bootstrap exception.

    A bootstrap exception claims no previous official release existed at
    staging time; when an official release published BEFORE the record's
    ``completedAt`` exists, the previous-official-frontend journey was
    required but never executed for this candidate, so promotion fails
    closed.
    """
    conclusion = record.compatibility.conclusion
    if record.compatibility.bootstrapException:
        if not conclusion.startswith("bootstrap-exception"):
            raise ValidationError(
                "staging record compatibility is inconsistent: bootstrapException "
                f"with conclusion {conclusion!r}"
            )
        _assert_bootstrap_exception_was_honest(api, record)
        return "bootstrap-exception"
    if conclusion == "passed" or conclusion.endswith(": passed"):
        return "passed"
    raise ValidationError(
        f"staging record compatibility conclusion is {conclusion!r}; promotion "
        "requires passed or a verified bootstrap exception (AD-15)"
    )


def _assert_bootstrap_exception_was_honest(
    api: GitHubApi, record: StagingOperationRecord
) -> None:
    completed_at = record.completedAt
    if completed_at is None:
        raise ValidationError(
            "staging record carries a bootstrap compatibility exception but no "
            "completedAt; the AD-15 honesty gate cannot compare publication times"
        )
    for release in api.list_releases():
        tag = release.get("tag_name")
        if not isinstance(tag, str) or not re.fullmatch(r"release-\d{4}", tag):
            continue
        published_raw = release.get("published_at")
        if not isinstance(published_raw, str) or not published_raw.strip():
            raise ValidationError(
                f"official release {tag} carries no published_at; the AD-15 "
                "bootstrap-exception gate fails closed"
            )
        try:
            published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValidationError(
                f"official release {tag} published_at is not ISO-8601: {published_raw!r}"
            ) from error
        if published_at.tzinfo is None or published_at.utcoffset() is None:
            raise ValidationError(
                f"official release {tag} published_at is not timezone-aware"
            )
        if published_at < completed_at:
            raise ValidationError(
                f"staging record records a bootstrap compatibility exception but "
                f"official release {tag} was published at {published_raw}, before "
                "the staging record completed; the AD-15 "
                "previous-official-frontend journey was required but never "
                "executed for this candidate (OP-STG-03)"
            )


def _extract_staging_record(zip_bytes: bytes) -> str:
    if len(zip_bytes) > _MAX_STAGING_RECORD_BYTES:
        raise ValidationError("staging record artifact exceeds the size bound")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            names = archive.namelist()
            if "staging-record.json" not in names:
                raise ValidationError(
                    "staging record artifact does not contain staging-record.json"
                )
            raw = archive.read("staging-record.json")
    except zipfile.BadZipFile as error:
        raise ValidationError(f"staging record artifact is not a valid zip: {error}") from error
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValidationError("staging-record.json is not valid UTF-8") from error


def _verify_sbom_files(sbom_dir: str) -> None:
    directory = Path(sbom_dir)
    if not directory.is_dir():
        raise ValidationError(f"SBOM directory {sbom_dir} is not a directory")
    for _component, filename in _SBOM_FILES.items():
        path = directory / filename
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise ReadError(f"SBOM file {path} is missing or unreadable: {error}") from error
        if not raw.strip():
            raise ValidationError(f"SBOM file {path} is empty")
        try:
            json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValidationError(f"SBOM file {path} is not valid JSON: {error}") from error


def _revalidate_ecr_digests(ctx, ids: dict, manifest: CandidateManifest) -> None:
    ecr_client = aws_context.client_for(ctx, "ecr")
    for key in _SERVICE_KEYS:
        repository = ids["ecrRepositories"][key]
        expected = getattr(manifest.artifacts, key).digest
        try:
            response = ecr_client.describe_images(
                repositoryName=repository, imageIds=[{"imageDigest": expected}]
            )
        except ClientError as error:
            if absent_or_read(error):
                raise AbsentResourceError(
                    f"digest {expected} does not exist in repository {repository}"
                ) from error
            raise ReadError(f"describe_images failed for {repository}") from error
        images = response.get("imageDetails") or []
        if not images:
            raise AbsentResourceError(
                f"digest {expected} does not exist in repository {repository}"
            )
        if images[0].get("imageDigest") != expected:
            raise ValidationError(f"ECR read-back digest mismatch for {repository}")


def _assert_snapshot_services(ids: dict, snapshot: ProductionSnapshot) -> None:
    if len(ids["services"]) != len(_SERVICE_KEYS):
        raise ValidationError(
            "identifiers services must contain exactly 3 names in order auth, items, api-gateway"
        )
    for key, name in zip(_SERVICE_KEYS, ids["services"], strict=True):
        if key not in snapshot.services:
            raise ValidationError(f"snapshot has no observation for service {name} ({key})")


def _assert_live_marker_matches_snapshot(ctx, ids: dict, snapshot: ProductionSnapshot) -> None:
    """Fresh read of the live marker must equal the just-captured snapshot."""
    s3_client = aws_context.client_for(ctx, "s3")
    bucket = ids["frontendBucket"]
    marker_key = ids["frontendLiveMarker"]
    current = read_s3_text(s3_client, bucket, marker_key, "frontend live marker").strip()
    if current != snapshot.frontend.immutableIdentity:
        raise ValidationError(
            "live marker drift: snapshot recorded a different marker than the fresh read; "
            "production changed after the snapshot was captured"
        )
    current_checksum = get_object_sha256(s3_client, bucket, marker_key)
    if current_checksum != snapshot.frontend.checksum:
        raise ValidationError(
            "live marker checksum drift: production changed after the snapshot was captured"
        )


# ---------------------------------------------------------------------------
# AD-11 and OP-DB gates
# ---------------------------------------------------------------------------


def _ad11_gate(
    api: GitHubApi,
    manifest: CandidateManifest,
    snapshot: ProductionSnapshot,
    newer: list[dict],
    reachability: dict,
) -> str:
    if not newer:
        return ""
    if reachability["status"] not in ("behind", "identical"):
        raise ValidationError(
            f"a newer main candidate exists but the selected candidate "
            f"(sha {manifest.source.fullSha}) is {reachability['status']} relative to "
            "current protected main; an older selection requires proof of reachability (AD-11)"
        )
    production_sha = _production_source_sha(snapshot)
    if production_sha is not None:
        relation = api.compare_commits(production_sha, manifest.source.fullSha)
        if relation["status"] == "behind":
            raise ValidationError(
                f"the selected candidate (sha {manifest.source.fullSha}) is older than "
                f"the running production release (sha {production_sha}) (AD-11)"
            )
    newest = newer[0]
    return (
        f"{len(newer)} newer complete main candidate(s) exist; newest is run {newest['id']} "
        f"(sha {newest['head_sha']}). This approval promotes an OLDER candidate that is "
        f"reachable from current main ({reachability['status']}) — AD-11 warning."
    )


def _production_source_sha(snapshot: ProductionSnapshot) -> str | None:
    marker = live_marker.parse_live_marker(snapshot.frontend.immutableIdentity)
    if marker is None:
        return None
    return marker.sourceSha


def _op_db_gate(repo_path: Path) -> str:
    """OP-DB-01 honest minimal check.

    A schema change cannot be detected from a shallow CI checkout without a
    versioned migration owner, so the gate verifies the documented
    precondition instead: NO versioned migration ownership exists in the
    repository. The real enforcement is the production IAM boundary — the
    production role grants no RDS mutation at all — plus the staging
    compatibility gate. When a migration owner is introduced (AD-15
    readiness), this gate must be replaced with an explicit additive-only
    schema diff before it will pass again.
    """
    offenders: list[str] = []
    for path in repo_path.rglob("*"):
        if ".git" in path.parts or "node_modules" in path.parts:
            continue
        text = path.as_posix().lower()
        if not path.is_file():
            continue
        if "flyway" in text or "liquibase" in text or "db/migration" in text:
            offenders.append(str(path.relative_to(repo_path)))
    if offenders:
        raise ValidationError(
            "migration-ownership files found in the repository: "
            f"{', '.join(sorted(set(offenders)))} — a schema-changing release is only "
            "permitted after OP-DB-01 readiness (AD-15); preflight fails closed"
        )
    return (
        "no versioned migration ownership exists in the repository; production schema "
        "changes remain blocked (AD-15/OP-DB-01). The production IAM boundary grants no "
        "RDS mutation, and the engine never mutates production data."
    )


# ---------------------------------------------------------------------------
# approval identity and post-approval drift
# ---------------------------------------------------------------------------


def _approval_identity(
    manifest: CandidateManifest,
    staging_record: StagingOperationRecord,
    staging_identity: str,
    snapshot: ProductionSnapshot,
    warning_text: str,
    op_db_gate: str,
) -> dict:
    return {
        "candidateId": manifest.candidateId,
        "fullSha": manifest.source.fullSha,
        "workflowRunId": manifest.build.workflowRunId,
        "workflowRunAttempt": manifest.build.workflowRunAttempt,
        "authDigest": manifest.artifacts.auth.digest,
        "itemsDigest": manifest.artifacts.items.digest,
        "gatewayDigest": manifest.artifacts.gateway.digest,
        "frontendChecksum": manifest.artifacts.frontend.contentChecksum,
        "staging": {
            "evidenceIdentity": staging_identity,
            "e2e": staging_record.e2e.conclusion,
            "cleanup": staging_record.cleanup.conclusion,
        },
        "newerCandidateWarning": warning_text,
        "opDbGate": op_db_gate,
        "production": {
            "services": {
                key: {
                    "taskDefinitionArn": snapshot.services[key].taskDefinitionArn,
                    "runningDigests": list(snapshot.services[key].runningDigests),
                }
                for key in _SERVICE_KEYS
            },
            "frontendMarker": snapshot.frontend.immutableIdentity,
            "frontendChecksum": snapshot.frontend.checksum,
        },
    }


def _assert_no_post_approval_drift(previous_report_path: str, approval_identity: str) -> None:
    try:
        raw = json.loads(Path(previous_report_path).read_text())
    except OSError as error:
        raise ReadError(
            f"cannot read previous preflight report {previous_report_path}: {error}"
        ) from error
    except json.JSONDecodeError as error:
        raise ValidationError(
            f"previous preflight report {previous_report_path} is not valid JSON: {error}"
        ) from error
    try:
        previous = PreflightReport.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(
            f"previous preflight report {previous_report_path} is invalid: {error}"
        ) from error
    if previous.approvalIdentity != approval_identity:
        raise ValidationError(
            "POST-APPROVAL DRIFT: the preflight result changed after approval "
            "(candidate, staging gate, or production state); aborting before any mutation "
            "requires a new operator decision (OP-PRO-02)"
        )
