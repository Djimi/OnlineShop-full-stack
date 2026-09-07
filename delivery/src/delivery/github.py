"""Exact GitHub workflow run/attempt authority helpers and REST client."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from .errors import ReadError, ValidationError


class _StripAuthRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects but never forward Authorization to a different host.

    GitHub's artifact zip endpoint redirects to a signed Azure Blob/S3
    object; forwarding the Bearer token there fails authentication (the
    signed request does not include it) and leaks the token off-host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        old_host = urllib.parse.urlsplit(req.full_url).hostname
        new_host = urllib.parse.urlsplit(newurl).hostname
        if new_host != old_host:
            new_req.remove_header("Authorization")
        return new_req


_ARTIFACT_OPENER = urllib.request.build_opener(_StripAuthRedirectHandler())

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_BRANCH_NAME = re.compile(r"^[A-Za-z0-9._/-]+$")
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9._-]+$")
_API_BASE = "https://api.github.com"


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


class GitHubApi:
    """Minimal urllib-based GitHub REST client (no extra dependencies).

    The token comes from the ``GITHUB_TOKEN`` environment variable only and
    is never logged, printed, or embedded in error messages. Every API
    failure is a ``ReadError``; absence and failure are never conflated.
    """

    def __init__(self, repository: str, token: str | None = None):
        if not isinstance(repository, str) or "/" not in repository:
            raise ValidationError(f"repository must be owner/name, got {repository!r}")
        self.repository = repository
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN")

    def _request(
        self, path: str, method: str = "GET", data: bytes | None = None, base: str = _API_BASE
    ) -> dict | list:
        if not self.token:
            raise ReadError("GITHUB_TOKEN is not set; GitHub API reads are unavailable")
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "onlineshop-delivery",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(f"{base}{path}", data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            raise ReadError(f"GitHub API {path} failed with HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ReadError(f"GitHub API {path} unreachable: {error.reason}") from error
        try:
            return json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReadError(f"GitHub API {path} returned invalid JSON") from error

    def list_run_artifacts(
        self, run_id: int, run_attempt: int, expected_names: set[str]
    ) -> list[dict]:
        """Select named artifacts owned by the exact run/attempt.

        The artifact list endpoint does not report the run attempt, so the
        exact run is additionally fetched and validated with
        ``parse_workflow_run`` (positive JSON numbers, head fields) before
        the artifacts are accepted. Unrelated entries are ignored before any
        of their other fields are parsed; every selected entry is validated.
        """
        run_id, run_attempt = assert_run_attempt_shape(run_id, run_attempt)
        expected_names = self._validate_expected_artifact_names(expected_names)
        run_response = self._request(f"/repos/{self.repository}/actions/runs/{run_id}")
        if not isinstance(run_response, dict):
            raise ReadError("workflow run response must be a JSON object")
        parse_workflow_run(run_response, run_id=run_id, run_attempt=run_attempt)
        data = self._request(f"/repos/{self.repository}/actions/runs/{run_id}/artifacts")
        if not isinstance(data, dict):
            raise ReadError("artifacts response must be a JSON object")
        raw_artifacts = data.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raise ReadError("artifacts response must contain an artifacts list")
        selected = {}
        for artifact in raw_artifacts:
            if not isinstance(artifact, dict):
                continue
            name = artifact.get("name")
            if not isinstance(name, str) or name not in expected_names:
                continue
            artifact_id = _positive_int(artifact.get("id"), "artifact id")
            if not _ARTIFACT_NAME.fullmatch(name):
                raise ReadError(f"artifact {artifact_id} has an unsafe name")
            expired = artifact.get("expired")
            if not isinstance(expired, bool):
                raise ReadError(f"artifact {artifact_id} has no boolean expired state")
            if expired:
                raise ReadError(f"artifact {artifact_id} is expired")
            run = artifact.get("workflow_run")
            if not isinstance(run, dict):
                raise ReadError(f"artifact {artifact_id} has no workflow_run object")
            observed_run_id = _positive_int(run.get("id"), "artifact run id")
            if observed_run_id != run_id:
                raise ValidationError(
                    f"artifact {artifact_id} belongs to run {observed_run_id}, "
                    f"not the requested run {run_id}"
                )
            if name in selected:
                raise ValidationError(
                    f"duplicate artifact {name!r} for run {run_id} attempt {run_attempt}"
                )
            selected[name] = {"id": artifact_id, "name": name}
        self._require_expected_artifacts(selected, expected_names, run_id, run_attempt)
        return [selected[name] for name in sorted(expected_names)]

    def list_releases(self) -> list[dict]:
        """List published GitHub Releases (newest first), fail-closed on shape.

        Only published (non-draft, non-prerelease) releases are returned:
        drafts and prereleases are never promotion sources, so a draft's
        manifest must never become the "previous official frontend".
        """
        data = self._request(f"/repos/{self.repository}/releases")
        if not isinstance(data, list):
            raise ReadError("releases response must be a JSON list")
        cleaned = []
        for release in data:
            if not isinstance(release, dict):
                raise ReadError("each release must be a JSON object")
            tag = release.get("tag_name")
            release_id = release.get("id")
            assets_raw = release.get("assets")
            if not isinstance(tag, str) or not tag:
                raise ReadError("release is missing tag_name")
            if not isinstance(release_id, int) or release_id <= 0:
                raise ReadError(f"release {tag} has an invalid id")
            if not isinstance(assets_raw, list):
                raise ReadError(f"release {tag} has no assets list")
            draft = release.get("draft")
            prerelease = release.get("prerelease")
            for label, value in (("draft", draft), ("prerelease", prerelease)):
                if not isinstance(value, bool):
                    raise ReadError(f"release {tag} has a non-boolean {label} flag")
            if draft or prerelease:
                continue
            assets = []
            for asset in assets_raw:
                if not isinstance(asset, dict):
                    raise ReadError(f"release {tag} has a malformed asset")
                name = asset.get("name")
                url = asset.get("browser_download_url")
                if not isinstance(name, str) or not name:
                    raise ReadError(f"release {tag} has an asset without a name")
                if not isinstance(url, str) or not url.startswith("https://"):
                    raise ReadError(f"release {tag} asset {name} has no https URL")
                assets.append({"name": name, "url": url})
            cleaned.append({"tag_name": tag, "id": release_id, "assets": assets})
        return cleaned

    def download_asset(self, url: str) -> bytes:
        """Download a release asset by URL, fail-closed on any error."""
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ValidationError(f"asset URL must be https, got {url!r}")
        if not self.token:
            raise ReadError("GITHUB_TOKEN is not set; asset download is unavailable")
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/octet-stream",
                "User-Agent": "onlineshop-delivery",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise ReadError(f"asset download failed with HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ReadError(f"asset download unreachable: {error.reason}") from error

    def get_run(self, run_id: int) -> dict:
        """Fetch the authoritative run object (current run_attempt included).

        Unlike ``list_run_artifacts`` this does not require the current
        attempt to equal any specific attempt: a run may have been re-run
        after the candidate's producing attempt. Artifact consumers bind the
        authoritative attempt to exact deterministic names because artifact
        entries do not include a run-attempt field.
        """
        run_id = _positive_int(run_id, "run id")
        response = self._request(f"/repos/{self.repository}/actions/runs/{run_id}")
        if not isinstance(response, dict):
            raise ReadError("workflow run response must be a JSON object")
        return parse_workflow_run(response, run_id=run_id)

    def list_artifacts_for_run(
        self, run_id: int, run_attempt: int, expected_names: set[str]
    ) -> list[dict]:
        """Select downloadable named artifacts from an exact run/attempt.

        The artifact list endpoint does not report the run attempt, so the
        exact run is additionally fetched and validated with
        ``parse_workflow_run`` before the artifacts are accepted.
        """
        run_id, run_attempt = assert_run_attempt_shape(run_id, run_attempt)
        expected_names = self._validate_expected_artifact_names(expected_names)
        run = self.get_run(run_id)
        if run["run_attempt"] != run_attempt:
            raise ValidationError(
                f"run attempt mismatch: requested {run_attempt}, observed {run['run_attempt']}"
            )
        data = self._request(f"/repos/{self.repository}/actions/runs/{run_id}/artifacts")
        if not isinstance(data, dict):
            raise ReadError("artifacts response must be a JSON object")
        raw_artifacts = data.get("artifacts")
        if not isinstance(raw_artifacts, list):
            raise ReadError("artifacts response must contain an artifacts list")
        selected = {}
        for artifact in raw_artifacts:
            if not isinstance(artifact, dict):
                continue
            name = artifact.get("name")
            if not isinstance(name, str) or name not in expected_names:
                continue
            artifact_id = _positive_int(artifact.get("id"), "artifact id")
            if not _ARTIFACT_NAME.fullmatch(name):
                raise ReadError(f"artifact {artifact_id} has an unsafe name")
            expired = artifact.get("expired")
            if not isinstance(expired, bool):
                raise ReadError(f"artifact {artifact_id} has no boolean expired state")
            if expired:
                raise ReadError(f"artifact {artifact_id} is expired")
            run_ref = artifact.get("workflow_run")
            if not isinstance(run_ref, dict):
                raise ReadError(f"artifact {artifact_id} has no workflow_run object")
            observed_run_id = _positive_int(run_ref.get("id"), "artifact run id")
            if observed_run_id != run_id:
                raise ValidationError(
                    f"artifact {artifact_id} belongs to run {observed_run_id}, "
                    f"not the requested run {run_id}"
                )
            archive_url = artifact.get("archive_download_url")
            if not isinstance(archive_url, str) or not archive_url.startswith("https://"):
                raise ReadError(f"artifact {artifact_id} has no https archive_download_url")
            if name in selected:
                raise ValidationError(
                    f"duplicate artifact {name!r} for run {run_id} attempt {run_attempt}"
                )
            selected[name] = {
                "id": artifact_id,
                "name": name,
                "archive_download_url": archive_url,
            }
        self._require_expected_artifacts(selected, expected_names, run_id, run_attempt)
        return [selected[name] for name in sorted(expected_names)]

    @staticmethod
    def _validate_expected_artifact_names(expected_names: set[str]) -> set[str]:
        if not isinstance(expected_names, set) or not expected_names:
            raise ValidationError("expected artifact names must be a non-empty set")
        for name in expected_names:
            if not isinstance(name, str) or not _ARTIFACT_NAME.fullmatch(name):
                raise ValidationError(f"unsafe expected artifact name {name!r}")
        return expected_names

    @staticmethod
    def _require_expected_artifacts(
        selected: dict, expected_names: set[str], run_id: int, run_attempt: int
    ) -> None:
        missing = sorted(expected_names - selected.keys())
        if missing:
            raise ValidationError(
                f"missing artifacts {', '.join(missing)} for run {run_id} attempt {run_attempt}"
            )

    def download_artifact_zip(self, archive_url: str) -> bytes:
        """Download an artifact zip from its archive URL.

        GitHub serves ``archive_download_url`` today as an authenticated
        ``api.github.com`` endpoint that redirects (302) to the signed blob
        object (Azure Blob Storage). The token is sent ONLY to GitHub hosts;
        a plain signed blob/S3 URL (the historical shape) is fetched without
        the Authorization header so the token is never sent off-host, and the
        redirect handler strips Authorization whenever a host change occurs.
        """
        if not isinstance(archive_url, str) or not archive_url.startswith("https://"):
            raise ValidationError(f"artifact archive URL must be https, got {archive_url!r}")
        if urllib.parse.urlsplit(archive_url).hostname == "api.github.com":
            if not self.token:
                raise ReadError("GITHUB_TOKEN is not set; artifact download is unavailable")
            # The GitHub zip endpoint answers 415 to any non-JSON Accept
            # header, so request the API media type and follow its redirect.
            headers = {
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "onlineshop-delivery",
            }
        else:
            headers = {"Accept": "application/octet-stream", "User-Agent": "onlineshop-delivery"}
        request = urllib.request.Request(archive_url, headers=headers)
        try:
            with _ARTIFACT_OPENER.open(request, timeout=60) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            raise ReadError(f"artifact archive download failed with HTTP {error.code}") from error
        except urllib.error.URLError as error:
            raise ReadError(f"artifact archive download unreachable: {error.reason}") from error

    def list_main_candidate_runs(self, selected_run_id: int, limit: int = 10) -> list[dict]:
        """List newer successful ``main`` runs that published a complete candidate.

        AD-11: build order is derived from run ids (monotonic per repository),
        never assumed from times. Completeness is proven by the presence of a
        ``candidate-manifest-<run>-<attempt>`` artifact for the run's own
        attempt; the search is bounded to ``limit`` newer runs.
        """
        selected_run_id = _positive_int(selected_run_id, "selected run id")
        data = self._request(
            f"/repos/{self.repository}/actions/runs?branch=main&status=success&per_page=100"
        )
        if not isinstance(data, dict):
            raise ReadError("workflow runs response must be a JSON object")
        raw_runs = data.get("workflow_runs")
        if not isinstance(raw_runs, list):
            raise ReadError("workflow runs response must contain workflow_runs")
        newer = []
        for run in raw_runs:
            if not isinstance(run, dict):
                raise ReadError("each workflow run must be a JSON object")
            run_id = _positive_int(run.get("id"), "run id")
            if run_id <= selected_run_id:
                continue
            _positive_int(run.get("run_attempt"), "run attempt")
            head_sha = run.get("head_sha")
            if not isinstance(head_sha, str) or not _FULL_SHA.fullmatch(head_sha):
                raise ReadError(f"run {run_id} has a malformed head_sha")
            html_url = run.get("html_url")
            if not isinstance(html_url, str) or not html_url.startswith("https://"):
                raise ReadError(f"run {run_id} has no https html_url")
            created_at = run.get("created_at")
            if not isinstance(created_at, str) or not created_at:
                raise ReadError(f"run {run_id} has no created_at")
            newer.append(run)
        candidates = []
        for run in sorted(newer, key=lambda entry: entry["id"])[:limit]:
            run_id = run["id"]
            required = f"candidate-manifest-{run_id}-{run['run_attempt']}"
            try:
                self.list_artifacts_for_run(run_id, run["run_attempt"], {required})
            except ValidationError as error:
                if str(error).startswith("missing artifacts "):
                    continue
                raise
            candidates.append(
                {
                    "id": run_id,
                    "run_attempt": run["run_attempt"],
                    "head_sha": run["head_sha"],
                    "created_at": run["created_at"],
                    "html_url": run["html_url"],
                }
            )
        return candidates

    def get_branch_head_sha(self, branch: str) -> str:
        """Resolve a branch ref to its current head SHA (AD-11 reachability).

        ``compare_commits`` is SHA-only by contract; callers compare against
        the exact tip of protected main at preflight time instead of passing
        a ref.
        """
        if not isinstance(branch, str) or not branch or not _BRANCH_NAME.fullmatch(branch):
            raise ValidationError(f"unsafe branch name {branch!r}")
        data = self._request(
            f"/repos/{self.repository}/branches/{urllib.parse.quote(branch, safe='')}"
        )
        if not isinstance(data, dict):
            raise ReadError("branch response must be a JSON object")
        commit = data.get("commit")
        if not isinstance(commit, dict):
            raise ReadError(f"branch {branch!r} has no commit object")
        sha = commit.get("sha")
        if not isinstance(sha, str) or not _FULL_SHA.fullmatch(sha):
            raise ReadError(f"branch {branch!r} has no valid head SHA")
        return sha

    def compare_commits(self, base: str, head: str) -> dict:
        """GitHub compare: expose build-order-independent source relation.

        Returns ``{"status": ..., "ahead_by": ..., "behind_by": ...}`` where
        status is one of ``identical``, ``ahead``, ``behind``, ``diverged``.
        A head that is an ancestor of base reports ``behind`` (CT-CAND-04).
        """
        for label, value in (("base", base), ("head", head)):
            if not isinstance(value, str) or not _FULL_SHA.fullmatch(value):
                raise ValidationError(f"{label} must be a 40-character lowercase hex SHA")
        data = self._request(f"/repos/{self.repository}/compare/{base}...{head}")
        if not isinstance(data, dict):
            raise ReadError("compare response must be a JSON object")
        status = data.get("status")
        if status not in ("identical", "ahead", "behind", "diverged"):
            raise ReadError(f"compare returned an invalid status {status!r}")
        for key in ("ahead_by", "behind_by"):
            if not isinstance(data.get(key), int):
                raise ReadError(f"compare response is missing integer {key}")
        return {
            "status": status,
            "ahead_by": data["ahead_by"],
            "behind_by": data["behind_by"],
        }

    def create_release(self, tag: str, name: str, body: str) -> dict:
        """Create a published (non-draft) GitHub Release and verify the id."""
        if not isinstance(tag, str) or not tag:
            raise ValidationError("release tag must be a non-empty string")
        payload = json.dumps(
            {"tag_name": tag, "name": name, "body": body, "draft": False, "prerelease": False}
        ).encode()
        data = self._request(
            f"/repos/{self.repository}/releases", method="POST", data=payload
        )
        if not isinstance(data, dict):
            raise ReadError("create release response must be a JSON object")
        release_id = _positive_int(data.get("id"), "release id")
        html_url = data.get("html_url")
        if not isinstance(html_url, str) or not html_url.startswith("https://"):
            raise ReadError("create release response has no https html_url")
        if data.get("tag_name") != tag:
            raise ReadError(f"create release read-back tag mismatch for {tag}")
        return {"id": release_id, "html_url": html_url, "tag_name": tag}

    def upload_release_asset(self, release_id: int, name: str, content: bytes) -> dict:
        """Upload a release asset to the given release and read back its URL."""
        release_id = _positive_int(release_id, "release id")
        if not isinstance(name, str) or not _ARTIFACT_NAME.fullmatch(name):
            raise ValidationError(f"unsafe release asset name {name!r}")
        if not isinstance(content, bytes) or not content:
            raise ValidationError("release asset content must be non-empty bytes")
        if not self.token:
            raise ReadError("GITHUB_TOKEN is not set; release asset upload is unavailable")
        path = f"/repos/{self.repository}/releases/{release_id}/assets"
        quoted = urllib.parse.quote(name, safe="")
        request = urllib.request.Request(
            f"https://uploads.github.com{path}?name={quoted}",
            data=content,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/octet-stream",
                "User-Agent": "onlineshop-delivery",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
        except urllib.error.HTTPError as error:
            raise ReadError(
                f"release asset upload {name} failed with HTTP {error.code}"
            ) from error
        except urllib.error.URLError as error:
            raise ReadError(f"release asset upload {name} unreachable: {error.reason}") from error
        try:
            data = json.loads(body.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ReadError(f"release asset upload {name} returned invalid JSON") from error
        if not isinstance(data, dict):
            raise ReadError("release asset upload response must be a JSON object")
        url = data.get("browser_download_url")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise ReadError(f"release asset upload {name} has no https browser_download_url")
        if data.get("name") != name:
            raise ReadError(f"release asset upload read-back name mismatch for {name}")
        return {"name": name, "url": url}
