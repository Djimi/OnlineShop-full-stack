"""Tests for the D1 staging ownership marker (RDS tag) and RDS tag helpers."""

import json
from datetime import timedelta

import pytest
from conftest import client_error
from fakes_staging import DB_INSTANCE, MARKER_TAG_KEY, FakeRds, marker_tag_value

from delivery import staging_marker
from delivery.aws.rds import (
    add_tags_to_resource,
    db_instance_arn,
    list_tags_for_resource,
    remove_tags_from_resource,
)
from delivery.errors import (
    MutationVerificationError,
    ReadError,
    StagingMarkerConflict,
    ValidationError,
)
from delivery.models import StagingOperationRecord

ARN = f"arn:aws:rds:eu-north-1:799111666795:db:{DB_INSTANCE}"


def test_list_tags_for_resource_maps_keys():
    fake = FakeRds(tags={"Environment": "staging"})
    tags = list_tags_for_resource(fake, ARN)
    assert tags == {"Environment": "staging"}


def test_list_tags_for_resource_read_error_is_error():
    fake = FakeRds(error=client_error("AccessDenied"))
    with pytest.raises(ReadError):
        list_tags_for_resource(fake, ARN)


def test_add_tags_to_resource_reads_back():
    fake = FakeRds()
    observed = add_tags_to_resource(fake, ARN, {MARKER_TAG_KEY: "value-1"})
    assert observed[MARKER_TAG_KEY] == "value-1"
    assert fake.tags[MARKER_TAG_KEY] == "value-1"


def test_add_tags_to_resource_fails_when_readback_missing():
    class BrokenAddFakeRds(FakeRds):
        def add_tags_to_resource(self, ResourceName, Tags):
            pass  # mutation silently dropped

    with pytest.raises(MutationVerificationError):
        add_tags_to_resource(BrokenAddFakeRds(), ARN, {MARKER_TAG_KEY: "x"})


def test_remove_tags_to_resource_reads_back_absence():
    fake = FakeRds(tags={MARKER_TAG_KEY: "value-1"})
    observed = remove_tags_from_resource(fake, ARN, [MARKER_TAG_KEY])
    assert MARKER_TAG_KEY not in observed


def test_db_instance_arn_missing_fails():
    with pytest.raises(ReadError):
        db_instance_arn({"DBInstanceIdentifier": "x"})


def _marker_json(**overrides):
    payload = json.loads(marker_tag_value())
    payload.update(overrides)
    return json.dumps(payload)


def test_read_marker_absent_when_tag_missing():
    assert staging_marker.read_marker(FakeRds(), ARN) is None


def test_read_marker_parses_valid_value():
    fake = FakeRds(tags={MARKER_TAG_KEY: _marker_json()})
    marker = staging_marker.read_marker(fake, ARN)
    assert marker.operationId == "stg-4712-2"
    assert marker.workflowRunId == 4712


def test_read_marker_malformed_value_is_error_not_absence():
    fake = FakeRds(tags={MARKER_TAG_KEY: "not json"})
    with pytest.raises(ValidationError):
        staging_marker.read_marker(fake, ARN)


def test_read_marker_wrong_shape_is_error_not_absence():
    fake = FakeRds(tags={MARKER_TAG_KEY: _marker_json(expiresAt="tomorrow")})
    with pytest.raises(ValidationError):
        staging_marker.read_marker(fake, ARN)


def test_acquire_marker_when_absent():
    fake = FakeRds()
    marker = staging_marker.build_marker("stg-1-1", 1, 1, "tester")
    staging_marker.acquire_marker(fake, ARN, marker)
    observed = staging_marker.read_marker(fake, ARN)
    assert observed == marker


