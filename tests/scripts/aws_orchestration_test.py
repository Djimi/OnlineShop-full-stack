"""Whole-session stories with durable external AWS/SSM/Terraform/GitHub doubles."""

import copy
import hashlib
import io
import json
import subprocess
import sys
import unittest
import zipfile
from pathlib import Path

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
                self.runtime_digest,
            ],
            env=self.env | {"GITHUB_JOB": "validate"},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

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
