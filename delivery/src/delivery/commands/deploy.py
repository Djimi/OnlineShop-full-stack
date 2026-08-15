"""deploy commands for backends, gateway, and frontend, implemented in phase 4."""

from __future__ import annotations

import argparse

from ..errors import NotImplementedPhaseError


def backends(args: argparse.Namespace) -> int:
    """Deploy the backend services (not implemented until phase 4)."""
    raise NotImplementedPhaseError("deploy backends: implemented in phase 4")


def gateway(args: argparse.Namespace) -> int:
    """Deploy the API gateway service (not implemented until phase 4)."""
    raise NotImplementedPhaseError("deploy gateway: implemented in phase 4")


def frontend(args: argparse.Namespace) -> int:
    """Deploy the frontend (not implemented until phase 4)."""
    raise NotImplementedPhaseError("deploy frontend: implemented in phase 4")
