"""Unit tests for production ECS task-definition and service-config hardening
rules (Pass 3, subphase 3.5)."""

import json
import os
import subprocess
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import ecs_config

FULL_SECRET = "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/auth/db-umtxh1"
DIGEST = "sha256:50310c92745326299ce463ebd8ad2279a4ff0386a3701246e48165c65d5682b0"
IMAGE = f"799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth@{DIGEST}"


def base_container(name="auth", image=None):
    return {
        "name": name,
        "image": image or IMAGE,
        "essential": True,
        "versionConsistency": "enabled",
        "stopTimeout": 30,
        "portMappings": [{"containerPort": 9001, "protocol": "tcp", "name": "auth-port"}],
        "environment": [
            {"name": "SPRING_DATASOURCE_URL", "value": "jdbc:postgresql://db:5432/auth"}
        ],
        "secrets": [
            {"name": "SPRING_DATASOURCE_USERNAME", "valueFrom": f"{FULL_SECRET}:username::"},
            {"name": "SPRING_DATASOURCE_PASSWORD", "valueFrom": f"{FULL_SECRET}:password::"},
        ],
        "logConfiguration": {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": "/ecs/onlineshop-auth",
                "awslogs-region": "eu-north-1",
                "awslogs-stream-prefix": "auth",
            },
        },
        "healthCheck": {
            "command": [
                "CMD-SHELL",
                "curl -f http://localhost:9001/actuator/health/liveness || exit 1",
            ],
            "interval": 30,
            "timeout": 5,
            "retries": 3,
            "startPeriod": 180,
        },
    }


def base_td(container=None, **overrides):
    td = {
        "family": "onlineshop-auth",
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
        "cpu": "512",
        "memory": "1024",
        "executionRoleArn": "arn:aws:iam::799111666795:role/ecsTaskExecutionRole",
        "containerDefinitions": [container or base_container()],
    }
    td.update(overrides)
    return td


def codes(outcome):
    return [issue["code"] for issue in outcome.issues]


class TaskDefinitionValidationTests(unittest.TestCase):
    def test_valid_definition_passes(self):
        outcome = ecs_config.validate_task_definition(base_td())
        self.assertTrue(outcome.valid, outcome.issues)

    def test_bridge_network_mode_rejected(self):
        outcome = ecs_config.validate_task_definition(base_td(networkMode="bridge"))
        self.assertFalse(outcome.valid)
        self.assertIn("NETWORK_MODE", codes(outcome))

    def test_non_fargate_rejected(self):
        outcome = ecs_config.validate_task_definition(base_td(requiresCompatibilities=["EC2"]))
        self.assertFalse(outcome.valid)
        self.assertIn("NOT_FARGATE", codes(outcome))

    def test_invalid_cpu_memory_pair_rejected(self):
        outcome = ecs_config.validate_task_definition(base_td(cpu="512", memory="512"))
        self.assertFalse(outcome.valid)
        self.assertIn("INVALID_CPU_MEMORY", codes(outcome))

    def test_valid_cpu_memory_pairs_accepted(self):
        for cpu, memory in (("256", "512"), ("512", "4096"), ("1024", "8192"), ("2048", "8192")):
            outcome = ecs_config.validate_task_definition(base_td(cpu=cpu, memory=memory))
            self.assertTrue(outcome.valid, f"{cpu}/{memory}: {outcome.issues}")

    def test_missing_execution_role_rejected(self):
        td = base_td()
        del td["executionRoleArn"]
        outcome = ecs_config.validate_task_definition(td)
        self.assertFalse(outcome.valid)
        self.assertIn("MISSING_EXECUTION_ROLE", codes(outcome))

    def test_shared_execution_and_task_role_rejected(self):
        # Execution-role and task-role duties must stay separate.
        td = base_td()
        td["taskRoleArn"] = td["executionRoleArn"]
        outcome = ecs_config.validate_task_definition(td)
        self.assertFalse(outcome.valid)
        self.assertIn("ROLE_NOT_DISTINCT", codes(outcome))

    def test_distinct_task_role_accepted(self):
        td = base_td(taskRoleArn="arn:aws:iam::799111666795:role/onlineshop-app-task")
        outcome = ecs_config.validate_task_definition(td)
        self.assertTrue(outcome.valid, outcome.issues)

    def test_floating_image_rejected(self):
        container = base_container()
        container["image"] = "799111666795.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-auth:sha-abc"
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("FLOATING_IMAGE", codes(outcome))

    def test_version_consistency_required(self):
        container = base_container()
        container["versionConsistency"] = "disabled"
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("VERSION_CONSISTENCY_DISABLED", codes(outcome))

    def test_missing_health_check_rejected(self):
        container = base_container()
        del container["healthCheck"]
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("MISSING_HEALTH_CHECK", codes(outcome))

    def test_missing_stop_timeout_rejected(self):
        container = base_container()
        del container["stopTimeout"]
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("INVALID_STOP_TIMEOUT", codes(outcome))

    def test_missing_logs_rejected(self):
        container = base_container()
        del container["logConfiguration"]
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("MISSING_LOGS", codes(outcome))

    def test_unamed_port_rejected(self):
        container = base_container()
        container["portMappings"] = [{"containerPort": 9001, "protocol": "tcp"}]
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("UNNAMED_PORT", codes(outcome))

    def test_secret_in_environment_rejected(self):
        container = base_container()
        container["environment"] = [{"name": "SPRING_DATASOURCE_PASSWORD", "value": "s3cr3t-value"}]
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("SECRET_PLAINTEXT_IN_ENV", codes(outcome))

    def test_secret_repeated_in_environment_value_rejected(self):
        container = base_container()
        container["environment"] = [{"name": "DEBUG_REF", "value": FULL_SECRET}]
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("SECRET_PLAINTEXT_IN_ENV", codes(outcome))

    def test_short_secret_arn_rejected(self):
        container = base_container()
        container["secrets"] = [
            {"name": "SPRING_DATASOURCE_PASSWORD", "valueFrom": "onlineshop/auth/db"}
        ]
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("SECRET_SHORT_ARN", codes(outcome))

    def test_secret_in_command_rejected(self):
        container = base_container()
        container["command"] = ["sh", "-c", f"echo {FULL_SECRET}"]
        outcome = ecs_config.validate_task_definition(base_td(container))
        self.assertFalse(outcome.valid)
        self.assertIn("SECRET_IN_COMMAND", codes(outcome))

    def test_invalid_task_definition_object_rejected(self):
        outcome = ecs_config.validate_task_definition("not a td")
        self.assertFalse(outcome.valid)


