import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts/lib'))
import homelab as h


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name).resolve()
        self.c=h.config(example=True)
        self.c['HOMELAB_CONFIG_DIR'] = str(self.base / 'host-config')
        for name in ('APPDATA_ROOT','SECRETS_ROOT','RUNTIME_ROOT','DATA_ROOT','MEDIA_ROOT'):
            self.c[name]=str(self.base/name.lower())
        self.c.update(REPO_ROOT=str(h.ROOT),LAN_IP='10.2.3.4',DOMAIN='lab.test',
                      MEDIA_UUID='abcd-1234',DATA_UUID='dcba-4321',IDENTITY_READY='yes',MIN_MEDIA_FREE_GIB='0')
        self.c['RESTIC_REPOSITORY']=self.c['DATA_ROOT']+'/backups/restic'
        self.secret=Path(self.c['SECRETS_ROOT'])
        self.secret.mkdir(mode=0o700)

    def tearDown(self):
        self.tmp.cleanup()

    def test_dockge_discovery_preserves_source_and_project_names(self):
        runtime=Path(self.c['RUNTIME_ROOT']);runtime.mkdir()
        source=self.base/'source';source.mkdir()
        self.c['REPO_ROOT']=str(source)
        host=self.base/'host.env';host.write_text('DOMAIN=lab.test\n')
        for stack in h.STACKS:
            folder=source/'stacks'/stack;folder.mkdir(parents=True)
            (folder/'compose.yaml').write_text('name: homelab-'+stack+'\n')
            (folder/'.env').symlink_to(host)
        h.render_dockge_stacks(self.c)
        view=runtime/'dockge-stacks'
        before={p.name:p.lstat().st_ino for p in view.iterdir()}
        h.render_dockge_stacks(self.c)
        self.assertEqual(before,{p.name:p.lstat().st_ino for p in view.iterdir()})
        self.assertEqual(set(before),{'homelab-'+s for s in h.STACKS})
        for stack in h.STACKS:
            alias=view/('homelab-'+stack)
            self.assertEqual((alias/'.env').resolve(),host)
            self.assertEqual((alias/'compose.yaml').stat().st_ino,
                             (source/'stacks'/stack/'compose.yaml').stat().st_ino)
        # A release directory replacement must be followed by the same links.
        source.rename(self.base/'previous-source')
        for stack in h.STACKS:
            folder=source/'stacks'/stack;folder.mkdir(parents=True)
            (folder/'compose.yaml').write_text('new release\n')
        self.assertEqual((view/'homelab-media'/'compose.yaml').read_text(),'new release\n')

    def test_dockge_discovery_rejects_conflicts_without_replacing_them(self):
        runtime=Path(self.c['RUNTIME_ROOT']);runtime.mkdir()
        view=runtime/'dockge-stacks';view.mkdir()
        conflict=view/'homelab-media'
        conflict.write_text('operator file')
        with self.assertRaises(h.SafetyError):h.render_dockge_stacks(self.c)
        self.assertEqual(conflict.read_text(),'operator file')
        self.assertEqual(list(view.iterdir()),[conflict])
        conflict.unlink();conflict.symlink_to(self.base/'wrong-target')
        with self.assertRaises(h.SafetyError):h.render_dockge_stacks(self.c)
        self.assertEqual(conflict.readlink(),self.base/'wrong-target')
        conflict.unlink();view.rmdir();view.symlink_to(self.base/'elsewhere')
        with self.assertRaises(h.SafetyError):h.render_dockge_stacks(self.c)

    def test_deploy_recreates_containers_to_refresh_source_and_storage_binds(self):
        with patch.object(h,'gates'), patch.object(h,'compose') as compose, patch.object(h,'health'):
            h.deploy(self.c,'infrastructure')
        up=next(c.args for c in compose.call_args_list if 'up' in c.args)
        self.assertIn('--force-recreate',up)
        self.assertEqual(up[up.index('--wait-timeout')+1],self.c['STACK_START_TIMEOUT'])

    def test_paths_reject_root_relative_overlap_and_symlink(self):
        for bad in ('/','relative','/mnt/../etc','/srv/has space','/etc'):
            with self.subTest(bad=bad),self.assertRaises(h.SafetyError):h.safe_path(bad)
        self.c['APPDATA_ROOT']=self.c['MEDIA_ROOT']+'/app'
        with self.assertRaises(h.SafetyError):h.validate_paths(self.c)
        (self.base/'real').mkdir();(self.base/'link').symlink_to(self.base/'real')
        with self.assertRaises(h.SafetyError):h.safe_path(str(self.base/'link'))

    def test_environment_is_never_shell_evaluated(self):
        p=self.base/'config'
        p.write_text('DOMAIN=$(touch /tmp/should-not-exist)\n')
        with self.assertRaises(h.SafetyError):h.read_env(p)

    def test_missing_mount_prevents_docker_mutation(self):
        with patch.object(h,'need_secrets'),patch.object(h,'mounted',side_effect=h.SafetyError('missing')),patch.object(h,'compose') as compose:
            with self.assertRaises(h.SafetyError):h.deploy(self.c,'media')
            compose.assert_not_called()

    def test_wrong_uuid_and_non_exact_mount_rejected(self):
        for item in [{'target':self.c['MEDIA_ROOT'],'uuid':'wrong','fstype':'ext4','options':'rw'},
                     {'target':'/mnt','uuid':self.c['MEDIA_UUID'],'fstype':'ext4','options':'rw'}]:
            with patch.object(h,'run',return_value=json.dumps({'filesystems':[item]})):
                with self.assertRaises(h.SafetyError):h.mounted(self.c['MEDIA_ROOT'],self.c['MEDIA_UUID'],media=True)

    def test_missing_secret_prevents_start(self):
        with patch.object(h,'compose') as compose:
            with self.assertRaises(h.SafetyError):h.deploy(self.c,'automation')
            compose.assert_not_called()

    def test_secrets_reject_group_read_and_write_permissions(self):
        for name in h.STACK_SECRETS['automation']:
            file=self.secret/name;file.write_text('temporary-only-secret');file.chmod(0o600)
        h.need_secrets(self.c,'automation')
        for mode in (0o640,0o660,0o644):
            (self.secret/'n8n-encryption-key').chmod(mode)
            with self.subTest(mode=mode),self.assertRaisesRegex(h.SafetyError,'mode 0600'):
                h.need_secrets(self.c,'automation')

    def test_media_validation_does_not_modify_files(self):
        media=Path(self.c['MEDIA_ROOT']);media.mkdir()
        self.c['PUID']=str(os.getuid() or 1000);self.c['PGID']=str(os.getgid() or 1000)
        for rel in h.MEDIA_DIRS:(media/rel).mkdir(parents=True)
        a=media/'downloads/torrents/complete/movies/test.mkv';a.write_bytes(b'preserve me')
        b=media/'media/movies/test.mkv';os.link(a,b)
        def snapshot():return {str(p.relative_to(media)):(p.stat().st_ino,p.stat().st_nlink,p.stat().st_mtime_ns,p.stat().st_mode,p.read_bytes() if p.is_file() else None) for p in media.rglob('*')}
        before=snapshot()
        with patch.object(h,'mounted',return_value=media),contextlib.redirect_stdout(io.StringIO()):h.check_media(self.c)
        self.assertEqual(before,snapshot());self.assertEqual(a.stat().st_ino,b.stat().st_ino)

    def test_storage_requires_confirmation_before_writes(self):
        with patch.object(h,'mounted'),patch.object(h,'atomic') as write,patch.object(h,'root_only') as root:
            with self.assertRaises(h.SafetyError):h.configure_storage(self.c)
            write.assert_not_called();root.assert_not_called()

    def test_fstab_never_schedules_filesystem_repair(self):
        output=io.StringIO()
        with patch.object(h,'mounted'),contextlib.redirect_stdout(output):
            h.configure_storage(self.c,dry=True)
        lines=[line for line in output.getvalue().splitlines() if line.startswith('UUID=')]
        self.assertEqual(len(lines),2)
        self.assertTrue(all(line.split()[-2:]==['0','0'] for line in lines))
        existing='UUID='+self.c['MEDIA_UUID']+' '+self.c['MEDIA_ROOT']+' ext4 defaults 0 2\n'
        with patch.object(h,'mounted'),patch.object(h,'root_only'),patch.object(Path,'read_text',return_value=existing), \
             patch.object(h,'atomic') as write,contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(h.SafetyError,'automatic filesystem repair'):
                h.configure_storage(self.c,confirmed=True)
        write.assert_not_called()

    def test_restore_never_targets_media_or_existing_directory(self):
        with patch.object(h,'run') as run:
            for target in (self.c['MEDIA_ROOT']+'/restore',self.c['APPDATA_ROOT']):
                with self.assertRaises(h.SafetyError):h.restore(self.c,'abcdef',target)
            run.assert_not_called()

    def test_backend_credentials_cannot_bypass_backup_restore_destination_checks(self):
        (self.secret/'restic-password').write_text('temporary-password')
        extra=self.secret/'restic-env.json'
        for key,value in (('RESTIC_REPOSITORY',self.c['MEDIA_ROOT']+'/backup'),
                          ('RESTIC_PASSWORD_COMMAND','untrusted-command'),('PATH','/untrusted')):
            extra.write_text(json.dumps({key:value}))
            with self.subTest(key=key),patch.object(h,'root_only'),patch.object(h,'mounted'),patch.object(h,'run') as run:
                with self.assertRaisesRegex(h.SafetyError,'cannot override'):h.backup(self.c,init=True)
                with self.assertRaisesRegex(h.SafetyError,'cannot override'):
                    h.restore(self.c,'latest',self.c['RUNTIME_ROOT']+'/restore/new')
                run.assert_not_called()
        extra.write_text(json.dumps({'AWS_ACCESS_KEY_ID':'audit-id','AWS_SECRET_ACCESS_KEY':'audit-key'}))
        env=h.restic_environment(self.c)
        self.assertEqual(env['RESTIC_REPOSITORY'],self.c['RESTIC_REPOSITORY'])
        self.assertEqual(env['AWS_SECRET_ACCESS_KEY'],'audit-key')

    def test_optional_ai_excluded_from_defaults(self):
        self.assertNotIn('ai',h.CORE)
        with patch.object(h,'config',return_value=self.c),patch.object(h,'deploy') as deploy,patch.object(h,'gates'),patch.object(sys,'argv',['control','deploy-all','--dry-run']):
            h.main()
            self.assertNotIn('ai',[x.args[1] for x in deploy.call_args_list])

    def test_ai_memory_gate_allows_reserved_ram_but_rejects_smaller_hosts(self):
        dynamic=Path(self.c['RUNTIME_ROOT'])/'traefik/dynamic.yml'
        dynamic.parent.mkdir(parents=True)
        dynamic.write_text('{}\n')
        for total,allowed in ((0,False),(4096,False),(7679,False),
                              (7680,True),(7782,True),(8192,True)):
            with self.subTest(total_mib=total),patch.object(h,'need_secrets'), \
                 patch.object(h,'mounted') as mounted, \
                 patch.object(Path,'read_text',return_value=f'MemTotal: {total*1024} kB\nSwapTotal: 8388608 kB\n'):
                if allowed:
                    h.gates(self.c,'ai')
                else:
                    with self.assertRaisesRegex(h.SafetyError,'7680 MiB OS-visible RAM'):
                        h.gates(self.c,'ai')
                mounted.assert_called_once_with(self.c['DATA_ROOT'],self.c['DATA_UUID'])

    def test_private_services_cannot_enter_tunnel(self):
        self.c.update(REMOTE_APPS='terminal,docker',REMOTE_READY='yes')
        with patch.object(h,'atomic') as write:
            with self.assertRaises(h.SafetyError):h.render(self.c)
            write.assert_not_called()

    def test_firewall_rejects_world_admin_cidrs_before_any_command(self):
        import runpy
        c={**self.c,'LAN_INTERFACE':'eth0'}
        for cidr in ('0.0.0.0/0','::/0'):
            with self.subTest(cidr=cidr),patch.object(h,'config',return_value={**c,'ADMIN_CIDRS':cidr}), \
                 patch.object(h,'run') as run,patch.object(sys,'argv',['firewall','--apply']):
                with self.assertRaisesRegex(h.SafetyError,'default route'):
                    runpy.run_path(str(h.ROOT/'scripts/lib/firewall.py'))
            run.assert_not_called()

    def test_mount_guard_stops_on_failure(self):
        with patch.object(h,'check_media',side_effect=h.SafetyError('missing')),patch.object(h,'mounted'),patch.object(h,'compose') as compose:
            with self.assertRaises(h.SafetyError):h.guard(self.c)
            self.assertEqual([call.args[1] for call in compose.call_args_list], ['media','storage'])

    def test_guard_attempts_every_affected_stack_after_one_stop_fails(self):
        def stop(c, stack, *args, **kwargs):
            if stack == 'media': raise subprocess.CalledProcessError(1, ['docker', 'compose', 'stop'])
        with patch.object(h,'check_media',side_effect=h.SafetyError('missing')), \
             patch.object(h,'mounted',side_effect=h.SafetyError('missing')), \
             patch.object(h,'compose',side_effect=stop) as compose:
            with self.assertRaisesRegex(h.SafetyError, 'stop FAILED for: media'):
                h.guard(self.c)
        self.assertEqual([call.args[1] for call in compose.call_args_list], ['media','storage','management','ai'])

    def test_health_rejects_empty_partial_and_starting_stacks(self):
        for rows in ([], [{'Service':'postgres','State':'running','Health':'healthy'}],
                     [{'Service':'postgres','State':'running','Health':'healthy'},
                      {'Service':'server','State':'running','Health':'starting'}]):
            def compose(c, stack, *args, **kwargs):
                return 'postgres\nserver\n' if args[0]=='config' else json.dumps(rows)
            with self.subTest(rows=rows), patch.object(h,'compose',side_effect=compose):
                with self.assertRaises(h.SafetyError): h.health(self.c,'identity')
        with patch.object(h,'compose',return_value=''):
            with self.assertRaises(h.SafetyError): h.health(self.c,'identity')
            with self.assertRaises(h.SafetyError): h.health(self.c)

    def test_health_accepts_completed_guard_and_healthy_consumers(self):
        rows=[{'Service':'data-guard','State':'exited','ExitCode':0},
              {'Service':'files','State':'running','Health':'healthy'}]
        def compose(c, stack, *args, **kwargs):
            return 'data-guard\nfiles\n' if args[0]=='config' else json.dumps(rows)
        with patch.object(h,'compose',side_effect=compose), contextlib.redirect_stdout(io.StringIO()):
            h.health(self.c,'storage')

    def test_backup_restart_guard_blocks_media_if_disk_removed(self):
        # A failure after application stop must still attempt restart of other applications.
        (Path(self.c['SECRETS_ROOT'])/'restic-password').write_text('test-only-password')
        (Path(self.c['RUNTIME_ROOT'])/'dumps').mkdir(parents=True)
        calls=[]
        def compose(c,n,*args,**kwargs):
            calls.append((n,args))
            if args[0]=='ps':return 'radarr\n' if n=='media' else ('kuma\n' if n=='monitoring' else '')
            return ''
        with patch.object(h,'mounted'),patch.object(h,'compose',side_effect=compose),patch.object(h,'check_media',side_effect=h.SafetyError('unplugged')),contextlib.redirect_stderr(io.StringIO()),contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(h.SafetyError):h.backup(self.c,dry=True)
        self.assertFalse(any(n=='media' and args[0]=='start' for n,args in calls))
        self.assertIn(('monitoring',('start','--wait','--wait-timeout','600','kuma')),calls)

    def test_rendered_routes_preserve_access_boundaries(self):
        runtime=Path(self.c['RUNTIME_ROOT'])
        for sub in ('traefik','homepage'):(runtime/sub).mkdir(parents=True)
        self.c.update(REMOTE_APPS='auth,home,files,hooks',REMOTE_READY='yes',ADMIN_CIDRS='10.2.3.0/24')
        h.render(self.c)
        dynamic=json.loads((runtime/'traefik/dynamic.yml').read_text())
        routers=dynamic['http']['routers']
        self.assertIn('PathPrefix(`/webhook/`)',routers['remote-hooks']['rule'])
        self.assertNotIn('/webhook-test',routers['remote-hooks']['rule'])
        for name,router in routers.items():
            self.assertNotIn('terminal',router['rule'])
            self.assertNotIn('docker.',router['rule'])
        self.assertEqual(routers['remote-files']['middlewares'],['headers@file'])
        self.assertIn('sso@file',routers['remote-home']['middlewares'])
        self.assertNotIn('hooks.',routers['remote-outpost']['rule'])
        self.assertNotIn('files.',routers['remote-outpost']['rule'])
        self.assertIn('home.',routers['remote-outpost']['rule'])
        ingress=json.loads((runtime/'cloudflared.yml').read_text())['ingress']
        self.assertEqual(ingress[-1],{'service':'http_status:404'})
        self.assertTrue(all('noTLSVerify' not in i.get('originRequest',{}) for i in ingress))
        self.assertEqual((runtime/'files.yaml').stat().st_mode & 0o777,0o600)
        self.assertEqual(json.loads((runtime/'files.yaml').read_text())['server']['sources'][0]['config']['defaultEnabled'],False)

    def test_backup_failure_restarts_original_running_services(self):
        (self.secret/'restic-password').write_text('test-only-password')
        (Path(self.c['RUNTIME_ROOT'])/'dumps').mkdir(parents=True)
        calls=[]
        def compose(c,n,*args,**kwargs):
            calls.append((n,args))
            if args[0]=='ps':return 'kuma\n' if n=='monitoring' else ''
            return ''
        def run(cmd,**kwargs):
            if cmd[:2]==['restic','backup']:raise subprocess.CalledProcessError(1,cmd)
            return ''
        with patch.object(h,'root_only'),patch.object(h,'mounted'),patch.object(h,'compose',side_effect=compose),patch.object(h,'run',side_effect=run):
            with self.assertRaises(subprocess.CalledProcessError):h.backup(self.c)
        self.assertIn(('monitoring',('stop','kuma')),calls)
        self.assertIn(('monitoring',('start','--wait','--wait-timeout','600','kuma')),calls)
        self.assertFalse((Path(self.c['RUNTIME_ROOT'])/'backup-status.json').exists())

    def test_backup_restart_failure_does_not_publish_success(self):
        (self.secret/'restic-password').write_text('test-only-password')
        runtime=Path(self.c['RUNTIME_ROOT']); (runtime/'dumps').mkdir(parents=True)
        def compose(c,n,*args,**kwargs):
            if args[0]=='ps': return 'kuma\n' if n=='monitoring' else ''
            if args[0]=='start': raise subprocess.CalledProcessError(1,['docker','compose','start'])
            return ''
        with patch.object(h,'root_only'), patch.object(h,'mounted'), \
             patch.object(h,'compose',side_effect=compose), patch.object(h,'run'), \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(h.SafetyError,'not restarted'): h.backup(self.c)
        self.assertFalse((runtime/'backup-status.json').exists())

    def test_mount_guard_rejects_non_block_expected_device(self):
        import mount_guard
        device=self.base/'not-a-device';device.write_text('not a block device')
        with self.assertRaises(RuntimeError):mount_guard.check(str(self.base),str(device))

    def test_mount_guard_rejects_symlinked_parent_inside_same_filesystem(self):
        import mount_guard
        import stat
        from types import SimpleNamespace
        root=self.base/'media'; root.mkdir()
        actual=self.base/'redirected'; actual.mkdir()
        (actual/'movies').mkdir(); (root/'media').symlink_to(actual)
        original=os.stat
        device=str(self.base/'expected-device')
        def inspect(path,*args,**kwargs):
            if str(path)==device:
                return SimpleNamespace(st_mode=stat.S_IFBLK,st_rdev=root.stat().st_dev)
            return original(path,*args,**kwargs)
        with patch.object(mount_guard.os,'stat',side_effect=inspect),patch.dict(os.environ,{'GUARD_KIND':'media'}):
            with self.assertRaisesRegex(RuntimeError,'redirected'):mount_guard.check(str(root),device)

    def test_existing_secret_is_not_rotated(self):
        key=self.secret/'n8n-encryption-key';key.write_text('keep-existing-key')
        # Generation is root-gated; mock privilege and avoid root ownership writes on this Mac.
        with patch.object(h,'root_only'),patch.object(h.os,'chown'),patch.object(h.os,'geteuid',return_value=0):
            h.generate_secrets(self.c)
        self.assertEqual(key.read_text(),'keep-existing-key')


    def test_postgres_dump_preserves_restic_backend_environment(self):
        (self.secret/'restic-password').write_text('test-only-password')
        (Path(self.c['RUNTIME_ROOT'])/'dumps').mkdir(parents=True)
        seen=[]
        def compose(c,n,*args,**kwargs):
            if args[0]=='ps':return 'postgres\nserver\n' if n=='identity' else ''
            return ''
        def run(cmd,**kwargs):
            if cmd[:2]==['restic','backup']:seen.append(kwargs['env'])
            return ''
        def pg_run(cmd,**kwargs):
            self.assertIn('pg_dump',cmd)
            kwargs['stdout'].write('-- consistent test dump\n')
        with patch.object(h,'root_only'),patch.object(h,'mounted'),patch.object(h,'compose',side_effect=compose),patch.object(h,'run',side_effect=run),patch.object(h.subprocess,'run',side_effect=pg_run):
            h.backup(self.c)
        self.assertEqual(seen[0]['RESTIC_REPOSITORY'],self.c['RESTIC_REPOSITORY'])
        self.assertEqual(seen[0]['RESTIC_PASSWORD_FILE'],str(self.secret/'restic-password'))
        manifest=json.loads((Path(self.c['RUNTIME_ROOT'])/'dumps/manifest.json').read_text())
        self.assertEqual(manifest['fresh_logical_dumps'],['authentik'])


if __name__=='__main__':unittest.main()
