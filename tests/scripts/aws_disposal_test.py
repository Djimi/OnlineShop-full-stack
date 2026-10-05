"""Disposal CLI stories at the workflow, AWS CLI and Terraform process boundaries."""

import base64
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

from tests.scripts import aws_validation_test as validation

SCRIPT = validation.SCRIPT
GENERATION = "run-200-attempt-1"


class DisposalPreflightStories(unittest.TestCase):
    def setUp(self):
        validation.RequestStories.setUp(self)
        self.env.update(
            AWS_TESTING_ACCOUNT_ID="111111111111",
            AWS_TESTING_OPERATOR_ROLE="arn:aws:iam::111111111111:role/onlineshop-test-operator",
            AWS_TESTING_STATE_BUCKET="test-state-bucket",
            GITHUB_WORKFLOW_REF=(
                f"{validation.REPO}/.github/workflows/aws-dispose.yml@refs/heads/main"
            ),
        )

    def invoke(self, *, generation=GENERATION, confirmation="dispose"):
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "dispose-preflight",
                "--generation",
                generation,
                "--confirmation",
                confirmation,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertNotIn("secret-token-canary", result.stdout + result.stderr)
        self.assertFalse((self.directory / "aws-called").exists())
        return result


class DisposalSessionStories(DisposalPreflightStories):
    def setUp(self):
        validation.RequestStories.setUp(self)
        self.env.update(
            AWS_TESTING_ACCOUNT_ID="111111111111",
            AWS_TESTING_OPERATOR_ROLE="arn:aws:iam::111111111111:role/onlineshop-test-operator",
            AWS_TESTING_STATE_BUCKET="test-state-bucket",
            RUNNER_TEMP=str(self.directory),
            GITHUB_WORKFLOW_REF=(
                f"{validation.REPO}/.github/workflows/aws-dispose.yml@refs/heads/main"
            ),
        )
        generation = "run-200-attempt-1"
        host_id = "i-11111111111111111"
        self.pointer = {
            "schema": 1,
            "generation": generation,
            "host_id": host_id,
            "status": "disposed",
            "predecessor": {"generation": "run-199-attempt-1", "host_id": host_id},
            "host_predecessor": {"generation": "run-199-attempt-1", "host_id": host_id},
            "request": {
                "repository": validation.REPO,
                "pr": 42,
                "head_sha": "a" * 40,
                "base_sha": "b" * 40,
                "candidate_sha": "c" * 40,
                "controller_sha": "d" * 40,
                "ci_run_id": 100,
                "ci_run_attempt": 1,
                "ci_artifact_id": 300,
                "validation_run_id": 200,
                "validation_run_attempt": 1,
                "actor": "owner",
                "triggering_actor": "owner",
                "check_run_id": 400,
            },
            "images": {
                name: f"111111111111.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-test-{name}@sha256:{str(index) * 64}"
                for index, name in enumerate(
                    ["auth", "items", "gateway", "frontend", "e2e"], 1
                )
            },
            "retained": [],
        }
        self.inventory = {
            "aws_vpc.main": "vpc-11111111111111111",
            "aws_subnet.host": "subnet-11111111111111111",
            "aws_internet_gateway.main": "igw-11111111111111111",
            "aws_route_table.host": "rtb-11111111111111111",
            "aws_route.outbound": "r-rtb-111111111111111111080289494",
            "aws_route_table_association.host": "rtbassoc-11111111111111111",
            "aws_security_group.host": "sg-11111111111111111",
            "aws_launch_template.host": "lt-11111111111111111",
            "aws_instance.host": host_id,
            "aws_ec2_tag.network_generation": "eni-11111111111111111,Generation",
            "instance_network_interface": "eni-11111111111111111",
            "instance_root_volume": "vol-11111111111111111",
        }
        terminal = {
            "schema": 1,
            "operation": "dispose",
            "generation": generation,
            "host_id": host_id,
            "status": "disposed",
            "account_id": "111111111111",
            "region": "eu-north-1",
            "root": "infra/aws/environment",
            "state_key": "state/environment.tfstate",
            "state_lineage": "owned-lineage",
            "state_serial": 2,
            "resource_ids": self.inventory,
            "verified_absence": True,
        }
        self.cloud = {
            "pointer": self.pointer,
            "state": {
                "version": 4,
                "lineage": "owned-lineage",
                "serial": 2,
                "resources": [],
                "outputs": {},
            },
            "terminal": terminal,
            "objects": {},
            "commands": [],
            "retention": {
                f"onlineshop-test-{name}:active-owner": "sha256:" + str(index) * 64
                for index, name in enumerate(
                    ["auth", "items", "gateway", "frontend", "e2e"], 1
                )
            },
            "live_resources": {},
            "terraform_calls": [],
            "aws_calls": [],
        }
        terminal_key = f"operations/{generation}/dispose-terminal.json"
        self.cloud["objects"][terminal_key] = base64.b64encode(
            json.dumps(terminal).encode()
        ).decode()
        self.cloud["objects"][f"operations/{generation}/dispose-intent.json"] = (
            base64.b64encode(
                json.dumps(
                    {
                        "schema": 1,
                        "operation": "dispose",
                        "generation": generation,
                        "host_id": host_id,
                        "account_id": "111111111111",
                        "region": "eu-north-1",
                        "root": "infra/aws/environment",
                        "state_key": "state/environment.tfstate",
                        "state_lineage": "owned-lineage",
                        "initial_state_serial": 1,
                        "resource_ids": self.inventory,
                        "status": "intent",
                    }
                ).encode()
            ).decode()
        )
        original_intent = dict(self.pointer, status="completed")
        self.cloud["objects"][f"operations/{generation}/intent.json"] = (
            base64.b64encode(json.dumps(original_intent).encode()).decode()
        )
        self.save_cloud()
        double = Path(__file__).with_name("aws_disposal_double.py")
        for name in ["aws", "terraform"]:
            path = self.bin / name
            path.write_text(
                f"#!/bin/sh\nexec '{sys.executable}' '{double}' '{name}' \"$@\"\n"
            )
            path.chmod(0o755)
        git = self.bin / "git"
        git.write_text("""#!/usr/bin/env python3
import os,sys
if sys.argv[-1]=='HEAD': print(os.environ['GITHUB_SHA'])
elif sys.argv[1]=='diff': pass
else: sys.exit(1)
""")
        git.chmod(0o755)

    def save_cloud(self):
        (self.directory / "disposal-cloud.json").write_text(json.dumps(self.cloud))

    def invoke_dispose(self, generation=GENERATION):
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "dispose",
                "--generation",
                generation,
                "--confirmation",
                "dispose",
                "--output",
                str(self.directory / "dispose-evidence"),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.assertNotIn("secret-token-canary", result.stdout + result.stderr)
        self.cloud = json.loads((self.directory / "disposal-cloud.json").read_text())
        return result

    def set_active_environment(self):
        generation = self.pointer["generation"]
        ids = self.inventory
        self.cloud["objects"].pop(
            f"operations/{generation}/dispose-terminal.json", None
        )
        tags = {
            "ManagedBy": "onlineshop-test",
            "Repository": validation.REPO,
            "Generation": generation,
        }
        template_tags = {"ManagedBy": "onlineshop-test", "Repository": validation.REPO}
        attrs = {
            "aws_vpc.main": {"id": ids["aws_vpc.main"], "tags": tags},
            "aws_subnet.host": {
                "id": ids["aws_subnet.host"],
                "vpc_id": ids["aws_vpc.main"],
                "tags": tags,
            },
            "aws_internet_gateway.main": {
                "id": ids["aws_internet_gateway.main"],
                "vpc_id": ids["aws_vpc.main"],
                "tags": tags,
            },
            "aws_route_table.host": {
                "id": ids["aws_route_table.host"],
                "vpc_id": ids["aws_vpc.main"],
                "tags": tags,
            },
            "aws_route.outbound": {
                "id": ids["aws_route.outbound"],
                "route_table_id": ids["aws_route_table.host"],
                "gateway_id": ids["aws_internet_gateway.main"],
                "destination_cidr_block": "0.0.0.0/0",
            },
            "aws_route_table_association.host": {
                "id": ids["aws_route_table_association.host"],
                "route_table_id": ids["aws_route_table.host"],
                "subnet_id": ids["aws_subnet.host"],
            },
            "aws_security_group.host": {
                "id": ids["aws_security_group.host"],
                "vpc_id": ids["aws_vpc.main"],
                "tags": tags,
            },
            "aws_launch_template.host": {
                "id": ids["aws_launch_template.host"],
                "tags": template_tags,
                "tag_specifications": [
                    {"resource_type": kind, "tags": template_tags}
                    for kind in ["instance", "volume", "network-interface"]
                ],
            },
            "aws_instance.host": {
                "id": ids["aws_instance.host"],
                "subnet_id": ids["aws_subnet.host"],
                "vpc_security_group_ids": [ids["aws_security_group.host"]],
                "launch_template": [{"id": ids["aws_launch_template.host"]}],
                "primary_network_interface_id": ids["instance_network_interface"],
                "root_block_device": [{"volume_id": ids["instance_root_volume"]}],
                "tags": tags,
                "volume_tags": tags,
            },
            "aws_ec2_tag.network_generation": {
                "id": ids["aws_ec2_tag.network_generation"],
                "resource_id": ids["instance_network_interface"],
                "key": "Generation",
                "value": generation,
            },
        }
        resources = []
        for address, values in attrs.items():
            kind, name = address.split(".")
            resources.append(
                {
                    "type": kind,
                    "name": name,
                    "mode": "managed",
                    "instances": [{"attributes": values}],
                }
            )
        self.cloud["state"] = {
            "version": 4,
            "lineage": "owned-lineage",
            "serial": 1,
            "resources": resources,
            "outputs": {},
        }
        self.cloud["inventory"] = ids
        self.cloud["pointer"]["status"] = "completed"
        original_intent = dict(self.pointer, status="completed")
        self.cloud["objects"][f"operations/{generation}/intent.json"] = (
            base64.b64encode(json.dumps(original_intent).encode()).decode()
        )
        retained = {
            "bootstrap/terraform.tfstate": b'{"serial":7,"lineage":"bootstrap"}',
            "bootstrap/operator-policy.json": b'{"policy":"unchanged"}',
            "state/backend-version.json": b'{"version":3,"lineage":"backend"}',
            "operations/run-199-attempt-1/terminal.json": b'{"status":"passed"}',
            "operations/runtime-evidence/run-199-attempt-1/reports.tar": b"retained-report-bytes",
            "secretsmanager/metadata.json": b'{"name":"onlineshop-test/credentials"}',
        }
        for key, data in retained.items():
            self.cloud["objects"][key] = base64.b64encode(data).decode()
        self.cloud["state_versions"] = ["retained-v1", "retained-v2"]
        self.cloud["live_resources"] = {
            **{address: True for address in ids if address.startswith("aws_")},
            "instance_network_interface": True,
            "instance_root_volume": True,
        }
        self.cloud["destroy_plan"] = {
            "planned_values": {"root_module": {"resources": []}},
            "resource_changes": [
                {
                    "address": address,
                    "change": {
                        "actions": ["delete"],
                        "before": copy.deepcopy(values),
                        "after": None,
                        "after_unknown": {},
                    },
                }
                for address, values in attrs.items()
            ],
        }
        self.save_cloud()

    def test_provider_comma_tag_id_survives_disposal_and_verified_noop(self):
        self.inventory["aws_ec2_tag.network_generation"] = (
            self.inventory["instance_network_interface"] + ",Generation"
        )
        self.set_active_environment()
        self.cloud["objects"].pop(f"operations/{GENERATION}/dispose-intent.json")
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cloud["pointer"]["status"], "disposed")
        repeated = self.invoke_dispose()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        summary = json.loads(
            (self.directory / "dispose-evidence" / "summary.json").read_text()
        )
        self.assertEqual(summary["status"], "no-op")
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "apply"]),
            1,
        )

    def test_nonprovider_tag_id_refuses_before_destroy(self):
        self.inventory["aws_ec2_tag.network_generation"] = (
            self.inventory["instance_network_interface"] + "_Generation"
        )
        self.set_active_environment()
        self.cloud["objects"].pop(f"operations/{GENERATION}/dispose-intent.json")
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(
            any(call[0] == "apply" for call in self.cloud["terraform_calls"])
        )
        self.assertEqual(self.cloud["pointer"]["status"], "completed")
        self.assertNotIn(
            f"operations/{GENERATION}/dispose-terminal.json", self.cloud["objects"]
        )

    def test_current_generation_uses_one_inspected_saved_destroy_plan_and_preserves_retained_resources(
        self,
    ):
        self.set_active_environment()
        protected = json.loads(
            json.dumps(
                {
                    "retention": self.cloud["retention"],
                    "objects": {
                        key: value
                        for key, value in self.cloud["objects"].items()
                        if not key.startswith(f"operations/{GENERATION}/")
                    },
                    "state_versions": self.cloud["state_versions"][:],
                }
            )
        )
        result = self.invoke_dispose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cloud["pointer"]["status"], "disposed")
        repeated = self.invoke_dispose()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        summary = json.loads(
            (self.directory / "dispose-evidence" / "summary.json").read_text()
        )
        self.assertEqual(summary["status"], "no-op")
        self.assertEqual(self.cloud["state"]["resources"], [])
        self.assertEqual(self.cloud["state"]["lineage"], "owned-lineage")
        self.assertEqual(self.cloud["state"]["serial"], 2)
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "plan"]),
            1,
        )
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "apply"]),
            1,
        )
        plan = next(call for call in self.cloud["terraform_calls"] if call[0] == "plan")
        self.assertIn("-destroy", plan)
        self.assertEqual(self.cloud["retention"], protected["retention"])
        self.assertEqual(
            {
                key: value
                for key, value in self.cloud["objects"].items()
                if key in protected["objects"]
            },
            protected["objects"],
        )
        self.assertEqual(self.cloud["state_versions"], protected["state_versions"])
        self.assertNotIn(
            "--version-id", " ".join(" ".join(call) for call in self.cloud["aws_calls"])
        )
        terminal = self.cloud["objects"][
            f"operations/{GENERATION}/dispose-terminal.json"
        ]
        record = json.loads(base64.b64decode(terminal))
        self.assertEqual(record["resource_ids"], self.inventory)
        self.assertEqual(record["verified_absence"], True)

    def test_stale_queued_generation_refuses_before_any_terraform_mutation(self):
        self.set_active_environment()
        result = self.invoke_dispose("run-199-attempt-1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.cloud["terraform_calls"], [])
        self.assertEqual(self.cloud["pointer"]["status"], "completed")

    def test_missing_or_foreign_state_never_counts_as_a_verified_noop(self):
        self.cloud["state"]["resources"] = [{"type": "aws_instance", "name": "foreign"}]
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.cloud["terraform_calls"], [])
        self.assertEqual(self.cloud["pointer"]["status"], "disposed")

    def test_active_ssm_and_unresolved_command_intent_block_destroy(self):
        self.set_active_environment()
        self.cloud["commands"] = [
            {
                "CommandId": "00000000-0000-0000-0000-000000000099",
                "Status": "InProgress",
            }
        ]
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.cloud["terraform_calls"], [])

        self.set_active_environment()
        unresolved = {
            "generation": GENERATION,
            "host_id": self.pointer["host_id"],
            "comment": GENERATION + ":runtime",
            "sha256": "a" * 64,
            "timeout": 60,
        }
        self.cloud["objects"][f"operations/{GENERATION}/runtime-intent.json"] = (
            base64.b64encode(json.dumps(unresolved).encode()).decode()
        )
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.cloud["terraform_calls"], [])

    def test_stale_saved_destroy_plan_is_not_replanned_or_applied(self):
        self.set_active_environment()
        self.cloud["stale_plan"] = True
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.cloud["pointer"]["status"], "completed")
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "plan"]),
            1,
        )
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "apply"]),
            1,
        )
        self.assertEqual(len(self.cloud["state"]["resources"]), 10)

    def test_refreshed_destroy_plan_cannot_replace_recorded_root_volume(self):
        self.set_active_environment()
        instance = next(
            item
            for item in self.cloud["destroy_plan"]["resource_changes"]
            if item["address"] == "aws_instance.host"
        )
        instance["change"]["before"]["root_block_device"][0]["volume_id"] = (
            "vol-99999999999999999"
        )
        self.save_cloud()

        result = self.invoke_dispose()

        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cloud["pointer"]["status"], "completed")
        self.assertFalse(
            any(call[0] == "apply" for call in self.cloud["terraform_calls"])
        )
        self.assertNotIn(
            f"operations/{GENERATION}/dispose-terminal.json", self.cloud["objects"]
        )

    def test_destroy_plan_requires_current_owner_tags_and_full_inventory_references(
        self,
    ):
        cases = [
            "missing-tags",
            "foreign-tags",
            "missing-volume-tags",
            "route-table",
            "partial-state-reference",
            "root-volume",
        ]
        for case in cases:
            with self.subTest(case=case):
                self.set_active_environment()
                item = next(
                    item
                    for item in self.cloud["destroy_plan"]["resource_changes"]
                    if item["address"] == "aws_subnet.host"
                )
                if case == "missing-tags":
                    item["change"]["before"].pop("tags")
                elif case == "foreign-tags":
                    item["change"]["before"]["tags"]["Repository"] = "foreign/repo"
                elif case == "missing-volume-tags":
                    item = next(
                        item
                        for item in self.cloud["destroy_plan"]["resource_changes"]
                        if item["address"] == "aws_instance.host"
                    )
                    item["change"]["before"].pop("volume_tags")
                elif case == "route-table":
                    item["change"]["before"]["vpc_id"] = "vpc-99999999999999999"
                    self.cloud["state"]["resources"] = [
                        resource
                        for resource in self.cloud["state"]["resources"]
                        if (resource["type"] + "." + resource["name"]) != "aws_vpc.main"
                    ]
                    self.cloud["state"]["serial"] = 2
                    self.cloud["destroy_plan"]["resource_changes"] = [
                        change
                        for change in self.cloud["destroy_plan"]["resource_changes"]
                        if change["address"] != "aws_vpc.main"
                    ]
                elif case == "partial-state-reference":
                    self.cloud["state"]["resources"] = [
                        resource
                        for resource in self.cloud["state"]["resources"]
                        if (resource["type"] + "." + resource["name"]) != "aws_vpc.main"
                    ]
                    subnet = next(
                        resource
                        for resource in self.cloud["state"]["resources"]
                        if resource["type"] == "aws_subnet"
                    )
                    subnet["instances"][0]["attributes"]["vpc_id"] = (
                        "vpc-99999999999999999"
                    )
                    self.cloud["state"]["serial"] = 2
                else:
                    item = next(
                        item
                        for item in self.cloud["destroy_plan"]["resource_changes"]
                        if item["address"] == "aws_instance.host"
                    )
                    item["change"]["before"]["primary_network_interface_id"] = (
                        "eni-99999999999999999"
                    )
                self.save_cloud()

                result = self.invoke_dispose()

                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    any(call[0] == "apply" for call in self.cloud["terraform_calls"])
                )
                self.assertTrue(
                    any(
                        call[:2] == ["ec2", "describe-instances"]
                        for call in self.cloud["aws_calls"]
                    )
                )

    def test_route_state_id_table_and_destination_must_match_provider_identity(self):
        cases = ["computed-id", "route-table", "destination"]
        for case in cases:
            with self.subTest(case=case):
                self.set_active_environment()
                route = next(
                    resource
                    for resource in self.cloud["state"]["resources"]
                    if resource["type"] == "aws_route"
                )["instances"][0]["attributes"]
                if case == "computed-id":
                    route["id"] = "r-rtb-111111111111111110000000000"
                elif case == "route-table":
                    route["route_table_id"] = "rtb-99999999999999999"
                else:
                    route["destination_cidr_block"] = "10.83.0.0/16"
                self.save_cloud()

                result = self.invoke_dispose()

                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.cloud["terraform_calls"], [])

    def test_partial_state_does_not_prove_recorded_running_host_is_gone_or_idle(self):
        self.set_active_environment()
        self.cloud["state"]["resources"] = [
            resource
            for resource in self.cloud["state"]["resources"]
            if resource["type"] not in {"aws_instance", "aws_ec2_tag"}
        ]
        self.cloud["state"]["serial"] = 2
        self.cloud["live_resources"]["aws_instance.host"] = {
            "state": "running",
            "host_lock": True,
        }
        self.save_cloud()

        result = self.invoke_dispose()

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(
            any(call[0] == "apply" for call in self.cloud["terraform_calls"])
        )
        self.assertTrue(
            any(
                call[:2] == ["ec2", "describe-instances"]
                for call in self.cloud["aws_calls"]
            )
        )

    def test_partial_state_host_transition_or_denied_lookup_refuses_before_apply(self):
        for observation in ["shutting-down", "denied"]:
            with self.subTest(observation=observation):
                self.set_active_environment()
                self.cloud["state"]["resources"] = [
                    resource
                    for resource in self.cloud["state"]["resources"]
                    if resource["type"] not in {"aws_instance", "aws_ec2_tag"}
                ]
                self.cloud["state"]["serial"] = 2
                self.cloud["live_resources"]["aws_instance.host"] = (
                    {"state": observation} if observation == "shutting-down" else True
                )
                self.cloud["deny_instance_lookup"] = observation == "denied"
                self.save_cloud()

                result = self.invoke_dispose()

                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(
                    any(call[0] == "apply" for call in self.cloud["terraform_calls"])
                )
                self.assertTrue(
                    any(
                        call[:2] == ["ec2", "describe-instances"]
                        for call in self.cloud["aws_calls"]
                    )
                )

    def test_partial_destroy_retry_uses_recorded_ids_and_never_creates_replacements(
        self,
    ):
        self.set_active_environment()
        operation = {
            "schema": 1,
            "operation": "dispose",
            "generation": GENERATION,
            "host_id": self.pointer["host_id"],
            "account_id": "111111111111",
            "region": "eu-north-1",
            "root": "infra/aws/environment",
            "state_key": "state/environment.tfstate",
            "state_lineage": "owned-lineage",
            "initial_state_serial": 1,
            "resource_ids": self.inventory,
            "status": "intent",
        }
        self.cloud["objects"][f"operations/{GENERATION}/dispose-intent.json"] = (
            base64.b64encode(json.dumps(operation).encode()).decode()
        )
        self.cloud["state"]["resources"] = [
            item
            for item in self.cloud["state"]["resources"]
            if item["type"] not in {"aws_instance", "aws_ec2_tag"}
        ]
        self.cloud["state"]["serial"] = 2
        self.cloud["live_resources"] = {"aws_vpc.main": True, "aws_subnet.host": True}
        planned = []
        for item in self.cloud["state"]["resources"]:
            address = item["type"] + "." + item["name"]
            planned.append(
                {
                    "address": address,
                    "change": {
                        "actions": ["delete"],
                        "before": item["instances"][0]["attributes"],
                        "after": None,
                        "after_unknown": {},
                    },
                }
            )
        self.cloud["destroy_plan"]["resource_changes"] = planned
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cloud["pointer"]["status"], "disposed")
        self.assertEqual(self.cloud["state"]["resources"], [])
        commands = [call[0] for call in self.cloud["terraform_calls"]]
        self.assertEqual(commands.count("plan"), 1)
        self.assertEqual(commands.count("apply"), 1)
        self.assertFalse(
            any(call[0] in {"create", "run"} for call in self.cloud["terraform_calls"])
        )

    def test_disposal_retry_rejects_extra_fields_in_immutable_intent(self):
        self.set_active_environment()
        key = f"operations/{GENERATION}/dispose-intent.json"
        intent = json.loads(base64.b64decode(self.cloud["objects"][key]))
        intent["unexpected"] = "field"
        self.cloud["objects"][key] = base64.b64encode(
            json.dumps(intent).encode()
        ).decode()
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.cloud["terraform_calls"], [])
        self.assertFalse(
            any(call[:2] == ["ssm", "send-command"] for call in self.cloud["aws_calls"])
        )

    def test_interrupted_partial_destroy_retries_with_a_new_inspected_plan(self):
        self.set_active_environment()
        self.cloud["partial_destroy_failure"] = True
        self.save_cloud()
        first = self.invoke_dispose()
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(self.cloud["pointer"]["status"], "completed")
        self.assertEqual(self.cloud["state"]["serial"], 2)
        self.assertNotEqual(self.cloud["state"]["resources"], [])
        self.assertNotIn(
            f"operations/{GENERATION}/dispose-terminal.json", self.cloud["objects"]
        )

        self.cloud["destroy_plan"]["resource_changes"] = [
            {
                "address": item["type"] + "." + item["name"],
                "change": {
                    "actions": ["delete"],
                    "before": item["instances"][0]["attributes"],
                    "after": None,
                    "after_unknown": {},
                },
            }
            for item in self.cloud["state"]["resources"]
        ]
        self.save_cloud()
        retried = self.invoke_dispose()
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(self.cloud["pointer"]["status"], "disposed")
        self.assertEqual(self.cloud["state"]["resources"], [])
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "apply"]),
            2,
        )
        recovery = json.loads(
            base64.b64decode(
                self.cloud["objects"][
                    f"operations/{GENERATION}/destroy-apply-recovery-attempt-0001.json"
                ]
            )
        )
        self.assertIn("aws_vpc.main", recovery["observed_state"]["addresses"])
        self.assertNotIn("aws_instance.host", recovery["observed_state"]["addresses"])
        self.assertEqual(recovery["next_attempt"], "attempt-0002")
        self.assertFalse(
            any(call[0] in {"create", "run"} for call in self.cloud["terraform_calls"])
        )

    def test_destroy_retry_after_unchanged_state_gets_a_distinct_inspected_plan(self):
        self.set_active_environment()
        self.cloud["apply_failure"] = True
        self.save_cloud()
        first = self.invoke_dispose()
        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(self.cloud["state"]["serial"], 1)
        self.assertEqual(len(self.cloud["state"]["resources"]), 10)

        retried = self.invoke_dispose()
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(self.cloud["pointer"]["status"], "disposed")
        self.assertEqual(self.cloud["state"]["resources"], [])
        attempts = [
            key
            for key in self.cloud["objects"]
            if key.startswith(f"operations/{GENERATION}/dispose-attempt-")
        ]
        self.assertEqual(len(attempts), 2)
        recovery_key = (
            f"operations/{GENERATION}/destroy-apply-recovery-attempt-0001.json"
        )
        self.assertIn(recovery_key, self.cloud["objects"])
        recovery = json.loads(base64.b64decode(self.cloud["objects"][recovery_key]))
        inspected = json.loads(
            base64.b64decode(
                self.cloud["objects"][
                    f"operations/{GENERATION}/destroy-plan-inspected-attempt-0001.json"
                ]
            )
        )
        self.assertEqual(recovery["plan_sha256"], inspected["sha256"])
        self.assertTrue(recovery["observed_state"]["native_lock_absent"])
        self.assertTrue(recovery["observed_state"]["ssm_terminal"])
        self.assertEqual(recovery["state_lineage"], "owned-lineage")
        self.assertEqual(
            recovery["observed_state"]["resources"]["aws_instance.host"], "present"
        )

    def test_unknown_apply_with_denied_recovery_observation_cannot_start_another_plan(
        self,
    ):
        self.set_active_environment()
        self.cloud["apply_failure"] = True
        self.save_cloud()
        first = self.invoke_dispose()
        self.assertNotEqual(first.returncode, 0)
        self.cloud["deny_resource_observation"] = True
        self.save_cloud()

        retried = self.invoke_dispose()

        self.assertNotEqual(retried.returncode, 0)
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "plan"]),
            1,
        )
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "apply"]),
            1,
        )

    def test_unknown_apply_with_native_lock_or_active_host_cannot_start_another_plan(
        self,
    ):
        for condition in ["native-lock", "active-host"]:
            with self.subTest(condition=condition):
                self.set_active_environment()
                self.cloud["apply_failure"] = True
                self.save_cloud()
                first = self.invoke_dispose()
                self.assertNotEqual(first.returncode, 0)
                if condition == "native-lock":
                    self.cloud["native_lock"] = True
                else:
                    self.cloud["host_lock"] = True
                self.save_cloud()

                retried = self.invoke_dispose()

                self.assertNotEqual(retried.returncode, 0)
                self.assertEqual(
                    len(
                        [
                            call
                            for call in self.cloud["terraform_calls"]
                            if call[0] == "plan"
                        ]
                    ),
                    1,
                )
                self.assertEqual(
                    len(
                        [
                            call
                            for call in self.cloud["terraform_calls"]
                            if call[0] == "apply"
                        ]
                    ),
                    1,
                )

    def test_empty_state_after_unknown_apply_is_observed_before_terminal_disposal(self):
        self.set_active_environment()
        self.cloud["empty_destroy_failure"] = True
        self.save_cloud()

        first = self.invoke_dispose()

        self.assertNotEqual(first.returncode, 0)
        self.assertEqual(self.cloud["state"]["resources"], [])
        self.assertEqual(self.cloud["pointer"]["status"], "completed")
        recovered = self.invoke_dispose()

        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(self.cloud["pointer"]["status"], "disposed")
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "plan"]),
            1,
        )
        self.assertEqual(
            len([call for call in self.cloud["terraform_calls"] if call[0] == "apply"]),
            1,
        )
        recovery = json.loads(
            base64.b64decode(
                self.cloud["objects"][
                    f"operations/{GENERATION}/destroy-apply-recovery-attempt-0001.json"
                ]
            )
        )
        self.assertEqual(recovery["observed_state"]["addresses"], [])
        self.assertIsNone(recovery["next_attempt"])

    def test_apply_failure_writes_sanitized_actionable_leftover_summary(self):
        self.set_active_environment()
        self.cloud["apply_failure"] = True
        self.save_cloud()

        result = self.invoke_dispose()
        summary = json.loads(
            (self.directory / "dispose-evidence" / "summary.json").read_text()
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)
        self.assertNotIn("simulated provider failure", result.stderr)
        self.assertEqual(summary["apply_outcome"], "unknown")
        self.assertIn("aws_instance.host", summary["leftovers"]["addresses"])
        self.assertEqual(summary["leftovers"]["count"], 12)
        self.assertIn(
            summary["leftovers"]["observation"], {"complete", "unknown-or-denied"}
        )

    def test_disposal_accepts_only_the_exact_owner_migration_pointer_shape(self):
        self.set_active_environment()
        self.cloud["pointer"] = {
            key: self.cloud["pointer"][key]
            for key in ["generation", "host_id", "status"]
        } | {
            "status": "provisioned",
            "purpose": "owner-empty-host-generation-migration",
        }
        self.pointer = self.cloud["pointer"]
        self.cloud["objects"].pop(f"operations/{GENERATION}/intent.json", None)
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.cloud["pointer"]["status"], "disposed")
        repeated = self.invoke_dispose()
        self.assertEqual(repeated.returncode, 0, repeated.stderr)
        summary = json.loads(
            (self.directory / "dispose-evidence" / "summary.json").read_text()
        )
        self.assertEqual(summary["status"], "no-op")

    def test_disposal_rejects_unknown_migration_purpose_extra_fields_and_status(self):
        baseline = copy.deepcopy(self.cloud)
        for change in [
            {"purpose": "unrecognized"},
            {"purpose": "owner-empty-host-generation-migration", "extra": "field"},
            {"purpose": "owner-empty-host-generation-migration", "status": "failed"},
        ]:
            with self.subTest(change=change):
                self.cloud = copy.deepcopy(baseline)
                self.pointer = self.cloud["pointer"]
                self.set_active_environment()
                self.cloud["pointer"] = {
                    key: self.cloud["pointer"][key]
                    for key in ["generation", "host_id", "status"]
                } | {"status": "provisioned", "purpose": change["purpose"]}
                self.cloud["pointer"].update(
                    {key: value for key, value in change.items() if key != "purpose"}
                )
                self.pointer = self.cloud["pointer"]
                self.save_cloud()
                result = self.invoke_dispose()
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.cloud["terraform_calls"], [])

    def test_verified_disposed_state_is_repeatable_noop_without_terraform(self):
        result = self.invoke_dispose()
        self.assertEqual(result.returncode, 0, result.stderr)
        summary = json.loads(
            (self.directory / "dispose-evidence" / "summary.json").read_text()
        )
        self.assertEqual(summary["status"], "no-op")
        self.assertEqual(self.cloud["terraform_calls"], [])
        self.assertEqual(self.cloud["pointer"], self.pointer)
        self.assertEqual(self.cloud["state"]["lineage"], "owned-lineage")
        self.assertEqual(
            self.cloud["retention"],
            {
                f"onlineshop-test-{name}:active-owner": "sha256:" + str(index) * 64
                for index, name in enumerate(
                    ["auth", "items", "gateway", "frontend", "e2e"], 1
                )
            },
        )

    def test_disposed_noop_refuses_missing_immutable_disposal_intent(self):
        self.cloud["objects"].pop(f"operations/{GENERATION}/dispose-intent.json")
        self.save_cloud()
        result = self.invoke_dispose()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.cloud["terraform_calls"])
        self.assertEqual(self.cloud["pointer"]["status"], "disposed")

    def test_valid_manual_scope_passes_without_exchanging_cloud_credentials(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("preflight", result.stdout.lower())

    def test_invalid_actor_ref_generation_confirmation_or_target_is_rejected_pre_oidc(
        self,
    ):
        cases = [
            ("ref", {"GITHUB_REF": "refs/heads/feature"}, GENERATION, "dispose"),
            ("generation", {}, "run-200-attempt-01", "dispose"),
            ("confirmation", {}, GENERATION, "delete"),
            (
                "account-role",
                {
                    "AWS_TESTING_OPERATOR_ROLE": "arn:aws:iam::222222222222:role/onlineshop-test-operator"
                },
                GENERATION,
                "dispose",
            ),
            ("missing-bucket", {"AWS_TESTING_STATE_BUCKET": ""}, GENERATION, "dispose"),
        ]
        for label, changed, generation, confirmation in cases:
            with self.subTest(label=label):
                original = {key: self.env.get(key) for key in changed}
                self.env.update(changed)
                result = self.invoke(generation=generation, confirmation=confirmation)
                self.assertNotEqual(result.returncode, 0)
                self.env.update(
                    {key: value for key, value in original.items() if value is not None}
                )

    def test_unprivileged_triggering_actor_is_rejected_before_cloud_credentials(self):
        self.env["GITHUB_TRIGGERING_ACTOR"] = "reader"
        self.env["GITHUB_RUN_ATTEMPT"] = "2"
        self.routes[f"GET {validation.PREFIX}/collaborators/reader/permission"] = {
            "permission": "read"
        }
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)

    def test_arbitrary_root_cannot_be_selected_by_the_manual_interface(self):
        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "dispose-preflight",
                "--generation",
                GENERATION,
                "--confirmation",
                "dispose",
                "--root",
                "/tmp/foreign-root",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.directory / "aws-called").exists())
