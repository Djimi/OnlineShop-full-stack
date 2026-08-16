"""Tests for ECR image and lifecycle policy adapters."""

import pytest
from conftest import client_error

from delivery.aws.ecr import (
    batch_get_image_digests,
    get_lifecycle_policy,
    get_lifecycle_policy_preview,
    put_image,
    put_lifecycle_policy,
    repository_digest,
    start_lifecycle_policy_preview,
)
from delivery.errors import AbsentResourceError, MutationVerificationError, ReadError

REPOSITORY = "onlineshop-items"
DIGEST_A = f"sha256:{'a' * 64}"
DIGEST_B = f"sha256:{'b' * 64}"


class FakeEcr:
    def __init__(self, images=None, lifecycle_policy=None):
        self.images = images or {}
        self.lifecycle_policy = lifecycle_policy
        self.error = None

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def describe_images(self, repositoryName, imageIds=None):
        self._maybe_fail()
        details = []
        for image_id in imageIds or []:
            tag = image_id.get("imageTag")
            digest = self.images.get(tag)
            if digest is None:
                raise client_error("ImageNotFoundException")
            details.append(
                {
                    "imageDigest": digest,
                    "imageTags": [tag],
                    "imageManifestMediaType": "application/vnd.oci.image.index.v1+json",
                }
            )
        return {"imageDetails": details}

    def put_image(self, repositoryName, imageTag, imageManifest):
        self._maybe_fail()
        self.images[imageTag] = DIGEST_A
        return {"repositoryName": repositoryName, "imageTag": imageTag}

    def get_lifecycle_policy(self, repositoryName):
        self._maybe_fail()
        if self.lifecycle_policy is None:
            raise client_error("LifecyclePolicyNotFoundException")
        return {"lifecyclePolicyText": self.lifecycle_policy, "repositoryName": repositoryName}

    def put_lifecycle_policy(self, repositoryName, lifecyclePolicyText):
        self._maybe_fail()
        self.lifecycle_policy = lifecyclePolicyText
        return {"repositoryName": repositoryName}

    def start_lifecycle_policy_preview(self, repositoryName):
        self._maybe_fail()
        return {"lifecyclePolicyPreviewId": "prev-1", "repositoryName": repositoryName}

    def get_lifecycle_policy_preview(self, repositoryName, lifecyclePolicyPreviewId):
        self._maybe_fail()
        return {"previewStatus": "COMPLETE", "previewResults": []}


class DriftingFakeEcr(FakeEcr):
    def put_lifecycle_policy(self, repositoryName, lifecyclePolicyText):
        self.lifecycle_policy = lifecyclePolicyText + "\n"
        return {"repositoryName": repositoryName}


class EventuallyVisibleFakeEcr(FakeEcr):
    """Returns empty imageDetails for the first N calls, then behaves normally."""

    def __init__(self, empty_attempts, images=None):
        super().__init__(images=images)
        self.calls = 0
        self.empty_attempts = empty_attempts

    def describe_images(self, repositoryName, imageIds=None):
        self._maybe_fail()
        self.calls += 1
        if self.calls <= self.empty_attempts:
            return {"imageDetails": []}
        return super().describe_images(repositoryName, imageIds)


def test_repository_digest_found():
    fake = FakeEcr(images={"main-latest": DIGEST_A})
    assert repository_digest(fake, REPOSITORY, "main-latest") == DIGEST_A


def test_repository_digest_retries_until_visible():
    fake = EventuallyVisibleFakeEcr(empty_attempts=2, images={"main-latest": DIGEST_A})
    assert repository_digest(fake, REPOSITORY, "main-latest") == DIGEST_A
    assert fake.calls == 3


def test_repository_digest_missing_tag_is_absent():
    fake = FakeEcr(images={})
    with pytest.raises(AbsentResourceError) as excinfo:
        repository_digest(fake, REPOSITORY, "main-latest")
    assert "not found" in str(excinfo.value)


def test_repository_digest_retries_exhausted_is_read_error():
    fake = EventuallyVisibleFakeEcr(empty_attempts=100, images={"main-latest": DIGEST_A})
    with pytest.raises(ReadError) as excinfo:
        repository_digest(fake, REPOSITORY, "main-latest")
    assert "not visible" in str(excinfo.value)
    assert fake.calls == 6


