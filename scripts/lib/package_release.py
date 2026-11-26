#!/usr/bin/env python3
"""Bundle the release manager into install.sh; no separate bootstrap launcher download."""
import argparse
import base64
from pathlib import Path
import textwrap

ROOT = Path(__file__).resolve().parents[2]


def package_installer(destination):
    source = ROOT / 'install.sh'
    launcher = ROOT / 'scripts/lib/releases.py'
    destination = Path(destination)
    if destination.resolve() == source.resolve() or destination.is_symlink():
        raise ValueError('Release output must not overwrite or symlink the source installer')
    if '\n# HOMELAB_RELEASE_PYTHON_BEGIN\n' in source.read_text():
        raise ValueError('Source installer already contains a generated payload')
    encoded = base64.b64encode(launcher.read_bytes()).decode('ascii')
    payload = '\n'.join('# ' + line for line in textwrap.wrap(encoded, 76))
    content = (source.read_text().rstrip() + '\n\n# HOMELAB_RELEASE_PYTHON_BEGIN\n'
               + payload + '\n# HOMELAB_RELEASE_PYTHON_END\n')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content)
    destination.chmod(0o755)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='release asset directory')
    args = parser.parse_args()
    print(package_installer(args.output / 'install.sh'))


if __name__ == '__main__':
    main()
