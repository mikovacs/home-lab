"""Interactive/release/API behavior is tested with temp files and fake endpoints only."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import MagicMock, Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'scripts/lib'))
import releases as launcher
import homelab as h
import installer as ui
import installer_api as api

ACCOUNT = 'a' * 32
ZONE = 'b' * 32
TUNNEL = '12345678-1234-1234-1234-123456789abc'


def credentials():
    return {'AccountTag': ACCOUNT, 'TunnelID': TUNNEL,
            'TunnelSecret': base64.b64encode(bytes(range(32))).decode()}


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name).resolve()
        self.env = patch.dict(os.environ, {}, clear=False); self.env.start()
        self.output = contextlib.redirect_stdout(io.StringIO()); self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None); self.env.stop(); self.tmp.cleanup()

    def values(self):
        c = h.config(example=True)
        c['HOMELAB_CONFIG_DIR'] = str(self.base / 'host-config')
        for key in ('APPDATA_ROOT', 'RUNTIME_ROOT', 'SECRETS_ROOT', 'DATA_ROOT', 'MEDIA_ROOT'):
            c[key] = str(self.base / key.lower())
        c.update(REPO_ROOT=str(h.ROOT), DOMAIN='lab.test', LAN_IP='192.168.20.2')
        return c

    def test_host_config_saved_outside_source_with_private_mode(self):
        c = self.values(); path = self.base / 'host.env'
        before = (ROOT / 'stacks/infrastructure/compose.yaml').read_bytes()
        ui.save_environment(path, c)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(h.read_env(path)['DOMAIN'], 'lab.test')
        self.assertEqual(before, (ROOT / 'stacks/infrastructure/compose.yaml').read_bytes())

    def test_settings_custom_data_root_moves_only_default_backup_repository(self):
        directory=self.base/'host'; directory.mkdir()
        for repository in ('/mnt/homelab-data/backups/restic','s3:example.invalid/backup'):
            wizard=ui.Wizard(directory)
            values=h.read_env(ROOT/'.env.example'); values['RESTIC_REPOSITORY']=repository
            def prompt(label,default='',validate=None):
                overrides={'Service domain':'lab.test','Reserved server':'192.168.20.2',
                           'LAN interface':'eth0','Actual LAN':'192.168.20.0/24',
                           'Certificate contact':'admin@lab.test','DATA_UUID':'data-123',
                           'MEDIA_UUID':'media-456','Existing non-root':'audit-admin'}
                for prefix,value in overrides.items():
                    if label.startswith(prefix): return value
                if label in ('APPDATA_ROOT','SECRETS_ROOT','RUNTIME_ROOT','DATA_ROOT','MEDIA_ROOT'):
                    return str(self.base/label.lower())
                return default
            with patch.object(h,'read_env',return_value=values.copy()),patch.object(ui,'prompt',side_effect=prompt), \
                 patch.object(h.shutil,'which',return_value=None),patch('pwd.getpwnam',return_value=Mock(pw_uid=1000)):
                wizard.settings()
            saved=h.read_env(wizard.environment)
            expected=str(self.base/'data_root/backups/restic') if repository.startswith('/') else repository
            self.assertEqual(saved['RESTIC_REPOSITORY'],expected)

    def test_controller_uses_external_environment_for_compose(self):
        path = self.base / 'host.env'; path.write_text('DOMAIN=lab.test\n')
        os.environ['HOMELAB_ENV_FILE'] = str(path)
        with patch.object(h, 'run') as run:
            h.compose(self.values(), 'identity', 'config', '--quiet')
        command = run.call_args.args[0]
        self.assertEqual(command[command.index('--env-file') + 1], path)

    def test_environment_shell_injection_is_rejected_without_write(self):
        c = self.values(); c['DOMAIN'] = '$(not-executed)'; path = self.base / 'host.env'
        with self.assertRaises(h.SafetyError): ui.save_environment(path, c)
        self.assertFalse(path.exists())

    def test_prepared_settings_cannot_escape_marker_or_reuse_network_approvals(self):
        c=self.values(); path=self.base/'host.env'
        ui.save_environment(path,c)
        runtime=Path(c['RUNTIME_ROOT']); runtime.mkdir()
        h.atomic(runtime/'install-state.json',json.dumps({'identity':{
            key:c[key] for key in ('RUNTIME_ROOT','APPDATA_ROOT','MEDIA_ROOT')}}))
        original=path.read_bytes()
        for key,value in (('RUNTIME_ROOT',str(self.base/'new-runtime')),('DOMAIN','other.test'),
                          ('LAN_IP','192.168.20.3')):
            with self.subTest(key=key),self.assertRaises(h.SafetyError):
                ui.save_environment(path,{**c,key:value})
            self.assertEqual(path.read_bytes(),original)

    def test_private_hosts_and_missing_identity_route_cannot_be_selected(self):
        for name in ('terminal', 'docker', 'traefik', 'adguard', 'monitor', 'plex', 'postgres', 'home'):
            with self.subTest(name=name), self.assertRaises(h.SafetyError): ui.selected_apps(name)
        self.assertEqual(ui.selected_apps('auth,home,hooks'), ['auth', 'home', 'hooks'])
        self.assertEqual(ui.selected_apps('none'), [])

    def test_remote_phase_is_blocked_until_native_and_identity_setup(self):
        wizard = ui.Wizard(self.base)
        with patch.object(h, 'compose') as compose:
            with self.assertRaises(h.SafetyError): wizard.remote()
            compose.assert_not_called()

    def test_bootstrap_password_is_hashed_by_pinned_image_over_stdin(self):
        password = 'only-a-temporary-test-password'
        with patch.object(h, 'compose', return_value='pbkdf2_sha256$policy$salt$encoded\n') as compose:
            result = ui.hash_authentik_password(self.values(), password)
        self.assertEqual(result, 'pbkdf2_sha256$policy$salt$encoded')
        self.assertNotIn(password, str(compose.call_args.args))
        self.assertEqual(compose.call_args.kwargs['stdin_text'], password + '\n')
        self.assertIn('--no-deps', compose.call_args.args)
        args = compose.call_args.args
        self.assertEqual(args[args.index('--entrypoint') + 1], 'python')
        self.assertNotIn('hash_password', args)
        self.assertIn('authentik.root.settings', args[-1])

    def test_bootstrap_password_rejects_missing_or_ambiguous_hash_output(self):
        for output in ('', 'Loaded config\n', 'pbkdf2_sha256$a\npbkdf2_sha256$b\n'):
            with self.subTest(output=output), patch.object(h, 'compose', return_value=output):
                with self.assertRaises(h.SafetyError):
                    ui.hash_authentik_password(self.values(), 'temporary-test-password')

    def test_dashboard_connector_token_is_not_local_credentials(self):
        with self.assertRaises(api.APIError): api.validate_tunnel_credentials({'a': ACCOUNT, 't': TUNNEL, 's': 'connector'})
        remote = Mock(); remote.request.return_value = {'config_src': 'cloudflare'}
        with self.assertRaises(api.APIError): api.verify_local_tunnel(remote, credentials())

    def test_api_tunnel_creation_is_explicitly_local_and_preserves_secret_first(self):
        events = []
        def request(method, path, data=None):
            events.append((method, data))
            return [] if method == 'GET' else {'id': TUNNEL, 'config_src': 'local'}
        client = Mock(); client.request.side_effect = request
        result = api.create_local_tunnel(client, ACCOUNT, 'homelab', lambda data: events.append(('pending', data)))
        self.assertEqual([x[0] for x in events], ['GET', 'pending', 'POST'])
        self.assertEqual(events[2][1]['config_src'], 'local')
        self.assertEqual(result['TunnelSecret'], events[1][1]['TunnelSecret'])
        self.assertEqual(result['TunnelID'], TUNNEL)

    def test_existing_tunnel_is_never_replaced(self):
        client = Mock(); client.request.return_value = [{'id': TUNNEL}]; pending = Mock()
        with self.assertRaises(api.APIError): api.create_local_tunnel(client, ACCOUNT, 'homelab', pending)
        self.assertEqual(client.request.call_count, 1); pending.assert_not_called()

    @contextlib.contextmanager
    def origin_response(self, status=200, head_status=None, malformed=False, tls_error=None):
        class HeadersOnly(io.BytesIO):
            def read(self, *args):
                raise AssertionError('Origin probe must not download the body')
        connection = MagicMock()
        connection.__enter__.return_value = connection
        def reply(request):
            code = head_status if request.startswith(b'HEAD ') and head_status is not None else status
            wire = (f'HTTP/1.1 {code} Test\r\nLocation: https://unrelated.invalid/\r\n'
                    'Content-Length: 1000000\r\n\r\n').encode()
            connection.makefile.return_value = HeadersOnly(b'not-http\r\n' if malformed else wire)
        connection.sendall.side_effect = reply
        context = ui.ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ui.ssl.CERT_REQUIRED)
        with patch.object(ui.ssl, 'create_default_context', return_value=context), \
             patch.object(context, 'wrap_socket', return_value=connection, side_effect=tls_error) as wrap, \
             patch.object(ui.socket, 'create_connection') as dial:
            yield connection, dial, wrap

    def test_origin_uses_get_for_filebrowser_with_head_404_and_preserves_local_tls(self):
        c = self.values()
        with self.origin_response(status=200, head_status=404) as (connection, dial, wrap):
            ui.tls_origin(c, 'files.lab.test')
            dial.assert_called_once_with((c['LAN_IP'], 443), timeout=15)
            wrap.assert_called_once_with(dial.return_value.__enter__.return_value,
                                         server_hostname='files.lab.test')
            connection.sendall.assert_called_once_with(
                b'GET / HTTP/1.1\r\nHost: files.lab.test\r\nConnection: close\r\n\r\n')

    def test_origin_accepts_auth_responses_without_following_redirects(self):
        for status in (200, 204, 301, 302, 303, 307, 308, 401, 403):
            with self.subTest(status=status), self.origin_response(status) as (_, dial, _):
                ui.tls_origin(self.values(), 'files.lab.test')
                self.assertEqual(dial.call_count, 1)

    def test_origin_rejects_missing_routes_method_errors_and_server_errors(self):
        for status in (404, 405, 429, 500, 502, 503):
            with self.subTest(status=status), self.origin_response(status):
                with self.assertRaisesRegex(h.SafetyError, f'files.lab.test.*HTTP {status}'):
                    ui.tls_origin(self.values(), 'files.lab.test')
        with self.origin_response(malformed=True):
            with self.assertRaisesRegex(h.SafetyError, 'Invalid HTTP response'):
                ui.tls_origin(self.values(), 'files.lab.test')

    def test_origin_hooks_404_exception_is_limited_to_probe_path(self):
        with self.origin_response(404) as (connection, _, _):
            ui.tls_origin(self.values(), 'hooks.lab.test')
            self.assertTrue(connection.sendall.call_args.args[0].startswith(
                b'GET /webhook/__homelab_probe__ HTTP/1.1\r\n'))
        with self.origin_response(500), self.assertRaises(h.SafetyError):
            ui.tls_origin(self.values(), 'hooks.lab.test')

    def test_origin_failure_blocks_remote_state_dns_and_deployment(self):
        for failure in ('http', 'tls'):
            with self.subTest(failure=failure):
                wizard = ui.Wizard(self.base)
                wizard.state = {'completed': ['identity', 'tunnel-credentials'],
                                'selected': ['storage'], 'native_stacks': ['storage']}
                wizard.mark('applications')
                before = wizard.file.read_bytes()
                tls_error = ui.ssl.SSLCertVerificationError('test certificate failure') if failure == 'tls' else None
                with patch.object(wizard, 'c', return_value=self.values()), \
                     patch.object(ui, 'prompt', return_value='auth,files'), \
                     patch.object(ui, 'yes', return_value=True), \
                     patch.object(ui, 'ensure_dns') as dns, \
                     patch.object(ui, 'save_environment') as save, \
                     patch.object(h, 'compose') as compose, \
                     self.origin_response(404, tls_error=tls_error):
                    with self.assertRaises((h.SafetyError, ui.ssl.SSLCertVerificationError)):
                        wizard.remote()
                    dns.assert_not_called(); save.assert_not_called(); compose.assert_not_called()
                self.assertEqual(wizard.file.read_bytes(), before)

    def test_dns_checks_every_conflict_before_any_write(self):
        client = Mock(); client.request.side_effect = [[], [{'type': 'A', 'content': '192.0.2.1'}]]
        with self.assertRaises(api.APIError): api.ensure_dns(client, ZONE, TUNNEL, ['home.lab.test', 'files.lab.test'])
        self.assertTrue(all(c.args[0] == 'GET' for c in client.request.call_args_list))

    def test_dns_records_are_exact_and_existing_matching_records_reused(self):
        client = Mock(); client.request.side_effect = [[], {}]
        api.ensure_dns(client, ZONE, TUNNEL, ['home.lab.test'])
        payload = client.request.call_args.args[2]
        self.assertEqual(payload['type'], 'CNAME'); self.assertEqual(payload['name'], 'home.lab.test')
        self.assertEqual(payload['content'], TUNNEL + '.cfargotunnel.com'); self.assertTrue(payload['proxied'])
        client.reset_mock(); client.request.side_effect = None
        client.request.return_value = [{'type': 'CNAME', 'content': payload['content'], 'proxied': True}]
        self.assertEqual(api.ensure_dns(client, ZONE, TUNNEL, ['home.lab.test']), [])
        self.assertEqual(client.request.call_count, 1)

    def test_zone_must_match_domain_and_be_active(self):
        client = Mock(); client.request.return_value = [{'id': ZONE, 'name': 'other.test', 'status': 'active'}]
        with self.assertRaises(api.APIError): api.zone_for_domain(client, 'lab.test', 'home.other.test')
        client.request.assert_not_called()
        client.request.return_value = [{'id': ZONE, 'name': 'lab.test', 'status': 'pending'}]
        with self.assertRaises(api.APIError): api.zone_for_domain(client, 'lab.test', 'home.lab.test')

    def test_provider_error_bodies_and_tokens_are_not_logged(self):
        raw = b'private-api-key-should-never-appear'
        error = urllib.error.HTTPError('https://api.cloudflare.com', 403, 'forbidden', {}, io.BytesIO(raw))
        with patch.object(api, 'open_api', side_effect=error):
            with self.assertRaises(api.APIError) as result: api.cloudflare(raw.decode()).request('POST', '/test', {'secret': raw.decode()})
        self.assertNotIn(raw.decode(), str(result.exception)); self.assertIn('403', str(result.exception))

    def test_authenticated_api_redirects_are_never_followed(self):
        import urllib.request
        request = urllib.request.Request('https://api.cloudflare.com/client/v4/test',
                                         headers={'Authorization':'Bearer test-only-token'})
        for url in ('https://unrelated.invalid/path', 'http://api.cloudflare.com/path',
                    'https://api.cloudflare.com/other'):
            with self.subTest(url=url), self.assertRaises(api.APIError) as result:
                api.NoRedirect().redirect_request(request, None, 302, '', {}, url)
            self.assertNotIn('test-only-token', str(result.exception))
        with patch('urllib.request.build_opener') as build:
            api.open_api(request)
        self.assertIsInstance(build.call_args.args[0], api.NoRedirect)

    def test_monitor_uses_redirect_safe_transport_and_redacts_failures(self):
        import runpy
        c=self.values()
        root=Path(c['SECRETS_ROOT']);root.mkdir()
        h.atomic(root/'monitor-webhook.json',json.dumps({'url':'https://notify.invalid/hook',
                 'headers':{'Authorization':'Bearer test-notification-secret'}}),0o600)
        with patch.object(h,'config',return_value=c), \
             patch.object(h,'mounted',side_effect=h.SafetyError('missing fixture mount')), \
             patch.object(api,'open_api',side_effect=api.APIError('redirect refused')) as transport:
            with self.assertRaises(SystemExit) as result:
                runpy.run_path(str(ROOT/'scripts/lib/monitor.py'))
        self.assertEqual(transport.call_count,1)
        self.assertNotIn('test-notification-secret',str(result.exception))

    def test_repository_and_release_validation(self):
        self.assertEqual(launcher.repository_name('https://github.com/example/homelab.git'), 'example/homelab')
        for value in ('https://user:password@github.com/example/homelab', 'file:///tmp/repo', '-x/repo'):
            with self.assertRaises(launcher.InstallError): launcher.repository_name(value)
        for value in ('main', 'v2.0.0-rc1', '--force', 'v1;anything'):
            with self.assertRaises(launcher.InstallError): launcher.release_tag(value)






    def test_adguard_exact_rewrites_preserve_conflicting_entries(self):
        client = Mock(); client.request.side_effect = [{'running': True}, [{'domain': 'home.lab.test', 'answer': '192.0.2.9'}]]
        with self.assertRaises(api.APIError): api.adguard(client, 'owner', 'test-only-password', 'lab.test', '192.168.20.2', ['home'], ['https://resolver.test/dns-query'])
        self.assertTrue(all(c.args[0] == 'GET' for c in client.request.call_args_list))

    def test_adguard_new_setup_uses_local_admin_and_explicit_dns_names(self):
        client = Mock(); client.request.side_effect = [api.APIError('not initialized'), {}, {}, [], {}, {}]
        planned = api.adguard(client, 'owner', 'test-only-password', 'lab.test', '192.168.20.2', ['home'], ['https://resolver.test/dns-query'])
        self.assertEqual(planned, [{'domain': 'home.lab.test', 'answer': '192.168.20.2'}])
        configuration = client.request.call_args_list[2].args[2]
        self.assertEqual(configuration['web']['port'], 3000)
        self.assertEqual(configuration['username'], 'owner')
        self.assertNotIn('*', str(planned))

    def test_menu_exit_and_cancelled_form_do_not_write_state(self):
        wizard = ui.Wizard(self.base)
        with patch('builtins.input', return_value='0'), patch.object(h, 'run') as run:
            wizard.menu(); run.assert_not_called()
        self.assertFalse((self.base / 'wizard.json').exists())
        with patch('builtins.input', return_value='back'), patch.object(h, 'run'):
            with self.assertRaises(ui.Back): wizard.settings()
        self.assertFalse((self.base / 'host.env').exists())

    def test_remote_disable_closes_local_routes_even_if_docker_stop_fails(self):
        wizard = ui.Wizard(self.base); c = self.values(); c.update(REMOTE_APPS='auth,home', REMOTE_READY='yes')
        calls = []
        def compose(*args, **kwargs):
            calls.append('stop')
            raise subprocess.CalledProcessError(1, ['docker', 'compose', 'stop'])
        with patch.object(wizard, 'c', return_value=c), patch.object(ui, 'yes', return_value=True), \
             patch.object(wizard, 'controller', side_effect=lambda *args: calls.append(args[0])), \
             patch.object(h, 'compose', side_effect=compose):
            with self.assertRaises(subprocess.CalledProcessError): wizard.disable_remote()
        self.assertEqual(calls, ['render', 'stop'])
        saved = h.read_env(self.base / 'host.env')
        self.assertEqual(saved['REMOTE_APPS'], ''); self.assertEqual(saved['REMOTE_READY'], 'no')

    def test_host_config_cannot_be_inside_media_or_parent_of_appdata(self):
        c = self.values()
        for cfg in (c['MEDIA_ROOT'] + '/installer', str(self.base)):
            c['HOMELAB_CONFIG_DIR'] = cfg
            with self.assertRaises(h.SafetyError): h.validate_paths(c)

    def test_api_does_not_accept_plain_http_remote_token_destinations(self):
        with self.assertRaises(api.APIError): api.JsonAPI('http://192.0.2.1', 'test-only-token')





    def test_remote_checks_native_onboarding_for_each_selected_stack(self):
        wizard = ui.Wizard(self.base); c = self.values()
        wizard.state.update(completed=['identity', 'tunnel-credentials', 'native-accounts'],
                            selected=['media', 'storage'], native_stacks=['media'])
        with patch.object(wizard, 'c', return_value=c), patch.object(ui, 'prompt', return_value='auth,files'), \
             patch.object(h, 'compose') as compose, patch.object(ui, 'ensure_dns') as dns:
            with self.assertRaises(h.SafetyError): wizard.remote()
            compose.assert_not_called(); dns.assert_not_called()

    def test_redeploy_invalidates_native_approval_until_reconfirmed(self):
        wizard = ui.Wizard(self.base); c = self.values()
        wizard.state.update(completed=['identity', 'native-accounts'], selected=['storage'], native_stacks=['storage'])
        with patch.object(wizard, 'c', return_value=c), patch.object(ui, 'prompt', side_effect=['storage', 'none']), \
             patch.object(ui, 'yes', side_effect=[True, False]), patch.object(h, 'gates'), \
             patch.object(wizard, 'controller'):
            wizard.applications()
        self.assertEqual(wizard.state['native_stacks'], [])
        self.assertNotIn('native-accounts', wizard.state['completed'])

    def test_boot_units_enable_only_selected_stacks_and_explicit_ai(self):
        for selected in (['media', 'storage', 'management'], ['ai']):
            with self.subTest(selected=selected):
                wizard = ui.Wizard(self.base)
                wizard.state.update(completed=['applications', 'units'], selected=selected)
                def answer(question):
                    return question.startswith('Enable verified-mount ')
                with patch.object(ui, 'yes', side_effect=answer), patch.object(h, 'run') as run:
                    wizard.operations()
                enabled = [call.args[0][-1] for call in run.call_args_list]
                self.assertEqual(set(enabled), {f'homelab-{stack}.service' for stack in selected})

    def test_optional_profiles_are_saved_only_after_deployment_approval(self):
        for approved in (False, True):
            wizard = ui.Wizard(self.base); c = self.values()
            wizard.state['completed'] = ['identity']
            with patch.object(wizard, 'c', return_value=c), \
                 patch.object(ui, 'prompt', side_effect=['media,management', 'bazarr,admin']), \
                 patch.object(ui, 'yes', side_effect=[approved, False]), \
                 patch.object(h, 'gates'), patch.object(wizard, 'controller') as controller, \
                 patch.object(ui, 'save_environment') as save:
                wizard.applications()
            self.assertEqual(save.called, approved)
            if approved:
                self.assertEqual(save.call_args.args[1]['COMPOSE_PROFILES'], 'bazarr,admin')
            else:
                controller.assert_not_called()

    def test_adding_ai_preserves_profiles_of_already_selected_stacks(self):
        wizard = ui.Wizard(self.base); c = self.values()
        c['COMPOSE_PROFILES'] = 'bazarr,admin'
        wizard.state.update(completed=['identity'], selected=['media', 'management'])
        with patch.object(wizard, 'c', return_value=c), \
             patch.object(ui, 'prompt', side_effect=['ai', 'bazarr,admin']), \
             patch.object(ui, 'yes', side_effect=[True, False]), patch.object(h, 'gates'), \
             patch.object(wizard, 'controller') as controller, patch.object(ui, 'save_environment') as save:
            wizard.applications()
        self.assertEqual(save.call_args.args[1]['COMPOSE_PROFILES'], 'bazarr,admin')
        controller.assert_called_once_with('deploy', '--stack', 'ai')


class IdentityReadinessTests(unittest.TestCase):
    def test_authenticated_api_waits_for_blueprints_before_returning(self):
        client=Mock(); reads=[]
        def listing(path):
            reads.append(path)
            if path.startswith('/core/users/'):return [{'username':'akadmin'}]
            if path=='/flows/instances/':
                return [] if reads.count(path)==1 else [{'slug':v} for v in ('default-provider-authorization-implicit-consent','default-provider-invalidation-flow')]
            if path=='/propertymappings/provider/scope/':return [{'managed':'goauthentik.io/providers/proxy/scope-proxy'}]
            if path=='/stages/authenticator/validate/':return [{'name':'default-authentication-mfa-validation'}]
            if path=='/outposts/instances/':return [{'managed':'goauthentik.io/outposts/embedded'}]
            raise AssertionError(path)
        client.listing.side_effect=listing
        with patch.object(api.time,'sleep') as sleep:api.wait_for_identity(client,attempts=2)
        sleep.assert_called_once_with(5);client.request.assert_not_called()

    def test_timeout_never_provisions_identity(self):
        client=Mock();client.listing.return_value=[]
        with patch.object(api.time,'sleep') as sleep, self.assertRaises(api.APIError):
            api.wait_for_identity(client,attempts=2)
        client.request.assert_not_called();sleep.assert_called_once()


class IdentityAPITests(unittest.TestCase):
    def test_group_bound_proxy_providers_outpost_and_mfa_are_created(self):
        calls = []; client = Mock(); counter = {'provider': 0}
        flows = [{'slug': s, 'pk': s} for s in ('default-provider-authorization-implicit-consent', 'default-provider-invalidation-flow')]
        def listing(path):
            if path.startswith('/core/users/'): return [{'pk': 1, 'username': 'akadmin', 'groups': ['builtin-admins']}]
            if path.startswith('/core/applications/'):
                self.assertIn('superuser_full_list=true', path, 'Normal lists can hide already-provisioned apps')
                return []
            if path == '/flows/instances/': return flows
            if path == '/propertymappings/provider/scope/': return [{'pk': 'proxy-scope', 'managed': 'goauthentik.io/providers/proxy/scope-proxy'}]
            if path == '/outposts/instances/': return [{'pk': 'embedded', 'managed': 'goauthentik.io/outposts/embedded', 'providers': [], 'config': {}}]
            if path == '/stages/authenticator/validate/': return [{'pk': 'mfa', 'name': 'default-authentication-mfa-validation'}]
            return []
        def request(method, path, data=None):
            calls.append((method, path, data))
            if path == '/stages/authenticator/totp/': return {'pk': 'totp-enrollment', **data}
            if path == '/core/groups/': return {'pk': data['name'], **data}
            if path == '/providers/proxy/':
                counter['provider'] += 1
                return {'pk': counter['provider'], **data}
            if path == '/core/applications/': return {'pk': data['slug'], **data}
            return data or {}
        client.listing.side_effect = listing; client.request.side_effect = request
        groups = api.authentik(client, 'lab.test')
        self.assertEqual(len(groups), 5)
        providers = [data for method, path, data in calls if path == '/providers/proxy/']
        self.assertEqual(len(providers), 10)
        self.assertTrue(all(p['mode'] == 'forward_single' and p['skip_path_regex'] == '' for p in providers))
        bindings = [data for method, path, data in calls if path == '/policies/bindings/']
        self.assertTrue(all(b['enabled'] and b['group'] and not b.get('policy') for b in bindings))
        monitor = next(p for p in providers if p['external_host'] == 'https://monitor.lab.test')
        self.assertFalse(monitor['intercept_header_auth'])
        self.assertEqual([b['group'] for b in bindings if b['target'] == 'homelab-monitor'],
                         ['homelab-admins'])
        self.assertIn(('PATCH', '/stages/authenticator/validate/mfa/', {'not_configured_action': 'configure', 'configuration_stages': ['totp-enrollment'], 'device_classes': ['totp', 'webauthn', 'static']}), calls)
        self.assertIn(('PATCH', '/core/users/1/', {'groups': ['builtin-admins', 'homelab-admins']}), calls)
        outpost = next(data for method, path, data in calls if path == '/outposts/instances/embedded/')
        self.assertEqual(outpost['config']['authentik_host'], 'https://auth.lab.test')
        self.assertEqual(len(outpost['providers']), 10)


if __name__ == '__main__': unittest.main()