def test_repository_digest_absent_error_is_absent():
    fake = FakeEcr()
    fake.error = client_error("404")
    with pytest.raises(AbsentResourceError):
        repository_digest(fake, REPOSITORY, "main-latest")


def test_repository_digest_other_error_is_read_error():
    fake = FakeEcr()
    fake.error = client_error("AccessDenied")
    with pytest.raises(ReadError):
        repository_digest(fake, REPOSITORY, "main-latest")


def test_batch_get_image_digests_returns_all_requested_tags():
    fake = FakeEcr(images={"v1": DIGEST_A, "v2": DIGEST_B})
    assert batch_get_image_digests(fake, REPOSITORY, ["v1", "v2"]) == {
        "v1": DIGEST_A,
        "v2": DIGEST_B,
    }


def test_batch_get_image_digests_retries_until_visible():
    fake = EventuallyVisibleFakeEcr(empty_attempts=3, images={"v1": DIGEST_A, "v2": DIGEST_B})
    assert batch_get_image_digests(fake, REPOSITORY, ["v1", "v2"]) == {
        "v1": DIGEST_A,
        "v2": DIGEST_B,
    }
    assert fake.calls == 4


def test_batch_get_image_digests_retries_exhausted_is_read_error():
    fake = EventuallyVisibleFakeEcr(empty_attempts=100, images={"v1": DIGEST_A, "v2": DIGEST_B})
    with pytest.raises(ReadError) as excinfo:
        batch_get_image_digests(fake, REPOSITORY, ["v1", "v2"])
    assert "not visible" in str(excinfo.value)
    assert fake.calls == 6


def test_batch_get_image_digests_missing_tag_fails_closed():
    fake = FakeEcr(images={"v1": DIGEST_A})
    with pytest.raises(ReadError):
        batch_get_image_digests(fake, REPOSITORY, ["v1", "v3"])


def test_batch_get_image_digests_error_is_read_error():
    fake = FakeEcr()
    fake.error = client_error("ServerException")
    with pytest.raises(ReadError):
        batch_get_image_digests(fake, REPOSITORY, ["v1"])


def test_put_image_ok():
    fake = FakeEcr()
    response = put_image(fake, REPOSITORY, "release-0001", b'{"schemaVersion": 2}')
    assert response["imageTag"] == "release-0001"
    assert fake.images["release-0001"] == DIGEST_A


def test_put_image_policy_rejection_is_mutation_verification_error():
    fake = FakeEcr()
    fake.error = client_error("RepositoryPolicyValidationException")
    with pytest.raises(MutationVerificationError):
        put_image(fake, REPOSITORY, "release-0001", b"manifest")


def test_put_image_other_error_propagates():
    fake = FakeEcr()
    fake.error = client_error("ImageAlreadyExistsException")
    with pytest.raises(Exception) as excinfo:
        put_image(fake, REPOSITORY, "release-0001", b"manifest")
    assert excinfo.value.response["Error"]["Code"] == "ImageAlreadyExistsException"


def test_get_lifecycle_policy_absent_is_absent():
    fake = FakeEcr()
    with pytest.raises(AbsentResourceError):
        get_lifecycle_policy(fake, REPOSITORY)


def test_get_lifecycle_policy_ok():
    fake = FakeEcr(lifecycle_policy='{"rules": []}')
    assert get_lifecycle_policy(fake, REPOSITORY) == '{"rules": []}'


def test_put_lifecycle_policy_verified_read_back():
    fake = FakeEcr()
    policy = '{"rules": [{"rulePriority": 1}]}'
    assert put_lifecycle_policy(fake, REPOSITORY, policy) == policy


def test_put_lifecycle_policy_drift_is_mutation_verification_error():
    fake = DriftingFakeEcr()
    with pytest.raises(MutationVerificationError):
        put_lifecycle_policy(fake, REPOSITORY, '{"rules": []}')


def test_start_lifecycle_policy_preview_returns_id():
    fake = FakeEcr()
    assert start_lifecycle_policy_preview(fake, REPOSITORY) == "prev-1"


def test_get_lifecycle_policy_preview_ok():
    fake = FakeEcr()
    preview = get_lifecycle_policy_preview(fake, REPOSITORY, "prev-1")
    assert preview["previewStatus"] == "COMPLETE"
