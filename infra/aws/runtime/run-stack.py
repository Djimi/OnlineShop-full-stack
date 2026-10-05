#!/usr/bin/env python3
"""Reset only the owned testing project, run isolated tests, retain sanitized evidence.

The trusted host bootstrap/current-generation records and IMDS firewall rules
are prerequisites. A host process lock spans reset through evidence collection.
Secrets exist only in restricted temporary configuration; no cloud credentials
reach candidate containers. Missing reports, unknown operations or stale identity
fail closed. The app remains for inspection; this command never publishes GitHub
success. Cloud-side reconciliation/transport is a separate unfinished controller.
"""

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import resource
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STATE = ROOT / ".runtime"
PROJECT = "onlineshop-test"
REGION = "eu-north-1"
IMAGES = {"auth", "items", "gateway", "frontend", "e2e"}
SUITES = {
    "com.onlineshop.e2e.ItemsE2ETest": 3,
    "com.onlineshop.e2e.RestAssuredLoggingTest": 1,
}
MAX_DATA = 8 * 1024**2
MAX_FIXTURES = 16 * 1024**2


class RuntimeFailed(Exception):
    pass


class TerminationUnknown(RuntimeFailed):
    pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generation", required=True)
    parser.add_argument("--images", type=Path)
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Observe host lock/test absence and prior outcome; never authorize retry",
    )
    args = parser.parse_args()
    if (args.images is None) != args.reconcile:
        parser.error(
            "choose --images for runtime execution or --reconcile for observation"
        )
    operation = None
    try:
        if not re.fullmatch(r"run-[1-9][0-9]*-attempt-[1-9][0-9]*", args.generation):
            raise RuntimeFailed("invalid generation")
        if (
            STATE.is_symlink()
            or not STATE.is_dir()
            or STATE.stat().st_uid != os.geteuid()
        ):
            raise RuntimeFailed("unsafe runtime directory")
        STATE.chmod(0o700)
        descriptor = os.open(
            STATE / "host.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(descriptor, "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeFailed("another host operation is active") from None
            if args.reconcile:
                observe_recovery(args.generation)
                return 0
            bootstrap, images = verify_generation(args.generation, args.images)
            verify_metadata_blocks()
            verify_no_detached_test()
            operation = {
                "generation": args.generation,
                "status": "running",
                "started_at": timestamp(),
                "stages": [],
            }
            save_operation(operation)
            credentials = read_credentials(bootstrap)
            env = runtime_environment(images, args.generation)
            try:
                write_secret_files(credentials)
                verify_project_ownership(env)
                deadline = time.monotonic() + 600
                login_registry(bootstrap, env)
                compose(["pull", "--include-deps"], env, remaining(deadline))
                compose(
                    ["down", "--volumes", "--remove-orphans", "--timeout", "30"],
                    env,
                    remaining(deadline),
                )
                initialize_fixtures()
                record_stage(operation, "reset")
                compose(
                    ["up", "-d", "--no-build", "--wait", "--wait-timeout", "290"],
                    env,
                    300,
                )
                record_stage(operation, "readiness")
                test_error = None
                try:
                    execute_e2e(images["e2e"], env, args.generation)
                except RuntimeFailed as error:
                    test_error = error
                    operation["stages"].append(
                        {"name": "e2e", "status": "failed", "at": timestamp()}
                    )
                    save_operation(operation)
                else:
                    record_stage(operation, "e2e")
                collect_reports(env, credentials, args.generation)
                record_stage(operation, "reports")
                if test_error is not None:
                    raise test_error
            finally:
                # This fixed container belongs to this project/generation; inspect
                # ownership before removing anything left by a failed test run.
                try:
                    remove_test_container(env, args.generation)
                finally:
                    for filename in ["run.env", "e2e.env", "docker-config/config.json"]:
                        path = STATE / filename
                        if path.exists() and not path.is_symlink():
                            path.unlink()
            operation.update(status="passed", finished_at=timestamp())
            save_operation(operation)
            print(
                "Owned runtime passed readiness and four executed report-backed tests; no GitHub success published"
            )
            return 0
    except (
        RuntimeFailed,
        OSError,
        ValueError,
        TypeError,
        KeyError,
        ET.ParseError,
        tarfile.TarError,
    ) as error:
        if operation is not None:
            operation.update(
                status="unknown" if isinstance(error, TerminationUnknown) else "failed",
                finished_at=timestamp(),
            )
            save_operation(operation)
        message = (
            str(error)
            if isinstance(error, RuntimeFailed)
            else "invalid runtime configuration or evidence"
        )
        print("Runtime failed: " + message, file=sys.stderr)
        return 1


def read_json(path):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 65536:
        raise RuntimeFailed("unsafe or missing runtime record")

    def fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise RuntimeFailed("duplicate runtime field")
            result[key] = value
        return result

    return json.loads(path.read_bytes(), object_pairs_hook=fields)


def observe_recovery(generation):
    if read_json(STATE / "current-generation.json") != {"generation": generation}:
        raise RuntimeFailed("selected generation is not current")
    previous = STATE / "operation.json"
    status = "absent"
    if previous.exists():
        record = read_json(previous)
        if record.get("generation") != generation or record.get("status") not in {
            "running",
            "unknown",
            "passed",
            "failed",
            "cancelled",
        }:
            raise RuntimeFailed("previous operation identity is not recognized")
        status = record["status"]
    verify_no_detached_test()
    print(
        json.dumps(
            {
                "generation": generation,
                "previous_status": status,
                "host_lock_acquired": True,
                "test_container_absent": True,
                "automatic_retry_authorized": False,
                "aws_validation_success": False,
            }
        )
    )


def verify_generation(generation, path):
    current = read_json(STATE / "current-generation.json")
    if current != {"generation": generation}:
        raise RuntimeFailed("selected generation is not current")
    previous = STATE / "operation.json"
    if previous.exists() and read_json(previous).get("status") not in [
        "passed",
        "failed",
        "cancelled",
    ]:
        raise RuntimeFailed("previous operation requires reconciliation")
    bootstrap = read_json(ROOT / "bootstrap.json")
    if (
        set(bootstrap) != {"account_id", "region", "secret_arn"}
        or bootstrap["region"] != REGION
        or not re.fullmatch(r"[0-9]{12}", bootstrap["account_id"])
        or not bootstrap["secret_arn"].startswith(
            f"arn:aws:secretsmanager:{REGION}:{bootstrap['account_id']}:secret:onlineshop-test/credentials-"
        )
    ):
        raise RuntimeFailed("invalid trusted bootstrap identity")
    record = read_json(path)
    allowed = {
        "repository",
        "pr",
        "head_sha",
        "base_sha",
        "candidate_sha",
        "controller_sha",
        "ci_run_id",
        "ci_run_attempt",
        "ci_artifact_id",
        "validation_run_id",
        "validation_run_attempt",
        "actor",
        "triggering_actor",
        "check_run_id",
        "build_artifact_id",
        "build_artifact_digest",
        "images",
        "fixtures",
    }
    if not set(record) <= allowed or set(record["images"]) != IMAGES:
        raise RuntimeFailed("unexpected image record fields")
    prefix = (
        f"{bootstrap['account_id']}.dkr.ecr.{REGION}.amazonaws.com/onlineshop-test-"
    )
    for name, image in record["images"].items():
        if not isinstance(image, str) or not re.fullmatch(
            re.escape(prefix + name) + r"@sha256:[0-9a-f]{64}", image
        ):
            raise RuntimeFailed("mutable or unexpected image identity")
    verify_fixture_transport(record["fixtures"])
    return bootstrap, record["images"]


def verify_fixture_transport(metadata):
    path = STATE / "fixtures.tar"
    if (
        set(metadata) != {"file", "size", "sha256"}
        or metadata["file"] != "fixtures.tar"
        or type(metadata["size"]) is not int
        or not 0 < metadata["size"] <= MAX_FIXTURES
        or not re.fullmatch(r"[0-9a-f]{64}", metadata["sha256"])
        or path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != metadata["size"]
    ):
        raise RuntimeFailed("invalid fixture transport identity")
    if hashlib.sha256(path.read_bytes()).hexdigest() != metadata["sha256"]:
        raise RuntimeFailed("fixture transport checksum mismatch")


def verify_metadata_blocks():
    for binary, address in [
        ("iptables", "169.254.169.254/32"),
        ("ip6tables", "fd00:ec2::254/128"),
    ]:
        command(
            [binary, "-C", "DOCKER-USER", "-d", address, "-j", "REJECT"], timeout=10
        )
        command([binary, "-C", "FORWARD", "-j", "DOCKER-USER"], timeout=10)


def initialize_fixtures():
    path = STATE / "fixtures.tar"
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FIXTURES:
        raise RuntimeFailed("verified service fixtures are missing or excessive")
    files = {}
    with tarfile.open(path, "r:*") as archive:
        for member in archive:
            parts = Path(member.name).parts
            if (
                not member.isfile()
                or len(parts) != 3
                or parts[0] not in ["Auth", "Items"]
                or parts[1] != "init-db"
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.sql", parts[2])
                or member.name == "Auth/init-db/02-seed-data.sql"
                or member.name in files
                or sum(len(value) for value in files.values()) + member.size
                > MAX_FIXTURES
            ):
                raise RuntimeFailed("unsafe service fixture entry")
            files[member.name] = archive.extractfile(member).read()
    if not {"Auth/init-db/01-schema.sql", "Items/init-db/01-schema.sql"} <= set(files):
        raise RuntimeFailed("both independent schemas are required")
    destination = STATE / "fixtures"
    if destination.exists():
        if (
            destination.is_symlink()
            or not destination.is_dir()
            or destination.stat().st_uid != os.geteuid()
        ):
            raise RuntimeFailed("unsafe owned fixture directory")
        shutil.rmtree(destination)
    for name, content in files.items():
        target = STATE / "fixtures" / name
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
        if target.is_symlink():
            raise RuntimeFailed("unsafe fixture destination")
        with target.open("wb") as stream:
            stream.write(content)
        target.chmod(0o644)


def read_credentials(bootstrap):
    identity = json.loads(
        command(
            [
                "aws",
                "sts",
                "get-caller-identity",
                "--region",
                REGION,
                "--output",
                "json",
                "--no-cli-pager",
            ]
        )
    )
    if identity.get("Account") != bootstrap["account_id"]:
        raise RuntimeFailed("host account differs from verified bootstrap")
    response = json.loads(
        command(
            [
                "aws",
                "secretsmanager",
                "get-secret-value",
                "--secret-id",
                bootstrap["secret_arn"],
                "--region",
                REGION,
                "--output",
                "json",
                "--no-cli-pager",
            ]
        )
    )
    values = json.loads(response["SecretString"])
    if set(values) != {"auth_db_password", "items_db_password", "e2e_password"} or any(
        not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", value)
        for value in values.values()
    ):
        raise RuntimeFailed("dedicated credentials are malformed")
    return values


def runtime_environment(images, generation):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("AWS_", "GITHUB_", "ACTIONS_"))
        and key not in ["GH_TOKEN", "GH_ENTERPRISE_TOKEN"]
    }
    env.update({name.upper() + "_IMAGE": image for name, image in images.items()})
    env.update(GENERATION=generation, DOCKER_CONFIG=str(STATE / "docker-config"))
    (STATE / "docker-config").mkdir(mode=0o700, exist_ok=True)
    return env


