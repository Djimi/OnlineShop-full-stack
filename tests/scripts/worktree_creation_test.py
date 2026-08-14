#!/usr/bin/env python3
"""Black-box tests for scripts/create-worktree.py."""

from __future__ import annotations

import hashlib
import re
import socket
import subprocess
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = PROJECT_ROOT / "scripts" / "create-worktree.py"
BASE_PORT = 20_000
BLOCK_SIZE = 20
SLOT_COUNT = 631


class WorktreeCreationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.repository = Path(self.temporary_directory.name) / "shop"
        self.repository.mkdir()

        self.git("init", "-b", "main")
        self.git("config", "user.name", "Test User")
        self.git("config", "user.email", "test@example.com")
        (self.repository / "README.md").write_text("fixture\n")
        self.git("add", "README.md")
        self.git("commit", "-m", "initial")
        self.git("branch", "incompatible-base")

        write_compose_contract(self.repository / "docker-compose.yml")
        self.git("add", "docker-compose.yml")
        self.git("commit", "-m", "add worktree port contract")

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_creation_writes_compose_values_and_preserves_existing_env(self) -> None:
        self.install_checkout_hook("UNCHANGED_VALUE=keep-me\n")

        result = self.create("payments", "feature/payments")

        self.assertEqual(result.returncode, 0, result.stderr)
        target = self.target("payments")
        values = env_values(target / ".env")
        slot = int(values["WORKTREE_SLOT"])
        first_port = BASE_PORT + slot * BLOCK_SIZE

        self.assertTrue(target.is_dir())
        self.assertIn("UNCHANGED_VALUE=keep-me\n", (target / ".env").read_text())
        self.assertEqual(values["COMPOSE_PROJECT_NAME"], f"onlineshop-wt{slot}")
        self.assertEqual(
            [int(values[name]) for name in port_names()],
            list(range(first_port, first_port + 10)),
        )

    def test_claimed_slot_is_skipped_even_when_the_first_stack_is_stopped(self) -> None:
        first_name, second_name = names_with_same_hash_slot()

        first = self.create(first_name, "feature/first")
        second = self.create(second_name, "feature/second")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertNotEqual(self.slot(first_name), self.slot(second_name))

    def test_listener_on_a_reserved_offset_skips_the_whole_block(self) -> None:
        name = "listener-test"
        expected_slot = hash_slot(name)
        reserved_port = BASE_PORT + expected_slot * BLOCK_SIZE + 15

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", reserved_port))
            listener.listen()
            result = self.create(name, "feature/listener")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotEqual(self.slot(name), expected_slot)

    def test_concurrent_creators_receive_different_slots(self) -> None:
        first_name, second_name = names_with_same_hash_slot()
        first = self.start_create(first_name, "feature/concurrent-one")
        second = self.start_create(second_name, "feature/concurrent-two")

        first_stdout, first_stderr = first.communicate(timeout=20)
        second_stdout, second_stderr = second.communicate(timeout=20)

        self.assertEqual(first.returncode, 0, first_stdout + first_stderr)
        self.assertEqual(second.returncode, 0, second_stdout + second_stderr)
        self.assertNotEqual(self.slot(first_name), self.slot(second_name))

    def test_allocation_failure_keeps_the_worktree_and_prints_recovery(self) -> None:
        owner = self.create("owner", "feature/owner")
        self.assertEqual(owner.returncode, 0, owner.stderr)
        env_file = self.target("owner") / ".env"
        env_file.write_text(
            "# <<< worktree ports\n"
            "# >>> worktree ports (managed by scripts/create-worktree.py)\n"
        )

        failed = self.create("failed", "feature/failed")

        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue(self.target("failed").is_dir())
        self.assertIn("worktree and branch were left in place", failed.stderr)
        self.assertIn("worktree remove", failed.stderr)

    def test_inconsistent_project_name_stops_allocation(self) -> None:
        owner = self.create("owner", "feature/owner")
        self.assertEqual(owner.returncode, 0, owner.stderr)
        env_file = self.target("owner") / ".env"
        env_file.write_text(
            env_file.read_text().replace(
                f"COMPOSE_PROJECT_NAME=onlineshop-wt{self.slot('owner')}",
                "COMPOSE_PROJECT_NAME=wrong-project",
            )
        )

        failed = self.create("failed", "feature/failed")

        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("COMPOSE_PROJECT_NAME does not match", failed.stderr)

    def test_inconsistent_port_stops_allocation(self) -> None:
        owner = self.create("owner", "feature/owner")
        self.assertEqual(owner.returncode, 0, owner.stderr)
        env_file = self.target("owner") / ".env"
        values = env_values(env_file)
        env_file.write_text(
            env_file.read_text().replace(
                f"KAFKA_UI_PORT={values['KAFKA_UI_PORT']}", "KAFKA_UI_PORT=1234"
            )
        )

        failed = self.create("failed", "feature/failed")

        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("KAFKA_UI_PORT does not match", failed.stderr)

    def test_incompatible_base_ref_is_rejected_after_creation(self) -> None:
        failed = self.create(
            "old-base", "feature/old-base", base_ref="incompatible-base"
        )

        self.assertNotEqual(failed.returncode, 0)
        self.assertTrue(self.target("old-base").is_dir())
        self.assertIn("base ref has no docker-compose.yml", failed.stderr)
        self.assertIn("worktree remove", failed.stderr)

    def create(
        self, name: str, branch: str, base_ref: str = "main"
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), name, "-b", branch, base_ref],
            cwd=self.repository,
            text=True,
            capture_output=True,
            check=False,
        )

    def start_create(self, name: str, branch: str) -> subprocess.Popen[str]:
        return subprocess.Popen(
            ["python3", str(SCRIPT), name, "-b", branch, "main"],
            cwd=self.repository,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def git(self, *arguments: str) -> None:
        subprocess.run(
            ["git", *arguments],
            cwd=self.repository,
            check=True,
            stdout=subprocess.DEVNULL,
        )

    def target(self, name: str) -> Path:
        return self.repository.parent / "shop-worktrees" / name

    def slot(self, name: str) -> int:
        return int(env_values(self.target(name) / ".env")["WORKTREE_SLOT"])

    def install_checkout_hook(self, contents: str) -> None:
        hook = self.repository / ".git" / "hooks" / "post-checkout"
        hook.write_text(f"#!/bin/sh\nprintf '%s' '{contents}' > .env\n")
        hook.chmod(0o755)


def port_names() -> tuple[str, ...]:
    return (
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


def write_compose_contract(compose_file: Path) -> None:
    lines = ["name: ${COMPOSE_PROJECT_NAME:-shop}", "services: {}", "x-ports:"]
    lines.extend(f"  {name}: ${{{name}:-0}}" for name in port_names())
    compose_file.write_text("\n".join(lines) + "\n")


def env_values(env_file: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def hash_slot(name: str) -> int:
    digest = hashlib.sha256(name.encode()).digest()
    return (int.from_bytes(digest[:8], "big") % SLOT_COUNT) + 1


def names_with_same_hash_slot() -> tuple[str, str]:
    names_by_slot: dict[int, str] = {}
    for number in range(SLOT_COUNT + 2):
        name = f"collision-{number}"
        slot = hash_slot(name)
        if slot in names_by_slot:
            return names_by_slot[slot], name
        names_by_slot[slot] = name
    raise AssertionError("pigeonhole principle failed")


class ComposePortContractTest(unittest.TestCase):
    def test_main_ports_are_unique_and_outside_the_worktree_range(self) -> None:
        compose = (PROJECT_ROOT / "docker-compose.yml").read_text()
        defaults: dict[str, int] = {}

        for name in port_names():
            matches = set(re.findall(rf"\$\{{{name}:-([0-9]+)\}}", compose))
            self.assertEqual(len(matches), 1, f"expected one default for {name}")
            defaults[name] = int(matches.pop())

        worktree_range = range(
            BASE_PORT + BLOCK_SIZE,
            BASE_PORT + SLOT_COUNT * BLOCK_SIZE + BLOCK_SIZE,
        )
        self.assertEqual(len(set(defaults.values())), len(defaults))
        self.assertTrue(all(port not in worktree_range for port in defaults.values()))
        self.assertEqual(defaults["KAFKA_HOST_PORT"], 9092)


if __name__ == "__main__":
    unittest.main(verbosity=2)
