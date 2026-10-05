"""Observe fixed operator state/EC2/SSM boundaries; never authorize mutation.

This independent cloud half of reconciliation consumes only trusted account/bucket
metadata. It does not launch host commands, clear locks, restore state, retry an
operation or publish success. Host evidence and persistent operation-ledger
reconciliation are still required, even when these observations pass.
"""

import json
import os
import re
import selectors
import signal
import subprocess
import tempfile
import time
from pathlib import Path

REGION = "eu-north-1"
READ_OPERATIONS = {
    ("sts", "get-caller-identity"),
    ("s3api", "head-object"),
    ("s3api", "get-object"),
    ("ec2", "describe-instances"),
    ("ssm", "list-commands"),
    ("ssm", "get-command-invocation"),
}
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


class ReconciliationBlocked(Exception):
    pass


def observe_cloud():
    deadline = time.monotonic() + 300
    account = os.environ.get("AWS_TESTING_ACCOUNT_ID", "")
    bucket = os.environ.get("AWS_TESTING_STATE_BUCKET", "")
    if not re.fullmatch(r"[0-9]{12}", account) or not re.fullmatch(
        r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket
    ):
        raise ReconciliationBlocked("invalid trusted cloud metadata")
    identity = read_aws(["sts", "get-caller-identity"], deadline=deadline)
    if identity.get("Account") != account or not identity.get("Arn", "").startswith(
        f"arn:aws:sts::{account}:assumed-role/onlineshop-test-operator/"
    ):
        raise ReconciliationBlocked("wrong operator account or role")
    location = ["--bucket", bucket, "--expected-bucket-owner", account]
    if (
        read_aws(
            [
                "s3api",
                "head-object",
                *location,
                "--key",
                "state/environment.tfstate.tflock",
            ],
            absent_ok=True,
            deadline=deadline,
        )
        is not None
    ):
        raise ReconciliationBlocked("environment state lock requires reconciliation")
    with tempfile.TemporaryDirectory(prefix="aws-cloud-observation-") as temporary:
        directory = Path(temporary)
        state = read_snapshot(
            location, "state/environment.tfstate", directory / "state.json", deadline
        )
        pointer = read_snapshot(
            location,
            "operations/current-generation.json",
            directory / "pointer.json",
            deadline,
        )
        generation, host_id = verify_state(state, pointer)
        verify_host(host_id, generation, deadline)
        observe_ssm(host_id, deadline)
        # No pass if identities changed while EC2/SSM were being inspected.
        current = read_snapshot(
            location,
            "operations/current-generation.json",
            directory / "pointer-final.json",
            deadline,
        )
        latest = read_snapshot(
            location,
            "state/environment.tfstate",
            directory / "state-final.json",
            deadline,
        )
        if current != pointer or latest != state:
            raise ReconciliationBlocked("environment changed during cloud observation")
    return {
        "status": "cloud-observed",
        "generation": generation,
        "state_verified": True,
        "state_lock_absent_at_start": True,
        "ssm_commands_terminal": True,
        "host_observation_required": True,
        "operation_ledger_reconciliation_required": True,
        "mutation_authorized": False,
        "aws_validation_success": False,
    }


