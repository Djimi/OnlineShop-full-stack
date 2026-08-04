#!/usr/bin/env bash

# Strict input-validation helpers for release workflows (Pass 3, subphase 3.1).
#
# Dispatch inputs MUST be validated here, then passed to downstream commands
# only as environment variables or as argument-array entries (never interpolated
# into a shell/JSON/GitHub-CLI/AWS-CLI command string). Every helper takes its
# value as an argument (argv), returns non-zero on failure, and keeps all
# diagnostics on stderr. Security-sensitive values are validated as single
# values, never parsed with regex-based JSON tricks.

rl_die() {
  echo "ERROR: $*" >&2
  return 1
}

rl_assert_regular_file() {
  local path="${1:-}"
  [ -n "$path" ] || rl_die "expected a path argument"
  [ -f "$path" ] || rl_die "not a regular file: $path"
}

rl_assert_semver() {
  local version="${1:-}"
  if [[ ! "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    rl_die "invalid canonical SemVer (MAJOR.MINOR.PATCH, no leading zeroes, no prerelease/build metadata, no leading v): $version"
    return 1
  fi
  # Keep parity with the schema's semver maxLength (32) so dispatch inputs can
  # never exceed what a manifest can record.
  if (( ${#version} > 32 )); then
    rl_die "invalid canonical SemVer (too long): $version"
    return 1
  fi
  return 0
}

rl_assert_full_sha() {
  local sha="${1:-}"
  if [[ ! "$sha" =~ ^[0-9a-f]{40}$ ]]; then
    rl_die "invalid full commit SHA (expected exactly 40 lowercase hex characters): $sha"
  fi
}

rl_assert_sha256_hex() {
  local hex="${1:-}"
  if [[ ! "$hex" =~ ^[0-9a-f]{64}$ ]]; then
    rl_die "invalid SHA-256 hex digest (expected exactly 64 lowercase hex characters): $hex"
  fi
}

rl_assert_image_digest() {
  local digest="${1:-}"
  if [[ ! "$digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
    rl_die "invalid image digest (expected sha256:<64 lowercase hex>): $digest"
  fi
}

rl_assert_positive_integer() {
  local value="${1:-}"
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    rl_die "invalid positive integer: $value"
    return 1
  fi
  # Cap at 18 digits (fits signed 64-bit) so `[ ... -eq ... ]` never overflows
  # with an opaque "integer expression expected" error on absurdly long input.
  if (( ${#value} > 18 )); then
    rl_die "invalid positive integer (too large): $value"
    return 1
  fi
  if [ "$value" -eq 0 ]; then
    rl_die "invalid positive integer: $value"
    return 1
  fi
  return 0
}

rl_assert_github_login() {
  local login="${1:-}"
  # GitHub logins are 1-39 chars; `{0,37}` keeps first+last = 39 max, matching
  # the schema's githubLogin maxLength (39).
  if [[ ! "$login" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$ ]]; then
    rl_die "invalid GitHub login (alphanumeric, optional interior hyphens, max 39 chars): $login"
  fi
}

rl_assert_http_url() {
  local url="${1:-}"
  local pattern="^https?://[A-Za-z0-9._~:/?#@!\$&%'()*+,;=%-]+$"
  if [[ ! "$url" =~ $pattern ]]; then
    rl_die "invalid HTTP(S) URL: $url"
  fi
}
