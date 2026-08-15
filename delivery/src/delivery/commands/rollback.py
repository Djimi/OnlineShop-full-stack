"""rollback preflight and execute commands, implemented in phase 4."""

from __future__ import annotations

import argparse

from ..errors import NotImplementedPhaseError


def preflight(args: argparse.Namespace) -> int:
    """Select and guard a rollback target (not implemented until phase 4)."""
    raise NotImplementedPhaseError("rollback preflight: implemented in phase 4")


def execute(args: argparse.Namespace) -> int:
    """Deploy the approved rollback target (not implemented until phase 4)."""
    raise NotImplementedPhaseError("rollback execute: implemented in phase 4")