def base_service(td=None, **overrides):
    service = {
        "serviceName": "onlineshop-auth",
        "cluster": "onlineshop-cluster",
        "desiredCount": 1,
        "schedulingStrategy": "REPLICA",
        "deploymentController": {"type": "ECS"},
        "deploymentConfiguration": {
            "deploymentCircuitBreaker": {"enable": True, "rollback": True},
            "minimumHealthyPercent": 100,
            "maximumPercent": 200,
        },
        "capacityProviderStrategy": [{"capacityProvider": "FARGATE_SPOT", "weight": 1}],
        "serviceConnectConfiguration": {
            "enabled": True,
            "namespace": "onlineshop.local",
            "services": [{"portName": "auth-port", "discoveryName": "auth"}],
        },
    }
    service.update(overrides)
    return service


class ServiceConfigValidationTests(unittest.TestCase):
    def test_valid_service_passes(self):
        outcome = ecs_config.validate_service_config(base_service())
        self.assertTrue(outcome.valid, outcome.issues)

    def test_circuit_breaker_disabled_rejected(self):
        service = base_service()
        service["deploymentConfiguration"]["deploymentCircuitBreaker"]["enable"] = False
        outcome = ecs_config.validate_service_config(service)
        self.assertFalse(outcome.valid)
        self.assertIn("CIRCUIT_BREAKER_DISABLED", codes(outcome))

    def test_rollback_disabled_rejected(self):
        service = base_service()
        service["deploymentConfiguration"]["deploymentCircuitBreaker"]["rollback"] = False
        outcome = ecs_config.validate_service_config(service)
        self.assertFalse(outcome.valid)
        self.assertIn("ROLLBACK_DISABLED", codes(outcome))

    def test_rolling_parameters_rejected(self):
        service = base_service()
        service["deploymentConfiguration"]["minimumHealthyPercent"] = 0
        service["deploymentConfiguration"]["maximumPercent"] = 100
        outcome = ecs_config.validate_service_config(service)
        self.assertFalse(outcome.valid)
        self.assertIn("MIN_HEALTHY_PERCENT", codes(outcome))
        self.assertIn("MAX_PERCENT", codes(outcome))

    def test_wrong_deployment_controller_rejected(self):
        service = base_service(deploymentController={"type": "CODE_DEPLOY"})
        outcome = ecs_config.validate_service_config(service)
        self.assertFalse(outcome.valid)
        self.assertIn("WRONG_DEPLOYMENT_CONTROLLER", codes(outcome))

    def test_missing_capacity_provider_rejected(self):
        service = base_service()
        del service["capacityProviderStrategy"]
        outcome = ecs_config.validate_service_config(service)
        self.assertFalse(outcome.valid)
        self.assertIn("MISSING_CAPACITY_PROVIDER", codes(outcome))

    def test_service_connect_port_not_in_td_rejected(self):
        td = base_td()
        service = base_service()
        service["serviceConnectConfiguration"]["services"] = [{"portName": "does-not-exist"}]
        outcome = ecs_config.validate_service_config(service, td)
        self.assertFalse(outcome.valid)
        self.assertIn("SC_PORT_NOT_IN_TD", codes(outcome))

    def test_service_connect_port_matches_td(self):
        td = base_td()
        outcome = ecs_config.validate_service_config(base_service(), td)
        self.assertTrue(outcome.valid, outcome.issues)


class CliTests(unittest.TestCase):
    def test_cli_validate_td(self):
        tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tmp-ecs-config.json")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(base_td(), handle)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "release_contract.ecs_config",
                    "validate-td",
                    "--input",
                    tmp,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('"valid":true', proc.stdout)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)

    def test_cli_validate_service(self):
        tmp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tmp-ecs-service.json")
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(base_service(), handle)
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "release_contract.ecs_config",
                    "validate-service",
                    "--input",
                    tmp,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn('"valid":true', proc.stdout)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)


if __name__ == "__main__":
    unittest.main()
