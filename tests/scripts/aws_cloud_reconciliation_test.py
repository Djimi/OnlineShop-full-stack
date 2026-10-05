"""Cloud observation stories at AWS/GitHub process boundaries; no live calls."""

import json
import subprocess
import sys
import unittest

from tests.scripts import aws_validation_test as validation


class CloudObservationStories(unittest.TestCase):
    def setUp(self):
        validation.RequestStories.setUp(self)
        result, _ = validation.RequestStories.invoke(self)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.env.update(
            AWS_TESTING_ACCOUNT_ID="111111111111",
            AWS_TESTING_STATE_BUCKET="test-state-bucket",
        )
        self.pointer = {
            "generation": "run-100-attempt-1",
            "status": "provisioned",
            "host_id": "i-11111111111111111",
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
        self.state = {
            "version": 4,
            "lineage": "test-lineage",
            "serial": 1,
            "resources": [
                {
                    "type": address.split(".")[0],
                    "name": address.split(".")[1],
                    "mode": "managed",
                    "instances": [
                        {
                            "attributes": {
                                "id": self.pointer["host_id"]
                                if address == "aws_instance.host"
                                else "fixture-resource",
                                "tags": {"Generation": self.pointer["generation"]},
                            }
                        }
                    ],
                }
                for address in addresses
            ],
        }
        self.aws = {
            "account": "111111111111",
            "role": "onlineshop-test-operator",
            "state": self.state,
            "pointer": self.pointer,
            "host_status": "running",
            "ssm_status": "Success",
            "lock_exists": False,
        }
        path = self.bin / "aws"
        path.write_text("""#!/usr/bin/env python3
import json,os,pathlib,sys
root=pathlib.Path(os.environ['FAKE_API']);args=sys.argv[1:];fixture=json.loads((root/'cloud.json').read_text())
with (root/'aws-calls.jsonl').open('a') as output:output.write(json.dumps(args)+'\\n')
if fixture.get('flood'):sys.stdout.write('raw-secret-canary'*100000);sys.exit(0)
if fixture.get('lock_read_denied') and 'state/environment.tfstate.tflock' in args:print('AccessDenied: raw-secret-canary',file=sys.stderr);sys.exit(254)
if args[:2]==['sts','get-caller-identity']:print(json.dumps({'Account':fixture['account'],'Arn':'arn:aws:sts::'+fixture['account']+':assumed-role/'+fixture['role']+'/session'}))
elif args[:2]==['s3api','head-object']:
 key=args[args.index('--key')+1]
 if key.endswith('.tflock'):
  if fixture['lock_exists']:print(json.dumps({'ContentLength':10,'ETag':'"lock"','VersionId':'v1'}))
  else:print('An error occurred (404) when calling the HeadObject operation: Not Found',file=sys.stderr);sys.exit(254)
 else:
  data=fixture['pointer'] if key.startswith('operations/') else fixture['state'];print(json.dumps({'ContentLength':len(json.dumps(data).encode()),'ETag':'"snapshot"','VersionId':'v1'}))
elif args[:2]==['s3api','get-object']:
 key=args[args.index('--key')+1];target=args[args.index('--if-match')+2];data=fixture['pointer'] if key.startswith('operations/') else fixture['state'];pathlib.Path(target).write_text(json.dumps(data));print(json.dumps({'VersionId':'v1'}))
elif args[:2]==['ec2','describe-instances']:
 pointer=fixture['pointer'];host={'InstanceId':pointer['host_id'],'State':{'Name':fixture['host_status']},'Tags':[{'Key':k,'Value':v} for k,v in {'ManagedBy':'onlineshop-test','Repository':'Djimi/OnlineShop-full-stack','Generation':pointer['generation']}.items()]};print(json.dumps({'Reservations':[{'Instances':[host]}]}))
elif args[:2]==['ssm','list-commands']:
 response={'Commands':[{'CommandId':'00000000-0000-0000-0000-000000000001','Status':fixture['ssm_status']}]}
 if fixture.get('second_page') and '--next-token' not in args:response['NextToken']='second'
 elif fixture.get('second_page'):response['Commands'][0]['Status']='InProgress'
 print(json.dumps(response))
elif args[:2]==['ssm','get-command-invocation']:print(json.dumps({'Status':fixture.get('invocation_status',fixture['ssm_status']),'ResponseCode':0}))
else:print('raw-secret-canary',file=sys.stderr);sys.exit(99)
""")
        path.chmod(0o755)

    def invoke_cloud(self):
        (self.directory / "cloud.json").write_text(json.dumps(self.aws))
        result = subprocess.run(
            [
                sys.executable,
                str(validation.SCRIPT),
                "reconcile-cloud",
                "--request",
                str(self.directory / "request.json"),
                "--output",
                str(self.directory / "cloud-observation.json"),
            ],
            env=self.env,
            capture_output=True,
            text=True,
            check=False,
            timeout=20,
        )
        self.assertNotIn("raw-secret-canary", result.stdout + result.stderr)
        path = self.directory / "aws-calls.jsonl"
        calls = (
            [json.loads(line) for line in path.read_text().splitlines()]
            if path.exists()
            else []
        )
        return result, calls

    def test_terminal_cloud_observation_never_authorizes_mutation_without_host_evidence(
        self,
    ):
        result, calls = self.invoke_cloud()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads((self.directory / "cloud-observation.json").read_text())
        self.assertTrue(report["state_verified"])
        self.assertTrue(report["ssm_commands_terminal"])
        self.assertTrue(report["host_observation_required"])
        self.assertFalse(report["mutation_authorized"])
        self.assertFalse(report["aws_validation_success"])
        self.assertEqual(report["candidate_sha"], "c" * 40)
        self.assertEqual(report["validation_run_id"], 200)
        self.assertTrue(
            all(
                call[1]
                in {
                    "get-caller-identity",
                    "head-object",
                    "get-object",
                    "describe-instances",
                    "list-commands",
                    "get-command-invocation",
                }
                for call in calls
            )
        )

    def test_wrong_role_active_lock_host_transition_and_active_ssm_refuse(self):
        for key, value in [
            ("role", "onlineshop-test-publisher"),
            ("lock_exists", True),
            ("host_status", "stopping"),
            ("ssm_status", "Cancelling"),
        ]:
            with self.subTest(key=key):
                original = self.aws[key]
                self.aws[key] = value
                result, _ = self.invoke_cloud()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.directory / "cloud-observation.json").exists())
                self.aws[key] = original

    def test_active_second_ssm_page_and_nonterminal_invocation_cannot_be_ignored(self):
        for key, value in [("second_page", True), ("invocation_status", "InProgress")]:
            with self.subTest(key=key):
                self.aws[key] = value
                result, _ = self.invoke_cloud()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.directory / "cloud-observation.json").exists())
                self.aws.pop(key)

    def test_empty_state_and_generation_mismatch_refuse_before_host_discovery(self):
        for state in [{**self.state, "resources": []}, {**self.state, "version": 3}]:
            with self.subTest(state=state["version"]):
                self.aws["state"] = state
                result, calls = self.invoke_cloud()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(any(call[0] in {"ec2", "ssm"} for call in calls))
        self.aws["state"] = self.state
        self.pointer["generation"] = "run-99-attempt-1"
        result, calls = self.invoke_cloud()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(call[0] in {"ec2", "ssm"} for call in calls))

    def test_denied_lock_lookup_is_not_absence_and_excessive_output_remains_private(
        self,
    ):
        for key in ["lock_read_denied", "flood"]:
            with self.subTest(key=key):
                self.aws[key] = True
                result, _ = self.invoke_cloud()
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse((self.directory / "cloud-observation.json").exists())
                self.aws.pop(key)
