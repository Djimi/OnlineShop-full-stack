"""Offline gates for the frontend content checksum (CT-CAND-01/03).

The aggregate content checksum must be path-independent: every line is just
the per-file SHA-256 hex, ordered by relative path (byte-wise), so identical
content hashes identically regardless of the extraction directory or path
base and regardless of the host locale.
"""

import shutil
import subprocess
from pathlib import Path

from delivery.frontend import content_checksum

PIPELINE = (
    "find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum "
    "| cut -d' ' -f1 | sha256sum | cut -d' ' -f1"
)


def _dist_tree(root: Path) -> Path:
    dist = root / "frontend-dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text('<html><body><div id="root"></div></body></html>')
    (dist / "assets").mkdir()
    (dist / "assets" / "app.js").write_text("console.log('ok');\n")
    (dist / "assets" / "Home-B1xYZ.js").write_text("export const Home = () => 1;\n")
    (dist / "assets" / "ItemDetail-X2.js").write_text("export const ItemDetail = () => 2;\n")
    (dist / "assets" / "apiError.js").write_text("export const apiError = () => 3;\n")
    return dist


def _shell_checksum(dist: Path) -> str:
    result = subprocess.run(
        ["bash", "-c", PIPELINE], cwd=dist, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_content_checksum_equals_shell_pipeline(tmp_path: Path) -> None:
    dist = _dist_tree(tmp_path)
    assert content_checksum(dist) == _shell_checksum(dist)


def test_content_checksum_is_path_independent(tmp_path: Path) -> None:
    dist = _dist_tree(tmp_path / "first-root")
    relocated = _dist_tree(tmp_path / "second-root")
    assert content_checksum(dist) == content_checksum(relocated)


def test_content_checksum_changes_when_content_changes(tmp_path: Path) -> None:
    dist = _dist_tree(tmp_path)
    before = content_checksum(dist)
    (dist / "assets" / "app.js").write_text("console.log('changed');\n")
    assert content_checksum(dist) != before


def test_shell_pipeline_ignores_host_locale(tmp_path: Path) -> None:
    # Mixed-case filenames sort differently under en_US.UTF-8 collation than
    # under byte order; LC_ALL=C pins the sort so every computing side agrees.
    dist = _dist_tree(tmp_path)
    result = subprocess.run(
        ["bash", "-c", f"LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 {PIPELINE}"],
        cwd=dist,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == content_checksum(dist)


def test_extracted_copy_hashes_identically(tmp_path: Path) -> None:
    dist = _dist_tree(tmp_path)
    copy = tmp_path / "extracted-elsewhere"
    shutil.copytree(dist, copy)
    assert content_checksum(copy) == content_checksum(dist)
