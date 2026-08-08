"""Staging mutation serialization model (Pass 3, subphase 3.2).

The staging resume → deploy → E2E → teardown path is a singleton mutation
shared by every successful ``main`` push. GitHub Actions job-level concurrency
with ``cancel-in-progress: false`` serializes it so a newer ``main`` push can
never race an older run's cleanup: the newer run's staging job is queued until
the older run's job — including its ``always()`` teardown step — finishes.

This module is a small deterministic model of those semantics. Offline
fixtures replay two-run timelines and prove the invariant that at most one run
owns the staging mutation at any instant and that a queued (newer) run cannot
displace the current owner while it is still in its critical section
(including teardown). ``simulate`` returns the owner history and any
violations; fixtures expect zero violations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class StagingMutexError(Exception):
    """Raised when an operation violates the serialization contract."""


@dataclass
class StagingMutex:
    """Single-owner mutex mirroring job-level concurrency semantics.

    Only the current owner may ``release``. A different run that calls
    ``try_acquire`` while the mutex is held is queued (returned ``False``) and
    must wait; it becomes owner only when the current owner releases. This is
    exactly ``cancel-in-progress: false``: a newer run never preempts an older
    run's in-flight critical section.
    """

    _owner: int | None = field(default=None, init=False)
    _queue: list[int] = field(default_factory=list, init=False)
    _violations: list[str] = field(default_factory=list, init=False)

    def owner(self) -> int | None:
        return self._owner

    def try_acquire(self, run_id: int) -> bool:
        """Return True and take ownership when free; otherwise queue and return False."""
        if run_id == self._owner:
            return True
        if self._owner is None:
            self._owner = run_id
            self._drain_queue()
            return True
        if run_id not in self._queue:
            self._queue.append(run_id)
        return False

    def release(self, run_id: int) -> None:
        """Release ownership held by ``run_id`` and grant the next queued run."""
        if self._owner != run_id:
            raise StagingMutexError(
                f"run {run_id} tried to release ownership held by run {self._owner}"
            )
        self._owner = None
        self._drain_queue()

    def _drain_queue(self) -> None:
        # FIFO: the oldest waiting run owns next (matches GitHub job-level
        # concurrency, which starts the first queued job when the holder ends).
        if self._owner is None and self._queue:
            self._owner = self._queue.pop(0)

    def record_violation(self, message: str) -> None:
        self._violations.append(message)

    @property
    def violations(self) -> list[str]:
        return list(self._violations)


def simulate(timeline: list[dict[str, Any]]) -> dict[str, Any]:
    """Replay an event timeline against a ``StagingMutex``.

    ``timeline`` events: ``{"event": "acquire", "run": <int>}`` or
    ``{"event": "release", "run": <int>}``. Returns
    ``{"owners": [owner-or-None after each event], "violations": [...]}``.
    An acquire while owned is *not* a violation by itself (the run is queued);
    only a preemptive takeover, a foreign release, or two simultaneous owners
    violates the contract.
    """
    mutex = StagingMutex()
    owners: list[int | None] = []
    events: list[str] = []
    for event in timeline:
        kind = event.get("event")
        run = event.get("run")
        if kind == "acquire":
            mutex.try_acquire(run)
        elif kind == "release":
            try:
                mutex.release(run)
            except StagingMutexError as exc:
                mutex.record_violation(str(exc))
        else:
            mutex.record_violation(f"unknown event {kind!r}")
        owners.append(mutex.owner())
        events.append(kind or "")

    # Invariant: ownership may only change via an explicit release of the
    # previous owner (the FIFO queue then grants the oldest waiter). A direct
    # handover on an acquire would be a preemptive takeover
    # (cancel-in-progress) — exactly what the serialization must prevent.
    preemptions: list[str] = []
    for index, owner in enumerate(owners):
        if index == 0 or owner is None:
            continue
        previous = owners[index - 1]
        if previous is not None and previous != owner and events[index] != "release":
            preemptions.append(f"run {previous} was displaced by run {owner} at step {index}")

    return {
        "owners": owners,
        "violations": mutex.violations,
        "preemptions": preemptions,
    }