def write_secret_files(values):
    for filename, content in [
        (
            "run.env",
            "AUTH_DB_PASSWORD="
            + values["auth_db_password"]
            + "\nITEMS_DB_PASSWORD="
            + values["items_db_password"]
            + "\n",
        ),
        (
            "e2e.env",
            "E2E_BASE_URL=http://api-gateway:10000\nE2E_TEST_PASSWORD="
            + values["e2e_password"]
            + "\n",
        ),
    ]:
        descriptor = os.open(
            STATE / filename,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)


def verify_project_ownership(env):
    for kind, listing in [
        ("container", ["docker", "ps", "-aq"]),
        ("volume", ["docker", "volume", "ls", "-q"]),
    ]:
        names = (
            command(
                [*listing, "--filter", "label=com.docker.compose.project=" + PROJECT],
                env=env,
            )
            .decode()
            .split()
        )
        for name in names:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
                raise RuntimeFailed("unexpected owned resource identity")
            records = json.loads(command(["docker", kind, "inspect", name], env=env))
            labels = (
                records[0].get("Config", {}).get("Labels", {})
                if kind == "container"
                else records[0].get("Labels", {})
            )
            if (
                labels.get("ManagedBy") != PROJECT
                or labels.get("com.docker.compose.project") != PROJECT
            ):
                raise RuntimeFailed("unknown project resource ownership")


