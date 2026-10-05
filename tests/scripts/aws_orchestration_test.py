"""Whole-session stories with durable external AWS/SSM/Terraform/GitHub doubles."""

import copy
import hashlib
import io
import json
import subprocess
import sys
import tarfile
import unittest
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from tests.scripts import aws_publication_evidence_test as publication
from tests.scripts import aws_validation_test as validation

DOUBLE = Path(__file__).with_name("aws_orchestration_double.py")


class ValidationSessionStories(unittest.TestCase):
    def setUp(self):
        publication.PublicationEvidenceStories.setUp(self)
        result = publication.PublicationEvidenceStories.invoke_publication(self)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.env.update(
            AWS_TESTING_STATE_BUCKET="test-state-bucket",
            AWS_TESTING_SECRET_ARN="arn:aws:secretsmanager:eu-north-1:111111111111:secret:onlineshop-test/credentials-example",
            RUNNER_TEMP=str(self.directory),
        )
        self.pointer = {
            "generation": "run-100-attempt-1",
            "host_id": "i-11111111111111111",
            "status": "provisioned",
        }
        addresses = [
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
        ]
        self.cloud = {
            "pointer": self.pointer,
            "state": {
                "version": 4,
                "lineage": "owned-lineage",
                "serial": 1,
                "resources": [
                    {
                        "type": a.split(".")[0],
                        "name": a.split(".")[1],
                        "mode": "managed",
                        "instances": [
                            {
                                "attributes": {
                                    "id": self.pointer["host_id"]
                                    if a == "aws_instance.host"
                                    else "owned-resource",
                                    "tags": {"Generation": self.pointer["generation"]},
                                }
                            }
                        ],
                    }
                    for a in addresses
                ],
            },
            "commands": [],
            "objects": {},
            "live_generation": self.pointer["generation"],
        }
        self.save_cloud()
        for name in ["aws", "terraform", "git"]:
            path = self.bin / name
            path.write_text(
                f"#!/bin/sh\nexec '{sys.executable}' '{DOUBLE}' '{name}' \"$@\"\n"
            )
            path.chmod(0o755)
        self.routes[f"PATCH {validation.PREFIX}/check-runs/400"] = {"id": 400}
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        (self.directory / "images.json").write_text(json.dumps(self.receipt))

    def save_cloud(self):
        (self.directory / "session-cloud.json").write_text(json.dumps(self.cloud))

    def load_cloud(self):
        self.cloud = json.loads((self.directory / "session-cloud.json").read_text())
        return self.cloud

    def invoke(self, command="validate", *, retain=True):
        args = [
            sys.executable,
            str(validation.SCRIPT),
            command,
            "--request",
            str(self.directory / "request.json"),
            "--images",
            str(self.directory / "images.json"),
            "--fixtures",
            str(self.fixture),
            "--output",
            str(self.directory / "evidence"),
        ]
        result = subprocess.run(
            args, env=self.env, capture_output=True, text=True, timeout=30, check=False
        )
        self.assertNotIn("secret-canary", result.stdout + result.stderr)
        if result.returncode == 0 and command == "validate" and retain:
            self.retain_evidence()
            return self.finalize_success()
        return result

    def invoke_dispose(self, generation):
        self.routes = json.loads((self.directory / "routes.json").read_text())
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        env = {
            **self.env,
            "AWS_TESTING_OPERATOR_ROLE": (
                "arn:aws:iam::111111111111:role/onlineshop-test-operator"
            ),
            "GITHUB_WORKFLOW_REF": (
                f"{validation.REPO}/.github/workflows/aws-dispose.yml@refs/heads/main"
            ),
        }
        result = subprocess.run(
            [
                sys.executable,
                str(validation.SCRIPT),
                "dispose",
                "--generation",
                generation,
                "--confirmation",
                "dispose",
                "--output",
                str(self.directory / "dispose-evidence"),
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.load_cloud()
        return result

    def retain_evidence(self, *, case=None):
        output = self.directory / "evidence"
        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as archive:
            for path in sorted(output.iterdir()):
                if path.is_file():
                    data = path.read_bytes()
                    if case == "malicious" and path.name == "summary.json":
                        data = data.replace(
                            b'"durable_terminal": true', b'"durable_terminal": false'
                        )
                    archive.writestr(path.name, data)
        data = zipped.getvalue()
        self.runtime_digest = "sha256:" + hashlib.sha256(data).hexdigest()
        run = self.env["GITHUB_RUN_ID"]
        attempt = self.env["GITHUB_RUN_ATTEMPT"]
        routes = json.loads((self.directory / "routes.json").read_text())
        artifact = {
            "id": 800,
            "name": f"aws-runtime-evidence-{run}-{attempt}",
            "expired": False,
            "size_in_bytes": len(data),
            "digest": self.runtime_digest,
            "created_at": "2026-10-05T00:00:00Z",
            "expires_at": "2026-10-19T00:00:00Z",
            "workflow_run": {"id": int(run), "head_sha": "d" * 40},
        }
        workflow = yaml.safe_load(
            (
                validation.SCRIPT.parents[1] / ".github/workflows/aws-validation.yml"
            ).read_text()
        )
        upload = next(
            step
            for step in workflow["jobs"]["validate"]["steps"]
            if step.get("id") == "runtime_evidence"
        )
        created = datetime(2026, 10, 5, tzinfo=timezone.utc)
        # Artifact creation finishes after the expiration clock starts at upload.
        expires = created + timedelta(days=upload["with"]["retention-days"], seconds=-1)
        artifact["expires_at"] = expires.isoformat().replace("+00:00", "Z")
        if case == "expired":
            artifact["expired"] = True
        if case == "short-retention":
            artifact["expires_at"] = "2026-10-18T00:00:00Z"
        if case == "stale":
            artifact["name"] = "aws-runtime-evidence-200-999"
        if case == "digest":
            artifact["digest"] = "sha256:" + "0" * 64
        if case == "empty":
            data = b""
        route = (
            f"GET {validation.PREFIX}/actions/runs/{run}/artifacts?per_page=100&page=1"
        )
        routes[route]["artifacts"].append(artifact)
        routes[f"GET {validation.PREFIX}/actions/artifacts/800/zip"] = {
            "__bytes__": validation.base64.b64encode(data).decode()
        }
        jobs = routes[
            f"GET {validation.PREFIX}/actions/runs/{run}/attempts/{attempt}/jobs?per_page=100&page=1"
        ]["jobs"]
        jobs.append(
            {
                "id": 900,
                "name": "Validate current candidate in AWS",
                "head_sha": "d" * 40,
                "run_id": int(run),
                "run_attempt": int(attempt),
                "status": "in_progress",
                "conclusion": None,
                "steps": [
                    {
                        "name": "Prepare bound runtime evidence",
                        "number": 10,
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "name": "Retain sanitized runtime evidence",
                        "number": 11,
                        "status": "completed",
                        "conclusion": "failure"
                        if case == "upload-failure"
                        else "success",
                    },
                ],
            }
        )
        (self.directory / "routes.json").write_text(json.dumps(routes))

    def finalize_success(self):
        workflow = yaml.safe_load(
            (
                validation.SCRIPT.parents[1] / ".github/workflows/aws-validation.yml"
            ).read_text()
        )
        step = next(
            item
            for item in workflow["jobs"]["validate"]["steps"]
            if "finalize-success" in item.get("run", "")
        )
        # The pinned upload action emits bare hex, while the REST API uses sha256:.
        action_digest = self.runtime_digest.removeprefix("sha256:")
        digest = step["env"]["RUNTIME_ARTIFACT_DIGEST"].replace(
            "${{ steps.runtime_evidence.outputs.artifact-digest }}", action_digest
        )
        return subprocess.run(
            [
                sys.executable,
                str(validation.SCRIPT),
                "finalize-success",
                "--request",
                str(self.directory / "request.json"),
                "--artifact-id",
                "800",
                "--artifact-digest",
                digest,
            ],
            env=self.env | {"GITHUB_JOB": "validate"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def test_real_upload_action_digest_and_retention_timing_finalize_success(self):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.success_patches()), 1)

    def test_runtime_and_preupload_cancellation_leave_check_pending(self):
        result = self.invoke(retain=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(self.success_patches())
        summary = json.loads((self.directory / "evidence" / "summary.json").read_text())
        self.assertEqual(summary["status"], "pending-success")
        self.assertFalse(summary["aws_validation_success"])

    def test_summary_write_failure_never_publishes_success(self):
        (self.directory / "evidence" / "summary.json").mkdir(parents=True)
        self.assertNotEqual(self.invoke(retain=False).returncode, 0)
        self.assertFalse(self.success_patches())

    def test_upload_failure_and_malicious_stale_empty_or_unretained_artifact_refuse_success(
        self,
    ):
        for case in [
            "upload-failure",
            "malicious",
            "stale",
            "empty",
            "expired",
            "short-retention",
            "digest",
        ]:
            with self.subTest(case=case):
                if case != "upload-failure":
                    self.temp.cleanup()
                    self.setUp()
                result = self.invoke(retain=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.retain_evidence(case=case)
                self.assertNotEqual(self.finalize_success().returncode, 0)
                self.assertFalse(self.success_patches())

    def calls(self):
        path = self.directory / "session-calls.jsonl"
        return (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )

    def success_patches(self):
        return [
            json.loads(line)
            for line in (self.directory / "calls.jsonl").read_text().splitlines()
            if '"conclusion": "success"' in line
        ]

    def test_complete_session_persists_generation_applies_saved_plan_and_requires_bound_reports(
        self,
    ):
        (self.directory / "evidence").mkdir()
        (self.directory / "evidence" / "admission.json").write_text(
            '{"status":"pending","aws_validation_success":false}'
        )
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse((self.directory / "evidence" / "admission.json").exists())
        cloud = self.load_cloud()
        self.assertEqual(cloud["pointer"]["generation"], "run-200-attempt-1")
        self.assertEqual(cloud["pointer"]["status"], "completed")
        self.assertNotIn("etag", json.dumps(cloud["pointer"]).lower())
        self.assertEqual(len(self.success_patches()), 1)
        calls = self.calls()
        apply = next(
            c for c in calls if c["tool"] == "terraform" and c["args"][0] == "apply"
        )
        self.assertIn("current.tfplan", apply["args"])
        self.assertFalse(any("-lock=false" in c["args"] for c in calls))
        first_mutation = next(
            c
            for c in calls
            if c["tool"] == "aws" and c["args"][:2] in [["ecr", "put-image"]]
        )
        self.assertEqual(first_mutation["generation"], "run-200-attempt-1")
        self.assertTrue((self.directory / "evidence" / "summary.json").is_file())
        self.assertTrue((self.directory / "evidence" / "reports.tar").is_file())

    def test_preflight_authorizes_receipt_and_candidate_without_aws(self):
        result = self.invoke("validate-preflight")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c["tool"] == "aws" for c in self.calls()))
        self.jobs[2]["conclusion"] = "skipped"
        self.routes[
            f"GET {validation.PREFIX}/actions/runs/200/attempts/1/jobs?per_page=100&page=1"
        ] = {"jobs": self.jobs}
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        result = self.invoke("validate-preflight")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["tool"] == "aws" for c in self.calls()))

    def test_pre_oidc_preflight_rejects_modified_trusted_runtime_inputs(self):
        self.cloud["modified_controller"] = True
        self.save_cloud()
        result = self.invoke("validate-preflight")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c["tool"] == "aws" for c in self.calls()))

    def test_known_nested_terraform_shapes_are_not_unknown_configuration(self):
        self.cloud["nested_unknown_shapes"] = True
        self.save_cloud()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_apply_failure_or_configuration_replacement_never_runs_runtime_or_success(
        self,
    ):
        for case in ["apply_failure", "replacement", "config_drift"]:
            with self.subTest(case=case):
                initial = copy.deepcopy(self.cloud)
                self.cloud[case] = True
                self.save_cloud()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.success_patches())
                cloud = self.load_cloud()
                self.assertFalse(
                    any(c["Comment"].endswith(":runtime") for c in cloud["commands"])
                )
                self.cloud = initial
                self.save_cloud()
                import shutil

                shutil.rmtree(self.directory / "evidence", ignore_errors=True)

    def test_exit_zero_missing_stage_wrong_hash_cleanup_or_old_candidate_cannot_pass(
        self,
    ):
        for case in [
            "missing_stage",
            "wrong_report_hash",
            "old_candidate",
            "cleanup_unknown",
            "empty_reports",
        ]:
            with self.subTest(case=case):
                initial = copy.deepcopy(self.cloud)
                self.cloud[case] = True
                self.save_cloud()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.success_patches())
                self.cloud = initial
                self.save_cloud()
                import shutil

                shutil.rmtree(self.directory / "evidence", ignore_errors=True)

    def test_unknown_send_is_discovered_once_next_attempt_and_never_retried_as_success(
        self,
    ):
        self.cloud["lost_send"] = True
        self.save_cloud()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        cloud = self.load_cloud()
        self.assertEqual(
            len([c for c in cloud["commands"] if c["Comment"].endswith(":runtime")]), 1
        )
        self.assertEqual(cloud["pointer"]["status"], "running")
        self.assertFalse(self.success_patches())
        # The next authorized attempt sees the same durable external cloud history.
        self.new_attempt()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        cloud = self.load_cloud()
        self.assertTrue(
            any(key.endswith("recovered-aborted.json") for key in cloud["objects"])
        )
        self.assertEqual(cloud["pointer"]["generation"], "run-200-attempt-2")
        self.assertEqual(len(self.success_patches()), 1)

    def new_attempt(self, attempt=2):
        import shutil

        shutil.rmtree(self.directory / "evidence", ignore_errors=True)
        self.env["GITHUB_RUN_ATTEMPT"] = str(attempt)
        request = json.loads((self.directory / "request.json").read_text())
        request["validation_run_attempt"] = attempt
        request["check_run_id"] = 399 + attempt
        (self.directory / "request.json").write_text(json.dumps(request))
        self.receipt.update(request)
        self.source["run_attempt"] = attempt
        self.routes[f"GET {validation.PREFIX}/check-runs/{399 + attempt}"] = {
            **self.routes[f"GET {validation.PREFIX}/check-runs/400"],
            "id": 399 + attempt,
            "external_id": f"aws-validation:200:{attempt}",
        }
        self.routes[
            f"GET {validation.PREFIX}/commits/{validation.CANDIDATE}/check-runs?check_name=AWS%20validation&filter=latest&per_page=100&page=1"
        ] = {"check_runs": [{"id": 399 + attempt, "app": {"slug": "github-actions"}}]}
        self.routes[f"PATCH {validation.PREFIX}/check-runs/{399 + attempt}"] = {
            "id": 399 + attempt
        }
        # Build an authentic attempt-2 artifact route using the same test fixture bytes.
        import hashlib
        import io
        import zipfile

        zipped = io.BytesIO()
        with zipfile.ZipFile(zipped, "w") as archive:
            archive.writestr("images.json", json.dumps(self.receipt))
        data = zipped.getvalue()
        self.routes[f"GET {validation.PREFIX}/actions/runs/200/attempts/{attempt}"] = (
            self.source
        )
        self.routes[
            f"GET {validation.PREFIX}/actions/runs/200/attempts/{attempt}/jobs?per_page=100&page=1"
        ] = {"jobs": self.jobs}
        self.routes[
            f"GET {validation.PREFIX}/actions/runs/200/artifacts?per_page=100&page=1"
        ] = {
            "artifacts": [
                {
                    "id": 699 + attempt,
                    "name": f"aws-images-200-{attempt}",
                    "expired": False,
                    "size_in_bytes": len(data),
                    "digest": "sha256:" + hashlib.sha256(data).hexdigest(),
                    "workflow_run": {"id": 200, "head_sha": validation.CONTROLLER},
                }
            ]
        }
        self.routes[
            f"GET {validation.PREFIX}/actions/artifacts/{699 + attempt}/zip"
        ] = {"__bytes__": validation.base64.b64encode(data).decode()}
        (self.directory / "routes.json").write_text(json.dumps(self.routes))
        (self.directory / "images.json").write_text(json.dumps(self.receipt))

    def test_ambiguous_or_zero_discovery_and_active_host_refuse_recovery(self):
        self.cloud["lost_send"] = True
        self.save_cloud()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.new_attempt()
        original = copy.deepcopy(self.load_cloud())
        for case in [
            "zero_discovery",
            "duplicate_discovery",
            "detached_test",
            "host_lock",
            "active_ssm",
        ]:
            with self.subTest(case=case):
                self.cloud = copy.deepcopy(original)
                self.cloud[case] = True
                self.save_cloud()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.success_patches())
                self.assertEqual(
                    self.load_cloud()["pointer"]["generation"], "run-200-attempt-1"
                )
                import shutil

                shutil.rmtree(self.directory / "evidence", ignore_errors=True)

    def test_disposed_missing_state_and_stale_completed_state_fail_closed(self):
        for case in ["disposed", "missing", "mismatch"]:
            with self.subTest(case=case):
                original = copy.deepcopy(self.cloud)
                if case == "disposed":
                    self.cloud["pointer"]["status"] = "disposed"
                if case == "missing":
                    self.cloud["state"] = {}
                if case == "mismatch":
                    self.cloud["pointer"]["generation"] = "run-99-attempt-1"
                self.save_cloud()
                self.assertNotEqual(self.invoke().returncode, 0)
                self.assertFalse(self.success_patches())
                self.assertFalse(self.load_cloud()["commands"])
                self.cloud = original
                self.save_cloud()
                import shutil

                shutil.rmtree(self.directory / "evidence", ignore_errors=True)

    def test_exact_owner_empty_host_migration_pointer_admits_verified_existing_state(
        self,
    ):
        self.cloud["pointer"]["purpose"] = "owner-empty-host-generation-migration"
        self.save_cloud()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        cloud = self.load_cloud()
        self.assertEqual(cloud["pointer"]["generation"], "run-200-attempt-1")
        self.assertEqual(cloud["pointer"]["status"], "completed")
        self.assertTrue(any(call["tool"] == "terraform" for call in self.calls()))

    def prepare_verified_disposed_environment(self):
        generation = "run-200-attempt-1"
        host_id = "i-11111111111111111"
        request = json.loads((self.directory / "request.json").read_text())
        request["validation_run_attempt"] = 1
        self.pointer = {
            "schema": 1,
            "generation": generation,
            "host_id": host_id,
            "status": "disposed",
            "predecessor": {"generation": "run-199-attempt-1", "host_id": host_id},
            "host_predecessor": {"generation": generation, "host_id": host_id},
            "request": request,
            "images": self.receipt["images"],
            "retained": [],
        }
        self.cloud.update(
            pointer=self.pointer,
            state={
                "version": 4,
                "lineage": "owned-lineage",
                "serial": 2,
                "resources": [],
                "outputs": {},
            },
            host_id=host_id,
            live_generation=generation,
            host_generation=generation,
            prior_generation=generation,
            create_environment=True,
            host_setup=False,
            resources_absent=True,
            ssm_history_expired=True,
        )
        inventory = {
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
            "resource_ids": inventory,
            "verified_absence": True,
        }
        self.cloud["objects"][f"operations/{generation}/dispose-terminal.json"] = (
            validation.base64.b64encode(json.dumps(terminal).encode()).decode()
        )
        disposal_intent = {
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
            "resource_ids": inventory,
            "status": "intent",
        }
        self.cloud["objects"][f"operations/{generation}/dispose-intent.json"] = (
            validation.base64.b64encode(json.dumps(disposal_intent).encode()).decode()
        )
        self.cloud["objects"][f"operations/{generation}/intent.json"] = (
            validation.base64.b64encode(
                json.dumps(dict(self.pointer, status="running")).encode()
            ).decode()
        )
        self.save_cloud()

    def prepare_recreation_fixture(self, generation):
        suffix = (
            "33333333333333333"
            if generation.endswith("attempt-3")
            else "22222222222222222"
        )
        identifiers = {
            "aws_vpc.main": "vpc-" + suffix,
            "aws_subnet.host": "subnet-" + suffix,
            "aws_internet_gateway.main": "igw-" + suffix,
            "aws_route_table.host": "rtb-" + suffix,
            "aws_route.outbound": "r-rtb-" + suffix + "1080289494",
            "aws_route_table_association.host": "rtbassoc-" + suffix,
            "aws_security_group.host": "sg-" + suffix,
            "aws_launch_template.host": "lt-" + suffix,
            "aws_instance.host": "i-" + suffix,
            "aws_ec2_tag.network_generation": "eni-" + suffix + ",Generation",
        }
        tags = {
            "ManagedBy": "onlineshop-test",
            "Repository": "Djimi/OnlineShop-full-stack",
            "Generation": generation,
        }
        stable_tags = {key: value for key, value in tags.items() if key != "Generation"}
        state_values = {
            "aws_vpc.main": {
                "id": identifiers["aws_vpc.main"],
                "cidr_block": "10.83.0.0/16",
                "enable_dns_support": True,
                "enable_dns_hostnames": True,
                "tags": tags,
            },
            "aws_subnet.host": {
                "id": identifiers["aws_subnet.host"],
                "vpc_id": identifiers["aws_vpc.main"],
                "cidr_block": "10.83.1.0/24",
                "availability_zone": "eu-north-1a",
                "map_public_ip_on_launch": True,
                "tags": tags,
            },
            "aws_internet_gateway.main": {
                "id": identifiers["aws_internet_gateway.main"],
                "vpc_id": identifiers["aws_vpc.main"],
                "tags": tags,
            },
            "aws_route_table.host": {
                "id": identifiers["aws_route_table.host"],
                "vpc_id": identifiers["aws_vpc.main"],
                "tags": tags,
            },
            "aws_route.outbound": {
                "id": identifiers["aws_route.outbound"],
                "route_table_id": identifiers["aws_route_table.host"],
                "destination_cidr_block": "0.0.0.0/0",
                "gateway_id": identifiers["aws_internet_gateway.main"],
            },
            "aws_route_table_association.host": {
                "id": identifiers["aws_route_table_association.host"],
                "subnet_id": identifiers["aws_subnet.host"],
                "route_table_id": identifiers["aws_route_table.host"],
            },
            "aws_security_group.host": {
                "id": identifiers["aws_security_group.host"],
                "name": "onlineshop-test-host",
                "description": "SSM-only testing host; no incoming connectivity",
                "vpc_id": identifiers["aws_vpc.main"],
                "ingress": [],
                "egress": [
                    {
                        "from_port": 0,
                        "to_port": 0,
                        "protocol": "-1",
                        "cidr_blocks": ["0.0.0.0/0"],
                    }
                ],
                "tags": tags,
            },
            "aws_launch_template.host": {
                "id": identifiers["aws_launch_template.host"],
                "name": "onlineshop-test-host",
                "tags": stable_tags,
                "tag_specifications": [
                    {"resource_type": kind, "tags": stable_tags}
                    for kind in ["instance", "volume", "network-interface"]
                ],
            },
            "aws_instance.host": {
                "id": identifiers["aws_instance.host"],
                "ami": "ami-04478a3e21a0d79a7",
                "instance_type": "m7i-flex.large",
                "subnet_id": identifiers["aws_subnet.host"],
                "vpc_security_group_ids": [identifiers["aws_security_group.host"]],
                "associate_public_ip_address": True,
                "iam_instance_profile": "onlineshop-test-host",
                "launch_template": [
                    {"id": identifiers["aws_launch_template.host"], "version": "1"}
                ],
                "primary_network_interface_id": "eni-" + suffix,
                "root_block_device": [
                    {
                        "volume_id": "vol-" + suffix,
                        "volume_size": 50,
                        "volume_type": "gp3",
                        "encrypted": True,
                        "delete_on_termination": True,
                    }
                ],
                "metadata_options": [
                    {
                        "http_endpoint": "enabled",
                        "http_tokens": "required",
                        "http_put_response_hop_limit": 1,
                        "http_protocol_ipv6": "disabled",
                        "instance_metadata_tags": "disabled",
                    }
                ],
                "tags": tags,
                "volume_tags": tags,
            },
            "aws_ec2_tag.network_generation": {
                "id": "eni-" + suffix + ",Generation",
                "resource_id": "eni-" + suffix,
                "key": "Generation",
                "value": generation,
            },
        }
        planned_values = copy.deepcopy(state_values)
        for address in identifiers:
            planned_values[address]["id"] = None
        planned_values["aws_subnet.host"]["vpc_id"] = None
        planned_values["aws_internet_gateway.main"]["vpc_id"] = None
        planned_values["aws_route_table.host"]["vpc_id"] = None
        planned_values["aws_route.outbound"].update(
            route_table_id=None, gateway_id=None
        )
        planned_values["aws_route_table_association.host"].update(
            subnet_id=None, route_table_id=None
        )
        planned_values["aws_security_group.host"]["vpc_id"] = None
        planned_values["aws_instance.host"].update(
            subnet_id=None,
            vpc_security_group_ids=[None],
            primary_network_interface_id=None,
            root_block_device=[
                {
                    **planned_values["aws_instance.host"]["root_block_device"][0],
                    "volume_id": None,
                }
            ],
            launch_template=[{"id": None, "version": None}],
        )
        planned_values["aws_ec2_tag.network_generation"]["resource_id"] = None
        resources = [
            {"address": address, "values": values}
            for address, values in planned_values.items()
        ]
        changes = [
            {
                "address": address,
                "change": {
                    "actions": ["create"],
                    "before": None,
                    "after": values,
                    "after_unknown": {},
                },
            }
            for address, values in planned_values.items()
        ]
        resources_by_address = [
            {
                "type": address.split(".", 1)[0],
                "name": address.split(".", 1)[1],
                "mode": "managed",
                "instances": [{"attributes": values}],
            }
            for address, values in state_values.items()
        ]
        self.cloud.update(
            create_plan={
                "planned_values": {"root_module": {"resources": resources}},
                "resource_changes": changes,
            },
            create_state={
                "version": 4,
                "lineage": "owned-lineage",
                "serial": self.cloud["state"]["serial"] + 1,
                "resources": resources_by_address,
                "outputs": {
                    "instance_id": {
                        "value": identifiers["aws_instance.host"],
                        "type": "string",
                        "sensitive": False,
                    },
                    "vpc_id": {
                        "value": identifiers["aws_vpc.main"],
                        "type": "string",
                        "sensitive": False,
                    },
                    "security_group_id": {
                        "value": identifiers["aws_security_group.host"],
                        "type": "string",
                        "sensitive": False,
                    },
                    "generation": {
                        "value": generation,
                        "type": "string",
                        "sensitive": False,
                    },
                    "root_volume_id": {
                        "value": "vol-" + suffix,
                        "type": "string",
                        "sensitive": False,
                    },
                },
            },
            created_host_id=identifiers["aws_instance.host"],
            host_setup_predecessor=self.cloud["pointer"]["generation"],
        )
        self.save_cloud()

    def test_verified_disposal_recreates_new_host_runs_reset_reports_and_finalizes_retained_success(
        self,
    ):
        self.prepare_verified_disposed_environment()
        self.new_attempt(attempt=2)
        self.prepare_recreation_fixture("run-200-attempt-2")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        cloud = self.load_cloud()
        self.assertEqual(cloud["pointer"]["generation"], "run-200-attempt-2")
        self.assertEqual(cloud["pointer"]["status"], "completed")
        self.assertEqual(cloud["pointer"]["schema"], 1)
        self.assertEqual(
            set(cloud["pointer"]),
            {
                "schema",
                "generation",
                "host_id",
                "status",
                "predecessor",
                "host_predecessor",
                "request",
                "images",
                "retained",
            },
        )
        self.assertEqual(cloud["pointer"]["host_id"], "i-22222222222222222")
        self.assertTrue(cloud["host_setup"])
        comments = [command["Comment"] for command in cloud["commands"]]
        self.assertTrue(any(comment.endswith(":host-setup") for comment in comments))
        self.assertTrue(
            any(comment.endswith(":recreation-probe") for comment in comments)
        )
        self.assertTrue(any(comment.endswith(":runtime") for comment in comments))
        setup_command = next(
            command
            for command in cloud["commands"]
            if command["Comment"].endswith(":host-setup")
        )
        self.assertEqual(setup_command["InstanceIds"], ["i-22222222222222222"])
        setup_body = setup_command["Parameters"]["commands"][0]
        self.assertIn(
            "operations/runtime-input/run-200-attempt-2/host-setup.tar", setup_body
        )
        self.assertIn("--initialize-disposed-predecessor", setup_body)
        self.assertIn("run-200-attempt-1", setup_body)
        setup_key = "operations/runtime-input/run-200-attempt-2/host-setup.tar"
        setup_bytes = validation.base64.b64decode(cloud["objects"][setup_key])
        with tarfile.open(fileobj=io.BytesIO(setup_bytes), mode="r:") as archive:
            self.assertEqual(
                set(archive.getnames()),
                {
                    "run-stack.py",
                    "compose.yml",
                    "host-setup.sh",
                    "host-session.py",
                    "bootstrap.json",
                },
            )
            bootstrap = json.load(archive.extractfile("bootstrap.json"))
            self.assertEqual(bootstrap["account_id"], "111111111111")
            self.assertEqual(bootstrap["region"], "eu-north-1")
            self.assertEqual(
                bootstrap["secret_arn"],
                "arn:aws:secretsmanager:eu-north-1:111111111111:secret:onlineshop-test/credentials-example",
            )
        self.assertIn(hashlib.sha256(setup_bytes).hexdigest(), setup_body)
        self.assertEqual(len(self.success_patches()), 1)
        summary = json.loads((self.directory / "evidence" / "summary.json").read_text())
        self.assertEqual(summary["status"], "pending-success")
        self.assertEqual(summary["tests"], 4)
        self.assertTrue((self.directory / "evidence" / "reports.tar").is_file())

    def test_recreation_rejects_foreign_or_inconsistent_terraform_outputs(self):
        cases = ["unknown", "sensitive", "wrong-type", "wrong-root-volume"]
        baseline = copy.deepcopy(self.cloud)
        for case in cases:
            with self.subTest(case=case):
                self.cloud = copy.deepcopy(baseline)
                self.prepare_verified_disposed_environment()
                self.new_attempt(attempt=2)
                self.prepare_recreation_fixture("run-200-attempt-2")
                outputs = self.cloud["create_state"]["outputs"]
                if case == "unknown":
                    outputs["unexpected"] = {
                        "value": "foreign",
                        "type": "string",
                        "sensitive": False,
                    }
                elif case == "sensitive":
                    outputs["instance_id"]["sensitive"] = True
                elif case == "wrong-type":
                    outputs["generation"]["type"] = "number"
                else:
                    outputs["root_volume_id"]["value"] = "vol-99999999999999999"
                self.save_cloud()

                result = self.invoke(retain=False)

                self.assertNotEqual(result.returncode, 0)
                cloud = self.load_cloud()
                self.assertEqual(cloud["pointer"]["status"], "provisioning")
                self.assertFalse(cloud["host_setup"])
                self.assertFalse(self.success_patches())

    def test_production_recreation_record_survives_disposal_noop_and_second_recreation(
        self,
    ):
        self.prepare_verified_disposed_environment()
        self.new_attempt(attempt=2)
        self.prepare_recreation_fixture("run-200-attempt-2")

        recreated = self.invoke()

        self.assertEqual(recreated.returncode, 0, recreated.stderr)
        cloud = self.load_cloud()
        generation = "run-200-attempt-2"
        immutable_intent = cloud["objects"][f"operations/{generation}/intent.json"]
        original_record = json.loads(validation.base64.b64decode(immutable_intent))
        self.assertEqual(original_record["status"], "provisioned")
        # The expired-history shortcut admitted the first validation attempt;
        # the newly created SSM commands are now observable to disposal.
        self.cloud["ssm_history_expired"] = False
        self.save_cloud()
        applies_before_dispose = sum(
            call["tool"] == "terraform" and call["args"][0] == "apply"
            for call in self.calls()
        )

        disposed = self.invoke_dispose(generation)

        self.assertEqual(disposed.returncode, 0, disposed.stderr)
        cloud = self.load_cloud()
        self.assertEqual(cloud["pointer"]["status"], "disposed")
        self.assertEqual(
            cloud["objects"][f"operations/{generation}/intent.json"], immutable_intent
        )
        noop = self.invoke_dispose(generation)
        self.assertEqual(noop.returncode, 0, noop.stderr)
        summary = json.loads(
            (self.directory / "dispose-evidence" / "summary.json").read_text()
        )
        self.assertEqual(summary["status"], "no-op")
        self.assertEqual(
            sum(
                call["tool"] == "terraform" and call["args"][0] == "apply"
                for call in self.calls()
            ),
            applies_before_dispose + 1,
        )

        self.new_attempt(attempt=3)
        self.prepare_recreation_fixture("run-200-attempt-3")
        second_recreation = self.invoke()

        self.assertEqual(second_recreation.returncode, 0, second_recreation.stderr)
        cloud = self.load_cloud()
        self.assertEqual(cloud["pointer"]["generation"], "run-200-attempt-3")
        self.assertEqual(cloud["pointer"]["status"], "completed")
        self.assertEqual(cloud["pointer"]["host_id"], "i-33333333333333333")
        self.assertTrue(cloud["host_setup"])

    def test_recreation_refuses_an_uninspected_or_noncreate_plan_before_host_setup(
        self,
    ):
        self.prepare_verified_disposed_environment()
        self.new_attempt(attempt=2)
        self.prepare_recreation_fixture("run-200-attempt-2")
        self.cloud["create_plan"]["resource_changes"][0]["change"]["actions"] = [
            "delete",
            "create",
        ]
        self.save_cloud()
        result = self.invoke(retain=False)
        self.assertNotEqual(result.returncode, 0)
        cloud = self.load_cloud()
        self.assertEqual(cloud["pointer"]["status"], "provisioning")
        self.assertFalse(
            any(
                call["tool"] == "terraform" and call["args"][0] == "apply"
                for call in self.calls()
            )
        )
        self.assertFalse(
            any(
                command["Comment"].endswith((":host-setup", ":runtime"))
                for command in cloud["commands"]
            )
        )
        self.assertFalse(self.success_patches())

    def test_recreation_refuses_a_plan_that_changes_the_pinned_free_plan_host(self):
        self.prepare_verified_disposed_environment()
        self.new_attempt(attempt=2)
        self.prepare_recreation_fixture("run-200-attempt-2")
        instance = next(
            resource["values"]
            for resource in self.cloud["create_plan"]["planned_values"]["root_module"][
                "resources"
            ]
            if resource["address"] == "aws_instance.host"
        )
        instance["instance_type"] = "unapproved.large"
        self.save_cloud()
        result = self.invoke(retain=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(
            any(
                call["tool"] == "terraform" and call["args"][0] == "apply"
                for call in self.calls()
            )
        )
        self.assertFalse(
            any(
                command["Comment"].endswith(":host-setup")
                for command in self.load_cloud()["commands"]
            )
        )
        self.assertFalse(self.success_patches())

    def test_recreation_refuses_state_changes_between_inspection_and_exact_apply(self):
        self.prepare_verified_disposed_environment()
        self.new_attempt(attempt=2)
        self.prepare_recreation_fixture("run-200-attempt-2")
        self.cloud["state_changes_after_create_plan"] = True
        self.save_cloud()
        result = self.invoke(retain=False)
        self.assertNotEqual(result.returncode, 0)
        cloud = self.load_cloud()
        self.assertEqual(cloud["state"]["resources"], [])
        self.assertEqual(cloud["pointer"]["status"], "provisioning")
        self.assertFalse(
            any(
                call["tool"] == "terraform" and call["args"][0] == "apply"
                for call in self.calls()
            )
        )
        self.assertFalse(
            any(
                command["Comment"].endswith(":host-setup")
                for command in cloud["commands"]
            )
        )
        self.assertFalse(self.success_patches())

    def test_recreation_apply_crash_after_state_mutation_stays_non_success_and_never_sets_up_host(
        self,
    ):
        self.prepare_verified_disposed_environment()
        self.new_attempt(attempt=2)
        self.prepare_recreation_fixture("run-200-attempt-2")
        self.cloud["create_crash_after_apply"] = True
        self.save_cloud()
        result = self.invoke(retain=False)
        self.assertNotEqual(result.returncode, 0)
        cloud = self.load_cloud()
        self.assertEqual(cloud["pointer"]["status"], "provisioning")
        self.assertEqual(len(cloud["state"]["resources"]), 10)
        self.assertFalse(cloud["host_setup"])
        self.assertFalse(
            any(
                command["Comment"].endswith(":runtime") for command in cloud["commands"]
            )
        )
        self.assertFalse(self.success_patches())

    def test_recreation_requires_exact_disposal_terminal_and_empty_retained_state(self):
        self.prepare_verified_disposed_environment()
        baseline = copy.deepcopy(self.cloud)
        cases = [
            "missing-terminal",
            "state-advanced",
            "partial-state",
            "foreign-intent",
        ]
        for case in cases:
            with self.subTest(case=case):
                self.cloud = copy.deepcopy(baseline)
                self.pointer = self.cloud["pointer"]
                self.new_attempt(attempt=2)
                if case == "missing-terminal":
                    self.cloud["objects"].pop(
                        "operations/run-200-attempt-1/dispose-terminal.json"
                    )
                elif case == "state-advanced":
                    self.cloud["state"]["serial"] += 1
                elif case == "partial-state":
                    self.cloud["state"]["resources"] = [
                        {"type": "aws_vpc", "name": "main", "instances": []}
                    ]
                else:
                    key = "operations/run-200-attempt-1/dispose-intent.json"
                    record = json.loads(
                        validation.base64.b64decode(self.cloud["objects"][key])
                    )
                    record["resource_ids"]["aws_vpc.main"] = "vpc-99999999999999999"
                    self.cloud["objects"][key] = validation.base64.b64encode(
                        json.dumps(record).encode()
                    ).decode()
                self.save_cloud()
                result = self.invoke(retain=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(self.cloud["pointer"]["status"], "disposed")
                self.assertEqual(self.cloud["commands"], [])
                self.assertFalse(
                    any(call["tool"] == "terraform" for call in self.calls())
                )
                self.assertFalse(self.success_patches())
                import shutil

                shutil.rmtree(self.directory / "evidence", ignore_errors=True)

    def test_exact_owner_migration_generation_can_be_recreated_after_verified_disposal(
        self,
    ):
        self.prepare_verified_disposed_environment()
        self.cloud["pointer"] = {
            "generation": "run-200-attempt-1",
            "host_id": "i-11111111111111111",
            "status": "disposed",
            "purpose": "owner-empty-host-generation-migration",
        }
        self.cloud["objects"].pop("operations/run-200-attempt-1/intent.json")
        self.save_cloud()
        self.new_attempt(attempt=2)
        self.prepare_recreation_fixture("run-200-attempt-2")
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        cloud = self.load_cloud()
        self.assertEqual(cloud["pointer"]["generation"], "run-200-attempt-2")
        self.assertEqual(cloud["pointer"]["status"], "completed")
        self.assertEqual(
            cloud["pointer"]["predecessor"]["generation"], "run-200-attempt-1"
        )
        self.assertEqual(len(self.success_patches()), 1)

    def test_disposed_legacy_generation_without_exact_migration_purpose_is_refused(
        self,
    ):
        self.prepare_verified_disposed_environment()
        self.cloud["pointer"] = {
            "generation": "run-200-attempt-1",
            "host_id": "i-11111111111111111",
            "status": "disposed",
        }
        self.cloud["objects"].pop("operations/run-200-attempt-1/intent.json")
        self.save_cloud()
        self.new_attempt(attempt=2)
        result = self.invoke(retain=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.load_cloud()["pointer"]["status"], "disposed")
        self.assertFalse(any(call["tool"] == "terraform" for call in self.calls()))
        self.assertFalse(self.success_patches())

    def test_unknown_purpose_extra_initial_fields_and_nonprovisioned_status_refuse_before_ssm(
        self,
    ):
        cases = [
            {"purpose": "other-owner-operation"},
            {"purpose": "owner-empty-host-generation-migration", "extra": "field"},
            {"purpose": "owner-empty-host-generation-migration", "status": "completed"},
        ]
        for change in cases:
            with self.subTest(change=change):
                original = copy.deepcopy(self.cloud)
                self.cloud["pointer"]["purpose"] = change["purpose"]
                self.cloud["pointer"].update(
                    {key: value for key, value in change.items() if key != "purpose"}
                )
                self.save_cloud()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                calls = self.calls()
                self.assertFalse(any(call["tool"] == "terraform" for call in calls))
                self.assertFalse(
                    any(
                        call["tool"] == "aws"
                        and call["args"][:2] == ["ssm", "send-command"]
                        for call in calls
                    )
                )
                self.cloud = original
                self.save_cloud()
                import shutil

                shutil.rmtree(self.directory / "evidence", ignore_errors=True)

    def test_lost_initial_host_probe_requires_unique_terminal_discovery_before_new_mutation(
        self,
    ):
        self.cloud["lost_probe"] = True
        self.save_cloud()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.new_attempt()
        self.load_cloud()
        self.cloud["zero_discovery"] = True
        self.save_cloud()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            self.load_cloud()["pointer"]["generation"], "run-100-attempt-1"
        )
        self.assertFalse(any(c["tool"] == "terraform" for c in self.calls()))

    def test_failed_runtime_retains_sanitized_report_evidence_but_never_success(self):
        self.cloud["failed_runtime"] = True
        self.save_cloud()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.success_patches())
        self.assertTrue((self.directory / "evidence" / "reports.tar").exists())

    def test_failed_ssm_cannot_pass_with_a_claimed_passed_host_envelope(self):
        self.cloud.update(failed_runtime=True, forged_passed_envelope=True)
        self.save_cloud()
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.success_patches())

    def test_new_candidate_or_newer_pending_attempt_during_runtime_prevents_success(
        self,
    ):
        for flag in ["candidate_changes", "newer_attempt"]:
            with self.subTest(flag=flag):
                original = copy.deepcopy(self.cloud)
                self.cloud[flag] = True
                self.save_cloud()
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.success_patches())
                self.assertTrue((self.directory / "evidence" / "summary.json").exists())
                self.cloud = original
                self.save_cloud()
                (self.directory / "routes.json").write_text(json.dumps(self.routes))
                import shutil

                shutil.rmtree(self.directory / "evidence", ignore_errors=True)

    def test_runtime_launch_hash_and_timeout_mismatch_refuse_recovery(self):
        self.cloud["lost_send"] = True
        self.save_cloud()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.new_attempt()
        original = copy.deepcopy(self.load_cloud())
        for field in ["commands", "executionTimeout"]:
            with self.subTest(field=field):
                self.cloud = copy.deepcopy(original)
                self.cloud["commands"][-1]["Parameters"][field] = ["untrusted"]
                self.save_cloud()
                self.assertNotEqual(self.invoke().returncode, 0)
                self.assertEqual(
                    self.load_cloud()["pointer"]["generation"], "run-200-attempt-1"
                )
                self.assertFalse(self.success_patches())
                import shutil

                shutil.rmtree(self.directory / "evidence", ignore_errors=True)

    def test_ssm_invocation_visibility_delay_is_bounded_pending_not_false_failure(self):
        self.cloud["visibility_delay"] = 1
        self.save_cloud()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.success_patches()), 1)

    def test_apply_crash_gap_accepts_updated_cloud_tags_with_unchanged_host_predecessor(
        self,
    ):
        self.cloud["crash_after_apply"] = True
        self.save_cloud()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.new_attempt()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.load_cloud()["pointer"]["status"], "completed")

    def test_two_consecutive_apply_gaps_retain_exact_idle_host_predecessor(self):
        for attempt in [1, 2]:
            if attempt > 1:
                self.new_attempt(attempt)
            self.load_cloud()
            self.cloud["crash_after_apply"] = True
            self.save_cloud()
            self.assertNotEqual(self.invoke().returncode, 0)
            cloud = self.load_cloud()
            pointer = cloud["pointer"]
            self.assertEqual(pointer["generation"], f"run-200-attempt-{attempt}")
            self.assertEqual(
                pointer["predecessor"]["generation"],
                "run-100-attempt-1" if attempt == 1 else "run-200-attempt-1",
            )
            self.assertEqual(
                pointer["host_predecessor"],
                {"generation": "run-100-attempt-1", "host_id": self.pointer["host_id"]},
            )
            self.assertEqual(
                cloud.get("host_generation", "run-100-attempt-1"), "run-100-attempt-1"
            )
        self.new_attempt(3)
        result = self.invoke(retain=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.load_cloud()["pointer"]["generation"], "run-200-attempt-3"
        )

    def test_repeated_gaps_do_not_admit_an_unrelated_host_generation(self):
        self.cloud["crash_after_apply"] = True
        self.save_cloud()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.new_attempt()
        self.load_cloud()
        self.cloud["host_generation"] = "run-999-attempt-1"
        self.save_cloud()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.assertEqual(
            self.load_cloud()["pointer"]["generation"], "run-200-attempt-1"
        )

    def test_replacement_protects_old_and_new_digests_then_releases_only_known_old_tags(
        self,
    ):
        self.cloud["retention"] = {
            "onlineshop-test-auth:active-owner-proof": "sha256:" + "2" * 64
        }
        self.save_cloud()
        self.assertEqual(self.invoke().returncode, 0)
        self.new_attempt()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        retained = self.load_cloud()["retention"]
        self.assertIn("onlineshop-test-auth:active-owner-proof", retained)
        self.assertTrue(all("active-run-200-attempt-1" not in key for key in retained))
        self.assertEqual(
            len([key for key in retained if "active-run-200-attempt-2" in key]), 5
        )

    def test_incoming_failure_preserves_previous_and_incoming_active_tags(self):
        self.assertEqual(self.invoke().returncode, 0)
        self.new_attempt()
        self.load_cloud()
        self.cloud["failed_runtime"] = True
        self.save_cloud()
        self.assertNotEqual(self.invoke().returncode, 0)
        retained = self.load_cloud()["retention"]
        self.assertEqual(
            len([k for k in retained if "active-run-200-attempt-1" in k]), 5
        )
        self.assertEqual(
            len([k for k in retained if "active-run-200-attempt-2" in k]), 5
        )

    def test_success_after_failed_replacement_releases_all_known_retained_predecessors(
        self,
    ):
        self.assertEqual(self.invoke().returncode, 0)
        self.new_attempt()
        self.load_cloud()
        self.cloud["failed_runtime"] = True
        self.save_cloud()
        self.assertNotEqual(self.invoke().returncode, 0)
        self.new_attempt(3)
        self.load_cloud()
        self.cloud.pop("failed_runtime")
        self.save_cloud()
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        retained = self.load_cloud()["retention"]
        self.assertEqual(len(retained), 5)
        self.assertTrue(all("active-run-200-attempt-3" in k for k in retained))
