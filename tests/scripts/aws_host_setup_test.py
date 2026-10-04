"""Host setup stories exercise AWS process boundaries, never a real host."""

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
elif args[:2]==['ssm','list-commands']:result={'Commands':[{'Status':'InProgress'}] if os.environ.get('ACTIVE') else []}
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

    def invoke(self):
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts/aws-host-setup.py"),
                "--identifiers",
                str(self.identifiers),
                "--output",
                str(self.path / "operation.json"),
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
