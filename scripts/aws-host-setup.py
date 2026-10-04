#!/usr/bin/env python3
"""Owner-only trusted host setup proof; never deploy candidate code or publish success.

Verify account/host/generation and idle SSM, transfer only trusted runtime files in
hashed finite chunks, then install prerequisites with a remote timeout and host
lock. Persist intent/command identity locally and in protected S3 before polling.
Unknown outcomes block reruns. This is not the routine validation controller.
"""

import argparse
import base64
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REGION = "eu-north-1"
FILES = ("host-setup.sh", "run-stack.py", "compose.yml")
TERMINAL = {"Success", "Failed", "Cancelled", "TimedOut", "Cancelling"}


class SetupFailed(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--identifiers", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    operation = None
    try:
        if args.output.exists():
            raise SetupFailed(
                "Existing setup record requires reconciliation, not automatic retry"
            )
        ids = json.loads(args.identifiers.read_text())
        verify_host(ids)
        verify_trusted_files()
        operation = {
            "generation": ids["generation"],
            "host_id": ids["instance_id"],
            "purpose": "owner-host-setup-proof",
            "status": "running",
            "commands": [],
            "aws_validation_success": False,
        }
        save_operation(args.output, ids, operation)
        commands = transfer_commands(ids)
        deadline = time.monotonic() + 1200
        commands.append(
            "flock -n /opt/onlineshop-test/.runtime/host.lock timeout --signal=TERM --kill-after=30 900 bash /opt/onlineshop-test/host-setup.sh --setup"
        )
        for command in commands:
            run_remote(command, ids, args.output, operation, deadline)
        operation["status"] = "success"
        save_operation(args.output, ids, operation)
        print(
            "Trusted host prerequisites installed; no application or AWS validation success."
        )
        return 0
    except (
        SetupFailed,
        OSError,
        ValueError,
        KeyError,
        subprocess.TimeoutExpired,
    ) as error:
        if operation is not None:
            operation["status"] = "unknown"
            try:
                save_operation(args.output, ids, operation)
            except (SetupFailed, OSError):
                pass
        print(
            "Host setup blocked: "
            + (
                str(error)
                if isinstance(error, SetupFailed)
                else "invalid protected input/record"
            ),
            file=sys.stderr,
        )
        return 1


def aws(arguments):
    result = subprocess.run(
        [
            "aws",
            *arguments,
            "--region",
            REGION,
            "--profile",
            "dpm-profile",
            "--output",
            "json",
        ],
        env={**os.environ, "AWS_PAGER": ""},
        capture_output=True,
        timeout=60,
        check=False,
    )
    if (
        result.returncode
        and arguments[:2] == ["ssm", "get-command-invocation"]
        and b"(InvocationDoesNotExist)" in result.stderr
    ):
        return {"Status": "Pending"}
    if result.returncode or len(result.stdout) > 1024**2:
        raise SetupFailed("Required AWS operation failed; no raw response emitted")
    return json.loads(result.stdout)


def verify_host(ids):
    if (
        not re.fullmatch(r"[0-9]{12}", ids["account_id"])
        or not re.fullmatch(r"i-[0-9a-f]{17}", ids["instance_id"])
        or not re.fullmatch(r"run-[1-9][0-9]*-attempt-[1-9][0-9]*", ids["generation"])
    ):
        raise SetupFailed("Invalid account/host/generation identity")
    if aws(["sts", "get-caller-identity"])["Account"] != ids["account_id"]:
        raise SetupFailed("Wrong AWS account")
    hosts = aws(["ec2", "describe-instances", "--instance-ids", ids["instance_id"]])[
        "Reservations"
    ]
    host = hosts[0]["Instances"][0]
    tags = {t["Key"]: t["Value"] for t in host["Tags"]}
    if (
        host["InstanceId"] != ids["instance_id"]
        or host["State"]["Name"] != "running"
        or host["InstanceType"] != "m7i-flex.large"
        or host["ImageId"] != "ami-04478a3e21a0d79a7"
        or not host["IamInstanceProfile"]["Arn"].endswith("/onlineshop-test-host")
        or any(
            tags.get(k) != v
            for k, v in {
                "ManagedBy": "onlineshop-test",
                "Repository": "Djimi/OnlineShop-full-stack",
                "Generation": ids["generation"],
            }.items()
        )
    ):
        raise SetupFailed("Host ownership/configuration mismatch")
    online = aws(
        [
            "ssm",
            "describe-instance-information",
            "--filters",
            json.dumps([{"Key": "InstanceIds", "Values": [ids["instance_id"]]}]),
        ]
    )["InstanceInformationList"]
    if len(online) != 1 or online[0]["PingStatus"] != "Online":
        raise SetupFailed("Host SSM is not online")
    prior = aws(["ssm", "list-commands", "--instance-id", ids["instance_id"]])[
        "Commands"
    ]
    if any(
        c["Status"] not in {"Success", "Failed", "Cancelled", "TimedOut"} for c in prior
    ):
        raise SetupFailed("Remote operation is not terminal")


def verify_trusted_files():
    result = subprocess.run(
        [
            "git",
            "diff",
            "origin/main",
            "--",
            *["infra/aws/runtime/" + name for name in FILES],
        ],
        cwd=ROOT,
        capture_output=True,
        timeout=30,
        check=False,
    )
    if result.returncode or result.stdout:
        raise SetupFailed("Host files differ from trusted main")


def transfer_commands(ids):
    commands = [
        'set -eu; umask 077; install -d -m 0700 /opt/onlineshop-test /opt/onlineshop-test/.runtime; if command -v docker >/dev/null; then test -z "$(docker ps -q)"; fi'
    ]
    files = {name: (ROOT / "infra/aws/runtime" / name).read_bytes() for name in FILES}
    files["bootstrap.json"] = json.dumps(
        {
            "account_id": ids["account_id"],
            "region": REGION,
            "secret_arn": ids["secret_arn"],
        }
    ).encode()
    for name, data in files.items():
        if len(data) > 128 * 1024:
            raise SetupFailed("Trusted host file exceeds transfer bound")
        target = "/opt/onlineshop-test/" + name
        commands.append(
            "set -eu; umask 077; test ! -L "
            + target
            + ".incoming; : > "
            + target
            + ".incoming"
        )
        for offset in range(0, len(data), 12000):
            encoded = base64.b64encode(data[offset : offset + 12000]).decode()
            commands.append(
                "set -eu; printf '%s' '"
                + encoded
                + "' | base64 --decode >> "
                + target
                + ".incoming"
            )
        digest = hashlib.sha256(data).hexdigest()
        commands.append(
            "set -eu; echo '"
            + digest
            + "  "
            + target
            + ".incoming' | sha256sum --check --status; chmod 0600 "
            + target
            + ".incoming; mv -f "
            + target
            + ".incoming "
            + target
        )
    return commands


def save_operation(path, ids, operation):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as output:
        json.dump(operation, output)
        output.flush()
        os.fsync(output.fileno())
    condition = (
        ["--if-match", operation["s3_etag"]]
        if "s3_etag" in operation
        else ["--if-none-match", "*"]
    )
    result = aws(
        [
            "s3api",
            "put-object",
            "--bucket",
            ids["state_bucket"],
            "--key",
            "operations/" + ids["generation"] + "/host-setup.json",
            "--body",
            str(path),
            "--expected-bucket-owner",
            ids["account_id"],
            "--server-side-encryption",
            "AES256",
            *condition,
        ]
    )
    operation["s3_etag"] = result["ETag"]


def run_remote(command, ids, path, operation, deadline):
    entry = {
        "stage": len(operation["commands"]),
        "status": "launching",
        "sha256": hashlib.sha256(command.encode()).hexdigest(),
    }
    operation["commands"].append(entry)
    save_operation(path, ids, operation)
    response = aws(
        [
            "ssm",
            "send-command",
            "--document-name",
            "AWS-RunShellScript",
            "--instance-ids",
            ids["instance_id"],
            "--comment",
            ids["generation"] + "-setup-" + str(entry["stage"]),
            "--timeout-seconds",
            "60",
            "--parameters",
            json.dumps({"commands": [command], "executionTimeout": ["930"]}),
        ]
    )
    entry.update(command_id=response["Command"]["CommandId"], status="running")
    save_operation(path, ids, operation)
    deadline = min(deadline, time.monotonic() + 960)
    while time.monotonic() < deadline:
        response = aws(
            [
                "ssm",
                "get-command-invocation",
                "--command-id",
                entry["command_id"],
                "--instance-id",
                ids["instance_id"],
            ]
        )
        if response["Status"] in TERMINAL:
            entry["status"] = response["Status"]
            save_operation(path, ids, operation)
            if response["Status"] != "Success" or response["ResponseCode"] != 0:
                raise SetupFailed(
                    "Bounded remote setup did not succeed; reconcile before retry"
                )
            return
        time.sleep(3)
    raise SetupFailed("Remote setup outcome unknown; reconcile before retry")


if __name__ == "__main__":
    raise SystemExit(main())
