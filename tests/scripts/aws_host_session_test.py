"""Execute trusted host session admission/envelope over the real runtime executable."""

import hashlib
import json
import subprocess
import sys
import unittest

from tests.scripts import aws_runtime_test as runtime


class HostSessionStories(unittest.TestCase):
    def setUp(self):
        runtime.RuntimeStories.setUp(self)
        self.env["REPORTS_VALID"] = "1"
        self.receipt = json.loads((self.directory / "images.json").read_text())
        self.request = {
            "repository": "Djimi/OnlineShop-full-stack",
            "pr": 42,
            "head_sha": "a" * 40,
            "base_sha": "b" * 40,
            "candidate_sha": "c" * 40,
            "controller_sha": "d" * 40,
            "validation_run_id": 200,
            "validation_run_attempt": 1,
            "ci_run_id": 100,
            "ci_run_attempt": 1,
            "ci_artifact_id": 300,
            "actor": "owner",
            "triggering_actor": "owner",
            "check_run_id": 400,
        }
        self.receipt.update(self.request)
        self.images = self.state / "images.json"
        self.images.write_text(json.dumps(self.receipt))
        self.binding = {
            "schema": 1,
            "request": self.request,
            "generation": runtime.GENERATION,
            "predecessor_generation": "run-100-attempt-1",
            "host_id": "i-11111111111111111",
            "operation": "runtime",
            "bucket": "test-state-bucket",
            "recovery_verified": True,
            "runtime_hashes": {},
            "images_sha256": hashlib.sha256(self.images.read_bytes()).hexdigest(),
            "fixtures_sha256": self.receipt["fixtures"]["sha256"],
        }
        for name in ["host-session.py", "run-stack.py", "compose.yml", "host-setup.sh"]:
            path = self.runtime / name
            self.binding["runtime_hashes"][name] = (
                hashlib.sha256(path.read_bytes()).hexdigest()
                if path.exists()
                else "0" * 64
            )
        (self.state / "current-generation.json").write_text(
            json.dumps({"generation": "run-100-attempt-1"})
        )
        (self.state / "operation.json").write_text(
            json.dumps({"generation": "run-100-attempt-1", "status": "unknown"})
        )
        aws = self.bin / "aws"
        aws.write_text(
            aws.read_text().replace(
                " else: sys.exit(1)",
                " elif args[:2]==['s3api','put-object']: print(json.dumps({'ETag':'verified','VersionId':'v1'}))\n else: sys.exit(1)",
            )
        )

    def invoke(self):
        (self.state / "binding.json").write_text(json.dumps(self.binding))
        result = subprocess.run(
            [
                sys.executable,
                str(self.runtime / "host-session.py"),
                "--generation",
                runtime.GENERATION,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertNotIn("secret-canary", result.stdout + result.stderr)
        return result

    def test_new_generation_archives_unknown_predecessor_only_after_idle_proof_and_uploads_bound_envelope(
        self,
    ):
        result = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = self.state / "evidence" / runtime.GENERATION
        envelope = json.loads((evidence / "envelope.json").read_text())
        self.assertEqual(envelope["binding"], self.binding)
        self.assertEqual(envelope["stages"], ["reset", "readiness", "e2e", "reports"])
        self.assertTrue(envelope["cleanup_verified"])
        self.assertEqual(
            envelope["reports_sha256"],
            hashlib.sha256((evidence / "reports.tar").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            json.loads((self.state / "history" / "run-100-attempt-1.json").read_text())[
                "status"
            ],
            "unknown",
        )
        self.assertEqual(
            json.loads((self.state / "operation.json").read_text())["status"], "passed"
        )

    def test_binding_hash_or_foreign_predecessor_refuses_before_credentials_and_reset(
        self,
    ):
        for key, value in [
            ("images_sha256", "0" * 64),
            ("predecessor_generation", "run-99-attempt-1"),
        ]:
            with self.subTest(key=key):
                original = self.binding[key]
                self.binding[key] = value
                result = self.invoke()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.directory / "test-started").exists())
                self.assertEqual(
                    json.loads((self.state / "operation.json").read_text())["status"],
                    "unknown",
                )
                self.binding[key] = original

    def test_detached_test_never_allows_automatic_unknown_recovery(self):
        self.env["DETACHED_TEST"] = "1"
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.directory / "test-started").exists())
        self.assertEqual(
            json.loads((self.state / "operation.json").read_text())["status"], "unknown"
        )

    def test_interrupted_admission_at_each_durable_boundary_can_admit_next_attempt(
        self,
    ):
        for boundary in ["intent", "archive", "pointer", "operation", "launch"]:
            with self.subTest(boundary=boundary):
                if boundary != "intent":
                    self.tearDown()
                    self.setUp()
                (self.state / "binding.json").write_text(json.dumps(self.binding))
                runner = self.directory / "interrupt.py"
                runner.write_text("""import importlib.util,os,sys
from pathlib import Path
spec=importlib.util.spec_from_file_location('session',sys.argv[1]);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
boundary=sys.argv[2]; original_new=m.write_new; original_atomic=m.write_atomic; original_run=m.subprocess.run
def new(path,record):
 original_new(path,record)
 if (boundary=='intent' and path.parent.name=='admissions') or (boundary=='archive' and path.parent.name=='history'):os._exit(91)
def atomic(path,record):
 original_atomic(path,record)
 if (boundary=='pointer' and path.name=='current-generation.json') or (boundary=='operation' and path.name=='operation.json'):os._exit(91)
def run(args,**kwargs):
 if boundary=='launch' and str(m.ROOT/'run-stack.py') in args:os._exit(91)
 return original_run(args,**kwargs)
m.write_new=new;m.write_atomic=atomic;m.subprocess.run=run
sys.argv=[sys.argv[1],'--generation',m.read_json(m.STATE/'binding.json')['generation']];sys.exit(m.main())
""")
                result = subprocess.run(
                    [
                        sys.executable,
                        str(runner),
                        str(self.runtime / "host-session.py"),
                        boundary,
                    ],
                    env=self.env,
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(result.returncode, 91)
                archived = self.state / "history" / "run-100-attempt-1.json"
                archived_bytes = (
                    archived.read_bytes() if boundary == "archive" else None
                )
                observed = json.loads(
                    (self.state / "current-generation.json").read_text()
                )["generation"]
                self.binding["predecessor_generation"] = observed
                self.binding["generation"] = "run-200-attempt-2"
                self.binding["request"]["validation_run_attempt"] = 2
                self.receipt["validation_run_attempt"] = 2
                self.images.write_text(json.dumps(self.receipt))
                self.binding["images_sha256"] = hashlib.sha256(
                    self.images.read_bytes()
                ).hexdigest()
                (self.state / "binding.json").write_text(json.dumps(self.binding))
                result = subprocess.run(
                    [
                        sys.executable,
                        str(self.runtime / "host-session.py"),
                        "--generation",
                        "run-200-attempt-2",
                    ],
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=20,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(
                    json.loads(archived.read_text()),
                    {"generation": "run-100-attempt-1", "status": "unknown"},
                )
                if archived_bytes is not None:
                    self.assertEqual(archived.read_bytes(), archived_bytes)

    def test_recovery_probe_refuses_current_operation_with_conflicting_archived_transition(
        self,
    ):
        generation = "run-200-attempt-1"
        prior = {"generation": "run-100-attempt-1", "status": "unknown"}
        transition = {
            "schema": 1,
            "old": "run-100-attempt-1",
            "new": generation,
            "request": self.request,
            "prior": prior,
        }
        (self.state / "admissions").mkdir()
        (self.state / "admissions" / (generation + ".json")).write_text(
            json.dumps(transition)
        )
        (self.state / "history").mkdir()
        archived = self.state / "history" / "run-100-attempt-1.json"
        archived.write_text(
            json.dumps({"generation": "run-100-attempt-1", "status": "failed"})
        )
        (self.state / "current-generation.json").write_text(
            json.dumps({"generation": generation})
        )
        (self.state / "operation.json").write_text(
            json.dumps({"generation": generation, "status": "recovered-aborted"})
        )
        probe = self.directory / "admission-probe.py"
        probe.write_text("""import importlib.util,sys
spec=importlib.util.spec_from_file_location('session',sys.argv[1]);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
module.admission_operation(sys.argv[2])
""")
        result = subprocess.run(
            [
                sys.executable,
                str(probe),
                str(self.runtime / "host-session.py"),
                generation,
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            archived.read_text(),
            json.dumps({"generation": "run-100-attempt-1", "status": "failed"}),
        )
        self.assertEqual(
            json.loads((self.state / "current-generation.json").read_text()),
            {"generation": generation},
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_runtime_failure_has_no_success_envelope(self):
        self.env["READINESS_FAILURE"] = "1"
        result = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        path = self.state / "evidence" / runtime.GENERATION / "envelope.json"
        if path.exists():
            envelope = json.loads(path.read_text())
            self.assertEqual(envelope["status"], "failed")

    def test_next_session_preserves_previous_evidence_and_holds_one_lock_through_runtime(
        self,
    ):
        self.assertEqual(self.invoke().returncode, 0)
        self.assertTrue(
            (self.state / "evidence" / runtime.GENERATION / "envelope.json").exists()
        )
        previous = (
            self.state / "evidence" / runtime.GENERATION / "envelope.json"
        ).read_bytes()
        self.binding["predecessor_generation"] = runtime.GENERATION
        self.binding["generation"] = "run-201-attempt-1"
        self.binding["request"]["validation_run_id"] = 201
        self.receipt["validation_run_id"] = 201
        self.images.write_text(json.dumps(self.receipt))
        self.binding["images_sha256"] = hashlib.sha256(
            self.images.read_bytes()
        ).hexdigest()
        (self.state / "binding.json").write_text(json.dumps(self.binding))
        result = subprocess.run(
            [
                sys.executable,
                str(self.runtime / "host-session.py"),
                "--generation",
                "run-201-attempt-1",
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (
                self.state / "evidence" / runtime.GENERATION / "envelope.json"
            ).read_bytes(),
            previous,
        )
