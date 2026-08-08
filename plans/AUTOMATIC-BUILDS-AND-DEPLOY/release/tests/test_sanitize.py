"""Unit tests for task-definition sanitization and drift-proofing
(Pass 3, subphase 3.5)."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import sanitize

FULL_SECRET = "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db-umtxh1"
OLD_IMAGE = (
    "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth:"
    "sha-263f0690aa08eaf24f23f715dea7e8895a759293"
)
NEW_IMAGE = (
    "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth@sha256:"
    "50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
)


def td(image=OLD_IMAGE, secret_value_from=None, env_extra=None):
    container = {
        "name": "auth",
        "image": image,
        "essential": True,
        "portMappings": [{"containerPort": 9001, "protocol": "tcp", "name": "auth-port"}],
        "environment": [
            {"name": "SPRING_DATASOURCE_URL", "value": "jdbc:postgresql://db:5432/auth"},
        ]
        + (env_extra or []),
        "secrets": [
            {
                "name": "SPRING_DATASOURCE_PASSWORD",
                "valueFrom": secret_value_from or f"{FULL_SECRET}:password::",
            },
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {"awslogs-group": "/ecs/onlineshop-auth", "awslogs-region": "eu-north-1"},
        },
    }
    return {
        "family": "onlineshop-auth",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "512",
        "memory": "1024",
        "executionRoleArn": "arn:aws:iam::799111666795:role/ecsTaskExecutionRole",
        "containerDefinitions": [container],
    }


def codes(result):
    if isinstance(result, list):
        return [issue["code"] for issue in result]
    return [issue["code"] for issue in result.issues]


class SanitizeTransformTests(unittest.TestCase):
    def test_replaces_only_named_container_image(self):
        original = td()
        result = sanitize.sanitize_task_definition(original, {"auth": NEW_IMAGE})
        self.assertIsNotNone(result.task_definition)
        self.assertEqual(result.issues, [])
        self.assertEqual(result.task_definition["containerDefinitions"][0]["image"], NEW_IMAGE)
        # The original is not mutated.
        self.assertEqual(original["containerDefinitions"][0]["image"], OLD_IMAGE)

    def test_missing_container_is_an_issue(self):
        result = sanitize.sanitize_task_definition(td(), {"nope": NEW_IMAGE})
        self.assertIn("MISSING_CONTAINER", codes(result))

    def test_non_digest_replacement_is_an_issue(self):
        result = sanitize.sanitize_task_definition(td(), {"auth": "repo:latest"})
        self.assertIn("NOT_DIGEST_PINNED", codes(result))


class SanitizedDiffTests(unittest.TestCase):
    def test_clean_image_only_diff(self):
        original = td()
        result = sanitize.sanitize_task_definition(original, {"auth": NEW_IMAGE})
        issues = sanitize.sanitized_diff_issues(original, result.task_definition)
        self.assertEqual(issues, [])
        fields = sanitize.diff_fields(original, result.task_definition)
        self.assertEqual(fields, ["$/containerDefinitions/0/image"])

    def test_unrelated_drift_reported(self):
        original = td()
        result = sanitize.sanitize_task_definition(original, {"auth": NEW_IMAGE})
        result.task_definition["containerDefinitions"][0]["environment"] = [
            {"name": "SOMETHING_ELSE", "value": "changed"}
        ]
        issues = sanitize.sanitized_diff_issues(original, result.task_definition)
        self.assertIn("UNRELATED_DRIFT", codes(issues))

    def test_container_removed_reported(self):
        original = td()
        result = sanitize.sanitize_task_definition(original, {"auth": NEW_IMAGE})
        del result.task_definition["containerDefinitions"][0]
        issues = sanitize.sanitized_diff_issues(original, result.task_definition)
        self.assertIn("CONTAINER_REMOVED", codes(issues))

    def test_floating_image_after_sanitize_reported(self):
        original = td()
        result = sanitize.sanitize_task_definition(original, {"auth": NEW_IMAGE})
        result.task_definition["containerDefinitions"][0]["image"] = "repo:floating"
        issues = sanitize.sanitized_diff_issues(original, result.task_definition)
        self.assertIn("FLOATING_IMAGE", codes(issues))

    def test_short_secret_arn_reported(self):
        original = td()
        result = sanitize.sanitize_task_definition(original, {"auth": NEW_IMAGE})
        result.task_definition["containerDefinitions"][0]["secrets"][0]["valueFrom"] = (
            "onlineshop/auth/db"
        )
        issues = sanitize.sanitized_diff_issues(original, result.task_definition)
        self.assertIn("SECRET_SHORT_ARN", codes(issues))

    def test_secret_reference_leaked_to_environment_reported(self):
        original = td()
        result = sanitize.sanitize_task_definition(original, {"auth": NEW_IMAGE})
        result.task_definition["containerDefinitions"][0]["environment"].append(
            {"name": "LEAK", "value": f"{FULL_SECRET}:password::"}
        )
        issues = sanitize.sanitized_diff_issues(original, result.task_definition)
        self.assertIn("SECRET_PLAINTEXT_IN_ENV", codes(issues))

    def test_secret_reference_leaked_to_command_reported(self):
        original = td()
        result = sanitize.sanitize_task_definition(original, {"auth": NEW_IMAGE})
        result.task_definition["containerDefinitions"][0]["command"] = [
            "sh",
            "-c",
            f"echo {FULL_SECRET}",
        ]
        issues = sanitize.sanitized_diff_issues(original, result.task_definition)
        self.assertIn("SECRET_IN_COMMAND", codes(issues))

    def test_secret_references_only_in_value_from(self):
        original = td()
        result = sanitize.sanitize_task_definition(original, {"auth": NEW_IMAGE})
        dump = json.dumps(result.task_definition)
        container = result.task_definition["containerDefinitions"][0]
        # Each secret ARN appears exactly once — only in secrets[].valueFrom —
        # and never as plaintext in environment/command.
        for secret in container.get("secrets", []):
            value_from = secret["valueFrom"]
            self.assertEqual(dump.count(value_from), 1, value_from)
        env_text = json.dumps(container.get("environment", []))
        command_text = json.dumps(container.get("command", []))
        for secret in container.get("secrets", []):
            self.assertNotIn(secret["valueFrom"], env_text)
            self.assertNotIn(secret["valueFrom"], command_text)


class CliTests(unittest.TestCase):
    def test_cli_sanitize_and_assert(self):
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        tmp_in = os.path.join(base, "tmp-sanitize-in.json")
        tmp_out = os.path.join(base, "tmp-sanitize-out.json")
        try:
            with open(tmp_in, "w", encoding="utf-8") as handle:
                json.dump(td(), handle)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "release_contract.sanitize",
                    "sanitize",
                    "--input",
                    tmp_in,
                    "--output",
                    tmp_out,
                    "--set-image",
                    f"auth={NEW_IMAGE}",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('"valid":true', proc.stdout)
            with open(tmp_out, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["containerDefinitions"][0]["image"], NEW_IMAGE)
            proc2 = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "release_contract.sanitize",
                    "assert",
                    "--original",
                    tmp_in,
                    "--sanitized",
                    tmp_out,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc2.returncode, 0, proc2.stderr)
        finally:
            for path in (tmp_in, tmp_out):
                if os.path.exists(path):
                    os.remove(path)


if __name__ == "__main__":
    unittest.main()
