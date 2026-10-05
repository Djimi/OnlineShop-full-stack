"""Host-runtime stories at external command boundaries; no real cloud calls."""

import fcntl
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / "infra/aws/runtime"
GENERATION = "run-200-attempt-1"


class RuntimeStories(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.runtime = self.directory / "runtime"
        self.runtime.mkdir()
        if RUNTIME.exists():
            shutil.copytree(RUNTIME, self.runtime, dirs_exist_ok=True)
        self.state = self.runtime / ".runtime"
        self.state.mkdir()
        (self.state / "current-generation.json").write_text(
            json.dumps({"generation": GENERATION})
        )
        (self.runtime / "bootstrap.json").write_text(
            json.dumps(
                {
                    "account_id": "111111111111",
                    "region": "eu-north-1",
                    "secret_arn": "arn:aws:secretsmanager:eu-north-1:111111111111:secret:onlineshop-test/credentials-example",
                }
            )
        )
        images = {
            name: f"111111111111.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-test-{name}@sha256:"
            + "1" * 64
            for name in ["auth", "items", "gateway", "frontend", "e2e"]
        }
        (self.directory / "images.json").write_text(json.dumps({"images": images}))
        with tarfile.open(self.state / "fixtures.tar", "w") as archive:
            for name in ["Auth/init-db/01-schema.sql", "Items/init-db/01-schema.sql"]:
                content = b"CREATE TABLE test (id integer);"
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
        fixtures = self.state / "fixtures.tar"
        (self.directory / "images.json").write_text(
            json.dumps(
                {
                    "images": images,
                    "fixtures": {
                        "file": "fixtures.tar",
                        "size": fixtures.stat().st_size,
                        "sha256": hashlib.sha256(fixtures.read_bytes()).hexdigest(),
                    },
                }
            )
        )
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        for command in ["aws", "docker", "iptables", "ip6tables"]:
            path = self.bin / command
            path.write_text("""#!/usr/bin/env python3
import io,json,os,pathlib,resource,sys,tarfile
root=pathlib.Path(os.environ['FAKE_RUNTIME']);command=pathlib.Path(sys.argv[0]).name;args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as f: f.write(json.dumps({'command':command,'args':args,'file_limit':resource.getrlimit(resource.RLIMIT_FSIZE)[0]})+'\\n')
if command in ['iptables','ip6tables']:
 if 'FORWARD' in args and os.environ.get('FIREWALL_FORWARD_MISSING')=='1':sys.exit(1)
 sys.exit(int(os.environ.get('FIREWALL_FAILURE','0')))
elif command=='aws':
 if args[:2]==['sts','get-caller-identity']: print(json.dumps({'Account':'111111111111'}))
 elif args[:2]==['secretsmanager','get-secret-value']:
  print(json.dumps({'SecretString':json.dumps({'auth_db_password':'auth-secret-canary','items_db_password':'items-secret-canary','e2e_password':'e2e-secret-canary'})}))
 elif args[:2]==['ecr','get-login-password']: print('ecr-secret-canary')
 else: sys.exit(1)
elif 'inspect' in args: sys.exit(1)
elif 'ps' in args and any('name=' in value for value in args):
 if os.environ.get('DETACHED_TEST'):print('detached-test-container')
 sys.exit(1 if os.environ.get('ABSENCE_UNKNOWN') or (os.environ.get('CLEANUP_UNKNOWN') and (root/'test-started').exists()) else 0)
elif 'up' in args and '--wait' in args: sys.exit(int(os.environ.get('READINESS_FAILURE','0')))
elif 'run' in args:
 (root/'test-started').touch()
 sys.exit(0 if '-d' in args else int(os.environ.get('E2E_FAIL','0')))
elif 'exec' in args and './mvnw' in args: sys.exit(int(os.environ.get('E2E_FAIL','0')))
elif 'exec' in args and 'tar' in args and os.environ.get('REPORTS_VALID')=='1':
 with tarfile.open(fileobj=sys.stdout.buffer,mode='w|') as archive:
  for name,count in [('ItemsE2ETest',3),('RestAssuredLoggingTest',1)]:
   cases=''.join('<testcase name="test'+str(i)+'" classname="com.onlineshop.e2e.'+name+'" />' for i in range(count))
   failed=os.environ.get('REPORTS_FAIL')=='1' and name=='ItemsE2ETest'
   if failed: cases=cases.replace(' />','><failure message="auth-secret-canary">auth-secret-canary</failure></testcase>',1)
   content=('<testsuite name="com.onlineshop.e2e.'+name+'" tests="'+str(count)+'" failures="'+('1' if failed else '0')+'" errors="0" skipped="0">'+cases+'</testsuite>').encode()
   member=tarfile.TarInfo('surefire-reports/TEST-com.onlineshop.e2e.'+name+'.xml');member.size=len(content);archive.addfile(member,io.BytesIO(content))
else: sys.exit(0)
""")
            path.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + ":" + os.environ["PATH"],
            "FAKE_RUNTIME": str(self.directory),
        }

    def invoke(self, generation=GENERATION, *, reconcile=False):
        result = subprocess.run(
            [
                sys.executable,
                str(self.runtime / "run-stack.py"),
                "--generation",
                generation,
                *(
                    ["--reconcile"]
                    if reconcile
                    else ["--images", str(self.directory / "images.json")]
                ),
            ],
            env=self.env,
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        for value in [
            "auth-secret-canary",
            "items-secret-canary",
            "e2e-secret-canary",
            "ecr-secret-canary",
        ]:
            self.assertNotIn(value, result.stdout + result.stderr)
        if result.returncode:
            self.assertIn("Runtime failed:", result.stderr)
        path = self.directory / "calls.jsonl"
        calls = (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )
        return result, calls

    def test_stale_generation_cannot_reset_or_read_credentials(self):
        result, calls = self.invoke("run-199-attempt-1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_unknown_previous_operation_blocks_reset(self):
        (self.state / "operation.json").write_text(
            json.dumps({"generation": "run-199-attempt-1", "status": "running"})
        )
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_active_host_process_lock_blocks_another_generation(self):
        with (self.state / "host.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_detached_test_blocks_credentials_and_reset_even_with_terminal_local_record(
        self,
    ):
        self.env.update(DETACHED_TEST="1", REPORTS_VALID="1")
        (self.state / "operation.json").write_text(
            json.dumps({"generation": GENERATION, "status": "failed"})
        )
        original = (self.state / "operation.json").read_bytes()
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["command"] == "aws" for call in calls))
        self.assertFalse(
            any("down" in call["args"] or "rm" in call["args"] for call in calls)
        )
        self.assertEqual((self.state / "operation.json").read_bytes(), original)
        self.assertFalse((self.state / "run.env").exists())

    def test_failed_detached_test_lookup_cannot_be_treated_as_absence(self):
        self.env["ABSENCE_UNKNOWN"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call["command"] == "aws" for call in calls))
        self.assertFalse((self.state / "operation.json").exists())

    def test_recovery_observation_proves_idle_host_without_clearing_unknown_outcome(
        self,
    ):
        record = json.dumps({"generation": GENERATION, "status": "unknown"})
        (self.state / "operation.json").write_text(record)
        result, calls = self.invoke(reconcile=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["generation"], GENERATION)
        self.assertEqual(report["previous_status"], "unknown")
        self.assertTrue(report["host_lock_acquired"])
        self.assertTrue(report["test_container_absent"])
        self.assertFalse(report["automatic_retry_authorized"])
        self.assertFalse(report["aws_validation_success"])
        self.assertEqual((self.state / "operation.json").read_text(), record)
        self.assertTrue(
            all(call["command"] == "docker" and "ps" in call["args"] for call in calls)
        )

    def test_recovery_observation_refuses_active_host_lock_without_external_calls(self):
        with (self.state / "host.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result, calls = self.invoke(reconcile=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_recovery_observation_refuses_detached_test_or_inconclusive_lookup(self):
        for variable in ["DETACHED_TEST", "ABSENCE_UNKNOWN"]:
            with self.subTest(variable=variable):
                self.env[variable] = "1"
                result, calls = self.invoke(reconcile=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertFalse(any(call["command"] == "aws" for call in calls))
                self.env.pop(variable)

    def test_recovery_observation_refuses_foreign_or_malformed_previous_operation(self):
        for record in [
            {"generation": "run-199-attempt-1", "status": "unknown"},
            {"generation": GENERATION, "status": "unrecognized"},
        ]:
            with self.subTest(record=record):
                (self.state / "operation.json").write_text(json.dumps(record))
                result, calls = self.invoke(reconcile=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, [])

    def test_mutable_or_other_account_image_cannot_reach_docker(self):
        path = self.directory / "images.json"
        manifest = json.loads(path.read_text())
        manifest["images"]["auth"] = "other-registry/auth:latest"
        path.write_text(json.dumps(manifest))
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_failed_readiness_stops_before_e2e_and_removes_temporary_credentials(self):
        self.env["READINESS_FAILURE"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        starts = [
            call
            for call in calls
            if call["command"] == "docker" and "up" in call["args"]
        ]
        self.assertEqual(len(starts), 1)
        self.assertIn("--wait", starts[0]["args"])
        self.assertFalse(
            any(call["command"] == "docker" and "run" in call["args"] for call in calls)
        )
        self.assertFalse((self.state / "run.env").exists())
        operation = json.loads((self.state / "operation.json").read_text())
        self.assertEqual(operation["status"], "failed")

    def test_zero_exit_without_reports_cannot_claim_runtime_success(self):
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(
            any(call["command"] == "docker" and "run" in call["args"] for call in calls)
        )
        self.assertFalse((self.state / "run.env").exists())
        operation = json.loads((self.state / "operation.json").read_text())
        self.assertEqual(operation["status"], "failed")

    def test_valid_executed_reports_record_success_after_scoped_reset_without_credentials_in_commands(
        self,
    ):
        self.env["REPORTS_VALID"] = "1"
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [call["args"] for call in calls if call["command"] == "docker"]
        reset = next(index for index, args in enumerate(commands) if "down" in args)
        start = next(index for index, args in enumerate(commands) if "up" in args)
        test = next(index for index, args in enumerate(commands) if "run" in args)
        self.assertLess(reset, start)
        self.assertLess(start, test)
        self.assertIn("--volumes", commands[reset])
        self.assertTrue(
            all("system" not in args and "prune" not in args for args in commands)
        )
        self.assertIn("--cap-drop=ALL", commands[test])
        self.assertIn("--security-opt=no-new-privileges:true", commands[test])
        self.assertFalse(
            any("host" in args or "--privileged" in args for args in commands)
        )
        for value in ["auth-secret-canary", "items-secret-canary", "e2e-secret-canary"]:
            self.assertNotIn(value, json.dumps(commands))
        self.assertFalse((self.state / "run.env").exists())
        operation = json.loads((self.state / "operation.json").read_text())
        self.assertEqual(operation["status"], "passed")

    def test_missing_metadata_block_rules_stop_before_credentials_or_reset(self):
        self.env["FIREWALL_FAILURE"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(calls)
        self.assertTrue(
            all(call["command"] in ["iptables", "ip6tables"] for call in calls)
        )

    def test_unverifiable_test_termination_blocks_next_run_and_still_removes_credentials(
        self,
    ):
        self.env.update(REPORTS_VALID="1", CLEANUP_UNKNOWN="1")
        result, _ = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("runtime passed", result.stdout)
        self.assertFalse((self.state / "run.env").exists())
        self.assertFalse((self.state / "e2e.env").exists())
        operation = json.loads((self.state / "operation.json").read_text())
        self.assertEqual(operation["status"], "unknown")
        (self.directory / "calls.jsonl").unlink()
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_failed_tests_retain_only_sanitized_reports_before_removing_test_container(
        self,
    ):
        self.env.update(REPORTS_VALID="1", REPORTS_FAIL="1", E2E_FAIL="1")
        result, _ = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        reports = list((self.state / "reports" / GENERATION).glob("*.xml"))
        self.assertEqual(len(reports), 2)
        contents = "".join(path.read_text() for path in reports)
        self.assertNotIn("auth-secret-canary", contents)
        self.assertIn('failures="1"', contents)
        self.assertFalse((self.state / "run.env").exists())

    def test_command_failure_cannot_pass_even_if_reports_claim_success(self):
        self.env.update(REPORTS_VALID="1", E2E_FAIL="1")
        result, _ = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            len(list((self.state / "reports" / GENERATION).glob("*.xml"))), 2
        )

    def test_new_fixture_set_does_not_execute_removed_previous_candidate_scripts(self):
        old = self.state / "fixtures/Auth/init-db/99-old.sql"
        old.parent.mkdir(parents=True)
        old.write_text("CREATE TABLE previous_candidate (id integer);")
        self.env["REPORTS_VALID"] = "1"
        result, _ = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(old.exists())

    def test_partial_secret_file_creation_cleans_the_first_file(self):
        (self.state / "e2e.env").write_text("leftover data")
        result, _ = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.state / "run.env").exists())

    def test_changed_fixture_transport_is_rejected_before_credentials_or_reset(self):
        with (self.state / "fixtures.tar").open("ab") as stream:
            stream.write(b"transport changed")
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])

    def test_unattached_metadata_chain_cannot_start_candidate_containers(self):
        self.env["FIREWALL_FORWARD_MISSING"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(
            all(call["command"] in ["iptables", "ip6tables"] for call in calls)
        )

    def test_external_output_has_a_kernel_enforced_bound_not_just_a_post_read_check(
        self,
    ):
        self.env["REPORTS_VALID"] = "1"
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(calls)
        self.assertTrue(all(call["file_limit"] == 8 * 1024**2 for call in calls))

    def test_test_image_has_readonly_root_and_bounded_tmpfs_until_reports_are_copied(
        self,
    ):
        self.env["REPORTS_VALID"] = "1"
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = [call["args"] for call in calls if call["command"] == "docker"]
        runner = next(args for args in commands if "run" in args)
        self.assertIn("--read-only", runner)
        self.assertIn("-d", runner)
        self.assertIn(
            "/workspace/e2e-tests/.build:rw,size=512m,uid=10001,gid=10001,mode=0755",
            runner,
        )
        self.assertIn(
            "/home/tests/.m2/wrapper:rw,size=64m,uid=10001,gid=10001,mode=0755", runner
        )
        execution = next(index for index, args in enumerate(commands) if "exec" in args)
        reports = next(index for index, args in enumerate(commands) if "tar" in args)
        self.assertIn("./mvnw", commands[execution])
        self.assertIn("--offline", commands[execution])
        self.assertIn(
            "-De2e.build.directory=/workspace/e2e-tests/.build/target",
            commands[execution],
        )
        self.assertLess(execution, reports)


class ComposeBoundaryStories(unittest.TestCase):
    def test_aws_definition_has_only_private_digest_apps_and_independent_owned_data(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            envfile = Path(directory) / "run.env"
            envfile.write_text(
                "AUTH_DB_PASSWORD=<generated-auth-password>\nITEMS_DB_PASSWORD=<generated-items-password>\n"
            )
            env = {**os.environ, "GENERATION": GENERATION}
            for name in ["AUTH", "ITEMS", "GATEWAY", "FRONTEND", "E2E"]:
                env[name + "_IMAGE"] = (
                    "111111111111.dkr.ecr.eu-north-1.amazonaws.com/onlineshop-test-"
                    + name.lower()
                    + "@sha256:"
                    + "1" * 64
                )
            result = subprocess.run(
                [
                    "docker",
                    "compose",
                    "--project-name",
                    "onlineshop-test",
                    "--file",
                    str(RUNTIME / "compose.yml"),
                    "--env-file",
                    str(envfile),
                    "config",
                    "--format",
                    "json",
                ],
                env=env,
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            config = json.loads(result.stdout)
            services = config["services"]
            self.assertEqual(
                set(services),
                {
                    "auth-postgres",
                    "items-postgres",
                    "redis",
                    "kafka",
                    "auth-service",
                    "items-service",
                    "api-gateway",
                    "frontend",
                },
            )
            for name, service in services.items():
                self.assertNotIn("build", service)
                self.assertNotIn("privileged", service)
                self.assertNotIn("network_mode", service)
                self.assertEqual(service["labels"]["ManagedBy"], "onlineshop-test")
                self.assertIn("ALL", service["cap_drop"])
                self.assertTrue(
                    all(mount.startswith("/") for mount in service.get("tmpfs", [])),
                    name,
                )
                for mount in service.get("volumes", []):
                    self.assertNotIn("docker.sock", mount["target"])
                    if mount["type"] == "bind":
                        self.assertIn(name, ["auth-postgres", "items-postgres"])
                        self.assertTrue(mount["read_only"])
                        self.assertEqual(mount["target"], "/docker-entrypoint-initdb.d")
                if name in ["api-gateway", "frontend"]:
                    self.assertEqual(len(service["ports"]), 1)
                    self.assertEqual(service["ports"][0]["host_ip"], "127.0.0.1")
                else:
                    self.assertFalse(service.get("ports"))
            self.assertEqual(services["auth-postgres"]["image"], "postgres:18-alpine")
            self.assertEqual(services["kafka"]["image"], "apache/kafka:4.1.1")
            self.assertIn("max_connections=125", services["auth-postgres"]["command"])
            self.assertTrue(
                all(
                    "@sha256:" in services[name]["image"]
                    for name in [
                        "auth-service",
                        "items-service",
                        "api-gateway",
                        "frontend",
                    ]
                )
            )
            self.assertEqual(
                services["frontend"]["environment"]["VITE_API_URL"],
                "http://localhost:10000",
            )
            self.assertIn("auth-postgres-data", config["volumes"])
            self.assertIn("items-postgres-data", config["volumes"])


class HostFirewallStories(unittest.TestCase):
    def test_setup_prepends_both_metadata_blocks_without_blocking_host_credentials(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ["iptables", "ip6tables"]:
                path = root / name
                path.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['FIREWALL_TEST']);name=pathlib.Path(sys.argv[0]).name;args=sys.argv[1:]
with (root/'calls.jsonl').open('a') as stream:stream.write(json.dumps([name,*args])+'\\n')
sys.exit(1 if '-C' in args or '-S' in args else 0)
""")
                path.chmod(0o755)
            result = subprocess.run(
                ["bash", str(RUNTIME / "host-setup.sh"), "--firewall"],
                check=False,
                env={
                    **os.environ,
                    "PATH": str(root) + ":" + os.environ["PATH"],
                    "FIREWALL_TEST": str(root),
                },
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = [
                json.loads(line)
                for line in (root / "calls.jsonl").read_text().splitlines()
            ]
            for name, address in [
                ("iptables", "169.254.169.254/32"),
                ("ip6tables", "fd00:ec2::254/128"),
            ]:
                block = [
                    name,
                    "-w",
                    "10",
                    "-I",
                    "DOCKER-USER",
                    "1",
                    "-d",
                    address,
                    "-j",
                    "REJECT",
                ]
                forward = [name, "-w", "10", "-I", "FORWARD", "1", "-j", "DOCKER-USER"]
                self.assertIn(block, calls)
                self.assertIn(forward, calls)
                self.assertLess(calls.index(block), calls.index(forward))
            self.assertFalse(any("OUTPUT" in call or "-F" in call for call in calls))


if __name__ == "__main__":
    unittest.main()
