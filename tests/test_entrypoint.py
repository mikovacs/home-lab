"""Bash/bootstrap packaging tests; package installation is always mocked."""
import base64
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/lib'))
from package_release import package_installer


class EntrypointTests(unittest.TestCase):
    def shell(self, body, input=None):
        return subprocess.run(['bash', '-c', 'source ' + shlex.quote(str(ROOT / 'install.sh')) + '\n' + body],
                              input=input, text=True, capture_output=True, check=False)

    def test_help_and_dry_run_work_without_privilege_or_host_changes(self):
        for argument in ('--help', '--dry-run'):
            result = subprocess.run(['bash', str(ROOT / 'install.sh'), argument],
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('apt-get update', result.stdout)
        self.assertIn('DRY RUN', result.stdout)

    def test_unknown_or_incomplete_arguments_fail_before_preparation(self):
        for arguments in (['--unexpected'], ['--repo'], ['--state-dir', '--update']):
            result = subprocess.run(['bash', str(ROOT / 'install.sh'), *arguments],
                                    text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 2)
            self.assertIn('ERROR', result.stderr)

    def test_unsupported_platform_is_rejected(self):
        result = self.shell('uname() { printf "Darwin\\n"; }; check_platform')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Debian', result.stderr)

    def test_installed_dependencies_do_not_trigger_apt(self):
        result = self.shell('''dpkg-query() { printf 'installed'; }
apt-get() { printf 'UNEXPECTED APT'; return 90; }
ensure_runtime''')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('UNEXPECTED', result.stdout)

    def test_only_missing_packages_are_installed_after_confirmation(self):
        result = self.shell('''dpkg-query() {
  if [[ ${!#} == python3 ]]; then return 1; fi
  printf 'installed'
}
apt-get() { printf 'APT:%s\\n' "$*"; }
ensure_runtime''', input='yes\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('APT:update', result.stdout)
        self.assertIn('APT:install -y --no-install-recommends python3', result.stdout)
        self.assertNotIn('ca-certificates python3', result.stdout)

    def test_cancelling_prerequisites_never_calls_apt(self):
        result = self.shell('''dpkg-query() { return 1; }
apt-get() { printf 'UNEXPECTED APT'; }
ensure_runtime''', input='no\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('UNEXPECTED', result.stdout)

    def test_apt_failure_prevents_launch(self):
        result = self.shell('''dpkg-query() { return 1; }
apt-get() { return 87; }
ensure_runtime
printf 'UNEXPECTED LAUNCH' ''', input='yes\n')
        self.assertEqual(result.returncode, 87)
        self.assertNotIn('UNEXPECTED', result.stdout)

    def test_release_asset_contains_exact_launcher_and_valid_shell(self):
        with tempfile.TemporaryDirectory() as temporary:
            packed = package_installer(Path(temporary) / 'install.sh')
            syntax = subprocess.run(['bash', '-n', str(packed)], text=True, capture_output=True)
            self.assertEqual(syntax.returncode, 0, syntax.stderr)
            result = subprocess.run(['bash', '-c', 'source ' + shlex.quote(str(packed)) + '; embedded_launcher'],
                                    text=True, capture_output=True, check=True)
            decoded = base64.b64decode(result.stdout, validate=False)
            self.assertEqual(decoded, (ROOT / 'scripts/lib/releases.py').read_bytes())
            compile(decoded, 'bundled-release-manager', 'exec')
            self.assertTrue(os.access(packed, os.X_OK))
            help_result = subprocess.run(['bash', str(packed), '--help'], text=True, capture_output=True)
            self.assertEqual(help_result.returncode, 0)
            self.assertIn('--repo', help_result.stdout)


if __name__ == '__main__': unittest.main()
