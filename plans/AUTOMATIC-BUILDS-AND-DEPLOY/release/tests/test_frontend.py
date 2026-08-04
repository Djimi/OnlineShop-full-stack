"""Unit tests for safe frontend archive validation and manifest verification."""

import os
import shutil
import sys
import tarfile
import tempfile
import unittest

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"),
)

from release_contract import checksums, frontend


def _write(path, content=b""):
    with open(path, "wb") as handle:
        handle.write(content)


class ArchiveValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp)

    def _archive_with(self, members):
        archive = os.path.join(self.temp, "evil.tar.gz")
        with tarfile.open(archive, "w:gz") as tar:
            for name, kind in members:
                info = tarfile.TarInfo(name)
                if kind == "file":
                    info.size = 0
                    tar.addfile(info, fileobj=None)
                else:
                    if kind == "symlink":
                        info.type = tarfile.SYMTYPE
                        info.linkname = "/etc/passwd"
                    elif kind == "hardlink":
                        info.type = tarfile.LNKTYPE
                        info.linkname = "somewhere"
                    elif kind == "fifo":
                        info.type = tarfile.FIFOTYPE
                    elif kind == "char":
                        info.type = tarfile.CHRTYPE
                    tar.addfile(info)
        return archive

    def test_clean_archive_ok(self):
        archive = os.path.join(self.temp, "clean.tar.gz")
        source = os.path.join(self.temp, "dist")
        os.makedirs(source)
        _write(os.path.join(source, "index.html"), b"<html></html>")
        with tarfile.open(archive, "w:gz") as tar:
            tar.add(source, arcname=".")
        ok, issues = frontend.validate_archive(archive)
        self.assertTrue(ok, msg=json_dumps(issues))

    def test_symlink_rejected(self):
        archive = self._archive_with([("app/link", "symlink")])
        ok, issues = frontend.validate_archive(archive)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "LINK_ENTRY" for i in issues))

    def test_hardlink_rejected(self):
        archive = self._archive_with([("app/hard", "hardlink")])
        ok, issues = frontend.validate_archive(archive)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "LINK_ENTRY" for i in issues))

    def test_fifo_rejected(self):
        archive = self._archive_with([("app/pipe", "fifo")])
        ok, issues = frontend.validate_archive(archive)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "DEVICE_ENTRY" for i in issues))

    def test_character_device_rejected(self):
        archive = self._archive_with([("app/tty", "char")])
        ok, issues = frontend.validate_archive(archive)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "DEVICE_ENTRY" for i in issues))

    def test_traversal_path_rejected(self):
        archive = self._archive_with([("app/../../escape", "file")])
        ok, issues = frontend.validate_archive(archive)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "TRAVERSAL_PATH" for i in issues))

    def test_absolute_path_rejected(self):
        archive = self._archive_with([("/etc/evil", "file")])
        ok, issues = frontend.validate_archive(archive)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "ABSOLUTE_PATH" for i in issues))

    def test_corrupt_archive_reports_error(self):
        archive = os.path.join(self.temp, "corrupt.tar.gz")
        _write(archive, b"this is not a tarball")
        ok, issues = frontend.validate_archive(archive)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "ARCHIVE_ERROR" for i in issues))


class ManifestVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.temp)

    def _tree(self, files):
        root = os.path.join(self.temp, "dist")
        os.makedirs(root)
        for name, content in files.items():
            path = os.path.join(root, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _write(path, content)
        return root

    def test_clean_manifest_passes(self):
        root = self._tree({"index.html": b"hi", "assets/a.js": b"a"})
        pairs = [
            (checksums.sha256_file(os.path.join(root, "index.html")), "index.html"),
            (checksums.sha256_file(os.path.join(root, "assets/a.js")), "assets/a.js"),
        ]
        lines = [f"{sha}  {path}" for sha, path in sorted(pairs, key=lambda pair: pair[1])]
        manifest = os.path.join(self.temp, "frontend-dist.sha256")
        with open(manifest, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
        ok, issues = frontend.verify_checksum_manifest(manifest, root)
        self.assertTrue(ok, msg=json_dumps(issues))

    def test_tampered_file_fails(self):
        root = self._tree({"index.html": b"hi"})
        manifest = os.path.join(self.temp, "frontend-dist.sha256")
        with open(manifest, "w", encoding="utf-8") as handle:
            handle.write(f"{checksums.sha256_hex(b'other')}  index.html\n")
        ok, issues = frontend.verify_checksum_manifest(manifest, root)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "MANIFEST_CHECKSUM_MISMATCH" for i in issues))

    def test_missing_file_fails(self):
        root = self._tree({})
        manifest = os.path.join(self.temp, "frontend-dist.sha256")
        with open(manifest, "w", encoding="utf-8") as handle:
            handle.write(f"{checksums.sha256_hex(b'x')}  index.html\n")
        ok, issues = frontend.verify_checksum_manifest(manifest, root)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "MANIFEST_FILE_MISSING" for i in issues))

    def test_unsorted_manifest_fails(self):
        root = self._tree({"a": b"1", "b": b"2"})
        manifest = os.path.join(self.temp, "frontend-dist.sha256")
        with open(manifest, "w", encoding="utf-8") as handle:
            handle.write(f"{checksums.sha256_hex(b'2')}  b\n{checksums.sha256_hex(b'1')}  a\n")
        ok, issues = frontend.verify_checksum_manifest(manifest, root)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "UNSORTED_MANIFEST" for i in issues))

    def test_unsafe_path_rejected(self):
        root = self._tree({})
        manifest = os.path.join(self.temp, "frontend-dist.sha256")
        with open(manifest, "w", encoding="utf-8") as handle:
            handle.write(f"{checksums.sha256_hex(b'x')}  ../../etc/passwd\n")
        ok, issues = frontend.verify_checksum_manifest(manifest, root)
        self.assertFalse(ok)
        self.assertTrue(any(i["code"] == "UNSAFE_MANIFEST_PATH" for i in issues))


def json_dumps(issues):
    import json

    return json.dumps(issues)


if __name__ == "__main__":
    unittest.main()
