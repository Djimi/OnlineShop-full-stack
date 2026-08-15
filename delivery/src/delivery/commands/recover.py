"""recover command, implemented in phase 4."""

from __future__ import annotations

import argparse

from ..errors import NotImplementedPhaseError


def recover(args: argparse.Namespace) -> int:
    """Compensate changed components from a snapshot (not implemented until phase 4)."""
    raise NotImplementedPhaseError("recover: implemented in phase 4")
