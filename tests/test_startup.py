"""Reproduce boot readiness failures without touching host networking or Docker."""
import contextlib
import http.client
import io
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import homelab as h
import install_systemd
import startup


class Clock:
    now = 0

    def sleep(self, seconds):
        self.now += seconds


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.c = {**h.config(example=True), 'REPO_ROOT': str(h.ROOT),
                  'RUNTIME_ROOT': str(self.root), 'DOMAIN': 'lab.test',
                  'LAN_IP': '192.168.1.236', 'LAN_INTERFACE': 'eno1',
                  'NETWORK_START_TIMEOUT': '3', 'STACK_START_TIMEOUT': '3'}
        self.clock = Clock()
        self.context = contextlib.ExitStack()
        self.context.enter_context(patch.object(startup.time, 'monotonic', side_effect=lambda: self.clock.now))
        self.context.enter_context(patch.object(startup.time, 'sleep', side_effect=self.clock.sleep))
        self.context.enter_context(contextlib.redirect_stdout(io.StringIO()))

    def tearDown(self):
        self.context.close()
        self.tmp.cleanup()

    def address_result(self, address='192.168.1.236', interface='eno1', state='UP'):
        return subprocess.CompletedProcess([], 0, json.dumps([
            {'ifname': interface, 'operstate': state, 'addr_info': [{'local': address}]}
        ]), '')

    def oidc_settings(self, enabled=True):
        issuer = 'https://auth.lab.test/application/o/files/'
        (self.root / 'files.yaml').write_text(json.dumps({'auth': {'methods': {'oidc': {
            'enabled': enabled, 'issuerUrl': issuer, 'clientSecret': 'test-only-do-not-log'
        }}}}))
        return issuer

    def test_delayed_lan_address_blocks_until_it_can_bind(self):
        missing = self.address_result('192.168.1.99')
        with patch.object(startup.subprocess, 'run', side_effect=[missing, self.address_result()]) as run, \
             patch.object(startup.socket, 'socket') as sock:
            startup.wait_for_lan(self.c)
        self.assertEqual(run.call_count, 2)
        sock.return_value.__enter__.return_value.bind.assert_called_once_with(('192.168.1.236', 0))
        self.assertEqual(self.clock.now, 1)

    def test_wrong_interface_address_or_down_link_never_opens_bind_probe(self):
        for result in (self.address_result(interface='other0'), self.address_result('192.168.1.1'),
                       self.address_result(state='DOWN')):
            self.clock.now = 0
            with patch.object(startup.subprocess, 'run', return_value=result), \
                 patch.object(startup.socket, 'socket') as sock:
                with self.assertRaisesRegex(TimeoutError, 'Docker startup blocked'):
                    startup.wait_for_lan(self.c)
                sock.assert_not_called()

    def test_address_present_but_bind_fails_is_retried(self):
        with patch.object(startup.subprocess, 'run', return_value=self.address_result()), \
             patch.object(startup.socket, 'socket') as sock:
            sock.return_value.__enter__.return_value.bind.side_effect = [OSError('not yet available'), None]
            startup.wait_for_lan(self.c)
        self.assertEqual(sock.call_count, 2)

    def test_docker_prestart_never_waits_on_controller_lock_or_calls_docker(self):
        import fcntl
        with patch.object(sys, 'argv', ['homelab.py', 'network-ready']), \
             patch.object(h, 'config', return_value=self.c), patch.object(startup, 'wait_for_lan') as wait, \
             patch.object(h, 'compose') as compose, patch.object(fcntl, 'flock') as flock:
            h.main()
        wait.assert_called_once_with(self.c, dry=False)
        compose.assert_not_called()
        flock.assert_not_called()

    def test_storage_waits_through_unready_tls_and_oidc_before_start(self):
        self.c['STACK_START_TIMEOUT'] = '10'
        self.oidc_settings()
        events = []
        def probe(*args):
            events.append('probe')
            if events.count('probe') == 1:
                raise ssl.SSLCertVerificationError('not yet ready')
            return (False, 'HTTP 503') if events.count('probe') == 2 else (True, 'ready')
        def compose(c, stack, *args, **kwargs):
            events.append(args[0])
        with patch.object(startup, 'probe_oidc', side_effect=probe), patch.object(h, 'gates'), \
             patch.object(h, 'compose', side_effect=compose), patch.object(h, 'health'):
            h.deploy(self.c, 'storage')
        self.assertEqual(events, ['config', 'probe', 'probe', 'probe', 'up'])

    def test_unready_oidc_does_not_start_filebrowser(self):
        self.oidc_settings()
        with patch.object(startup, 'probe_oidc', return_value=(False, 'HTTP 302')), \
             patch.object(h, 'gates'), patch.object(h, 'compose') as compose:
            with self.assertRaisesRegex(TimeoutError, 'HTTP 302'):
                h.deploy(self.c, 'storage')
        self.assertEqual([call.args[2] for call in compose.call_args_list], ['config'])

    def test_password_only_filebrowser_and_dry_runs_do_not_probe(self):
        with patch.object(startup, 'probe_oidc') as probe, patch.object(startup.subprocess, 'run') as run:
            self.oidc_settings(enabled=False)
            startup.wait_for_files_oidc(self.c)
            self.oidc_settings()
            startup.wait_for_files_oidc(self.c, dry=True)
            startup.wait_for_lan(self.c, dry=True)
        probe.assert_not_called()
        run.assert_not_called()

    def test_oidc_probe_uses_lan_origin_verified_tls_and_exact_issuer(self):
        issuer = self.oidc_settings()
        for status, document, expected in ((302, {}, False), (200, {'issuer': 'https://other/'}, False),
                                           (200, {'issuer': issuer}, True)):
            context = Mock()
            with patch.object(startup.ssl, 'create_default_context', return_value=context), \
                 patch.object(startup.socket, 'create_connection') as connect, \
                 patch.object(startup.http.client, 'HTTPSConnection') as client:
                context.wrap_socket.return_value = connect.return_value
                response = client.return_value.getresponse.return_value
                response.status = status
                response.read.return_value = json.dumps(document).encode()
                ready, _ = startup.probe_oidc('auth.lab.test', '192.168.1.236', '/discovery', issuer, 5)
            self.assertEqual(ready, expected)
            connect.assert_called_once_with(('192.168.1.236', 443), timeout=5)
            self.assertEqual(context.wrap_socket.call_args.kwargs['server_hostname'], 'auth.lab.test')
            client.return_value.request.assert_called_once_with('GET', '/discovery', headers={'Accept': 'application/json'})
            client.return_value.close.assert_called_once()

    def test_unmanaged_docker_dropin_blocks_install_without_overwriting_units(self):
        target = self.root / 'units'
        dropin = target / 'docker.service.d/20-homelab-network.conf'
        dropin.parent.mkdir(parents=True)
        dropin.write_text('# Operator configuration\n')
        with patch.object(h, 'run', return_value='mock.mount'), patch.object(h, 'atomic') as atomic:
            with self.assertRaisesRegex(h.SafetyError, 'unmanaged Docker drop-in'):
                install_systemd.install_units(self.c, target=target)
        atomic.assert_not_called()
        self.assertEqual(dropin.read_text(), '# Operator configuration\n')

    def test_installing_units_never_restarts_running_docker(self):
        target = self.root / 'units'
        with patch.object(h, 'root_only'), patch.object(h, 'run', return_value='mock.mount') as run, \
             patch.dict(os.environ, {'HOMELAB_ENV_FILE': '/etc/homelab/host.env'}):
            install_systemd.install_units(self.c, target=target)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual([cmd for cmd in commands if cmd[0] == 'systemctl'], [['systemctl', 'daemon-reload']])
        dropin = target / 'docker.service.d/20-homelab-network.conf'
        self.assertIn('HOMELAB_ENV_FILE=/etc/homelab/host.env', dropin.read_text())
        self.assertNotIn('@', dropin.read_text())
        self.assertEqual(dropin.stat().st_mode & 0o777, 0o644)

    def test_invalid_lan_configuration_never_installs_docker_guard(self):
        for override in ({'LAN_IP': '0.0.0.0'}, {'LAN_INTERFACE': 'SET_ME'},
                         {'NETWORK_START_TIMEOUT': '0'}):
            with self.subTest(override=override), patch.object(h, 'atomic') as atomic, \
                 patch.object(h, 'run') as run:
                with self.assertRaises(ValueError):
                    install_systemd.install_units({**self.c, **override}, target=self.root / 'units')
            atomic.assert_not_called()
            run.assert_not_called()