def login_registry(bootstrap, env):
    password = command(
        ["aws", "ecr", "get-login-password", "--region", REGION, "--no-cli-pager"]
    )
    command(
        [
            "docker",
            "login",
            "--username",
            "AWS",
            "--password-stdin",
            f"{bootstrap['account_id']}.dkr.ecr.{REGION}.amazonaws.com",
        ],
        env=env,
        data=password,
    )


def compose(arguments, env, timeout):
    return command(
        [
            "docker",
            "compose",
            "--project-name",
            PROJECT,
            "--file",
            str(ROOT / "compose.yml"),
            "--env-file",
            str(STATE / "run.env"),
            *arguments,
        ],
        env=env,
        timeout=timeout,
    )


def execute_e2e(image, env, generation):
    deadline = time.monotonic() + 900
    command(
        [
            "docker",
            "run",
            "-d",
            "--name",
            PROJECT + "-e2e",
            "--label",
            "ManagedBy=" + PROJECT,
            "--label",
            "Generation=" + generation,
            "--label",
            "com.docker.compose.project=" + PROJECT,
            "--network",
            PROJECT + "_network",
            "--user",
            "10001:10001",
            "--workdir",
            "/workspace/e2e-tests",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges:true",
            "--read-only",
            "--pids-limit",
            "512",
            "--memory",
            "1g",
            "--cpus",
            "2",
            "--env-file",
            str(STATE / "e2e.env"),
            "--tmpfs",
            "/tmp:rw,size=128m,uid=10001,gid=10001,mode=1777",
            "--tmpfs",
            "/workspace/e2e-tests/.build:rw,size=512m,uid=10001,gid=10001,mode=0755",
            "--tmpfs",
            "/home/tests/.m2/wrapper:rw,size=64m,uid=10001,gid=10001,mode=0755",
            "--entrypoint",
            "/bin/sleep",
            image,
            "infinity",
        ],
        env=env,
        timeout=min(60, remaining(deadline)),
    )
    # Keep the isolated container alive until reports are copied: tmpfs would
    # vanish when a blocking `docker run ./mvnw` exits, including on test failure.
    command(
        [
            "docker",
            "exec",
            "--user",
            "10001:10001",
            "--workdir",
            "/workspace/e2e-tests",
            PROJECT + "-e2e",
            "./mvnw",
            "--batch-mode",
            "--offline",
            "-De2e.build.directory=/workspace/e2e-tests/.build/target",
            "clean",
            "test",
        ],
        env=env,
        timeout=remaining(deadline),
    )


