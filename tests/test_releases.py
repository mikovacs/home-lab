"""Published-asset installation and updates, with real tar/filesystem operations."""
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
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
import releases as r


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.base = Path(self.tmp.name).resolve()
        self.target, self.state = self.base/'source', self.base/'host'
        self.state.mkdir(mode=0o700)
        self.output = contextlib.redirect_stdout(io.StringIO()); self.output.__enter__()

    def tearDown(self):
        self.output.__exit__(None, None, None); self.tmp.cleanup()

    def archive(self, version='one', extra=None):
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode='w:gz') as tar:
            for name in ('install.sh', '.env.example', 'scripts/bootstrap.sh', 'scripts/lib/homelab.py',
                         'scripts/lib/installer.py', 'scripts/lib/releases.py', 'stacks/media/compose.yaml'):
                data = version.encode(); member = tarfile.TarInfo('homelab/'+name)
                member.size = len(data); member.mode = 0o755 if name.endswith('.sh') else 0o644
                tar.addfile(member, io.BytesIO(data))
            if extra: extra(tar)
        return output.getvalue()

    def server(self, archive, wrong_checksum=False):
        digest = hashlib.sha256(archive).hexdigest()
        def download(url, path, limit, **kwargs):
            if url.endswith('/assets/2'):
                path.write_bytes(archive)
            elif url.endswith('/assets/3'):
                path.write_text(('0'*64 if wrong_checksum else digest)+'  homelab-source.tar.gz\n')
            else: raise AssertionError(url)
        metadata = {'tag_name': 'v1.0.0', 'id': 10, 'assets': [
            {'name': name, 'id': n, 'url': f'https://api.github.com/repos/example/lab/releases/assets/{n}', 'state': 'uploaded'}
            for name,n in [('homelab-source.tar.gz',2), ('SHA256SUMS',3)]]}
        return patch.object(r, 'release_metadata', return_value=metadata), patch.object(r, 'download', side_effect=download)

    def install(self, version='one', tag='v1.0.0', update=False, wrong_checksum=False):
        metadata, download = self.server(self.archive(version), wrong_checksum)
        with metadata, download, patch.object(r, 'confirm', return_value=True):
            return r.install_release('example/lab', self.target, self.state, tag, update)

    def test_download_extract_record_and_offline_resume_without_git(self):
        result = self.install()
        self.assertFalse((self.target/'.git').exists())
        self.assertEqual(result['schema'],2)
        self.assertTrue(os.access(self.target/'scripts/bootstrap.sh',os.X_OK))
        with patch.object(r,'download',side_effect=AssertionError('offline')):
            resumed=r.install_release('example/lab',self.target,self.state,'v1.0.0')
        self.assertEqual(resumed,result)
        self.assertEqual((self.state/'release.json').stat().st_mode & 0o777,0o600)

    def test_launcher_selects_release_changes_directory_and_clears_token_before_wizard(self):
        metadata,download=self.server(self.archive())
        cwd=Path.cwd()
        try:
            with metadata, download, patch.object(r,'require_platform'), patch.object(r,'latest_release',return_value='v1.0.0'), \
                 patch.object(r,'confirm',return_value=True), patch.dict(os.environ,{'GITHUB_TOKEN':'test-only-ephemeral-token'}), \
                 patch.object(sys,'argv',['install','--repo','example/lab','--target',str(self.target),'--state-dir',str(self.state),'--private']), \
                 patch.object(r.os,'execv') as execute:
                r.main()
                self.assertNotIn('GITHUB_TOKEN',os.environ)
                self.assertEqual(Path.cwd(),self.target)
                self.assertEqual(execute.call_args.args[1],[sys.executable,str(self.target/'scripts/lib/installer.py'),'--state-dir',str(self.state)])
                self.assertTrue(json.loads((self.state/'release.json').read_text())['private'])
        finally:os.chdir(cwd)

    def test_checksum_mismatch_never_installs_source(self):
        with self.assertRaises(r.InstallError):self.install(wrong_checksum=True)
        self.assertFalse(self.target.exists());self.assertFalse((self.state/'release.json').exists())

    def test_local_edits_extra_files_and_exec_bit_changes_block_updates(self):
        self.install()
        file=self.target/'scripts/bootstrap.sh';original=file.read_bytes()
        for change in ('content','extra','mode'):
            with self.subTest(change=change):
                if change=='content':file.write_text('local edit')
                if change=='extra':(self.target/'unexpected').write_text('keep')
                if change=='mode':file.chmod(0o644)
                with self.assertRaises(r.InstallError):self.install('two','v1.1.0',True)
                file.write_bytes(original);file.chmod(0o755)
                (self.target/'unexpected').unlink(missing_ok=True)

    def test_backup_failure_retains_source_and_record(self):
        original=self.install()
        with patch.object(r,'backup_before_update',side_effect=r.InstallError('failed')):
            with self.assertRaises(r.InstallError):self.install('two','v1.1.0',True)
        self.assertEqual((self.target/'install.sh').read_text(),'one')
        self.assertEqual(json.loads((self.state/'release.json').read_text()),original)
        self.assertFalse((self.state/'release-pending.json').exists())

    def test_update_preserves_host_links_and_old_source_after_backup(self):
        self.install();host=self.state/'host.env';host.write_text('DOMAIN=keep.test\n')
        link=self.target/'stacks/media/.env';link.symlink_to(host)
        def backup(target,state):
            self.assertEqual((target/'install.sh').read_text(),'one')
        with patch.object(r,'backup_before_update',side_effect=backup) as backup_mock:
            result=self.install('two','v1.1.0',True)
        backup_mock.assert_called_once()
        self.assertEqual(result['release'],'v1.1.0')
        self.assertEqual((self.target/'install.sh').read_text(),'two')
        self.assertEqual(link.resolve(),host)
        old=list(self.base.glob('.source-previous-*'));self.assertEqual(len(old),1)
        self.assertEqual((old[0]/'install.sh').read_text(),'one')
        self.assertEqual(host.read_text(),'DOMAIN=keep.test\n')

    def test_interrupted_directory_switch_preserves_previous_source_and_blocks_resume(self):
        self.install()
        rename=Path.rename
        def interrupted(path,destination):
            if path.name=='source' and '-stage-' in str(path.parent):raise OSError('simulated interrupted switch')
            return rename(path,destination)
        with patch.object(Path,'rename',interrupted):
            with self.assertRaises(OSError):self.install('two','v1.1.0',True)
        old=list(self.base.glob('.source-previous-*'))
        self.assertEqual(len(old),1)
        self.assertEqual((old[0]/'install.sh').read_text(),'one')
        self.assertTrue((self.state/'release-pending.json').exists())
        with self.assertRaises(r.InstallError):self.install('two','v1.1.0',True)

    def test_same_version_mutation_and_downgrade_are_refused(self):
        self.install()
        with self.assertRaises(r.InstallError):self.install('different','v1.0.0',True)
        with self.assertRaises(r.InstallError):self.install('old','v0.9.0',True)
        self.assertEqual((self.target/'install.sh').read_text(),'one')

    def test_unknown_target_and_pending_transaction_are_not_overwritten(self):
        self.target.mkdir();(self.target/'wanted').write_text('keep')
        with self.assertRaises(r.InstallError):self.install()
        self.assertEqual((self.target/'wanted').read_text(),'keep')
        (self.state/'release-pending.json').write_text('{}')
        with self.assertRaises(r.InstallError):self.install()

    def test_unsafe_archives_are_refused_before_extraction(self):
        for kind in ('traversal','absolute','symlink','hardlink','device','duplicate'):
            with self.subTest(kind=kind):
                def extra(tar):
                    member=tarfile.TarInfo({'traversal':'homelab/../escaped','absolute':'/escaped',
                        'duplicate':'homelab/install.sh'}.get(kind,'homelab/evil'))
                    if kind in ('symlink','hardlink'):member.type=tarfile.SYMTYPE if kind=='symlink' else tarfile.LNKTYPE;member.linkname='/etc/passwd'
                    if kind=='device':member.type=tarfile.CHRTYPE
                    tar.addfile(member)
                archive=self.base/'bad.tar.gz';archive.write_bytes(self.archive(extra=extra))
                out=self.base/('out-'+kind);out.mkdir()
                with self.assertRaises(r.InstallError):r.extract_source(archive,out)
                self.assertEqual(list(out.iterdir()),[])
        self.assertFalse((self.base/'escaped').exists())

    def test_token_removed_on_cdn_redirect_and_https_required(self):
        req=urllib.request.Request('https://api.github.com/repos/example/lab/releases/assets/1',headers={'Authorization':'Bearer private-test'})
        redirect=r.SafeRedirect().redirect_request(req,None,302,'',{},'https://release-assets.githubusercontent.com/asset')
        self.assertIsNone(redirect.get_header('Authorization'))
        with self.assertRaises(r.InstallError):r.SafeRedirect().redirect_request(req,None,302,'',{},'http://example.com/asset')

    def test_invalid_private_token_never_reaches_transport_or_error_output(self):
        token='private-test-token\naccidental-newline'
        with patch.dict(os.environ,{'GITHUB_TOKEN':token}),patch('urllib.request.build_opener') as opener:
            with self.assertRaises(r.InstallError) as result:
                r.download('https://api.github.com/repos/example/lab/releases/assets/1',self.base/'asset',100)
        opener.assert_not_called()
        self.assertNotIn('private-test-token',str(result.exception))

    def test_transport_header_errors_do_not_echo_credentials(self):
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=ValueError('Invalid header value: Bearer private-test-token')
            with self.assertRaises(r.InstallError) as result:
                r.download('https://api.github.com/repos/example/lab/releases/assets/1',self.base/'asset',100)
        self.assertNotIn('private-test-token',str(result.exception))

    def test_release_and_wizard_cannot_run_concurrently(self):
        with r.installation_lock(self.state):
            with self.assertRaises(r.InstallError):
                with r.installation_lock(self.state):pass


if __name__=='__main__':unittest.main()
