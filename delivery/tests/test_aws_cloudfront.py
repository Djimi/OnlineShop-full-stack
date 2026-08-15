"""Tests for CloudFront distribution and invalidation adapters."""

import pytest
from conftest import client_error

from delivery.aws.cloudfront import create_invalidation, get_distribution
from delivery.errors import MutationVerificationError, ReadError

DISTRIBUTION_ID = "E1EXAMPLE"


class FakeCloudFront:
    def __init__(self):
        self.distribution = {"Distribution": {"Id": DISTRIBUTION_ID, "Status": "Deployed"}}
        self.invalidations = {}
        self.error = None

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def get_distribution(self, Id):
        self._maybe_fail()
        return self.distribution

    def create_invalidation(self, DistributionId, InvalidationBatch):
        self._maybe_fail()
        invalidation_id = f"inv-{len(self.invalidations) + 1}"
        invalidation = {
            "Id": invalidation_id,
            "Status": "InProgress",
            "InvalidationBatch": InvalidationBatch,
        }
        self.invalidations[invalidation_id] = invalidation
        return {"Invalidation": invalidation}

    def get_invalidation(self, DistributionId, Id):
        self._maybe_fail()
        invalidation = self.invalidations.get(Id)
        if invalidation is None:
            raise client_error("NoSuchInvalidation")
        return {"Invalidation": invalidation}


class EmptyIdFakeCloudFront(FakeCloudFront):
    def create_invalidation(self, DistributionId, InvalidationBatch):
        return {"Invalidation": {}}


class ConfusedFakeCloudFront(FakeCloudFront):
    def get_invalidation(self, DistributionId, Id):
        return {"Invalidation": {"Id": "inv-999", "Status": "Completed"}}


def test_get_distribution_ok():
    fake = FakeCloudFront()
    assert get_distribution(fake, DISTRIBUTION_ID)["Distribution"]["Status"] == "Deployed"


def test_get_distribution_failure_is_read_error():
    fake = FakeCloudFront()
    fake.error = client_error("AccessDenied")
    with pytest.raises(ReadError):
        get_distribution(fake, DISTRIBUTION_ID)


def test_create_invalidation_ok_with_read_back():
    fake = FakeCloudFront()
    invalidation = create_invalidation(fake, DISTRIBUTION_ID, ["/index.html", "/_releases/*"])
    assert invalidation["Id"].startswith("inv-")
    assert fake.invalidations[invalidation["Id"]]["Status"] == "InProgress"


def test_create_invalidation_no_id_is_read_error():
    fake = EmptyIdFakeCloudFront()
    with pytest.raises(ReadError):
        create_invalidation(fake, DISTRIBUTION_ID, ["/index.html"])


def test_create_invalidation_read_back_mismatch_raises():
    fake = ConfusedFakeCloudFront()
    with pytest.raises(MutationVerificationError):
        create_invalidation(fake, DISTRIBUTION_ID, ["/index.html"])
