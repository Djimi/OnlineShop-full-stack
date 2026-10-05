"""Destroy only the inspected disposable environment under its shared session lock."""

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import zlib
from pathlib import Path

from aws_cloud_reconciliation import (
    ADDRESSES,
    REGION,
    ReconciliationBlocked,
    observe_ssm,
    read_aws,
    read_snapshot,
    verify_state,
)
from aws_runtime_orchestration import (
    GENERATION,
    POINTER,
    ROOT,
    host_probe,
    location,
    private_tools,
    put_json,
    reconcile_operations,
    remote,
    snapshots,
    verify_idle,
)

DESTROY_LIMIT_SECONDS = 20 * 60
MAX_DISPOSAL_ATTEMPTS = 10
RESOURCE_PREFIXES = {
    "aws_vpc.main": "vpc-",
    "aws_subnet.host": "subnet-",
    "aws_internet_gateway.main": "igw-",
    "aws_route_table.host": "rtb-",
    "aws_route.outbound": "r-rtb-",
    "aws_route_table_association.host": "rtbassoc-",
    "aws_security_group.host": "sg-",
    "aws_launch_template.host": "lt-",
    "aws_instance.host": "i-",
    "aws_ec2_tag.network_generation": "eni-",
}
TAGS = {"ManagedBy": "onlineshop-test", "Repository": "Djimi/OnlineShop-full-stack"}
TAGGED_ADDRESSES = {
    "aws_vpc.main",
    "aws_subnet.host",
    "aws_internet_gateway.main",
    "aws_route_table.host",
    "aws_security_group.host",
    "aws_launch_template.host",
    "aws_instance.host",
}


def dispose_session(expected_generation, output):
    if output.is_symlink():
        raise ReconciliationBlocked("unsafe disposal evidence directory")
    output.mkdir(mode=0o700, parents=True, exist_ok=True)
    summary = {
        "status": "failed",
        "generation": expected_generation,
        "aws_validation_success": False,
        "stage": "admission",
        "apply_outcome": "not-started",
    }
    try:
        if not GENERATION.fullmatch(expected_generation):
            raise ReconciliationBlocked("invalid expected generation")
        context = trusted_context()
        with tempfile.TemporaryDirectory(
            prefix="aws-disposal-", dir=os.environ["RUNNER_TEMP"]
        ) as temporary:
            directory = Path(temporary)
            summary["stage"] = "reconciliation"
            state, pointer, etag = snapshots(context, directory)
            verify_pointer(pointer)
            if pointer["generation"] != expected_generation:
                raise ReconciliationBlocked("queued disposal generation is stale")
            if pointer["status"] == "disposed":
                verify_disposed_generation(context, state, pointer, directory)
                summary.update(
                    status="no-op", stage="verified-absence", resource_count=0
                )
                return

            summary["stage"] = "ownership"
            generation, host_id = pointer["generation"], pointer["host_id"]
            operation = disposal_intent(context, pointer, state, directory)
            verify_retry_state(state, operation, pointer)
            observe_ssm(host_id, time.monotonic() + 300)
            reconcile_operations(context, pointer, directory)
            attempt = (
                next_disposal_attempt_name(
                    context, generation, state, operation, directory
                )
                if current_addresses(state)
                else None
            )
            summary["stage"] = "host-observation"
            host_observation = observe_disposal_host(host_id, generation, directory)
            if host_observation["status"] == "running":
                predecessor = pointer.get("host_predecessor", {}).get("generation")
                observation = remote(
                    context,
                    pointer,
                    "dispose-probe-" + attempt,
                    host_probe(generation, predecessor),
                    60,
                    directory,
                )
                verify_idle(observation, generation, predecessor)
                host_observation["idle_verified"] = True

            summary["stage"] = "operation-reconciliation"
            recovered_from = reconcile_destroy_applies(
                context,
                pointer,
                state,
                etag,
                operation,
                host_observation,
                attempt,
                directory,
            )
            if attempt is not None:
                attempt = next_disposal_attempt(
                    context,
                    generation,
                    state,
                    operation,
                    directory,
                    attempt=attempt,
                    recovered_from=recovered_from,
                )

            current_state, current_pointer, current_etag = snapshots(context, directory)
            if (
                current_state != state
                or current_pointer != pointer
                or current_etag != etag
            ):
                raise ReconciliationBlocked(
                    "state or generation changed during disposal admission"
                )
            verify_retry_state(current_state, operation, pointer)
            if current_addresses(current_state):
                summary["stage"] = "destroy-plan"
                inspected = destroy_plan(
                    context, generation, current_state, operation, attempt, directory
                )
                summary["stage"] = "host-final-observation"
                final_host = observe_disposal_host(host_id, generation, directory)
                if final_host["status"] == "running":
                    predecessor = pointer.get("host_predecessor", {}).get("generation")
                    probe = remote(
                        context,
                        pointer,
                        "dispose-final-probe-" + attempt,
                        host_probe(generation, predecessor),
                        60,
                        directory,
                    )
                    verify_idle(probe, generation, predecessor)
                    final_host["idle_verified"] = True
                observe_ssm(host_id, time.monotonic() + 300)
                latest_state, latest_pointer, latest_etag = snapshots(
                    context, directory
                )
                if (
                    latest_state != current_state
                    or latest_pointer != pointer
                    or latest_etag != etag
                ):
                    raise ReconciliationBlocked(
                        "state changed during final host-idle observation"
                    )
                host_receipt = {
                    "schema": 1,
                    "operation": "destroy-host-observation",
                    "generation": generation,
                    "host_id": host_id,
                    "attempt": attempt,
                    "plan_sha256": inspected["digest"],
                    "status": final_host["status"],
                    "idle_verified": final_host["idle_verified"],
                    "state_lineage": current_state["lineage"],
                    "state_serial": current_state["serial"],
                    "state_sha256": state_digest(current_state),
                    "resource_ids": operation["resource_ids"],
                    "ssm_terminal": True,
                }
                put_event_once(
                    context,
                    generation,
                    "destroy-host-observation-" + attempt,
                    host_receipt,
                    directory,
                )
                summary["stage"] = "destroy-apply"
                try:
                    destroy_apply(
                        context,
                        generation,
                        current_state,
                        operation,
                        inspected,
                        directory,
                        host_receipt,
                        summary,
                    )
                except private_tools().ProofFailed:
                    summary["diagnostic"] = "protected_destroy_apply_failed"
                    summary["leftovers"] = observed_leftovers(
                        context,
                        current_state,
                        pointer,
                        operation,
                        directory,
                    )
                    raise ReconciliationBlocked(
                        "destroy apply outcome is unknown; reconcile its exact attempt before retry"
                    ) from None
                final_state, final_pointer, final_etag = snapshots(context, directory)
                if final_pointer != pointer or final_etag != etag:
                    raise ReconciliationBlocked("generation changed during destroy")
                verify_empty_state(final_state, operation["state_lineage"])
                if final_state["serial"] <= current_state["serial"]:
                    raise ReconciliationBlocked(
                        "destroy did not advance retained Terraform state"
                    )
            else:
                # An interrupted apply may have emptied the retained state before
                # the terminal event/pointer CAS. Reconcile its exact prior intent;
                # never init or reapply against an empty state.
                final_state, final_pointer, final_etag = current_state, pointer, etag
                verify_empty_state(final_state, operation["state_lineage"])
                if final_state["serial"] <= operation["initial_state_serial"]:
                    raise ReconciliationBlocked(
                        "empty state has no recorded destroy transition"
                    )
            if final_pointer != pointer or final_etag != etag:
                raise ReconciliationBlocked("generation changed during destroy")
            summary["stage"] = "resource-absence"
            verify_resource_absence(context, operation["resource_ids"], directory)
            terminal = {
                "schema": 1,
                "operation": "dispose",
                "generation": generation,
                "host_id": host_id,
                "status": "disposed",
                "account_id": context["account"],
                "region": REGION,
                "root": "infra/aws/environment",
                "state_key": "state/environment.tfstate",
                "state_lineage": operation["state_lineage"],
                "state_serial": final_state["serial"],
                "resource_ids": operation["resource_ids"],
                "verified_absence": True,
            }
            write_immutable_event(
                context, generation, "dispose-terminal", terminal, directory
            )
            disposed = dict(pointer, status="disposed")
            new_etag = put_json(context, POINTER, disposed, directory, etag=etag)
            if not new_etag:
                raise ReconciliationBlocked(
                    "disposed generation pointer was not persisted"
                )
            summary.update(
                status="disposed", stage="complete", resource_count=len(ADDRESSES)
            )
    except ReconciliationBlocked:
        raise
    except private_tools().ProofFailed:
        summary["diagnostic"] = "protected_process_failed"
        raise ReconciliationBlocked(
            "disposal stopped at "
            + summary["stage"]
            + "; reconcile the recorded generation"
        ) from None
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        StopIteration,
        subprocess.SubprocessError,
    ):
        raise ReconciliationBlocked(
            "disposal stopped safely; reconcile the recorded generation"
        ) from None
    finally:
        (output / "summary.json").write_text(
            json.dumps(summary, sort_keys=True, indent=2)
        )


