#!/usr/bin/env python3
"""Authorize candidates, publish exact images, and validate an owned runtime attempt.

Only trusted main workflows may call this command. GitHub CLI receives its job
token through GH_TOKEN; API errors are never echoed. CI evidence is bounded inert
data from an unchanged trusted workflow, not a PR's provenance claim. No AWS
credentials or calls are needed except for isolated publication/locked validation. Builds run only candidate
Dockerfiles in Docker, with job/cloud tokens removed from client environments.
Build/verification failure leaves no accepted manifest; no command publishes a
successful AWS result outside the locked validation command. Publication verifies the trusted
workflow artifact digest and manifest independently, uses five fixed ECR targets,
and compares pushed digests without running image code. Partial pushes remain
bounded by repository lifecycle policies; failed publication emits no receipt.
Validation joins immutable operation recovery, tag-only saved planning/apply,
trusted S3/SSM host execution and bound stages/report/cleanup evidence. Success
requires the current/latest candidate attempt; failure finalization preserves
an already completed owned outcome. Missing/disposed state requires Task 7.
"""

import argparse
import base64
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

from aws_cloud_reconciliation import ReconciliationBlocked, observe_cloud

REPOSITORY = "Djimi/OnlineShop-full-stack"
API_ROOT = f"repos/{REPOSITORY}"
CI_WORKFLOW = ".github/workflows/ci.yml"
CHECK_NAME = "AWS validation"
CONTROLLER_WORKFLOWS = ("aws-validation.yml", "aws-check-proof.yml")
SHA = re.compile(r"[0-9a-f]{40}")
LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})(?:\[bot\])?")
MAX_EVIDENCE_BYTES = 65_536
REQUIRED_CI_JOBS = {"Java (Auth)", "Java (API Gateway)", "Java (common -> Items)",
                    "Frontend", "Images and PR E2E", "Candidate identity"}
IMAGE_NAMES = {"auth", "items", "gateway", "frontend", "e2e"}
BUILD_CONTEXTS = {"auth": "Auth", "items": "", "gateway": "api-gateway", "frontend": "frontend", "e2e": "e2e-tests"}
MAX_IMAGE_BYTES = 4 * 1024**3
MAX_FIXTURE_BYTES = 16 * 1024**2
REGION = "eu-north-1"
REPORT_SUITES = {"com.onlineshop.e2e.ItemsE2ETest": 3,
                 "com.onlineshop.e2e.RestAssuredLoggingTest": 1}


