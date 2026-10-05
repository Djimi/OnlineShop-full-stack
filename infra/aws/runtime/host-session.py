#!/usr/bin/env python3
"""Trusted host admission and current-request execution envelope.

The fixed SSM downloader installs hash-verified root-owned inputs. Prove idle
predecessor state, archive its exact record, admit the new generation, execute the
trusted runtime and publish create-only stage/report/cleanup evidence. Never turn
recovery into success or remove a detached test to make admission appear safe.
"""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".runtime"
FILES = {"host-session.py", "run-stack.py", "compose.yml", "host-setup.sh"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", required=True)
    args = parser.parse_args()
    lock = None
    try:
        binding = verify_inputs(args.generation)
        descriptor = os.open(
            STATE / "host.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        lock = os.fdopen(descriptor, "w")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        admit_predecessor(binding, lock.fileno())
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "run-stack.py"),
                "--generation",
                args.generation,
                "--images",
                str(STATE / "images.json"),
                "--host-lock-fd",
                str(lock.fileno()),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2000,
            check=False,
            pass_fds=(lock.fileno(),),
        )
        envelope = collect_envelope(binding, result.returncode)
        publish_evidence(binding)
        if envelope["status"] != "passed":
            raise ValueError("required runtime stages or cleanup failed")
        print("Bound host stages/reports/cleanup published; no GitHub success")
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        print("Trusted host session failed; protected details omitted", file=sys.stderr)
        return 1
    finally:
        if lock is not None:
            lock.close()


