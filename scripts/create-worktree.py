#!/usr/bin/env python3
"""Create a Git worktree with an isolated Docker Compose environment.

Contract
--------
The command creates a new branch and worktree, then configures that worktree;
it never starts or stops services. The selected base commit must contain a
``docker-compose.yml`` that consumes the generated project and port variables.

Port allocation is serialized by a lock in Git's common directory. While the
lock is held, the command validates managed claims in every registered
worktree, starts at a deterministic slot derived from the new directory name,
and moves forward until it finds an unclaimed 20-port block whose ports can all
be bound locally. It then atomically writes the selected Compose project, slot,
and ten currently assigned ports to a marked block in ``.env``. Content outside
that managed block is preserved.

If setup fails after Git creates the worktree, the worktree is deliberately
kept for inspection and explicit recovery commands are printed. Claims are
coordinated only within the same clone; occupied local ports provide the safety
check for unrelated processes and separate clones.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import shlex
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_PORT = 20_000
BLOCK_SIZE = 20
SLOT_COUNT = 631

PORT_NAMES = (
    "GATEWAY_PORT",
    "ITEMS_PORT",
    "AUTH_PORT",
    "FRONTEND_PORT",
    "ITEMS_DB_PORT",
    "AUTH_DB_PORT",
    "PGADMIN_PORT",
    "REDIS_PORT",
    "KAFKA_HOST_PORT",
    "KAFKA_UI_PORT",
)

MARKER_START = "# >>> worktree ports (managed by scripts/create-worktree.py)"
MARKER_END = "# <<< worktree ports"


def main() -> int:
    """Run the creation workflow in its user-visible order."""

    args = parse_arguments()

    repository = find_repository()
    base_commit = resolve_base_commit(repository, args.base_ref)
    target = resolve_target(repository, args.target)
    validate_new_branch(repository, args.branch)
    ensure_target_is_available(target)

    create_worktree(repository, target, args.branch, base_commit)

    try:
        validate_compose_contract(target)
        slot = allocate_ports(repository, target)
    except (
        OSError,
        RuntimeError,
        UnicodeError,
        subprocess.CalledProcessError,
    ) as error:
        print(
            f"ERROR: worktree created, but environment setup failed: {error}",
            file=sys.stderr,
        )
        print_recovery(repository, target, args.branch)
        return 1

    print_result(target, slot)
    return 0


def parse_arguments() -> argparse.Namespace:
    """Parse the target, new branch, and optional base ref."""

    parser = argparse.ArgumentParser(
        description=(
            "Create a branch and worktree, reserve a unique 20-port block, "
            "and write its Docker Compose values to .env."
        )
    )
    parser.add_argument(
        "target",
        help="target path, or a name placed in the repository's sibling worktrees directory",
    )
    parser.add_argument("-b", "--branch", required=True, help="new branch name")
    parser.add_argument(
        "base_ref", nargs="?", default="main", help="base ref (default: main)"
    )
    return parser.parse_args()


# Git worktree creation -----------------------------------------------------


def find_repository() -> Path:
    """Return the current checkout's repository root."""

    try:
        return Path(git_output(Path.cwd(), "rev-parse", "--show-toplevel")).resolve()
    except RuntimeError as error:
        raise SystemExit(
            "ERROR: run this command from an existing Git checkout"
        ) from error


def resolve_base_commit(repository: Path, base_ref: str) -> str:
    """Resolve the requested base ref to one immutable commit."""

    try:
        return git_output(repository, "rev-parse", "--verify", f"{base_ref}^{{commit}}")
    except RuntimeError as error:
        raise SystemExit(
            f"ERROR: base ref does not resolve to a commit: {base_ref}"
        ) from error


def validate_new_branch(repository: Path, branch: str) -> None:
    """Reject branch names that Git cannot create."""

    result = subprocess.run(
        ["git", "check-ref-format", "--branch", branch],
        cwd=repository,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"ERROR: invalid branch name: {branch}")


def resolve_target(repository: Path, requested: str) -> Path:
    """Resolve a path directly or place a bare name beside the main worktree."""

    if os.sep in requested:
        return Path(requested).expanduser().resolve()

    main_worktree = list_worktrees(repository)[0]
    worktrees_directory = main_worktree.parent / f"{main_worktree.name}-worktrees"
    return worktrees_directory / requested