def trusted_context():
    account = os.environ.get("AWS_TESTING_ACCOUNT_ID", "")
    bucket = os.environ.get("AWS_TESTING_STATE_BUCKET", "")
    if not re.fullmatch(r"[0-9]{12}", account) or not re.fullmatch(
        r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket
    ):
        raise ReconciliationBlocked("invalid fixed account or state bucket")
    identity = read_aws(["sts", "get-caller-identity"], deadline=time.monotonic() + 60)
    if identity.get("Account") != account or not identity.get("Arn", "").startswith(
        f"arn:aws:sts::{account}:assumed-role/onlineshop-test-operator/"
    ):
        raise ReconciliationBlocked("wrong operator role or account")
    root = ROOT / "infra/aws/environment"
    backend = root / "backend.tf"
    if (
        root.is_symlink()
        or backend.is_symlink()
        or not backend.is_file()
        or 'key                  = "state/environment.tfstate"'
        not in backend.read_text()
        or 'region               = "eu-north-1"' not in backend.read_text()
        or not (root / ".terraform.lock.hcl").is_file()
    ):
        raise ReconciliationBlocked("fixed Terraform root or backend changed")
    return {"account": account, "bucket": bucket}


def verify_pointer(pointer):
    if (
        not isinstance(pointer, dict)
        or not GENERATION.fullmatch(pointer.get("generation", ""))
        or not re.fullmatch(r"i-[a-f0-9]{17}", pointer.get("host_id", ""))
    ):
        raise ReconciliationBlocked("generation pointer identity is invalid")
    if pointer.get("schema") is None:
        legacy = {"generation", "host_id", "status"}
        migration = legacy | {"purpose"}
        if set(pointer) == migration:
            if pointer.get("purpose") != "owner-empty-host-generation-migration":
                raise ReconciliationBlocked("unsupported initial generation purpose")
        elif set(pointer) != legacy:
            raise ReconciliationBlocked("unsupported initial environment pointer")
        if pointer["status"] not in {"provisioned", "disposed"}:
            raise ReconciliationBlocked("unsupported initial environment pointer")
        return
    expected = {
        "schema",
        "generation",
        "host_id",
        "status",
        "predecessor",
        "host_predecessor",
        "request",
        "images",
        "retained",
    }
    if (
        set(pointer) != expected
        or pointer["schema"] != 1
        or pointer["status"]
        not in {
            "provisioned",
            "running",
            "failed",
            "completed",
            "recovered-aborted",
            "disposed",
        }
    ):
        raise ReconciliationBlocked("unrecognized environment generation record")
    if (
        not isinstance(pointer["request"], dict)
        or pointer["generation"]
        != f"run-{pointer['request'].get('validation_run_id')}-attempt-{pointer['request'].get('validation_run_attempt')}"
    ):
        raise ReconciliationBlocked("generation does not match its immutable request")
    for key in ("predecessor", "host_predecessor"):
        value = pointer[key]
        if (
            not isinstance(value, dict)
            or set(value) != {"generation", "host_id"}
            or not GENERATION.fullmatch(value["generation"])
            or value["host_id"] != pointer["host_id"]
        ):
            raise ReconciliationBlocked("environment predecessor identity is invalid")
    if not isinstance(pointer["retained"], list):
        raise ReconciliationBlocked("retained image history is invalid")


def owned_inventory(state, pointer):
    generation, host_id = pointer["generation"], pointer["host_id"]
    verify_state(
        state, {"generation": generation, "host_id": host_id, "status": "provisioned"}
    )
    entries = {
        resource["type"] + "." + resource["name"]: resource
        for resource in state["resources"]
    }
    if set(entries) != ADDRESSES:
        raise ReconciliationBlocked("environment state address set is not exact")
    attributes = {}
    for address, resource in entries.items():
        if (
            resource.get("mode") != "managed"
            or resource.get("module")
            or len(resource.get("instances", [])) != 1
        ):
            raise ReconciliationBlocked("environment state contains a foreign resource")
        value = resource["instances"][0].get("attributes")
        if not isinstance(value, dict) or not isinstance(value.get("id"), str):
            raise ReconciliationBlocked("environment resource identity is incomplete")
        prefix = RESOURCE_PREFIXES[address]
        if not value["id"].startswith(prefix):
            raise ReconciliationBlocked(
                "environment resource ID has an unexpected type"
            )
        verify_resource_tags(address, value, generation)
        attributes[address] = value
    if (
        attributes["aws_subnet.host"].get("vpc_id") != attributes["aws_vpc.main"]["id"]
        or attributes["aws_internet_gateway.main"].get("vpc_id")
        != attributes["aws_vpc.main"]["id"]
        or attributes["aws_route_table.host"].get("vpc_id")
        != attributes["aws_vpc.main"]["id"]
        or attributes["aws_security_group.host"].get("vpc_id")
        != attributes["aws_vpc.main"]["id"]
        or attributes["aws_route.outbound"].get("route_table_id")
        != attributes["aws_route_table.host"]["id"]
        or attributes["aws_route.outbound"].get("gateway_id")
        != attributes["aws_internet_gateway.main"]["id"]
        or attributes["aws_route.outbound"].get("destination_cidr_block") != "0.0.0.0/0"
        or attributes["aws_route.outbound"].get("id")
        != route_resource_id(attributes["aws_route_table.host"]["id"], "0.0.0.0/0")
        or attributes["aws_route_table_association.host"].get("route_table_id")
        != attributes["aws_route_table.host"]["id"]
        or attributes["aws_route_table_association.host"].get("subnet_id")
        != attributes["aws_subnet.host"]["id"]
    ):
        raise ReconciliationBlocked("environment state resource relationships mismatch")
    instance = attributes["aws_instance.host"]
    network_id = instance.get("primary_network_interface_id")
    volumes = instance.get("root_block_device")
    launch_template = instance.get("launch_template")
    security_groups = instance.get("vpc_security_group_ids")
    if (
        not isinstance(network_id, str)
        or not network_id.startswith("eni-")
        or not isinstance(volumes, list)
        or len(volumes) != 1
        or not volumes[0].get("volume_id", "").startswith("vol-")
        or instance.get("subnet_id") != attributes["aws_subnet.host"]["id"]
        or security_groups != [attributes["aws_security_group.host"]["id"]]
        or not isinstance(launch_template, list)
        or len(launch_template) != 1
        or launch_template[0].get("id") != attributes["aws_launch_template.host"]["id"]
        or attributes["aws_ec2_tag.network_generation"].get("resource_id") != network_id
        or attributes["aws_ec2_tag.network_generation"].get("key") != "Generation"
        or attributes["aws_ec2_tag.network_generation"].get("value") != generation
    ):
        raise ReconciliationBlocked("host secondary resource identity mismatch")
    return generation, host_id, resource_ids(attributes)


def resource_ids(attributes):
    return {
        **{address: values["id"] for address, values in attributes.items()},
        "instance_network_interface": attributes["aws_instance.host"][
            "primary_network_interface_id"
        ],
        "instance_root_volume": attributes["aws_instance.host"]["root_block_device"][0][
            "volume_id"
        ],
    }


def current_addresses(state):
    return {
        resource["type"] + "." + resource["name"] for resource in state["resources"]
    }


