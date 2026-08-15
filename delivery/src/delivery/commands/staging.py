"""staging lifecycle and apply commands, implemented in phase 4."""

from __future__ import annotations

import argparse

from ..errors import NotImplementedPhaseError


def lifecycle(args: argparse.Namespace) -> int:
    """Run the staging lifecycle (not implemented until phase 4)."""
    raise NotImplementedPhaseError("staging lifecycle: implemented in phase 4")


def apply(args: argparse.Namespace) -> int:
    """Apply a candidate to the staging environment (not implemented until phase 4)."""
    raise NotImplementedPhaseError("staging apply: implemented in phase 4")
