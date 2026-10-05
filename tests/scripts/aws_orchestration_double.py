"""Durable process boundary double; never imported by production."""

import base64
import hashlib
import io
import json
import os
import sys
import tarfile
from pathlib import Path

root = Path(os.environ["FAKE_API"])
tool, *args = sys.argv[1:]
cloud_path = root / "session-cloud.json"
cloud = json.loads(cloud_path.read_text())
with (root / "session-calls.jsonl").open("a") as output:
    output.write(
        json.dumps(
            {"tool": tool, "args": args, "generation": cloud["pointer"]["generation"]}
        )
        + "\n"
    )


def value(flag):
    return args[args.index(flag) + 1]


def save():
    cloud_path.write_text(json.dumps(cloud))


def emit(data):
    print(json.dumps(data))


def object_data(key):
    if key == "operations/current-generation.json":
        return json.dumps(cloud["pointer"]).encode()
    if key == "state/environment.tfstate":
        return json.dumps(cloud["state"]).encode()
    return base64.b64decode(cloud["objects"][key])


def reports():
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for short, count in [("ItemsE2ETest", 3), ("RestAssuredLoggingTest", 1)]:
            suite = "com.onlineshop.e2e." + short
            cases = "".join(
                f'<testcase name="test{i}" classname="{suite}" />' for i in range(count)
            )
            data = f'<testsuite name="{suite}" tests="{count}" failures="0" errors="0" skipped="0">{cases}</testsuite>'.encode()
            info = tarfile.TarInfo("TEST-" + suite + ".xml")
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


if tool == "git":
    if args[:2] == ["rev-parse", "HEAD"]:
        print(os.environ["GITHUB_SHA"])
    if args[0] == "diff" and cloud.get("modified_controller"):
        print("changed trusted runtime")
    sys.exit(0)
if tool == "terraform":
    if args[0] == "plan":
        Path("current.tfplan").write_bytes(b"inspected-plan")
    if args[0] == "show":
        resources = []
        changes = []
        for resource in cloud["state"]["resources"]:
            address = resource["type"] + "." + resource["name"]
            before = resource["instances"][0]["attributes"]
            after = json.loads(json.dumps(before))
            if address != "aws_launch_template.host":
                after["tags"]["Generation"] = os.environ["TF_VAR_generation"]
            if cloud.get("config_drift") and address == "aws_instance.host":
                after["instance_type"] = "unapproved"
            resources.append({"address": address, "values": after})
            changes.append(
                {
                    "address": address,
                    "change": {
                        "actions": ["delete", "create"]
                        if cloud.get("replacement")
                        else ["update"]
                        if before != after
                        else ["no-op"],
                        "before": before,
                        "after": after,
                        "after_unknown": {
                            "metadata_options": [{}],
                            "root_block_device": [{}],
                            "tags_all": {},
                        }
                        if cloud.get("nested_unknown_shapes")
                        else {},
                    },
                }
            )
        emit(
            {
                "planned_values": {"root_module": {"resources": resources}},
                "resource_changes": changes,
            }
        )
    if args[0] == "apply":
        if cloud.get("apply_failure"):
            print("raw-secret-canary", file=sys.stderr)
            sys.exit(1)
        for resource in cloud["state"]["resources"]:
            if resource["type"] != "aws_launch_template":
                resource["instances"][0]["attributes"]["tags"]["Generation"] = (
                    os.environ["TF_VAR_generation"]
                )
        cloud["state"]["serial"] += 1
        cloud["live_generation"] = os.environ["TF_VAR_generation"]
        save()
        if cloud.pop("crash_after_apply", False):
            save()
            sys.exit(1)
    sys.exit(0)
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
    keys = [k for k in cloud["objects"] if k.startswith(prefix)]
    emit(
        {
            "KeyCount": len(keys),
            "Contents": [{"Key": k} for k in keys],
            "IsTruncated": False,
        }
    )
elif operation == ["s3api", "head-object"]:
    data = object_data(value("--key"))
    emit(
        {
            "ContentLength": len(data),
            "VersionId": "v1",
            "ETag": hashlib.sha256(data).hexdigest(),
        }
    )
elif operation == ["s3api", "get-object"]:
    data = object_data(value("--key"))
    if "--if-match" in args and value("--if-match") != hashlib.sha256(data).hexdigest():
        sys.exit(1)
    target = args[args.index("--region") - 1]
    Path(target).write_bytes(data)
    emit({"VersionId": "v1"})
elif operation == ["s3api", "put-object"]:
    key = value("--key")
    data = Path(value("--body")).read_bytes()
    if "--if-none-match" in args and key in cloud["objects"]:
        sys.exit(1)
    if (
        "--if-match" in args
        and value("--if-match") != hashlib.sha256(object_data(key)).hexdigest()
    ):
        sys.exit(1)
    if key == "operations/current-generation.json":
        cloud["pointer"] = json.loads(data)
    else:
        cloud["objects"][key] = base64.b64encode(data).decode()
    save()
    emit({"ETag": hashlib.sha256(data).hexdigest(), "VersionId": "v1"})
elif operation == ["ec2", "describe-instances"]:
    emit(
        {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": cloud["pointer"]["host_id"],
                            "State": {"Name": "running"},
                            "Tags": [
                                {"Key": k, "Value": v}
                                for k, v in {
                                    "ManagedBy": "onlineshop-test",
                                    "Repository": "Djimi/OnlineShop-full-stack",
                                    "Generation": cloud["live_generation"],
                                }.items()
                            ],
                        }
                    ]
                }
            ]
        }
    )
