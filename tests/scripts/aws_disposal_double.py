"""Durable AWS/Terraform process double for disposal lifecycle stories."""

import hashlib
import json
import os
import sys
from pathlib import Path

root = Path(os.environ["FAKE_API"])
path = root / "disposal-cloud.json"
cloud = json.loads(path.read_text())
tool, *args = sys.argv[1:]


def save():
    path.write_text(json.dumps(cloud))


def emit(value):
    print(json.dumps(value))


def value(flag):
    return args[args.index(flag) + 1]


def object_bytes(key):
    if key == "operations/current-generation.json":
        return json.dumps(cloud["pointer"], sort_keys=True).encode()
    if key == "state/environment.tfstate":
        return json.dumps(cloud["state"], sort_keys=True).encode()
    return __import__("base64").b64decode(cloud["objects"][key])


if tool == "terraform":
    cloud["terraform_calls"].append(args)
    save()
    if args[0] == "init":
        sys.exit(0)
    if args[0] == "plan":
        cloud["destroy_plan_count"] = cloud.get("destroy_plan_count", 0) + 1
        Path("destroy.tfplan").write_text(
            "saved-destroy-plan-" + str(cloud["destroy_plan_count"])
        )
        save()
        sys.exit(0)
    if args[0] == "show":
        emit(cloud["destroy_plan"])
        sys.exit(0)
    if args[0] == "apply":
        if cloud.get("stale_plan"):
            print("saved plan is stale", file=sys.stderr)
            sys.exit(1)
        if cloud.pop("apply_failure", False):
            save()
            print("simulated provider failure before state change", file=sys.stderr)
            sys.exit(1)
        if cloud.pop("empty_destroy_failure", False):
            cloud["state"]["resources"] = []
            cloud["state"]["outputs"] = {}
            cloud["state"]["serial"] += 1
            cloud["live_resources"] = {}
            save()
            print("simulated provider failure after complete deletion", file=sys.stderr)
            sys.exit(1)
        if cloud.pop("partial_destroy_failure", False):
            cloud["state"]["resources"] = [
                item
                for item in cloud["state"]["resources"]
                if item["type"] not in {"aws_instance", "aws_ec2_tag"}
            ]
            cloud["state"]["serial"] += 1
            cloud["live_resources"] = {
                "aws_vpc.main": True,
                "aws_subnet.host": True,
                "aws_internet_gateway.main": True,
                "aws_route_table.host": True,
                "aws_security_group.host": True,
                "aws_launch_template.host": True,
            }
            save()
            print(
                "simulated cancellation after partial provider deletion",
                file=sys.stderr,
            )
            sys.exit(1)
        cloud["state"]["resources"] = []
        cloud["state"]["outputs"] = {}
        cloud["state"]["serial"] += 1
        cloud["live_resources"] = {}
        save()
        sys.exit(0)
    sys.exit(99)

if tool != "aws":
    sys.exit(99)

cloud["aws_calls"].append(args)
save()
operation = args[:2]
if operation == ["sts", "get-caller-identity"]:
    emit(
        {
            "Account": "111111111111",
            "Arn": "arn:aws:sts::111111111111:assumed-role/onlineshop-test-operator/session",
        }
    )
elif operation == ["s3api", "list-objects-v2"]:
    prefix = value("--prefix")
    keys = sorted(key for key in cloud["objects"] if key.startswith(prefix))
    if cloud.get("native_lock") and "state/environment.tfstate.tflock".startswith(
        prefix
    ):
        keys.append("state/environment.tfstate.tflock")
    emit(
        {
            "IsTruncated": False,
            "KeyCount": len(keys),
            "Contents": [{"Key": key} for key in keys],
        }
    )
elif operation == ["s3api", "head-object"]:
    data = object_bytes(value("--key"))
    emit(
        {
            "VersionId": "v" + str(cloud["state"]["serial"]),
            "ContentLength": len(data),
            "ETag": hashlib.sha256(data).hexdigest(),
        }
    )
elif operation == ["s3api", "get-object"]:
    data = object_bytes(value("--key"))
    target = args[args.index("--region") - 1]
    Path(target).write_bytes(data)
    emit({"VersionId": "v" + str(cloud["state"]["serial"])})
elif operation == ["s3api", "put-object"]:
    key = value("--key")
    data = Path(value("--body")).read_bytes()
    if "--if-none-match" in args and key in cloud["objects"]:
        print("PreconditionFailed", file=sys.stderr)
        sys.exit(1)
    if "--if-match" in args:
        old = object_bytes(key)
        if value("--if-match") != hashlib.sha256(old).hexdigest():
            print("PreconditionFailed", file=sys.stderr)
            sys.exit(1)
    if key == "operations/current-generation.json":
        cloud["pointer"] = json.loads(data)
    else:
        import base64

        cloud["objects"][key] = base64.b64encode(data).decode()
    save()
    emit(
        {
            "ETag": hashlib.sha256(data).hexdigest(),
            "VersionId": "v" + str(cloud["state"]["serial"]),
        }
    )
