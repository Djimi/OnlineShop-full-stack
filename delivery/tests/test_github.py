"""Tests for exact workflow run/attempt authority helpers."""

import pytest

from delivery.errors import ValidationError
from delivery.github import (
    assert_run_attempt_shape,
    candidate_id,
    parse_attempt_jobs,
    parse_workflow_run,
)

FULL_SHA = "1111111111111111111111111111111111111111"


def _run_payload(**overrides):
    payload = {
        "id": 4711,
        "run_attempt": 1,
        "head_sha": FULL_SHA,
        "head_branch": "main",
        "run_number": 42,
        "html_url": "https://github.com/acme/shop/actions/runs/4711",
        "untrusted_junk": {"exec": "rm -rf /"},
    }
    payload.update(overrides)
    return payload


def _job_payload(**overrides):
    payload = {
        "id": 1001,
        "run_id": 4711,
        "run_attempt": 1,
        "name": "build",
        "status": "completed",
        "conclusion": "success",
        "html_url": "https://github.com/acme/shop/actions/runs/4711/attempts/1/jobs/1001",
        "untrusted_junk": "never echoed",
    }
    payload.update(overrides)
    return payload


def _jobs_payload(jobs=None):
    if jobs is None:
        jobs = [_job_payload()]
    return {"total_count": len(jobs), "jobs": jobs}


@pytest.mark.parametrize("value", ["7", 7.0, 0, -3, True, None])
def test_assert_run_attempt_shape_rejects_non_positive_integers(value):
    with pytest.raises(ValidationError):
        assert_run_attempt_shape(value, 1)
    with pytest.raises(ValidationError):
        assert_run_attempt_shape(1, value)


def test_assert_run_attempt_shape_accepts_positive_integers():
    assert assert_run_attempt_shape(4711, 2) == (4711, 2)


def test_candidate_id_format_is_readable_and_collision_resistant():
    assert candidate_id(4711, 1, FULL_SHA) == "cand-4711-1-111111111111"
    assert candidate_id(1, 1, "abc") == "cand-1-1-abc"


def test_parse_workflow_run_accepts_exact_shape():
    cleaned = parse_workflow_run(_run_payload(), run_id=4711, run_attempt=1)
    assert cleaned == {
        "id": 4711,
        "run_attempt": 1,
        "head_sha": FULL_SHA,
        "head_branch": "main",
        "run_number": 42,
        "html_url": "https://github.com/acme/shop/actions/runs/4711",
    }


def test_parse_workflow_run_never_echoes_untrusted_keys():
    cleaned = parse_workflow_run(_run_payload(), run_id=4711, run_attempt=1)
    assert "untrusted_junk" not in cleaned


@pytest.mark.parametrize(
    "overrides", [{"id": 4712}, {"run_attempt": 2}, {"id": 4711, "run_attempt": 2}]
)
def test_parse_workflow_run_rejects_mismatched_identity(overrides):
    with pytest.raises(ValidationError):
        parse_workflow_run(_run_payload(**overrides), run_id=4711, run_attempt=1)


def test_parse_workflow_run_rejects_float_id():
    with pytest.raises(ValidationError):
        parse_workflow_run(_run_payload(id=4711.0), run_id=4711, run_attempt=1)


def test_parse_workflow_run_rejects_missing_head_branch():
    payload = _run_payload()
    del payload["head_branch"]
    with pytest.raises(ValidationError):
        parse_workflow_run(payload, run_id=4711, run_attempt=1)


def test_parse_workflow_run_rejects_empty_head_branch():
    with pytest.raises(ValidationError):
        parse_workflow_run(_run_payload(head_branch=""), run_id=4711, run_attempt=1)


@pytest.mark.parametrize(
    "branch",
    [
        "main'",
        'main"',
        "main`",
        "main$()",
        "main; rm -rf /",
        "feature\nrefs/heads/x",
        "feature\trefs",
        "a b",
        "main\\x",
    ],
)
def test_parse_workflow_run_rejects_injected_head_branch(branch):
    with pytest.raises(ValidationError):
        parse_workflow_run(_run_payload(head_branch=branch), run_id=4711, run_attempt=1)


def test_parse_workflow_run_accepts_safe_branch_names():
    for branch in ("main", "feature/checkout-flow", "release-1.2", "hotfix_x.y"):
        cleaned = parse_workflow_run(_run_payload(head_branch=branch), run_id=4711, run_attempt=1)
        assert cleaned["head_branch"] == branch


def test_parse_workflow_run_rejects_string_run_attempt():
    with pytest.raises(ValidationError):
        parse_workflow_run(_run_payload(run_attempt="1"), run_id=4711, run_attempt=1)


def test_parse_attempt_jobs_accepts_exact_attempt():
    cleaned = parse_attempt_jobs(_jobs_payload(), 4711, 1)
    assert cleaned == [
        {
            "id": 1001,
            "name": "build",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/acme/shop/actions/runs/4711/attempts/1/jobs/1001",
        }
    ]


def test_parse_attempt_jobs_never_echoes_untrusted_keys():
    cleaned = parse_attempt_jobs(_jobs_payload(), 4711, 1)
    assert "untrusted_junk" not in cleaned[0]


def test_parse_attempt_jobs_rejects_run_id_mismatch():
    jobs = [_job_payload(run_id=4712)]
    with pytest.raises(ValidationError):
        parse_attempt_jobs(_jobs_payload(jobs), 4711, 1)


def test_parse_attempt_jobs_rejects_attempt_mismatch():
    jobs = [_job_payload(run_attempt=2)]
    with pytest.raises(ValidationError):
        parse_attempt_jobs(_jobs_payload(jobs), 4711, 1)


def test_parse_attempt_jobs_rejects_unscoped_latest_response():
    jobs = [_job_payload()]
    del jobs[0]["run_attempt"]
    with pytest.raises(ValidationError):
        parse_attempt_jobs(_jobs_payload(jobs), 4711, 1)


def test_parse_attempt_jobs_rejects_missing_jobs_list():
    with pytest.raises(ValidationError):
        parse_attempt_jobs({"total_count": 0}, 4711, 1)
