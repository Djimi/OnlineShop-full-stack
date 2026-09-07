"""Tests for the delivery exception hierarchy."""

import re

import pytest

from delivery.errors import (
    AbsentResourceError,
    DeliveryError,
    ReadError,
)

_ALL_ERROR_CLASSES = [DeliveryError]


def _collect_subclasses(cls: type) -> None:
    for sub in cls.__subclasses__():
        _ALL_ERROR_CLASSES.append(sub)
        _collect_subclasses(sub)


_collect_subclasses(DeliveryError)

_CODE_FORMAT = re.compile(r"[A-Z][A-Z0-9_]*")


@pytest.mark.parametrize("cls", _ALL_ERROR_CLASSES)
def test_every_error_class_is_a_delivery_error(cls):
    assert issubclass(cls, DeliveryError)


@pytest.mark.parametrize("cls", _ALL_ERROR_CLASSES)
def test_every_error_class_exposes_a_non_empty_string_code(cls):
    assert isinstance(cls.code, str) and cls.code


@pytest.mark.parametrize("cls", _ALL_ERROR_CLASSES)
def test_codes_follow_uppercase_snake_format(cls):
    assert _CODE_FORMAT.fullmatch(cls.code)


def test_codes_are_unique_across_error_classes():
    codes = [cls.code for cls in _ALL_ERROR_CLASSES]
    assert len(codes) == len(set(codes))


def test_absence_is_a_finding_not_a_read_failure():
    assert issubclass(AbsentResourceError, ReadError)
    assert AbsentResourceError.code != ReadError.code


def test_read_error_is_not_absence():
    read_error = ReadError("network failure")
    assert not isinstance(read_error, AbsentResourceError)


@pytest.mark.parametrize(
    "error",
    [
        ReadError("boom"),
        AbsentResourceError("gone"),
    ],
)
def test_instances_expose_their_class_code(error):
    assert error.code == type(error).code
