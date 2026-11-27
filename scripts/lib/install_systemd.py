"""Install repository-owned units and the Docker LAN-readiness drop-in."""
import argparse
from pathlib import Path
import homelab as h
from startup import lan_settings


def install_units(c, *, target=Path('/etc/systemd/system'), dry=False):
    h.require(c['REPO_ROOT'] == str(h.ROOT), 'REPO_ROOT must match checkout')
    _, _, network_wait = lan_settings(c)
    def mount_unit(path):
        return (h.run(['systemd-escape', '--path', '--suffix=mount', path], capture=True).strip()
                if not dry else '<escaped-mount>')
    values = {**c, 'MEDIA_MOUNT': mount_unit(c['MEDIA_ROOT']),
              'DATA_MOUNT': mount_unit(c['DATA_ROOT']), 'HOST_ENV_FILE': str(h.env_file()),
              'STACK_UNIT_TIMEOUT': str(3 * int(h.start_timeout(c)) + 60),
              'DOCKER_UNIT_TIMEOUT': str(network_wait + 300)}
    sources = sorted((h.ROOT / 'config/systemd').glob('homelab-*'))
    sources.append(h.ROOT / 'config/systemd/docker.service.d/20-homelab-network.conf')
    plan = {}
    for src in sources:
        relative = src.relative_to(h.ROOT / 'config/systemd')
        destination = target / relative
        h.require(not destination.is_symlink() and destination.parent.resolve() == destination.parent,
                  f'Unexpected systemd destination symlink: {destination}')
        content = src.read_text()
        if relative.suffix == '.conf' and destination.exists():
            h.require(destination.read_text().startswith('# Managed by HomeLab:'),
                      f'Existing unmanaged Docker drop-in; review without overwriting: {destination}')
        for key, value in values.items():
            content = content.replace('@' + key + '@', value)
        if '[Service]' in content and relative.suffix != '.conf':
            content = content.replace('[Service]', '[Service]\nEnvironment=PYTHONDONTWRITEBYTECODE=1\n'
                                      'Environment=HOMELAB_ENV_FILE=' + str(h.env_file()))
        plan[destination] = content
    if not dry:
        h.root_only()
    for destination, content in plan.items():
        if dry:
            print('DRY RUN: install', destination)
        else:
            destination.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
            h.atomic(destination, content, 0o644)
    if not dry:
        h.run(['systemctl', 'daemon-reload'])
    print('Units and Docker LAN-readiness drop-in installed; no services enabled or restarted.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    install_units(h.config(), dry=args.dry_run)


if __name__ == '__main__':
    main()