def ensure_target_is_available(target: Path) -> None:
    """Require a new target path so existing content is never overwritten."""

    if target.exists() or target.is_symlink():
        raise SystemExit(f"ERROR: target path already exists: {target}")


def create_worktree(
    repository: Path, target: Path, branch: str, base_commit: str
) -> None:
    """Create the requested branch and Git worktree."""

    target.parent.mkdir(parents=True, exist_ok=True)
    print(f"Creating {target} on branch {branch}...")
    subprocess.run(
        ["git", "worktree", "add", str(target), "-b", branch, base_commit],
        cwd=repository,
        check=True,
    )


def validate_compose_contract(target: Path) -> None:
    """Require the target Compose file to consume every generated value."""

    compose_file = target / "docker-compose.yml"
    try:
        compose = compose_file.read_text()
    except FileNotFoundError as error:
        raise RuntimeError(f"base ref has no {compose_file.name}") from error

    required_variables = ("COMPOSE_PROJECT_NAME", *PORT_NAMES)
    missing = [
        name
        for name in required_variables
        if f"${{{name}}}" not in compose and f"${{{name}:" not in compose
    ]
    if missing:
        raise RuntimeError(
            "base ref's docker-compose.yml does not use: " + ", ".join(missing)
        )


# Port allocation -----------------------------------------------------------


def allocate_ports(repository: Path, target: Path) -> int:
    """Select and persist one slot while holding the clone-wide lock."""

    git_common_directory = Path(
        git_output(
            repository, "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
    )
    lock_path = git_common_directory / "worktree-port-allocation.lock"

    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        claimed_slots = read_claimed_slots(repository, target)
        slot = find_available_slot(target.name, claimed_slots)
        write_environment(target / ".env", slot)

    return slot


def read_claimed_slots(repository: Path, target: Path) -> set[int]:
    """Validate and collect claims from the clone's other worktrees."""

    claimed_slots: set[int] = set()

    for worktree in list_worktrees(repository):
        if worktree == target or not worktree.exists():
            continue

        slot = read_claimed_slot(worktree / ".env")
        if slot is None:
            continue
        if slot in claimed_slots:
            raise RuntimeError(f"multiple worktrees already claim slot {slot}")
        claimed_slots.add(slot)

    return claimed_slots


def find_available_slot(worktree_name: str, claimed_slots: set[int]) -> int:
    """Probe from the name's stable candidate until a complete block is free."""

    first_slot = hash_slot(worktree_name)

    for offset in range(SLOT_COUNT):
        slot = ((first_slot - 1 + offset) % SLOT_COUNT) + 1
        if slot not in claimed_slots and block_is_free(slot):
            return slot

    raise RuntimeError(f"all {SLOT_COUNT} worktree port blocks are in use")


def hash_slot(worktree_name: str) -> int:
    """Map a worktree name to its stable first slot candidate."""

    digest = hashlib.sha256(worktree_name.encode()).digest()
    return (int.from_bytes(digest[:8], "big") % SLOT_COUNT) + 1


def block_is_free(slot: int) -> bool:
    """Return whether all 20 ports in a slot can currently be bound."""

    first_port = BASE_PORT + slot * BLOCK_SIZE
    return all(port_is_free(first_port + offset) for offset in range(BLOCK_SIZE))


def port_is_free(port: int) -> bool:
    """Check whether one IPv4 loopback TCP port can currently be bound."""

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as candidate:
        try:
            candidate.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


# Managed .env block --------------------------------------------------------


def read_claimed_slot(env_file: Path) -> int | None:
    """Return a valid managed slot, or None when the file has no claim."""

    if not env_file.exists():
        return None

    contents = env_file.read_text()
    block = find_managed_block(contents, env_file)
    if block is None:
        return None

    start, end = block
    managed = contents[start + len(MARKER_START) : end - len(MARKER_END)]
    values: dict[str, str] = {}
    for line in managed.splitlines():
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"invalid line in managed block in {env_file}")
        name, value = line.split("=", 1)
        if name in values:
            raise RuntimeError(f"duplicate {name} in {env_file}")
        values[name] = value

    try:
        slot = int(values["WORKTREE_SLOT"])
    except (KeyError, ValueError) as error:
        raise RuntimeError(f"invalid WORKTREE_SLOT in {env_file}") from error

    if not 1 <= slot <= SLOT_COUNT:
        raise RuntimeError(
            f"WORKTREE_SLOT in {env_file} must be between 1 and {SLOT_COUNT}"
        )

    for name, expected in environment_values(slot).items():
        if values.get(name) != expected:
            raise RuntimeError(f"{name} does not match WORKTREE_SLOT in {env_file}")

    return slot


