"""Tests for S3 object checksum and read-back adapters."""

import base64
import hashlib
import io

import pytest
from conftest import client_error

from delivery.aws.s3 import (
    get_object_sha256,
    list_objects,
    object_exists,
    put_object,
)
from delivery.errors import AbsentResourceError, MutationVerificationError, ReadError

BUCKET = "onlineshop-frontend"
KEY = "index.html"
BODY = b"<!doctype html><html></html>"
BODY_HEX = hashlib.sha256(BODY).hexdigest()
BODY_B64 = base64.b64encode(hashlib.sha256(BODY).digest()).decode()


class FakeS3:
    def __init__(self, objects=None, checksums=None):
        self.objects = objects or {}
        self.checksums = checksums or {}
        self.error = None
        self.checksum_mode_required = False
        self.puts = []

    def _maybe_fail(self):
        if self.error:
            raise self.error

    def head_object(self, Bucket, Key, ChecksumMode=None):
        self._maybe_fail()
        if self.checksum_mode_required and ChecksumMode != "ENABLED":
            return {"ContentLength": len(self.objects[Key]), "ETag": '"etag-fake"'}
        if Key not in self.objects:
            raise client_error("404", "Not Found")
        head = {"ContentLength": len(self.objects[Key]), "ETag": '"etag-fake"'}
        if Key in self.checksums:
            head["ChecksumSHA256"] = self.checksums[Key]
        return head

    def get_object(self, Bucket, Key):
        self._maybe_fail()
        if Key not in self.objects:
            raise client_error("NoSuchKey", "Not Found")
        return {"Body": io.BytesIO(self.objects[Key])}

    def put_object(self, Bucket, Key, Body, ChecksumAlgorithm="SHA256", ContentType=None):
        self._maybe_fail()
        self.puts.append({"Key": Key, "ContentType": ContentType})
        self.objects[Key] = Body
        self.checksums[Key] = base64.b64encode(hashlib.sha256(Body).digest()).decode()
        return {"ETag": '"etag-fake"'}

    def list_objects_v2(self, Bucket, Prefix):
        self._maybe_fail()
        keys = sorted(key for key in self.objects if key.startswith(Prefix))
        return {"Contents": [{"Key": key, "Size": len(self.objects[key])} for key in keys]}


class LyingFakeS3(FakeS3):
    def put_object(self, Bucket, Key, Body, ChecksumAlgorithm="SHA256", ContentType=None):
        self.objects[Key] = Body + b"-corrupt"
        self.checksums[Key] = base64.b64encode(hashlib.sha256(Body).digest()).decode()
        return {"ETag": '"etag-fake"'}


class WrongChecksumFakeS3(FakeS3):
    def put_object(self, Bucket, Key, Body, ChecksumAlgorithm="SHA256", ContentType=None):
        self.objects[Key] = Body
        self.checksums[Key] = base64.b64encode(hashlib.sha256(b"other").digest()).decode()
        return {"ETag": '"etag-fake"'}


def test_object_exists_true():
    fake = FakeS3(objects={KEY: BODY})
    assert object_exists(fake, BUCKET, KEY) is True


def test_object_exists_404_is_false():
    fake = FakeS3()
    assert object_exists(fake, BUCKET, KEY) is False


def test_object_exists_other_error_is_read_error():
    fake = FakeS3(objects={KEY: BODY})
    fake.error = client_error("AccessDenied")
    with pytest.raises(ReadError):
        object_exists(fake, BUCKET, KEY)


def test_get_object_sha256_prefers_server_checksum():
    fake = FakeS3(objects={KEY: BODY}, checksums={KEY: BODY_B64})
    assert get_object_sha256(fake, BUCKET, KEY) == BODY_HEX


def test_get_object_sha256_falls_back_to_body_hash():
    fake = FakeS3(objects={KEY: BODY})
    assert get_object_sha256(fake, BUCKET, KEY) == BODY_HEX


def test_get_object_sha256_absent_is_absent():
    fake = FakeS3()
    with pytest.raises(AbsentResourceError):
        get_object_sha256(fake, BUCKET, KEY)


def test_get_object_sha256_other_error_is_read_error():
    fake = FakeS3(objects={KEY: BODY})
    fake.error = client_error("AccessDenied")
    with pytest.raises(ReadError):
        get_object_sha256(fake, BUCKET, KEY)


def test_put_object_ok_returns_canonical_hex():
    fake = FakeS3()
    assert put_object(fake, BUCKET, KEY, BODY) == BODY_HEX
    assert fake.objects[KEY] == BODY


def test_put_object_sets_content_type_for_known_extensions():
    fake = FakeS3()
    put_object(fake, BUCKET, "index.html", BODY)
    put_object(fake, BUCKET, "assets/app.js", BODY)
    put_object(fake, BUCKET, "assets/style.css", BODY)
    put_object(fake, BUCKET, "assets/logo.svg", BODY)
    put_object(fake, BUCKET, "release.json", BODY)
    put_object(fake, BUCKET, "unknown.bin", BODY)
    by_key = {entry["Key"]: entry["ContentType"] for entry in fake.puts}
    assert by_key["index.html"] == "text/html"
    assert by_key["assets/app.js"] == "application/javascript"
    assert by_key["assets/style.css"] == "text/css"
    assert by_key["assets/logo.svg"] == "image/svg+xml"
    assert by_key["release.json"] == "application/json"
    assert by_key["unknown.bin"] is None


def test_put_object_size_mismatch_raises():
    fake = LyingFakeS3()
    with pytest.raises(MutationVerificationError):
        put_object(fake, BUCKET, KEY, BODY)


def test_put_object_checksum_mismatch_raises():
    fake = WrongChecksumFakeS3()
    with pytest.raises(MutationVerificationError):
        put_object(fake, BUCKET, KEY, BODY)


def test_put_object_requests_checksum_mode_enabled_read_back():
    fake = FakeS3()
    fake.checksum_mode_required = True
    assert put_object(fake, BUCKET, KEY, BODY) == BODY_HEX
    assert fake.objects[KEY] == BODY


def test_get_object_sha256_requests_checksum_mode_enabled():
    fake = FakeS3(objects={KEY: BODY}, checksums={KEY: BODY_B64})
    fake.checksum_mode_required = True
    assert get_object_sha256(fake, BUCKET, KEY) == BODY_HEX


def test_list_objects_returns_matching_keys():
    fake = FakeS3(objects={"_releases/v1/index.html": BODY, "index.html": BODY})
    keys = [entry["Key"] for entry in list_objects(fake, BUCKET, "_releases/v1/")]
    assert keys == ["_releases/v1/index.html"]


def test_list_objects_empty_prefix_is_empty_list():
    fake = FakeS3()
    assert list_objects(fake, BUCKET, "missing/") == []
