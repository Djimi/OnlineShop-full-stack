"""One locked validation session, not a general automation engine.

Admit the existing owned host, reconcile immutable operation records and idle host
proof, persist the next generation, inspect/apply a private tag-only saved plan,
transport trusted runtime inputs, verify bound stages/reports/cleanup, then publish
only the current attempt's success. Crash gaps stay unknown until unique terminal
SSM discovery and fresh host absence proof. Missing/disposed state requires Task 7;
never force-unlock, replace the host, reconstruct state or infer recovered success.
"""

import functools
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

from aws_cloud_reconciliation import (
    ADDRESSES,
    REGION,
    ReconciliationBlocked,
    observe_ssm,
    read_aws,
    read_snapshot,
    unique_fields,
    verify_host,
    verify_lock_absent,
    verify_state,
)

ROOT = Path(__file__).resolve().parents[1]
POINTER = "operations/current-generation.json"
TERMINAL = {"Success", "Failed", "Cancelled", "TimedOut"}
FILES = ("run-stack.py", "compose.yml", "host-setup.sh", "host-session.py")
INPUTS = (
    "main.tf",
    "variables.tf",
    "outputs.tf",
    "versions.tf",
    "backend.tf",
    ".terraform.lock.hcl",
)
GENERATION = re.compile(r"run-[1-9][0-9]*-attempt-[1-9][0-9]*")


def validate_session(request, receipt, fixtures, output, *, recheck, verify_reports):
    if output.is_symlink():
        raise ReconciliationBlocked("unsafe public evidence directory")
    output.mkdir(mode=0o700, exist_ok=True)
    summary = {
        "status": "failed",
        "stage": "admission",
        "aws_validation_success": False,
        "candidate_sha": request["candidate_sha"],
        "validation_run_id": request["validation_run_id"],
        "validation_run_attempt": request["validation_run_attempt"],
    }
    try:
        with tempfile.TemporaryDirectory(
            prefix="aws-session-", dir=os.environ["RUNNER_TEMP"]
        ) as temporary:
            directory = Path(temporary)
            context = trusted_inputs(request, directory)
            recheck()
            state, pointer, etag = snapshots(context, directory)
            generation = f"run-{request['validation_run_id']}-attempt-{request['validation_run_attempt']}"
            if generation == pointer["generation"]:
                raise ReconciliationBlocked(
                    "duplicate generation requires a new authorized attempt"
                )
            summary["stage"] = "reconciliation"
            recreate = pointer.get("status") == "disposed"
            if recreate:
                from aws_disposal_orchestration import verify_disposed_generation

                disposal = verify_disposed_generation(
                    context, state, pointer, directory
                )
                previous = {
                    "generation": pointer["generation"],
                    "host_id": pointer["host_id"],
                }
                observed = {"host_generation": pointer["generation"]}
            else:
                previous, _state_generation = admit_state(context, state, pointer)
                observe_ssm(pointer["host_id"], time.monotonic() + 300)
                reconcile_operations(context, pointer, directory)
                # This recovery probe belongs to the existing generation. It cannot reset,
                # remove tests, read secrets or turn a failed attempt into success.
                host_predecessor = pointer.get("host_predecessor", {}).get("generation")
                probe = host_probe(pointer["generation"], host_predecessor)
                observed = remote(
                    context, pointer, "recovery-" + generation, probe, 60, directory
                )
                verify_idle(
                    observed,
                    pointer["generation"],
                    host_predecessor,
                )
            if pointer.get("schema") == 1 and pointer["status"] in {
                "running",
                "failed",
            }:
                event(
                    context,
                    pointer["generation"],
                    "recovered-aborted",
                    {
                        "generation": pointer["generation"],
                        "host_id": pointer["host_id"],
                        "status": "recovered-aborted",
                        "recovery_generation": generation,
                        "ssm_terminal": True,
                        "host_absence_verified": True,
                        "aws_validation_success": False,
                    },
                    directory,
                    allow_existing=True,
                )
            recheck()
            current = {
                "schema": 2 if recreate else 1,
                "generation": generation,
                "host_id": pointer["host_id"],
                "status": "provisioning" if recreate else "running",
                "predecessor": previous,
                "host_predecessor": {
                    "generation": observed["host_generation"],
                    "host_id": pointer["host_id"],
                },
                "request": request,
                "images": receipt["images"],
                "retained": retained_predecessors(pointer),
            }
            if recreate:
                current["recreation"] = {
                    "generation": pointer["generation"],
                    "host_id": pointer["host_id"],
                    "state_lineage": disposal["state_lineage"],
                    "state_serial": disposal["state_serial"],
                    "resource_ids_sha256": hashlib.sha256(
                        json.dumps(disposal["resource_ids"], sort_keys=True).encode()
                    ).hexdigest(),
                }
            if recreate:
                event(
                    context,
                    generation,
                    "recreation-intent",
                    {
                        key: current[key]
                        for key in [
                            "generation",
                            "host_id",
                            "recreation",
                            "request",
                            "images",
                        ]
                    },
                    directory,
                )
            else:
                event(context, generation, "intent", current, directory)
            etag = put_json(context, POINTER, current, directory, etag=etag)
            summary.update(generation=generation, stage="image-retention")
            protect_images(context, receipt["images"], generation, directory)
            summary["stage"] = "provision"
            event(
                context,
                generation,
                "terraform-intent",
                {"generation": generation, "status": "launching"},
                directory,
            )
            if recreate:
                applied, current, etag = create_after_disposal(
                    context, generation, current, etag, directory
                )
            else:
                plan_and_apply(context, generation, directory)
                event(
                    context,
                    generation,
                    "terraform-terminal",
                    {"generation": generation, "status": "passed"},
                    directory,
                )
                # Native state lock must be released and actual tags/state must agree.
                applied, latest, latest_etag = snapshots(context, directory)
                if latest != current or latest_etag != etag:
                    raise ReconciliationBlocked(
                        "generation changed during provisioning"
                    )
            verify_state(applied, {**current, "status": "provisioned"})
            verify_host(current["host_id"], generation, time.monotonic() + 300)
            if recreate:
                wait_for_ssm(current["host_id"], time.monotonic() + 300)
                install_recreated_host(
                    context, current, previous["generation"], directory
                )
                probe = remote(
                    context,
                    current,
                    "recreation-probe",
                    host_probe(generation, previous["generation"]),
                    60,
                    directory,
                )
                verify_idle(probe, generation, previous["generation"])
                observed = {"host_generation": previous["generation"]}
            recheck()
            summary["stage"] = "runtime"
            binding, bundle = runtime_bundle(
                context,
                current,
                receipt,
                fixtures,
                directory,
                observed["host_generation"],
            )
            key = "operations/runtime-input/" + generation + "/bundle.tar"
            put_file(context, key, bundle, directory)
            body = runtime_bootstrap(context, key, bundle, binding)
            remote_outcome = remote(context, current, "runtime", body, 2100, directory)
            summary["remote_status"] = remote_outcome["Status"]
            summary["stage"] = "evidence"
            envelope, reports = collect_evidence(context, binding, directory)
            sanitized_reports(reports, output / "reports.tar")
            if (
                remote_outcome.get("Status") != "Success"
                or remote_outcome.get("ResponseCode") != 0
            ):
                raise ReconciliationBlocked(
                    "runtime SSM invocation did not succeed; sanitized evidence retained"
                )
            verify_envelope(envelope, binding, reports)
            suites = verify_reports(reports)
            # Final evidence is sanitized by constructing XML from fixed suite/case
            # identities, never copying arbitrary candidate XML to public artifacts.
            summary.update(
                status="pending-success",
                stage="evidence-retained-pending-success",
                tests=sum(suites.values()),
                suites=suites,
                stages=["provision", "reset", "readiness", "e2e", "reports"],
                cleanup_verified=True,
                runtime_provenance_verified=True,
                durable_terminal=True,
            )
            current["status"] = "completed"
            event(
                context,
                generation,
                "terminal",
                {
                    **current,
                    "status": "passed",
                    "reports_sha256": envelope["reports_sha256"],
                },
                directory,
            )
            etag = put_json(context, POINTER, current, directory, etag=etag)
            release_previous_retention(context, current["retained"], directory)
            current["retained"] = []
            etag = put_json(context, POINTER, current, directory, etag=etag)
            recheck()
            (output / "admission.json").unlink(missing_ok=True)
    except ReconciliationBlocked:
        raise
    except private_tools().ProofFailed as error:
        summary["error_labels"] = error.error_labels
        raise ReconciliationBlocked(
            "locked protected process failed at "
            + summary["stage"]
            + "; allowlisted labels retained in evidence"
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
        tarfile.TarError,
        ET.ParseError,
    ):
        # Private process exceptions can carry raw AWS/Terraform data. Publish only
        # this controller-owned stage; never a traceback or an external error body.
        raise ReconciliationBlocked(
            "locked session failed at "
            + summary["stage"]
            + "; reconcile before another authorized attempt"
        ) from None
    finally:
        # Fixed identity/stage/counts only, even when protected tools fail.
        (output / "summary.json").write_text(json.dumps(summary, indent=2))


