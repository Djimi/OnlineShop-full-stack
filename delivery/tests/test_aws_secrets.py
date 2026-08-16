"""Tests for the secrets reference-only adapter."""

import pytest
from conftest import client_error

import delivery.aws.secrets as secrets_module
from delivery.aws.secrets import secret_reference
from delivery.errors import ReadError

NAME = "onlineshop/db-admin"
FULL_ARN = "arn:aws:secretsmanager:eu-north-1:799111666795:secret:onlineshop/db-admin-abc123"


class FakeSecrets:
    def __init__(self, arns=None):
        self.arns = arns or {NAME: FULL_ARN}
        self.error = None

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def describe_secret(self, SecretId):
        self._maybe_fail()
        if SecretId not in self.arns:
            raise client_error("ResourceNotFoundException")
        return {"ARN": self.arns[SecretId], "Name": SecretId}


def test_secret_reference_returns_full_arn_unchanged():
    fake = FakeSecrets()
    assert secret_reference(fake, FULL_ARN) == FULL_ARN


def test_secret_reference_resolves_name_to_full_arn():
    fake = FakeSecrets()
    assert secret_reference(fake, NAME) == FULL_ARN


def test_secret_reference_failure_is_read_error():
    fake = FakeSecrets()
    fake.error = client_error("AccessDenied")
    with pytest.raises(ReadError):
        secret_reference(fake, NAME)


def test_secrets_adapter_exposes_no_value_api():
    assert not hasattr(secrets_module, "get_secret_value")
