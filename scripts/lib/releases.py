#!/usr/bin/env python3
"""Download and verify published release assets; keep host state outside source."""
import argparse
import contextlib
import fcntl
import getpass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid


class InstallError(RuntimeError):
    pass


def need(ok, message):
    if not ok:
        raise InstallError(message)


def ask(message, default=''):
    value = input(message + (f' [{default}]' if default else '') + ': ').strip()
    return value or default


def confirm(message):
    return ask(message + ' (yes/no)', 'no').lower() == 'yes'


def command(args, env=None):
    subprocess.run(list(map(str, args)), check=True, env=env)


def repository_name(value):
    value = re.sub(r'^https://github\.com/', '', value.strip()).removesuffix('.git').rstrip('/')
    need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', value),
         'Use owner/repository or an HTTPS github.com repository URL without credentials.')
    return value.lower()


def release_tag(value):
    need(re.fullmatch(r'v[0-9]+\.[0-9]+\.[0-9]+', value), 'Only stable vMAJOR.MINOR.PATCH releases are accepted.')
    return value


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        need(urllib.parse.urlsplit(newurl).scheme == 'https', 'Release downloads require HTTPS.')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and urllib.parse.urlsplit(req.full_url).netloc != urllib.parse.urlsplit(newurl).netloc:
            redirected.remove_header('Authorization')
        return redirected


def download(url, destination, limit, accept='application/octet-stream'):
    # Tokens go only to the GitHub API, never release CDN redirects or process arguments.
    parsed = urllib.parse.urlsplit(url)
    need(parsed.scheme == 'https' and parsed.netloc == 'api.github.com' and not parsed.fragment,
         'Expected a GitHub API release asset URL.')
    headers = {'User-Agent': 'HomeLab-installer', 'Accept': accept}
    token = os.environ.get('GITHUB_TOKEN', '')
    if token:
        need(all(33 <= ord(character) <= 126 for character in token),
             'GitHub token contains invalid whitespace or control characters; no credential was logged.')
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.build_opener(SafeRedirect()).open(request, timeout=60) as response, open(destination, 'wb') as out:
            total = 0
            while chunk := response.read(1024 * 1024):
                total += len(chunk)
                need(total <= limit, 'Release download exceeds the allowed size.')
                out.write(chunk)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
        if isinstance(exc, urllib.error.HTTPError): exc.close()
        raise InstallError('Release download failed; check repository access and connectivity.') from None


def release_metadata(repo, tag=None):
    repo = repository_name(repo)
    suffix = 'tags/' + release_tag(tag) if tag else 'latest'
    with tempfile.TemporaryDirectory(prefix='homelab-metadata-') as temporary:
        path = Path(temporary) / 'release.json'
        download('https://api.github.com/repos/' + repo + '/releases/' + suffix, path, 2 * 1024**2, accept='application/vnd.github+json')
        data = json.loads(path.read_text())
    need(not data.get('draft') and not data.get('prerelease'), 'A published stable release is required.')
    actual = release_tag(data.get('tag_name', ''))
    need(tag is None or actual == tag, 'Release metadata differs from the selected version.')
    return data


def latest_release(repo):
    return release_tag(release_metadata(repo)['tag_name'])


def safe_directory(value):
    p = Path(value)
    need(p.is_absolute() and p.resolve() == p and str(p) not in ('/', '/srv', '/home', '/etc', '/tmp', '/var'),
         'Choose a dedicated absolute directory without symlinks.')
    need(not any(x in str(p) for x in (' ', '\n', '\r', '\t', ':')), 'Directory must not contain whitespace or colons.')
    need(not any(str(p) == x or str(p).startswith(x + '/') for x in ('/dev', '/proc', '/sys', '/usr', '/boot', '/bin', '/sbin', '/lib', '/lib64', '/var/lib/docker')),
         'Protected system path is not an installation directory.')
    return p


def write_json(path, data):
    need(not path.is_symlink(), 'Refusing a symlinked installer state file.')
    fd, name = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as out:
            json.dump(data, out, indent=2); out.write('\n')
        os.chmod(name, 0o600)
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def source_manifest(target, state_dir):
    files = {}
    for path in sorted(target.rglob('*')):
        rel = path.relative_to(target).as_posix()
        if path.is_symlink():
            need(re.fullmatch(r'stacks/[a-z-]+/\.env', rel) and path.resolve() == (state_dir / 'host.env').resolve(),
                 'Unexpected symlink in release source: ' + rel)
            continue
        if path.is_dir(): continue
        need(path.is_file(), 'Unexpected special file in release source: ' + rel)
        files[rel] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                      'mode': path.stat().st_mode & 0o777}
    return files


def clean_source(target, repo, state_dir, state):
    need(state.get('schema') == 2 and state.get('repository') == repository_name(repo)
         and state.get('target') == str(target), 'Target is not a recorded asset installation; no files will be overwritten.')
    need(source_manifest(target, state_dir) == state.get('files'),
         'Release source contains local changes. Review them; the installer never discards source edits.')


