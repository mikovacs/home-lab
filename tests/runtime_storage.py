#!/usr/bin/env python3
"""Opt-in Docker negative storage test; no real devices, media or host Docker socket.

Run explicitly: python3 tests/runtime_storage.py
A regular file stands in for an invalid UUID device. The real guard must fail,
and Compose must never start the stub consumer or create a missing bind source.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import homelab as h


def main():
    name = 'homelab-storage-test-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=name) as temporary:
        base = Path(temporary).resolve()
        data = base / 'data'; data.mkdir()
        device = base / 'not-a-device'; device.write_text('invalid UUID device fixture')
        env = dict(os.environ, **h.config(example=True))
        source = json.loads(subprocess.check_output(['docker', 'compose', '--env-file', str(ROOT / '.env.example'),
            '-f', str(ROOT / 'stacks/storage/compose.yaml'), 'config', '--format', 'json'], env=env, text=True))
        guard = source['services']['data-guard']
        replacements = {'/guard.py': ROOT / 'scripts/lib/mount_guard.py', '/data': data, '/expected-device': device}
        for volume in guard['volumes']:
            volume['source'] = str(replacements[volume['target']])
        consumer = {'image': guard['image'], 'command': ['python', '-c', 'raise RuntimeError("consumer must never start")'],
                    'network_mode': 'none', 'restart': 'no',
                    'depends_on': {'data-guard': {'condition': 'service_completed_successfully'}},
                    'volumes': [{'type': 'bind', 'source': str(data), 'target': '/data', 'read_only': True,
                                 'bind': {'create_host_path': False}}]}
        path = base / 'compose.json'
        path.write_text(json.dumps({'name': name, 'services': {'data-guard': guard, 'consumer': consumer}}))
        command = ['docker', 'compose', '-f', str(path)]
        try:
            for missing in (False, True):
                if missing: data.rmdir()
                result = subprocess.run(command + ['up', '-d', '--wait', '--wait-timeout', '30'],
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
                assert result.returncode != 0, result.stdout
                raw = subprocess.check_output(command + ['ps', '--all', '--format', 'json'], text=True)
                rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
                assert all(row.get('State') == 'created' for row in rows if row['Service'] == 'consumer'), rows
                if missing: assert not data.exists(), 'Docker created a missing storage bind'
                else:
                    logs = subprocess.check_output(command + ['logs', '--no-color', 'data-guard'], text=True)
                    assert 'does not match expected UUID device' in logs, logs
                subprocess.run(command + ['down'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print('PASS: real guard rejects non-device; missing binds stay absent; stub consumers never start.')
        finally:
            subprocess.run(command + ['down'], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