def verify_retry_state(state, intent, pointer):
    if state.get("lineage") != intent["state_lineage"] or not isinstance(
        state.get("resources"), list
    ):
        raise ReconciliationBlocked(
            "environment state lineage or resource list changed"
        )
    if (
        type(state.get("serial")) is not int
        or state["serial"] < intent["initial_state_serial"]
    ):
        raise ReconciliationBlocked("environment state serial moved backwards")
    validate_inventory_ids(intent["resource_ids"])
    addresses = current_addresses(state)
    if not addresses <= ADDRESSES:
        raise ReconciliationBlocked("partial destroy state has unknown addresses")
    values = {}
    for resource in state["resources"]:
        address = resource["type"] + "." + resource["name"]
        if (
            resource.get("module")
            or resource.get("mode") != "managed"
            or len(resource.get("instances", [])) != 1
        ):
            raise ReconciliationBlocked("partial destroy state has foreign contents")
        values[address] = resource["instances"][0].get("attributes", {})
        expected_id = intent["resource_ids"].get(address)
        if (
            values[address].get("id") != expected_id
            or not isinstance(expected_id, str)
            or not expected_id.startswith(RESOURCE_PREFIXES[address])
        ):
            raise ReconciliationBlocked(
                "partial destroy state replaced a recorded resource"
            )
        verify_resource_tags(address, values[address], pointer["generation"])
    for address, reference, field in [
        ("aws_subnet.host", "aws_vpc.main", "vpc_id"),
        ("aws_internet_gateway.main", "aws_vpc.main", "vpc_id"),
        ("aws_route_table.host", "aws_vpc.main", "vpc_id"),
        ("aws_security_group.host", "aws_vpc.main", "vpc_id"),
        ("aws_route.outbound", "aws_route_table.host", "route_table_id"),
        ("aws_route.outbound", "aws_internet_gateway.main", "gateway_id"),
        ("aws_route_table_association.host", "aws_route_table.host", "route_table_id"),
        ("aws_route_table_association.host", "aws_subnet.host", "subnet_id"),
    ]:
        if (
            address in values
            and values[address].get(field) != intent["resource_ids"][reference]
        ):
            raise ReconciliationBlocked(
                "partial environment resource relationship changed"
            )
    if (
        "aws_route.outbound" in values
        and values["aws_route.outbound"].get("destination_cidr_block") != "0.0.0.0/0"
    ):
        raise ReconciliationBlocked("partial environment route destination changed")
    if "aws_instance.host" in values:
        interface = values["aws_instance.host"].get("primary_network_interface_id")
        volumes = values["aws_instance.host"].get("root_block_device")
        if (
            interface != intent["resource_ids"]["instance_network_interface"]
            or not isinstance(volumes, list)
            or len(volumes) != 1
            or volumes[0].get("volume_id")
            != intent["resource_ids"]["instance_root_volume"]
            or values["aws_instance.host"].get("subnet_id")
            != intent["resource_ids"]["aws_subnet.host"]
            or values["aws_instance.host"].get("vpc_security_group_ids")
            != [intent["resource_ids"]["aws_security_group.host"]]
            or not isinstance(values["aws_instance.host"].get("launch_template"), list)
            or len(values["aws_instance.host"]["launch_template"]) != 1
            or values["aws_instance.host"]["launch_template"][0].get("id")
            != intent["resource_ids"]["aws_launch_template.host"]
        ):
            raise ReconciliationBlocked(
                "partial host secondary IDs differ from disposal intent"
            )
    if "aws_ec2_tag.network_generation" in values and any(
        values["aws_ec2_tag.network_generation"].get(key) != value
        for key, value in {
            "resource_id": intent["resource_ids"]["instance_network_interface"],
            "key": "Generation",
            "value": pointer["generation"],
        }.items()
    ):
        raise ReconciliationBlocked("partial ENI generation tag identity changed")


def disposal_intent(context, pointer, state, directory):
    key = f"operations/{pointer['generation']}/dispose-intent.json"
    prior = existing_event(context, key, directory / "dispose-intent-prior.json")
    if prior is not None:
        expected = {
            "schema",
            "operation",
            "generation",
            "host_id",
            "account_id",
            "region",
            "root",
            "state_key",
            "state_lineage",
            "initial_state_serial",
            "resource_ids",
            "status",
        }
        if (
            not isinstance(prior, dict)
            or set(prior) != expected
            or prior.get("schema") != 1
            or prior.get("operation") != "dispose"
            or prior.get("generation") != pointer["generation"]
            or prior.get("host_id") != pointer["host_id"]
            or prior.get("account_id") != context["account"]
            or prior.get("region") != REGION
            or prior.get("root") != "infra/aws/environment"
            or prior.get("state_key") != "state/environment.tfstate"
            or prior.get("state_lineage") != state.get("lineage")
            or type(prior.get("initial_state_serial")) is not int
            or prior["initial_state_serial"] < 1
            or prior["initial_state_serial"] > state.get("serial", -1)
            or prior.get("status") != "intent"
        ):
            raise ReconciliationBlocked(
                "prior disposal intent differs from the locked generation"
            )
        validate_inventory_ids(prior.get("resource_ids"))
        return prior
    _, _, inventory = owned_inventory(state, pointer)
    current = {
        "schema": 1,
        "operation": "dispose",
        "generation": pointer["generation"],
        "host_id": pointer["host_id"],
        "account_id": context["account"],
        "region": REGION,
        "root": "infra/aws/environment",
        "state_key": "state/environment.tfstate",
        "state_lineage": state.get("lineage"),
        "initial_state_serial": state.get("serial"),
        "resource_ids": inventory,
        "status": "intent",
    }
    if (
        not isinstance(current["state_lineage"], str)
        or type(current["initial_state_serial"]) is not int
    ):
        raise ReconciliationBlocked("invalid state identity for disposal intent")
    put_json(context, key, current, directory)
    return current


def disposal_attempt_records(context, generation, state, intent, directory):
    prefix = f"operations/{generation}/dispose-attempt-"
    listing = read_aws(
        [
            "s3api",
            "list-objects-v2",
            *location(context),
            "--prefix",
            prefix,
            "--max-keys",
            str(MAX_DISPOSAL_ATTEMPTS + 1),
            "--no-paginate",
        ],
        deadline=time.monotonic() + 60,
    )
    entries = listing.get("Contents", [])
    if (
        listing.get("IsTruncated") is not False
        or not isinstance(entries, list)
        or len(entries) > MAX_DISPOSAL_ATTEMPTS
        or listing.get("KeyCount") != len(entries)
    ):
        raise ReconciliationBlocked("disposal retry history is not bounded")
    records = []
    for item in entries:
        key = item.get("Key") if isinstance(item, dict) else None
        match = re.fullmatch(re.escape(prefix) + r"([0-9]{4})-intent\.json", key or "")
        if not match:
            raise ReconciliationBlocked("unexpected disposal retry history")
        number = int(match.group(1))
        record = existing_event(context, key, directory / "disposal-attempt-prior.json")
        if (
            number < 1
            or number > MAX_DISPOSAL_ATTEMPTS
            or not isinstance(record, dict)
            or set(record)
            != {
                "generation",
                "attempt",
                "state_lineage",
                "state_serial",
                "resource_ids",
                "recovered_from",
                "status",
            }
            or record["generation"] != generation
            or record["attempt"] != f"attempt-{number:04d}"
            or record["state_lineage"] != state["lineage"]
            or type(record["state_serial"]) is not int
            or record["state_serial"] < intent["initial_state_serial"]
            or record["resource_ids"] != intent["resource_ids"]
            or not valid_recovery_links(record["recovered_from"])
            or record["status"] != "admitted"
        ):
            raise ReconciliationBlocked(
                "disposal retry record differs from the immutable inventory"
            )
        records.append((number, record))
    numbers = [number for number, _ in records]
    if len(set(numbers)) != len(numbers):
        raise ReconciliationBlocked("duplicate disposal retry number")
    return records


def valid_recovery_links(links):
    if not isinstance(links, list) or len(links) > MAX_DISPOSAL_ATTEMPTS:
        return False
    for link in links:
        if (
            not isinstance(link, dict)
            or set(link) != {"attempt", "plan_sha256", "state_sha256", "state_serial"}
            or not re.fullmatch(r"attempt-[0-9]{4}", link.get("attempt", ""))
            or not re.fullmatch(r"[a-f0-9]{64}", link.get("plan_sha256", ""))
            or not re.fullmatch(r"[a-f0-9]{64}", link.get("state_sha256", ""))
            or type(link.get("state_serial")) is not int
            or link["state_serial"] < 1
        ):
            return False
    return len({link["attempt"] for link in links}) == len(links)


