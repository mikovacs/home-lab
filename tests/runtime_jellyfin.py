#!/usr/bin/env python3
"""Opt-in Jellyfin startup check with disposable state and no host media.

Run: python3 tests/runtime_jellyfin.py
Uses the native Linux image, no network, no published ports and tmpfs mounts.
This verifies startup/read-only media, not TV codecs or real library playback.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import homelab as h


def main():
    env = dict(os.environ, **h.config(example=True))
    source = json.loads(subprocess.check_output([
        'docker', 'compose', '--env-file', str(ROOT / '.env.example'),
        '-f', str(ROOT / 'stacks/media/compose.yaml'),
        'config', '--format', 'json'], env=env, text=True))
    service = source['services']['jellyfin']
    for field in ('volumes', 'ports', 'networks', 'depends_on', 'profiles', 'labels'):
        service.pop(field, None)
    # Jellyfin checks 2 GiB of free config space at startup. Tmpfs reserves no
    # memory up front; actual allocations remain limited by the container cap.
    service.update(network_mode='none', tmpfs=[
        '/config:rw,uid=1000,gid=1000,mode=0750,size=4g',
        '/cache:rw,uid=1000,gid=1000,mode=0750,size=4g',
        '/data:ro,mode=0777,size=1m'])
    service['user'] = '1000:1000'
    name = 'homelab-jellyfin-test-' + uuid.uuid4().hex[:12]
    with tempfile.TemporaryDirectory(prefix=name) as temporary:
        path = Path(temporary) / 'compose.json'
        path.write_text(json.dumps({'name': name, 'services': {'jellyfin': service}}))
        command = ['docker', 'compose', '-f', str(path)]

        def compose(*args, check=True, timeout=60):
            return subprocess.run(command + list(args), check=check, timeout=timeout,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        try:
            print('Starting isolated Jellyfin with non-root user and dropped capabilities...', flush=True)
            compose('up', '-d', timeout=600)
            container = compose('ps', '--all', '-q', 'jellyfin').stdout.strip()
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                current = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]
                assert current['State']['Running'], 'Jellyfin exited during startup'
                assert not current['HostConfig']['PortBindings']
                assert current['HostConfig']['NetworkMode'] == 'none'
                assert not any(m['Type'] == 'bind' for m in current['Mounts'])
                if current['State']['Health']['Status'] == 'healthy':
                    break
                time.sleep(2)
            else:
                raise AssertionError('Jellyfin HTTP readiness timed out')
            write = compose('exec', '-T', 'jellyfin', 'touch', '/data/forbidden', check=False)
            assert write.returncode != 0 and 'Read-only file system' in write.stderr, write
            compose('stop', '-t', '30', 'jellyfin')
            stopped = json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]['State']
            assert not stopped['OOMKilled'] and stopped['ExitCode'] == 0, stopped
            print('PASS: Jellyfin healthy, media writes blocked, clean stop.', flush=True)
        except Exception:
            print(compose('logs', '--no-color', check=False).stdout, file=sys.stderr)
            raise
        finally:
            compose('down', '--volumes', timeout=90)


if __name__ == '__main__':
    main()
