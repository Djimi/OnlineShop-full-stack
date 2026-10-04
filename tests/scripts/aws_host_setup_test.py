"""Host setup stories exercise AWS process boundaries, never a real host."""

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class HostSetupStories(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.identifiers = self.path / "identifiers.json"
        self.identifiers.write_text(
            json.dumps(
                {
                    "account_id": "111111111111",
                    "instance_id": "i-0123456789abcdef0",
                    "generation": "run-200-attempt-1",
                    "state_bucket": "test-state",
                    "secret_arn": "arn:aws:secretsmanager:eu-north-1:111111111111:secret:test",
                    "host_profile": "onlineshop-test-host",
                }
            )
        )
        self.aws = self.path / "aws"
        self.aws.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
args=sys.argv[1:]; root=pathlib.Path(os.environ['FAKE_ROOT'])
with (root/'calls.jsonl').open('a') as stream:stream.write(json.dumps(args)+'\\n')
if args[:2]==['sts','get-caller-identity']: result={'Account':'111111111111'}
elif args[:2]==['ec2','describe-instances']:
 result={'Reservations':[{'Instances':[{'InstanceId':'i-0123456789abcdef0','State':{'Name':'running'},'InstanceType':'m7i-flex.large','ImageId':'ami-04478a3e21a0d79a7','IamInstanceProfile':{'Arn':'arn:aws:iam::111111111111:instance-profile/onlineshop-test-host'},'Tags':[{'Key':k,'Value':v} for k,v in {'ManagedBy':'onlineshop-test','Repository':'Djimi/OnlineShop-full-stack','Generation':os.environ.get('HOST_GENERATION','run-200-attempt-1')}.items()]}]}]}
elif args[:2]==['ssm','describe-instance-information']:result={'InstanceInformationList':[{'InstanceId':'i-0123456789abcdef0','PingStatus':'Online'}]}
elif args[:2]==['ssm','list-commands']:
 result={'Commands':json.loads((root/'remote-commands.json').read_text()) if (root/'remote-commands.json').exists() else ([{'Status':'InProgress'}] if os.environ.get('ACTIVE') else [])}
elif args[:2]==['s3api','get-object']:
 pathlib.Path(args[args.index('--region')-1]).write_text((root/'cloud-record.json').read_text());result={'ETag':'"cloud-current"','VersionId':'version-1'}
elif args[:2]==['s3api','head-object']:
 result={'ContentLength':1048577 if os.environ.get('EXCESSIVE_CLOUD_RECORD') else (root/'cloud-record.json').stat().st_size,'ETag':'"cloud-current"','VersionId':'version-1'}
elif args[:2]==['ssm','send-command']: result={'Command':{'CommandId':'00000000-0000-0000-0000-000000000001'}}
elif args[:2]==['ssm','get-command-invocation']:
 if os.environ.get('DELAYED_INVOCATION') and not (root/'invocation-visible').exists():
  (root/'invocation-visible').touch();print('An error occurred (InvocationDoesNotExist)',file=sys.stderr);sys.exit(1)
 result={'Status':os.environ.get('REMOTE_STATUS','Success'),'ResponseCode':0,'StandardOutputContent':'candidate-password=DO-NOT-PRINT'}
elif args[:2]==['s3api','put-object']:
 if os.environ.get('EXISTING_RECORD'):
  print('An error occurred (PreconditionFailed)',file=sys.stderr);sys.exit(1)
 result={'ETag':'"example"'}
else:sys.exit(1)
print(json.dumps(result))
""")
        self.aws.chmod(0o755)
        git = self.path / "git"
        git.write_text(
            '#!/bin/sh\nif [ -n "${MODIFIED_TRUSTED_FILES:-}" ]; then echo changed; fi\n'
        )
        git.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.path) + ":" + os.environ["PATH"],
            "FAKE_ROOT": str(self.path),
        }

    def invoke(self, *, reconcile=False):
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts/aws-host-setup.py"),
                "--identifiers",
                str(self.identifiers),
                "--output",
                str(self.path / "operation.json"),
                *(["--reconcile"] if reconcile else []),
            ],
            cwd=ROOT,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        calls = (
            [
                json.loads(line)
                for line in (self.path / "calls.jsonl").read_text().splitlines()
            ]
            if (self.path / "calls.jsonl").exists()
            else []
        )
        return result, calls

    def test_owned_online_host_receives_only_hashed_trusted_files_and_bounded_setup(
        self,
    ):
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        sends = [call for call in calls if call[:2] == ["ssm", "send-command"]]
        self.assertTrue(sends)
        self.assertTrue(
            all(
                "executionTimeout" in call[call.index("--parameters") + 1]
                for call in sends
            )
        )
        self.assertTrue(
            all(len(call[call.index("--parameters") + 1]) < 24000 for call in sends)
        )
        self.assertIn("sha256", str(sends))
        self.assertIn("--setup", str(sends))
        self.assertNotIn("DO-NOT-PRINT", result.stdout + result.stderr)
        self.assertEqual(
            json.loads((self.path / "operation.json").read_text())["status"], "success"
        )

    def test_foreign_generation_is_rejected_before_remote_mutation(self):
        self.env["HOST_GENERATION"] = "run-100-attempt-1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[:2] == ["ssm", "send-command"] for call in calls))

    def test_active_remote_operation_blocks_setup(self):
        self.env["ACTIVE"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[:2] == ["ssm", "send-command"] for call in calls))

    def test_locally_modified_host_files_cannot_be_sent_to_credential_bearing_host(
        self,
    ):
        self.env["MODIFIED_TRUSTED_FILES"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[:2] == ["ssm", "send-command"] for call in calls))

    def test_new_command_visibility_delay_is_polled_without_resending(self):
        self.env["DELAYED_INVOCATION"] = "1"
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        sends = sum(call[:2] == ["ssm", "send-command"] for call in calls)
        polls = sum(call[:2] == ["ssm", "get-command-invocation"] for call in calls)
        self.assertEqual(polls, sends + 1)

    def test_existing_cloud_intent_cannot_be_overwritten_using_another_local_path(self):
        self.env["EXISTING_RECORD"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[:2] == ["ssm", "send-command"] for call in calls))
        writes = [call for call in calls if call[:2] == ["s3api", "put-object"]]
        self.assertTrue(writes)
        self.assertTrue(all("--if-none-match" in call for call in writes))

    def test_unknown_remote_outcome_is_retained_and_never_retried_automatically(self):
        self.env["REMOTE_STATUS"] = "Cancelled"
        result, _ = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(
            json.loads((self.path / "operation.json").read_text())["status"], "unknown"
        )
        _, calls = self.invoke()
        self.assertEqual(sum(call[:2] == ["ssm", "send-command"] for call in calls), 1)

    def recovery_fixture(self, *, matches=1, status="Success", known_id=False):
        command = "set -eu; true"
        entry = {
            "stage": 0,
            "status": "launching",
            "sha256": hashlib.sha256(command.encode()).hexdigest(),
        }
        if known_id:
            entry["command_id"] = "00000000-0000-0000-0000-000000000001"
        record = {
            "generation": "run-200-attempt-1",
            "host_id": "i-0123456789abcdef0",
            "purpose": "owner-host-setup-proof",
            "status": "unknown",
            "commands": [entry],
            "aws_validation_success": False,
        }
        (self.path / "cloud-record.json").write_text(json.dumps(record))
        remote = [
            {
                "CommandId": f"00000000-0000-0000-0000-{number:012d}",
                "Comment": "run-200-attempt-1-setup-0",
                "DocumentName": "AWS-RunShellScript",
                "InstanceIds": ["i-0123456789abcdef0"],
                "Status": status,
                "Parameters": {"commands": [command], "executionTimeout": ["930"]},
            }
            for number in range(1, matches + 1)
        ]
        (self.path / "remote-commands.json").write_text(json.dumps(remote))

    def test_excessive_cloud_record_is_refused_before_download(self):
        self.recovery_fixture()
        self.env["EXCESSIVE_CLOUD_RECORD"] = "1"
        result, calls = self.invoke(reconcile=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[:2] == ["s3api", "get-object"] for call in calls))
        self.assertFalse((self.path / "operation.json").exists())

    def test_reconciliation_discovers_terminal_command_without_relaunching_or_synthesizing_success(
        self,
    ):
        self.recovery_fixture()
        result, calls = self.invoke(reconcile=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.path / "operation.json").read_text())
        self.assertEqual(report["status"], "reconciled")
        self.assertEqual(
            report["commands"][0]["command_id"], "00000000-0000-0000-0000-000000000001"
        )
        self.assertFalse(report["aws_validation_success"])
        self.assertFalse(report["automatic_retry_authorized"])
        self.assertFalse(
            any(
                call[:2]
                in (
                    ["ssm", "send-command"],
                    ["ssm", "cancel-command"],
                    ["s3api", "put-object"],
                )
                for call in calls
            )
        )
        self.assertNotIn(
            "DO-NOT-PRINT", result.stdout + result.stderr + json.dumps(report)
        )

    def test_absent_discovered_command_is_unknown_not_safe_to_retry(self):
        self.recovery_fixture(matches=0)
        result, calls = self.invoke(reconcile=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.path / "operation.json").exists())
        self.assertFalse(any(call[:2] == ["ssm", "send-command"] for call in calls))

    def test_ambiguous_discovery_blocks_even_when_both_commands_are_terminal(self):
        self.recovery_fixture(matches=2)
        result, _ = self.invoke(reconcile=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.path / "operation.json").exists())

    def test_active_or_cancelling_remote_operation_is_not_reconciled(self):
        for status in ["InProgress", "Cancelling"]:
            with self.subTest(status=status):
                self.recovery_fixture(status=status)
                result, _ = self.invoke(reconcile=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.path / "operation.json").exists())

    def test_recorded_id_still_requires_exact_command_body_identity(self):
        self.recovery_fixture(known_id=True)
        path = self.path / "remote-commands.json"
        commands = json.loads(path.read_text())
        commands[0]["Parameters"]["commands"] = ["echo foreign-command"]
        path.write_text(json.dumps(commands))
        result, _ = self.invoke(reconcile=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.path / "operation.json").exists())

    def test_foreign_cloud_record_is_rejected_without_a_reconciliation_report(self):
        self.recovery_fixture()
        path = self.path / "cloud-record.json"
        record = json.loads(path.read_text())
        record["generation"] = "run-100-attempt-1"
        path.write_text(json.dumps(record))
        result, _ = self.invoke(reconcile=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.path / "operation.json").exists())

    def test_failed_terminal_invocation_is_reported_as_failed_not_setup_success(self):
        self.recovery_fixture(status="Failed")
        self.env["REMOTE_STATUS"] = "Failed"
        result, _ = self.invoke(reconcile=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.path / "operation.json").read_text())
        self.assertEqual(report["commands"][0]["status"], "Failed")
        self.assertEqual(report["status"], "reconciled")
        self.assertFalse(report["automatic_retry_authorized"])

    def test_discovered_invocation_pending_visibility_is_not_terminal_evidence(self):
        self.recovery_fixture()
        self.env["DELAYED_INVOCATION"] = "1"
        result, _ = self.invoke(reconcile=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.path / "operation.json").exists())

    def test_duplicate_cloud_identity_fields_cannot_replace_the_verified_generation(
        self,
    ):
        self.recovery_fixture()
        path = self.path / "cloud-record.json"
        text = path.read_text()
        path.write_text('{"generation":"run-100-attempt-1",' + text[1:])
        result, _ = self.invoke(reconcile=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.path / "operation.json").exists())