def extract_source(archive, destination):
    # No extractall: reject traversal, links, devices, duplicates and archive bombs before writing.
    with tarfile.open(archive, 'r:gz') as source:
        members = []; seen = set(); total = 0
        for member in source:
            path = PurePosixPath(member.name)
            need(not path.is_absolute() and '..' not in path.parts and path.parts and path.parts[0] == 'homelab',
                 'Unsafe release archive path.')
            need(member.isfile() or member.isdir(), 'Release archives cannot contain links or special files.')
            need(path.as_posix() not in seen and len(seen) < 10000, 'Duplicate or excessive archive entries.')
            seen.add(path.as_posix()); total += member.size
            need(total <= 128 * 1024**2, 'Expanded release exceeds the allowed size.')
            if len(path.parts) == 1:
                need(member.isdir(), 'Expected a homelab archive directory.'); continue
            need('.git' not in path.parts and (path.name != '.env'), 'Host/Git state is forbidden in release assets.')
            members.append((member, destination.joinpath(*path.parts[1:])))
        for member, path in members:
            if member.isdir(): path.mkdir(parents=True, exist_ok=True, mode=0o755)
            else:
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                with source.extractfile(member) as inp, path.open('xb') as out: shutil.copyfileobj(inp, out)
                path.chmod(0o755 if member.mode & 0o111 else 0o644)
    for name in ('install.sh', '.env.example', 'scripts/bootstrap.sh', 'scripts/lib/homelab.py', 'scripts/lib/installer.py', 'scripts/lib/releases.py'):
        need((destination / name).is_file(), 'Incomplete release asset: ' + name)
    need(os.access(destination / 'scripts/bootstrap.sh', os.X_OK), 'Release bootstrap must be executable.')


def fetch_source(repo, tag, destination):
    data = release_metadata(repo, tag)
    assets = data.get('assets', [])
    def asset(name):
        matches = [a for a in assets if a.get('name') == name and a.get('state') == 'uploaded']
        need(len(matches) == 1, 'Release must contain exactly one uploaded ' + name)
        value = matches[0]
        need(re.fullmatch(r'https://api\.github\.com/repos/' + re.escape(repo) + r'/releases/assets/[0-9]+', value.get('url', ''), re.IGNORECASE),
             'Asset URL is outside the selected repository.')
        return value
    archive_asset, checksum_asset = asset('homelab-source.tar.gz'), asset('SHA256SUMS')
    with tempfile.TemporaryDirectory(prefix='homelab-download-') as temporary:
        archive, sums = Path(temporary) / 'source.tar.gz', Path(temporary) / 'SHA256SUMS'
        download(checksum_asset['url'], sums, 1024**2)
        matches = [m.group(1).lower() for line in sums.read_text().splitlines()
                   if (m := re.fullmatch(r'([a-fA-F0-9]{64}) [ *]homelab-source\.tar\.gz', line))]
        need(len(matches) == 1, 'Missing or ambiguous source archive checksum.')
        download(archive_asset['url'], archive, 64 * 1024**2)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        need(digest == matches[0], 'Release archive checksum mismatch; nothing installed.')
        extract_source(archive, destination)
    return {'archive_sha256': digest, 'release_id': data['id'], 'asset_id': archive_asset['id']}


def backup_before_update(target, state_dir):
    config = state_dir / 'host.env'
    if not config.exists(): return
    values = {}
    for line in config.read_text().splitlines():
        if line and not line.startswith('#') and '=' in line:
            key, value = line.split('=', 1); values[key] = value
    marker = Path(values.get('RUNTIME_ROOT', '/nonexistent')) / 'install-state.json'
    if not marker.exists():
        appdata = Path(values.get('APPDATA_ROOT', '/nonexistent'))
        need(not appdata.exists() or appdata.is_dir() and not any(appdata.iterdir()),
             'Existing application state has no installation marker; review before updating.')
        return
    env = dict(os.environ, HOMELAB_ENV_FILE=str(config))
    env.pop('GITHUB_TOKEN', None)
    print('Backing up with the installed release. Failure blocks the update.')
    command([sys.executable, target / 'scripts/lib/homelab.py', 'backup'], env=env)


