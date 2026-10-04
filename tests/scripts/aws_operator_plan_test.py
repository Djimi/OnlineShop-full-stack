"""Guarded current-environment planning stories at external process boundaries."""

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class OperatorPlanStories(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.env = {
            **os.environ,
            "PATH": str(self.path) + ":" + os.environ["PATH"],
            "RUNNER_TEMP": str(self.path),
            "GITHUB_REPOSITORY": "Djimi/OnlineShop-full-stack",
            "GITHUB_REF": "refs/heads/main",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_WORKFLOW_REF": "Djimi/OnlineShop-full-stack/.github/workflows/aws-operator-plan-proof.yml@refs/heads/main",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_ACTOR": "owner",
            "GITHUB_TRIGGERING_ACTOR": "owner",
            "GITHUB_RUN_ID": "200",
            "GITHUB_RUN_ATTEMPT": "1",
            "GH_TOKEN": "job-token-canary",
            "AWS_TESTING_ACCOUNT_ID": "111111111111",
            "AWS_TESTING_STATE_BUCKET": "owned-state",
        }
        resources = [
            ("aws_vpc", "main"),
            ("aws_subnet", "host"),
            ("aws_internet_gateway", "main"),
            ("aws_route_table", "host"),
            ("aws_route", "outbound"),
            ("aws_route_table_association", "host"),
            ("aws_security_group", "host"),
            ("aws_launch_template", "host"),
            ("aws_instance", "host"),
            ("aws_ec2_tag", "network_generation"),
        ]
        self.state = {
            "version": 4,
            "lineage": "test-lineage",
            "serial": 10,
            "resources": [
                {
                    "mode": "managed",
                    "type": kind,
                    "name": name,
                    "instances": [
                        {
                            "attributes": {
                                "id": "owned-id",
                                "tags": {"Generation": "run-100-attempt-1"},
                            }
                        }
                    ],
                }
                for kind, name in resources
            ],
        }
        (self.path / "state.json").write_text(json.dumps(self.state))
        (self.path / "pointer.json").write_text(
            json.dumps(
                {
                    "generation": "run-100-attempt-1",
                    "status": "provisioned",
                    "host_id": "owned-id",
                }
            )
        )
        programs = {
            "git": "if args[0]=='rev-parse':print('a'*40)\nelif os.environ.get('DIRTY_CONTROLLER'):print('changed')",
            "gh": "print(json.dumps({'permission':'read' if os.environ.get('DENIED_ACTOR') else 'admin'}))",
            "aws": """
if args[:2]==['sts','get-caller-identity']:
 print(json.dumps({'Account':'111111111111','Arn':'arn:aws:sts::111111111111:assumed-role/onlineshop-test-'+('publisher' if os.environ.get('WRONG_ROLE') else 'operator')+'/proof'}))
elif args[:2]==['s3api','head-object']:
 if os.environ.get('MISSING_STATE') and 'state/environment.tfstate' in args:sys.exit(1)
 file=root/('state.json' if 'state/environment.tfstate' in args else 'pointer.json');print(json.dumps({'ETag':'"snapshot"','VersionId':'v1','ContentLength':file.stat().st_size}))
elif args[:2]==['s3api','get-object']:
 file=root/('state.json' if 'state/environment.tfstate' in args else 'pointer.json');pathlib.Path(args[-1]).write_bytes(file.read_bytes());print(json.dumps({'ETag':'"snapshot"','VersionId':'v1'}))
else:sys.exit(1)
""",
            "terraform": """
if args[:2]==['show','-json']:
 resources=[{'address':r['type']+'.'+r['name']} for r in json.loads((root/'state.json').read_text())['resources']]
 print(json.dumps({'planned_values':{'root_module':{'resources':[] if os.environ.get('EMPTY_PLAN') else resources}},'resource_changes':[{'address':'aws_instance.host','change':{'actions':['delete','create']}}] if os.environ.get('CHANGES') else []}))
elif args[0] in ['init','plan']:print('raw-plan-secret-canary')
else:sys.exit(1)
""",
        }
        for name, body in programs.items():
            path = self.path / name
            path.write_text(
                "#!/usr/bin/env python3\nimport json,os,pathlib,sys\nargs=sys.argv[1:];root=pathlib.Path(os.environ['RUNNER_TEMP'])\nwith (root/'calls.jsonl').open('a') as out:out.write(json.dumps([pathlib.Path(sys.argv[0]).name,*args])+'\\n')\n"
                + body
            )
            path.chmod(0o755)

    def invoke(self, phase="plan"):
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts/aws-operator-plan-proof.py"),
                phase,
                "--output",
                str(self.path / "proof.json"),
            ],
            env=self.env,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        calls = (
            [
                json.loads(line)
                for line in (self.path / "calls.jsonl").read_text().splitlines()
            ]
            if (self.path / "calls.jsonl").exists()
            else []
        )
        self.assertNotIn("raw-plan-secret-canary", result.stdout + result.stderr)
        self.assertNotIn("job-token-canary", result.stdout + result.stderr)
        return result, calls

    def test_exact_existing_state_produces_no_change_plan_and_never_applies(self):
        result, calls = self.invoke()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.path / "proof.json").read_text())
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["aws_validation_success"])
        self.assertTrue(any(c[:2] == ["terraform", "plan"] for c in calls))
        self.assertFalse(any(c[:2] == ["terraform", "apply"] for c in calls))
        plan = next(c for c in calls if c[:2] == ["terraform", "plan"])
        self.assertNotIn("-lock=false", plan)

    def test_authorization_phase_uses_no_aws_or_terraform_credentials(self):
        result, calls = self.invoke("authorize")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(c[0] in ["aws", "terraform"] for c in calls))

    def test_unauthorized_rerun_actor_is_rejected_before_aws(self):
        self.env["GITHUB_TRIGGERING_ACTOR"] = "outsider"
        self.env["DENIED_ACTOR"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "aws" for c in calls))

    def test_missing_state_refuses_initialization_and_duplicate_creation(self):
        self.env["MISSING_STATE"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "terraform" for c in calls))

    def test_empty_state_refuses_initialization(self):
        self.state["resources"] = []
        (self.path / "state.json").write_text(json.dumps(self.state))
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "terraform" for c in calls))

    def test_wrong_assumed_role_refuses_state_reads(self):
        self.env["WRONG_ROLE"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ["aws", "s3api"] for c in calls))

    def test_generation_mismatch_refuses_terraform(self):
        (self.path / "pointer.json").write_text(
            json.dumps(
                {
                    "generation": "run-99-attempt-1",
                    "status": "provisioned",
                    "host_id": "owned-id",
                }
            )
        )
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "terraform" for c in calls))

    def test_unexpected_changes_fail_without_apply_or_raw_plan_publication(self):
        self.env["CHANGES"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[:2] == ["terraform", "apply"] for c in calls))
        self.assertFalse((self.path / "proof.json").exists())

    def test_empty_plan_cannot_claim_a_verified_existing_environment(self):
        self.env["EMPTY_PLAN"] = "1"
        result, _ = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.path / "proof.json").exists())

    def test_modified_trusted_inputs_are_rejected_before_aws_exchange(self):
        self.env["DIRTY_CONTROLLER"] = "1"
        result, calls = self.invoke()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(c[0] == "aws" for c in calls))
