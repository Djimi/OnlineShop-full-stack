#!/usr/bin/env bash
# Trusted AL2023 host setup only; no candidate code, secrets or app startup.
# Docker's service hooks retain forwarded IPv4/IPv6 IMDS rejection on restart.
set -euo pipefail

runtime=/opt/onlineshop-test
compose_version=v5.5.1
compose_sha256=db1889184726840f75c4f9c001048430d4f25b3be3cb084d3ddd762bc0aed576

block_container_metadata() {
  local binary address
  for binary in iptables ip6tables; do
    address=169.254.169.254/32
    [[ "$binary" == ip6tables ]] && address=fd00:ec2::254/128
    # Docker may not create an IPv6 user chain when IPv6 bridge routing is off.
    if ! "$binary" -w 10 -S DOCKER-USER >/dev/null 2>&1; then
      "$binary" -w 10 -N DOCKER-USER
    fi
    if ! "$binary" -w 10 -C DOCKER-USER -d "$address" -j REJECT >/dev/null 2>&1; then
      "$binary" -w 10 -I DOCKER-USER 1 -d "$address" -j REJECT
    fi
    if ! "$binary" -w 10 -C FORWARD -j DOCKER-USER >/dev/null 2>&1; then
      "$binary" -w 10 -I FORWARD 1 -j DOCKER-USER
    fi
  done
}

setup_host() {
  [[ $EUID -eq 0 && $(uname -m) == x86_64 ]] || {
    echo 'Host setup requires root on the verified x86_64 AL2023 host.' >&2
    exit 1
  }
  [[ -f /etc/os-release ]] && grep -q '^ID="\?amzn"\?$' /etc/os-release
  grep -q '^VERSION_ID="\?2023"\?$' /etc/os-release
  # Download only a pinned controller dependency; never run an install pipe.
  dnf install -y docker python3 curl iptables
  install -d -m 0700 "$runtime/.runtime"
  install -d -m 0755 /usr/local/lib/docker/cli-plugins
  local temporary
  temporary=$(mktemp "$runtime/.runtime/compose.XXXXXX")
  trap 'rm -f -- "$temporary"' RETURN
  curl --fail --silent --show-error --location --max-time 180 \
    "https://github.com/docker/compose/releases/download/$compose_version/docker-compose-linux-x86_64" \
    --output "$temporary"
  echo "$compose_sha256  $temporary" | sha256sum --check --status
  install -m 0755 "$temporary" /usr/local/lib/docker/cli-plugins/docker-compose
  install -m 0755 "$runtime/host-setup.sh" /usr/local/sbin/onlineshop-test-host-setup
  install -d -m 0755 /etc/systemd/system/docker.service.d
  cat > /etc/systemd/system/docker.service.d/onlineshop-metadata.conf <<'EOF'
[Service]
ExecStartPre=/usr/local/sbin/onlineshop-test-host-setup --firewall
ExecStartPost=/usr/local/sbin/onlineshop-test-host-setup --firewall
EOF
  systemctl daemon-reload
  systemctl enable --now docker
  block_container_metadata
  docker compose version --short
  aws --version
  systemctl is-active amazon-ssm-agent
  echo 'Host prerequisites installed; no application started. Live isolation proofs still required.'
}

case "${1:-}" in
  --firewall) block_container_metadata ;;
  --setup) setup_host ;;
  *) echo 'Usage: host-setup.sh --setup | --firewall' >&2; exit 2 ;;
esac
