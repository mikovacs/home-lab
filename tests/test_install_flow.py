"""Release asset -> prepared host -> selected stacks, mocking only host/provider boundaries."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts/lib'))
import homelab as h
import installer as ui
import releases as r


class InstallFlowTests(unittest.TestCase):
    def test_release_to_foundation_and_apps_retains_media_and_source(self):
        with tempfile.TemporaryDirectory() as temporary, contextlib.redirect_stdout(io.StringIO()):
            base=Path(temporary).resolve(); target=base/'source'; state=base/'host';state.mkdir(mode=0o700)
            # Package the actual source tree without host state, as the Git-tracked release archive does.
            archive=base/'source.tar.gz'
            with tarfile.open(archive,'w:gz') as tar:
                for name in ('install.sh','.env.example','scripts','stacks','tests/check_config.py'):
                    tar.add(ROOT/name,arcname='homelab/'+name,filter=lambda m: None if '__pycache__' in m.name or m.name.endswith('/.env') else m)
            digest=hashlib.sha256(archive.read_bytes()).hexdigest()
            metadata={'id':1,'assets':[{'name':name,'state':'uploaded','id':num,
                'url':f'https://api.github.com/repos/example/lab/releases/assets/{num}'}
                for name,num in [('homelab-source.tar.gz',2),('SHA256SUMS',3)]]}
            def download(url,path,limit):
                path.write_bytes(archive.read_bytes() if url.endswith('/2') else (digest+'  homelab-source.tar.gz\n').encode())
            with patch.object(r,'release_metadata',return_value=metadata), patch.object(r,'download',side_effect=download), patch.object(r,'confirm',return_value=True):
                installed=r.install_release('example/lab',target,state,'v1.0.0')
            c=h.read_env(target/'.env.example')
            for name in ('APPDATA_ROOT','SECRETS_ROOT','RUNTIME_ROOT','DATA_ROOT','MEDIA_ROOT'):c[name]=str(base/name.lower())
            c.update(REPO_ROOT=str(target),HOMELAB_CONFIG_DIR=str(state),DOMAIN='lab.test',LAN_IP='192.168.20.2',
                     LAN_INTERFACE='eth0',LAN_CIDR='192.168.20.0/24',ADMIN_CIDRS='192.168.20.0/24',
                     ACME_EMAIL='admin@lab.test',MEDIA_UUID='media-123',DATA_UUID='data-456',MIN_MEDIA_FREE_GIB='0')
            c['RESTIC_REPOSITORY']=c['DATA_ROOT']+'/backups/restic'
            media=Path(c['MEDIA_ROOT']);data=Path(c['DATA_ROOT']);data.mkdir();media.mkdir()
            for name in h.MEDIA_DIRS:
                folder=media/name;folder.mkdir(parents=True);folder.chmod(0o777)
            movie=media/'media/movies/retained.mkv';movie.write_bytes(b'retained library')
            torrent=media/'downloads/torrents/complete/movies/retained.mkv';os.link(movie,torrent)
            def media_state():return {str(p.relative_to(media)):(p.stat().st_ino,p.stat().st_nlink,p.stat().st_mode,p.stat().st_mtime_ns,p.read_bytes() if p.is_file() else None) for p in media.rglob('*')}
            before=media_state();commands=[];configs=[]
            def run(args,**kwargs):
                args=list(map(str,args));commands.append(args)
                if args[0]=='docker' and 'compose' in args:
                    if '--env-file' in args:configs.append(kwargs.get('env',{}).get('REPO_ROOT'))
                    if '--entrypoint' in args and args[args.index('--entrypoint')+1]=='python':
                        self.assertIn('make_password(input())', args[-1])
                        self.assertEqual(kwargs.get('input'),'test-only-long-owner-password\n')
                        return 'pbkdf2_sha256$policy$salt$encoded\n'
                    if 'ps' in args:return json.dumps([{'Service':'test-app','State':'running','Health':'healthy'}])
                if args[0]=='htpasswd':return 'homelab-admin:$2y$test-only-protected-hash\n'
                return ''
            def controller(action,*args):
                with patch.object(sys,'argv',['homelab',action,*args]):h.main()
            with patch.dict(os.environ,{'PYTHONDONTWRITEBYTECODE':'1'}),patch.object(h,'ROOT',target), \
                 patch.object(h,'root_only'),patch.object(h,'configure_users'),patch.object(h.os,'chown'), \
                 patch.object(h,'mounted',side_effect=lambda path,*a,**kw:Path(path)),patch.object(h,'run',side_effect=run), \
                 patch.object(ui,'yes',return_value=True):
                wizard=ui.Wizard(state)
                ui.save_environment(wizard.environment,c);wizard.mark('settings');wizard.bootstrap();wizard.mark('storage')
                with patch.object(wizard,'controller',side_effect=controller):
                    wizard.setup()
                    self.assertFalse(any('up' in args for args in commands))
                    zone={'id':'b'*32,'account':{'id':'a'*32}}
                    def prompt(label,default='',validate=None):
                        if 'zone name' in label:return 'lab.test'
                        return default
                    with patch.object(ui,'prompt',side_effect=prompt),patch.object(ui,'secret',return_value='test-only-dns-token-value'), \
                         patch.object(ui,'new_password',return_value='test-only-long-owner-password'),patch.object(ui,'zone_for_domain',return_value=zone):
                        wizard.credentials()
                    wizard.foundation()
                    starts=[args for args in commands if 'up' in args]
                    self.assertEqual(len(starts),2)
                    self.assertTrue(all(any('/stacks/'+name+'/' in x for x in starts[i]) for i,name in enumerate(('infrastructure','identity'))))
                    with patch.object(ui,'JsonAPI',return_value=Mock()),patch.object(ui,'authentik') as identity,patch.object(ui,'adguard') as dns,patch.object(ui,'wait_for_identity'):
                        wizard.identity();identity.assert_called_once();dns.assert_called_once()
                    self.assertEqual(h.read_env(wizard.environment)['IDENTITY_READY'],'yes')
                    with patch.object(ui,'prompt',return_value='media,storage,monitoring,management,automation'):
                        wizard.applications()
                self.assertEqual(set(wizard.state['native_stacks']),set(ui.APP_STACKS)-{'ai'})
                self.assertEqual(len([args for args in commands if 'up' in args]),7)
                self.assertTrue(all(path==str(target) for path in configs))
                self.assertEqual(json.loads((Path(c['RUNTIME_ROOT'])/'install-state.json').read_text())['status'],'prepared')
                r.clean_source(target,'example/lab',state,installed)
            self.assertEqual(before,media_state())
            self.assertFalse((target/'.env').exists())
            for stack in h.STACKS:self.assertEqual((target/'stacks'/stack/'.env').resolve(),wizard.environment)


if __name__=='__main__':unittest.main()
