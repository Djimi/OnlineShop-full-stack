"""Tests for AWS context, identity preflight, and session caching."""

import pytest
from conftest import client_error

from delivery.aws.context import (
    AwsContext,
    client_for,
    identity_preflight,
    require_environment,
    session_for,
)
from delivery.errors import ReadError, ValidationError

ACCOUNT = "799111666795"


def _ctx(**overrides):
    values = {
        "region": "eu-north-1",
        "account_id": ACCOUNT,
        "environment": "production",
    }
    values.update(overrides)
    return AwsContext(**values)


class FakeSts:
    def __init__(self, account=ACCOUNT, error=None):
        self.account = account
        self.error = error

    def get_caller_identity(self):
        if self.error:
            raise self.error
        return {
            "Account": self.account,
            "Arn": f"arn:aws:iam::{self.account}:user/tester",
            "UserId": "AIDAEXAMPLE",
        }


def test_identity_preflight_ok(monkeypatch):
    monkeypatch.setattr("delivery.aws.context.client_for", lambda ctx, service: FakeSts())
    assert identity_preflight(_ctx()) == ACCOUNT


def test_identity_preflight_failure_is_read_error(monkeypatch):
    sts = FakeSts(error=client_error("ThrottlingException"))
    monkeypatch.setattr("delivery.aws.context.client_for", lambda ctx, service: sts)
    with pytest.raises(ReadError):
        identity_preflight(_ctx())


def test_identity_preflight_account_mismatch_is_validation_error(monkeypatch):
    sts = FakeSts(account="000000000000")
    monkeypatch.setattr("delivery.aws.context.client_for", lambda ctx, service: sts)
    with pytest.raises(ValidationError):
        identity_preflight(_ctx())


def test_session_for_creates_session_with_context_region():
    session = session_for(_ctx())
    assert session.region_name == "eu-north-1"
    assert session.profile_name in (None, "default")


class FakeSession:
    def __init__(self):
        self.clients = {}

    def client(self, service):
        if service not in self.clients:
            self.clients[service] = object()
        return self.clients[service]


def test_client_for_caches_per_service(monkeypatch):
    monkeypatch.setattr("delivery.aws.context.session_for", lambda ctx: FakeSession())
    ctx = _ctx()
    assert client_for(ctx, "sts") is client_for(ctx, "sts")
    assert client_for(ctx, "s3") is not client_for(ctx, "sts")


def test_require_environment_allows_expected():
    require_environment(_ctx(environment="production"), ("production", "staging"))
    require_environment(_ctx(environment="staging"), ("production", "staging"))


def test_require_environment_rejects_unexpected():
    with pytest.raises(ValidationError):
        require_environment(_ctx(environment="production"), ("staging",))
