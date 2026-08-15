"""verify commands for production and staging, implemented in phase 4."""

from __future__ import annotations

import argparse

from ..errors import NotImplementedPhaseError


def production(args: argparse.Namespace) -> int:
    """Verify production against an official manifest (not implemented until phase 4)."""
    raise NotImplementedPhaseError("verify production: implemented in phase 4")


def staging(args: argparse.Namespace) -> int:
    """Verify staging against a candidate (not implemented until phase 4)."""
    raise NotImplementedPhaseError("verify staging: implemented in phase 4")
