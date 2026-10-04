"""Actual-role proof command stories at the AWS CLI boundary."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class RoleBoundaryStories(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        command = self.root / "aws"
        command.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['PROOF_TEST']);args=sys.argv[1:];api=args[:2]
with (root/'calls.jsonl').open('a') as output:output.write(json.dumps(args)+'\\n')
if api==['sts','get-caller-identity']:
 print(json.dumps({'Account':os.environ.get('PROOF_ACCOUNT','111111111111'),'Arn':'arn:aws:sts::111111111111:assumed-role/onlineshop-test-'+os.environ['PROOF_ROLE']+'/proof'}))
elif os.environ.get('PROOF_WRONG_SUCCESS')=='1':print('secret-canary')
elif api==['ecr','describe-images'] or (os.environ['PROOF_ROLE']=='operator' and (api in [['ec2','describe-instances'],['iam','get-instance-profile']] or (api==['s3api','list-objects-v2'] and 'state/environment.tfstate' in args))):print('{}')
else:
 print('An error occurred ('+os.environ.get('PROOF_ERROR','AccessDenied')+') when calling operation: secret-canary',file=sys.stderr)
 sys.exit(254)
""")
        command.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": str(self.root) + ":" + os.environ["PATH"],
            "PROOF_TEST": str(self.root),
            "AWS_TESTING_ACCOUNT_ID": "111111111111",
            "AWS_TESTING_STATE_BUCKET": "approved-test-state",
            "AWS_TESTING_SECRET_ARN": "arn:aws:secretsmanager:eu-north-1:111111111111:secret:onlineshop-test/credentials-example",
            "GITHUB_REPOSITORY": "Djimi/OnlineShop-full-stack",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_WORKFLOW_REF": "Djimi/OnlineShop-full-stack/.github/workflows/aws-boundary-proof.yml@refs/heads/main",
        }

    def invoke(self, role="operator"):
        self.env["PROOF_ROLE"] = role
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts/aws-boundary-proof.py"),
                "--role",
                role,
                "--output",
                str(self.root / "proof.json"),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        self.assertNotIn("secret-canary", result.stdout + result.stderr)
        if result.returncode:
            self.assertIn("Boundary proof failed:", result.stderr)
        calls = self.root / "calls.jsonl"
        return result, [
            json.loads(line) for line in calls.read_text().splitlines()
        ] if calls.exists() else []

    def test_operator_proves_approved_discovery_and_denied_protected_reads(self):
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads((self.root / "proof.json").read_text())
        self.assertEqual(evidence["role"], "operator")
        self.assertEqual(evidence["status"], "passed")
        self.assertIn("bootstrap-state-read", evidence["denied"])
        self.assertIn("environment-state-list", evidence["allowed"])
        self.assertFalse(
            any(
                api in call
                for call in calls
                for api in [
                    "delete-object",
                    "delete-role",
                    "terminate-instances",
                    "put-object",
                ]
            )
        )

    def test_publisher_never_needs_host_or_secret_permissions(self):
        result, _ = self.invoke("publisher")
        self.assertEqual(result.returncode, 0, result.stderr)
        evidence = json.loads((self.root / "proof.json").read_text())
        self.assertIn("host-discovery", evidence["denied"])
        self.assertIn("dedicated-secret-read", evidence["denied"])

    def test_unexpected_success_is_not_accepted_as_a_denial(self):
        self.env["PROOF_WRONG_SUCCESS"] = "1"
        result, _ = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "proof.json").exists())

    def test_network_or_missing_resource_error_is_not_permission_proof(self):
        self.env["PROOF_ERROR"] = "NoSuchKey"
        result, _ = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.root / "proof.json").exists())

    def test_wrong_account_stops_before_any_boundary_api(self):
        self.env["PROOF_ACCOUNT"] = "222222222222"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 1)

    def test_nonmain_controller_cannot_use_role_proof_command(self):
        self.env["GITHUB_REF"] = "refs/heads/feature"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