def next_disposal_attempt_name(context, generation, state, intent, directory):
    records = disposal_attempt_records(context, generation, state, intent, directory)
    number = max((number for number, _ in records), default=0) + 1
    if number > MAX_DISPOSAL_ATTEMPTS:
        raise ReconciliationBlocked("disposal retry limit requires reviewed recovery")
    return f"attempt-{number:04d}"


def next_disposal_attempt(
    context,
    generation,
    state,
    intent,
    directory,
    *,
    attempt,
    recovered_from,
):
    prefix = f"operations/{generation}/dispose-attempt-"
    records = disposal_attempt_records(context, generation, state, intent, directory)
    number = max((number for number, _ in records), default=0) + 1
    if number > MAX_DISPOSAL_ATTEMPTS:
        raise ReconciliationBlocked("disposal retry limit requires reviewed recovery")
    if attempt != f"attempt-{number:04d}" or not valid_recovery_links(recovered_from):
        raise ReconciliationBlocked(
            "disposal retry admission changed during reconciliation"
        )
    put_json(
        context,
        prefix + f"{number:04d}-intent.json",
        {
            "generation": generation,
            "attempt": attempt,
            "state_lineage": state["lineage"],
            "state_serial": state["serial"],
            "resource_ids": intent["resource_ids"],
            "recovered_from": recovered_from,
            "status": "admitted",
        },
        directory,
    )
    return attempt


def observe_disposal_host(host_id, generation, directory):
    result = read_aws(
        ["ec2", "describe-instances", "--instance-ids", host_id],
        deadline=time.monotonic() + 60,
        absent_error="InvalidInstanceID.NotFound",
    )
    if result.get("absent") is True:
        return {"status": "absent", "idle_verified": False}
    reservations = result.get("Reservations")
    if not isinstance(reservations, list):
        raise ReconciliationBlocked("recorded host observation is incomplete")
    instances = [
        instance
        for reservation in reservations
        if isinstance(reservation, dict)
        for instance in reservation.get("Instances", [])
    ]
    if not instances:
        return {"status": "absent", "idle_verified": False}
    if len(instances) != 1 or instances[0].get("InstanceId") != host_id:
        raise ReconciliationBlocked("recorded host identity is ambiguous")
    instance = instances[0]
    status = instance.get("State", {}).get("Name")
    if status == "terminated":
        return {"status": "terminated", "idle_verified": False}
    if status != "running":
        raise ReconciliationBlocked("recorded host is transitioning or unknown")
    tags = aws_tags(instance.get("Tags"))
    if any(
        tags.get(key) != value
        for key, value in {
            **TAGS,
            "Generation": generation,
        }.items()
    ):
        raise ReconciliationBlocked("recorded running host ownership changed")
    return {"status": "running", "idle_verified": False}


def aws_tags(tags):
    if not isinstance(tags, list):
        raise ReconciliationBlocked("taggable AWS resource has no tag inventory")
    result = {}
    for tag in tags:
        if (
            not isinstance(tag, dict)
            or not isinstance(tag.get("Key"), str)
            or not isinstance(tag.get("Value"), str)
            or tag["Key"] in result
        ):
            raise ReconciliationBlocked("AWS ownership tags are malformed")
        result[tag["Key"]] = tag["Value"]
    return result


def require_aws_tags(resource, expected):
    tags = aws_tags(resource.get("Tags"))
    if any(tags.get(key) != value for key, value in expected.items()):
        raise ReconciliationBlocked("recorded AWS resource ownership changed")
    return tags


def observe_recorded_resources(context, inventory, generation, directory):
    deadline = time.monotonic() + 300
    observations = {}

    def query(address, command, flag, identifier, collection, not_found):
        result = read_aws(
            ["ec2", command, flag, identifier],
            deadline=deadline,
            absent_error=not_found,
        )
        if result.get("absent") is True:
            return None
        resources = result.get(collection)
        if not isinstance(resources, list):
            raise ReconciliationBlocked("recorded resource observation is incomplete")
        if not resources:
            return None
        if len(resources) != 1 or not isinstance(resources[0], dict):
            raise ReconciliationBlocked("recorded resource observation is ambiguous")
        return resources[0]

    expected_tags = {**TAGS, "Generation": generation}
    stable_tags = dict(TAGS)
    checks = [
        (
            "aws_vpc.main",
            "describe-vpcs",
            "--vpc-ids",
            "Vpcs",
            "VpcId",
            "InvalidVpcID.NotFound",
            expected_tags,
        ),
        (
            "aws_subnet.host",
            "describe-subnets",
            "--subnet-ids",
            "Subnets",
            "SubnetId",
            "InvalidSubnetID.NotFound",
            expected_tags,
        ),
        (
            "aws_internet_gateway.main",
            "describe-internet-gateways",
            "--internet-gateway-ids",
            "InternetGateways",
            "InternetGatewayId",
            "InvalidInternetGatewayID.NotFound",
            expected_tags,
        ),
        (
            "aws_route_table.host",
            "describe-route-tables",
            "--route-table-ids",
            "RouteTables",
            "RouteTableId",
            "InvalidRouteTableID.NotFound",
            expected_tags,
        ),
        (
            "aws_security_group.host",
            "describe-security-groups",
            "--group-ids",
            "SecurityGroups",
            "GroupId",
            "InvalidGroup.NotFound",
            expected_tags,
        ),
        (
            "aws_launch_template.host",
            "describe-launch-templates",
            "--launch-template-ids",
            "LaunchTemplates",
            "LaunchTemplateId",
            "InvalidLaunchTemplateId.NotFound",
            stable_tags,
        ),
    ]
    for address, command, flag, collection, identity, not_found, tags in checks:
        resource = query(
            address,
            command,
            flag,
            inventory[address],
            collection,
            not_found,
        )
        if resource is None:
            observations[address] = "absent"
            continue
        if resource.get(identity) != inventory[address]:
            raise ReconciliationBlocked("recorded AWS resource ID changed")
        require_aws_tags(resource, tags)
        observations[address] = "present"
        if address == "aws_route_table.host":
            routes = resource.get("Routes")
            associations = resource.get("Associations")
            if not isinstance(routes, list) or not isinstance(associations, list):
                raise ReconciliationBlocked(
                    "route-table relationship observation is incomplete"
                )
            matching_routes = [
                item
                for item in routes
                if item.get("DestinationCidrBlock") == "0.0.0.0/0"
            ]
            if len(matching_routes) > 1:
                raise ReconciliationBlocked("recorded route is ambiguous")
            if (
                matching_routes
                and matching_routes[0].get("GatewayId")
                != inventory["aws_internet_gateway.main"]
            ):
                raise ReconciliationBlocked("recorded route target changed")
            observations["aws_route.outbound"] = (
                "present" if matching_routes else "absent"
            )
            matching_associations = [
                item
                for item in associations
                if item.get("RouteTableAssociationId")
                == inventory["aws_route_table_association.host"]
            ]
            if len(matching_associations) > 1:
                raise ReconciliationBlocked("recorded route association is ambiguous")
            if matching_associations and (
                matching_associations[0].get("RouteTableId")
                != inventory["aws_route_table.host"]
                or matching_associations[0].get("SubnetId")
                != inventory["aws_subnet.host"]
            ):
                raise ReconciliationBlocked("recorded route association changed")
            observations["aws_route_table_association.host"] = (
                "present" if matching_associations else "absent"
            )
    observations.setdefault("aws_route.outbound", "absent")
    observations.setdefault("aws_route_table_association.host", "absent")

    instance_result = read_aws(
        [
            "ec2",
            "describe-instances",
            "--instance-ids",
            inventory["aws_instance.host"],
        ],
        deadline=deadline,
        absent_error="InvalidInstanceID.NotFound",
    )
    if instance_result.get("absent") is True:
        observations["aws_instance.host"] = "absent"
    else:
        reservations = instance_result.get("Reservations")
        if not isinstance(reservations, list):
            raise ReconciliationBlocked("recorded instance observation is incomplete")
        instances = [
            item
            for reservation in reservations
            if isinstance(reservation, dict)
            for item in reservation.get("Instances", [])
        ]
        if not instances:
            observations["aws_instance.host"] = "absent"
        else:
            if (
                len(instances) != 1
                or instances[0].get("InstanceId") != inventory["aws_instance.host"]
            ):
                raise ReconciliationBlocked(
                    "recorded instance observation is ambiguous"
                )
            instance = instances[0]
            status = instance.get("State", {}).get("Name")
            if status == "terminated":
                observations["aws_instance.host"] = "terminated"
            elif status == "running":
                require_aws_tags(instance, expected_tags)
                mappings = instance.get("BlockDeviceMappings")
                interfaces = instance.get("NetworkInterfaces")
                if (
                    not isinstance(mappings, list)
                    or len(mappings) != 1
                    or mappings[0].get("Ebs", {}).get("VolumeId")
                    != inventory["instance_root_volume"]
                    or not isinstance(interfaces, list)
                    or len(interfaces) != 1
                    or interfaces[0].get("NetworkInterfaceId")
                    != inventory["instance_network_interface"]
                ):
                    raise ReconciliationBlocked(
                        "recorded host secondary resources changed"
                    )
                observations["aws_instance.host"] = "present"
            else:
                raise ReconciliationBlocked("recorded host is transitioning or unknown")

    for address, command, flag, identifier, collection, not_found in [
        (
            "instance_root_volume",
            "describe-volumes",
            "--volume-ids",
            inventory["instance_root_volume"],
            "Volumes",
            "InvalidVolume.NotFound",
        ),
        (
            "instance_network_interface",
            "describe-network-interfaces",
            "--network-interface-ids",
            inventory["instance_network_interface"],
            "NetworkInterfaces",
            "InvalidNetworkInterfaceID.NotFound",
        ),
    ]:
        resource = query(address, command, flag, identifier, collection, not_found)
        if resource is None:
            observations[address] = "absent"
            continue
        identity = (
            "VolumeId" if address == "instance_root_volume" else "NetworkInterfaceId"
        )
        if resource.get(identity) != identifier:
            raise ReconciliationBlocked("recorded host secondary resource ID changed")
        require_aws_tags(resource, expected_tags)
        observations[address] = "present"
    if observations.get("instance_network_interface") == "present":
        observations["aws_ec2_tag.network_generation"] = "present"
    else:
        observations["aws_ec2_tag.network_generation"] = "absent"
    if set(observations) != set(inventory):
        raise ReconciliationBlocked("recorded resource observation set is incomplete")
    return observations


