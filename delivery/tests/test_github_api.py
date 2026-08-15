"""Tests for the urllib-based GitHubApi helper (exact run/attempt authority)."""

import json
import urllib.error

import pytest

from delivery.errors import ReadError, ValidationError
from delivery.github import GitHubApi

REPO = "owner/repo"
RUN = 4712
ATTEMPT = 2


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def read(self):
        return self.payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _api(monkeypatch, responses: dict, token="token"):
    def urlopen(request, timeout=None):
        url = request.full_url
        if url in responses:
            return responses[url]
        raise urllib.error.HTTPError(url, 404, "not found", {}, None)

    monkeypatch.setattr("delivery.github.urllib.request.urlopen", urlopen)
    return GitHubApi(REPO, token=token)


def test_requires_repository_shape():
    with pytest.raises(ValidationError):
        GitHubApi("no-slash")


def test_request_without_token_fails_closed(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    api = GitHubApi(REPO, token=None)
    with pytest.raises(ReadError):
        api.list_releases()


def test_http_error_is_read_error(monkeypatch):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 500, "boom", {}, None)

    monkeypatch.setattr("delivery.github.urllib.request.urlopen", urlopen)
    api = GitHubApi(REPO, token="token")
    with pytest.raises(ReadError):
        api.list_releases()


def test_list_run_artifacts_validates_exact_run_and_attempt(monkeypatch):
    run_json = {
        "id": RUN,
        "run_attempt": ATTEMPT,
        "run_number": 7,
        "head_sha": "b" * 40,
        "head_branch": "feature/x",
        "html_url": f"https://github.com/{REPO}/actions/runs/{RUN}",
    }
    artifacts_json = {
        "artifacts": [
            {
                "id": 11,
                "name": f"candidate-manifest-{RUN}-{ATTEMPT}",
                "workflow_run": {"id": RUN, "run_attempt": ATTEMPT},
            }
        ]
    }
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}": FakeResponse(run_json),
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}/artifacts": FakeResponse(
                artifacts_json
            ),
        },
    )
    artifacts = api.list_run_artifacts(RUN, ATTEMPT)
    assert artifacts == [{"id": 11, "name": f"candidate-manifest-{RUN}-{ATTEMPT}"}]


def test_list_run_artifacts_rejects_run_attempt_mismatch(monkeypatch):
    run_json = {
        "id": RUN,
        "run_attempt": 99,
        "run_number": 7,
        "head_sha": "b" * 40,
        "head_branch": "feature/x",
        "html_url": "https://github.com/owner/repo/actions/runs/4712",
    }
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}": FakeResponse(run_json),
        },
    )
    with pytest.raises(ValidationError):
        api.list_run_artifacts(RUN, ATTEMPT)


def test_list_run_artifacts_rejects_foreign_run_artifact(monkeypatch):
    run_json = {
        "id": RUN,
        "run_attempt": ATTEMPT,
        "run_number": 7,
        "head_sha": "b" * 40,
        "head_branch": "feature/x",
        "html_url": "https://github.com/owner/repo/actions/runs/4712",
    }
    artifacts_json = {
        "artifacts": [
            {
                "id": 11,
                "name": "other-manifest",
                "workflow_run": {"id": 9999, "run_attempt": ATTEMPT},
            }
        ]
    }
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}": FakeResponse(run_json),
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}/artifacts": FakeResponse(
                artifacts_json
            ),
        },
    )
    with pytest.raises(ValidationError):
        api.list_run_artifacts(RUN, ATTEMPT)


def test_list_run_artifacts_rejects_attempt_mismatch(monkeypatch):
    run_json = {
        "id": RUN,
        "run_attempt": ATTEMPT,
        "run_number": 7,
        "head_sha": "b" * 40,
        "head_branch": "feature/x",
        "html_url": "https://github.com/owner/repo/actions/runs/4712",
    }
    artifacts_json = {
        "artifacts": [
            {
                "id": 11,
                "name": "other-manifest",
                "workflow_run": {"id": RUN, "run_attempt": 99},
            }
        ]
    }
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}": FakeResponse(run_json),
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}/artifacts": FakeResponse(
                artifacts_json
            ),
        },
    )
    with pytest.raises(ValidationError):
        api.list_run_artifacts(RUN, ATTEMPT)