def trusted_inputs(request, directory):
    account = os.environ.get("AWS_TESTING_ACCOUNT_ID", "")
    bucket = os.environ.get("AWS_TESTING_STATE_BUCKET", "")
    secret = os.environ.get("AWS_TESTING_SECRET_ARN", "")
    if (
        not re.fullmatch(r"[0-9]{12}", account)
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", bucket)
        or not re.fullmatch(
            re.escape(
                f"arn:aws:secretsmanager:{REGION}:{account}:secret:onlineshop-test/credentials-"
            )
            + r"[A-Za-z0-9_-]+",
            secret,
        )
    ):
        raise ReconciliationBlocked("invalid fixed bootstrap identifiers")
    verify_controller(request)
    identity = read_aws(["sts", "get-caller-identity"], deadline=time.monotonic() + 60)
    if identity.get("Account") != account or not identity.get("Arn", "").startswith(
        f"arn:aws:sts::{account}:assumed-role/onlineshop-test-operator/"
    ):
        raise ReconciliationBlocked("wrong operator account or role")
    return {"account": account, "bucket": bucket, "secret": secret}


def verify_controller(request):
    paths = [
        "scripts/aws-validation.py",
        "scripts/aws_runtime_orchestration.py",
        "scripts/aws_cloud_reconciliation.py",
        "scripts/aws-operator-plan-proof.py",
        ".github/workflows/aws-validation.yml",
        *["infra/aws/runtime/" + name for name in FILES],
        *["infra/aws/environment/" + name for name in INPUTS],
    ]
    for command, expected in [
        (["git", "rev-parse", "HEAD"], request["controller_sha"].encode() + b"\n"),
        (["git", "diff", request["controller_sha"], "--", *paths], b""),
    ]:
        result = subprocess.run(
            command, cwd=ROOT, capture_output=True, timeout=30, check=False
        )
        if result.returncode or result.stdout != expected:
            raise ReconciliationBlocked("modified or untrusted controller inputs")


def location(context):
    return [
        "--bucket",
        context["bucket"],
        "--expected-bucket-owner",
        context["account"],
    ]


def snapshots(context, directory):
    deadline = time.monotonic() + 300
    verify_lock_absent(location(context), deadline)

    def snapshot(key, label):
        return read_snapshot(
            location(context), key, directory / (label + ".json"), deadline
        )

    pointer = snapshot(POINTER, "pointer")
    head = read_aws(
        ["s3api", "head-object", *location(context), "--key", POINTER],
        deadline=deadline,
    )
    if snapshot(POINTER, "pointer-confirmed") != pointer:
        raise ReconciliationBlocked("generation changed while reading pointer")
    return snapshot("state/environment.tfstate", "state"), pointer, head["ETag"]


