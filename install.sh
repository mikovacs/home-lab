#!/usr/bin/env bash
set -euo pipefail

if [[ -z ${BASH_SOURCE[0]:-} ]]; then
  printf 'ERROR: Download install.sh and run it from a file, not a pipe.\n' >&2
  exit 1
fi
installer_file=${BASH_SOURCE[0]}
installer_dir=$(cd -- "$(dirname -- "$installer_file")" && pwd)
installer_file="$installer_dir/$(basename -- "$installer_file")"
workdir=''

usage() {
  cat <<'EOF'
HomeLab installer: Bash prerequisites, Python release manager and interactive wizard.

Usage: sudo bash install.sh [options]
  --repo OWNER/REPOSITORY  Download/resume verified stable GitHub release assets
  --private                Ask for a private-source token in the Python launcher
  --target PATH            Release source directory (default: /srv/homelab/repository)
  --state-dir PATH         Host-only configuration (default: /etc/homelab)
  --update                 Review a newer release; backup before switching source
  --dry-run                Show the entrypoint plan without changing the host
  -h, --help               Show this help without root, Python or network access

In a source checkout, no release options opens its wizard directly.
The published install.sh is self-contained and obtains a stable release.
OS installation and internal disk formatting are manual; external media is preserved.
EOF
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  return 1
}

check_platform() {
  [[ $(uname -s) == Linux && -r /etc/os-release ]] || {
    fail 'Run on Debian 13 trixie.'
    return 1
  }
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ ${ID:-} == debian && ${VERSION_CODENAME:-} == trixie ]] || {
    fail 'Only Debian 13 trixie is supported.'
    return 1
  }
}

ensure_runtime() {
  local package answer
  local -a missing=()
  for package in python3 ca-certificates; do
    if [[ $(dpkg-query -W -f='${db:Status-Status}' "$package" 2>/dev/null || true) != installed ]]; then
      missing+=("$package")
    fi
  done
  if ((${#missing[@]})); then
    printf 'Install missing Debian prerequisites: %s\n' "${missing[*]}"
    read -r -p 'Install these packages through apt? (yes/no) [no]: ' answer
    [[ $answer == yes ]] || {
      fail 'Prerequisite installation cancelled.'
      return 1
    }
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
  fi
  if ! command -v python3 >/dev/null; then
    fail 'Python 3 is required.'
    return 1
  fi
}

embedded_launcher() {
  sed -n '/^# HOMELAB_RELEASE_PYTHON_BEGIN$/,/^# HOMELAB_RELEASE_PYTHON_END$/ {
    /^# HOMELAB_RELEASE_PYTHON_/d
    s/^# //p
  }' "$installer_file"
}

cleanup() {
  if [[ -n $workdir && -d $workdir ]]; then
    # Only files created in our private mktemp directory are removed.
    rm -f -- "$workdir/releases.py"
    rmdir -- "$workdir" 2>/dev/null || true
  fi
}

main() {
  local dry_run=false release_mode=false payload
  local -a launcher_args=() wizard_args=()
  while (($#)); do
    case "$1" in
      -h | --help)
        usage
        return 0
        ;;
      --dry-run)
        dry_run=true
        shift
        ;;
      --private | --update)
        release_mode=true
        launcher_args+=("$1")
        shift
        ;;
      --repo | --target | --state-dir)
        [[ $# -ge 2 && -n $2 && $2 != -* ]] || { fail "Missing value for $1" || return 2; }
        launcher_args+=("$1" "$2")
        if [[ $1 == --state-dir ]]; then
          wizard_args+=("$1" "$2")
        else
          release_mode=true
        fi
        shift 2
        ;;
      *) fail "Unknown option: $1" || return 2 ;;
    esac
  done
  if $dry_run; then
    printf '%s\n' \
      'DRY RUN: verify Debian 13, root and an interactive terminal.' \
      'DRY RUN: install only missing python3 and ca-certificates after confirmation.' \
      'DRY RUN: start the Python wizard/release manager with the selected options.' \
      'Docker and application setup remain guided steps; no disks or applications changed.'
    return 0
  fi
  payload=$(embedded_launcher)
  [[ -n $payload || -f $installer_dir/scripts/lib/releases.py ]] || {
    fail 'This source script needs its checkout. Download the self-contained install.sh from a trusted release.'
    return 1
  }
  check_platform
  [[ $EUID == 0 ]] || {
    fail 'Run with sudo bash install.sh.'
    return 1
  }
  [[ -t 0 && -t 1 ]] || {
    fail 'Use an interactive terminal; download the script instead of piping it into Bash.'
    return 1
  }
  ensure_runtime
  export PYTHONDONTWRITEBYTECODE=1
  if [[ -n $payload ]]; then
    workdir=$(mktemp -d "${TMPDIR:-/tmp}/homelab-install.XXXXXXXX")
    trap cleanup EXIT
    printf '%s\n' "$payload" | base64 --decode >"$workdir/releases.py"
    chmod 0600 "$workdir/releases.py"
    python3 "$workdir/releases.py" "${launcher_args[@]}"
  elif [[ -f $installer_dir/scripts/lib/releases.py ]]; then
    if $release_mode; then
      exec python3 "$installer_dir/scripts/lib/releases.py" "${launcher_args[@]}"
    else
      exec python3 "$installer_dir/scripts/lib/installer.py" "${wizard_args[@]}"
    fi
  else
    fail 'This source script needs its checkout. Download the self-contained install.sh from a trusted GitHub release.'
  fi
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then
  main "$@"
fi
