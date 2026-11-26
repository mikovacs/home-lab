#!/usr/bin/env python3
"""Opt-in real-image regression for credential setup before PostgreSQL exists.

Run explicitly: python3 tests/runtime_authentik_hash.py
Uses a unique Compose project, no network, no mounts and disposable passwords.
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
import installer as ui


def main():
    name = 'homelab-hash-test-' + uuid.uuid4().hex[:12]
    values = h.config(example=True)
    env = dict(os.environ, **values)
    original = json.loads(subprocess.check_output([
        'docker', 'compose', '--env-file', str(ROOT / '.env.example'),
        '-f', str(ROOT / 'stacks/identity/compose.yaml'), 'config', '--format', 'json'],
        env=env, text=True))
    with tempfile.TemporaryDirectory(prefix=name) as temporary:
        base = Path(temporary).resolve()
        stack = base / 'stacks/identity'; stack.mkdir(parents=True)
        (base / '.env.example').write_text((ROOT / '.env.example').read_text())
        model = {'name': name, 'services': {'server': {
            'image': original['services']['server']['image'],
            'network_mode': 'none', 'read_only': True, 'cap_drop': ['ALL'],
            'security_opt': ['no-new-privileges:true'], 'mem_limit': '512m',
            'environment': {'AUTHENTIK_POSTGRESQL__HOST': 'absent-postgres.invalid'},
        }}}
        path = stack / 'compose.yaml'; path.write_text(json.dumps(model))
        command = ['docker', 'compose', '-f', str(path)]
        password = 'disposable-password-for-hash-test'
        original_run = h.run

        def bounded_run(*args, **kwargs):
            return original_run(*args, timeout=60, **kwargs)

        try:
            # Only source/env paths point at fixtures; execute the real helper and
            # real Compose command with the production image and its entrypoint.
            with patch.object(h, 'ROOT', base), patch.object(h, 'run', side_effect=bounded_run), patch.dict(os.environ, {
                    'HOMELAB_ENV_FILE': str(base / '.env.example')}):
                hashed = ui.hash_authentik_password(values, password)
                second = ui.hash_authentik_password(values, password)
            assert hashed != second, 'Each password hash must use a fresh salt'
            script = ('import os,json,sys; '
                      'os.environ["DJANGO_SETTINGS_MODULE"]="authentik.root.settings"; '
                      'from django.contrib.auth.hashers import check_password; '
                      'p,h=json.load(sys.stdin); '
                      'assert check_password(p,h); assert not check_password(p+"wrong",h)')
            subprocess.run(command + ['run', '--rm', '--no-deps', '-T',
                '--entrypoint', 'python', 'server', '-c', script],
                input=json.dumps([password, hashed]), text=True, check=True,
                stdout=subprocess.DEVNULL, timeout=60)
            print('PASS: real Authentik hash generation and verification without PostgreSQL, network or host data.')
        finally:
            subprocess.run(command + ['down', '--remove-orphans'], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