def collect_reports(env, credentials, generation):
    data = command(
        [
            "docker",
            "exec",
            "--user",
            "10001:10001",
            PROJECT + "-e2e",
            "tar",
            "-c",
            "-C",
            "/workspace/e2e-tests/.build/target",
            "surefire-reports",
        ],
        env=env,
        timeout=120,
    )
    if not data:
        raise RuntimeFailed("required reports are missing")
    suites = {}
    unsuccessful = False
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        seen, total = set(), 0
        for member in archive:
            path = Path(member.name)
            total += member.size
            if (
                path.is_absolute()
                or ".." in path.parts
                or member.name in seen
                or not (member.isfile() or member.isdir())
                or total > MAX_DATA
            ):
                raise RuntimeFailed("unsafe report archive")
            seen.add(member.name)
            if not member.isfile() or not member.name.endswith(".xml"):
                continue
            if (
                len(path.parts) != 2
                or path.parts[0] != "surefire-reports"
                or member.size > 1024**2
            ):
                raise RuntimeFailed("unexpected report path")
            raw = archive.extractfile(member).read()
            if b"<!DOCTYPE" in raw.upper() or b"<!ENTITY" in raw.upper():
                raise RuntimeFailed("unsafe report XML")
            source = ET.fromstring(raw)
            name = source.get("name")
            if source.tag != "testsuite" or name not in SUITES or name in suites:
                raise RuntimeFailed("unexpected test suite")
            counts = {
                key: int(source.get(key, "-1"))
                for key in ["tests", "failures", "errors", "skipped"]
            }
            cases = source.findall("testcase")
            if any(value < 0 or value > 100 for value in counts.values()):
                raise RuntimeFailed("invalid bounded report counts")
            if (
                counts
                != {"tests": SUITES[name], "failures": 0, "errors": 0, "skipped": 0}
                or len(cases) != SUITES[name]
                or any(
                    case.find("failure") is not None
                    or case.find("error") is not None
                    or case.find("skipped") is not None
                    for case in cases
                )
            ):
                unsuccessful = True
            # Retain only recognized executed cases, never arbitrary system output,
            # properties/environment dumps or test-produced error messages.
            cleaned = ET.Element(
                "testsuite",
                {"name": name, **{key: str(value) for key, value in counts.items()}},
            )
            for index, case in enumerate(cases, 1):
                label = case.get("name", "")
                if not re.fullmatch(r"[A-Za-z0-9_.]{1,128}", label) or any(
                    value in label for value in credentials.values()
                ):
                    label = "case-" + str(index)
                retained = ET.SubElement(
                    cleaned, "testcase", {"name": label, "classname": name}
                )
                for outcome in ["failure", "error", "skipped"]:
                    if case.find(outcome) is not None:
                        ET.SubElement(
                            retained,
                            outcome,
                            {
                                "message": "Test outcome retained; candidate-produced details omitted"
                            },
                        )
            suites[name] = cleaned
    if set(suites) != set(SUITES):
        raise RuntimeFailed("required recognized reports are missing")
    directory = STATE / "reports" / generation
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name, suite in suites.items():
        ET.ElementTree(suite).write(
            directory / ("TEST-" + name + ".xml"),
            encoding="utf-8",
            xml_declaration=True,
        )
    if unsuccessful:
        raise RuntimeFailed(
            "required tests failed or were not executed; sanitized reports retained"
        )


