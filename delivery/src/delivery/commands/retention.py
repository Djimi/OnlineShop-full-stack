"""retention audit, preview, and apply commands, implemented in phase 4."""

from __future__ import annotations

import argparse

from ..errors import NotImplementedPhaseError


def audit(args: argparse.Namespace) -> int:
    """Audit the 10-release rollback window (not implemented until phase 4)."""
    raise NotImplementedPhaseError("retention audit: implemented in phase 4")


def preview(args: argparse.Namespace) -> int:
    """Preview a lifecycle policy effect (not implemented until phase 4)."""
    raise NotImplementedPhaseError("retention preview: implemented in phase 4")


def apply(args: argparse.Namespace) -> int:
    """Apply a lifecycle policy (not implemented until phase 4)."""
    raise NotImplementedPhaseError("retention apply: implemented in phase 4")