def write_environment(env_file: Path, slot: int) -> None:
    """Atomically add or replace the managed block, preserving other content."""

    existing = env_file.read_text() if env_file.exists() else ""
    managed_block = render_environment(slot)
    block = find_managed_block(existing, env_file)

    if block is None:
        separator = (
            "" if not existing else ("\n" if existing.endswith("\n") else "\n\n")
        )
        updated = existing + separator + managed_block
    else:
        start, end = block
        updated = existing[:start] + managed_block + existing[end:]

    atomic_write(env_file, updated)


def render_environment(slot: int) -> str:
    """Render the complete marked block written for one slot."""

    lines = [MARKER_START]
    lines.extend(f"{name}={value}" for name, value in environment_values(slot).items())
    lines.append(MARKER_END)
    return "\n".join(lines) + "\n"


def environment_values(slot: int) -> dict[str, str]:
    """Build the Compose project, slot, and assigned port values."""

    first_port = BASE_PORT + slot * BLOCK_SIZE
    values = {
        "COMPOSE_PROJECT_NAME": f"onlineshop-wt{slot}",
        "WORKTREE_SLOT": str(slot),
    }
    values.update(
        (name, str(first_port + offset)) for offset, name in enumerate(PORT_NAMES)
    )
    return values


def find_managed_block(contents: str, source: Path) -> tuple[int, int] | None:
    """Locate one well-ordered managed block and reject malformed markers."""

    start_count = contents.count(MARKER_START)
    end_count = contents.count(MARKER_END)
    if start_count == 0 and end_count == 0:
        return None
    if start_count != 1 or end_count != 1:
        raise RuntimeError(f"malformed managed block in {source}")

    start = contents.index(MARKER_START)
    end = contents.index(MARKER_END)
    if end < start:
        raise RuntimeError(f"malformed managed block in {source}")
    return start, end + len(MARKER_END)


def atomic_write(path: Path, contents: str) -> None:
    """Replace a file from a sibling temporary file in one filesystem step."""

    file_descriptor, temporary_name = tempfile.mkstemp(prefix=".env.", dir=path.parent)
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w") as temporary_file:
            temporary_file.write(contents)
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


# Git output and user-facing result -----------------------------------------


def list_worktrees(repository: Path) -> list[Path]:
    """List every worktree registered in the repository's common Git data."""

    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain", "-z"],
        cwd=repository,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        message = os.fsdecode(result.stderr).strip() or "git worktree list failed"
        raise RuntimeError(message)

    return [
        Path(os.fsdecode(record.removeprefix(b"worktree "))).resolve()
        for record in result.stdout.split(b"\0")
        if record.startswith(b"worktree ")
    ]


def git_output(repository: Path, *arguments: str) -> str:
    """Run a read-only Git query and return its stripped standard output."""

    result = subprocess.run(
        ["git", *arguments],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Git command failed")
    return result.stdout.strip()


def print_recovery(repository: Path, target: Path, branch: str) -> None:
    """Explain how to remove a worktree retained after setup failure."""

    repository_argument = shlex.quote(str(repository))
    target_argument = shlex.quote(str(target))
    branch_argument = shlex.quote(branch)
    print(
        f"""
The worktree and branch were left in place for inspection.
After fixing the setup problem, remove them and run the creation command again:

  git -C {repository_argument} worktree remove {target_argument}
  git -C {repository_argument} branch -D {branch_argument}

Use --force only after checking that the worktree contains nothing valuable.
""".rstrip(),
        file=sys.stderr,
    )


def print_result(target: Path, slot: int) -> None:
    """Show the selected ports and the next command to run."""

    first_port = BASE_PORT + slot * BLOCK_SIZE
    print(f"\nCreated worktree with port slot {slot}:")
    for offset, name in enumerate(PORT_NAMES):
        print(f"  {name:<22} {first_port + offset}")
    print(f"\nNext:\n  cd {shlex.quote(str(target))}\n  docker compose up -d --build")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.returncode) from error
    except (OSError, RuntimeError, UnicodeError) as error:
        raise SystemExit(f"ERROR: {error}") from error