def reconcile_destroy_applies(
    context,
    pointer,
    state,
    pointer_etag,
    disposal,
    host_observation,
    next_attempt,
    directory,
):
    generation = pointer["generation"]
    prefix = f"operations/{generation}/destroy-apply-intent-"
    listing = read_aws(
        [
            "s3api",
            "list-objects-v2",
            *location(context),
            "--prefix",
            prefix,
            "--max-keys",
            str(MAX_DISPOSAL_ATTEMPTS + 1),
            "--no-paginate",
        ],
        deadline=time.monotonic() + 60,
    )
    entries = listing.get("Contents", [])
    if (
        listing.get("IsTruncated") is not False
        or not isinstance(entries, list)
        or len(entries) > MAX_DISPOSAL_ATTEMPTS
        or listing.get("KeyCount") != len(entries)
    ):
        raise ReconciliationBlocked("destroy apply history is incomplete")
    apply_attempts = []
    for item in entries:
        key = item.get("Key") if isinstance(item, dict) else None
        match = re.fullmatch(re.escape(prefix) + r"(attempt-[0-9]{4})\.json", key or "")
        if not match:
            raise ReconciliationBlocked("unexpected destroy apply history")
        apply_attempts.append(match.group(1))
    if len(set(apply_attempts)) != len(apply_attempts):
        raise ReconciliationBlocked("duplicate destroy apply attempt")
    if len(apply_attempts) > MAX_DISPOSAL_ATTEMPTS:
        raise ReconciliationBlocked(
            "destroy recovery history requires reviewed recovery"
        )

    attempt_records = {
        f"attempt-{number:04d}": record
        for number, record in disposal_attempt_records(
            context, generation, state, disposal, directory
        )
    }
    recovered_from = []
    for attempt in sorted(apply_attempts):
        apply_intent = existing_event(
            context,
            prefix + attempt + ".json",
            directory / "destroy-apply-intent-prior.json",
        )
        apply_fields = {
            "generation",
            "status",
            "sha256",
            "attempt",
            "state_lineage",
            "state_serial",
            "resource_ids",
            "host_observation_sha256",
        }
        if (
            not isinstance(apply_intent, dict)
            or set(apply_intent) != apply_fields
            or apply_intent.get("generation") != generation
            or apply_intent.get("status") != "applying"
            or apply_intent.get("attempt") != attempt
            or not re.fullmatch(r"[a-f0-9]{64}", apply_intent.get("sha256", ""))
            or apply_intent.get("state_lineage") != disposal["state_lineage"]
            or type(apply_intent.get("state_serial")) is not int
            or apply_intent["state_serial"] < disposal["initial_state_serial"]
            or apply_intent.get("resource_ids") != disposal["resource_ids"]
            or not re.fullmatch(
                r"[a-f0-9]{64}", apply_intent.get("host_observation_sha256", "")
            )
        ):
            raise ReconciliationBlocked("destroy apply intent is malformed or foreign")
        host_record = existing_event(
            context,
            f"operations/{generation}/destroy-host-observation-{attempt}.json",
            directory / "destroy-host-observation-prior.json",
        )
        verify_destroy_host_observation(host_record, apply_intent, disposal, pointer)
        plan = existing_event(
            context,
            f"operations/{generation}/destroy-plan-inspected-{attempt}.json",
            directory / "destroy-plan-inspected-prior.json",
        )
        plan_fields = {
            "generation",
            "status",
            "sha256",
            "attempt",
            "state_lineage",
            "state_serial",
            "resource_ids",
        }
        if (
            not isinstance(plan, dict)
            or set(plan) != plan_fields
            or plan.get("generation") != generation
            or plan.get("status") != "inspected"
            or plan.get("sha256") != apply_intent["sha256"]
            or plan.get("attempt") != attempt
            or plan.get("state_lineage") != apply_intent["state_lineage"]
            or plan.get("state_serial") != apply_intent["state_serial"]
            or plan.get("resource_ids") != disposal["resource_ids"]
        ):
            raise ReconciliationBlocked("destroy apply lacks its exact inspected plan")
        admitted = attempt_records.get(attempt)
        if (
            not isinstance(admitted, dict)
            or admitted.get("state_lineage") != apply_intent["state_lineage"]
            or admitted.get("state_serial") != apply_intent["state_serial"]
            or admitted.get("resource_ids") != disposal["resource_ids"]
        ):
            raise ReconciliationBlocked("destroy apply lacks its exact retry admission")

        terminal_key = f"operations/{generation}/destroy-apply-terminal-{attempt}.json"
        terminal = existing_event(
            context, terminal_key, directory / "destroy-apply-terminal-prior.json"
        )
        recovery_key = f"operations/{generation}/destroy-apply-recovery-{attempt}.json"
        recovery = existing_event(
            context, recovery_key, directory / "destroy-apply-recovery-prior.json"
        )
        if terminal is not None:
            if (
                terminal != {**apply_intent, "status": "applied"}
                or recovery is not None
            ):
                raise ReconciliationBlocked("destroy apply terminal history conflicts")
            continue

        if recovery is None:
            observe_ssm(pointer["host_id"], time.monotonic() + 300)
            resources = observe_recorded_resources(
                context, disposal["resource_ids"], generation, directory
            )
            observed_state, observed_pointer, observed_etag = snapshots(
                context, directory
            )
            if (
                observed_state != state
                or observed_pointer != pointer
                or observed_etag != pointer_etag
            ):
                raise ReconciliationBlocked(
                    "environment changed during exact destroy recovery observation"
                )
            verify_retry_state(observed_state, disposal, pointer)
            if (
                host_observation["status"] == "running"
                and not host_observation["idle_verified"]
            ):
                raise ReconciliationBlocked(
                    "running recorded host is not verified idle"
                )
            state_sha256 = state_digest(observed_state)
            next_name = next_attempt if current_addresses(observed_state) else None
            if current_addresses(observed_state) and next_name is None:
                raise ReconciliationBlocked("partial destroy retry identity is missing")
            recovery = {
                "schema": 1,
                "operation": "destroy-apply-recovery",
                "generation": generation,
                "attempt": attempt,
                "plan_sha256": apply_intent["sha256"],
                "state_lineage": apply_intent["state_lineage"],
                "state_serial": apply_intent["state_serial"],
                "resource_ids": disposal["resource_ids"],
                "observed_state": {
                    "lineage": observed_state["lineage"],
                    "serial": observed_state["serial"],
                    "sha256": state_sha256,
                    "addresses": sorted(current_addresses(observed_state)),
                    "native_lock_absent": True,
                    "ssm_terminal": True,
                    "host_status": host_observation["status"],
                    "host_idle_verified": host_observation["idle_verified"],
                    "resources": resources,
                },
                "next_attempt": next_name,
            }
            put_event_once(
                context,
                generation,
                "destroy-apply-recovery-" + attempt,
                recovery,
                directory,
            )
        else:
            verify_recovery_record(
                recovery,
                generation,
                attempt,
                apply_intent,
                disposal,
            )

        observed = recovery["observed_state"]
        link = {
            "attempt": attempt,
            "plan_sha256": apply_intent["sha256"],
            "state_sha256": observed["sha256"],
            "state_serial": observed["serial"],
        }
        next_name = recovery["next_attempt"]
        if next_name is None:
            if (
                next_attempt is not None
                or state_digest(state) != observed["sha256"]
                or state.get("serial") != observed["serial"]
                or sorted(current_addresses(state)) != observed["addresses"]
            ):
                raise ReconciliationBlocked("empty-state recovery observation changed")
            continue
        next_record = attempt_records.get(next_name)
        if next_record is None:
            if (
                next_attempt != next_name
                or state_digest(state) != observed["sha256"]
                or state.get("serial") != observed["serial"]
                or sorted(current_addresses(state)) != observed["addresses"]
            ):
                raise ReconciliationBlocked("destroy recovery observation is stale")
            recovered_from.append(link)
        elif (
            link not in next_record["recovered_from"]
            or next_record["state_lineage"] != observed["lineage"]
            or next_record["state_serial"] != observed["serial"]
        ):
            raise ReconciliationBlocked(
                "next destroy attempt lacks its recovery binding"
            )
    return recovered_from


