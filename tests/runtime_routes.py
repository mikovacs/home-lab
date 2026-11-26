#!/usr/bin/env python3
"""Opt-in Traefik routing regression: temporary files, stub origins, no host socket/account.

Run explicitly: python3 tests/runtime_routes.py
Requires a local Docker daemon and permission to pull the pinned images.
This checks routing, not real authentication, certificates, disks or Cloudflare.
"""
import http.client
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import homelab as h


def docker(*args):
    return subprocess.check_output(['docker', *map(str, args)], text=True).strip()


def main():
    name = 'homelab-route-test-' + uuid.uuid4().hex[:12]
    created = []
    network = False
    with tempfile.TemporaryDirectory(prefix=name) as temporary:
        base = Path(temporary).resolve()
        c = h.config(example=True)
        c.update(RUNTIME_ROOT=str(base), SECRETS_ROOT=str(base / 'secrets'),
                 DOMAIN='audit.test', IDENTITY_READY='yes', REMOTE_READY='yes',
                 REMOTE_APPS='auth,home,hooks', PUID=str(os.getuid()), PGID=str(os.getgid()))
        for path in ('traefik', 'homepage', 'secrets'):
            (base / path).mkdir()
        h.render(c)
        env = dict(os.environ, **h.config(example=True))
        config = json.loads(subprocess.check_output(['docker', 'compose', '--env-file', str(ROOT / '.env.example'),
            '-f', str(ROOT / 'stacks/infrastructure/compose.yaml'), 'config', '--format', 'json'], env=env, text=True))
        image = config['services']['traefik']['image']
        guard = json.loads(subprocess.check_output(['docker', 'compose', '--env-file', str(ROOT / '.env.example'),
            '-f', str(ROOT / 'stacks/storage/compose.yaml'), 'config', '--format', 'json'], env=env, text=True))
        python_image = guard['services']['data-guard']['image']
        try:
            docker('network', 'create', name); network = True
            stub = ('from http.server import HTTPServer,BaseHTTPRequestHandler\n'
                    'from threading import Thread\n'
                    'class Handler(BaseHTTPRequestHandler):\n'
                    ' def do_GET(self): self.send_response(204); self.end_headers()\n'
                    ' def log_message(self,*args): pass\n'
                    'for port in (9000,5678,3000):\n'
                    ' Thread(target=HTTPServer(("0.0.0.0",port),Handler).serve_forever,daemon=True).start()\n'
                    'Thread().join()\n')
            # Keep the process alive without sending any external requests.
            stub = stub.replace('Thread().join()', 'from threading import Event; Event().wait()')
            docker('create', '--name', name + '-origin', '--network', name,
                   '--network-alias', 'server', '--network-alias', 'n8n', '--network-alias', 'homepage',
                   '--memory', '64m', '--read-only', '--cap-drop', 'ALL', python_image, 'python', '-c', stub)
            created.append(name + '-origin'); docker('start', created[-1])
            docker('create', '--name', name + '-proxy', '--network', name,
                   '--publish', '127.0.0.1::8443', '--memory', '256m', '--read-only', '--cap-drop', 'ALL',
                   '--mount', 'type=bind,src=' + str(base / 'traefik') + ',dst=/dynamic,readonly', image,
                   '--entrypoints.tunnel.address=:8443', '--providers.file.directory=/dynamic', '--providers.file.watch=true')
            created.append(name + '-proxy'); docker('start', created[-1])
            port = int(docker('port', created[-1], '8443/tcp').rsplit(':', 1)[1])
            # The test has no ACME account and intentionally uses Traefik's self-signed cert.
            context = ssl._create_unverified_context()

            def status(host, path='/', extra=None):
                connection = http.client.HTTPSConnection('127.0.0.1', port, context=context, timeout=5)
                try:
                    connection.request('GET', path, headers={'Host': host, **(extra or {})})
                    response = connection.getresponse(); response.read()
                    return response.status
                finally:
                    connection.close()

            def wait_status(host, wanted):
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        if status(host) == wanted: return
                    except (OSError, http.client.HTTPException):
                        pass
                    time.sleep(0.2)
                raise AssertionError((host, wanted, 'timeout'))

            wait_status('auth.audit.test', 204)
            for host in ('auth.audit.test', 'AUTH.audit.test', 'auth.audit.test.', 'auth.audit.test:443'):
                assert status(host) == 204, host
            for host in ('docker.audit.test', 'terminal.audit.test', 'traefik.audit.test', 'adguard.audit.test',
                         'monitor.audit.test', 'plex.audit.test', 'auth.audit.test.evil.test', 'evil.test'):
                for path in ('/', '/outpost.goauthentik.io/ping'):
                    assert status(host, path, {'X-Forwarded-Host': 'auth.audit.test', 'X-Forwarded-For': '127.0.0.1'}) == 404, (host, path)
            assert status('hooks.audit.test', '/webhook/probe') == 204
            for path in ('/rest/settings', '/webhook-test/probe', '/webhook/../rest/settings',
                         '/webhook/%2e%2e/rest/settings', '/outpost.goauthentik.io/ping'):
                assert status('hooks.audit.test', path) == 404, path
            assert status('home.audit.test', '/outpost.goauthentik.io/ping') == 204
            c.update(REMOTE_APPS='', REMOTE_READY='no'); h.render(c)
            wait_status('auth.audit.test', 404)
            assert status('hooks.audit.test', '/webhook/probe') == 404
            assert status('home.audit.test', '/outpost.goauthentik.io/ping') == 404
            print('PASS: real Traefik host/path boundaries and atomic remote disable; origins are stubs.')
        finally:
            for container in reversed(created):
                subprocess.run(['docker', 'rm', '-f', container], check=True, stdout=subprocess.DEVNULL)
            if network:
                subprocess.run(['docker', 'network', 'rm', name], check=True, stdout=subprocess.DEVNULL)


if __name__ == '__main__':
    main()
