"""Unit tests for the staging serialization model (subphase 3.2)."""

import json
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract.serialization import StagingMutex, StagingMutexError, simulate

RELEASE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(RELEASE_ROOT, "fixtures", "serialization")


def fixture(name):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as handle:
        return json.load(handle)


class StagingMutexTests(unittest.TestCase):
    def test_first_acquire_wins(self):
        mutex = StagingMutex()
        self.assertTrue(mutex.try_acquire(100))
        self.assertFalse(mutex.try_acquire(200))
        self.assertEqual(mutex.owner(), 100)

    def test_newer_run_cannot_displace_older(self):
        mutex = StagingMutex()
        mutex.try_acquire(100)
        self.assertFalse(mutex.try_acquire(200))
        mutex.release(100)
        self.assertEqual(mutex.owner(), 200)

    def test_foreign_release_raises(self):
        mutex = StagingMutex()
        mutex.try_acquire(100)
        with self.assertRaises(StagingMutexError):
            mutex.release(200)

    def test_release_requires_owner(self):
        mutex = StagingMutex()
        with self.assertRaises(StagingMutexError):
            mutex.release(100)


class SimulateTests(unittest.TestCase):
    def test_serialized_two_runs_no_violations(self):
        result = simulate(fixture("serialized-two-runs.json"))
        self.assertEqual(result["owners"], [100, 100, 100, 200, 200])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["preemptions"], [])

    def test_newer_run_queued_until_older_cleanup_completes(self):
        # Run B (newer) attempts to take over while run A (older) is mid
        # critical section (events 2-3). B must never own the mutation before
        # A's release (event 4) completes the critical section.
        result = simulate(fixture("serialized-two-runs.json"))
        self.assertNotIn(200, result["owners"][:3])
        self.assertEqual(result["owners"][3], 200)
        self.assertEqual(result["preemptions"], [])

    def test_foreign_release_is_a_violation(self):
        result = simulate(fixture("foreign-release.json"))
        self.assertTrue(result["violations"])

    def test_adversarial_newer_takeover_fails_closed(self):
        # A malicious timeline where run 200 tries to release 100's ownership
        # and then take over while 100 is still in its critical section. The
        # mutex must record this as a violation and never silently grant 200
        # ownership.
        result = simulate(fixture("adversarial-newer-takeover.json"))
        self.assertTrue(result["violations"])
        self.assertEqual(result["owners"][0], 100)
        self.assertEqual(result["owners"][1], 100)


if __name__ == "__main__":
    unittest.main()
