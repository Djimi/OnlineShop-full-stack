"""Subprocess regression tests for the `python -m delivery` entry points.

These prove the CLI actually executes as a real process (the original bug:
`python -m delivery.cli` imported the module and exited 0 doing nothing).
They must run under the delivery venv (pydantic/boto3 installed);
PYTHONPATH is computed from the repository layout so the source tree is
imported regardless of the current working directory.
"""

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"

ENTRY_MODULES = ["delivery", "delivery.cli"]


def _run(module: str, *argv: str) -> subprocess.CompletedProcess:
    existing = os.environ.get("PYTHONPATH")
    pythonpath = str(SRC) if not existing else f"{SRC}{os.pathsep}{existing}"
    return subprocess.run(
        [sys.executable, "-m", module, *argv],
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONPATH=pythonpath),
        timeout=60,
    )


def test_help_exits_zero():
    for module in ENTRY_MODULES:
        result = _run(module, "--help")
        assert result.returncode == 0, result.stderr
        assert "delivery" in result.stdout


def test_invalid_manifest_exits_nonzero_with_stderr():
    bogus = str(Path(__file__).parent / "fixtures" / "does-not-exist.json")
    for module in ENTRY_MODULES:
        result = _run(module, "candidate", "validate", "--manifest", bogus)
        assert result.returncode != 0, result.stdout
        assert result.stderr.strip(), f"{module} printed no error diagnostics"
        assert "ERROR" in result.stderr