def read_aws(arguments, *, deadline, absent_ok=False):
    operation = tuple(arguments[:2])
    if operation not in READ_OPERATIONS:
        raise ReconciliationBlocked("unsupported cloud read operation")
    deadline = min(deadline, time.monotonic() + 60)
    if time.monotonic() >= deadline:
        raise ReconciliationBlocked("cloud observation deadline exceeded")
    env = {
        key: value
        for key, value in os.environ.items()
        if key
        not in {
            "GH_TOKEN",
            "GITHUB_TOKEN",
            "AWS_PROFILE",
            "AWS_CONFIG_FILE",
            "AWS_SHARED_CREDENTIALS_FILE",
        }
    }
    env.update(AWS_PAGER="", AWS_CLI_AUTO_PROMPT="off")
    process = subprocess.Popen(
        ["aws", *arguments, "--region", REGION, "--output", "json"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        start_new_session=True,
    )
    buffers = {"stdout": bytearray(), "stderr": bytearray()}
    with selectors.DefaultSelector() as streams:
        streams.register(process.stdout, selectors.EVENT_READ, "stdout")
        streams.register(process.stderr, selectors.EVENT_READ, "stderr")
        try:
            while streams.get_map():
                if time.monotonic() >= deadline:
                    raise ReconciliationBlocked("cloud read deadline exceeded")
                for key, _ in streams.select(
                    timeout=max(0, min(0.25, deadline - time.monotonic()))
                ):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if not data:
                        streams.unregister(key.fileobj)
                        key.fileobj.close()
                        continue
                    if len(buffers[key.data]) + len(data) > 1024**2:
                        raise ReconciliationBlocked("cloud read output bound exceeded")
                    buffers[key.data].extend(data)
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            for stream in [process.stdout, process.stderr]:
                stream.close()
    if process.returncode:
        if (
            absent_ok
            and bytes(buffers["stderr"]).strip()
            == b"An error occurred (404) when calling the HeadObject operation: Not Found"
        ):
            return None
        raise ReconciliationBlocked(
            "required cloud read failed at "
            + "/".join(operation)
            + "; protected details omitted"
        )
    result = json.loads(buffers["stdout"], object_pairs_hook=unique_fields)
    if not isinstance(result, dict):
        raise ReconciliationBlocked("cloud JSON root is not an object")
    return result


def unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReconciliationBlocked("duplicate cloud record field")
        result[key] = value
    return result


def read_snapshot(location, key, path, deadline):
    args = [*location, "--key", key]
    head = read_aws(["s3api", "head-object", *args], deadline=deadline)
    if (
        not head.get("VersionId")
        or type(head.get("ContentLength")) is not int
        or not 0 < head["ContentLength"] <= 16 * 1024**2
    ):
        raise ReconciliationBlocked("missing bounded versioned snapshot")
    result = read_aws(
        ["s3api", "get-object", *args, "--if-match", head["ETag"], str(path)],
        deadline=deadline,
    )
    if (
        result.get("VersionId") != head["VersionId"]
        or path.stat().st_size != head["ContentLength"]
    ):
        raise ReconciliationBlocked("snapshot changed during download")
    result = json.loads(path.read_bytes(), object_pairs_hook=unique_fields)
    if not isinstance(result, dict):
        raise ReconciliationBlocked("invalid snapshot root")
    return result


def verify_state(state, pointer):
    generation = pointer["generation"]
    host_id = pointer["host_id"]
    if (
        not re.fullmatch(r"run-[1-9][0-9]*-attempt-[1-9][0-9]*", generation)
        or not re.fullmatch(r"i-[a-f0-9]{17}", host_id)
        or pointer.get("status") != "provisioned"
        or state.get("version") != 4
        or not state.get("lineage")
        or type(state.get("serial")) is not int
    ):
        raise ReconciliationBlocked("unrecognized environment identity")
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
        raise ReconciliationBlocked("missing or unexpected environment resources")
    host = next(
        r["instances"][0]["attributes"]
        for r in resources
        if r["type"] == "aws_instance"
    )
    if host["id"] != host_id or host["tags"]["Generation"] != generation:
        raise ReconciliationBlocked("state and generation host mismatch")
    return generation, host_id


def verify_host(host_id, generation, deadline):
    reservations = read_aws(
        ["ec2", "describe-instances", "--instance-ids", host_id], deadline=deadline
    )["Reservations"]
    if len(reservations) != 1 or len(reservations[0]["Instances"]) != 1:
        raise ReconciliationBlocked("host identity is not unique")
    host = reservations[0]["Instances"][0]
    tags = {item["Key"]: item["Value"] for item in host["Tags"]}
    if (
        host["InstanceId"] != host_id
        or host["State"]["Name"] != "running"
        or any(
            tags.get(key) != value
            for key, value in {
                "ManagedBy": "onlineshop-test",
                "Repository": "Djimi/OnlineShop-full-stack",
                "Generation": generation,
            }.items()
        )
    ):
        raise ReconciliationBlocked(
            "host ownership or transition requires reconciliation"
        )


def observe_ssm(host_id, deadline):
    base = [
        "ssm",
        "list-commands",
        "--instance-id",
        host_id,
        "--max-results",
        "50",
        "--no-paginate",
    ]
    arguments = base
    tokens = set()
    for _ in range(20):
        response = read_aws(arguments, deadline=deadline)
        commands = response["Commands"]
        if (
            not isinstance(commands, list)
            or len(commands) > 50
            or any(
                command.get("Status")
                not in {"Success", "Failed", "Cancelled", "TimedOut"}
                for command in commands
            )
        ):
            raise ReconciliationBlocked("remote command is not provably terminal")
        for command in commands:
            command_id = command["CommandId"]
            if not re.fullmatch(
                r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", command_id
            ):
                raise ReconciliationBlocked("invalid remote command identity")
            invocation = read_aws(
                [
                    "ssm",
                    "get-command-invocation",
                    "--command-id",
                    command_id,
                    "--instance-id",
                    host_id,
                ],
                deadline=deadline,
            )
            if invocation.get("Status") not in {
                "Success",
                "Failed",
                "Cancelled",
                "TimedOut",
            }:
                raise ReconciliationBlocked(
                    "remote invocation is not provably terminal"
                )
        token = response.get("NextToken")
        if not token:
            return
        if not isinstance(token, str) or len(token) > 1024 or token in tokens:
            raise ReconciliationBlocked("SSM pagination identity is invalid")
        tokens.add(token)
        arguments = [*base, "--next-token", token]
    raise ReconciliationBlocked("SSM discovery bound exceeded")