def verify_recovery_record(record, generation, attempt, apply_intent, disposal):
    observed = record.get("observed_state") if isinstance(record, dict) else None
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "schema",
            "operation",
            "generation",
            "attempt",
            "plan_sha256",
            "state_lineage",
            "state_serial",
            "resource_ids",
            "observed_state",
            "next_attempt",
        }
        or record.get("schema") != 1
        or record.get("operation") != "destroy-apply-recovery"
        or record.get("generation") != generation
        or record.get("attempt") != attempt
        or record.get("plan_sha256") != apply_intent["sha256"]
        or record.get("state_lineage") != apply_intent["state_lineage"]
        or record.get("state_serial") != apply_intent["state_serial"]
        or record.get("resource_ids") != disposal["resource_ids"]
        or not isinstance(observed, dict)
        or set(observed)
        != {
            "lineage",
            "serial",
            "sha256",
            "addresses",
            "native_lock_absent",
            "ssm_terminal",
            "host_status",
            "host_idle_verified",
            "resources",
        }
        or observed.get("lineage") != disposal["state_lineage"]
        or type(observed.get("serial")) is not int
        or observed["serial"] < apply_intent["state_serial"]
        or not re.fullmatch(r"[a-f0-9]{64}", observed.get("sha256", ""))
        or not isinstance(observed.get("addresses"), list)
        or not set(observed["addresses"]) <= ADDRESSES
        or observed.get("native_lock_absent") is not True
        or observed.get("ssm_terminal") is not True
        or observed.get("host_status") not in {"absent", "terminated", "running"}
        or observed.get("host_idle_verified")
        is not (observed.get("host_status") == "running")
        or not isinstance(observed.get("resources"), dict)
        or set(observed["resources"]) != set(disposal["resource_ids"])
        or any(
            value not in {"present", "absent", "terminated"}
            for value in observed["resources"].values()
        )
        or record.get("next_attempt") is not None
        and not re.fullmatch(r"attempt-[0-9]{4}", record["next_attempt"])
    ):
        raise ReconciliationBlocked("immutable destroy recovery decision is malformed")


def verify_destroy_host_observation(record, apply_intent, disposal, pointer):
    expected = {
        "schema",
        "operation",
        "generation",
        "host_id",
        "attempt",
        "plan_sha256",
        "status",
        "idle_verified",
        "state_lineage",
        "state_serial",
        "state_sha256",
        "resource_ids",
        "ssm_terminal",
    }
    if (
        not isinstance(record, dict)
        or set(record) != expected
        or record.get("schema") != 1
        or record.get("operation") != "destroy-host-observation"
        or record.get("generation") != pointer["generation"]
        or record.get("host_id") != pointer["host_id"]
        or record.get("attempt") != apply_intent["attempt"]
        or record.get("plan_sha256") != apply_intent["sha256"]
        or record.get("status") not in {"absent", "terminated", "running"}
        or record.get("idle_verified") is not (record.get("status") == "running")
        or record.get("state_lineage") != apply_intent["state_lineage"]
        or record.get("state_serial") != apply_intent["state_serial"]
        or not re.fullmatch(r"[a-f0-9]{64}", record.get("state_sha256", ""))
        or record.get("resource_ids") != disposal["resource_ids"]
        or record.get("ssm_terminal") is not True
        or hashlib.sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        != apply_intent["host_observation_sha256"]
    ):
        raise ReconciliationBlocked(
            "destroy apply lacks its bound final host observation"
        )


def state_digest(state):
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def observed_leftovers(context, state, pointer, disposal, directory):
    state_addresses = sorted(current_addresses(state))
    try:
        latest_state, latest_pointer, _ = snapshots(context, directory)
        if latest_pointer != pointer:
            raise ReconciliationBlocked("generation changed during failure observation")
        verify_retry_state(latest_state, disposal, pointer)
        resources = observe_recorded_resources(
            context, disposal["resource_ids"], pointer["generation"], directory
        )
        cloud_addresses = sorted(
            address for address, status in resources.items() if status != "absent"
        )
        addresses = sorted(set(current_addresses(latest_state)) | set(cloud_addresses))
        return {
            "addresses": addresses,
            "count": len(addresses),
            "observation": "complete",
        }
    except (
        ReconciliationBlocked,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
        StopIteration,
        subprocess.SubprocessError,
    ):
        return {
            "addresses": state_addresses,
            "count": len(state_addresses),
            "observation": "unknown-or-denied",
        }


def destroy_plan(context, generation, state, intent, attempt, directory):
    tools = private_tools()
    work = directory / "terraform-destroy"
    work.mkdir(mode=0o700)
    root = ROOT / "infra/aws/environment"
    for name in [
        "main.tf",
        "variables.tf",
        "outputs.tf",
        "versions.tf",
        "backend.tf",
        ".terraform.lock.hcl",
    ]:
        source = root / name
        if source.is_symlink() or not source.is_file():
            raise ReconciliationBlocked("unsafe fixed Terraform input")
        shutil.copyfile(source, work / name)
    plan_path = work / "destroy.tfplan"
    plan_path.touch(mode=0o600)
    plan_path.unlink()
    plan_path = None
    env = {
        **os.environ,
        "TF_VAR_account_id": context["account"],
        "TF_VAR_host_profile": "onlineshop-test-host",
        "TF_VAR_generation": generation,
        "TF_IN_AUTOMATION": "1",
        "TF_LOG": "",
        "AWS_REGION": REGION,
        "AWS_DEFAULT_REGION": REGION,
    }
    deadline = time.monotonic() + DESTROY_LIMIT_SECONDS

    def run(arguments, label):
        return tools.run_private(
            ["terraform", *arguments],
            work,
            label,
            env=env,
            timeout=max(0.01, deadline - time.monotonic()),
        )

    run(
        [
            "init",
            "-input=false",
            "-no-color",
            "-lockfile=readonly",
            "-backend-config=bucket=" + context["bucket"],
        ],
        "destroy-init.log",
    )
    run(
        [
            "plan",
            "-destroy",
            "-input=false",
            "-no-color",
            "-lock-timeout=30s",
            "-out=destroy.tfplan",
        ],
        "destroy-plan.log",
    )
    plan_file = work / "destroy.tfplan"
    if (
        plan_file.is_symlink()
        or not plan_file.is_file()
        or not 0 < plan_file.stat().st_size <= 16 * 1024**2
    ):
        raise ReconciliationBlocked("saved destroy plan is missing or excessive")
    plan_file.chmod(0o600)
    plan = tools.read_json(
        run(["show", "-json", "destroy.tfplan"], "destroy-plan.json")
    )
    planned = plan.get("planned_values", {}).get("root_module", {}).get("resources", [])
    changes = plan.get("resource_changes", [])
    addresses = current_addresses(state)
    if (
        plan.get("errored")
        or planned
        or {item.get("address") for item in changes} != addresses
        or len(changes) != len(addresses)
    ):
        raise ReconciliationBlocked(
            "saved destroy plan is outside the exact current state"
        )
    values = {
        resource["type"] + "." + resource["name"]: resource["instances"][0][
            "attributes"
        ]
        for resource in state["resources"]
    }
    for item in changes:
        address = item["address"]
        change = item.get("change", {})
        before = change.get("before")
        if (
            change.get("actions") != ["delete"]
            or has_unknown(change.get("after_unknown", {}))
            or not isinstance(before, dict)
            or before.get("id") != values[address].get("id")
            or before.get("id") != intent["resource_ids"].get(address)
        ):
            raise ReconciliationBlocked(
                "destroy plan contains a replacement or unowned resource"
            )
        verify_destroy_plan_resource(
            address, before, intent["resource_ids"], generation
        )
    digest = hashlib.sha256(plan_file.read_bytes()).hexdigest()
    inspected = {
        "generation": generation,
        "status": "inspected",
        "sha256": digest,
        "attempt": attempt,
        "state_lineage": state["lineage"],
        "state_serial": state["serial"],
        "resource_ids": intent["resource_ids"],
    }
    put_event_once(
        context, generation, "destroy-plan-inspected-" + attempt, inspected, directory
    )
    return {
        "work": work,
        "plan": plan_file,
        "digest": digest,
        "deadline": deadline,
        "env": env,
        "attempt": attempt,
    }


