#!/usr/bin/env python3
"""Opt-in source replacement test using the real deploy path and disposable Docker binds.

Run explicitly: python3 tests/runtime_bind_refresh.py
Only host gates and project selection are adapted to temporary, non-storage fixtures.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import homelab as h


def main():
    name = 'homelab-bind-test-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=name) as temporary:
        base = Path(temporary).resolve()
        source = base / 'source'; source.mkdir(); (source / 'version').write_text('old')
        env = dict(os.environ, **h.config(example=True))
        original = json.loads(subprocess.check_output(['docker', 'compose', '--env-file', str(ROOT / '.env.example'),
            '-f', str(ROOT / 'stacks/storage/compose.yaml'), 'config', '--format', 'json'], env=env, text=True))
        model = {'name': name, 'services': {'reader': {'image': original['services']['data-guard']['image'],
            'command': ['python', '-c', 'import time; time.sleep(300)'], 'network_mode': 'none',
            'read_only': True, 'cap_drop': ['ALL'], 'mem_limit': '32m',
            'volumes': [{'type': 'bind', 'source': str(source), 'target': '/source', 'read_only': True,
                         'bind': {'create_host_path': False}}]}}}
        path = base / 'compose.json'; path.write_text(json.dumps(model))
        command = ['docker', 'compose', '-f', str(path)]

        def compose(c, stack, *args, dry=False, capture=False, **kwargs):
            return h.run(command + list(args), dry=dry, capture=capture)

        def version():
            return subprocess.check_output(command + ['exec', '-T', 'reader', 'python', '-c',
                'from pathlib import Path; print(Path("/source/version").read_text())'], text=True).strip()

        try:
            with patch.object(h, 'gates'), patch.object(h, 'compose', side_effect=compose):
                h.deploy({'STACK_START_TIMEOUT': '30'}, 'management')
                assert version() == 'old'
                source.rename(base / 'previous')
                source.mkdir(); (source / 'version').write_text('new')
                assert version() == 'old', 'Expected the running directory bind to retain its old inode'
                h.deploy({'STACK_START_TIMEOUT': '30'}, 'management')
                assert version() == 'new'
                assert (base / 'previous/version').read_text() == 'old'
            print('PASS: unchanged Compose configuration is recreated by deploy and sees replaced source; old source retained.')
        finally:
            subprocess.run(command + ['down'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
