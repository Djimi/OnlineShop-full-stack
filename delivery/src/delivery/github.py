"""Exact GitHub workflow run/attempt authority helpers."""

from __future__ import annotations

import re

from .errors import ValidationError

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_BRANCH_NAME = re.compile(r"^[A-Za-z0-9._/-]+$")


def _positive_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(
            f"{label} must be a positive JSON integer, got {type(value).__name__}"
        )
    if value <= 0:
        raise ValidationError(f"{label} must be positive, got {value}")
    return value


def assert_run_attempt_shape(run_id, run_attempt) -> tuple[int, int]:
    return _positive_int(run_id, "run id"), _positive_int(run_attempt, "run attempt")


def candidate_id(run_id: int, run_attempt: int, short_sha: str) -> str:
    return f"cand-{run_id}-{run_attempt}-{short_sha[:12]}"


def parse_workflow_run(
    run_json: dict, run_id: int | None = None, run_attempt: int | None = None
) -> dict:
    if not isinstance(run_json, dict):
        raise ValidationError("workflow run response must be a JSON object")
    observed_id = _positive_int(run_json.get("id"), "run id")
    observed_attempt = _positive_int(run_json.get("run_attempt"), "run attempt")
    if run_id is not None and observed_id != run_id:
        raise ValidationError(f"run id mismatch: requested {run_id}, observed {observed_id}")
    if run_attempt is not None and observed_attempt != run_attempt:
        raise ValidationError(
            f"run attempt mismatch: requested {run_attempt}, observed {observed_attempt}"
        )
    head_branch = run_json.get("head_branch")
    if not isinstance(head_branch, str) or not _BRANCH_NAME.fullmatch(head_branch):
        raise ValidationError("head_branch must be a branch name of [A-Za-z0-9._/-]")
    head_sha = run_json.get("head_sha")
    if not isinstance(head_sha, str) or not _FULL_SHA.fullmatch(head_sha):
        raise ValidationError("head_sha must be a 40-character lowercase hex string")
    run_number = _positive_int(run_json.get("run_number"), "run number")
    html_url = run_json.get("html_url")
    if not isinstance(html_url, str) or not html_url.startswith("https://"):
        raise ValidationError("html_url must be an https URL")
    return {
        "id": observed_id,
        "run_attempt": observed_attempt,
        "head_sha": head_sha,
        "head_branch": head_branch,
        "run_number": run_number,
        "html_url": html_url,
    }


def parse_attempt_jobs(jobs_json: dict, run_id: int, run_attempt: int) -> list[dict]:
    if not isinstance(jobs_json, dict):
        raise ValidationError("jobs response must be a JSON object")
    run_id, run_attempt = assert_run_attempt_shape(run_id, run_attempt)
    jobs = jobs_json.get("jobs")
    if not isinstance(jobs, list):
        raise ValidationError("jobs response must contain a jobs list")
    cleaned = []
    for job in jobs:
        if not isinstance(job, dict):
            raise ValidationError("each job must be a JSON object")
        job_id = _positive_int(job.get("id"), "job id")
        observed_run_id = _positive_int(job.get("run_id"), "job run id")
        observed_attempt = _positive_int(job.get("run_attempt"), "job run attempt")
        if observed_run_id != run_id or observed_attempt != run_attempt:
            raise ValidationError(
                f"job {job_id} belongs to run {observed_run_id} attempt {observed_attempt}, "
                f"not the requested run {run_id} attempt {run_attempt}"
            )
        name = job.get("name")
        status = job.get("status")
        conclusion = job.get("conclusion")
        for label, value in (("name", name), ("status", status), ("conclusion", conclusion)):
            if not isinstance(value, str) or not value:
                raise ValidationError(f"job {label} must be a non-empty string")
        html_url = job.get("html_url")
        if not isinstance(html_url, str) or not html_url.startswith("https://"):
            raise ValidationError("job html_url must be an https URL")
        cleaned.append(
            {
                "id": job_id,
                "name": name,
                "status": status,
                "conclusion": conclusion,
                "html_url": html_url,
            }
        )
    return cleaned