def install_release(repo, target, state_dir, tag, update=False):
    repo, tag = repository_name(repo), release_tag(tag)
    target, state_dir = safe_directory(str(target)), safe_directory(str(state_dir))
    need(target != state_dir and target not in state_dir.parents and state_dir not in target.parents,
         'Installer configuration and source must not overlap.')
    manifest, pending = state_dir / 'release.json', state_dir / 'release-pending.json'
    need(not manifest.is_symlink(), 'Release state must not be a symlink.')
    need(not pending.exists(), 'Interrupted release switch: inspect release-pending.json and preserved source before continuing.')
    state = json.loads(manifest.read_text()) if manifest.exists() else {}
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    need(state_dir.stat().st_uid == os.geteuid() and state_dir.stat().st_mode & 0o077 == 0,
         'Installer configuration must be owned by this process and have mode 0700.')
    if target.exists():
        clean_source(target, repo, state_dir, state)
        if not update: return state
        old = tuple(map(int, release_tag(state.get('release', ''))[1:].split('.')))
        new = tuple(map(int, tag[1:].split('.')))
        need(new >= old, 'Automatic source downgrade is prohibited; use staged recovery.')
    else:
        need(not state, 'Recorded release source is missing; recover it before installing elsewhere.')
    if target.exists() and tag != state['release']:
        need(confirm(f'Update {state["release"]} -> {tag}? Backup runs before source switch'), 'Update cancelled.')
    elif not target.exists():
        need(confirm(f'Download and trust {repo} release {tag} into {target}?'), 'Installation cancelled.')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.' + target.name + '-stage-', dir=target.parent) as temporary:
        stage = Path(temporary) / 'source'; stage.mkdir(mode=0o755)
        identity = fetch_source(repo, tag, stage)
        if target.exists() and tag == state['release']:
            need(identity['archive_sha256'] == state['archive_sha256'], 'Published release asset changed; refusing a mutable release.')
            return state
        files = source_manifest(stage, state_dir)
        if target.exists():
            backup_before_update(target, state_dir)
            clean_source(target, repo, state_dir, state)
            for link in target.glob('stacks/*/.env'):
                need(link.is_symlink() and link.resolve() == (state_dir / 'host.env').resolve(), 'Invalid host environment link.')
                (stage / link.relative_to(target)).symlink_to(state_dir / 'host.env')
        new_state = {'schema': 2, 'repository': repo, 'target': str(target), 'release': tag,
                     'private': state.get('private', False), 'files': files, **identity}
        previous = target.with_name('.' + target.name + '-previous-' + uuid.uuid4().hex) if target.exists() else None
        write_json(pending, {'target': str(target), 'previous_source': str(previous) if previous else None,
                             'new_release': new_state})
        if previous: target.rename(previous)
        stage.rename(target)
        write_json(manifest, new_state)
        pending.unlink()
        if previous: print('Previous source retained at ' + str(previous))
        return new_state


@contextlib.contextmanager
def installation_lock(state_dir):
    state_dir.mkdir(parents=True, mode=0o700, exist_ok=True)
    need(state_dir.stat().st_uid == os.geteuid() and state_dir.stat().st_mode & 0o077 == 0,
         'Installer state directory must be owned by this process and have mode 0700.')
    with contextlib.ExitStack() as stack:
        for name in ('release.lock', 'wizard.lock'):
            path = state_dir / name
            need(not path.is_symlink(), 'Installer lock must not be a symlink.')
            lock = stack.enter_context(path.open('a'))
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError: raise InstallError('Another installer is running.') from None
        yield


def require_platform():
    need(sys.stdin.isatty(), 'Run the downloaded installer interactively, not through a pipe.')
    need(sys.platform.startswith('linux') and os.geteuid() == 0, 'Run on Debian through sudo bash install.sh.')
    os_info = Path('/etc/os-release').read_text()
    need(re.search(r'^ID=debian$', os_info, re.MULTILINE) and re.search(r'^VERSION_CODENAME=trixie$', os_info, re.MULTILINE),
         'The installer targets Debian 13 trixie.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', help='GitHub owner/repository')
    parser.add_argument('--private', action='store_true', help='prompt for an ephemeral GitHub release-read token')
    parser.add_argument('--target', help='release source directory (default: recorded target or /srv/homelab/repository)')
    parser.add_argument('--state-dir', default='/etc/homelab')
    parser.add_argument('--update', action='store_true', help='review stable release assets; backup before switching source')
    args = parser.parse_args()
    require_platform()
    state_dir = safe_directory(args.state_dir)
    with installation_lock(state_dir):
        record = state_dir / 'release.json'
        need(not record.is_symlink(), 'Release state must not be a symlink.')
        previous = json.loads(record.read_text()) if record.exists() else {}
        target = safe_directory(args.target or previous.get('target') or '/srv/homelab/repository')
        need(not target.exists() or previous, 'Existing target is not a recorded asset installation; no files will be overwritten.')
        repo = repository_name(args.repo or previous.get('repository') or ask('GitHub repository (owner/repository)'))
        private = args.private or previous.get('private', False)
        try:
            if private and (args.update or not target.exists()) and not os.environ.get('GITHUB_TOKEN'):
                token = getpass.getpass('GitHub release-read token (hidden, not saved): ')
                need(bool(token), 'A private-source GitHub token is required.')
                os.environ['GITHUB_TOKEN'] = token
            tag = latest_release(repo) if args.update or not target.exists() else release_tag(previous.get('release', ''))
            state = install_release(repo, target, state_dir, tag, args.update)
            state['private'] = private
            write_json(record, state)
        finally:
            os.environ.pop('GITHUB_TOKEN', None)
    print('Using release ' + state['release'] + ' (archive ' + state['archive_sha256'][:12] + ').')
    os.chdir(target)
    os.execv(sys.executable, [sys.executable, str(target / 'scripts/lib/installer.py'), '--state-dir', str(state_dir)])


if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: sys.exit('\nCancelled. No storage deletion performed; inspect any pending release switch.')
    except (InstallError, OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        sys.exit('ERROR: ' + str(exc))