elif operation == ["ec2", "describe-instances"]:
    if cloud.get("deny_instance_lookup"):
        print("An error occurred (UnauthorizedOperation): denied", file=sys.stderr)
        sys.exit(254)
    instance = cloud.get("live_resources", {}).get("aws_instance.host")
    if instance:
        state = (
            instance.get("state", "running")
            if isinstance(instance, dict)
            else "running"
        )
        generation = cloud["pointer"]["generation"]
        inventory = cloud.get("inventory", {})
        emit(
            {
                "Reservations": [
                    {
                        "Instances": [
                            {
                                "InstanceId": inventory.get(
                                    "aws_instance.host", "i-11111111111111111"
                                ),
                                "State": {"Name": state},
                                "Tags": [
                                    {"Key": "ManagedBy", "Value": "onlineshop-test"},
                                    {
                                        "Key": "Repository",
                                        "Value": "Djimi/OnlineShop-full-stack",
                                    },
                                    {
                                        "Key": "Generation",
                                        "Value": generation,
                                    },
                                ],
                                "BlockDeviceMappings": [
                                    {
                                        "Ebs": {
                                            "VolumeId": inventory.get(
                                                "instance_root_volume"
                                            )
                                        }
                                    }
                                ],
                                "NetworkInterfaces": [
                                    {
                                        "NetworkInterfaceId": inventory.get(
                                            "instance_network_interface"
                                        )
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        )
    else:
        print(
            "An error occurred (InvalidInstanceID.NotFound): not found", file=sys.stderr
        )
        sys.exit(254)
elif operation == ["ssm", "list-commands"]:
    emit({"Commands": cloud["commands"]})
elif operation == ["ssm", "send-command"]:
    body = json.loads(value("--parameters"))["commands"][0]
    command_id = "00000000-0000-0000-0000-" + str(len(cloud["commands"]) + 1).zfill(12)
    output = {
        "generation": cloud["pointer"]["generation"],
        "host_generation": cloud["pointer"]["generation"],
        "host_lock_acquired": True,
        "test_container_absent": True,
        "temporary_credentials_absent": True,
    }
    recorded_host = cloud.get("live_resources", {}).get("aws_instance.host")
    if (
        cloud.get("host_lock")
        or cloud.get("detached_test")
        or isinstance(recorded_host, dict)
        and (recorded_host.get("host_lock") or recorded_host.get("detached_test"))
    ):
        output["host_lock_acquired"] = False
    cloud["commands"].append(
        {
            "CommandId": command_id,
            "Comment": value("--comment"),
            "DocumentName": "AWS-RunShellScript",
            "InstanceIds": [value("--instance-ids")],
            "Parameters": {"commands": [body], "executionTimeout": ["60"]},
            "Status": "Success",
            "Output": json.dumps(output),
        }
    )
    save()
    emit({"Command": {"CommandId": command_id}})
elif operation == ["ssm", "get-command-invocation"]:
    command = next(
        item for item in cloud["commands"] if item["CommandId"] == value("--command-id")
    )
    emit(
        {
            "Status": command["Status"],
            "ResponseCode": 0,
            "StandardOutputContent": command["Output"],
        }
    )
elif operation and operation[0] == "ec2" and operation[1].startswith("describe-"):
    if cloud.get("deny_resource_observation"):
        print("An error occurred (AccessDenied): denied", file=sys.stderr)
        sys.exit(254)
    inventory = cloud.get("inventory", {})
    live = cloud.get("live_resources", {})
    api = {
        "describe-vpcs": ("aws_vpc.main", "Vpcs", "VpcId", "InvalidVpcID.NotFound"),
        "describe-subnets": (
            "aws_subnet.host",
            "Subnets",
            "SubnetId",
            "InvalidSubnetID.NotFound",
        ),
        "describe-internet-gateways": (
            "aws_internet_gateway.main",
            "InternetGateways",
            "InternetGatewayId",
            "InvalidInternetGatewayID.NotFound",
        ),
        "describe-route-tables": (
            "aws_route_table.host",
            "RouteTables",
            "RouteTableId",
            "InvalidRouteTableID.NotFound",
        ),
        "describe-security-groups": (
            "aws_security_group.host",
            "SecurityGroups",
            "GroupId",
            "InvalidGroup.NotFound",
        ),
        "describe-launch-templates": (
            "aws_launch_template.host",
            "LaunchTemplates",
            "LaunchTemplateId",
            "InvalidLaunchTemplateId.NotFound",
        ),
        "describe-volumes": (
            "instance_root_volume",
            "Volumes",
            "VolumeId",
            "InvalidVolume.NotFound",
        ),
        "describe-network-interfaces": (
            "instance_network_interface",
            "NetworkInterfaces",
            "NetworkInterfaceId",
            "InvalidNetworkInterfaceID.NotFound",
        ),
    }
    address, field, identity, not_found = api[operation[1]]
    resource = live.get(address)
    if resource:
        generation = cloud["pointer"]["generation"]
        tags = [
            {"Key": "ManagedBy", "Value": "onlineshop-test"},
            {"Key": "Repository", "Value": "Djimi/OnlineShop-full-stack"},
        ]
        if address != "aws_launch_template.host":
            tags.append({"Key": "Generation", "Value": generation})
        value = {identity: inventory.get(address, inventory.get(address)), "Tags": tags}
        if operation[1] == "describe-route-tables":
            value["Routes"] = (
                [
                    {
                        "DestinationCidrBlock": "0.0.0.0/0",
                        "GatewayId": inventory["aws_internet_gateway.main"],
                    }
                ]
                if live.get("aws_route.outbound")
                else []
            )
            value["Associations"] = (
                [
                    {
                        "RouteTableAssociationId": inventory[
                            "aws_route_table_association.host"
                        ],
                        "RouteTableId": inventory["aws_route_table.host"],
                        "SubnetId": inventory["aws_subnet.host"],
                    }
                ]
                if live.get("aws_route_table_association.host")
                else []
            )
        emit({field: [value]})
    else:
        print(f"An error occurred ({not_found}): not found", file=sys.stderr)
        sys.exit(254)
else:
    print("unhandled protected operation", file=sys.stderr)
    sys.exit(99)
