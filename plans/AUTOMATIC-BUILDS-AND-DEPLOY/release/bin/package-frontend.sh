#!/usr/bin/env bash
set -euo pipefail

# Packages a Vite `dist` directory reproducibly as the canonical frontend
# candidate artifact `frontend-dist.tar.gz` plus a sorted per-file checksum
# manifest (Pass 3, subphase 3.2).
#
# The archive is built with normalized metadata (owner/group 0, mtime @0,
# sorted members, gzip -n) so two builds of the same source produce byte-
# identical archives. Symlinks, device/FIFO/socket entries, and paths that
# escape the dist root are rejected: they must never enter the artifact.
#
# Usage:
#   package-frontend.sh --dist <dir> --out <dir> [--label <name>]
#
# Outputs in <out>:
#   frontend-dist.tar.gz       normalized archive (artifact name is fixed)
#   frontend-dist.sha256       sorted "<sha256>  <path>" per-file manifest
#   frontend-package.json      {artifact, sha256, checksumManifest, fileCount}
#
# Exit codes: 0 success, 1 validation/packaging error, 2 usage error.

usage() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" >&2
}

DIST=""
OUT=""
LABEL="frontend"
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dist) DIST="${2:-}"; shift 2 ;;
    --out) OUT="${2:-}"; shift 2 ;;
    --label) LABEL="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$DIST" ] && [ -n "$OUT" ] || { usage; exit 2; }
[ -d "$DIST" ] || { echo "ERROR: --dist is not a directory: $DIST" >&2; exit 1; }
mkdir -p "$OUT"

# Refuse to package anything that could escape the artifact or that is not a
# plain file tree (symlinks, hardlinks, devices, FIFOs, sockets).
if [[ -n "$(find "$DIST" \( -type l -o -type b -o -type c -o -type p -o -type s \) -print -quit)" ]]; then
  echo "ERROR: $DIST contains links/device files; refusing to package" >&2
  exit 1
fi
if ! find "$DIST" -type f -print -quit | grep -q .; then
  echo "ERROR: $DIST contains no regular files; refusing to package" >&2
  exit 1
fi

# Normalized archive: sorted members, uid/gid 0, epoch mtime, pax metadata
# stripped of atime/ctime, gzip without a timestamp header. `@0` is epoch.
# `--sort=name` is supported by GNU tar (present on all GitHub runners).
tar -C "$DIST" -cf "$OUT/frontend-dist.tar" \
  --sort=name \
  --owner=0 --group=0 --numeric-owner \
  --mtime=@0 \
  --format=posix \
  --pax-option=delete=atime,delete=ctime,delete=schily.xattr \
  .
gzip -n "$OUT/frontend-dist.tar"

# Sorted per-file checksum manifest: paths are sorted *before* hashing so the
# output lines are ordered by relative path.
( cd "$DIST" && find . -type f -print0 | sort -z | xargs -0 sha256sum ) > "$OUT/frontend-dist.sha256"

ARCHIVE_SHA=$(sha256sum "$OUT/frontend-dist.tar.gz")
ARCHIVE_SHA=${ARCHIVE_SHA%% *}
FILE_COUNT=$(find "$DIST" -type f | wc -l)

jq -n \
  --arg artifact "frontend-dist.tar.gz" \
  --arg sha256 "$ARCHIVE_SHA" \
  --arg checksumManifest "frontend-dist.sha256" \
  --argjson fileCount "$FILE_COUNT" \
  --arg label "$LABEL" \
  '{artifact: $artifact, label: $label, sha256: $sha256, checksumManifest: $checksumManifest, fileCount: $fileCount}' \
  > "$OUT/frontend-package.json"

echo "Packaged $FILE_COUNT files into $OUT/frontend-dist.tar.gz"
echo "Archive sha256: $ARCHIVE_SHA"
