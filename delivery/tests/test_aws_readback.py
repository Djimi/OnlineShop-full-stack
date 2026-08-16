"""Tests for mutation read-back verification and absence classification."""

import pytest
from conftest import client_error

from delivery.aws.readback import absent_or_read, mutate_and_read_back
from delivery.errors import MutationVerificationError

ABSENT_CODES = [
    "404",
    "NoSuchKey",
    "NoSuchEntity",
    "NotFoundException",
    "ResourceNotFoundException",
]


@pytest.mark.parametrize("code", ABSENT_CODES)
def test_absent_or_read_true_for_genuine_absence_codes(code):
    assert absent_or_read(client_error(code)) is True


def test_absent_or_read_false_for_other_client_errors():
    assert absent_or_read(client_error("AccessDenied")) is False
    assert absent_or_read(client_error("ThrottlingException")) is False


def test_absent_or_read_false_for_non_client_errors():
    assert absent_or_read(ValueError("boom")) is False
    assert absent_or_read(RuntimeError("boom")) is False


def test_mutate_and_read_back_ok():
    calls = []

    def mutate():
        calls.append("mutate")

    def read():
        calls.append("read")
        return {"status": "ok"}

    result = mutate_and_read_back(mutate, read, label="resource")
    assert result == {"status": "ok"}
    assert calls == ["mutate", "read"]


def test_mutate_and_read_back_check_failure_raises():
    with pytest.raises(MutationVerificationError):
        mutate_and_read_back(
            lambda: None,
            lambda: {"status": "bad"},
            label="resource",
            check=lambda result: result["status"] == "ok",
        )


def test_mutate_and_read_back_expected_mismatch_raises():
    with pytest.raises(MutationVerificationError):
        mutate_and_read_back(
            lambda: None, lambda: "observed", label="resource", expected="expected"
        )


def test_mutate_and_read_back_read_error_is_wrapped():
    with pytest.raises(MutationVerificationError) as excinfo:
        mutate_and_read_back(
            lambda: None,
            lambda: (_ for _ in ()).throw(client_error("AccessDenied")),
            label="resource",
        )
    assert isinstance(excinfo.value.__cause__, Exception)


def test_mutate_and_read_back_mutation_error_propagates_unchanged():
    original = RuntimeError("mutation failed")

    def mutate():
        raise original

    with pytest.raises(RuntimeError) as excinfo:
        mutate_and_read_back(mutate, lambda: None, label="resource")
    assert excinfo.value is original