class RequestRejected(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    request = commands.add_parser(
        "request", help="Freeze CI-verified PR identity; create a pending check"
    )
    request.add_argument("--pr", type=positive_integer, required=True)
    request.add_argument("--output", type=Path, required=True)
    verify = commands.add_parser(
        "verify-build", help="Validate exact candidate build archives as inert data"
    )
    verify.add_argument("--request", type=Path, required=True)
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--artifacts", type=Path, required=True)
    verify.add_argument("--output", type=Path, required=True)
    failure = commands.add_parser(
        "finalize-failure", help="Complete only this attempt's pending check as failure"
    )
    failure.add_argument("--request", type=Path, required=True)
    success = commands.add_parser(
        "finalize-success",
        help="Verify retained runtime evidence before completing the owned check",
    )
    success.add_argument("--request", type=Path, required=True)
    success.add_argument("--artifact-id", required=True)
    success.add_argument("--artifact-digest", required=True)
    build = commands.add_parser(
        "build", help="Package the exact clean candidate in a credential-free build job"
    )
    build.add_argument("--request", type=Path, required=True)
    build.add_argument("--candidate", type=Path, required=True)
    build.add_argument("--artifacts", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    publish = commands.add_parser(
        "publish",
        help="Publish trusted exact build archives to five fixed ECR repositories",
    )
    publish.add_argument("--request", type=Path, required=True)
    publish.add_argument("--manifest", type=Path, required=True)
    publish.add_argument("--artifacts", type=Path, required=True)
    publish.add_argument("--output", type=Path, required=True)
    reports = commands.add_parser(
        "verify-reports",
        help="Verify four executed tests as inert data; never publish success",
    )
    reports.add_argument("--request", type=Path, required=True)
    reports.add_argument("--reports", type=Path, required=True)
    reports.add_argument("--output", type=Path, required=True)
    cloud = commands.add_parser(
        "reconcile-cloud", help="Observe fixed state/EC2/SSM; never authorize mutation"
    )
    cloud.add_argument("--request", type=Path, required=True)
    cloud.add_argument("--output", type=Path, required=True)
    publication = commands.add_parser(
        "verify-publication",
        help="Authenticate trusted publisher receipt and fixture bytes without AWS",
    )
    publication.add_argument("--request", type=Path, required=True)
    publication.add_argument("--fixtures", type=Path, required=True)
    publication.add_argument("--output", type=Path, required=True)
    for name in ["validate-preflight", "validate"]:
        validation = commands.add_parser(
            name,
            help="Authorize current publication; validate holds the operator session lock",
        )
        validation.add_argument("--request", type=Path, required=True)
        validation.add_argument("--images", type=Path, required=True)
        validation.add_argument("--fixtures", type=Path, required=True)
        validation.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        context = trusted_dispatch()
        authorize_actors(context)
        if args.command in {"validate-preflight", "validate"}:
            from aws_runtime_orchestration import validate_session, verify_controller

            record = read_request(args.request, context)
            verify_controller(record)
            evidence = verify_publication(record, args.fixtures)
            if bounded_json(args.images) != evidence["receipt"]:
                raise RequestRejected(
                    "local images differ from authenticated publication"
                )
            read_request(args.request, context)
            if args.command == "validate-preflight":
                print(
                    "Actors, current candidate and exact publication authorized before AWS exchange"
                )
                return 0
            validate_session(
                record,
                evidence["receipt"],
                args.fixtures,
                args.output,
                recheck=lambda: read_request(args.request, context),
                verify_reports=verify_test_reports,
            )
            print(
                "Current candidate passed bound AWS runtime stages and four report-backed tests"
            )
            return 0
        if args.command == "finalize-success":
            record = read_request(args.request, context)
            verify_retained_runtime_evidence(
                record, args.artifact_id, args.artifact_digest
            )
            # Candidate and latest attempt are re-read only after the exact retained
            # artifact has been fetched, digested, and parsed in this locked job.
            record = read_request(args.request, context)
            github(
                f"check-runs/{record['check_run_id']}",
                method="PATCH",
                body={
                    "status": "completed",
                    "conclusion": "success",
                    "output": {
                        "title": "AWS validation passed",
                        "summary": "The exact candidate completed provision, reset, readiness, four executed tests and verified cleanup. Sanitized evidence is retained for at least 14 days.",
                    },
                    "details_url": f"https://github.com/{record['repository']}/actions/runs/{record['validation_run_id']}",
                },
            )
            print("Owned check completed after exact runtime artifact verification")
            return 0
        if args.command == "verify-publication":
            record = read_request(args.request, context)
            evidence = verify_publication(record, args.fixtures)
            read_request(args.request, context)
            write_request(args.output, evidence)
            print(
                "Trusted publisher receipt and fixture bytes verified; no image execution or AWS success"
            )
            return 0
        if args.command == "reconcile-cloud":
            record = read_request(args.request, context)
            observation = observe_cloud()
            read_request(args.request, context)
            observation.update({key: record[key] for key in [
                "repository", "candidate_sha", "controller_sha", "validation_run_id", "validation_run_attempt",
            ]})
            write_request(args.output, observation)
            print("Cloud boundaries observed; host/ledger reconciliation still required, no mutation authorized")
            return 0
        if args.command == "verify-reports":
            record = read_request(args.request, context)
            summary = verify_test_reports(args.reports)
            read_request(args.request, context)
            write_request(args.output, {
                "candidate_sha": record["candidate_sha"],
                "validation_run_id": record["validation_run_id"],
                "validation_run_attempt": record["validation_run_attempt"],
                "status": "reports-verified", "tests": sum(summary.values()),
                "suites": summary, "aws_validation_success": False,
                "runtime_provenance_verified": False,
            })
            print("Reports declare four successful test cases; runtime provenance unproved, no AWS success published")
            return 0
        if args.command == "publish":
            record = read_request(args.request, context)
            manifest = verify_archives(record, args.manifest, args.artifacts)
            artifact = verify_build_provenance(record, manifest)
            read_request(args.request, context)
            published = publish_images(record, manifest, args.artifacts)
            published.update(build_artifact_id=artifact["id"], build_artifact_digest=artifact["digest"])
            read_request(args.request, context)
            write_request(args.output, published)
            print("Five fixed repository digests published without running candidate image code")
            return 0
        if args.command == "build":
            record = read_request(args.request, context)
            manifest = build_candidate(record, args.candidate, args.artifacts)
            read_request(args.request, context)
            write_request(args.output, manifest)
            print("Five exact candidate archives and independent fixtures packaged; no publication or deployment")
            return 0
        if args.command == "finalize-failure":
            record = read_request(args.request, context, require_current=False, allow_completed=True)
            if github(f"check-runs/{record['check_run_id']}").get("status") == "completed":
                print("Owned check already completed; finalizer preserves its outcome")
                return 0
            github(f"check-runs/{record['check_run_id']}", method="PATCH", body={
                "status": "completed", "conclusion": "failure",
                "output": {"title": "AWS validation unsuccessful",
                           "summary": "This attempt did not complete all required validation stages; see its linked workflow evidence."},
            })
            print("Owned attempt completed as failure; no success inferred")
            return 0
        if args.command == "verify-build":
            record = read_request(args.request, context)
            manifest = verify_archives(record, args.manifest, args.artifacts)
            write_request(args.output, manifest)
            print("Exact candidate image/fixture archives verified; no image code executed")
            return 0
        identities = resolve_candidate(args.pr)
        ci = verify_source_ci(identities, context)
        reject_duplicate_attempt(identities, context)
        if resolve_candidate(args.pr) != identities:
            raise RequestRejected("PR or main changed during resolution; retry manually")
        record = {**identities, **ci, **context}
        record["check_run_id"] = create_pending_check(record)
        write_request(args.output, record)
        print(json.dumps(record, sort_keys=True))
        return 0
    except (RequestRejected, ReconciliationBlocked) as error:
        print(f"Request rejected: {error}; correct prerequisites and retry manually", file=sys.stderr)
        return 1
    except (KeyError, TypeError, ValueError, OSError, IndexError, AttributeError,
            StopIteration, subprocess.TimeoutExpired, argparse.ArgumentTypeError):
        # External values and exception bodies can contain tokens. Only fixed
        # rejection diagnostics reach logs; detailed secrets never do.
        print("Request rejected: authorization, candidate or exact CI evidence is invalid; retry manually after correcting prerequisites", file=sys.stderr)
        return 1


def positive_integer(value):
    if not re.fullmatch(r"[1-9][0-9]{0,18}", str(value)):
        raise argparse.ArgumentTypeError("expected a positive decimal integer")
    return int(value)


def verify_publication(record, fixtures):
    run_id, attempt = record["validation_run_id"], record["validation_run_attempt"]
    source = github(f"actions/runs/{run_id}/attempts/{attempt}")
    if (source.get("id") != run_id or source.get("run_attempt") != attempt
            or source.get("repository", {}).get("full_name") != REPOSITORY
            or source.get("head_branch") != "main" or source.get("head_sha") != record["controller_sha"]
            or source.get("path") != ".github/workflows/aws-validation.yml"
            or source.get("event") != "workflow_dispatch" or source.get("status") not in {"in_progress", "completed"}):
        raise RequestRejected("publication source is not this trusted main attempt")
    jobs = list(github_list(f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs"))
    for name in ["Candidate request", "Build candidate", "Publish candidate"]:
        matches = [job for job in jobs if job.get("name") == name]
        if len(matches) != 1 or matches[0].get("status") != "completed" or matches[0].get("conclusion") != "success":
            raise RequestRejected("trusted publication prerequisite did not pass")
    artifacts = github_list(f"actions/runs/{run_id}/artifacts", "artifacts")
    matches = [artifact for artifact in artifacts if artifact.get("name") == f"aws-images-{run_id}-{attempt}"]
    if len(matches) != 1:
        raise RequestRejected("trusted publication artifact is missing or ambiguous")
    artifact = matches[0]
    if (artifact.get("expired") is not False or type(artifact.get("size_in_bytes")) is not int
            or not 0 < artifact["size_in_bytes"] <= MAX_EVIDENCE_BYTES
            or artifact.get("workflow_run", {}).get("id") != run_id
            or artifact.get("workflow_run", {}).get("head_sha") != record["controller_sha"]
            or not isinstance(artifact.get("digest"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact["digest"])):
        raise RequestRejected("invalid trusted publication artifact metadata")
    artifact_id = positive_integer(artifact["id"])
    data = github(f"actions/artifacts/{artifact_id}/zip", binary=True)
    if (not 0 < len(data) <= MAX_EVIDENCE_BYTES
            or "sha256:" + hashlib.sha256(data).hexdigest() != artifact["digest"]):
        raise RequestRejected("publication artifact digest mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if (len(members) != 1 or members[0].filename != "images.json"
                    or not 0 < members[0].file_size <= MAX_EVIDENCE_BYTES
                    or (members[0].external_attr >> 16) & 0o170000 == 0o120000):
                raise RequestRejected("unsafe publication receipt archive")
            receipt = json.loads(archive.read(members[0]), object_pairs_hook=unique_fields)
    except (zipfile.BadZipFile, RuntimeError):
        raise RequestRejected("corrupt publication receipt archive") from None
    if set(receipt) != set(record) | {"images", "fixtures", "build_artifact_id", "build_artifact_digest"} or any(
        receipt.get(key) != value for key, value in record.items()
    ):
        raise RequestRejected("publication receipt candidate or attempt mismatch")
    account = os.environ.get("AWS_TESTING_ACCOUNT_ID", "")
    if not re.fullmatch(r"[0-9]{12}", account) or set(receipt["images"]) != IMAGE_NAMES:
        raise RequestRejected("invalid fixed publication targets")
    for name, uri in receipt["images"].items():
        prefix = f"{account}.dkr.ecr.{REGION}.amazonaws.com/onlineshop-test-{name}"
        if not isinstance(uri, str) or not re.fullmatch(re.escape(prefix) + r"@sha256:[0-9a-f]{64}", uri):
            raise RequestRejected("publication image is mutable or outside the fixed targets")
    if (type(receipt["build_artifact_id"]) is not int or receipt["build_artifact_id"] <= 0
            or not isinstance(receipt["build_artifact_digest"], str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", receipt["build_artifact_digest"])):
        raise RequestRejected("invalid source build artifact identity")
    if fixtures.name != "fixtures.tar":
        raise RequestRejected("unexpected fixture transport name")
    path = verify_archive_checksum(fixtures.parent, receipt["fixtures"], "fixtures.tar", MAX_FIXTURE_BYTES)
    verify_fixture_archive(path)
    return {"status": "publication-verified", "receipt": receipt, "publication_artifact_id": artifact_id,
            "publication_artifact_digest": artifact["digest"], "aws_validation_success": False}


def verify_retained_runtime_evidence(record, artifact_id, artifact_digest):
    if os.environ.get("GITHUB_JOB") != "validate":
        raise RequestRejected(
            "runtime evidence may only finalize inside the locked job"
        )
    artifact_id = positive_integer(artifact_id)
    if not isinstance(artifact_digest, str) or not re.fullmatch(
        r"sha256:[0-9a-f]{64}", artifact_digest
    ):
        raise RequestRejected("invalid retained artifact digest")

    run = github(
        f"actions/runs/{record['validation_run_id']}/attempts/{record['validation_run_attempt']}"
    )
    if (
        run.get("id") != record["validation_run_id"]
        or run.get("run_attempt") != record["validation_run_attempt"]
        or run.get("head_branch") != "main"
        or run.get("head_sha") != record["controller_sha"]
        or run.get("path") != ".github/workflows/aws-validation.yml"
        or run.get("event") != "workflow_dispatch"
        or run.get("status") != "in_progress"
    ):
        raise RequestRejected(
            "runtime artifact is not from this trusted active workflow attempt"
        )

    jobs = list(
        github_list(
            f"actions/runs/{record['validation_run_id']}/attempts/{record['validation_run_attempt']}/jobs",
            "jobs",
        )
    )
    matches = [
        job for job in jobs if job.get("name") == "Validate current candidate in AWS"
    ]
    if (
        len(matches) != 1
        or matches[0].get("status") != "in_progress"
        or matches[0].get("run_id") != record["validation_run_id"]
        or matches[0].get("head_sha") != record["controller_sha"]
    ):
        raise RequestRejected("locked validation job identity is not active")
    steps = [
        step
        for step in matches[0].get("steps", [])
        if step.get("name") == "Retain sanitized runtime evidence"
    ]
    if (
        len(steps) != 1
        or steps[0].get("status") != "completed"
        or steps[0].get("conclusion") != "success"
    ):
        raise RequestRejected("sanitized evidence upload did not complete successfully")

    artifacts = list(
        github_list(
            f"actions/runs/{record['validation_run_id']}/artifacts", "artifacts"
        )
    )
    named = [
        artifact
        for artifact in artifacts
        if artifact.get("name")
        == f"aws-runtime-evidence-{record['validation_run_id']}-{record['validation_run_attempt']}"
    ]
    if len(named) != 1:
        raise RequestRejected("runtime artifact is missing or ambiguous")
    artifact = named[0]
    if (
        artifact.get("id") != artifact_id
        or artifact.get("expired") is not False
        or type(artifact.get("size_in_bytes")) is not int
        or not 0 < artifact["size_in_bytes"] <= MAX_EVIDENCE_BYTES
        or artifact.get("workflow_run", {}).get("id") != record["validation_run_id"]
        or artifact.get("workflow_run", {}).get("head_sha") != record["controller_sha"]
        or artifact.get("digest") != artifact_digest
    ):
        raise RequestRejected(
            "runtime artifact metadata does not match this workflow output"
        )
    try:
        created = datetime.fromisoformat(artifact["created_at"].replace("Z", "+00:00"))
        expires = datetime.fromisoformat(artifact["expires_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        raise RequestRejected(
            "runtime artifact retention metadata is invalid"
        ) from None
    if (
        created.tzinfo is None
        or expires.tzinfo is None
        or (expires - created).total_seconds() < 14 * 86400
    ):
        raise RequestRejected("runtime artifact retention is shorter than 14 days")

    data = github(f"actions/artifacts/{artifact_id}/zip", binary=True)
    if (
        not 0 < len(data) <= MAX_EVIDENCE_BYTES
        or "sha256:" + hashlib.sha256(data).hexdigest() != artifact_digest
    ):
        raise RequestRejected("runtime artifact download digest mismatch")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            names = {"summary.json", "reports.tar"}
            if (
                len(members) != len(names)
                or {member.filename for member in members} != names
                or sum(member.file_size for member in members) > MAX_EVIDENCE_BYTES
                or any(
                    member.file_size > MAX_EVIDENCE_BYTES
                    or ((member.external_attr >> 16) & 0o170000) == 0o120000
                    for member in members
                )
            ):
                raise RequestRejected(
                    "runtime artifact content is unsafe or incomplete"
                )
            content = {member.filename: archive.read(member) for member in members}
    except (zipfile.BadZipFile, RuntimeError):
        raise RequestRejected("runtime artifact archive is corrupt") from None
    try:
        summary = json.loads(content["summary.json"], object_pairs_hook=unique_fields)
    except (UnicodeError, json.JSONDecodeError):
        raise RequestRejected("runtime summary is invalid") from None
    if (
        summary.get("status") != "pending-success"
        or summary.get("stage") != "evidence-retained-pending-success"
        or summary.get("candidate_sha") != record["candidate_sha"]
        or summary.get("validation_run_id") != record["validation_run_id"]
        or summary.get("validation_run_attempt") != record["validation_run_attempt"]
        or summary.get("aws_validation_success") is not False
        or summary.get("runtime_provenance_verified") is not True
        or summary.get("durable_terminal") is not True
        or summary.get("stages")
        != ["provision", "reset", "readiness", "e2e", "reports"]
        or summary.get("cleanup_verified") is not True
        or summary.get("tests") != 4
        or summary.get("suites") != REPORT_SUITES
    ):
        raise RequestRejected("runtime summary does not prove the required stages")
    with tempfile.TemporaryDirectory(prefix="aws-runtime-evidence-") as temporary:
        reports = Path(temporary) / "reports.tar"
        reports.write_bytes(content["reports.tar"])
        if verify_test_reports(reports) != REPORT_SUITES:
            raise RequestRejected(
                "retained runtime reports do not match the required suites"
            )


def verify_test_reports(path):
    # Report identity/transport must still be established by the locked runtime.
    # This parser proves report content, not that deployment or execution occurred.
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 8 * 1024**2
    ):
        raise RequestRejected("unsafe or excessive report archive")
    filenames = {"TEST-" + suite + ".xml": suite for suite in REPORT_SUITES}
    verified = {}
    try:
        # Uncompressed transport also bounds tar header/PAX metadata before the
        # parser encounters a file member; compressed size alone cannot do that.
        with tarfile.open(path, "r|") as archive:
            for member in safe_tar_members(archive, 1024**2):
                if (
                    not member.isfile()
                    or member.name not in filenames
                    or member.size > 512 * 1024
                ):
                    raise RequestRejected("unexpected report archive member")
                data = archive.extractfile(member).read().decode("utf-8-sig")
                if "<!DOCTYPE" in data.upper() or "<!ENTITY" in data.upper():
                    raise RequestRejected("report XML declarations are forbidden")
                suite = ET.fromstring(data)
                expected_name = filenames[member.name]
                count = REPORT_SUITES[expected_name]
                if suite.tag != "testsuite" or suite.get("name") != expected_name:
                    raise RequestRejected("unrecognized report suite")
                if any(
                    suite.get(key) != str(value)
                    for key, value in {
                        "tests": count,
                        "failures": 0,
                        "errors": 0,
                        "skipped": 0,
                    }.items()
                ):
                    raise RequestRejected(
                        "reports are not four successful executed tests"
                    )
                cases = suite.findall("testcase")
                names = [case.get("name", "") for case in cases]
                if (
                    len(cases) != count
                    or len(set(names)) != count
                    or any(not name for name in names)
                    or any(case.get("classname") != expected_name for case in cases)
                    or any(
                        node.tag in {"failure", "error", "skipped"}
                        for node in suite.iter()
                    )
                ):
                    raise RequestRejected("report counters do not match executed cases")
                verified[expected_name] = count
    except (tarfile.TarError, ET.ParseError, UnicodeError, EOFError):
        raise RequestRejected("invalid report archive or XML") from None
    if set(verified) != set(REPORT_SUITES):
        raise RequestRejected("required test reports are missing")
    return verified


def require_sha(value):
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise RequestRejected("invalid revision")
    return value


def trusted_dispatch():
    env = os.environ
    workflows = {
        f"{REPOSITORY}/.github/workflows/{name}@refs/heads/main"
        for name in CONTROLLER_WORKFLOWS
    }
    if (
        env.get("GITHUB_REPOSITORY") != REPOSITORY
        or env.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or env.get("GITHUB_REF") != "refs/heads/main"
        or env.get("GITHUB_WORKFLOW_REF") not in workflows
        or not env.get("GH_TOKEN")
    ):
        raise RequestRejected("untrusted dispatch context")
    controller = require_sha(env.get("GITHUB_SHA"))
    if env.get("GITHUB_WORKFLOW_SHA") != controller:
        raise RequestRejected("workflow and controller differ")
    actor, triggering = (
        env.get("GITHUB_ACTOR", ""),
        env.get("GITHUB_TRIGGERING_ACTOR", ""),
    )
    if not LOGIN.fullmatch(actor) or not LOGIN.fullmatch(triggering):
        raise RequestRejected("invalid actor")
    return {
        "controller_sha": controller,
        "actor": actor,
        "triggering_actor": triggering,
        "validation_run_id": positive_integer(env.get("GITHUB_RUN_ID", "")),
        "validation_run_attempt": positive_integer(env.get("GITHUB_RUN_ATTEMPT", "")),
    }


def github(path, *, method="GET", body=None, binary=False):
    command = ["gh", "api", f"{API_ROOT}/{path}", "--method", method]
    if body is not None:
        command += ["--input", "-"]
    try:
        result = subprocess.run(
            command,
            input=json.dumps(body).encode() if body is not None else None,
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raise RequestRejected("GitHub API unavailable") from None
    if result.returncode or len(result.stdout) > 4 * 1024 * 1024:
        raise RequestRejected("GitHub API failed")
    return (
        result.stdout
        if binary
        else json.loads(result.stdout, object_pairs_hook=unique_fields)
    )


def github_list(path, key):
    separator = "&" if "?" in path else "?"
    for page in range(1, 11):
        values = github(f"{path}{separator}per_page=100&page={page}")[key]
        if not isinstance(values, list):
            raise RequestRejected("invalid API list")
        yield from values
        if len(values) < 100:
            return
    raise RequestRejected("API listing exceeds bound")


def authorize_actors(context):
    for actor in sorted({context["actor"], context["triggering_actor"]}):
        if github(f"collaborators/{actor}/permission").get("permission") != "admin":
            raise RequestRejected("actor requires repository admin permission")


def resolve_candidate(number):
    pr = github(f"pulls/{number}")
    if (pr.get("number") != number or pr.get("state") != "open" or pr.get("draft") is not False
            or pr.get("mergeable") is not True or pr["user"]["login"].lower() == "dependabot[bot]"
            or pr["head"]["repo"]["full_name"] != REPOSITORY
            or pr["base"]["repo"]["full_name"] != REPOSITORY or pr["base"]["ref"] != "main"):
        raise RequestRejected("unsupported PR")
    head, base = require_sha(pr["head"]["sha"]), require_sha(pr["base"]["sha"])
    candidate = require_sha(github(f"git/ref/pull/{number}/merge")["object"]["sha"])
    if (candidate != require_sha(pr["merge_commit_sha"])
            or base != require_sha(github("git/ref/heads/main")["object"]["sha"])):
        raise RequestRejected("ambiguous merge candidate")
    return {"repository": REPOSITORY, "pr": number, "head_sha": head,
            "base_sha": base, "candidate_sha": candidate}


def verify_source_ci(identities, context):
    """Run metadata reports PR head, so a separate trusted artifact binds merge SHA."""
    head = identities["head_sha"]
    runs = list(github_list(f"actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}", "workflow_runs"))
    matching = [run for run in runs if run.get("event") == "pull_request" and run.get("head_sha") == head
                and run.get("path") == CI_WORKFLOW and run.get("repository", {}).get("full_name") == REPOSITORY]
    if not matching:
        raise RequestRejected("missing source CI")
    # The newest run must pass; never fall back to an older successful rerun.
    run = max(matching, key=lambda value: positive_integer(value["id"]))
    run_id, attempt = positive_integer(run["id"]), positive_integer(run["run_attempt"])
    current = github(f"actions/runs/{run_id}/attempts/{attempt}")
    identity_fields = ("id", "run_attempt", "head_sha", "event", "path", "repository", "status", "conclusion")
    if (any(current.get(field) != run.get(field) for field in identity_fields)
            or run.get("status") != "completed" or run.get("conclusion") != "success"):
        raise RequestRejected("exact CI must complete successfully; retry manually")
    jobs = list(github_list(f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs"))
    for name in REQUIRED_CI_JOBS:
        named_jobs = [job for job in jobs if job.get("name") == name]
        if (len(named_jobs) != 1 or named_jobs[0].get("status") != "completed"
                or named_jobs[0].get("conclusion") != "success"):
            raise RequestRejected("required CI job is missing, skipped or unsuccessful")
    trusted = github(f"contents/{CI_WORKFLOW}?ref={context['controller_sha']}")
    producer = github(f"contents/{CI_WORKFLOW}?ref={identities['candidate_sha']}")
    if (trusted.get("encoding") != "base64" or producer.get("encoding") != "base64"
            or base64.b64decode(trusted["content"]) != base64.b64decode(producer["content"])):
        raise RequestRejected("candidate edits trusted CI evidence producer")
    artifacts = list(github_list(f"actions/runs/{run_id}/artifacts", "artifacts"))
    matching_artifacts = [artifact for artifact in artifacts if artifact.get("name") == f"ci-candidate-{run_id}-{attempt}"]
    if len(matching_artifacts) != 1:
        raise RequestRejected("missing or ambiguous CI candidate evidence")
    artifact = matching_artifacts[0]
    if (artifact.get("expired") is not False or not 0 < artifact["size_in_bytes"] <= MAX_EVIDENCE_BYTES
            or artifact["workflow_run"]["id"] != run_id or artifact["workflow_run"]["head_sha"] != head):
        raise RequestRejected("invalid source artifact")
    artifact_id = positive_integer(artifact["id"])
    evidence = read_candidate_evidence(github(f"actions/artifacts/{artifact_id}/zip", binary=True))
    expected = {**identities, "ci_run_id": run_id, "ci_run_attempt": attempt, "event": "pull_request"}
    if evidence != expected:
        raise RequestRejected("source CI tested a different candidate")
    return {"ci_run_id": run_id, "ci_run_attempt": attempt, "ci_artifact_id": artifact_id}


def read_candidate_evidence(data):
    if len(data) > MAX_EVIDENCE_BYTES:
        raise RequestRejected("oversized source evidence")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            if (len(members) != 1 or members[0].filename != "candidate.json"
                    or members[0].file_size > MAX_EVIDENCE_BYTES
                    or (members[0].external_attr >> 16) & 0o170000 == 0o120000):
                raise RequestRejected("unsafe source evidence")
            return json.loads(archive.read(members[0]), object_pairs_hook=unique_fields)
    except (zipfile.BadZipFile, RuntimeError):
        raise RequestRejected("corrupt source evidence") from None


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise RequestRejected("duplicate JSON field")
        result[key] = value
    return result


def read_request(path, context, *, require_current=True, allow_completed=False):
    record = bounded_json(path)
    keys = {"repository", "pr", "head_sha", "base_sha", "candidate_sha", "controller_sha",
            "ci_run_id", "ci_run_attempt", "ci_artifact_id", "validation_run_id",
            "validation_run_attempt", "actor", "triggering_actor", "check_run_id"}
    if set(record) != keys or any(record.get(key) != value for key, value in context.items()):
        raise RequestRejected("request identity does not match this trusted attempt")
    for key in ["pr", "ci_run_id", "ci_run_attempt", "ci_artifact_id", "check_run_id"]:
        positive_integer(record[key])
    for key in ["head_sha", "base_sha", "candidate_sha", "controller_sha"]:
        require_sha(record[key])
    if record["repository"] != REPOSITORY:
        raise RequestRejected("request repository mismatch")
    if require_current:
        identities = resolve_candidate(record["pr"])
        if any(record[key] != value for key, value in identities.items()):
            raise RequestRejected("candidate changed since request")
    check = github(f"check-runs/{record['check_run_id']}")
    if (check.get("name") != CHECK_NAME or check.get("head_sha") != record["candidate_sha"]
            or check.get("external_id") != attempt_identity(record)
            or check.get("app", {}).get("slug") != "github-actions"
            or check.get("status") not in ({"in_progress", "completed"} if allow_completed else {"in_progress"})):
        raise RequestRejected("request does not own a pending candidate check")
    if require_current:
        latest = [value for value in github_list(
            f"commits/{record['candidate_sha']}/check-runs?check_name=AWS%20validation&filter=latest", "check_runs")
            if value.get("app", {}).get("slug") == "github-actions"]
        if len(latest) != 1 or latest[0].get("id") != record["check_run_id"]:
            raise RequestRejected("a newer candidate attempt superseded this request")
    return record


def bounded_json(path):
    if path.is_symlink() or path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise RequestRejected("unsafe or excessive JSON input")
    return json.loads(path.read_bytes(), object_pairs_hook=unique_fields)


def build_candidate(record, candidate, artifacts):
    if candidate.is_symlink() or not candidate.is_dir():
        raise RequestRejected("unsafe candidate directory")
    candidate = candidate.resolve()
    if artifacts.resolve().is_relative_to(candidate):
        raise RequestRejected("build artifacts must remain outside candidate checkout")
    verify_checkout(candidate, record["candidate_sha"])
    artifacts.mkdir(mode=0o700, parents=True, exist_ok=False)
    images = {}
    for name, context in BUILD_CONTEXTS.items():
        dockerfile = candidate / ("Items" if name == "items" else context) / "Dockerfile"
        if dockerfile.is_symlink() or not dockerfile.is_file():
            raise RequestRejected("missing or unsafe candidate Dockerfile")
        tag = f"onlineshop-test-{name}:run-{record['validation_run_id']}-attempt-{record['validation_run_attempt']}"
        run_candidate_build(["docker", "build", "--platform", "linux/amd64", "--tag", tag,
                             "--file", str(dockerfile), str(candidate / context)])
        path = artifacts / f"{name}.tar"
        run_candidate_build(["docker", "save", "--output", str(path), tag])
        images[name] = archive_metadata(path, MAX_IMAGE_BYTES)
    fixtures = artifacts / "fixtures.tar"
    with tarfile.open(fixtures, "x") as archive:
        for service in ["Auth", "Items"]:
            directory = candidate / service / "init-db"
            if directory.is_symlink() or not directory.is_dir():
                raise RequestRejected("missing or unsafe service fixture directory")
            for path in sorted(directory.iterdir()):
                if path.name == "02-seed-data.sql" and service == "Auth":
                    continue
                if path.is_symlink() or not path.is_file() or path.suffix != ".sql":
                    raise RequestRejected("unexpected service fixture entry")
                if path.stat().st_size > MAX_FIXTURE_BYTES:
                    raise RequestRejected("excessive service fixture")
                archive.add(path, arcname=path.relative_to(candidate).as_posix(), recursive=False)
    verify_checkout(candidate, record["candidate_sha"])
    manifest = {key: record[key] for key in ["repository", "candidate_sha", "controller_sha", "ci_run_id", "ci_run_attempt"]}
    manifest.update(build_run_id=record["validation_run_id"], build_run_attempt=record["validation_run_attempt"],
                    images=images, fixtures=archive_metadata(fixtures, MAX_FIXTURE_BYTES))
    path = artifacts / "build.json"
    write_request(path, manifest)
    verify_archives(record, path, artifacts)
    return manifest


def verify_checkout(candidate, revision):
    for command, expected in [(["git", "-C", str(candidate), "rev-parse", "HEAD"], revision),
                              (["git", "-C", str(candidate), "status", "--porcelain", "--untracked-files=all"], "")]:
        try:
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
        except (subprocess.TimeoutExpired, FileNotFoundError):
            raise RequestRejected("candidate checkout cannot be verified") from None
        if result.returncode or result.stdout.strip() != expected:
            raise RequestRejected("candidate checkout revision or cleanliness mismatch")


def run_candidate_build(command):
    # Candidate code executes only in Docker's build context, never as a controller
    # script. No cloud/job tokens are inherited even by the Docker client process.
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("AWS_", "GITHUB_", "ACTIONS_")) and key not in {"GH_TOKEN", "GH_ENTERPRISE_TOKEN"}}
    try:
        result = subprocess.run(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=1200, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raise RequestRejected("candidate image packaging timed out or is unavailable") from None
    if result.returncode:
        raise RequestRejected("candidate image packaging failed")


def archive_metadata(path, limit):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= limit:
        raise RequestRejected("unsafe or excessive packaged archive")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
    return {"file": path.name, "size": path.stat().st_size, "sha256": digest.hexdigest()}


def verify_build_provenance(record, manifest):
    run_id, attempt = record["validation_run_id"], record["validation_run_attempt"]
    if os.environ.get("GITHUB_WORKFLOW_REF") != f"{REPOSITORY}/.github/workflows/aws-validation.yml@refs/heads/main":
        raise RequestRejected("publication requires the trusted validation workflow")
    run = github(f"actions/runs/{run_id}/attempts/{attempt}")
    expected = {"id": run_id, "run_attempt": attempt, "head_sha": record["controller_sha"],
                "event": "workflow_dispatch", "path": ".github/workflows/aws-validation.yml"}
    if any(run.get(key) != value for key, value in expected.items()) or run.get("repository", {}).get("full_name") != REPOSITORY:
        raise RequestRejected("build workflow provenance mismatch")
    jobs = [job for job in github_list(f"actions/runs/{run_id}/attempts/{attempt}/jobs", "jobs")
            if job.get("name") == "Build candidate"]
    if len(jobs) != 1 or jobs[0].get("status") != "completed" or jobs[0].get("conclusion") != "success":
        raise RequestRejected("trusted candidate build must complete successfully")
    artifacts = [artifact for artifact in github_list(f"actions/runs/{run_id}/artifacts", "artifacts")
                 if artifact.get("name") == f"aws-build-{run_id}-{attempt}"]
    if len(artifacts) != 1:
        raise RequestRejected("missing or ambiguous trusted build artifact")
    artifact = artifacts[0]
    limit = len(IMAGE_NAMES) * MAX_IMAGE_BYTES + MAX_FIXTURE_BYTES + MAX_EVIDENCE_BYTES
    if (artifact.get("expired") is not False or not 0 < artifact["size_in_bytes"] <= limit
            or artifact["workflow_run"]["id"] != run_id
            or artifact["workflow_run"]["head_sha"] != record["controller_sha"]
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", artifact.get("digest", ""))):
        raise RequestRejected("invalid trusted build artifact identity")
    with tempfile.TemporaryDirectory(prefix="aws-build-provenance-") as directory:
        path = Path(directory) / "artifact.zip"
        with path.open("xb") as stream:
            try:
                result = subprocess.run(["gh", "api", f"{API_ROOT}/actions/artifacts/{positive_integer(artifact['id'])}/zip", "--method", "GET"],
                                        stdout=stream, stderr=subprocess.DEVNULL, timeout=1500, check=False)
            except (subprocess.TimeoutExpired, FileNotFoundError):
                raise RequestRejected("trusted artifact download failed") from None
        if result.returncode or not 0 < path.stat().st_size <= limit:
            raise RequestRejected("trusted artifact download failed or exceeds bound")
        if "sha256:" + archive_metadata(path, limit)["sha256"] != artifact["digest"]:
            raise RequestRejected("trusted artifact digest mismatch")
        try:
            with zipfile.ZipFile(path) as archive:
                members = archive.infolist()
                expected_names = {"build.json", "fixtures.tar", *(f"{name}.tar" for name in IMAGE_NAMES)}
                if (len(members) != len(expected_names) or {m.filename for m in members} != expected_names
                        or any((m.external_attr >> 16) & 0o170000 == 0o120000 for m in members)
                        or sum(m.file_size for m in members) > limit
                        or archive.getinfo("build.json").file_size > MAX_EVIDENCE_BYTES):
                    raise RequestRejected("unsafe trusted artifact archive")
                source = json.loads(archive.read("build.json"), object_pairs_hook=unique_fields)
                if source != manifest:
                    raise RequestRejected("local manifest differs from trusted build artifact")
                for metadata in [*manifest["images"].values(), manifest["fixtures"]]:
                    member = archive.getinfo(metadata["file"])
                    if member.file_size != metadata["size"]:
                        raise RequestRejected("trusted packaged image size mismatch")
                    digest = hashlib.sha256()
                    with archive.open(member) as stream:
                        for block in iter(lambda: stream.read(1024**2), b""):
                            digest.update(block)
                    if digest.hexdigest() != metadata["sha256"]:
                        raise RequestRejected("trusted packaged image checksum mismatch")
        except (zipfile.BadZipFile, RuntimeError):
            raise RequestRejected("corrupt trusted build artifact") from None
    return artifact


def publish_images(record, manifest, artifacts):
    account = os.environ.get("AWS_TESTING_ACCOUNT_ID", "")
    if not re.fullmatch(r"[0-9]{12}", account):
        raise RequestRejected("approved testing account configuration is missing")
    identity = json.loads(publication_command(["aws", "sts", "get-caller-identity", "--region", REGION, "--output", "json", "--no-cli-pager"]))
    if (identity.get("Account") != account
            or not identity.get("Arn", "").startswith(f"arn:aws:sts::{account}:assumed-role/onlineshop-test-publisher/")):
        raise RequestRejected("publisher role or target account mismatch")
    registry = f"{account}.dkr.ecr.{REGION}.amazonaws.com"
    published = {}
    with tempfile.TemporaryDirectory(prefix="aws-ecr-auth-") as directory:
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("AWS_", "GITHUB_", "ACTIONS_")) and key not in {"GH_TOKEN", "GH_ENTERPRISE_TOKEN"}}
        env["DOCKER_CONFIG"] = directory
        password = publication_command(["aws", "ecr", "get-login-password", "--region", REGION, "--no-cli-pager"])
        publication_command(["docker", "login", "--username", "AWS", "--password-stdin", registry], env=env, data=password)
        for name in sorted(IMAGE_NAMES):
            tag = f"run-{record['validation_run_id']}-attempt-{record['validation_run_attempt']}"
            local, remote = f"onlineshop-test-{name}:{tag}", f"{registry}/onlineshop-test-{name}:{tag}"
            publication_command(["docker", "load", "--input", str(artifacts / manifest["images"][name]["file"])], env=env)
            publication_command(["docker", "tag", local, remote], env=env)
            publication_command(["docker", "push", remote], env=env)
            details = json.loads(publication_command(["aws", "ecr", "describe-images", "--repository-name", f"onlineshop-test-{name}",
                                                     "--image-ids", f"imageTag={tag}", "--region", REGION, "--output", "json", "--no-cli-pager"]))["imageDetails"]
            if len(details) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", details[0].get("imageDigest", "")):
                raise RequestRejected("published registry digest is invalid")
            digest_uri = f"{registry}/onlineshop-test-{name}@{details[0]['imageDigest']}"
            local_digests = json.loads(publication_command(["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", remote], env=env))
            if digest_uri not in local_digests:
                raise RequestRejected("registry digest differs from pushed image")
            published[name] = digest_uri
    return {**record, "images": published, "fixtures": manifest["fixtures"]}


def publication_command(command, *, env=None, data=None):
    try:
        result = subprocess.run(command, env=env, input=data, capture_output=True, timeout=600, check=False)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raise RequestRejected("publication command timed out or is unavailable") from None
    if result.returncode or len(result.stdout) > 4 * 1024**2:
        raise RequestRejected("publication command failed")
    return result.stdout


def verify_archives(record, manifest_path, directory):
    manifest = bounded_json(manifest_path)
    if set(manifest) != {"repository", "candidate_sha", "controller_sha", "ci_run_id", "ci_run_attempt",
                        "build_run_id", "build_run_attempt", "images", "fixtures"}:
        raise RequestRejected("unexpected build manifest fields")
    expected = {key: record[key] for key in ["repository", "candidate_sha", "controller_sha", "ci_run_id", "ci_run_attempt"]}
    expected.update(build_run_id=record["validation_run_id"], build_run_attempt=record["validation_run_attempt"])
    if any(manifest.get(key) != value for key, value in expected.items()) or set(manifest["images"]) != IMAGE_NAMES:
        raise RequestRejected("build source or attempt identity mismatch")
    for name in sorted(IMAGE_NAMES):
        path = verify_archive_checksum(directory, manifest["images"][name], f"{name}.tar", MAX_IMAGE_BYTES)
        verify_image_archive(path, f"onlineshop-test-{name}:run-{record['validation_run_id']}-attempt-{record['validation_run_attempt']}")
    path = verify_archive_checksum(directory, manifest["fixtures"], "fixtures.tar", MAX_FIXTURE_BYTES)
    verify_fixture_archive(path)
    return manifest


def verify_archive_checksum(directory, metadata, filename, limit):
    if (set(metadata) != {"file", "size", "sha256"} or metadata["file"] != filename
            or type(metadata["size"]) is not int or not 0 < metadata["size"] <= limit
            or not isinstance(metadata["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])):
        raise RequestRejected("invalid archive metadata")
    path = directory / filename
    if path.is_symlink() or not path.is_file() or path.stat().st_size != metadata["size"]:
        raise RequestRejected("archive path or size mismatch")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024**2), b""):
            digest.update(block)
    if digest.hexdigest() != metadata["sha256"]:
        raise RequestRejected("archive checksum mismatch")
    return path


def safe_tar_members(archive, limit):
    seen, total = set(), 0
    for member in archive:
        path = Path(member.name)
        total += member.size
        if (path.is_absolute() or ".." in path.parts or not path.parts
                or member.name in seen or not (member.isfile() or member.isdir())
                or total > limit or len(seen) >= 10_000):
            raise RequestRejected("unsafe or excessive archive member")
        seen.add(member.name)
        yield member


def verify_image_archive(path, tag):
    try:
        with tarfile.open(path, "r:*") as archive:
            manifest = None
            for member in safe_tar_members(archive, MAX_IMAGE_BYTES):
                if member.name == "manifest.json":
                    if not member.isfile() or member.size > MAX_EVIDENCE_BYTES:
                        raise RequestRejected("unsafe Docker manifest")
                    manifest = json.loads(archive.extractfile(member).read(), object_pairs_hook=unique_fields)
            if not isinstance(manifest, list) or len(manifest) != 1 or manifest[0].get("RepoTags") != [tag]:
                raise RequestRejected("Docker archive contains unexpected image or mutable tag")
    except tarfile.TarError:
        raise RequestRejected("corrupt image archive") from None


def verify_fixture_archive(path):
    required = {"Auth/init-db/01-schema.sql", "Items/init-db/01-schema.sql"}
    files = set()
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in safe_tar_members(archive, MAX_FIXTURE_BYTES):
                if member.isdir():
                    continue
                if (not member.name.startswith(("Auth/init-db/", "Items/init-db/"))
                        or len(Path(member.name).parts) != 3 or not member.name.endswith(".sql")
                        or member.name == "Auth/init-db/02-seed-data.sql"):
                    raise RequestRejected("unexpected fixture path or committed authentication seed")
                files.add(member.name)
            if not required <= files:
                raise RequestRejected("both independently owned schemas are required")
    except tarfile.TarError:
        raise RequestRejected("corrupt fixture archive") from None


def reject_duplicate_attempt(identities, context):
    checks = github_list(f"commits/{identities['candidate_sha']}/check-runs?check_name=AWS%20validation&filter=all", "check_runs")
    external_id = attempt_identity(context)
    if any(check.get("external_id") == external_id and check.get("app", {}).get("slug") == "github-actions" for check in checks):
        raise RequestRejected("duplicate attempt")


def attempt_identity(record):
    return f"aws-validation:{record['validation_run_id']}:{record['validation_run_attempt']}"


def create_pending_check(record):
    check = github("check-runs", method="POST", body={
        "name": CHECK_NAME, "head_sha": record["candidate_sha"], "status": "in_progress",
        "external_id": attempt_identity(record),
        "details_url": f"https://github.com/{REPOSITORY}/actions/runs/{record['validation_run_id']}",
        "output": {"title": "Manually requested AWS validation pending",
                   "summary": f"PR #{record['pr']}; candidate `{record['candidate_sha']}`; actor `{record['triggering_actor']}`. No deployment or tests completed."},
    })
    if check.get("head_sha") != record["candidate_sha"]:
        raise RequestRejected("check candidate association mismatch")
    return positive_integer(check["id"])


def write_request(output, record):
    # Exclusive creation avoids replacing another attempt's record or following a symlink.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(record, stream, indent=2, sort_keys=True)
        stream.write("\n")


if __name__ == "__main__":
    sys.exit(main())
