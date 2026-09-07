"""Tests for RDS instance start/stop adapters."""

import pytest
from conftest import client_error

from delivery.aws.rds import describe_db_instance, start_db_instance, stop_db_instance
from delivery.errors import AbsentResourceError, MutationVerificationError, ReadError

IDENTIFIER = "onlineshop-staging"


class FakeRds:
    def __init__(self, status="available"):
        self.status = status
        self.error = None

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def describe_db_instances(self, DBInstanceIdentifier):
        self._maybe_fail()
        if DBInstanceIdentifier != IDENTIFIER:
            raise client_error("DBInstanceNotFound")
        return {
            "DBInstances": [
                {"DBInstanceIdentifier": DBInstanceIdentifier, "DBInstanceStatus": self.status}
            ]
        }

    def start_db_instance(self, DBInstanceIdentifier):
        self._maybe_fail()
        self.status = "available"

    def stop_db_instance(self, DBInstanceIdentifier):
        self._maybe_fail()
        self.status = "stopped"


class BrokenStartFakeRds(FakeRds):
    def start_db_instance(self, DBInstanceIdentifier):
        self.status = "starting"


class BrokenStopFakeRds(FakeRds):
    def stop_db_instance(self, DBInstanceIdentifier):
        self.status = "stopping"


def test_describe_db_instance_ok():
    fake = FakeRds(status="stopped")
    instance = describe_db_instance(fake, IDENTIFIER)
    assert instance["DBInstanceStatus"] == "stopped"


def test_describe_db_instance_absent_is_absent():
    fake = FakeRds()
    with pytest.raises(AbsentResourceError):
        describe_db_instance(fake, "does-not-exist")


def test_describe_db_instance_other_error_is_read_error():
    fake = FakeRds()
    fake.error = client_error("AccessDenied")
    with pytest.raises(ReadError):
        describe_db_instance(fake, IDENTIFIER)


def test_start_db_instance_verified():
    fake = FakeRds(status="stopped")
    instance = start_db_instance(fake, IDENTIFIER)
    assert instance["DBInstanceStatus"] == "available"


def test_start_db_instance_status_mismatch_raises():
    fake = BrokenStartFakeRds(status="stopped")
    with pytest.raises(MutationVerificationError):
        start_db_instance(fake, IDENTIFIER)


def test_stop_db_instance_verified():
    fake = FakeRds(status="available")
    instance = stop_db_instance(fake, IDENTIFIER)
    assert instance["DBInstanceStatus"] == "stopped"


def test_stop_db_instance_status_mismatch_raises():
    fake = BrokenStopFakeRds(status="available")
    with pytest.raises(MutationVerificationError):
        stop_db_instance(fake, IDENTIFIER)