elif operation == ["ssm", "list-commands"]:
    commands = cloud["commands"]
    if cloud.get("zero_discovery"):
        commands = []
    if cloud.get("duplicate_discovery"):
        commands = commands + commands
    if cloud.get("active_ssm"):
        commands = [{**c, "Status": "InProgress"} for c in commands]
    emit({"Commands": commands})
elif operation == ["ssm", "send-command"]:
    parameters = json.loads(value("--parameters"))
    comment = value("--comment")
    body = parameters["commands"][0]
    if "<<'PY'\n" in body:
        compile(
            body.split("<<'PY'\n", 1)[1].rsplit("PY", 1)[0], "<fixed-SSM-body>", "exec"
        )
    command_id = "00000000-0000-0000-0000-" + str(len(cloud["commands"]) + 1).zfill(12)
    output = {
        "generation": cloud["live_generation"],
        "host_generation": cloud.get("host_generation", "run-100-attempt-1"),
        "host_lock_acquired": True,
        "test_container_absent": True,
        "temporary_credentials_absent": True,
    }
    if cloud.get("host_lock") or cloud.get("detached_test"):
        output["host_lock_acquired"] = False
    if comment.endswith(":runtime"):
        key = next(
            k
            for k in cloud["objects"]
            if k.endswith("bundle.tar") and cloud["pointer"]["generation"] in k
        )
        with tarfile.open(fileobj=io.BytesIO(object_data(key))) as archive:
            binding = json.loads(archive.extractfile("binding.json").read())
        if binding["predecessor_generation"] != cloud.get(
            "host_generation", "run-100-attempt-1"
        ):
            sys.exit(1)
        cloud["host_generation"] = cloud["pointer"]["generation"]
        data = b"" if cloud.get("empty_reports") else reports()
        envelope = {
            "binding": binding,
            "status": "passed",
            "stages": ["reset", "readiness", "e2e", "reports"],
            "cleanup_verified": not cloud.get("cleanup_unknown"),
            "reports_sha256": hashlib.sha256(data).hexdigest(),
        }
        if cloud.get("missing_stage"):
            envelope["stages"].remove("e2e")
        if cloud.get("wrong_report_hash"):
            envelope["reports_sha256"] = "0" * 64
        if cloud.get("old_candidate"):
            envelope["binding"]["request"]["candidate_sha"] = "f" * 40
        prefix = "operations/runtime-evidence/" + cloud["pointer"]["generation"] + "/"
        cloud["objects"][prefix + "envelope.json"] = base64.b64encode(
            json.dumps(envelope).encode()
        ).decode()
        cloud["objects"][prefix + "reports.tar"] = base64.b64encode(data).decode()
        if cloud.get("failed_runtime") and not cloud.get("forged_passed_envelope"):
            envelope["status"] = "failed"
            cloud["objects"][prefix + "envelope.json"] = base64.b64encode(
                json.dumps(envelope).encode()
            ).decode()
        if cloud.get("candidate_changes") or cloud.get("newer_attempt"):
            routes = json.loads((root / "routes.json").read_text())
            if cloud.get("candidate_changes"):
                routes["GET repos/Djimi/OnlineShop-full-stack/pulls/42"]["state"] = (
                    "closed"
                )
            else:
                route = (
                    "GET repos/Djimi/OnlineShop-full-stack/commits/"
                    + "c" * 40
                    + "/check-runs?check_name=AWS%20validation&filter=latest&per_page=100&page=1"
                )
                routes[route] = {
                    "check_runs": [{"id": 999, "app": {"slug": "github-actions"}}]
                }
            (root / "routes.json").write_text(json.dumps(routes))
    command = {
        "CommandId": command_id,
        "DocumentName": "AWS-RunShellScript",
        "InstanceIds": [cloud["pointer"]["host_id"]],
        "Parameters": parameters,
        "Comment": comment,
        "Status": "Success",
        "Output": json.dumps(output),
    }
    cloud["commands"].append(command)
    lost = cloud.pop("lost_send", False) if comment.endswith(":runtime") else False
    lost = lost or cloud.pop("lost_probe", False)
    if cloud.get("failed_runtime") and comment.endswith(":runtime"):
        command["Status"] = "Failed"
    save()
    if lost:
        print("raw-secret-canary", file=sys.stderr)
        sys.exit(1)
    emit({"Command": {"CommandId": command_id}})
elif operation == ["ssm", "get-command-invocation"]:
    if cloud.get("visibility_delay", 0):
        cloud["visibility_delay"] -= 1
        save()
        print(
            "An error occurred (InvocationDoesNotExist): raw-secret-canary",
            file=sys.stderr,
        )
        sys.exit(254)
    command = next(
        c for c in cloud["commands"] if c["CommandId"] == value("--command-id")
    )
    emit(
        {
            "Status": command["Status"],
            "ResponseCode": 0,
            "StandardOutputContent": command["Output"],
        }
    )
elif operation == ["ecr", "batch-get-image"]:
    digest = value("--image-ids").split("=", 1)[1]
    emit(
        {
            "images": [
                {"imageId": {"imageDigest": digest}, "imageManifest": "fixed-manifest"}
            ],
            "failures": [],
        }
    )
elif operation == ["ecr", "put-image"]:
    cloud.setdefault("retention", {})[
        value("--repository-name") + ":" + value("--image-tag")
    ] = value("--image-digest")
    save()
    emit({"image": {"imageId": {"imageDigest": value("--image-digest")}}})
elif operation == ["ecr", "batch-delete-image"]:
    tag = value("--image-ids").split("=", 1)[1]
    cloud.setdefault("retention", {}).pop(value("--repository-name") + ":" + tag, None)
    save()
    emit({"imageIds": [{"imageTag": tag}], "failures": []})
else:
    print("unhandled protected operation", file=sys.stderr)
    sys.exit(99)
