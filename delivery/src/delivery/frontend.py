"""Shared candidate-frontend archive verification (CT-CAND-01/CT-CAND-03).

Both the staging gate (Phase 4) and production promotion (Phase 5) consume
the exact candidate frontend archive bytes: the archive digest must match the
manifest ``artifactDigest`` and the extracted tree's aggregate content
checksum must match the manifest ``contentChecksum``. This module is the
single implementation of those rules.
"""

from __future__ import annotations

import tarfile
import tempfile
from pathlib import Path

from .errors import ReadError, ValidationError
from .models import CandidateManifest
from .serialization import sha256_hex


def extract_archive_bytes(payload: bytes, destination: Path) -> None:
    """Safely extract a tar archive into ``destination`` (path traversal refused)."""
    try:
        with tarfile.open(fileobj=_BytesIO(payload), mode="r:*") as archive:
            _safe_extract(archive, destination)
    except (tarfile.TarError, OSError) as error:
        raise ValidationError(f"frontend archive is not a valid tar file: {error}") from error


def verify_frontend_archive(archive_path: str, manifest: CandidateManifest) -> Path:
    """Verify archive bytes and content checksum, then extract and return the dir."""
    archive = Path(archive_path)
    try:
        payload = archive.read_bytes()
    except OSError as error:
        raise ReadError(f"cannot read frontend archive {archive_path}: {error}") from error
    observed_digest = f"sha256:{sha256_hex(payload)}"
    if observed_digest != manifest.artifacts.frontend.artifactDigest:
        raise ValidationError(
            f"frontend archive digest {observed_digest} does not match manifest "
            f"{manifest.artifacts.frontend.artifactDigest}"
        )
    destination = Path(tempfile.mkdtemp(prefix="onlineshop-frontend-"))
    extract_archive_bytes(payload, destination)
    observed_checksum = content_checksum(destination)
    if observed_checksum != manifest.artifacts.frontend.contentChecksum:
        raise ValidationError(
            f"frontend content checksum {observed_checksum} does not match manifest "
            f"{manifest.artifacts.frontend.contentChecksum}"
        )
    return destination


def content_checksum(dist_dir: Path) -> str:
    """Content-only aggregate checksum of the tree (path-independent).

    Equivalent to ``find dist -type f -print0 | LC_ALL=C sort -z | xargs -0
    sha256sum | cut -d' ' -f1 | sha256sum``: every line is just the per-file
    SHA-256 hex (no path prefix), ordered by relative path (byte-wise, so
    ``LC_ALL=C`` in the shell pipeline), newline-terminated, and the aggregate
    is the SHA-256 of that byte string. Identical content therefore hashes
    identically regardless of the extraction directory or path base.
    """
    lines = []
    for path in sorted(
        dist_dir.rglob("*"), key=lambda p: p.relative_to(dist_dir).as_posix()
    ):
        if not path.is_file():
            continue
        lines.append(f"{sha256_hex(path.read_bytes())}\n")
    return sha256_hex("".join(lines).encode())


def _safe_extract(archive: tarfile.TarFile, destination: Path) -> None:
    root = destination.resolve()
    for member in archive.getmembers():
        target = (destination / member.name).resolve()
        if target != root and root not in target.parents:
            raise ValidationError(
                f"frontend archive member {member.name!r} escapes the extraction root"
            )
        if member.isdev() or member.isfifo() or member.issym() or member.islnk():
            raise ValidationError(
                f"frontend archive member {member.name!r} is not a regular file or directory"
            )
    archive.extractall(destination)


class _BytesIO:
    """Tiny file-like wrapper so tarfile can read an in-memory payload."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            chunk = self._data[self._pos :]
            self._pos = len(self._data)
            return chunk
        chunk = self._data[self._pos : self._pos + size]
        self._pos += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self._pos = offset
        elif whence == 1:
            self._pos += offset
        elif whence == 2:
            self._pos = len(self._data) + offset
        else:
            raise ValueError(f"invalid whence {whence}")
        return self._pos

    def tell(self) -> int:
        return self._pos


def sha256_file(path: Path) -> str:
    """Return the canonical hex SHA-256 of a file's bytes."""
    try:
        return sha256_hex(path.read_bytes())
    except OSError as error:
        raise ReadError(f"cannot read {path}: {error}") from error


__all__ = ["content_checksum", "extract_archive_bytes", "sha256_file", "verify_frontend_archive"]
