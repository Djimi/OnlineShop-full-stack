#!/usr/bin/env python3
"""Trusted-main operator proof: inspect existing state and make a no-change plan.

Authorize both actors before OIDC and again in the plan phase. Refuse missing,
empty, unexpected or generation-mismatched state before Terraform initialization.
Native planning may acquire/release its S3 lock but never applies changes, resets
the app, or publishes AWS success. Raw snapshots/plans/logs stay private on the
runner and are never artifacts. A failure/timeout requires lock reconciliation.
"""

import argparse
import json
import os
import re
import selectors
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "Djimi/OnlineShop-full-stack"
WORKFLOW = f"{REPOSITORY}/.github/workflows/aws-operator-plan-proof.yml@refs/heads/main"
REGION = "eu-north-1"
INPUTS = (
    "main.tf",
    "variables.tf",
    "outputs.tf",
    "versions.tf",
    "backend.tf",
    ".terraform.lock.hcl",
)
ADDRESSES = {
    "aws_vpc.main",
    "aws_subnet.host",
    "aws_internet_gateway.main",
    "aws_route_table.host",
    "aws_route.outbound",
    "aws_route_table_association.host",
    "aws_security_group.host",
    "aws_launch_template.host",
    "aws_instance.host",
    "aws_ec2_tag.network_generation",
}


