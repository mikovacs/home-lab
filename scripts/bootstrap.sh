#!/usr/bin/env bash
set -euo pipefail
if [[ ${1:-} == --dry-run ]]; then
  echo 'DRY RUN: Debian 13 prerequisites, official Docker apt repository, unattended security updates, timers; no disk or SSH changes.'
  exit 0
fi
[[ $# == 0 ]] || {
  echo 'Usage: bootstrap.sh [--dry-run]' >&2
  exit 2
}
[[ $EUID == 0 ]] || {
  echo 'Run on Debian using sudo.' >&2
  exit 1
}
# shellcheck disable=SC1091
source /etc/os-release
[[ $ID == debian && ${VERSION_CODENAME:-} == trixie ]] || {
  echo 'This bootstrap targets Debian 13 trixie only.' >&2
  exit 1
}
apt-get update
apt-get install -y ca-certificates curl gnupg python3 git make apache2-utils acl jq smartmontools lm-sensors pciutils util-linux iproute2 iptables unattended-upgrades apt-listchanges restic logrotate systemd-timesyncd
install -m 0755 -d /etc/apt/keyrings
if [[ ! -f /etc/apt/keyrings/docker.asc ]]; then
  curl --fail --silent --show-error --location https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
  chmod 0644 /etc/apt/keyrings/docker.asc
fi
if [[ ! -f /etc/apt/sources.list.d/docker.sources ]]; then
  cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/debian
Suites: trixie
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
fi
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
cat >/etc/apt/apt.conf.d/52homelab-security <<'EOF'
Unattended-Upgrade::Origins-Pattern { "origin=Debian,codename=${distro_codename}-security,label=Debian-Security"; };
Unattended-Upgrade::Automatic-Reboot "false";
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
systemctl enable --now docker systemd-timesyncd
systemctl enable apt-daily.timer apt-daily-upgrade.timer
printf '%s\n' 'Host packages installed. Follow docs/installation.md; no storage, SSH or firewall settings changed.'