def verify_no_detached_test():
    # A terminal controller record is not proof that a detached container ended.
    # Fail before credentials/reset; only reconciled recovery may remove it.
    try:
        names = command(
            ["docker", "ps", "-aq", "--filter", "name=^" + PROJECT + "-e2e$"],
            timeout=10,
        )
    except RuntimeFailed:
        raise TerminationUnknown("detached test absence cannot be verified") from None
    if names.strip():
        raise TerminationUnknown("detached test requires reconciliation")


def remove_test_container(env, generation):
    try:
        names = (
            command(
                ["docker", "ps", "-aq", "--filter", "name=^" + PROJECT + "-e2e$"],
                env=env,
                timeout=10,
            )
            .decode()
            .split()
        )
        if not names:
            return
        records = json.loads(
            command(
                ["docker", "container", "inspect", PROJECT + "-e2e"],
                env=env,
                timeout=10,
            )
        )
    except RuntimeFailed:
        raise TerminationUnknown("test termination cannot be verified") from None
    labels = records[0].get("Config", {}).get("Labels", {})
    if labels.get("ManagedBy") != PROJECT or labels.get("Generation") != generation:
        raise TerminationUnknown("test container ownership is unknown")
    try:
        command(
            ["docker", "rm", "--force", "--volumes", PROJECT + "-e2e"],
            env=env,
            timeout=30,
        )
        if command(
            ["docker", "ps", "-aq", "--filter", "name=^" + PROJECT + "-e2e$"],
            env=env,
            timeout=10,
        ).strip():
            raise TerminationUnknown("test container still exists after termination")
    except RuntimeFailed:
        raise TerminationUnknown("test termination cannot be verified") from None


def command(arguments, *, env=None, data=None, timeout=30):
    try:
        with (
            tempfile.TemporaryFile(dir=STATE) as output,
            tempfile.TemporaryFile(dir=STATE) as errors,
        ):
            result = subprocess.run(
                arguments,
                env=env,
                input=data,
                stdout=output,
                stderr=errors,
                timeout=timeout,
                check=False,
                preexec_fn=limit_command_output,
            )
            output.seek(0)
            data = output.read(MAX_DATA + 1)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        raise RuntimeFailed(
            "bounded external command timed out or is unavailable"
        ) from None
    if result.returncode or len(data) > MAX_DATA:
        raise RuntimeFailed("required bounded external command failed")
    return data


def limit_command_output():
    # Trusted host execution is single-threaded. Bound each anonymous private
    # output file in the child before it can emit unlimited candidate logs.
    resource.setrlimit(resource.RLIMIT_FSIZE, (MAX_DATA, MAX_DATA))


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise RuntimeFailed("reset/deployment stage timed out")
    return value


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def record_stage(operation, stage):
    operation["stages"].append({"name": stage, "status": "passed", "at": timestamp()})
    save_operation(operation)


def save_operation(operation):
    path = STATE / "operation.json"
    temporary = STATE / "operation.new"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
    )
    with os.fdopen(descriptor, "w") as stream:
        json.dump(operation, stream, indent=2)
    os.replace(temporary, path)


if __name__ == "__main__":
    sys.exit(main())
