#!/usr/bin/env python3
"""Read-only live OIDC role boundary proof; never provision or publish AWS success.

Only the trusted main proof workflow may call this command. All raw API responses
are discarded; an unexpected successful protected read or non-authorization error
fails without retaining a proof. Evidence contains operation labels, not secrets,
state, identifiers or credentials. This does not prove mutation permissions.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPOSITORY = "Djimi/OnlineShop-full-stack"
WORKFLOW = f"{REPOSITORY}/.github/workflows/aws-boundary-proof.yml@refs/heads/main"
REGION = "eu-north-1"
DENIED = re.compile(
    rb"An error occurred \((AccessDenied|AccessDeniedException|UnauthorizedOperation)\)"
)


class ProofFailed(Exception):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=["operator", "publisher"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        account, bucket, secret = verify_controller()
        verify_assumed_role(account, args.role)
        allowed, denied = boundary_operations(args.role, bucket, secret)
        for label, operation in allowed.items():
            result = call_aws(operation)
            if result.returncode:
                raise ProofFailed("approved read failed: " + label)
        for label, operation in denied.items():
            result = call_aws(operation)
            if not result.returncode or not DENIED.search(result.stderr):
                raise ProofFailed(
                    "protected read did not prove authorization denial: " + label
                )
        evidence = {
            "role": args.role,
            "status": "passed",
            "allowed": list(allowed),
            "denied": list(denied),
            "mutation_permissions_proved": False,
            "aws_validation_success": False,
        }
        descriptor = os.open(
            args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as stream:
            json.dump(evidence, stream, indent=2)
        print(
            "Read-only role boundary proof passed; no mutation permission or AWS test success inferred"
        )
        return 0
    except (ProofFailed, OSError, ValueError, KeyError, TypeError):
        print(
            "Boundary proof failed: controller, identity, permission or evidence mismatch; raw data omitted",
            file=sys.stderr,
        )
        return 1


def verify_controller():
    if (
        os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or os.environ.get("GITHUB_REF") != "refs/heads/main"
        or os.environ.get("GITHUB_WORKFLOW_REF") != WORKFLOW
    ):
        raise ProofFailed("untrusted controller")
    account = os.environ.get("AWS_TESTING_ACCOUNT_ID", "")
    bucket = os.environ.get("AWS_TESTING_STATE_BUCKET", "")
    secret = os.environ.get("AWS_TESTING_SECRET_ARN", "")
    if (
        not re.fullmatch(r"[0-9]{12}", account)
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
        or not secret.startswith(
            f"arn:aws:secretsmanager:{REGION}:{account}:secret:onlineshop-test/credentials-"
        )
    ):
        raise ProofFailed("unverified inputs")
    return account, bucket, secret


def verify_assumed_role(account, role):
    result = call_aws(["sts", "get-caller-identity"])
    if result.returncode:
        raise ProofFailed("identity unavailable")
    identity = json.loads(result.stdout)
    if identity.get("Account") != account or not identity.get("Arn", "").startswith(
        f"arn:aws:sts::{account}:assumed-role/onlineshop-test-{role}/"
    ):
        raise ProofFailed("wrong account or role")


def boundary_operations(role, bucket, secret):
    denied = {
        "backend-state-read": [
            "s3api",
            "get-object",
            "--bucket",
            bucket,
            "--key",
            "state/backend.tfstate",
            "/dev/null",
        ],
        "bootstrap-state-read": [
            "s3api",
            "get-object",
            "--bucket",
            bucket,
            "--key",
            "state/bootstrap.tfstate",
            "/dev/null",
        ],
        "dedicated-secret-read": [
            "secretsmanager",
            "get-secret-value",
            "--secret-id",
            secret,
        ],
    }
    if role == "operator":
        allowed = {
            "host-discovery": ["ec2", "describe-instances", "--max-results", "5"],
            "exact-host-profile": [
                "iam",
                "get-instance-profile",
                "--instance-profile-name",
                "onlineshop-test-host",
            ],
            "environment-state-list": [
                "s3api",
                "list-objects-v2",
                "--bucket",
                bucket,
                "--prefix",
                "state/environment.tfstate",
                "--max-keys",
                "1",
            ],
        }
        denied["backend-state-list"] = [
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--prefix",
            "state/backend.tfstate",
            "--max-keys",
            "1",
        ]
        denied["host-role-read"] = [
            "iam",
            "get-role",
            "--role-name",
            "onlineshop-test-host",
        ]
    else:
        allowed = {
            "fixed-image-discovery": [
                "ecr",
                "describe-images",
                "--repository-name",
                "onlineshop-test-auth",
                "--max-results",
                "5",
            ]
        }
        denied["host-discovery"] = ["ec2", "describe-instances", "--max-results", "5"]
    return allowed, denied


def call_aws(operation):
    try:
        result = subprocess.run(
            [
                "aws",
                *operation,
                "--region",
                REGION,
                "--output",
                "json",
                "--no-cli-pager",
                "--no-paginate",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raise ProofFailed("bounded API unavailable") from None
    if len(result.stdout) + len(result.stderr) > 1024**2:
        raise ProofFailed("unexpected API output size")
    return result


if __name__ == "__main__":
    sys.exit(main())
