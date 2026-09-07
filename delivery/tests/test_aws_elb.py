"""Tests for the read-only ELB helpers."""

import pytest
from conftest import client_error

from delivery.aws.elb import (
    describe_load_balancer,
    describe_target_health,
    load_balancer_dns_name,
)
from delivery.errors import AbsentResourceError, ReadError


class FakeElb:
    def __init__(self, balancers=None, error=None):
        self.balancers = balancers
        self.error = error

    def describe_load_balancers(self, Names=None):
        if self.error is not None:
            raise self.error
        names = Names or []
        found = [
            b for b in (self.balancers or []) if b.get("LoadBalancerName") in names
        ]
        if not found:
            raise client_error("LoadBalancerNotFound")
        return {"LoadBalancers": found}

    def describe_target_health(self, TargetGroupArn):
        return {
            "TargetHealthDescriptions": [
                {"Target": {"Id": "x"}, "TargetHealth": {"State": "healthy"}}
            ]
        }


def test_describe_load_balancer_ok():
    fake = FakeElb(
        balancers=[{"LoadBalancerName": "staging-alb", "DNSName": "x.elb.amazonaws.com"}]
    )
    assert describe_load_balancer(fake, "staging-alb")["DNSName"] == "x.elb.amazonaws.com"


def test_describe_load_balancer_absent_is_absent():
    with pytest.raises(AbsentResourceError):
        describe_load_balancer(FakeElb(), "staging-alb")


def test_describe_load_balancer_read_error_is_error():
    with pytest.raises(ReadError):
        describe_load_balancer(FakeElb(error=client_error("AccessDenied")), "staging-alb")


def test_load_balancer_dns_name():
    fake = FakeElb(
        balancers=[{"LoadBalancerName": "staging-alb", "DNSName": "y.elb.amazonaws.com"}]
    )
    assert load_balancer_dns_name(fake, "staging-alb") == "y.elb.amazonaws.com"


def test_load_balancer_dns_name_missing_fails():
    fake = FakeElb(balancers=[{"LoadBalancerName": "staging-alb"}])
    with pytest.raises(ReadError):
        load_balancer_dns_name(fake, "staging-alb")


def test_describe_target_health():
    assert describe_target_health(FakeElb(), "arn:tg")[0]["TargetHealth"]["State"] == "healthy"
