"""Tests for the urllib-based GitHubApi helper (exact run/attempt authority)."""

import json
import urllib.error

import pytest

from delivery.errors import ReadError, ValidationError
from delivery.github import GitHubApi

REPO = "owner/repo"
RUN = 4712
ATTEMPT = 2
EXPECTED = f"candidate-manifest-{RUN}-{ATTEMPT}"


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
                "name": EXPECTED,
                "expired": False,
                "workflow_run": {"id": RUN},
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
    artifacts = api.list_run_artifacts(RUN, ATTEMPT, {EXPECTED})
    assert artifacts == [{"id": 11, "name": EXPECTED}]


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
        api.list_run_artifacts(RUN, ATTEMPT, {EXPECTED})


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
                "name": EXPECTED,
                "expired": False,
                "workflow_run": {"id": 9999},
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
        api.list_run_artifacts(RUN, ATTEMPT, {EXPECTED})


def test_list_run_artifacts_rejects_artifact_named_for_wrong_attempt(monkeypatch):
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
                "name": f"candidate-manifest-{RUN}-99",
                "expired": False,
                "workflow_run": {"id": RUN},
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
    with pytest.raises(ValidationError, match="missing artifacts"):
        api.list_run_artifacts(RUN, ATTEMPT, {EXPECTED})


def test_list_run_artifacts_rejects_zero_run_id():
    with pytest.raises(ValidationError):
        GitHubApi(REPO, token="t").list_run_artifacts(0, 1, {"candidate-manifest-0-1"})


def test_list_run_artifacts_ignores_buildx_and_malformed_unrelated_entries(monkeypatch):
    run_json = {
        "id": RUN,
        "run_attempt": ATTEMPT,
        "run_number": 7,
        "head_sha": "b" * 40,
        "head_branch": "main",
        "html_url": f"https://github.com/{REPO}/actions/runs/{RUN}",
    }
    expected = {
        f"candidate-manifest-{RUN}-{ATTEMPT}",
        f"frontend-archive-{RUN}-{ATTEMPT}",
        f"sboms-{RUN}-{ATTEMPT}",
        f"test-results-{RUN}-{ATTEMPT}",
    }
    artifacts = [
        {
            "id": index,
            "name": name,
            "expired": False,
            "workflow_run": {"id": RUN},
        }
        for index, name in enumerate(sorted(expected), start=11)
    ]
    artifacts.extend(
        [
            {
                "id": 99,
                "name": "Djimi~OnlineShop-full-stack~QVQL7U.dockerbuild",
                "expired": False,
            },
            {"id": 100, "name": f"candidate-manifest-{RUN}-{ATTEMPT}-similar"},
            {"id": "malformed-without-name"},
            "malformed top-level entry",
        ]
    )
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}": FakeResponse(run_json),
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}/artifacts": FakeResponse(
                {"artifacts": artifacts}
            ),
        },
    )

    selected = api.list_run_artifacts(RUN, ATTEMPT, expected)

    assert {artifact["name"] for artifact in selected} == expected


def test_list_run_artifacts_rejects_duplicate_expected_artifact(monkeypatch):
    run_json = {
        "id": RUN,
        "run_attempt": ATTEMPT,
        "run_number": 7,
        "head_sha": "b" * 40,
        "head_branch": "main",
        "html_url": f"https://github.com/{REPO}/actions/runs/{RUN}",
    }
    artifact = {
        "id": 11,
        "name": EXPECTED,
        "expired": False,
        "workflow_run": {"id": RUN},
    }
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}": FakeResponse(run_json),
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}/artifacts": FakeResponse(
                {"artifacts": [artifact, dict(artifact, id=12)]}
            ),
        },
    )

    with pytest.raises(ValidationError, match="duplicate artifact"):
        api.list_run_artifacts(RUN, ATTEMPT, {EXPECTED})


