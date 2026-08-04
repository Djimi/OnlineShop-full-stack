#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=config/staging.env
source "$SCRIPT_DIR/config/staging.env"
# shellcheck source=lib/lifecycle.sh
source "$SCRIPT_DIR/lib/lifecycle.sh"

lc_init
lc_require_environment staging
lc_verify_identity
lc_capture_diagnostics

