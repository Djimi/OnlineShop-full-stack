#!/usr/bin/env bash
set -euo pipefail

# Generates an SPDX JSON SBOM with a pinned Syft version (Pass 3,
# subphase 3.2). SYFT_VERSION and SYFT_LINUX_AMD64_SHA256 pin the exact tool
# binary; the archive checksum is verified before the tool is ever executed.
#
# Usage:
#   generate-sbom.sh --target <syft-target> --output <out.spdx.json>
#
# `--target` is a syft source: `registry:<repo>@sha256:<digest>`,
# `docker:<image>`, or `dir:<path>` (e.g. `dir:frontend/dist`).
#
# When SYFT_TOOL is set (tests, or a preinstalled binary) it is used as-is.
# Otherwise the pinned archive is downloaded to a cache directory (override
# with SYFT_CACHE_DIR) and verified against the pinned checksum.
#
# Exit codes: 0 success, 1 generation/verification error, 2 usage error.

SYFT_VERSION="v1.50.0"
SYFT_ARCHIVE="syft_1.50.0_linux_amd64.tar.gz"
SYFT_LINUX_AMD64_SHA256="bf7b29ff57f06da30918266a0e1c2885a8f99784798d1bdb1628886aa015d788"
SYFT_URL="https://github.com/anchore/syft/releases/download/${SYFT_VERSION}/${SYFT_ARCHIVE}"

usage() {
  sed -n '2,18p' "${BASH_SOURCE[0]}" >&2
}

TARGET=""
OUTPUT=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --output) OUTPUT="${2:-}"; shift 2 ;;
    --help) usage; exit 0 ;;
    *) echo "ERROR: unknown option: $1" >&2; usage; exit 2 ;;
  esac
done
[ -n "$TARGET" ] && [ -n "$OUTPUT" ] || { usage; exit 2; }

if [ -n "${SYFT_TOOL:-}" ]; then
  SYFT_BIN="$SYFT_TOOL"
  [ -x "$SYFT_BIN" ] || { echo "ERROR: SYFT_TOOL is not executable: $SYFT_BIN" >&2; exit 1; }
else
  CACHE="${SYFT_CACHE_DIR:-${TMPDIR:-/tmp}/onlineshop-syft}"
  mkdir -p "$CACHE"
  ARCHIVE="$CACHE/$SYFT_ARCHIVE"
  SYFT_BIN="$CACHE/syft-$SYFT_VERSION"

  if [ ! -x "$SYFT_BIN" ]; then
    if [ ! -f "$ARCHIVE" ]; then
      curl -fSL --retry 3 -o "$ARCHIVE" "$SYFT_URL" || { echo "ERROR: failed to download $SYFT_URL" >&2; exit 1; }
    fi
    ACTUAL=$(sha256sum "$ARCHIVE")
    ACTUAL=${ACTUAL%% *}
    if [ "$ACTUAL" != "$SYFT_LINUX_AMD64_SHA256" ]; then
      echo "ERROR: syft archive checksum mismatch (expected $SYFT_LINUX_AMD64_SHA256, got $ACTUAL)" >&2
      exit 1
    fi
    tar -xzf "$ARCHIVE" -C "$CACHE"
    mv "$CACHE/syft" "$SYFT_BIN"
    chmod +x "$SYFT_BIN"
  fi
fi

"$SYFT_BIN" "$TARGET" -o spdx-json="$OUTPUT"
echo "Generated $OUTPUT from $TARGET (syft $SYFT_VERSION)"