@pytest.mark.parametrize(
    ("artifact", "error"),
    [
        (None, ValidationError),
        (
            {
                "id": 11,
                "name": EXPECTED,
                "expired": True,
                "workflow_run": {"id": RUN},
            },
            ReadError,
        ),
        (
            {
                "id": "11",
                "name": EXPECTED,
                "expired": False,
                "workflow_run": {"id": RUN},
            },
            ValidationError,
        ),
        (
            {
                "id": 11,
                "name": EXPECTED,
                "workflow_run": {"id": RUN},
            },
            ReadError,
        ),
    ],
)
def test_list_run_artifacts_rejects_missing_expired_or_malformed_selected(
    monkeypatch, artifact, error
):
    run_json = {
        "id": RUN,
        "run_attempt": ATTEMPT,
        "run_number": 7,
        "head_sha": "b" * 40,
        "head_branch": "main",
        "html_url": f"https://github.com/{REPO}/actions/runs/{RUN}",
    }
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}": FakeResponse(run_json),
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}/artifacts": FakeResponse(
                {"artifacts": [] if artifact is None else [artifact]}
            ),
        },
    )

    with pytest.raises(error):
        api.list_run_artifacts(RUN, ATTEMPT, {EXPECTED})


def test_list_downloadable_artifact_uses_authoritative_attempt_not_artifact_field(monkeypatch):
    name = f"staging-record-{RUN}-{ATTEMPT}"
    run_json = {
        "id": RUN,
        "run_attempt": ATTEMPT,
        "run_number": 7,
        "head_sha": "b" * 40,
        "head_branch": "main",
        "html_url": f"https://github.com/{REPO}/actions/runs/{RUN}",
    }
    artifact = {
        "id": 11,
        "name": name,
        "expired": False,
        "archive_download_url": "https://example.com/artifacts/11.zip",
        "workflow_run": {"id": RUN},
    }
    api = _api(
        monkeypatch,
        {
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}": FakeResponse(run_json),
            f"https://api.github.com/repos/{REPO}/actions/runs/{RUN}/artifacts": FakeResponse(
                {"artifacts": [artifact]}
            ),
        },
    )

    selected = api.list_artifacts_for_run(RUN, ATTEMPT, {name})

    assert selected == [
        {
            "id": 11,
            "name": name,
            "archive_download_url": "https://example.com/artifacts/11.zip",
        }
    ]


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


def test_download_artifact_zip_sends_token_only_to_github_host(monkeypatch):
    seen = {}

    class BytesResponse:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    class FakeOpener:
        def open(self, request, timeout=None):
            seen["url"] = request.full_url
            seen["authorization"] = request.get_header("Authorization")
            seen["accept"] = request.get_header("Accept")
            return BytesResponse(b"zip-bytes")

    monkeypatch.setattr("delivery.github._ARTIFACT_OPENER", FakeOpener())
    api = GitHubApi(REPO, token="secret-token")

    github_url = "https://api.github.com/repos/owner/repo/actions/artifacts/11/zip"
    assert api.download_artifact_zip(github_url) == b"zip-bytes"
    assert seen["url"] == github_url
    assert seen["authorization"] == "Bearer secret-token"
    assert seen["accept"] == "application/vnd.github+json"

    s3_url = "https://example.s3.amazonaws.com/artifacts/11.zip"
    assert api.download_artifact_zip(s3_url) == b"zip-bytes"
    assert seen["url"] == s3_url
    assert seen["authorization"] is None
    assert seen["accept"] == "application/octet-stream"


def test_artifact_redirect_handler_strips_authorization_cross_host():
    from delivery.github import _StripAuthRedirectHandler

    handler = _StripAuthRedirectHandler()
    request = urllib.request.Request(
        "https://api.github.com/repos/owner/repo/actions/artifacts/11/zip",
        headers={"Authorization": "Bearer secret-token"},
    )
    blob_url = "https://blob.core.windows.net/actions/artifacts/11.zip"
    new_request = handler.redirect_request(
        request, None, 302, "Found", {"Location": blob_url}, blob_url
    )
    assert new_request is not None
    assert new_request.get_header("Authorization") is None
    assert new_request.full_url == blob_url

    same_host = handler.redirect_request(
        request, None, 302, "Found",
        {"Location": "https://api.github.com/repos/owner/repo/actions/artifacts/12/zip"},
        "https://api.github.com/repos/owner/repo/actions/artifacts/12/zip",
    )
    assert same_host is not None
    assert same_host.get_header("Authorization") == "Bearer secret-token"


def test_download_artifact_zip_github_host_without_token_fails_closed(monkeypatch):
    def urlopen(request, timeout=None):
        raise AssertionError("urlopen must not be called without a token")

    monkeypatch.setattr("delivery.github.urllib.request.urlopen", urlopen)
    with pytest.raises(ReadError):
        GitHubApi(REPO, token=None).download_artifact_zip(
            "https://api.github.com/repos/owner/repo/actions/artifacts/11/zip"
        )