def read_json(path):
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= 65536:
        raise ValueError("unsafe trusted record")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate field")
            result[key] = value
        return result

    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def verify_inputs(generation):
    if not re.fullmatch(r"run-[1-9][0-9]*-attempt-[1-9][0-9]*", generation):
        raise ValueError("invalid generation")
    if STATE.is_symlink() or not STATE.is_dir() or STATE.stat().st_uid != os.geteuid():
        raise ValueError("unsafe runtime state")
    binding = read_json(STATE / "binding.json")
    if (
        set(binding)
        != {
            "schema",
            "request",
            "generation",
            "predecessor_generation",
            "host_id",
            "operation",
            "bucket",
            "runtime_hashes",
            "images_sha256",
            "fixtures_sha256",
            "recovery_verified",
        }
        or binding["schema"] != 1
        or binding["generation"] != generation
        or binding["operation"] != "runtime"
        or binding["recovery_verified"] is not True
        or set(binding["runtime_hashes"]) != FILES
        or not re.fullmatch(r"i-[a-f0-9]{17}", binding["host_id"])
        or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", binding["bucket"])
        or generation
        != f"run-{binding['request']['validation_run_id']}-attempt-{binding['request']['validation_run_attempt']}"
    ):
        raise ValueError("invalid bound request")
    for name, digest in binding["runtime_hashes"].items():
        path = ROOT / name
        if (
            path.is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("trusted runtime hash mismatch")
    for name, digest in [
        ("images.json", binding["images_sha256"]),
        ("fixtures.tar", binding["fixtures_sha256"]),
    ]:
        path = STATE / name
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size > 16 * 1024**2
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            raise ValueError("input transport hash mismatch")
    images = read_json(STATE / "images.json")
    if any(images.get(key) != value for key, value in binding["request"].items()):
        raise ValueError("images/request binding mismatch")
    if (STATE / "evidence" / generation).exists() or (
        STATE / "reports" / generation
    ).exists():
        raise ValueError("duplicate host attempt")
    return binding


def admit_predecessor(binding, lock_fd):
    descriptor = os.dup(lock_fd)
    with os.fdopen(descriptor, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = read_json(STATE / "current-generation.json")
        if current != {"generation": binding["predecessor_generation"]}:
            raise ValueError("foreign host predecessor")
        spec = importlib.util.spec_from_file_location(
            "trusted_host_runtime", ROOT / "run-stack.py"
        )
        runtime = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runtime)
        try:
            runtime.verify_no_detached_test()
        except runtime.RuntimeFailed:
            raise ValueError("test absence unknown") from None
        if any(
            (STATE / name).exists()
            for name in ["run.env", "e2e.env", "docker-config/config.json"]
        ):
            raise ValueError("temporary credentials remain")
        prior = admission_operation(current["generation"])
        # A previous terminal SSM plus the controller's fresh idle proof authorizes
        # aborting only this exact recorded pointer/operation write gap.
        if prior is not None and prior["generation"] != current["generation"]:
            prior = {"generation": current["generation"], "status": "recovered-aborted"}
            write_atomic(STATE / "operation.json", prior)
        admissions = STATE / "admissions"
        admissions.mkdir(mode=0o700, exist_ok=True)
        transition = {
            "schema": 1,
            "old": current["generation"],
            "new": binding["generation"],
            "request": binding["request"],
            "prior": prior,
        }
        write_identical(admissions / (binding["generation"] + ".json"), transition)
        if prior is not None:
            history = STATE / "history"
            history.mkdir(mode=0o700, exist_ok=True)
            write_identical(history / (current["generation"] + ".json"), prior)
        write_atomic(
            STATE / "current-generation.json", {"generation": binding["generation"]}
        )
        # This terminal placeholder is installed before launching run-stack. Until
        # then, the immutable transition authenticates the old/new identity gap.
        write_atomic(
            STATE / "operation.json",
            {"generation": binding["generation"], "status": "recovered-aborted"},
        )


def admission_operation(generation):
    """Observe a normal operation or one exact immutable admission write gap."""
    operation = STATE / "operation.json"
    prior = read_json(operation) if operation.exists() else None
    statuses = {
        "running",
        "unknown",
        "passed",
        "failed",
        "cancelled",
        "recovered-aborted",
    }
    if prior is not None and prior.get("status") not in statuses:
        raise ValueError("unrecognized predecessor outcome")
    transition_path = STATE / "admissions" / (generation + ".json")
    if not transition_path.exists():
        if prior is None:
            return None
        if prior.get("generation") == generation:
            return prior
        raise ValueError("foreign predecessor operation")
    transition = read_json(transition_path)
    if (
        set(transition) != {"schema", "old", "new", "request", "prior"}
        or transition["schema"] != 1
        or transition["new"] != generation
        or not isinstance(transition["request"], dict)
        or not re.fullmatch(r"run-[1-9][0-9]*-attempt-[1-9][0-9]*", transition["old"])
        or transition["old"] == generation
        or generation
        != f"run-{transition['request']['validation_run_id']}-attempt-{transition['request']['validation_run_attempt']}"
    ):
        raise ValueError("foreign admission transition")
    history = STATE / "history" / (transition["old"] + ".json")
    if transition["prior"] is None:
        if history.exists():
            raise ValueError("unexpected admission archive")
    elif not history.exists() or read_json(history) != transition["prior"]:
        raise ValueError("admission archive differs from immutable intent")
    if prior is not None and prior.get("generation") == generation:
        return prior
    if prior != transition["prior"] or (
        prior is not None and prior.get("generation") != transition["old"]
    ):
        raise ValueError("foreign predecessor operation")
    return prior


def write_identical(path, record):
    if path.exists():
        if read_json(path) != record:
            raise ValueError("existing immutable record differs")
        return
    write_new(path, record)


def collect_envelope(binding, returncode):
    operation = read_json(STATE / "operation.json")
    stages = [s["name"] for s in operation["stages"] if s.get("status") == "passed"]
    cleanup = not any(
        (STATE / name).exists()
        for name in ["run.env", "e2e.env", "docker-config/config.json"]
    )
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter", "name=^onlineshop-test-e2e$"],
        capture_output=True,
        timeout=10,
        check=False,
    )
    cleanup = cleanup and result.returncode == 0 and not result.stdout.strip()
    evidence = STATE / "evidence" / binding["generation"]
    evidence.mkdir(mode=0o700, parents=True)
    reports = evidence / "reports.tar"
    with tarfile.open(reports, "w", format=tarfile.USTAR_FORMAT) as archive:
        for suite in [
            "com.onlineshop.e2e.ItemsE2ETest",
            "com.onlineshop.e2e.RestAssuredLoggingTest",
        ]:
            path = (
                STATE / "reports" / binding["generation"] / ("TEST-" + suite + ".xml")
            )
            if (
                path.is_file()
                and not path.is_symlink()
                and path.stat().st_size <= 512 * 1024
            ):
                archive.add(path, arcname=path.name, recursive=False)
    passed = (
        returncode == 0
        and operation["generation"] == binding["generation"]
        and operation["status"] == "passed"
        and stages == ["reset", "readiness", "e2e", "reports"]
        and cleanup
    )
    envelope = {
        "binding": binding,
        "status": "passed" if passed else "failed",
        "stages": stages,
        "cleanup_verified": cleanup,
        "reports_sha256": hashlib.sha256(reports.read_bytes()).hexdigest(),
    }
    write_new(evidence / "envelope.json", envelope)
    return envelope


def publish_evidence(binding):
    account = read_json(ROOT / "bootstrap.json")["account_id"]
    for name in ["reports.tar", "envelope.json"]:
        subprocess.run(
            [
                "aws",
                "s3api",
                "put-object",
                "--bucket",
                binding["bucket"],
                "--key",
                "operations/runtime-evidence/" + binding["generation"] + "/" + name,
                "--body",
                str(STATE / "evidence" / binding["generation"] / name),
                "--expected-bucket-owner",
                account,
                "--server-side-encryption",
                "AES256",
                "--if-none-match",
                "*",
                "--region",
                "eu-north-1",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=True,
        )


def write_new(path, record):
    descriptor = os.open(
        path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as output:
        json.dump(record, output)
        output.flush()
        os.fsync(output.fileno())
    sync_directory(path.parent)


def write_atomic(path, record):
    temporary = path.with_suffix(".incoming")
    write_new(temporary, record)
    os.replace(temporary, path)
    sync_directory(path.parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    raise SystemExit(main())
