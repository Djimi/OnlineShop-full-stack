"""staging lifecycle, apply, and reconcile commands (OP-STG-01/02/03/04/05).

The lifecycle runs the shared-staging state machine:

    QUEUED -> OWNED/revalidate -> STARTING -> RESETTING/seed/verify
           -> DEPLOYING exact candidate -> COMPATIBILITY -> E2E
           -> EVIDENCE -> STOPPING -> CLEANUP_VERIFY -> COMPLETE

Because the cloud E2E is an external Maven suite, the machine is split into
two invocations:

1. ``staging lifecycle --candidate ...`` runs through COMPATIBILITY
   (previous-official-frontend journey or bootstrap exception + candidate
   frontend journeys), writes the record at phase E2E with conclusion
   ``pending``, and emits the resolved E2E URL. The workflow then runs the
   cloud E2E suite against that URL.
2. ``staging lifecycle --continue --e2e-conclusion <passed|failed>`` reloads
   the record, revalidates that the live ownership marker still belongs to
   the same operation (the record itself is visibility only, CT-STG-02),
   records the real E2E conclusion, stops services and RDS, verifies cleanup,
   releases the marker, and completes the record.

Every mutation is read back; every phase failure joins the evidence and
cleanup path (OP-STG-04): diagnostics are captured before the destructive
cleanup, cleanup runs and is verified, and the record carries the failure
and cleanup conclusions. E2E success with failed cleanup is never success.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import NoReturn

from botocore.exceptions import ClientError
from pydantic import ValidationError as PydanticValidationError

from .. import frontend as frontend_utils
from .. import staging_marker
from ..aws import context as aws_context
from ..aws import (
    db_instance_arn,
    describe_db_instance,
    describe_services,
    describe_target_health,
    describe_task_definition,
    load_balancer_dns_name,
    register_task_definition,
    replace_container_images,
    running_digests,
    scale_service,
    task_definition_images,
    update_service,
    wait_for_deployment,
    wait_for_service_running_count,
)
from ..aws.readback import absent_or_read
from ..aws.sqlrunner import SqlStep, execute_sql_steps
from ..aws.waiters import bounded_waiter
from ..errors import (
    AbsentResourceError,
    E2EFailed,
    MutationVerificationError,
    OwnerlessRdsStopped,
    ReadError,
    StagingCleanupFailure,
    ValidationError,
)
from ..github import GitHubApi
from ..models import (
    CandidateManifest,
    CleanupConclusion,
    CompatibilityConclusion,
    DatabaseConclusions,
    DiagnosticsRecord,
    E2EConclusion,
    ExpectedArtifacts,
    FailureInfo,
    JourneyConclusion,
    ObservedArtifacts,
    OwnershipMarker,
    Phase,
    PhaseLog,
    ReconcileRecord,
    ReleaseManifest,
    StagingCandidateIdentity,
    StagingOperationRecord,
)
from ..serialization import canonical_json
from ..serving import FrontendServer, run_readonly_journeys
from ..validation import is_expired
from ..validation import validate as validate_record

_SERVICE_KEYS = ("auth", "items", "gateway")
# GitHub logins are at most 39 characters; the marker owner is one of them.
_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9@._-]{1,39}$")

# Timeout budget (seconds) for staging waits. All are bounded (OP-GEN-03).
RDS_START_TIMEOUT = 900
RDS_STOP_TIMEOUT = 900
SERVICE_SCALE_TIMEOUT = 600
DEPLOYMENT_TIMEOUT = 600

# Reset SQL sources, read by the engine (never executed as shell). They are
# resolved against the repository checkout passed as --repo-path, so the
# wheel-installed engine does not depend on the source-tree layout.
_SQL_SOURCE_FILES = (
    ("bootstrap", "scripts/sql/staging-bootstrap.sql"),
    ("auth_grants", "scripts/sql/staging-auth-grants.sql"),
    ("items_grants", "scripts/sql/staging-items-grants.sql"),
    ("auth_schema", "Auth/init-db/01-schema.sql"),
    ("auth_seed", "Auth/init-db/02-seed-data.sql"),
    ("items_schema", "Items/init-db/01-schema.sql"),
    ("items_seed", "Items/init-db/02-data.sql"),
)

_STAGING_DATABASES = ("auth_staging", "items_staging")
_AUTH_ROLE = "auth_app_staging"
_ITEMS_ROLE = "items_app_staging"

DROP_SQL = (
    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
    f"WHERE datname IN ('{_STAGING_DATABASES[0]}','{_STAGING_DATABASES[1]}');\n"
    f"DROP DATABASE IF EXISTS {_STAGING_DATABASES[0]} WITH (FORCE);\n"
    f"DROP DATABASE IF EXISTS {_STAGING_DATABASES[1]} WITH (FORCE);\n"
)
DROP_VERIFY = (
    "DO $v$ BEGIN IF EXISTS (SELECT 1 FROM pg_database WHERE datname IN "
    f"('{_STAGING_DATABASES[0]}','{_STAGING_DATABASES[1]}')) THEN RAISE EXCEPTION "
    "'staging tenant databases still exist'; END IF; END $v$;"
)
BOOTSTRAP_VERIFY = (
    "DO $v$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='"
    f"{_AUTH_ROLE}') OR NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{_ITEMS_ROLE}') "
    "OR NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='"
    f"{_STAGING_DATABASES[0]}') OR NOT EXISTS (SELECT 1 FROM pg_database WHERE datname='"
    f"{_STAGING_DATABASES[1]}') THEN RAISE EXCEPTION 'staging role/database "
    "verification failed'; END IF; END $v$;"
)
AUTH_SCHEMA_VERIFY = (
    "DO $v$ BEGIN IF to_regclass('public.users') IS NULL OR "
    "to_regclass('public.sessions') IS NULL THEN RAISE EXCEPTION "
    "'Auth schema verification failed'; END IF; END $v$;"
)
AUTH_SEED_VERIFY = (
    "DO $v$ BEGIN IF (SELECT count(*) FROM users WHERE username = 'testuser') <> 1 "
    "THEN RAISE EXCEPTION 'Auth seed verification failed'; END IF; END $v$;"
)
AUTH_GRANTS_VERIFY = (
    f"DO $v$ BEGIN IF NOT has_table_privilege('{_AUTH_ROLE}', 'users', "
    "'SELECT,INSERT,UPDATE,DELETE') OR NOT has_sequence_privilege('"
    f"{_AUTH_ROLE}', 'users_id_seq', 'USAGE,SELECT') THEN RAISE EXCEPTION "
    "'Auth grants verification failed'; END IF; END $v$;"
)
ITEMS_SCHEMA_VERIFY = (
    "DO $v$ BEGIN IF to_regclass('public.items') IS NULL THEN RAISE EXCEPTION "
    "'Items schema verification failed'; END IF; END $v$;"
)
ITEMS_SEED_VERIFY = (
    "DO $v$ BEGIN IF (SELECT count(*) FROM items) <> 5 THEN RAISE EXCEPTION "
    "'Items seed verification failed'; END IF; END $v$;"
)
ITEMS_GRANTS_VERIFY = (
    f"DO $v$ BEGIN IF NOT has_table_privilege('{_ITEMS_ROLE}', 'items', "
    "'SELECT,INSERT,UPDATE,DELETE') THEN RAISE EXCEPTION "
    "'Items grants verification failed'; END IF; END $v$;"
)
# Framed count markers (OP-STG-02): the SQL itself emits the expected count
# ONLY when it matches, so the runner can compare the captured output.
_AUTH_CONNECTIVITY_COUNT = "=== COUNT seeded_auth_users=1 ==="
_ITEMS_CONNECTIVITY_COUNT = "=== COUNT seeded_items=5 ==="
AUTH_CONNECTIVITY_SQL = (
    f"SELECT '{_AUTH_CONNECTIVITY_COUNT}' WHERE "
    "(SELECT count(*) FROM users WHERE username = 'testuser') = 1;"
)
ITEMS_CONNECTIVITY_SQL = (
    f"SELECT '{_ITEMS_CONNECTIVITY_COUNT}' WHERE (SELECT count(*) FROM items) = 5;"
)
CROSS_TENANT_SQL = "SELECT 1;"

_COMPAT_FRONTEND_ARCHIVE = "frontend.tar.gz"
_RELEASE_MANIFEST_ASSET = "release-manifest.json"


# ---------------------------------------------------------------------------
# CLI handlers
# ---------------------------------------------------------------------------


def lifecycle(args: argparse.Namespace) -> int:
    """Run the staging lifecycle (two-invocation machine, see module docstring)."""
    if getattr(args, "continue_lifecycle", False):
        return _lifecycle_continue(args)
    return _lifecycle_first(args)


def apply(args: argparse.Namespace) -> int:
    """Deploy exact candidate digests to an already-running staging environment.

    ``apply`` never starts or stops anything: it deploys digest-pinned
    revisions into the running environment and verifies the running digests.
    Failures are not followed by cleanup because the environment may be owned
    by another staging operation.
    """
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("staging",))
    ids = args.identifiers_data
    manifest = _load_candidate(args.candidate, args.max_age_days)
    machine = _StagingMachine(ctx, ids, args.out, manifest, _owner(args), Path(args.repo_path))
    machine._revalidate_ecr_digests()
    machine.deploy_exact_digests()
    machine.record = machine._build_record(Phase.DEPLOYING)
    machine.record.completedAt = datetime.now(UTC)
    machine._write()
    print(f"staging apply complete for candidate {manifest.candidateId}")
    return 0


def reconcile(args: argparse.Namespace) -> int:
    """OP-STG-05: stop ownerless running staging RDS and surface the event."""
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("staging",))
    ids = args.identifiers_data
    rds_client = aws_context.client_for(ctx, "rds")
    observed_at = datetime.now(UTC)
    try:
        instance = describe_db_instance(rds_client, ids["dbInstance"])
    except AbsentResourceError:
        # A proven-absent staging DB is a real observation (nothing to stop),
        # not a read error: the scheduled run succeeds silently.
        record = ReconcileRecord(
            observedAt=observed_at,
            dbInstance=ids["dbInstance"],
            dbStatus="absent",
            action="none",
            conclusion=(f"staging RDS {ids['dbInstance']} does not exist; nothing to stop"),
        )
        _write_out(args.out, record)
        print(f"reconcile: staging RDS {ids['dbInstance']} is absent; nothing to do")
        return 0
    db_arn = db_instance_arn(instance)
    status = instance.get("DBInstanceStatus") or ""
    record = ReconcileRecord(
        observedAt=observed_at,
        dbInstance=ids["dbInstance"],
        dbStatus=status,
        action="none",
        conclusion="",
    )
    if status == "stopped":
        record = record.model_copy(update={"conclusion": "staging RDS is stopped; nothing to do"})
        _write_out(args.out, record)
        print(f"reconcile: staging RDS {ids['dbInstance']} is stopped; nothing to do")
        return 0
    marker = staging_marker.read_marker(rds_client, db_arn)
    if marker is not None and staging_marker.marker_is_active(marker):
        record = record.model_copy(
            update={
                "marker": marker,
                "conclusion": (
                    f"active staging owner {marker.operationId} "
                    f"(expires {marker.expiresAt.isoformat()}); leaving running"
                ),
            }
        )
        _write_out(args.out, record)
        print(
            f"reconcile: staging RDS {ids['dbInstance']} is running with active "
            f"owner {marker.operationId}; no action"
        )
        return 0
    if marker is not None:
        marker_state = f"expired marker {marker.operationId}"
    else:
        marker_state = "no ownership marker"
    print(
        f"OWNERLESS_STAGING_RDS: staging RDS {ids['dbInstance']} is running "
        f"({status}) with {marker_state}; stopping it now (OP-STG-05)"
    )
    rds_client.stop_db_instance(DBInstanceIdentifier=ids["dbInstance"])
    _wait_for_db_status(rds_client, ids["dbInstance"], "stopped", RDS_STOP_TIMEOUT)
    stopped = describe_db_instance(rds_client, ids["dbInstance"])
    if stopped.get("DBInstanceStatus") != "stopped":
        raise MutationVerificationError(
            f"staging RDS {ids['dbInstance']} did not verify stopped after reconcile"
        )
    record = record.model_copy(
        update={
            "marker": marker,
            "action": "stopped",
            "conclusion": (
                f"ownerless staging RDS (status {status}, {marker_state}) was "
                "stopped and the stopped state verified"
            ),
        }
    )
    _write_out(args.out, record)
    print(
        "OWNERLESS_STAGING_RDS: stopped and verified. This scheduled run fails "
        "visibly so the event is surfaced (OP-STG-05)."
    )
    raise OwnerlessRdsStopped(
        f"ownerless staging RDS {ids['dbInstance']} (status {status}, "
        f"{marker_state}) was stopped and verified"
    )


# ---------------------------------------------------------------------------
# Lifecycle: first invocation
# ---------------------------------------------------------------------------


def _lifecycle_first(args: argparse.Namespace) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("staging",))
    ids = args.identifiers_data
    manifest = _load_candidate(args.candidate, args.max_age_days)
    frontend_dir = _extract_and_verify_frontend(args.frontend_archive, manifest)
    machine = _StagingMachine(ctx, ids, args.out, manifest, _owner(args), Path(args.repo_path))
    try:
        machine.run_first(frontend_dir, args.e2e_url_out)
        return 0
    except Exception as error:
        machine.handle_failure(error)
        raise


def _lifecycle_continue(args: argparse.Namespace) -> int:
    ctx = args.context_builder(args)
    aws_context.identity_preflight(ctx)
    aws_context.require_environment(ctx, ("staging",))
    record = _load_staging_record(args.out)
    if record.phase != Phase.E2E:
        raise ValidationError(
            f"continuation requires a record at phase E2E, found {record.phase.value}"
        )
    if record.e2e.conclusion not in ("pending", "not-run"):
        raise ValidationError(
            f"record E2E conclusion is {record.e2e.conclusion!r}, not pending; "
            "refusing to overwrite a decided conclusion"
        )
    machine = _StagingMachine(
        ctx, args.identifiers_data, args.out, None, record.owner, Path(args.repo_path)
    )
    machine.restore(record)
    try:
        machine.continue_after_e2e(args.e2e_conclusion)
        return 0
    except Exception as error:
        machine.handle_failure(error)
        raise


# ---------------------------------------------------------------------------
# Candidate loading and frontend verification
# ---------------------------------------------------------------------------


def _load_candidate(path: str, max_age_days: int | None) -> CandidateManifest:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read candidate manifest {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"candidate manifest {path} is not valid JSON: {error}") from error
    try:
        manifest = CandidateManifest.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(
            f"candidate manifest {path} failed schema validation: {error}"
        ) from error
    errors = validate_record(manifest)
    if errors:
        raise ValidationError(f"candidate manifest {path} is invalid: {'; '.join(errors)}")
    if max_age_days is not None and is_expired(manifest, max_age_days):
        raise ValidationError(
            f"candidate {manifest.candidateId} is expired; exceeds --max-age-days {max_age_days}"
        )
    return manifest


def _extract_and_verify_frontend(archive_path: str, manifest: CandidateManifest) -> Path:
    """Verify the frontend archive bytes and content checksum, then extract."""
    return frontend_utils.verify_frontend_archive(archive_path, manifest)


def _content_checksum(dist_dir: Path) -> str:
    """Aggregate checksum matching the publish-time pipeline exactly."""
    return frontend_utils.content_checksum(dist_dir)


def _extract_archive_bytes(payload: bytes, destination: Path) -> None:
    """Safely extract a tar archive into ``destination`` (delegates)."""
    frontend_utils.extract_archive_bytes(payload, destination)


def _owner(args: argparse.Namespace) -> str:
    owner = getattr(args, "owner", None)
    if owner is None:
        return "local"
    if not _OWNER_PATTERN.fullmatch(owner):
        raise ValidationError(f"unsafe owner value {owner!r}")
    return owner


def _resolve_sql_sources(repo_path: Path) -> dict[str, str]:
    """Resolve every required reset SQL source against the checkout (F1).

    A wheel-installed engine has no source-tree layout, so the SQL sources
    are only reachable through the ``--repo-path`` checkout. A missing or
    unreadable required file fails closed before any staging mutation.
    """
    sources: dict[str, str] = {}
    for label, relative in _SQL_SOURCE_FILES:
        path = repo_path / relative
        try:
            sources[label] = path.read_text()
        except OSError as error:
            raise ValidationError(
                f"required SQL source {label!r} is missing or unreadable at {path}: {error}"
            ) from error
    return sources


def _wait_for_db_status(rds_client, identifier: str, expected: str, timeout: int) -> dict:
    def poll() -> bool:
        return describe_db_instance(rds_client, identifier).get("DBInstanceStatus") == expected

    bounded_waiter(
        poll,
        label=f"DB instance {identifier} to reach {expected}",
        timeout_seconds=timeout,
        interval_seconds=15,
    )
    observed = describe_db_instance(rds_client, identifier)
    if observed.get("DBInstanceStatus") != expected:
        raise MutationVerificationError(
            f"DB instance {identifier} did not reach {expected} "
            f"(observed {observed.get('DBInstanceStatus')!r})"
        )
    return observed


# ---------------------------------------------------------------------------
# The machine
# ---------------------------------------------------------------------------


class _StagingMachine:
    """Stateful OP-STG-01 machine; writes the record after every phase."""

    def __init__(
        self,
        ctx,
        ids: dict,
        out_path: str,
        manifest: CandidateManifest | None,
        owner: str,
        repo_path: Path,
    ):
        self.ctx = ctx
        self.ids = ids
        self.out_path = out_path
        self.manifest = manifest
        self.owner = owner
        self.sql_sources = _resolve_sql_sources(repo_path)
        self.record: StagingOperationRecord | None = None
        self.marker: OwnershipMarker | None = None
        self.mutation_began = False
        self.current_phase = Phase.QUEUED
        self.phase_log: list[PhaseLog] = []
        self.journeys: list[JourneyConclusion] = []
        self.database = DatabaseConclusions(
            resetConclusion="not-run",
            seedConclusion="not-run",
            accessVerificationConclusion="not-run",
        )
        self.observed = ObservedArtifacts()
        self.compatibility = CompatibilityConclusion(conclusion="not-run", bootstrapException=False)
        self.e2e = E2EConclusion(conclusion="not-run")
        self.cleanup = CleanupConclusion(conclusion="not-run")
        self.diagnostics_path: str | None = None
        self.e2e_url: str | None = None
        if manifest is not None:
            self.identity = StagingCandidateIdentity(
                candidateId=manifest.candidateId,
                branch=manifest.source.branch,
                fullSha=manifest.source.fullSha,
                workflowRunId=manifest.build.workflowRunId,
                workflowRunAttempt=manifest.build.workflowRunAttempt,
            )
            self.operation_id = (
                f"stg-{manifest.build.workflowRunId}-{manifest.build.workflowRunAttempt}"
            )
            self.expected = ExpectedArtifacts(
                authDigest=manifest.artifacts.auth.digest,
                itemsDigest=manifest.artifacts.items.digest,
                gatewayDigest=manifest.artifacts.gateway.digest,
                frontendChecksum=manifest.artifacts.frontend.contentChecksum,
            )
        else:
            self.identity: StagingCandidateIdentity | None = None
            self.operation_id: str | None = None
            self.expected: ExpectedArtifacts | None = None

    # -- record assembly ----------------------------------------------------

    def _build_record(self, phase: Phase) -> StagingOperationRecord:
        if self.identity is None or self.operation_id is None or self.expected is None:
            raise ValidationError("machine has no candidate identity; cannot build a record")
        return StagingOperationRecord(
            operationId=self.operation_id,
            candidate=self.identity,
            owner=self.owner,
            acquiredAt=datetime.now(UTC),
            phase=phase,
            database=self.database,
            artifactsExpected=self.expected,
            artifactsObserved=self.observed,
            compatibility=self.compatibility,
            e2e=self.e2e,
            cleanup=self.cleanup,
            phaseLog=self.phase_log,
            journeys=self.journeys,
            diagnosticsPath=self.diagnostics_path,
            e2eUrl=self.e2e_url,
        )

    def _write(self) -> None:
        if self.record is None:
            return
        errors = validate_record(self.record)
        if errors:
            raise ValidationError(f"staging record failed internal validation: {'; '.join(errors)}")
        _write_out(self.out_path, self.record)

    def restore(self, record: StagingOperationRecord) -> None:
        """Resume state from the record written by the first invocation."""
        self.record = record
        self.identity = record.candidate
        self.operation_id = record.operationId
        self.expected = record.artifactsExpected
        self.phase_log = list(record.phaseLog)
        self.journeys = list(record.journeys)
        self.database = record.database
        self.observed = record.artifactsObserved
        self.compatibility = record.compatibility
        self.e2e = record.e2e
        self.cleanup = record.cleanup
        self.e2e_url = record.e2eUrl
        self.current_phase = record.phase

    @contextmanager
    def _phase(self, name: Phase):
        self.current_phase = name
        started = datetime.now(UTC)
        entry = PhaseLog(name=name.value, startedAt=started, conclusion="started")
        self.phase_log.append(entry)
        try:
            yield
        except Exception:
            entry.conclusion = "failed"
            entry.endedAt = datetime.now(UTC)
            if self.identity is not None:
                self.record = self._build_record(name)
            raise
        entry.conclusion = "passed"
        entry.endedAt = datetime.now(UTC)

    # -- AWS plumbing -------------------------------------------------------

    def _start_db(self, rds_client, identifier: str) -> dict:
        instance = describe_db_instance(rds_client, identifier)
        if instance.get("DBInstanceStatus") == "available":
            return instance
        rds_client.start_db_instance(DBInstanceIdentifier=identifier)
        self.mutation_began = True
        return _wait_for_db_status(rds_client, identifier, "available", RDS_START_TIMEOUT)

    def _stop_db(self, rds_client, identifier: str) -> dict:
        instance = describe_db_instance(rds_client, identifier)
        if instance.get("DBInstanceStatus") == "stopped":
            return instance
        rds_client.stop_db_instance(DBInstanceIdentifier=identifier)
        return _wait_for_db_status(rds_client, identifier, "stopped", RDS_STOP_TIMEOUT)

    def _scale(self, ecs_client, service: str, desired: int) -> None:
        scale_service(ecs_client, self.ids["cluster"], service, desired)
        wait_for_service_running_count(
            ecs_client,
            self.ids["cluster"],
            service,
            desired,
            timeout_seconds=SERVICE_SCALE_TIMEOUT,
        )

    # -- phase implementations ----------------------------------------------

    def run_first(self, frontend_dir: Path, e2e_url_out: str | None) -> None:
        with self._phase(Phase.QUEUED):
            self.record = self._build_record(Phase.QUEUED)
            self._write()
        with self._phase(Phase.OWNED):
            self._revalidate_github_artifacts()
            self._acquire_ownership()
            self._revalidate_ecr_digests()
            self.record = self._build_record(Phase.OWNED)
            self._write()
        with self._phase(Phase.STARTING):
            self._start_environment()
            self.record = self._build_record(Phase.STARTING)
            self._write()
        with self._phase(Phase.RESETTING):
            self._reset_database()
            self.record = self._build_record(Phase.RESETTING)
            self._write()
        with self._phase(Phase.DEPLOYING):
            self.deploy_exact_digests()
            self.record = self._build_record(Phase.DEPLOYING)
            self._write()
        with self._phase(Phase.COMPATIBILITY):
            self._run_compatibility(frontend_dir)
            self.record = self._build_record(Phase.COMPATIBILITY)
            self._write()
        with self._phase(Phase.E2E):
            self._resolve_e2e_url()
            self.e2e = E2EConclusion(conclusion="pending")
            self.record = self._build_record(Phase.E2E)
            self._write()
        if e2e_url_out:
            Path(e2e_url_out).write_text(f"{self.e2e_url}\n")
        print(
            f"staging environment prepared for candidate {self.identity.candidateId}; "
            f"E2E URL: {self.e2e_url}"
        )
        print(
            "run the cloud E2E suite, then continue with: "
            "delivery staging lifecycle --continue --e2e-conclusion <passed|failed> "
            f"--out {self.out_path}"
        )

    def continue_after_e2e(self, conclusion: str) -> None:
        assert self.record is not None
        rds_client = aws_context.client_for(self.ctx, "rds")
        instance = describe_db_instance(rds_client, self.ids["dbInstance"])
        db_arn = db_instance_arn(instance)
        live_marker = staging_marker.read_marker(rds_client, db_arn)
        staging_marker.assert_marker_owned_by(live_marker, self.record)
        self.marker = live_marker
        actual = "not-run" if self.e2e.conclusion == "not-run" else conclusion
        self.e2e = E2EConclusion(conclusion=actual)
        with self._phase(Phase.EVIDENCE):
            self.record = self._build_record(Phase.EVIDENCE)
            self._write()
        with self._phase(Phase.STOPPING):
            self._cleanup()
            self.cleanup = CleanupConclusion(conclusion="passed")
            self.record = self._build_record(Phase.STOPPING)
            self._write()
        with self._phase(Phase.CLEANUP_VERIFY):
            self._verify_stopped()
            staging_marker.release_marker(rds_client, db_arn)
            self.record = self._build_record(Phase.CLEANUP_VERIFY)
            self._write()
        if actual == "passed":
            self.record = self._build_record(Phase.COMPLETE)
            self.record.completedAt = datetime.now(UTC)
            self._write()
            print(f"staging lifecycle COMPLETE for candidate {self.record.candidate.candidateId}")
        else:
            self.record.completedAt = datetime.now(UTC)
            self._write()
            print(
                f"staging lifecycle cleaned up but E2E conclusion is {actual!r}; "
                "this staging gate is NOT valid promotion evidence"
            )
            raise E2EFailed(
                f"staging E2E conclusion is {actual!r} for candidate "
                f"{self.record.candidate.candidateId}"
            )

    # -- phases -------------------------------------------------------------

    def _revalidate_github_artifacts(self) -> None:
        api = GitHubApi(self._repository())
        run = self.identity.workflowRunId
        attempt = self.identity.workflowRunAttempt
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

    def _repository(self) -> str:
        return self.manifest.source.repository

    def _acquire_ownership(self) -> None:
        rds_client = aws_context.client_for(self.ctx, "rds")
        instance = describe_db_instance(rds_client, self.ids["dbInstance"])
        db_arn = db_instance_arn(instance)
        marker = staging_marker.build_marker(
            self.operation_id,
            self.identity.workflowRunId,
            self.identity.workflowRunAttempt,
            self.owner,
        )
        staging_marker.acquire_marker(rds_client, db_arn, marker)
        self.marker = marker

    def _revalidate_ecr_digests(self) -> None:
        ecr_client = aws_context.client_for(self.ctx, "ecr")
        for key in _SERVICE_KEYS:
            repository = self.ids["ecrRepositories"][key]
            expected = getattr(self.manifest.artifacts, key).digest
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
                raise MutationVerificationError(f"ECR read-back digest mismatch for {repository}")

    def _start_environment(self) -> None:
        rds_client = aws_context.client_for(self.ctx, "rds")
        self._start_db(rds_client, self.ids["dbInstance"])
        ecs_client = aws_context.client_for(self.ctx, "ecs")
        for service in self.ids["services"]:
            self._scale(ecs_client, service, 1)
            self.mutation_began = True

    def _reset_database(self) -> None:
        rds_client = aws_context.client_for(self.ctx, "rds")
        instance = describe_db_instance(rds_client, self.ids["dbInstance"])
        endpoint = instance.get("Endpoint") or {}
        db_host = endpoint.get("Address")
        if not db_host:
            raise ReadError("staging DB instance has no endpoint address")
        master_secret = instance.get("MasterUserSecret") or {}
        master_secret_arn = master_secret.get("SecretArn")
        if not master_secret_arn:
            raise ReadError("staging DB instance has no MasterUserSecret.SecretArn")
        secrets_client = aws_context.client_for(self.ctx, "secretsmanager")
        auth_secret_arn = self._resolve_staging_secret(
            secrets_client, self.ids["dbSecrets"]["auth"]
        )
        items_secret_arn = self._resolve_staging_secret(
            secrets_client, self.ids["dbSecrets"]["items"]
        )
        steps = self._reset_steps(db_host, master_secret_arn, auth_secret_arn, items_secret_arn)
        results = execute_sql_steps(self.ctx, self.ids, steps, db_host)
        if len(results) != len(steps):
            raise MutationVerificationError(
                f"SQL runner executed {len(results)} of {len(steps)} reset steps"
            )
        self.database = DatabaseConclusions(
            resetConclusion="passed",
            seedConclusion="passed",
            accessVerificationConclusion="passed",
        )

    def _resolve_staging_secret(self, secrets_client, secret_name: str) -> str:
        from ..aws.secrets import secret_reference

        arn = secret_reference(secrets_client, secret_name)
        if secret_name not in arn:
            raise ValidationError(
                f"resolved secret ARN {arn} does not reference staging secret {secret_name!r}"
            )
        return arn

    def _reset_steps(
        self, db_host: str, master_arn: str, auth_arn: str, items_arn: str
    ) -> list[SqlStep]:
        bootstrap_sql = self.sql_sources["bootstrap"]
        auth_schema = self.sql_sources["auth_schema"]
        auth_seed = self.sql_sources["auth_seed"]
        auth_grants = self.sql_sources["auth_grants"]
        items_schema = self.sql_sources["items_schema"]
        items_seed = self.sql_sources["items_seed"]
        items_grants = self.sql_sources["items_grants"]
        return [
            SqlStep(
                database="postgres", sql=DROP_SQL, verify_sql=DROP_VERIFY, secret_arn=master_arn
            ),
            SqlStep(
                database="postgres",
                sql=bootstrap_sql,
                verify_sql=BOOTSTRAP_VERIFY,
                secret_arn=master_arn,
                extra_secrets={"AUTH_PASSWORD": auth_arn, "ITEMS_PASSWORD": items_arn},
            ),
            SqlStep(
                database="auth_staging",
                sql=auth_schema,
                verify_sql=AUTH_SCHEMA_VERIFY,
                secret_arn=master_arn,
            ),
            SqlStep(
                database="auth_staging",
                sql=auth_seed,
                verify_sql=AUTH_SEED_VERIFY,
                secret_arn=master_arn,
            ),
            SqlStep(
                database="auth_staging",
                sql=auth_grants,
                verify_sql=AUTH_GRANTS_VERIFY,
                secret_arn=master_arn,
            ),
            SqlStep(
                database="items_staging",
                sql=items_schema,
                verify_sql=ITEMS_SCHEMA_VERIFY,
                secret_arn=master_arn,
            ),
            SqlStep(
                database="items_staging",
                sql=items_seed,
                verify_sql=ITEMS_SEED_VERIFY,
                secret_arn=master_arn,
            ),
            SqlStep(
                database="items_staging",
                sql=items_grants,
                verify_sql=ITEMS_GRANTS_VERIFY,
                secret_arn=master_arn,
            ),
            SqlStep(
                database="auth_staging",
                sql=AUTH_CONNECTIVITY_SQL,
                user=_AUTH_ROLE,
                secret_arn=auth_arn,
                read_only=True,
                expect_output=_AUTH_CONNECTIVITY_COUNT,
            ),
            SqlStep(
                database="items_staging",
                sql=ITEMS_CONNECTIVITY_SQL,
                user=_ITEMS_ROLE,
                secret_arn=items_arn,
                read_only=True,
                expect_output=_ITEMS_CONNECTIVITY_COUNT,
            ),
            SqlStep(
                database="items_staging",
                sql=CROSS_TENANT_SQL,
                user=_AUTH_ROLE,
                secret_arn=auth_arn,
                expect_success=False,
            ),
            SqlStep(
                database="auth_staging",
                sql=CROSS_TENANT_SQL,
                user=_ITEMS_ROLE,
                secret_arn=items_arn,
                expect_success=False,
            ),
        ]

    def deploy_exact_digests(self) -> None:
        """DEPLOYING: register digest-pinned revisions, deploy in order, verify."""
        ecs_client = aws_context.client_for(self.ctx, "ecs")
        registry = f"{self.ids['accountId']}.dkr.ecr.{self.ctx.region}.amazonaws.com"
        observed: dict[str, str] = {}
        for key, service in zip(_SERVICE_KEYS, self.ids["services"], strict=True):
            expected_digest = getattr(self.manifest.artifacts, key).digest
            repository = self.ids["ecrRepositories"][key]
            service_observed = describe_services(ecs_client, self.ids["cluster"], [service])[
                service
            ]
            td_arn = service_observed.get("taskDefinition")
            if not td_arn:
                raise ReadError(f"service {service} has no taskDefinition")
            td = describe_task_definition(ecs_client, td_arn)["taskDefinition"]
            images = task_definition_images(td)
            target = f"{registry}/{repository}@{expected_digest}"
            matches = [
                name
                for name, image in images.items()
                if image.rsplit("@", 1)[0].split(":", 1)[0].endswith(f"/{repository}")
            ]
            if len(matches) != 1:
                raise ReadError(
                    f"service {service} must have exactly one container for "
                    f"repository {repository}, found {matches}"
                )
            revision_arn = register_task_definition(
                ecs_client,
                replace_container_images(td, {matches[0]: target}),
            )
            update_service(ecs_client, self.ids["cluster"], service, revision_arn)
            deployment_id = _primary_deployment_id(
                describe_services(ecs_client, self.ids["cluster"], [service])[service],
                service,
            )
            wait_for_deployment(
                ecs_client,
                self.ids["cluster"],
                service,
                deployment_id,
                timeout_seconds=DEPLOYMENT_TIMEOUT,
            )
            digests = running_digests(ecs_client, self.ids["cluster"], service)
            if digests != [expected_digest]:
                raise MutationVerificationError(
                    f"service {service} running digests {digests} do not match "
                    f"expected {[expected_digest]}"
                )
            observed[key] = expected_digest
            self.mutation_began = True
        self.observed = ObservedArtifacts(
            authDigest=observed["auth"],
            itemsDigest=observed["items"],
            gatewayDigest=observed["gateway"],
            frontendChecksum=self.manifest.artifacts.frontend.contentChecksum,
        )

    def _run_compatibility(self, frontend_dir: Path) -> None:
        """D5 previous-official-frontend journey or bootstrap exception, then D6."""
        e2e_base = self._resolve_e2e_url()
        self._previous_release_journey(e2e_base)
        self._candidate_frontend_journey(frontend_dir, e2e_base)

    def _resolve_e2e_url(self) -> str:
        override = self.ids.get("e2eBaseUrl")
        if override:
            if not override.startswith(("http://", "https://")):
                raise ValidationError(f"e2eBaseUrl must be http(s), got {override!r}")
            self.e2e_url = override
            return override
        elb_client = aws_context.client_for(self.ctx, "elb")
        dns_name = load_balancer_dns_name(elb_client, self.ids["albName"])
        self.e2e_url = f"http://{dns_name}"
        return self.e2e_url

    def _previous_release_journey(self, e2e_base: str) -> None:
        api = GitHubApi(self._repository())
        release_manifest = _find_previous_release(api)
        if release_manifest is None:
            self.compatibility = CompatibilityConclusion(
                conclusion=(
                    "bootstrap-exception: no previous official release exists yet, "
                    "so the previous-official-frontend journey cannot run (AD-15)"
                ),
                bootstrapException=True,
            )
            return
        archive_bytes = self._download_previous_frontend(release_manifest)
        directory = Path(tempfile.mkdtemp(prefix="onlineshop-prev-frontend-"))
        _extract_archive_bytes(archive_bytes, directory)
        checksum = _content_checksum(directory)
        if checksum != release_manifest.artifacts.frontend.checksum:
            raise ValidationError(
                f"previous official frontend checksum {checksum} does not match "
                f"release {release_manifest.releaseId} manifest "
                f"{release_manifest.artifacts.frontend.checksum}"
            )
        self._run_journeys("previous-frontend", directory, e2e_base)
        conclusions = [
            journey.conclusion
            for journey in self.journeys
            if journey.name.startswith("previous-frontend:")
        ]
        passed = bool(conclusions) and all(c == "passed" for c in conclusions)
        self.compatibility = CompatibilityConclusion(
            conclusion=(
                f"previous official frontend {release_manifest.releaseId} "
                f"against candidate backends: {'passed' if passed else 'failed'}"
            ),
            bootstrapException=False,
        )
        if not passed:
            raise ValidationError("previous-official-frontend journey failed (AD-15)")

    def _download_previous_frontend(self, release: ReleaseManifest) -> bytes:
        """Read-only download of the previous official frontend bundle (S3)."""
        s3_client = aws_context.client_for(self.ctx, "s3")
        prefix = self.ids["compatFrontendReleasesPrefix"]
        bucket = self.ids["compatFrontendBucket"]
        key = f"{prefix}{release.releaseId}/{_COMPAT_FRONTEND_ARCHIVE}"
        try:
            body = s3_client.get_object(Bucket=bucket, Key=key).get("Body")
        except ClientError as error:
            if absent_or_read(error):
                raise AbsentResourceError(
                    f"previous official frontend bundle s3://{bucket}/{key} not found"
                ) from error
            raise ReadError(f"get_object failed for s3://{bucket}/{key}") from error
        if body is None:
            raise ReadError(f"get_object returned no body for s3://{bucket}/{key}")
        return body.read()

    def _candidate_frontend_journey(self, frontend_dir: Path, e2e_base: str) -> None:
        self._run_journeys("candidate-frontend", frontend_dir, e2e_base)
        conclusions = [
            journey.conclusion
            for journey in self.journeys
            if journey.name.startswith("candidate-frontend:")
        ]
        if not conclusions or not all(c == "passed" for c in conclusions):
            raise ValidationError("candidate-frontend journey failed (D6)")

    def _run_journeys(self, prefix: str, www_dir: Path, e2e_base: str) -> None:
        with FrontendServer(www_dir, e2e_base) as server:
            results = run_readonly_journeys(server.base_url, e2e_base)
        for result in results:
            self.journeys.append(
                JourneyConclusion(
                    name=f"{prefix}:{result.name}",
                    conclusion=result.conclusion,
                    detail=result.detail,
                )
            )

    # -- cleanup and failure ------------------------------------------------

    def _cleanup(self) -> None:
        """STOPPING: scale services to 0, stop RDS; every step read back."""
        ecs_client = aws_context.client_for(self.ctx, "ecs")
        for service in self.ids["services"]:
            self._scale(ecs_client, service, 0)
        rds_client = aws_context.client_for(self.ctx, "rds")
        self._stop_db(rds_client, self.ids["dbInstance"])

    def _verify_stopped(self) -> None:
        """CLEANUP_VERIFY: read-backs proving the environment is stopped."""
        ecs_client = aws_context.client_for(self.ctx, "ecs")
        for service in self.ids["services"]:
            observed = describe_services(ecs_client, self.ids["cluster"], [service])[service]
            if observed.get("desiredCount") != 0:
                raise MutationVerificationError(
                    f"service {service} desiredCount is {observed.get('desiredCount')}, "
                    "not 0 after cleanup"
                )
            if observed.get("runningCount") != 0:
                raise MutationVerificationError(
                    f"service {service} runningCount is {observed.get('runningCount')}, "
                    "not 0 after cleanup"
                )
        rds_client = aws_context.client_for(self.ctx, "rds")
        instance = describe_db_instance(rds_client, self.ids["dbInstance"])
        if instance.get("DBInstanceStatus") != "stopped":
            raise MutationVerificationError(
                f"staging RDS is {instance.get('DBInstanceStatus')!r}, not stopped after cleanup"
            )

    def capture_diagnostics(self, error: BaseException) -> str:
        """Capture a redacted environment snapshot before destructive cleanup (D7)."""
        diagnostics = DiagnosticsRecord(
            capturedAt=datetime.now(UTC),
            environment=self.ctx.environment,
            cluster=self.ids["cluster"],
        )
        ecs_client = aws_context.client_for(self.ctx, "ecs")
        try:
            services = describe_services(ecs_client, self.ids["cluster"], self.ids["services"])
            diagnostics.services = [_redact_service(observed) for observed in services.values()]
        except Exception as capture_error:
            diagnostics.errors.append(f"ecs describe_services: {capture_error}")
        try:
            for service in self.ids["services"]:
                listed = ecs_client.list_tasks(cluster=self.ids["cluster"], serviceName=service)
                task_arns = listed.get("taskArns") or []
                if task_arns:
                    described = ecs_client.describe_tasks(
                        cluster=self.ids["cluster"], tasks=task_arns
                    )
                    for task in described.get("tasks") or []:
                        diagnostics.services.append(
                            {
                                "service": service,
                                "taskArn": task.get("taskArn"),
                                "lastStatus": task.get("lastStatus"),
                                "stoppedReason": task.get("stoppedReason"),
                            }
                        )
        except Exception as capture_error:
            diagnostics.errors.append(f"ecs describe_tasks: {capture_error}")
        rds_client = aws_context.client_for(self.ctx, "rds")
        try:
            instance = describe_db_instance(rds_client, self.ids["dbInstance"])
            diagnostics.dbInstance = {
                "DBInstanceIdentifier": instance.get("DBInstanceIdentifier"),
                "DBInstanceStatus": instance.get("DBInstanceStatus"),
                "Engine": instance.get("Engine"),
                "EngineVersion": instance.get("EngineVersion"),
                "DBInstanceClass": instance.get("DBInstanceClass"),
                "StorageEncrypted": instance.get("StorageEncrypted"),
                "PubliclyAccessible": instance.get("PubliclyAccessible"),
            }
        except Exception as capture_error:
            diagnostics.errors.append(f"rds describe_db_instances: {capture_error}")
        target_group = self.ids.get("targetGroupArn")
        if target_group:
            elb_client = aws_context.client_for(self.ctx, "elb")
            try:
                diagnostics.albTargetHealth = describe_target_health(elb_client, target_group)
            except Exception as capture_error:
                diagnostics.errors.append(f"elb describe_target_health: {capture_error}")
        path = f"{self.out_path}.diagnostics.json"
        _write_out(path, diagnostics)
        self.diagnostics_path = path
        return path

    def _release_owned_marker(self) -> None:
        """Release the ownership marker when this machine acquired it.

        Only a marker this operation actually acquired is removed; the
        read-back must prove absence or the release fails closed. When the
        machine holds no marker (acquire never happened, or the continuation
        ownership check failed), nothing is touched.
        """
        if self.marker is None or self.marker.operationId != self.operation_id:
            return
        rds_client = aws_context.client_for(self.ctx, "rds")
        instance = describe_db_instance(rds_client, self.ids["dbInstance"])
        staging_marker.release_marker(rds_client, db_instance_arn(instance))

    def handle_failure(self, error: BaseException) -> NoReturn:
        """OP-STG-04: diagnose, cleanup, verify, record both conclusions.

        Always raises: either the original error (cleanup verified passed) or
        a distinct StagingCleanupFailure (cleanup failed, promotion blocked).
        Cleanup only ever runs when this operation verifiably owns the
        environment (the ownership marker was acquired, or a mutation by this
        operation already began). Any failure before acquisition — a marker
        read error, a GitHub read error, a marker conflict — leaves the
        possibly foreign-owned environment untouched and records
        ``cleanup skipped / ownership unverified`` instead.
        """
        if self.record is None and self.identity is not None:
            self.record = self._build_record(self.current_phase)
        try:
            self.capture_diagnostics(error)
        except Exception as capture_error:
            print(f"WARNING: diagnostics capture failed: {capture_error}", file=sys.stderr)
        cleanup_error: BaseException | None = None
        if self.marker is None and not self.mutation_began:
            self.cleanup = CleanupConclusion(conclusion="skipped", reason="ownership unverified")
        else:
            try:
                self._cleanup()
                self._verify_stopped()
                self._release_owned_marker()
                self.cleanup = CleanupConclusion(conclusion="passed")
            except Exception as error_cleanup:
                cleanup_error = error_cleanup
                self.cleanup = CleanupConclusion(conclusion="failed")
        if self.record is not None:
            self.record = self._build_record(self.current_phase)
            self.record.failure = FailureInfo(
                environment=self.ctx.environment,
                failedPhase=self.current_phase.value,
                mutationBegan=self.mutation_began,
                cleanupConclusion=self.cleanup.conclusion,
            )
            self.record.completedAt = datetime.now(UTC)
            try:
                self._write()
            except Exception as write_error:
                print(f"WARNING: could not write staging record: {write_error}", file=sys.stderr)
        if cleanup_error is not None:
            raise StagingCleanupFailure(
                f"staging cleanup failed after {type(error).__name__} "
                f"({error}); cleanup error: {cleanup_error}; promotion remains blocked"
            ) from error
        raise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _primary_deployment_id(observed: dict, service: str) -> str:
    for deployment in observed.get("deployments") or []:
        if deployment.get("status") == "PRIMARY":
            deployment_id = deployment.get("id")
            if deployment_id:
                return deployment_id
    raise ReadError(f"no PRIMARY deployment for service {service}")


def _load_staging_record(path: str) -> StagingOperationRecord:
    try:
        raw = json.loads(Path(path).read_text())
    except OSError as error:
        raise ReadError(f"cannot read staging record {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ValidationError(f"staging record {path} is not valid JSON: {error}") from error
    try:
        record = StagingOperationRecord.model_validate(raw)
    except PydanticValidationError as error:
        raise ValidationError(f"staging record {path} failed schema validation: {error}") from error
    errors = validate_record(record)
    if errors:
        raise ValidationError(f"staging record {path} is invalid: {'; '.join(errors)}")
    return record


def _find_previous_release(api: GitHubApi) -> ReleaseManifest | None:
    """Locate the newest published official release's manifest (D5).

    Only published (non-draft, non-prerelease) releases qualify; drafts and
    prereleases are never promotion sources, so their manifests must not
    become the "previous official frontend" either. The newest qualifying
    release must carry a ``release-manifest.json`` asset — a published
    official release without one fails closed instead of silently falling
    back to an older frontend. The bootstrap exception applies only when
    zero qualifying releases exist.
    """
    releases = api.list_releases()
    qualifying = [
        release
        for release in releases
        if not release.get("draft") and not release.get("prerelease")
    ]
    if not qualifying:
        return None
    release = qualifying[0]
    manifest_asset = next(
        (asset for asset in release["assets"] if asset["name"] == _RELEASE_MANIFEST_ASSET),
        None,
    )
    if manifest_asset is None:
        raise AbsentResourceError(
            f"newest published official release {release['tag_name']} has no "
            f"{_RELEASE_MANIFEST_ASSET} asset (AD-15)"
        )
    manifest_bytes = api.download_asset(manifest_asset["url"])
    try:
        manifest = ReleaseManifest.model_validate_json(manifest_bytes)
    except PydanticValidationError as error:
        raise ValidationError(
            f"official release {release['tag_name']} manifest is malformed: {error}"
        ) from error
    errors = validate_record(manifest)
    if errors:
        raise ValidationError(
            f"official release {release['tag_name']} manifest is invalid: {'; '.join(errors)}"
        )
    return manifest


def _redact_service(service: dict) -> dict:
    redacted = {}
    for key, value in service.items():
        if (
            key in {"events", "deployments", "loadBalancers", "taskDefinition"}
            or isinstance(value, (str, int, float, bool))
            or value is None
        ):
            redacted[key] = value
    return redacted


def _write_out(path: str, record) -> None:
    try:
        Path(path).write_text(canonical_json(record.model_dump(mode="json")) + "\n")
    except OSError as error:
        raise ReadError(f"cannot write record to {path}: {error}") from error