class ProofFailed(Exception):
    def __init__(self, message, *, stage="controller", error_labels=()):
        super().__init__(message)
        self.stage = stage
        self.error_labels = list(error_labels)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["authorize", "plan"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    stage = "controller"
    try:
        context = trusted_context()
        directory = Path(os.environ["RUNNER_TEMP"]) / (
            "operator-" + args.phase + "-" + context["run"]
        )
        directory.mkdir(mode=0o700)
        stage = "actor-authorization"
        authorize_actors(directory)
        report = {"status": "authorized", "aws_validation_success": False}
        if args.phase == "plan":
            stage = "operator-identity"
            verify_operator(context, directory)
            stage = "state-snapshot"
            state = read_protected_object(
                context, "state/environment.tfstate", directory, "state.json"
            )
            stage = "generation-snapshot"
            pointer = read_protected_object(
                context, "operations/current-generation.json", directory, "pointer.json"
            )
            stage = "state-validation"
            generation = verify_existing_state(state, pointer)
            stage = "terraform-planning"
            make_current_plan(context, generation, directory)
            report = {
                "status": "passed",
                "existing_state_verified": True,
                "native_lock_plan_completed": True,
                "resource_changes": 0,
                "apply_performed": False,
                "aws_validation_success": False,
                "mutation_permissions_proved": False,
            }
        write_report(args.output, report)
        print(
            "Operator proof phase completed; no apply, app mutation or AWS validation success."
        )
        return 0
    except (
        ProofFailed,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        subprocess.TimeoutExpired,
    ) as error:
        failure = {
            "status": "failed",
            "aws_validation_success": False,
            "stage": error.stage
            if isinstance(error, ProofFailed) and error.stage != "controller"
            else stage,
            "error_labels": error.error_labels
            if isinstance(error, ProofFailed)
            else [],
            "reason": str(error)
            if isinstance(error, ProofFailed)
            else "invalid input or process outcome",
        }
        try:
            write_report(args.output, failure)
        except OSError:
            pass
        print(
            "Operator plan proof blocked at "
            + failure["stage"]
            + ": "
            + failure["reason"]
            + "; protected details omitted. Reconcile locks before retry.",
            file=sys.stderr,
        )
        return 1


def write_report(path, report):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as output:
        json.dump(report, output, indent=2)
        output.flush()
        os.fsync(output.fileno())


def trusted_context():
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch"
        or os.environ.get("GITHUB_WORKFLOW_REF") != WORKFLOW
    ):
        raise ProofFailed("Untrusted controller")
    account = os.environ.get("AWS_TESTING_ACCOUNT_ID", "")
    bucket = os.environ.get("AWS_TESTING_STATE_BUCKET", "")
    sha = os.environ.get("GITHUB_SHA", "")
    run = (
        os.environ.get("GITHUB_RUN_ID", "")
        + "-"
        + os.environ.get("GITHUB_RUN_ATTEMPT", "")
    )
    if (
        not re.fullmatch(r"[0-9]{12}", account)
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
        or not re.fullmatch(r"[a-f0-9]{40}", sha)
        or not re.fullmatch(r"[1-9][0-9]*-[1-9][0-9]*", run)
    ):
        raise ProofFailed("Invalid trusted metadata")
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode or result.stdout.decode().strip() != sha:
        raise ProofFailed("Wrong checkout")
    trusted_paths = [
        "scripts/aws-operator-plan-proof.py",
        ".github/workflows/aws-operator-plan-proof.yml",
        *["infra/aws/environment/" + name for name in INPUTS],
    ]
    result = subprocess.run(
        ["git", "diff", sha, "--", *trusted_paths],
        cwd=ROOT,
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode or result.stdout:
        raise ProofFailed("Modified trusted inputs")
    return {"account": account, "bucket": bucket, "run": run}


def run_private(command, directory, name, *, env=None, timeout=300):
    path = directory / name
    with path.open("xb") as output, (directory / (name + ".err")).open("xb") as error:
        process = subprocess.Popen(
            command,
            cwd=directory,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        deadline = time.monotonic() + timeout
        streams = selectors.DefaultSelector()
        streams.register(process.stdout, selectors.EVENT_READ, (output, 0))
        streams.register(process.stderr, selectors.EVENT_READ, (error, 0))
        try:
            while streams.get_map():
                if time.monotonic() >= deadline:
                    raise ProofFailed("Protected process deadline exceeded")
                for key, _ in streams.select(
                    timeout=max(0, min(0.25, deadline - time.monotonic()))
                ):
                    chunk = os.read(key.fileobj.fileno(), 65536)
                    if not chunk:
                        streams.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    destination, written = key.data
                    remaining = 16 * 1024**2 - written
                    destination.write(chunk[:remaining])
                    if len(chunk) > remaining:
                        raise ProofFailed("Protected process output limit exceeded")
                    streams.modify(
                        key.fileobj,
                        selectors.EVENT_READ,
                        (destination, written + len(chunk)),
                    )
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        finally:
            streams.close()
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=5)
            finally:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
            for stream in [process.stdout, process.stderr]:
                if not stream.closed:
                    stream.close()
    if process.returncode:
        stages = {
            "actor-0.json": "dispatch-authorization",
            "actor-1.json": "rerun-authorization",
            "identity.json": "operator-identity",
            "head-state.json": "state-metadata",
            "get-state.json": "state-download",
            "head-pointer.json": "generation-metadata",
            "get-pointer.json": "generation-download",
            "init.log": "terraform-init",
            "plan.log": "terraform-plan",
            "plan.json": "terraform-plan-inspection",
        }
        allowed = (
            "AccessDenied",
            "UnauthorizedOperation",
            "InvalidClientTokenId",
            "ExpiredToken",
            "NoSuchKey",
            "NoSuchBucket",
            "PreconditionFailed",
            "s3:ListBucket",
            "s3:GetObject",
            "s3:GetObjectVersion",
            "s3:PutObject",
            "s3:DeleteObject",
            "iam:GetInstanceProfile",
            "Error acquiring the state lock",
        )
        raw = path.read_bytes() + (directory / (name + ".err")).read_bytes()
        labels = [
            label
            for label in allowed
            if re.search(
                rb"(?<![A-Za-z0-9])" + re.escape(label.encode()) + rb"(?![A-Za-z0-9])",
                raw,
            )
        ]
        raise ProofFailed(
            "Required protected process did not succeed",
            stage=stages.get(name, "protected-process"),
            error_labels=labels,
        )
    return path


def read_json(path):
    def fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProofFailed("Duplicate identity field")
            result[key] = value
        return result

    if not 0 < path.stat().st_size <= 16 * 1024**2:
        raise ProofFailed("Unbounded protected JSON")
    document = json.loads(path.read_bytes(), object_pairs_hook=fields)
    if not isinstance(document, dict):
        raise ProofFailed("Protected JSON root is not an object")
    return document


def authorize_actors(directory):
    for index, name in enumerate(["GITHUB_ACTOR", "GITHUB_TRIGGERING_ACTOR"]):
        actor = os.environ.get(name, "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", actor):
            raise ProofFailed("Invalid actor")
        path = run_private(
            ["gh", "api", f"repos/{REPOSITORY}/collaborators/{actor}/permission"],
            directory,
            f"actor-{index}.json",
        )
        if read_json(path).get("permission") != "admin":
            raise ProofFailed("Actor unauthorized")


def aws(arguments, directory, name):
    path = run_private(
        ["aws", *arguments[:2], "--region", REGION, "--output", "json", *arguments[2:]],
        directory,
        name,
        env={**os.environ, "AWS_PAGER": ""},
    )
    return read_json(path)


def verify_operator(context, directory):
    identity = aws(["sts", "get-caller-identity"], directory, "identity.json")
    if identity.get("Account") != context["account"] or not identity.get(
        "Arn", ""
    ).startswith(
        f"arn:aws:sts::{context['account']}:assumed-role/onlineshop-test-operator/"
    ):
        raise ProofFailed("Wrong account or assumed role")


def read_protected_object(context, key, directory, name):
    arguments = [
        "--bucket",
        context["bucket"],
        "--key",
        key,
        "--expected-bucket-owner",
        context["account"],
    ]
    head = aws(["s3api", "head-object", *arguments], directory, "head-" + name)
    if not head.get("VersionId") or not 0 < head["ContentLength"] <= 16 * 1024**2:
        raise ProofFailed("No bounded authoritative state")
    target = directory / name
    actual = aws(
        ["s3api", "get-object", *arguments, "--if-match", head["ETag"], str(target)],
        directory,
        "get-" + name,
    )
    if (
        actual.get("VersionId") != head["VersionId"]
        or target.stat().st_size != head["ContentLength"]
    ):
        raise ProofFailed("Snapshot changed")
    return read_json(target)


def verify_existing_state(state, pointer):
    if not isinstance(state, dict) or not isinstance(pointer, dict):
        raise ProofFailed("Invalid environment snapshot")
    generation = pointer["generation"]
    if (
        not re.fullmatch(r"run-[1-9][0-9]*-attempt-[1-9][0-9]*", generation)
        or pointer.get("status") != "provisioned"
        or state.get("version") != 4
        or not state.get("lineage")
        or type(state.get("serial")) is not int
    ):
        raise ProofFailed("Unrecognized environment identity")
    resources = state["resources"]
    if (
        not isinstance(resources, list)
        or len(resources) != len(ADDRESSES)
        or {r["type"] + "." + r["name"] for r in resources} != ADDRESSES
        or any(
            r.get("module") or r["mode"] != "managed" or len(r["instances"]) != 1
            for r in resources
        )
    ):
        raise ProofFailed("Missing or unexpected environment resources")
    host = next(
        r["instances"][0]["attributes"]
        for r in resources
        if r["type"] == "aws_instance"
    )
    if host["id"] != pointer["host_id"] or host["tags"]["Generation"] != generation:
        raise ProofFailed("Generation/state host mismatch")
    return generation


def make_current_plan(context, generation, directory):
    source = ROOT / "infra/aws/environment"
    for name in INPUTS:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ProofFailed("Unsafe trusted configuration")
        shutil.copyfile(path, directory / path.name)
    env = {
        **os.environ,
        "TF_VAR_account_id": context["account"],
        "TF_VAR_host_profile": "onlineshop-test-host",
        "TF_VAR_generation": generation,
        "TF_IN_AUTOMATION": "1",
        "TF_LOG": "",
        "AWS_REGION": REGION,
        "AWS_DEFAULT_REGION": REGION,
    }
    run_private(
        [
            "terraform",
            "init",
            "-input=false",
            "-no-color",
            "-lockfile=readonly",
            "-backend-config=bucket=" + context["bucket"],
        ],
        directory,
        "init.log",
        env=env,
    )
    run_private(
        [
            "terraform",
            "plan",
            "-input=false",
            "-no-color",
            "-lock-timeout=30s",
            "-out=current.tfplan",
        ],
        directory,
        "plan.log",
        env=env,
    )
    plan = read_json(
        run_private(
            ["terraform", "show", "-json", "current.tfplan"],
            directory,
            "plan.json",
            env=env,
        )
    )
    planned_resources = plan["planned_values"]["root_module"]["resources"]
    if (
        plan.get("errored")
        or len(planned_resources) != len(ADDRESSES)
        or {item["address"] for item in planned_resources} != ADDRESSES
    ):
        raise ProofFailed("Plan does not cover the existing environment")
    if any(
        change["change"]["actions"] != ["no-op"]
        for change in plan.get("resource_changes", [])
    ):
        raise ProofFailed("Current environment plan proposes changes")


if __name__ == "__main__":
    raise SystemExit(main())
