"""finalize command, implemented in phase 4."""

from __future__ import annotations

import argparse

from ..errors import NotImplementedPhaseError


def finalize(args: argparse.Namespace) -> int:
    """Finalize an approved release (not implemented until phase 4)."""
    raise NotImplementedPhaseError("finalize: implemented in phase 4")
