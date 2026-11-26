#!/usr/bin/env python3
"""Opt-in real LinuxServer startup test, including the broken Docker-init case.

Run: python3 tests/runtime_media_init.py
Uses native images, unique containers, tmpfs state, no network or host binds.
This checks application HTTP startup, not physical storage or Plex playback.
"""
import copy
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

ENDPOINTS = {'sonarr': (8989, '/'), 'radarr': (7878, '/'),
             'prowlarr': (9696, '/'), 'qbittorrent': (8080, '/'),
             'plex': (32400, '/identity'), 'bazarr': (6767, '/')}


def main():
    name = 'homelab-media-init-test-' + uuid.uuid4().hex[:12]
    platform = subprocess.check_output([
        'docker', 'version', '--format', '{{.Server.Os}}/{{.Server.Arch}}'], text=True).strip()
    env = dict(os.environ, **h.config(example=True))
    source = json.loads(subprocess.check_output([
        'docker', 'compose', '--env-file', str(ROOT / '.env.example'),
        '-f', str(ROOT / 'stacks/media/compose.yaml'), '--profile', '*',
        'config', '--format', 'json'], env=env, text=True))
    services = {}
    for key in ENDPOINTS:
        svc = copy.deepcopy(source['services'][key])
        for field in ('volumes', 'ports', 'networks', 'depends_on', 'profiles', 'labels'):
            svc.pop(field, None)
        svc.update(platform=platform, network_mode='none',
                   tmpfs=['/config:rw,mode=0755', '/data:rw,mode=0755'])
        # Match production read-only Plex media without mounting any host data.
        if key == 'plex': svc['tmpfs'][1] = '/data:ro,mode=0755'
        services[key] = svc
    services['broken-sonarr'] = copy.deepcopy(services['sonarr'])
    services['broken-sonarr']['init'] = True
    with tempfile.TemporaryDirectory(prefix=name) as temporary:
        path = Path(temporary) / 'compose.json'
        path.write_text(json.dumps({'name': name, 'services': services}))
        command = ['docker', 'compose', '-f', str(path)]

        def compose(*args, check=True, timeout=60):
            return subprocess.run(command + list(args), check=check, timeout=timeout,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def state(service):
            container = compose('ps', '--all', '-q', service).stdout.strip()
            return json.loads(subprocess.check_output(['docker', 'inspect', container], text=True))[0]

        try:
            print('Reproducing the original Sonarr PID 1 failure...', flush=True)
            compose('up', '-d', 'broken-sonarr', timeout=300)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                broken = state('broken-sonarr')['State']
                if not broken['Running']: break
                time.sleep(1)
            assert broken['Status'] == 'exited' and broken['ExitCode'] == 100, broken
            logs = compose('logs', '--no-color', 'broken-sonarr').stdout
            assert 'can only run as pid 1' in logs
            print('PASS: init=true reproduces s6 exit 100.', flush=True)
            compose('rm', '-f', '-v', 'broken-sonarr')

            for service, (port, endpoint) in ENDPOINTS.items():
                print('Starting isolated ' + service + '...', flush=True)
                compose('up', '-d', service, timeout=600)
                deadline = time.monotonic() + 180
                while time.monotonic() < deadline:
                    current = state(service)
                    assert current['State']['Running'], service + ' stopped before HTTP readiness'
                    assert current['HostConfig']['Init'] is False
                    assert current['HostConfig']['NetworkMode'] == 'none'
                    assert not any(m['Type'] == 'bind' for m in current['Mounts'])
                    probe = compose('exec', '-T', service, 'curl', '--silent', '--max-time', '3',
                                    '--output', '/dev/null', '--write-out', '%{http_code}',
                                    f'http://127.0.0.1:{port}{endpoint}', check=False)
                    if probe.returncode == 0 and probe.stdout in ('200', '301', '302', '303', '307', '308', '401', '403'):
                        break
                    time.sleep(2)
                else:
                    raise AssertionError(service + ' HTTP startup timed out')
                pid1 = compose('exec', '-T', service, 'cat', '/proc/1/comm').stdout.strip()
                assert pid1 == 's6-svscan', (service, pid1)
                compose('stop', '-t', '30', service)
                stopped = state(service)['State']
                assert not stopped['OOMKilled'] and stopped['ExitCode'] == 0, (service, stopped)
                compose('rm', '-f', '-v', service)
                print(f'PASS: {service} HTTP {probe.stdout}, s6 PID 1, clean stop.', flush=True)
        finally:
            compose('down', '--remove-orphans', '--volumes', timeout=90)


if __name__ == '__main__':
    main()