def test_acquire_marker_conflicts_with_active_marker():
    fake = FakeRds(tags={MARKER_TAG_KEY: _marker_json()})
    marker = staging_marker.build_marker("stg-9-9", 9, 9, "intruder")
    with pytest.raises(StagingMarkerConflict):
        staging_marker.acquire_marker(fake, ARN, marker)
    # never stolen
    assert json.loads(fake.tags[MARKER_TAG_KEY])["operationId"] == "stg-4712-2"


def test_acquire_marker_overwrites_expired_marker():
    expired = _marker_json(expiresAt="2020-01-01T00:00:00Z")
    fake = FakeRds(tags={MARKER_TAG_KEY: expired})
    marker = staging_marker.build_marker("stg-9-9", 9, 9, "tester")
    staging_marker.acquire_marker(fake, ARN, marker)
    assert json.loads(fake.tags[MARKER_TAG_KEY])["operationId"] == "stg-9-9"


def test_marker_is_active_respects_expiry():
    marker = staging_marker.build_marker("stg-1-1", 1, 1, "tester")
    assert staging_marker.marker_is_active(marker)
    expired = marker.model_copy(
        update={"expiresAt": marker.expiresAt - timedelta(hours=4)}
    )
    assert not staging_marker.marker_is_active(expired)


def test_release_marker_removes_and_verifies():
    fake = FakeRds(tags={MARKER_TAG_KEY: _marker_json()})
    staging_marker.release_marker(fake, ARN)
    assert MARKER_TAG_KEY not in fake.tags


def test_assert_marker_owned_by_accepts_matching_marker():
    record = _record()
    marker = staging_marker.build_marker("stg-4712-2", 4712, 2, "tester")
    assert staging_marker.assert_marker_owned_by(marker, record) == marker


def test_assert_marker_owned_by_rejects_missing_marker():
    with pytest.raises(StagingMarkerConflict):
        staging_marker.assert_marker_owned_by(None, _record())


def test_assert_marker_owned_by_rejects_expired_marker():
    marker = staging_marker.build_marker("stg-4712-2", 4712, 2, "tester")
    expired = marker.model_copy(update={"expiresAt": marker.expiresAt - timedelta(hours=4)})
    with pytest.raises(StagingMarkerConflict):
        staging_marker.assert_marker_owned_by(expired, _record())


def test_assert_marker_owned_by_rejects_foreign_operation():
    record = _record()
    marker = staging_marker.build_marker("stg-9999-9", 9999, 9, "other")
    with pytest.raises(StagingMarkerConflict):
        staging_marker.assert_marker_owned_by(marker, record)


def test_marker_value_is_canonical_and_contains_no_secrets():
    marker = staging_marker.build_marker("stg-1-1", 1, 1, "tester")
    value = staging_marker.marker_value(marker)
    assert value == json.dumps(marker.model_dump(mode="json"), separators=(",", ":"))


def _record() -> StagingOperationRecord:
    return StagingOperationRecord.model_validate_json(
        json.dumps(
            {
                "schemaVersion": "1.0",
                "operationId": "stg-4712-2",
                "candidate": {
                    "candidateId": "cand-4712-2-222222222222",
                    "branch": "feature/x",
                    "fullSha": "2222222222222222222222222222222222222222",
                    "workflowRunId": 4712,
                    "workflowRunAttempt": 2,
                },
                "owner": "tester",
                "acquiredAt": "2026-08-15T10:00:00Z",
                "phase": "E2E",
                "database": {
                    "resetConclusion": "passed",
                    "seedConclusion": "passed",
                    "accessVerificationConclusion": "passed",
                },
                "artifactsExpected": {
                    "authDigest": f"sha256:{'a' * 64}",
                    "itemsDigest": f"sha256:{'b' * 64}",
                    "gatewayDigest": f"sha256:{'c' * 64}",
                    "frontendChecksum": f"{'d' * 64}",
                },
                "artifactsObserved": {},
                "compatibility": {"conclusion": "not-run", "bootstrapException": False},
                "e2e": {"conclusion": "pending"},
                "cleanup": {"conclusion": "not-run"},
            }
        )
    )
