"""Opt-in actual Compose reset test using a previously verified image receipt.

Run with AWS_IMAGE_RECEIPT=/protected/path/images.json. Only owner-profile ECR
reads occur; no AWS provisioning, database service or firewall mutation occurs.
All local containers/data/network/config belong to a fresh disposable project.
This proves Compose/reset behavior, not EC2 metadata or OIDC/SSM isolation.
"""

import importlib.util
import json
import os
import re
import secrets
import subprocess
import tempfile
import unittest
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


class ActualComposeResetStory(unittest.TestCase):
    def test_fresh_owned_volumes_remove_old_databases_cache_and_broker_state_and_e2e_passes(
        self,
    ):
        receipt = Path(os.environ["AWS_IMAGE_RECEIPT"])
        record = json.loads(receipt.read_text())
        self.assertEqual(
            set(record["images"]), {"auth", "items", "gateway", "frontend", "e2e"}
        )
        identity = json.loads(self.owner_aws(["sts", "get-caller-identity"]))
        account = identity["Account"]
        for name, image in record["images"].items():
            self.assertRegex(
                image,
                "^"
                + account
                + r"\.dkr\.ecr\.eu-north-1\.amazonaws\.com/onlineshop-test-"
                + name
                + r"@sha256:[0-9a-f]{64}$",
            )
        with tempfile.TemporaryDirectory(
            prefix="aws-runtime-reset-", dir="/tmp/opencode"
        ) as directory:
            path = Path(directory)
            project = "aws-reset-" + path.name.rsplit("-", 1)[-1]
            env = {
                key: value
                for key, value in os.environ.items()
                if not key.startswith(("AWS_", "GITHUB_", "ACTIONS_"))
                and key not in ["GH_TOKEN", "GH_ENTERPRISE_TOKEN"]
            }
            env.update(
                {
                    name.upper() + "_IMAGE": image
                    for name, image in record["images"].items()
                }
            )
            generation = (
                "run-"
                + str(record["validation_run_id"])
                + "-attempt-"
                + str(record["validation_run_attempt"])
            )
            env.update(GENERATION=generation, DOCKER_CONFIG=str(path / "docker-config"))
            (path / "docker-config").mkdir(mode=0o700)
            passwords = {
                key: secrets.token_urlsafe(32)
                for key in ["auth_db_password", "items_db_password", "e2e_password"]
            }
            secret_file = path / "run.env"
            secret_file.write_text(
                "AUTH_DB_PASSWORD="
                + passwords["auth_db_password"]
                + "\nITEMS_DB_PASSWORD="
                + passwords["items_db_password"]
                + "\n"
            )
            secret_file.chmod(0o600)
            self.run_command(
                [
                    "docker",
                    "login",
                    "--username",
                    "AWS",
                    "--password-stdin",
                    account + ".dkr.ecr.eu-north-1.amazonaws.com",
                ],
                env,
                input=self.owner_aws(["ecr", "get-login-password"], json_output=False),
            )
            for image in record["images"].values():
                self.run_command(["docker", "pull", image], env, timeout=600)
            config = json.loads(
                self.run_command(
                    [
                        "docker",
                        "compose",
                        "--file",
                        str(ROOT / "infra/aws/runtime/compose.yml"),
                        "--env-file",
                        str(secret_file),
                        "config",
                        "--format",
                        "json",
                    ],
                    env,
                )
            )
            config["name"] = project
            config["networks"]["network"]["name"] = project + "_network"
            for name, volume in config["volumes"].items():
                volume["name"] = project + "_" + name
            for service in config["services"].values():
                for mount in service.get("volumes", []):
                    if mount["type"] == "bind":
                        owner = "Auth" if "Auth/init-db" in mount["source"] else "Items"
                        destination = path / "fixtures" / owner
                        destination.mkdir(parents=True, exist_ok=True)
                        for source in (ROOT / owner / "init-db").glob("*.sql"):
                            if owner == "Auth" and source.name == "02-seed-data.sql":
                                continue
                            (destination / source.name).write_bytes(source.read_bytes())
                        mount["source"] = str(destination)
                for port in service.get("ports", []):
                    port["published"] = "0"
            definition = path / "compose.json"
            definition.write_text(json.dumps(config))
            definition.chmod(0o600)
            compose = [
                "docker",
                "compose",
                "--project-name",
                project,
                "--file",
                str(definition),
            ]
            container = project + "-e2e"
            try:
                self.run_command(
                    [
                        *compose,
                        "up",
                        "-d",
                        "--no-build",
                        "--wait",
                        "--wait-timeout",
                        "300",
                    ],
                    env,
                    timeout=360,
                )
                for service, port, endpoint in [
                    ("api-gateway", "10000", "/actuator/health"),
                    ("frontend", "5173", "/"),
                ]:
                    address = (
                        self.run_command([*compose, "port", service, port], env)
                        .decode()
                        .strip()
                    )
                    with urlopen(
                        "http://" + address + endpoint, timeout=10
                    ) as response:
                        self.assertEqual(response.status, 200)
                self.run_e2e(
                    record, env, project, container, generation, passwords, path
                )
                for service, database in [
                    ("auth-postgres", "auth"),
                    ("items-postgres", "items"),
                ]:
                    self.run_command(
                        [
                            *compose,
                            "exec",
                            "-T",
                            service,
                            "psql",
                            "-U",
                            database,
                            "-d",
                            database,
                            "-c",
                            "CREATE TABLE aws_reset_probe (id integer); INSERT INTO aws_reset_probe VALUES (1);",
                        ],
                        env,
                    )
                self.run_command(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "redis",
                        "redis-cli",
                        "SET",
                        "previous-run",
                        "marker",
                    ],
                    env,
                )
                self.run_command(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "kafka",
                        "/opt/kafka/bin/kafka-topics.sh",
                        "--bootstrap-server",
                        "localhost:9092",
                        "--create",
                        "--topic",
                        "previous-run",
                        "--partitions",
                        "1",
                        "--replication-factor",
                        "1",
                    ],
                    env,
                    timeout=60,
                )
                self.run_command(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "kafka",
                        "/opt/kafka/bin/kafka-console-producer.sh",
                        "--bootstrap-server",
                        "localhost:9092",
                        "--topic",
                        "previous-run",
                    ],
                    env,
                    input=b"marker\n",
                    timeout=60,
                )
                self.run_command(
                    [*compose, "down", "--volumes", "--remove-orphans"],
                    env,
                    timeout=120,
                )
                self.run_command(
                    [
                        *compose,
                        "up",
                        "-d",
                        "--no-build",
                        "--wait",
                        "--wait-timeout",
                        "300",
                    ],
                    env,
                    timeout=360,
                )
                for service, database in [
                    ("auth-postgres", "auth"),
                    ("items-postgres", "items"),
                ]:
                    result = self.run_command(
                        [
                            *compose,
                            "exec",
                            "-T",
                            service,
                            "psql",
                            "-U",
                            database,
                            "-d",
                            database,
                            "-Atc",
                            "SELECT to_regclass('public.aws_reset_probe') IS NULL;",
                        ],
                        env,
                    )
                    self.assertEqual(result.strip(), b"t")
                self.assertEqual(
                    self.run_command(
                        [
                            *compose,
                            "exec",
                            "-T",
                            "redis",
                            "redis-cli",
                            "EXISTS",
                            "previous-run",
                        ],
                        env,
                    ).strip(),
                    b"0",
                )
                topics = self.run_command(
                    [
                        *compose,
                        "exec",
                        "-T",
                        "kafka",
                        "/opt/kafka/bin/kafka-topics.sh",
                        "--bootstrap-server",
                        "localhost:9092",
                        "--list",
                    ],
                    env,
                    timeout=60,
                )
                self.assertNotIn(b"previous-run", topics)
                self.run_e2e(
                    record, env, project, container, generation, passwords, path
                )
            except BaseException:
                output = subprocess.run(
                    [*compose, "logs", "--no-color", "--tail", "60"],
                    check=False,
                    env=env,
                    capture_output=True,
                    timeout=30,
                )
                text = (output.stdout + output.stderr).decode(errors="replace")[:65536]
                for value in passwords.values():
                    text = text.replace(value, "<redacted>")
                text = re.sub(
                    r"(?i)((?:password|secret|authorization|token)\s*[:=]\s*)[^\s,;]+",
                    r"\1<redacted>",
                    text,
                )
                destination = (
                    ROOT
                    / ".superpowers/sdd/aws-testing-environment-PLAN/local-compose-failure.log"
                )
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text)
                destination.chmod(0o600)
                raise
            finally:
                subprocess.run(
                    ["docker", "rm", "--force", "--volumes", container],
                    check=False,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=30,
                )
                subprocess.run(
                    [*compose, "down", "--volumes", "--remove-orphans"],
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=120,
                    check=True,
                )

    def run_e2e(self, record, env, project, container, generation, passwords, path):
        secret_file = path / "e2e.env"
        secret_file.write_text(
            "E2E_BASE_URL=http://api-gateway:10000\nE2E_TEST_PASSWORD="
            + passwords["e2e_password"]
            + "\n"
        )
        secret_file.chmod(0o600)
        self.run_command(
            [
                "docker",
                "run",
                "--name",
                container,
                "--network",
                project + "_network",
                "--user",
                "10001:10001",
                "--cap-drop=ALL",
                "--security-opt=no-new-privileges:true",
                "--pids-limit",
                "512",
                "--memory",
                "1g",
                "--env-file",
                str(secret_file),
                "--entrypoint",
                "./mvnw",
                "--workdir",
                "/workspace/e2e-tests",
                record["images"]["e2e"],
                "--batch-mode",
                "clean",
                "test",
            ],
            env,
            timeout=900,
        )
        spec = importlib.util.spec_from_file_location(
            "trusted_runtime", ROOT / "infra/aws/runtime/run-stack.py"
        )
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        runtime.PROJECT = project
        runtime.STATE = path / "evidence"
        runtime.STATE.mkdir(mode=0o700, exist_ok=True)
        runtime.collect_reports(env, passwords, generation)
        self.assertEqual(
            len(list((runtime.STATE / "reports" / generation).glob("*.xml"))), 2
        )
        self.run_command(["docker", "rm", "--volumes", container], env)

    def owner_aws(self, arguments, *, json_output=True):
        args = [
            "aws",
            *arguments,
            "--profile",
            "dpm-profile",
            "--region",
            "eu-north-1",
            "--no-cli-pager",
        ]
        if json_output:
            args += ["--output", "json"]
        result = subprocess.run(args, capture_output=True, timeout=60, check=False)
        self.assertEqual(
            result.returncode,
            0,
            "Owner-profile read-only AWS operation failed; no raw diagnostics retained",
        )
        return result.stdout

    def run_command(self, arguments, env, *, input=None, timeout=60):
        result = subprocess.run(
            arguments,
            env=env,
            input=input,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            "Scoped Compose/reset command failed; raw output suppressed to protect credentials",
        )
        return result.stdout


if __name__ == "__main__":
    unittest.main()
