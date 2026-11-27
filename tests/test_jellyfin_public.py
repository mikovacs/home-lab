import contextlib
import copy
import io
import json
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import homelab as h
import jellyfin_public as j


class PublicJellyfinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.c = h.config(example=True)
        self.c.update(DOMAIN='lab.test', LAN_IP='192.168.1.236', LAN_INTERFACE='eno1',
                      RUNTIME_ROOT=str(self.base / 'runtime'),
                      HOMELAB_CONFIG_DIR=str(self.base / 'settings'))
        (self.base / 'settings').mkdir()
        (self.base / 'runtime').mkdir()
        self.zone, self.ident = 'a' * 32, 'b' * 32
        self.row = {'id': self.ident, 'name': 'jellyfin.lab.test', 'type': 'A',
                    'content': '192.168.1.236', 'proxied': False, 'ttl': 300}
        self.api = Mock()
        self.api.request.return_value = [self.row]

    def tearDown(self):
        self.tmp.cleanup()

    def bind(self):
        j.initialize(self.c, self.api, self.zone, '192.168.1.236')
        self.api.reset_mock()

    def test_render_opt_in_keeps_all_other_routers_off_public_listener(self):
        for enabled in ('no', 'yes'):
            self.c['JELLYFIN_PUBLIC_ENABLED'] = enabled
            writes = {}
            with patch.object(h, 'atomic', side_effect=lambda p, s, mode: writes.update({p.name: s})), \
                 patch.object(h, 'render_dockge_stacks'):
                h.render(self.c)
            dynamic = json.loads(writes['dynamic.yml'])
            public = {n: r for n, r in dynamic['http']['routers'].items()
                      if 'jellyfin-public' in r['entryPoints']}
            self.assertEqual(set(public), {'jellyfin-public'} if enabled == 'yes' else set())
            if enabled == 'yes':
                self.assertEqual(public['jellyfin-public']['rule'], 'Host(`jellyfin.lab.test`)')
                self.assertEqual(dynamic['http']['services']['jellyfin-public']['loadBalancer']['servers'],
                                 [{'url': 'http://jellyfin:8096'}])
            self.assertNotIn('jellyfin', writes['cloudflared.yml'])

    def test_firewall_only_opt_in_original_and_translated_9443(self):
        for enabled in ('no', 'yes'):
            self.c['JELLYFIN_PUBLIC_ENABLED'] = enabled
            with patch.object(h, 'config', return_value=self.c), patch.object(h, 'run') as run, \
                 patch.object(sys, 'argv', ['firewall', '--dry-run']), contextlib.redirect_stdout(io.StringIO()):
                runpy.run_path(str(h.ROOT / 'scripts/lib/firewall.py'))
            public = [x.args[0] for x in run.call_args_list if '--ctorigdstport' in x.args[0]]
            self.assertEqual(len(public), 1 if enabled == 'yes' else 0)
            if public:
                args = public[0]
                self.assertEqual(args[0], 'iptables')
                for option, value in (('--ctorigdstport', '9443'), ('--dport', '9443'),
                                      ('--ctorigdst', self.c['LAN_IP']), ('-p', 'tcp')):
                    self.assertEqual(args[args.index(option) + 1], value)
                self.assertNotIn('-s', args)

    def test_init_never_mutates_dns_and_rejects_conflicts(self):
        for changes in ({'type': 'CNAME'}, {'proxied': True}, {'name': 'docker.lab.test'},
                        {'content': '8.8.8.8'}, {'id': '../../records'}):
            self.api.request.return_value = [{**self.row, **changes}]
            with self.assertRaises((h.SafetyError, j.APIError)):
                j.initialize(self.c, self.api, self.zone, '192.168.1.236')
            self.assertFalse(j.state_path(self.c).exists())
        self.api.request.return_value = [self.row]
        self.bind()
        self.assertEqual(j.state_path(self.c).stat().st_mode & 0o777, 0o600)
        self.assertEqual(json.loads(j.state_path(self.c).read_text())['initial_record'], self.row)
        with self.assertRaises(h.SafetyError): self.bind()

    def test_update_preflight_review_write_and_noop(self):
        self.bind()
        current = copy.deepcopy(self.row)
        def request(method, path, data=None):
            if method == 'GET': return [copy.deepcopy(current)]
            self.assertEqual(method, 'PATCH')
            self.assertEqual(path, f'/zones/{self.zone}/dns_records/{self.ident}')
            self.assertEqual(data, {'content': '81.182.191.205', 'proxied': False, 'ttl': 300})
            current.update(data)
            return copy.deepcopy(current)
        self.api.request.side_effect = request
        with patch.object(j, 'discover_ip', return_value='81.182.191.205'), patch.object(j, 'readiness') as ready:
            j.update(self.c, self.api)
            self.assertEqual(current['content'], '192.168.1.236')
            self.assertEqual(json.loads(j.state_path(self.c).read_text())['last_ip'], '192.168.1.236')
            j.update(self.c, self.api, apply=True)
            self.assertEqual(json.loads(j.state_path(self.c).read_text())['last_ip'], current['content'])
            j.update(self.c, self.api, apply=True)
            self.assertEqual(ready.call_count, 3)
        self.assertEqual(sum(call.args[0] == 'PATCH' for call in self.api.request.call_args_list), 1)

    def test_no_write_on_conflicting_record_or_failed_readiness(self):
        self.bind()
        for changes in ({'id': 'c' * 32}, {'content': '9.9.9.9'}, {'proxied': True}):
            self.api.request.return_value = [{**self.row, **changes}]
            with patch.object(j, 'discover_ip', return_value='81.182.191.205'), \
                 patch.object(j, 'readiness'), self.assertRaises(h.SafetyError):
                j.update(self.c, self.api, apply=True)
        self.api.request.return_value = [self.row]
        with patch.object(j, 'discover_ip', return_value='81.182.191.205'), \
             patch.object(j, 'readiness', side_effect=h.SafetyError('not ready')), self.assertRaises(h.SafetyError):
            j.update(self.c, self.api, apply=True)
        self.assertTrue(all(call.args[0] == 'GET' for call in self.api.request.call_args_list))

    def test_discovery_rejects_private_ipv6_and_disagreement(self):
        for value in ('192.168.1.236', '100.64.1.1', '127.0.0.1', '224.0.0.1', '2001:4860::1', 'bad'):
            with self.assertRaises(h.SafetyError): j.public_ipv4(value)
        with patch.object(j, 'curl', side_effect=['ip=81.182.191.205\n', '8.8.8.8']), self.assertRaises(h.SafetyError):
            j.discover_ip(self.c)

    def test_readiness_refuses_incomplete_wizard_unprotected_api_and_other_hosts(self):
        for outputs in ([(500, '')], [(200, ''), (200, '{"StartupWizardCompleted": false}')],
                        [(200, ''), (200, '{"StartupWizardCompleted": true}'), (200, '')],
                        [(200, ''), (200, '{"StartupWizardCompleted": true}'), (401, ''), (200, '')]):
            with patch.object(j, 'probe', side_effect=outputs), self.assertRaises(h.SafetyError):
                j.readiness(self.c)


if __name__ == '__main__':
    unittest.main()