def test_list_run_artifacts_rejects_missing_artifact_run_attempt(monkeypatch):
    run_json = {
        "id": RUN,
        "run_attempt": ATTEMPT,
        "run_number": 7,
        "head_sha": "b" * 40,
        "head_branch": "feature/x",
        "html_url": "https://github.com/owner/repo/actions/runs/4712",
    }
    artifacts_json = {
        "artifacts": [{"id": 11, "name": "other-manifest", "workflow_run": {"id": RUN}}]
    }
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}": FakeResponse(run_json),
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}/artifacts": FakeResponse(
                artifacts_json
            ),
        },
    )
    with pytest.raises(ValidationError):
        api.list_run_artifacts(RUN, ATTEMPT)


def test_list_run_artifacts_rejects_zero_run_id():
    with pytest.raises(ValidationError):
        GitHubApi(REPO, token="t").list_run_artifacts(0, 1)


def test_list_releases_parses_assets(monkeypatch):
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/releases": FakeResponse(
                [
                    {
                        "tag_name": "release-0002",
                        "id": 5,
                        "draft": False,
                        "prerelease": False,
                        "assets": [
                            {
                                "name": "release-manifest.json",
                                "browser_download_url": "https://example.com/manifest.json",
                            }
                        ],
                    }
                ]
            )
        },
    )
    releases = api.list_releases()
    assert releases[0]["tag_name"] == "release-0002"
    assert releases[0]["assets"][0]["name"] == "release-manifest.json"


def test_list_releases_filters_drafts_and_prereleases(monkeypatch):
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/releases": FakeResponse(
                [
                    {
                        "tag_name": "release-0004",
                        "id": 9,
                        "draft": True,
                        "prerelease": False,
                        "assets": [],
                    },
                    {
                        "tag_name": "release-0003",
                        "id": 8,
                        "draft": False,
                        "prerelease": True,
                        "assets": [],
                    },
                    {
                        "tag_name": "release-0002",
                        "id": 5,
                        "draft": False,
                        "prerelease": False,
                        "assets": [
                            {
                                "name": "release-manifest.json",
                                "browser_download_url": "https://example.com/manifest.json",
                            }
                        ],
                    },
                ]
            )
        },
    )
    releases = api.list_releases()
    # only the published release survives; drafts/prereleases are invisible
    assert [release["tag_name"] for release in releases] == ["release-0002"]


def test_list_releases_non_boolean_flags_fail_closed(monkeypatch):
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/releases": FakeResponse(
                [{"tag_name": "release-0002", "id": 5, "draft": "false", "assets": []}]
            )
        },
    )
    with pytest.raises(ReadError):
        api.list_releases()


def test_list_releases_malformed_asset_fails_closed(monkeypatch):
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/releases": FakeResponse(
                [
                    {
                        "tag_name": "release-0002",
                        "id": 5,
                        "draft": False,
                        "prerelease": False,
                        "assets": [{"name": ""}],
                    }
                ]
            )
        },
    )
    with pytest.raises(ReadError):
        api.list_releases()


def test_download_asset_returns_bytes(monkeypatch):
    api = _api(
        monkeypatch,
        {"https://example.com/manifest.json": FakeResponse({"releaseId": "release-0002"})},
    )
    assert b"release-0002" in api.download_asset("https://example.com/manifest.json")


def test_download_asset_rejects_non_https_url():
    with pytest.raises(ValidationError):
        GitHubApi(REPO, token="t").download_asset("ftp://example.com/x")


def test_download_asset_http_error_is_read_error(monkeypatch):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "nope", {}, None)

    monkeypatch.setattr("delivery.github.urllib.request.urlopen", urlopen)
    with pytest.raises(ReadError):
        GitHubApi(REPO, token="t").download_asset("https://example.com/x")


def test_invalid_json_response_is_read_error(monkeypatch):
    class BrokenResponse:
        def read(self):
            return b"{not json"

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(
        "delivery.github.urllib.request.urlopen",
        lambda request, timeout=None: BrokenResponse(),
    )
    with pytest.raises(ReadError):
        GitHubApi(REPO, token="t").list_releases()
