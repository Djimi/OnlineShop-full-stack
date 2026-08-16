"""Tests for the D1 staging ownership marker (RDS tag) and RDS tag helpers."""

import json
import re
from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError
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


def test_read_marker_absent_when_tag_missing():
    assert staging_marker.read_marker(FakeRds(), ARN) is None


def test_read_marker_parses_valid_value():
    fake = FakeRds(tags={MARKER_TAG_KEY: marker_tag_value()})
    marker = staging_marker.read_marker(fake, ARN)
    assert marker.operationId == "stg-4712-2"
    assert marker.workflowRunId == 4712


def test_read_marker_malformed_value_is_error_not_absence():
    fake = FakeRds(tags={MARKER_TAG_KEY: "not-a-marker"})
    with pytest.raises(ValidationError):
        staging_marker.read_marker(fake, ARN)


def test_read_marker_wrong_shape_is_error_not_absence():
    fake = FakeRds(tags={MARKER_TAG_KEY: "v1:stg-4712-2:4712:2:tester:1:tomorrow"})
    with pytest.raises(ValidationError):
        staging_marker.read_marker(fake, ARN)


@pytest.mark.parametrize(
    "value",
    [
        "v1:stg-1-1:1:1:tester:1",
        "v1:stg-1-1:1:1:tester:1:2:extra",
        "v1:stg-1-1:0:1:tester:1:2",
        "v1:stg-1-1:1:+1:tester:1:2",
        "v1:stg-1-1:1:1:tester:0:2",
        f"v1:stg-1-1:1:1:tester:{'9' * 100}:2",
        "v1:not-an-operation:1:1:tester:1:2",
    ],
)
def test_parse_marker_rejects_malformed_values(value):
    with pytest.raises(ValidationError):
        staging_marker.parse_marker(value)


def test_parse_marker_rejects_unsupported_version():
    with pytest.raises(ValidationError, match="unsupported marker version"):
        staging_marker.parse_marker("v2:stg-1-1:1:1:tester:1:2")


def test_parse_marker_rejects_too_long_value():
    value = f"v1:stg-{'1' * 240}-1:1:1:tester:1:2"
    with pytest.raises(ValidationError, match="256"):
        staging_marker.parse_marker(value)


def test_acquire_marker_when_absent():
    fake = FakeRds()
    marker = staging_marker.build_marker("stg-1-1", 1, 1, "tester")
    staging_marker.acquire_marker(fake, ARN, marker)
    observed = staging_marker.read_marker(fake, ARN)
    assert observed == marker


def test_acquire_marker_conflicts_with_active_marker():
    fake = FakeRds(tags={MARKER_TAG_KEY: marker_tag_value()})
    marker = staging_marker.build_marker("stg-9-9", 9, 9, "intruder")
    with pytest.raises(StagingMarkerConflict):
        staging_marker.acquire_marker(fake, ARN, marker)
    # never stolen
    assert staging_marker.parse_marker(fake.tags[MARKER_TAG_KEY]).operationId == "stg-4712-2"


def test_acquire_marker_overwrites_expired_marker():
    expired = marker_tag_value(expires_in=timedelta(hours=-1))
    fake = FakeRds(tags={MARKER_TAG_KEY: expired})
    marker = staging_marker.build_marker("stg-9-9", 9, 9, "tester")
    staging_marker.acquire_marker(fake, ARN, marker)
    assert staging_marker.parse_marker(fake.tags[MARKER_TAG_KEY]).operationId == "stg-9-9"


def test_marker_is_active_respects_expiry():
    marker = staging_marker.build_marker("stg-1-1", 1, 1, "tester")
    assert staging_marker.marker_is_active(marker)
    expired = marker.model_copy(
        update={"expiresAt": marker.expiresAt - timedelta(hours=4)}
    )
    assert not staging_marker.marker_is_active(expired)


def test_release_marker_removes_and_verifies():
    fake = FakeRds(tags={MARKER_TAG_KEY: marker_tag_value()})
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


def test_marker_value_is_aws_safe_and_round_trips_exactly():
    marker = staging_marker.OwnershipMarker(
        operationId="stg-4712-2",
        workflowRunId=4712,
        workflowRunAttempt=2,
        owner="test.user@example",
        acquiredAt=datetime(2026, 8, 16, 10, 11, 12, tzinfo=UTC),
        expiresAt=datetime(2026, 8, 16, 13, 11, 12, tzinfo=UTC),
    )
    value = staging_marker.marker_value(marker)
    assert value == "v1:stg-4712-2:4712:2:test.user@example:1786875072:1786885872"
    assert len(value) <= 256
    assert re.fullmatch(r"[A-Za-z0-9_.:/=+@-]+", value)
    assert staging_marker.parse_marker(value) == marker


def test_marker_value_rejects_too_long_value():
    marker = staging_marker.build_marker(f"stg-{'1' * 240}-1", 1, 1, "tester")
    with pytest.raises(ValidationError, match="256"):
        staging_marker.marker_value(marker)


def test_marker_value_rejects_unsafe_operation_id():
    marker = staging_marker.build_marker("stg-1-1", 1, 1, "tester").model_copy(
        update={"operationId": "stg-{1}-1"}
    )
    with pytest.raises(ValidationError):
        staging_marker.marker_value(marker)


def test_fake_rds_rejects_raw_json_tag_value_like_aws():
    fake = FakeRds()
    with pytest.raises(ClientError) as error:
        fake.add_tags_to_resource(ARN, [{"Key": MARKER_TAG_KEY, "Value": '{"owner":"x"}'}])
    assert error.value.response["Error"]["Code"] == "InvalidParameterValue"


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