def admit_state(context, state, pointer):
    if pointer.get("status") == "disposed":
        raise ReconciliationBlocked(
            "disposed-state recreation requires Task 7; no mutation authorized"
        )
    if pointer.get("schema") is None:
        legacy = {"generation", "host_id", "status"}
        migration = legacy | {"purpose"}
        if set(pointer) == migration:
            if pointer.get("purpose") != "owner-empty-host-generation-migration":
                raise ReconciliationBlocked("unsupported initial generation purpose")
        elif set(pointer) != legacy:
            raise ReconciliationBlocked("unsupported initial generation schema")
        if pointer.get("status") != "provisioned":
            raise ReconciliationBlocked("unsupported initial generation status")
        generation, _ = verify_state(state, pointer)
    else:
        if (
            set(pointer)
            != {
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
            or pointer.get("schema") != 1
            or pointer.get("status")
            not in {"running", "failed", "completed", "recovered-aborted"}
        ):
            raise ReconciliationBlocked("unrecognized routine generation")
        if not GENERATION.fullmatch(pointer["generation"]):
            raise ReconciliationBlocked("invalid generation identity")
        predecessor = pointer["predecessor"]
        host_predecessor = pointer["host_predecessor"]
        if (
            set(predecessor) != {"generation", "host_id"}
            or predecessor["host_id"] != pointer["host_id"]
            or not GENERATION.fullmatch(predecessor["generation"])
            or set(host_predecessor) != {"generation", "host_id"}
            or host_predecessor["host_id"] != pointer["host_id"]
            or not GENERATION.fullmatch(host_predecessor["generation"])
            or pointer["generation"]
            != f"run-{pointer['request']['validation_run_id']}-attempt-{pointer['request']['validation_run_attempt']}"
        ):
            raise ReconciliationBlocked("routine request/predecessor identity mismatch")
        generation = next(
            r["instances"][0]["attributes"]["tags"]["Generation"]
            for r in state["resources"]
            if r["type"] == "aws_instance"
        )
        allowed = {pointer["generation"]}
        if pointer["status"] in {"running", "failed", "recovered-aborted"}:
            allowed.add(pointer["predecessor"]["generation"])
        if generation not in allowed:
            raise ReconciliationBlocked(
                "completed state mismatch or unknown predecessor crash gap"
            )
        verify_state(
            state,
            {
                "generation": generation,
                "host_id": pointer["host_id"],
                "status": "provisioned",
            },
        )
    verify_host(pointer["host_id"], generation, time.monotonic() + 300)
    return {"generation": generation, "host_id": pointer["host_id"]}, generation


@functools.cache
def private_tools():
    spec = importlib.util.spec_from_file_location(
        "operator_private_capture", ROOT / "scripts/aws-operator-plan-proof.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mutate(context, arguments, directory):
    # Reuse the independently bounded streamed capture, never the owner profile wrapper.
    tools = private_tools()
    serial = len(list(directory.glob("mutation-*.json")))
    path = tools.run_private(
        ["aws", *arguments, "--region", REGION, "--output", "json"],
        directory,
        f"mutation-{serial}.json",
        timeout=60,
        env={
            k: v
            for k, v in os.environ.items()
            if k
            not in {
                "GH_TOKEN",
                "GITHUB_TOKEN",
                "AWS_PROFILE",
                "AWS_CONFIG_FILE",
                "AWS_SHARED_CREDENTIALS_FILE",
            }
        }
        | {"AWS_PAGER": ""},
    )
    return tools.read_json(path)


def put_file(context, key, path, directory, *, etag=None):
    result = mutate(
        context,
        [
            "s3api",
            "put-object",
            *location(context),
            "--key",
            key,
            "--body",
            str(path),
            "--server-side-encryption",
            "AES256",
            *(["--if-match", etag] if etag else ["--if-none-match", "*"]),
        ],
        directory,
    )
    if not result.get("ETag") or not result.get("VersionId"):
        raise ReconciliationBlocked(
            "conditional operation persistence did not return version identity"
        )
    return result["ETag"]


def put_json(context, key, record, directory, *, etag=None):
    path = directory / ("write-" + str(len(list(directory.glob("write-*")))) + ".json")
    path.write_text(json.dumps(record, sort_keys=True))
    path.chmod(0o600)
    return put_file(context, key, path, directory, etag=etag)


def event(context, generation, stage, record, directory, *, allow_existing=False):
    key = "operations/" + generation + "/" + stage + ".json"
    if allow_existing:
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
        if listing.get("IsTruncated") is not False:
            raise ReconciliationBlocked("unbounded recovery event listing")
        if any(item["Key"] == key for item in listing.get("Contents", [])):
            prior = read_snapshot(
                location(context),
                key,
                directory / "prior-recovery.json",
                time.monotonic() + 60,
            )
            if (
                prior.get("generation") != generation
                or prior.get("status") != "recovered-aborted"
                or prior.get("host_absence_verified") is not True
            ):
                raise ReconciliationBlocked("invalid prior recovery outcome")
            return
    put_json(context, key, record, directory)


def discover(context, pointer, entry):
    deadline = time.monotonic() + 300
    base = [
        "ssm",
        "list-commands",
        "--instance-id",
        pointer["host_id"],
        "--max-results",
        "50",
        "--no-paginate",
    ]
    arguments, tokens, matches = base, set(), []
    for _ in range(20):
        result = read_aws(arguments, deadline=deadline)
        commands = result["Commands"]
        if not isinstance(commands, list) or len(commands) > 50:
            raise ReconciliationBlocked("unbounded remote discovery")
        matches += [c for c in commands if c.get("Comment") == entry["comment"]]
        token = result.get("NextToken")
        if not token:
            break
        if not isinstance(token, str) or len(token) > 1024 or token in tokens:
            raise ReconciliationBlocked("incomplete remote discovery")
        tokens.add(token)
        arguments = [*base, "--next-token", token]
    else:
        raise ReconciliationBlocked("remote discovery exceeded page bound")
    if len(matches) != 1:
        raise ReconciliationBlocked(
            "uncertain send has zero or duplicate remote matches"
        )
    command = matches[0]
    parameters = command.get("Parameters", {})
    body = parameters.get("commands")
    if (
        command.get("DocumentName") != "AWS-RunShellScript"
        or command.get("InstanceIds") != [pointer["host_id"]]
        or not isinstance(body, list)
        or len(body) != 1
        or not isinstance(body[0], str)
        or hashlib.sha256(body[0].encode()).hexdigest() != entry["sha256"]
        or parameters.get("executionTimeout") != [str(entry["timeout"])]
        or not re.fullmatch(
            r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", command.get("CommandId", "")
        )
    ):
        raise ReconciliationBlocked(
            "remote document/target/body/timeout identity mismatch"
        )
    return command["CommandId"]


def reconcile_operations(context, pointer, directory):
    prefix = "operations/" + pointer["generation"] + "/"
    result = read_aws(
        [
            "s3api",
            "list-objects-v2",
            *location(context),
            "--prefix",
            prefix,
            "--max-keys",
            "100",
            "--no-paginate",
        ],
        deadline=time.monotonic() + 60,
    )
    entries = result.get("Contents", [])
    if (
        result.get("IsTruncated") is not False
        or len(entries) > 100
        or result.get("KeyCount") != len(entries)
    ):
        raise ReconciliationBlocked("persistent operation inventory is incomplete")
    keys = {entry["Key"] for entry in entries}
    if pointer.get("schema") == 1 and prefix + "intent.json" not in keys:
        raise ReconciliationBlocked("routine generation has no immutable intent")
    if pointer.get("schema") == 1:
        intent = read_snapshot(
            location(context),
            prefix + "intent.json",
            directory / "prior-intent.json",
            time.monotonic() + 60,
        )
        if any(
            intent.get(key) != pointer[key]
            for key in [
                "schema",
                "generation",
                "host_id",
                "predecessor",
                "host_predecessor",
                "request",
                "images",
            ]
        ):
            raise ReconciliationBlocked(
                "mutable pointer differs from immutable accepted intent"
            )
    for key in sorted(keys):
        if not key.startswith(prefix) or not key.endswith(".json"):
            raise ReconciliationBlocked("unexpected operation record")
        if not key.endswith("-intent.json") or key.endswith("terraform-intent.json"):
            continue
        entry = read_snapshot(
            location(context),
            key,
            directory / "reconcile-entry.json",
            time.monotonic() + 60,
        )
        if entry.get("comment") is None:
            # Durable controller-side operation intents (Terraform/disposal) do not
            # represent SSM commands and must never be sent through discovery.
            continue
        if (
            entry.get("host_id") != pointer["host_id"]
            or entry.get("generation") != pointer["generation"]
        ):
            raise ReconciliationBlocked("foreign persistent remote operation")
        command_id = discover(context, pointer, entry)
        launched_key = key.removesuffix("-intent.json") + "-launched.json"
        if launched_key in keys:
            launched = read_snapshot(
                location(context),
                launched_key,
                directory / "reconcile-launched.json",
                time.monotonic() + 60,
            )
            if (
                launched.get("command_id") != command_id
                or launched.get("sha256") != entry["sha256"]
            ):
                raise ReconciliationBlocked("recorded and discovered remote IDs differ")
        invocation = read_aws(
            [
                "ssm",
                "get-command-invocation",
                "--command-id",
                command_id,
                "--instance-id",
                pointer["host_id"],
            ],
            deadline=time.monotonic() + 60,
        )
        if invocation.get("Status") not in TERMINAL:
            raise ReconciliationBlocked("previous invocation is not terminal")
        if launched_key not in keys:
            put_json(
                context,
                launched_key,
                {**entry, "command_id": command_id, "discovered": True},
                directory,
            )
        terminal_key = key.removesuffix("-intent.json") + "-terminal.json"
        if terminal_key not in keys:
            put_json(
                context,
                terminal_key,
                {
                    **entry,
                    "command_id": command_id,
                    "status": invocation["Status"],
                    "recovered": True,
                },
                directory,
            )


def remote(context, pointer, stage, body, timeout, directory):
    entry = {
        "generation": pointer["generation"],
        "host_id": pointer["host_id"],
        "comment": pointer["generation"] + ":" + stage,
        "sha256": hashlib.sha256(body.encode()).hexdigest(),
        "timeout": timeout,
    }
    event(context, pointer["generation"], stage + "-intent", entry, directory)
    response = mutate(
        context,
        [
            "ssm",
            "send-command",
            "--document-name",
            "AWS-RunShellScript",
            "--instance-ids",
            pointer["host_id"],
            "--comment",
            entry["comment"],
            "--timeout-seconds",
            "60",
            "--parameters",
            json.dumps({"commands": [body], "executionTimeout": [str(timeout)]}),
        ],
        directory,
    )
    command_id = response["Command"]["CommandId"]
    if not re.fullmatch(r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}", command_id):
        raise ReconciliationBlocked("invalid launched command identity")
    event(
        context,
        pointer["generation"],
        stage + "-launched",
        {**entry, "command_id": command_id},
        directory,
    )
    deadline = time.monotonic() + timeout + 60
    while time.monotonic() < deadline:
        invocation = read_aws(
            [
                "ssm",
                "get-command-invocation",
                "--command-id",
                command_id,
                "--instance-id",
                pointer["host_id"],
            ],
            deadline=deadline,
        )
        if invocation.get("Status") in TERMINAL:
            event(
                context,
                pointer["generation"],
                stage + "-terminal",
                {**entry, "command_id": command_id, "status": invocation["Status"]},
                directory,
            )
            if stage == "runtime":
                return invocation
            if invocation["Status"] != "Success" or invocation.get("ResponseCode") != 0:
                raise ReconciliationBlocked(
                    "fixed remote operation failed; reconcile before another attempt"
                )
            return json.loads(
                invocation.get("StandardOutputContent", ""),
                object_pairs_hook=unique_fields,
            )
        time.sleep(3)
    raise ReconciliationBlocked("remote outcome unknown; no automatic cancel or retry")


def host_probe(generation, predecessor):
    allowed = [generation] + ([predecessor] if predecessor else [])
    code = """import fcntl,importlib.util,json,os,pathlib,subprocess
p=pathlib.Path('/opt/onlineshop-test/.runtime')
assert p.is_dir() and not p.is_symlink() and p.stat().st_uid==0
with open(p/'host.lock','a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
  current=json.loads((p/'current-generation.json').read_bytes())
  assert set(current)=={'generation'} and current['generation'] in ALLOWED
  session=p.parent/'host-session.py'
  if session.exists():
   spec=importlib.util.spec_from_file_location('trusted_admission',session);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
   module.admission_operation(current['generation'])
  else:
   op=p/'operation.json'
   if op.exists():
    prior=json.loads(op.read_bytes());assert prior['generation']==current['generation'] and prior['status'] in {'running','unknown','passed','failed','cancelled','recovered-aborted'}
  tests=subprocess.run(['docker','ps','-aq','--filter','name=^onlineshop-test-e2e$'],capture_output=True,timeout=10,check=True)
  assert not tests.stdout.strip()
  processes=[path for path in pathlib.Path('/proc').iterdir() if path.name.isdigit()]
  assert len(processes)<=4096
  watched={b'/opt/onlineshop-test/run-stack.py',b'/opt/onlineshop-test/host-session.py',b'/opt/onlineshop-test/compose.yml'}
  for process in processes:
   try:
    with open(process/'cmdline','rb') as data:tokens=data.read(65537).split(bytes([0]))
   except FileNotFoundError:continue
   assert not watched.intersection(tokens)
  assert not any((p/name).exists() for name in ['run.env','e2e.env','docker-config/config.json'])
  print(json.dumps({'generation':GENERATION,'host_generation':current['generation'],'host_lock_acquired':True,'test_container_absent':True,'temporary_credentials_absent':True}))
""".replace("ALLOWED", repr(allowed)).replace("GENERATION", repr(generation))
    return (
        "set -eu; timeout --signal=TERM --kill-after=10 45 python3 - <<'PY'\n"
        + code
        + "PY"
    )


def verify_idle(observation, generation, predecessor):
    if (
        set(observation)
        != {
            "generation",
            "host_generation",
            "host_lock_acquired",
            "test_container_absent",
            "temporary_credentials_absent",
        }
        or observation["generation"] != generation
        or observation["host_generation"] not in {generation, predecessor}
        or any(
            observation[key] is not True
            for key in [
                "host_lock_acquired",
                "test_container_absent",
                "temporary_credentials_absent",
            ]
        )
    ):
        raise ReconciliationBlocked(
            "host lock/process/test/temporary credential absence not proved"
        )


def protect_images(context, images, generation, directory):
    for name, uri in sorted(images.items()):
        digest = uri.split("@", 1)[1]
        source = mutate(
            context,
            [
                "ecr",
                "batch-get-image",
                "--registry-id",
                context["account"],
                "--repository-name",
                "onlineshop-test-" + name,
                "--image-ids",
                "imageDigest=" + digest,
            ],
            directory,
        )
        found = source.get("images", [])
        if (
            source.get("failures")
            or len(found) != 1
            or found[0]["imageId"]["imageDigest"] != digest
        ):
            raise ReconciliationBlocked("active image protection source mismatch")
        manifest = directory / ("retain-" + name + ".json")
        manifest.write_text(found[0]["imageManifest"])
        result = mutate(
            context,
            [
                "ecr",
                "put-image",
                "--registry-id",
                context["account"],
                "--repository-name",
                "onlineshop-test-" + name,
                "--image-tag",
                "active-" + generation,
                "--image-digest",
                digest,
                "--image-manifest",
                "file://" + str(manifest),
            ],
            directory,
        )
        if result["image"]["imageId"]["imageDigest"] != digest:
            raise ReconciliationBlocked("active image protection digest mismatch")


def retained_predecessors(pointer):
    if pointer.get("schema") != 1:
        return []  # Never remove owner or other unknown active tags.
    retained = pointer.get("retained", [])
    if not isinstance(retained, list) or len(retained) >= 20:
        raise ReconciliationBlocked(
            "retained failed-generation history requires reviewed recovery"
        )
    generations = [item["generation"] for item in retained]
    if (
        len(set(generations)) != len(generations)
        or pointer["generation"] in generations
    ):
        raise ReconciliationBlocked("duplicate retained-generation identity")
    return [
        *retained,
        {"generation": pointer["generation"], "images": pointer["images"]},
    ]


def release_previous_retention(context, retained, directory):
    for previous in retained:
        if set(previous) != {"generation", "images"} or not GENERATION.fullmatch(
            previous["generation"]
        ):
            raise ReconciliationBlocked("previous retention generation is invalid")
        images = previous["images"]
        if set(images) != {"auth", "items", "gateway", "frontend", "e2e"}:
            raise ReconciliationBlocked(
                "previous deployed retention identity is missing"
            )
        for name, uri in sorted(images.items()):
            prefix = f"{context['account']}.dkr.ecr.{REGION}.amazonaws.com/onlineshop-test-{name}@sha256:"
            if not isinstance(uri, str) or not re.fullmatch(
                re.escape(prefix) + r"[a-f0-9]{64}", uri
            ):
                raise ReconciliationBlocked(
                    "previous deployed retention identity is invalid"
                )
            result = mutate(
                context,
                [
                    "ecr",
                    "batch-delete-image",
                    "--registry-id",
                    context["account"],
                    "--repository-name",
                    "onlineshop-test-" + name,
                    "--image-ids",
                    "imageTag=active-" + previous["generation"],
                ],
                directory,
            )
            if any(
                failure.get("failureCode") != "ImageNotFound"
                for failure in result.get("failures", [])
            ):
                raise ReconciliationBlocked(
                    "previous active-tag release failed; incoming images stay protected"
                )


def plan_and_apply(context, generation, directory):
    tools = private_tools()
    work = directory / "terraform"
    work.mkdir(mode=0o700)
    for name in INPUTS:
        path = ROOT / "infra/aws/environment" / name
        if not path.is_file() or path.is_symlink():
            raise ReconciliationBlocked("unsafe trusted Terraform input")
        shutil.copyfile(path, work / name)
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
    deadline = time.monotonic() + 1200

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
        "init.log",
    )
    run(
        [
            "plan",
            "-input=false",
            "-no-color",
            "-lock-timeout=30s",
            "-out=current.tfplan",
        ],
        "plan.log",
    )
    plan = tools.read_json(run(["show", "-json", "current.tfplan"], "plan.json"))
    resources = plan["planned_values"]["root_module"]["resources"]
    if (
        plan.get("errored")
        or len(resources) != len(ADDRESSES)
        or {r["address"] for r in resources} != ADDRESSES
    ):
        raise ReconciliationBlocked(
            "saved plan does not cover the exact existing environment"
        )
    changes = plan.get("resource_changes", [])
    if len(changes) != len(ADDRESSES) or {r["address"] for r in changes} != ADDRESSES:
        raise ReconciliationBlocked("saved plan change coverage is incomplete")
    for item in changes:
        change = item["change"]
        if change["actions"] not in [["no-op"], ["update"]] or has_unknown_value(
            change.get("after_unknown", {})
        ):
            raise ReconciliationBlocked(
                "replacement/unknown plan requires reviewed recovery"
            )
        before, after = change["before"], change["after"]
        if change["actions"] == ["update"]:
            before, after = (
                json.loads(json.dumps(before)),
                json.loads(json.dumps(after)),
            )
            for key in ["tags", "tags_all", "volume_tags"]:
                for value in [before, after]:
                    if isinstance(value.get(key), dict):
                        value[key].pop("Generation", None)
            if (
                item["address"] == "aws_ec2_tag.network_generation"
                and before.get("key") == "Generation"
            ):
                before.pop("value", None)
                after.pop("value", None)
            if before != after:
                raise ReconciliationBlocked(
                    "real configuration drift is outside routine tag-only admission"
                )
    run(
        ["apply", "-input=false", "-no-color", "-lock-timeout=30s", "current.tfplan"],
        "apply.log",
    )


def create_after_disposal(context, generation, current, etag, directory):
    if current.get("status") != "provisioning" or current.get("schema") != 2:
        raise ReconciliationBlocked("recreation pointer is not durably admitted")
    work = directory / "terraform-create"
    work.mkdir(mode=0o700)
    root = ROOT / "infra/aws/environment"
    for name in INPUTS:
        path = root / name
        if path.is_symlink() or not path.is_file():
            raise ReconciliationBlocked("unsafe trusted Terraform creation input")
        shutil.copyfile(path, work / name)
    plan_path = work / "create.tfplan"
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
    deadline = time.monotonic() + 1200

    def run(arguments, label):
        return private_tools().run_private(
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
        "create-init.log",
    )
    run(
        [
            "plan",
            "-input=false",
            "-no-color",
            "-lock-timeout=30s",
            "-out=create.tfplan",
        ],
        "create-plan.log",
    )
    if (
        plan_path.is_symlink()
        or not plan_path.is_file()
        or not 0 < plan_path.stat().st_size <= 16 * 1024**2
    ):
        raise ReconciliationBlocked("saved creation plan is missing or excessive")
    plan_path.chmod(0o600)
    plan = private_tools().read_json(
        run(["show", "-json", "create.tfplan"], "create-plan.json")
    )
    resources = (
        plan.get("planned_values", {}).get("root_module", {}).get("resources", [])
    )
    changes = plan.get("resource_changes", [])
    if (
        plan.get("errored")
        or {item.get("address") for item in resources} != ADDRESSES
        or len(resources) != len(ADDRESSES)
        or {item.get("address") for item in changes} != ADDRESSES
        or len(changes) != len(ADDRESSES)
    ):
        raise ReconciliationBlocked(
            "creation plan is outside the fixed environment address set"
        )
    planned = {item["address"]: item.get("values", {}) for item in resources}
    for item in changes:
        change = item.get("change", {})
        if change.get("actions") != ["create"] or change.get("before") is not None:
            raise ReconciliationBlocked(
                "creation plan contains an update, deletion or replacement"
            )
    verify_creation_configuration(planned, generation)
    digest = hashlib.sha256(plan_path.read_bytes()).hexdigest()
    event(
        context,
        generation,
        "terraform-plan-inspected",
        {
            "generation": generation,
            "status": "create-only-inspected",
            "sha256": digest,
            "addresses": sorted(ADDRESSES),
            "predecessor_generation": current["recreation"]["generation"],
        },
        directory,
    )
    event(
        context,
        generation,
        "terraform-apply-intent",
        {
            "generation": generation,
            "status": "creating",
            "sha256": digest,
            "addresses": sorted(ADDRESSES),
        },
        directory,
    )
    latest_state, latest_pointer, latest_etag = snapshots(context, directory)
    if (
        latest_pointer != current
        or latest_etag != etag
        or latest_state.get("lineage") != current["recreation"]["state_lineage"]
        or latest_state.get("serial") != current["recreation"]["state_serial"]
        or latest_state.get("resources") != []
        or latest_state.get("outputs", {}) != {}
    ):
        raise ReconciliationBlocked(
            "retained empty state changed before exact creation apply"
        )
    private_tools().run_private(
        [
            "terraform",
            "apply",
            "-input=false",
            "-no-color",
            "-lock-timeout=30s",
            "create.tfplan",
        ],
        work,
        "create-apply.log",
        env=env,
        timeout=max(0.01, deadline - time.monotonic()),
    )
    state, pointer, latest_etag = snapshots(context, directory)
    if pointer != current or latest_etag != etag:
        raise ReconciliationBlocked(
            "generation changed during exact saved creation apply"
        )
    if (
        state.get("lineage") != current["recreation"]["state_lineage"]
        or not isinstance(state.get("resources"), list)
        or {r["type"] + "." + r["name"] for r in state["resources"]} != ADDRESSES
        or len(state["resources"]) != len(ADDRESSES)
        or state.get("serial", 0) <= current["recreation"]["state_serial"]
    ):
        raise ReconciliationBlocked(
            "creation state is partial or has foreign resources"
        )
    instance = next(
        r["instances"][0]["attributes"]
        for r in state["resources"]
        if r["type"] == "aws_instance" and r["name"] == "host"
    )
    host_id = instance.get("id", "")
    if not re.fullmatch(r"i-[a-f0-9]{17}", host_id):
        raise ReconciliationBlocked("created host identity is invalid")
    updated = {
        "schema": 1,
        "generation": generation,
        "host_id": host_id,
        "status": "provisioned",
        "predecessor": {
            "generation": current["recreation"]["generation"],
            "host_id": host_id,
        },
        "host_predecessor": {
            "generation": current["recreation"]["generation"],
            "host_id": host_id,
        },
        "request": current["request"],
        "images": current["images"],
        "retained": current["retained"],
    }
    verify_state(state, updated)
    from aws_disposal_orchestration import owned_inventory

    _, _, inventory = owned_inventory(state, updated)
    verify_creation_outputs(state, generation, inventory)
    if (
        instance.get("instance_type") != "m7i-flex.large"
        or instance.get("ami") != "ami-04478a3e21a0d79a7"
    ):
        raise ReconciliationBlocked(
            "created host differs from the fixed Free-plan configuration"
        )
    event(
        context,
        generation,
        "terraform-terminal",
        {
            "generation": generation,
            "status": "passed",
            "host_id": host_id,
            "state_lineage": state["lineage"],
            "state_serial": state["serial"],
            "plan_sha256": digest,
        },
        directory,
    )
    event(
        context,
        generation,
        "provisioned",
        {
            "generation": generation,
            "host_id": host_id,
            "state_lineage": state["lineage"],
            "state_serial": state["serial"],
            "addresses": sorted(ADDRESSES),
        },
        directory,
    )
    event(context, generation, "intent", updated, directory)
    new_etag = put_json(context, POINTER, updated, directory, etag=etag)
    return state, updated, new_etag


def verify_creation_outputs(state, generation, inventory):
    expected = {
        "instance_id": inventory["aws_instance.host"],
        "vpc_id": inventory["aws_vpc.main"],
        "security_group_id": inventory["aws_security_group.host"],
        "generation": generation,
        "root_volume_id": inventory["instance_root_volume"],
    }
    outputs = state.get("outputs")
    if not isinstance(outputs, dict) or set(outputs) != set(expected):
        raise ReconciliationBlocked("creation state outputs are incomplete or unknown")
    for name, value in expected.items():
        record = outputs.get(name)
        if (
            not isinstance(record, dict)
            or set(record)
            not in (
                {"value", "type"},
                {"value", "type", "sensitive"},
            )
            or record.get("value") != value
            or record.get("type") != "string"
            or record.get("sensitive", False) is not False
        ):
            raise ReconciliationBlocked("creation state output identity mismatch")


def verify_creation_configuration(resources, generation):
    tags = {
        "ManagedBy": "onlineshop-test",
        "Repository": "Djimi/OnlineShop-full-stack",
        "Generation": generation,
    }
    stable_tags = {"ManagedBy": tags["ManagedBy"], "Repository": tags["Repository"]}
    expected = {
        "aws_vpc.main": {
            "cidr_block": "10.83.0.0/16",
            "enable_dns_support": True,
            "enable_dns_hostnames": True,
            "tags": tags,
        },
        "aws_subnet.host": {
            "cidr_block": "10.83.1.0/24",
            "availability_zone": "eu-north-1a",
            "map_public_ip_on_launch": True,
            "tags": tags,
        },
        "aws_internet_gateway.main": {"tags": tags},
        "aws_route_table.host": {"tags": tags},
        "aws_route.outbound": {"destination_cidr_block": "0.0.0.0/0"},
        "aws_route_table_association.host": {},
        "aws_security_group.host": {
            "name": "onlineshop-test-host",
            "description": "SSM-only testing host; no incoming connectivity",
            "ingress": [],
            "egress": [
                {
                    "from_port": 0,
                    "to_port": 0,
                    "protocol": "-1",
                    "cidr_blocks": ["0.0.0.0/0"],
                }
            ],
            "tags": tags,
        },
        "aws_launch_template.host": {
            "name": "onlineshop-test-host",
            "tags": stable_tags,
        },
        "aws_instance.host": {
            "ami": "ami-04478a3e21a0d79a7",
            "instance_type": "m7i-flex.large",
            "associate_public_ip_address": True,
            "iam_instance_profile": "onlineshop-test-host",
            "root_block_device": [
                {
                    "volume_size": 50,
                    "volume_type": "gp3",
                    "encrypted": True,
                    "delete_on_termination": True,
                }
            ],
            "metadata_options": [
                {
                    "http_endpoint": "enabled",
                    "http_tokens": "required",
                    "http_put_response_hop_limit": 1,
                    "http_protocol_ipv6": "disabled",
                    "instance_metadata_tags": "disabled",
                }
            ],
            "tags": tags,
            "volume_tags": tags,
        },
        "aws_ec2_tag.network_generation": {"key": "Generation", "value": generation},
    }
    for address, required in expected.items():
        values = resources.get(address)
        if not isinstance(values, dict) or any(
            not creation_value_matches(values.get(key), value)
            for key, value in required.items()
        ):
            raise ReconciliationBlocked(
                "creation plan differs from the fixed environment configuration"
            )
    for address in [
        "aws_vpc.main",
        "aws_subnet.host",
        "aws_internet_gateway.main",
        "aws_route_table.host",
        "aws_security_group.host",
        "aws_instance.host",
    ]:
        if resources[address].get("tags") != tags:
            raise ReconciliationBlocked(
                "creation plan has unexpected environment ownership tags"
            )
    if (
        resources["aws_instance.host"].get("volume_tags") != tags
        or resources["aws_launch_template.host"].get("tags") != stable_tags
    ):
        raise ReconciliationBlocked(
            "creation plan has unexpected stable launch/volume tags"
        )
    specifications = resources["aws_launch_template.host"].get("tag_specifications")
    if (
        not isinstance(specifications, list)
        or len(specifications) != 3
        or {item.get("resource_type") for item in specifications}
        != {"instance", "volume", "network-interface"}
        or any(item.get("tags") != stable_tags for item in specifications)
    ):
        raise ReconciliationBlocked(
            "creation plan launch-template tag specifications changed"
        )
    egress = resources["aws_security_group.host"].get("egress")
    if (
        not isinstance(egress, list)
        or len(egress) != 1
        or any(
            egress[0].get(key) != value
            for key, value in {
                "from_port": 0,
                "to_port": 0,
                "protocol": "-1",
                "cidr_blocks": ["0.0.0.0/0"],
            }.items()
        )
        or bool(egress[0].get("ipv6_cidr_blocks"))
        or bool(egress[0].get("prefix_list_ids"))
        or bool(egress[0].get("security_groups"))
        or egress[0].get("self") is True
    ):
        raise ReconciliationBlocked(
            "creation plan egress differs from the fixed host boundary"
        )


def creation_value_matches(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and creation_value_matches(actual[key], value)
            for key, value in expected.items()
        )
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(actual) == len(expected)
            and all(
                creation_value_matches(value, expected[index])
                for index, value in enumerate(actual)
            )
        )
    return actual == expected