def destroy_apply(
    context, generation, state, intent, inspected, directory, host_observation, summary
):
    digest = hashlib.sha256(inspected["plan"].read_bytes()).hexdigest()
    if digest != inspected["digest"]:
        raise ReconciliationBlocked("saved destroy plan changed after inspection")
    apply_intent = {
        "generation": generation,
        "status": "applying",
        "sha256": digest,
        "attempt": inspected["attempt"],
        "state_lineage": state["lineage"],
        "state_serial": state["serial"],
        "resource_ids": intent["resource_ids"],
        "host_observation_sha256": hashlib.sha256(
            json.dumps(host_observation, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    put_event_once(
        context,
        generation,
        "destroy-apply-intent-" + inspected["attempt"],
        apply_intent,
        directory,
    )
    summary["apply_outcome"] = "unknown"
    private_tools().run_private(
        [
            "terraform",
            "apply",
            "-input=false",
            "-no-color",
            "-lock-timeout=30s",
            "destroy.tfplan",
        ],
        inspected["work"],
        "destroy-apply.log",
        env=inspected["env"],
        timeout=max(0.01, inspected["deadline"] - time.monotonic()),
    )
    put_event_once(
        context,
        generation,
        "destroy-apply-terminal-" + inspected["attempt"],
        {**apply_intent, "status": "applied"},
        directory,
    )
    summary["apply_outcome"] = "applied"


def put_event_once(context, generation, name, record, directory):
    key = f"operations/{generation}/{name}.json"
    existing = existing_event(context, key, directory / (name + "-existing.json"))
    if existing is None:
        put_json(context, key, record, directory)
        return
    if existing != record:
        raise ReconciliationBlocked("immutable disposal operation history conflicts")


def write_immutable_event(context, generation, name, record, directory):
    put_event_once(context, generation, name, record, directory)


def existing_event(context, key, path):
    listing = read_aws(
        [
            "s3api",
            "list-objects-v2",
            *location(context),
            "--prefix",
            key,
            "--max-keys",
            "2",
            "--no-paginate",
        ],
        deadline=time.monotonic() + 60,
    )
    contents = listing.get("Contents", [])
    if (
        listing.get("IsTruncated") is not False
        or not isinstance(contents, list)
        or len(contents) > 2
        or listing.get("KeyCount") != len(contents)
        or any(
            not isinstance(value, dict) or value.get("Key") != key for value in contents
        )
    ):
        raise ReconciliationBlocked(
            "immutable event absence or identity was not authoritatively listed"
        )
    if not contents:
        return None
    return read_snapshot(location(context), key, path, time.monotonic() + 60)


def disposal_receipt(context, pointer, directory):
    key = f"operations/{pointer['generation']}/dispose-terminal.json"
    receipt = existing_event(context, key, directory / "dispose-terminal.json")
    expected = {
        "schema",
        "operation",
        "generation",
        "host_id",
        "status",
        "account_id",
        "region",
        "root",
        "state_key",
        "state_lineage",
        "state_serial",
        "resource_ids",
        "verified_absence",
    }
    if (
        set(receipt) != expected
        or receipt["schema"] != 1
        or receipt["operation"] != "dispose"
        or receipt["generation"] != pointer["generation"]
        or receipt["host_id"] != pointer["host_id"]
        or receipt["status"] != "disposed"
        or receipt["account_id"] != context["account"]
        or receipt["region"] != REGION
        or receipt["root"] != "infra/aws/environment"
        or receipt["state_key"] != "state/environment.tfstate"
        or receipt["verified_absence"] is not True
    ):
        raise ReconciliationBlocked(
            "disposed pointer lacks its exact verified terminal record"
        )
    validate_inventory_ids(receipt["resource_ids"])
    return receipt


def verify_disposed_generation(context, state, pointer, directory):
    verify_pointer(pointer)
    if pointer["status"] != "disposed":
        raise ReconciliationBlocked(
            "only a recorded disposed generation can be recreated"
        )
    receipt = disposal_receipt(context, pointer, directory)
    if pointer.get("schema") is None:
        migration = {"generation", "host_id", "status", "purpose"}
        if (
            set(pointer) != migration
            or pointer.get("purpose") != "owner-empty-host-generation-migration"
        ):
            raise ReconciliationBlocked(
                "disposed legacy pointer lacks its exact migration purpose"
            )
        original = existing_event(
            context,
            f"operations/{pointer['generation']}/intent.json",
            directory / "recreation-legacy-intent.json",
        )
        if original is not None:
            raise ReconciliationBlocked(
                "legacy migration pointer has unexpected operation history"
            )
    else:
        original = existing_event(
            context,
            f"operations/{pointer['generation']}/intent.json",
            directory / "recreation-pointer-intent.json",
        )
        expected_keys = {
            "schema",
            "generation",
            "host_id",
            "status",
            "predecessor",
            "host_predecessor",
            "request",
            "images",
            "retained",
        }
        if (
            not isinstance(original, dict)
            or set(original) != expected_keys
            or any(
                original.get(key) != pointer.get(key)
                for key in [
                    "schema",
                    "generation",
                    "host_id",
                    "predecessor",
                    "host_predecessor",
                    "request",
                    "images",
                ]
            )
            or original.get("status")
            not in {
                "provisioned",
                "running",
                "failed",
                "completed",
                "recovered-aborted",
            }
            or not isinstance(original.get("retained"), list)
        ):
            raise ReconciliationBlocked(
                "disposed pointer differs from its immutable generation intent"
            )
    intent_key = f"operations/{pointer['generation']}/dispose-intent.json"
    intent = existing_event(
        context, intent_key, directory / "recreation-dispose-intent.json"
    )
    intent_keys = {
        "schema",
        "operation",
        "generation",
        "host_id",
        "account_id",
        "region",
        "root",
        "state_key",
        "state_lineage",
        "initial_state_serial",
        "resource_ids",
        "status",
    }
    if (
        not isinstance(intent, dict)
        or set(intent) != intent_keys
        or intent.get("schema") != 1
        or intent.get("operation") != "dispose"
        or intent.get("generation") != pointer["generation"]
        or intent.get("host_id") != pointer["host_id"]
        or intent.get("account_id") != context["account"]
        or intent.get("region") != REGION
        or intent.get("root") != receipt["root"]
        or intent.get("state_key") != receipt["state_key"]
        or intent.get("state_lineage") != receipt["state_lineage"]
        or type(intent.get("initial_state_serial")) is not int
        or intent["initial_state_serial"] < 1
        or receipt["state_serial"] <= intent["initial_state_serial"]
        or intent.get("status") != "intent"
        or intent.get("resource_ids") != receipt["resource_ids"]
    ):
        raise ReconciliationBlocked(
            "disposed environment lacks matching immutable destroy intent"
        )
    verify_receipt_state(state, receipt)
    verify_resource_absence(context, receipt["resource_ids"], directory)
    return receipt


def verify_empty_state(state, lineage):
    if (
        state.get("version") != 4
        or state.get("lineage") != lineage
        or type(state.get("serial")) is not int
        or state["serial"] < 1
        or state.get("resources") != []
        or state.get("outputs", {}) != {}
    ):
        raise ReconciliationBlocked("retained Terraform state is not verified empty")


def verify_receipt_state(state, receipt):
    verify_empty_state(state, receipt["state_lineage"])
    if state["serial"] != receipt["state_serial"]:
        raise ReconciliationBlocked(
            "retained state serial differs from verified disposal"
        )


def validate_inventory_ids(inventory):
    if not isinstance(inventory, dict) or set(inventory) != set(RESOURCE_PREFIXES) | {
        "instance_network_interface",
        "instance_root_volume",
    }:
        raise ReconciliationBlocked("disposal inventory address set is not exact")
    for address, prefix in RESOURCE_PREFIXES.items():
        value = inventory.get(address)
        if not isinstance(value, str) or not value.startswith(prefix):
            raise ReconciliationBlocked(
                "disposal inventory contains an invalid resource ID"
            )
    if (
        not inventory["instance_network_interface"].startswith("eni-")
        or not inventory["instance_root_volume"].startswith("vol-")
        or inventory["aws_route.outbound"]
        != route_resource_id(inventory["aws_route_table.host"], "0.0.0.0/0")
        or inventory["aws_ec2_tag.network_generation"]
        != inventory["instance_network_interface"] + ",Generation"
    ):
        raise ReconciliationBlocked("disposal secondary resource IDs do not match")


def verify_resource_tags(address, values, generation):
    if address not in TAGGED_ADDRESSES:
        return
    tags = values.get("tags")
    expected = dict(TAGS)
    if address != "aws_launch_template.host":
        expected["Generation"] = generation
    if not isinstance(tags, dict) or any(
        tags.get(key) != value for key, value in expected.items()
    ):
        raise ReconciliationBlocked("environment resource ownership tags mismatch")
    if address == "aws_launch_template.host":
        specifications = values.get("tag_specifications")
        if (
            "Generation" in tags
            or not isinstance(specifications, list)
            or len(specifications) != 3
            or {item.get("resource_type") for item in specifications}
            != {"instance", "volume", "network-interface"}
            or any(item.get("tags") != dict(TAGS) for item in specifications)
        ):
            raise ReconciliationBlocked("launch-template ownership tags mismatch")
    if address == "aws_instance.host":
        volume_tags = values.get("volume_tags")
        if not isinstance(volume_tags, dict) or any(
            volume_tags.get(key) != value for key, value in expected.items()
        ):
            raise ReconciliationBlocked("root-volume ownership tags mismatch")


def verify_destroy_plan_resource(address, before, inventory, generation):
    verify_resource_tags(address, before, generation)
    references = {
        "aws_subnet.host": {"vpc_id": "aws_vpc.main"},
        "aws_internet_gateway.main": {"vpc_id": "aws_vpc.main"},
        "aws_route_table.host": {"vpc_id": "aws_vpc.main"},
        "aws_route.outbound": {
            "route_table_id": "aws_route_table.host",
            "gateway_id": "aws_internet_gateway.main",
        },
        "aws_route_table_association.host": {
            "route_table_id": "aws_route_table.host",
            "subnet_id": "aws_subnet.host",
        },
        "aws_security_group.host": {"vpc_id": "aws_vpc.main"},
    }
    if any(
        before.get(field) != inventory[reference]
        for field, reference in references.get(address, {}).items()
    ):
        raise ReconciliationBlocked("destroy plan resource relationship changed")
    if address == "aws_route.outbound" and (
        before.get("destination_cidr_block") != "0.0.0.0/0"
        or before.get("id")
        != route_resource_id(inventory["aws_route_table.host"], "0.0.0.0/0")
    ):
        raise ReconciliationBlocked("destroy plan route identity changed")
    if (
        address == "aws_route_table_association.host"
        and before.get("id") != inventory["aws_route_table_association.host"]
    ):
        raise ReconciliationBlocked("destroy plan association identity changed")
    if address == "aws_ec2_tag.network_generation" and any(
        before.get(key) != value
        for key, value in {
            "resource_id": inventory["instance_network_interface"],
            "key": "Generation",
            "value": generation,
        }.items()
    ):
        raise ReconciliationBlocked("destroy plan ENI tag identity changed")
    if address == "aws_instance.host":
        volumes = before.get("root_block_device")
        templates = before.get("launch_template")
        if (
            before.get("primary_network_interface_id")
            != inventory["instance_network_interface"]
            or not isinstance(volumes, list)
            or len(volumes) != 1
            or volumes[0].get("volume_id") != inventory["instance_root_volume"]
            or before.get("subnet_id") != inventory["aws_subnet.host"]
            or before.get("vpc_security_group_ids")
            != [inventory["aws_security_group.host"]]
            or not isinstance(templates, list)
            or len(templates) != 1
            or templates[0].get("id") != inventory["aws_launch_template.host"]
        ):
            raise ReconciliationBlocked("destroy plan host secondary identity changed")


def route_resource_id(route_table_id, destination):
    # aws_route uses its table ID plus Terraform SDK hashcode.String(destination).
    return f"r-{route_table_id}{zlib.crc32(destination.encode()) & 0xFFFFFFFF}"


def verify_resource_absence(context, inventory, directory):
    validate_inventory_ids(inventory)
    checks = [
        (
            "ec2",
            "describe-instances",
            "--instance-ids",
            inventory["aws_instance.host"],
            "InvalidInstanceID.NotFound",
        ),
        (
            "ec2",
            "describe-volumes",
            "--volume-ids",
            inventory["instance_root_volume"],
            "InvalidVolume.NotFound",
        ),
        (
            "ec2",
            "describe-network-interfaces",
            "--network-interface-ids",
            inventory["instance_network_interface"],
            "InvalidNetworkInterfaceID.NotFound",
        ),
        (
            "ec2",
            "describe-vpcs",
            "--vpc-ids",
            inventory["aws_vpc.main"],
            "InvalidVpcID.NotFound",
        ),
        (
            "ec2",
            "describe-subnets",
            "--subnet-ids",
            inventory["aws_subnet.host"],
            "InvalidSubnetID.NotFound",
        ),
        (
            "ec2",
            "describe-internet-gateways",
            "--internet-gateway-ids",
            inventory["aws_internet_gateway.main"],
            "InvalidInternetGatewayID.NotFound",
        ),
        (
            "ec2",
            "describe-route-tables",
            "--route-table-ids",
            inventory["aws_route_table.host"],
            "InvalidRouteTableID.NotFound",
        ),
        (
            "ec2",
            "describe-security-groups",
            "--group-ids",
            inventory["aws_security_group.host"],
            "InvalidGroup.NotFound",
        ),
        (
            "ec2",
            "describe-launch-templates",
            "--launch-template-ids",
            inventory["aws_launch_template.host"],
            "InvalidLaunchTemplateId.NotFound",
        ),
    ]
    collections = {
        "describe-instances": "Reservations",
        "describe-volumes": "Volumes",
        "describe-network-interfaces": "NetworkInterfaces",
        "describe-vpcs": "Vpcs",
        "describe-subnets": "Subnets",
        "describe-internet-gateways": "InternetGateways",
        "describe-route-tables": "RouteTables",
        "describe-security-groups": "SecurityGroups",
        "describe-launch-templates": "LaunchTemplates",
    }
    for service, command, flag, identifier, not_found in checks:
        result = read_aws(
            [service, command, flag, identifier],
            deadline=time.monotonic() + 60,
            absent_error=not_found,
        )
        if result.get("absent") is True:
            continue
        resources = result.get(collections[command])
        if resources == []:
            continue
        if (
            command == "describe-instances"
            and result.get("Reservations")
            and all(
                instance.get("State", {}).get("Name") == "terminated"
                for reservation in result["Reservations"]
                for instance in reservation.get("Instances", [])
            )
        ):
            continue
        raise ReconciliationBlocked("recorded disposable resource is still present")


def has_unknown(value):
    if isinstance(value, dict):
        return any(has_unknown(child) for child in value.values())
    if isinstance(value, list):
        return any(has_unknown(child) for child in value)
    if type(value) is not bool:
        raise ReconciliationBlocked("destroy plan has malformed unknown-value flags")
    return value
