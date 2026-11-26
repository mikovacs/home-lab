"""Fresh-host preparation tests: no real accounts, mounts or Docker mutations."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/lib'))
import homelab as h


class FreshInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.c = h.config(example=True)
        self.c['HOMELAB_CONFIG_DIR'] = str(self.base / 'host-config')
        for key in ('APPDATA_ROOT', 'RUNTIME_ROOT', 'SECRETS_ROOT', 'DATA_ROOT', 'MEDIA_ROOT'):
            self.c[key] = str(self.base / key.lower())
        self.c.update(REPO_ROOT=str(h.ROOT), DATA_UUID='data-123', MEDIA_UUID='media-456')
        self.output = contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None)
        self.tmp.cleanup()

    def init_fixture(self):
        (self.base / '.env.example').write_text((h.ROOT / '.env.example').read_text())
        return patch.object(h, 'ROOT', self.base)

    def test_init_sets_checkout_and_private_mode_without_overwriting(self):
        with self.init_fixture():
            h.initialize_config()
            target = self.base / '.env'
            self.assertIn('REPO_ROOT=' + str(self.base), target.read_text())
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            target.write_text('DOMAIN=keep.test\n')
            h.initialize_config()
            self.assertEqual(target.read_text(), 'DOMAIN=keep.test\n')

    def test_init_dry_run_writes_nothing(self):
        with self.init_fixture():
            h.initialize_config(dry=True)
        self.assertFalse((self.base / '.env').exists())

    def test_init_refuses_symlink(self):
        with self.init_fixture():
            target = self.base / 'existing'; target.write_text('retain')
            (self.base / '.env').symlink_to(target)
            with self.assertRaises(h.SafetyError): h.initialize_config()
            self.assertEqual(target.read_text(), 'retain')

    def test_missing_identities_create_only_non_login_user_and_group(self):
        with patch('pwd.getpwnam', side_effect=KeyError), patch('pwd.getpwuid', side_effect=KeyError), \
             patch('grp.getgrnam', side_effect=KeyError), patch('grp.getgrgid', side_effect=KeyError), \
             patch.object(h, 'root_only'), patch.object(h, 'run') as run:
            h.configure_users(self.c)
        commands = [call.args[0] for call in run.call_args_list]
        self.assertEqual(commands[0], ['groupadd', '--gid', self.c['PGID'], self.c['MEDIA_GROUP']])
        self.assertIn('--no-create-home', commands[1])
        self.assertIn('/usr/sbin/nologin', commands[1])
        self.assertNotIn('usermod', str(commands))
        self.assertNotIn('chown', str(commands))

    def test_existing_numeric_mappings_are_reused_without_mutation(self):
        with patch('pwd.getpwnam', side_effect=KeyError), patch('grp.getgrnam', side_effect=KeyError), \
             patch('pwd.getpwuid', return_value=SimpleNamespace(pw_name='new-admin')), \
             patch('grp.getgrgid', return_value=SimpleNamespace(gr_name='new-admin')), \
             patch.object(h, 'root_only'), patch.object(h, 'run') as run:
            h.configure_users(self.c)
            run.assert_not_called()

    def test_name_id_collision_fails_before_any_user_change(self):
        with patch('pwd.getpwnam', return_value=SimpleNamespace(pw_uid=9876)), \
             patch('grp.getgrnam', side_effect=KeyError), patch.object(h, 'run') as run:
            with self.assertRaises(h.SafetyError): h.configure_users(self.c)
            run.assert_not_called()

    @staticmethod
    def unmounted(cmd, **kwargs):
        if cmd[0] == 'findmnt': raise subprocess.CalledProcessError(1, cmd)
        return ''

    def test_mount_requires_confirmation_before_mkdir_or_mount(self):
        with patch.object(h, 'probe_ext4_uuid'), patch.object(h, 'run', side_effect=self.unmounted) as run, \
             patch.object(h, 'mkdir_new') as mkdir:
            with self.assertRaises(h.SafetyError): h.mount_storage(self.c)
            mkdir.assert_not_called()
            self.assertTrue(all(c.args[0][0] == 'findmnt' for c in run.call_args_list))

    def test_mount_dry_run_has_no_mutations(self):
        with patch.object(h, 'probe_ext4_uuid'), patch.object(h, 'run', side_effect=self.unmounted) as run, \
             patch.object(h, 'mkdir_new') as mkdir:
            h.mount_storage(self.c, dry=True)
            mkdir.assert_not_called()
            self.assertTrue(all(c.args[0][0] == 'findmnt' for c in run.call_args_list))

    def test_invalid_second_device_prevents_all_mounts(self):
        with patch.object(h, 'probe_ext4_uuid', side_effect=[None, h.SafetyError('wrong media disk')]), \
             patch.object(h, 'run', side_effect=self.unmounted) as run, patch.object(h, 'mkdir_new') as mkdir:
            with self.assertRaises(h.SafetyError): h.mount_storage(self.c, confirmed=True)
            mkdir.assert_not_called()
            self.assertFalse(any(c.args[0][0] == 'mount' for c in run.call_args_list))

    def test_mount_refuses_to_hide_existing_directory_contents(self):
        data = Path(self.c['DATA_ROOT']); data.mkdir(); (data / 'important').write_text('keep')
        with patch.object(h, 'probe_ext4_uuid'), patch.object(h, 'run', side_effect=self.unmounted), \
             patch.object(h, 'mkdir_new') as mkdir:
            with self.assertRaises(h.SafetyError): h.mount_storage(self.c, confirmed=True)
            mkdir.assert_not_called()
        self.assertEqual((data / 'important').read_text(), 'keep')

    def test_confirmed_mount_uses_uuids_and_checks_each_mount(self):
        with patch.object(h, 'probe_ext4_uuid'), patch.object(h, 'run', side_effect=self.unmounted) as run, \
             patch.object(h, 'mkdir_new'), patch.object(h, 'mounted') as mounted, patch.object(h, 'root_only'):
            h.mount_storage(self.c, confirmed=True)
        commands = [c.args[0] for c in run.call_args_list if c.args[0][0] == 'mount']
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0][-2], 'UUID=' + self.c['DATA_UUID'])
        self.assertEqual(commands[1][-2], 'UUID=' + self.c['MEDIA_UUID'])
        self.assertEqual(mounted.call_count, 2)

    def test_fresh_setup_rejects_old_state_without_deleting_it(self):
        appdata = Path(self.c['APPDATA_ROOT']); appdata.mkdir(); old = appdata / 'old.db'; old.write_bytes(b'keep')
        with patch.object(h, 'mounted'), patch.object(h, 'check_media'), patch.object(h, 'run') as run, \
             patch.object(h, 'generate_secrets') as secrets:
            with self.assertRaises(h.SafetyError): h.setup_fresh(self.c)
            run.assert_not_called(); secrets.assert_not_called()
        self.assertEqual(old.read_bytes(), b'keep')

    def test_setup_never_starts_containers_and_can_be_repeated(self):
        runtime = Path(self.c['RUNTIME_ROOT'])
        def prepare(c, dry=False):
            if not dry: runtime.mkdir(exist_ok=True)
        with patch.object(h, 'mounted'), patch.object(h, 'check_media'), patch.object(h, 'run', return_value='') as run, \
             patch.object(h, 'configure_users'), patch.object(h, 'generate_secrets'), \
             patch.object(h, 'prepare', side_effect=prepare), patch.object(h, 'root_only'), patch.object(h, 'compose') as compose:
            h.setup_fresh(self.c)
            appdata = Path(self.c['APPDATA_ROOT']); appdata.mkdir(); (appdata / 'new.db').write_bytes(b'new state')
            h.setup_fresh(self.c)
            compose.assert_not_called()
        self.assertTrue((runtime / 'install-state.json').is_file())
        self.assertEqual((appdata / 'new.db').read_bytes(), b'new state')
        self.assertTrue(all(not any(word in c.args[0] for word in ('up', 'start', 'restart')) for c in run.call_args_list))

    def test_setup_marker_rejects_changed_ids(self):
        runtime = Path(self.c['RUNTIME_ROOT']); runtime.mkdir()
        (runtime / 'install-state.json').write_text(json.dumps({'identity': {'PUID': 'different'}}))
        with patch.object(h, 'mounted'), patch.object(h, 'check_media'), patch.object(h, 'configure_users') as users:
            with self.assertRaises(h.SafetyError): h.setup_fresh(self.c)
            users.assert_not_called()

    def test_batch_preflights_every_stack_before_starting_any(self):
        with patch.object(h, 'config', return_value=self.c), patch.object(h, 'gates', side_effect=[None, h.SafetyError('missing secret')]), \
             patch.object(h, 'deploy') as deploy, patch.object(sys, 'argv', ['control', 'deploy-foundation', '--dry-run']):
            with self.assertRaises(h.SafetyError): h.main()
            deploy.assert_not_called()


    def test_partial_setup_resumes_after_failure_without_reset(self):
        runtime, appdata = Path(self.c['RUNTIME_ROOT']), Path(self.c['APPDATA_ROOT'])
        def prepare(c, dry=False):
            appdata.mkdir(exist_ok=True)
            (appdata / 'new.db').write_text('preserve prepared state')
        count = 0
        def run(cmd, **kwargs):
            nonlocal count
            if cmd[:3] == ['docker', 'network', 'inspect']:
                count += 1
                if count == 1: raise OSError('simulated daemon disconnect')
            return ''
        with patch.object(h, 'mounted'), patch.object(h, 'check_media'), patch.object(h, 'root_only'), \
             patch.object(h, 'configure_users'), patch.object(h, 'generate_secrets'), \
             patch.object(h, 'prepare', side_effect=prepare), patch.object(h, 'run', side_effect=run):
            with self.assertRaises(OSError): h.setup_fresh(self.c)
            self.assertEqual(json.loads((runtime / 'install-state.json').read_text())['status'], 'preparing')
            h.setup_fresh(self.c)
        self.assertEqual(json.loads((runtime / 'install-state.json').read_text())['status'], 'prepared')
        self.assertEqual((appdata / 'new.db').read_text(), 'preserve prepared state')

    def test_first_setup_refuses_old_secrets(self):
        secrets = Path(self.c['SECRETS_ROOT']); secrets.mkdir()
        (secrets / 'old-key').write_text('keep')
        with patch.object(h, 'mounted'), patch.object(h, 'check_media'), patch.object(h, 'run') as run:
            with self.assertRaises(h.SafetyError): h.setup_fresh(self.c)
            run.assert_not_called()
        self.assertEqual((secrets / 'old-key').read_text(), 'keep')

    def test_setup_dry_run_does_not_create_state_or_start_containers(self):
        with patch.object(h, 'mounted'), patch.object(h, 'check_media'), patch.object(h, 'run', return_value=''), \
             patch.object(h, 'configure_users') as users, patch.object(h, 'generate_secrets') as secrets, \
             patch.object(h, 'prepare') as prepare, patch.object(h, 'compose') as compose:
            h.setup_fresh(self.c, dry=True)
            users.assert_called_once_with(self.c, dry=True)
            secrets.assert_called_once_with(self.c, dry=True)
            prepare.assert_called_once_with(self.c, dry=True)
            compose.assert_not_called()
        self.assertFalse(Path(self.c['RUNTIME_ROOT']).exists())


if __name__ == '__main__': unittest.main()