def wait_for_ssm(host_id, deadline):
    while time.monotonic() < deadline:
        response = read_aws(
            [
                "ssm",
                "describe-instance-information",
                "--filters",
                json.dumps([{"Key": "InstanceIds", "Values": [host_id]}]),
            ],
            deadline=deadline,
        )
        entries = response.get("InstanceInformationList")
        if (
            isinstance(entries, list)
            and len(entries) == 1
            and entries[0].get("InstanceId") == host_id
            and entries[0].get("PingStatus") == "Online"
        ):
            return
        time.sleep(5)
    raise ReconciliationBlocked(
        "new trusted host did not become SSM-online before the bound"
    )


def install_recreated_host(context, pointer, predecessor_generation, directory):
    content = {}
    for name in FILES:
        path = ROOT / "infra/aws/runtime" / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 16 * 1024**2
        ):
            raise ReconciliationBlocked("unsafe trusted host setup input")
        content[name] = path.read_bytes()
    content["bootstrap.json"] = json.dumps(
        {
            "account_id": context["account"],
            "region": REGION,
            "secret_arn": context["secret"],
        },
        sort_keys=True,
    ).encode()
    bundle = directory / "host-setup.tar"
    with tarfile.open(bundle, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(content.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o700 if name == "host-setup.sh" else 0o600
            archive.addfile(member, io.BytesIO(data))
    if not 0 < bundle.stat().st_size <= 16 * 1024**2:
        raise ReconciliationBlocked("trusted host setup bundle exceeds its bound")
    key = "operations/runtime-input/" + pointer["generation"] + "/host-setup.tar"
    put_file(context, key, bundle, directory)
    body = host_setup_bootstrap(
        context, pointer, predecessor_generation, key, bundle, content
    )
    outcome = remote(context, pointer, "host-setup", body, 1000, directory)
    expected = {
        "generation": pointer["generation"],
        "host_id": pointer["host_id"],
        "predecessor_generation": predecessor_generation,
        "setup_verified": True,
    }
    if outcome != expected:
        raise ReconciliationBlocked(
            "new host setup did not report its exact trusted identity"
        )


def host_setup_bootstrap(
    context, pointer, predecessor_generation, key, bundle, content
):
    names = sorted(content)
    digests = {name: hashlib.sha256(data).hexdigest() for name, data in content.items()}
    code = r"""import hashlib,json,os,pathlib,subprocess,tarfile,tempfile
root=pathlib.Path('/opt/onlineshop-test')
if root.exists():
 assert root.is_dir() and not root.is_symlink() and root.stat().st_uid==0 and not list(root.iterdir())
root.mkdir(mode=0o755,parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='trusted-host-setup-') as tmp:
 bundle=pathlib.Path(tmp)/'host-setup.tar'
 subprocess.run(['aws','s3api','get-object','--bucket',BUCKET,'--key',KEY,'--expected-bucket-owner',ACCOUNT,'--region','eu-north-1',str(bundle)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=True)
 assert bundle.stat().st_size==SIZE and hashlib.sha256(bundle.read_bytes()).hexdigest()==DIGEST
 with tarfile.open(bundle,'r|') as archive:
  seen=set()
  for member in archive:
   assert member.isfile() and member.pax_headers=={} and member.name in NAMES and member.name not in seen and member.size<=16777216
   data=archive.extractfile(member).read();assert hashlib.sha256(data).hexdigest()==HASHES[member.name]
   target=root/member.name
   if target.exists():assert target.is_file() and not target.is_symlink() and target.stat().st_uid==0 and target.read_bytes()==data
   else:
    fd=os.open(target,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o700 if member.name=='host-setup.sh' else 0o600)
    with os.fdopen(fd,'wb') as output:output.write(data);output.flush();os.fsync(output.fileno())
   seen.add(member.name)
  assert seen==set(NAMES)
 subprocess.run(['bash',str(root/'host-setup.sh'),'--setup'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=900,check=True)
 subprocess.run(['python3',str(root/'host-session.py'),'--initialize-disposed-predecessor',PREDECESSOR],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=True)
 print(json.dumps({'generation':GENERATION,'host_id':HOST_ID,'predecessor_generation':PREDECESSOR,'setup_verified':True},sort_keys=True))
"""
    values = {
        "BUCKET": context["bucket"],
        "KEY": key,
        "ACCOUNT": context["account"],
        "SIZE": bundle.stat().st_size,
        "DIGEST": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "NAMES": names,
        "HASHES": digests,
        "GENERATION": pointer["generation"],
        "HOST_ID": pointer["host_id"],
        "PREDECESSOR": predecessor_generation,
    }
    for name, value in values.items():
        code = code.replace(name, repr(value))
    return (
        "set -eu; umask 077; timeout --signal=TERM --kill-after=30 960 python3 - <<'PY'\n"
        + code
        + "\nPY"
    )


def has_unknown_value(value):
    # Terraform preserves nested shape as empty maps/lists even when all leaves
    # are known. Only a true leaf means unknown; malformed leaves still refuse.
    if isinstance(value, dict):
        return any(has_unknown_value(child) for child in value.values())
    if isinstance(value, list):
        return any(has_unknown_value(child) for child in value)
    if type(value) is not bool:
        raise ReconciliationBlocked("invalid Terraform unknown-value tree")
    return value


def runtime_bundle(context, current, receipt, fixtures, directory, host_predecessor):
    hashes = {
        name: hashlib.sha256(
            (ROOT / "infra/aws/runtime" / name).read_bytes()
        ).hexdigest()
        for name in FILES
    }
    images = json.dumps(receipt, sort_keys=True).encode()
    binding = {
        "schema": 1,
        "request": current["request"],
        "generation": current["generation"],
        "predecessor_generation": host_predecessor,
        "host_id": current["host_id"],
        "operation": "runtime",
        "bucket": context["bucket"],
        "runtime_hashes": hashes,
        "images_sha256": hashlib.sha256(images).hexdigest(),
        "fixtures_sha256": receipt["fixtures"]["sha256"],
        "recovery_verified": True,
    }
    files = {name: (ROOT / "infra/aws/runtime" / name).read_bytes() for name in FILES}
    files.update(
        {
            "binding.json": json.dumps(binding, sort_keys=True).encode(),
            "images.json": images,
            "fixtures.tar": fixtures.read_bytes(),
            "bootstrap.json": json.dumps(
                {
                    "account_id": context["account"],
                    "region": REGION,
                    "secret_arn": context["secret"],
                }
            ).encode(),
        }
    )
    bundle = directory / "bundle.tar"
    with tarfile.open(bundle, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            member.mode = 0o600
            archive.addfile(member, io.BytesIO(data))
    return binding, bundle


def runtime_bootstrap(context, key, bundle, binding):
    # Fixed bounded downloader: one S3 object, one checksum, flat trusted file set.
    code = """import fcntl,hashlib,os,pathlib,subprocess,tarfile,tempfile
root=pathlib.Path('/opt/onlineshop-test');state=root/'.runtime'
assert root.is_dir() and not root.is_symlink() and root.stat().st_uid==0
assert state.is_dir() and not state.is_symlink() and state.stat().st_uid==0
with open(state/'host.lock','a') as lock:
 fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 with tempfile.TemporaryDirectory(dir=state) as tmp:
  target=pathlib.Path(tmp)/'bundle.tar'
  subprocess.run(['aws','s3api','get-object','--bucket',BUCKET,'--key',KEY,'--expected-bucket-owner',ACCOUNT,'--region','eu-north-1',str(target)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,check=True)
  assert target.stat().st_size==SIZE and hashlib.sha256(target.read_bytes()).hexdigest()==DIGEST
  with tarfile.open(target,'r|') as archive:
   seen=set()
   for member in archive:
    assert member.isfile() and member.name in NAMES and member.name not in seen and member.size<=16777216
    seen.add(member.name);destination=(state if member.name in ['binding.json','images.json','fixtures.tar'] else root)/member.name
    assert not destination.is_symlink()
    with open(destination.with_suffix(destination.suffix+'.incoming'),'xb') as out:out.write(archive.extractfile(member).read())
    os.chmod(destination.with_suffix(destination.suffix+'.incoming'),0o600);os.replace(destination.with_suffix(destination.suffix+'.incoming'),destination)
   assert seen==set(NAMES)
subprocess.run(['python3',str(root/'host-session.py'),'--generation',GENERATION],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=2040,check=True)
"""
    replacements = {
        "BUCKET": repr(context["bucket"]),
        "KEY": repr(key),
        "ACCOUNT": repr(context["account"]),
        "SIZE": str(bundle.stat().st_size),
        "DIGEST": repr(hashlib.sha256(bundle.read_bytes()).hexdigest()),
        "NAMES": repr(
            [*FILES, "binding.json", "images.json", "fixtures.tar", "bootstrap.json"]
        ),
        "GENERATION": repr(binding["generation"]),
    }
    for name, value in replacements.items():
        code = code.replace(name, value)
    return (
        "set -eu; umask 077; timeout --signal=TERM --kill-after=30 2070 python3 - <<'PY'\n"
        + code
        + "PY"
    )


def collect_evidence(context, binding, directory):
    deadline = time.monotonic() + 300
    prefix = "operations/runtime-evidence/" + binding["generation"] + "/"
    envelope = read_snapshot(
        location(context),
        prefix + "envelope.json",
        directory / "envelope.json",
        deadline,
    )
    args = [*location(context), "--key", prefix + "reports.tar"]
    head = read_aws(["s3api", "head-object", *args], deadline=deadline)
    if not head.get("VersionId") or not 0 < head["ContentLength"] <= 8 * 1024**2:
        raise ReconciliationBlocked("missing bounded runtime reports")
    path = directory / "reports.tar"
    downloaded = read_aws(
        ["s3api", "get-object", *args, "--if-match", head["ETag"], str(path)],
        deadline=deadline,
    )
    if (
        downloaded.get("VersionId") != head["VersionId"]
        or path.stat().st_size != head["ContentLength"]
    ):
        raise ReconciliationBlocked("report snapshot changed")
    return envelope, path


def verify_envelope(envelope, binding, reports):
    if (
        set(envelope)
        != {"binding", "status", "stages", "cleanup_verified", "reports_sha256"}
        or envelope["binding"] != binding
        or envelope["status"] != "passed"
        or envelope["stages"] != ["reset", "readiness", "e2e", "reports"]
        or envelope["cleanup_verified"] is not True
        or envelope["reports_sha256"]
        != hashlib.sha256(reports.read_bytes()).hexdigest()
    ):
        raise ReconciliationBlocked(
            "host execution/stages/report hash/cleanup envelope mismatch"
        )


def sanitized_reports(source, target):
    import xml.etree.ElementTree as ET

    names = {
        "TEST-" + name + ".xml": name
        for name in [
            "com.onlineshop.e2e.ItemsE2ETest",
            "com.onlineshop.e2e.RestAssuredLoggingTest",
        ]
    }
    retained = {}
    with tarfile.open(source, "r|") as archive:
        for member in archive:
            if (
                not member.isfile()
                or member.name not in names
                or member.name in retained
                or member.size > 512 * 1024
            ):
                raise ReconciliationBlocked("unsafe report retention input")
            data = archive.extractfile(member).read()
            if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
                raise ReconciliationBlocked("report declarations refused")
            document = ET.fromstring(data)
            name = names[member.name]
            if document.tag != "testsuite" or document.get("name") != name:
                raise ReconciliationBlocked("unknown retention suite")
            counts = {
                key: int(document.get(key, "-1"))
                for key in ["tests", "failures", "errors", "skipped"]
            }
            if (
                any(not 0 <= value <= 100 for value in counts.values())
                or len(document.findall("testcase")) > 100
            ):
                raise ReconciliationBlocked("excessive retention counts")
            clean = ET.Element(
                "testsuite",
                {"name": name, **{key: str(value) for key, value in counts.items()}},
            )
            for index, case in enumerate(document.findall("testcase"), 1):
                retained_case = ET.SubElement(
                    clean, "testcase", {"classname": name, "name": "case-" + str(index)}
                )
                for outcome in ["failure", "error", "skipped"]:
                    if case.find(outcome) is not None:
                        ET.SubElement(retained_case, outcome)
            retained[member.name] = ET.tostring(
                clean, encoding="utf-8", xml_declaration=True
            )
    with tarfile.open(target, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(retained.items()):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))
