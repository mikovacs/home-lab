#!/usr/bin/env python3
"""Resumable interactive HomeLab installer. Host data stays outside release source."""
import argparse
import getpass
import http.client
import ipaddress
import json
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import sys

import homelab as h
from installer_api import (APIError, JsonAPI, AdGuardAPI, adguard, authentik, cloudflare, create_local_tunnel,
                           ensure_dns, validate_tunnel_credentials,
                           verify_local_tunnel, wait_for_identity, zone_for_domain)

REMOTE = {'auth': 'identity', 'home': 'management', 'files': 'storage', 'radarr': 'media',
          'sonarr': 'media', 'prowlarr': 'media', 'torrent': 'media', 'n8n': 'automation',
          'ai': 'ai', 'status': 'monitoring', 'hooks': 'automation'}
APP_STACKS = ('media', 'storage', 'monitoring', 'management', 'automation', 'ai')


class Back(Exception):
    pass


def prompt(label, default='', validate=None):
    while True:
        value = input(label + (f' [{default}]' if default != '' else '') + ': ').strip()
        if value.lower() in ('back', 'cancel'):
            raise Back()
        value = value or str(default)
        if validate is None or validate(value):
            return value
        print('Invalid value. Try again, or type back to return to the menu.')


def yes(label, default=False):
    return prompt(label + ' (yes/no)', 'yes' if default else 'no',
                  lambda x: x.lower() in ('yes', 'no')).lower() == 'yes'


def secret(label, existing=None):
    if existing and existing.exists():
        print(label + ': existing protected credential retained.')
        return existing.read_text().strip()
    value = getpass.getpass(label + ' (hidden; blank cancels): ')
    if not value:
        raise Back()
    h.require('\n' not in value and '\r' not in value, 'Credentials cannot contain line breaks')
    return value


def new_password(label):
    while True:
        first = secret(label)
        second = getpass.getpass('Repeat password: ')
        if len(first) >= 14 and first.strip() and first == second:
            return first
        print('Use at least 14 characters and enter the same password twice.')


def hash_authentik_password(c, password):
    h.require(len(password) >= 14 and '\n' not in password and '\r' not in password, 'Use at least 14 characters, without line breaks')
    # Both the image entrypoint and manage.py wait for PostgreSQL, which is not
    # running during credential setup. Load only the pinned image's hash policy.
    script = ('import os; os.environ["DJANGO_SETTINGS_MODULE"] = "authentik.root.settings"; '
              'from django.contrib.auth.hashers import make_password; print(make_password(input()))')
    output = h.compose(c, 'identity', 'run', '--rm', '--no-deps', '-T', '--entrypoint', 'python',
                       'server', '-c', script,
                       capture=True, stdin_text=password + '\n')
    hashes = [line.strip() for line in output.splitlines()
              if line.strip().startswith(('pbkdf2_sha256$', 'argon2$', 'bcrypt_sha256$', 'scrypt$'))]
    h.require(len(hashes) == 1 and "'" not in hashes[0], 'Pinned Authentik image did not produce one supported password hash')
    return hashes[0]


def selected_apps(value):
    if value.strip().lower() == 'none': return []
    names = [x.strip() for x in value.split(',') if x.strip()]
    h.require(len(names) == len(set(names)) and set(names) <= set(REMOTE),
              'Select only listed remote application names; private administration/Plex are prohibited.')
    h.require(not (set(names) - {'auth', 'hooks'}) or 'auth' in names,
              'Include auth so remote users can reach the identity provider.')
    return names


def save_environment(path, values):
    h.require(path.is_absolute() and path.resolve() == path, 'Host environment path cannot be symlinked')
    for key, value in values.items():
        h.require(re.fullmatch(r'[A-Z][A-Z0-9_]*', key) and isinstance(value, str)
                  and not any(c in value for c in ('\n', '\r', '$', '`', '"', "'", '\\')),
                  'Invalid unquoted setting: ' + key)
    h.validate_paths(values)
    previous = h.read_env(path) if path.exists() else {}
    roots = {values['RUNTIME_ROOT'], previous.get('RUNTIME_ROOT', values['RUNTIME_ROOT'])}
    for root in roots:
        marker = Path(root) / 'install-state.json'
        if not marker.exists(): continue
        identity = json.loads(marker.read_text()).get('identity', {})
        h.require(identity and all(values.get(k) == v for k, v in identity.items()),
                  'Prepared storage/service identity cannot be changed in the wizard; review migration separately.')
        h.require(not previous or all(values.get(k) == previous.get(k)
                  for k in ('DOMAIN', 'LAN_IP', 'LAN_CIDR', 'LAN_INTERFACE')),
                  'Prepared network/domain changes require a separate DNS, identity and route migration; settings retained.')
    h.atomic(path, '\n'.join(k + '=' + v for k, v in values.items()) + '\n', 0o600)


def tls_origin(c, host):
    # Verify the real hostname against the local origin; public DNS need not exist yet.
    context = ssl.create_default_context()
    with socket.create_connection((c['LAN_IP'], 443), timeout=15) as raw:
        with context.wrap_socket(raw, server_hostname=host) as connection:
            path = '/webhook/__homelab_probe__' if host == 'hooks.' + c['DOMAIN'] else '/'
            # Probe the browser route: some apps return 404 for HEAD but serve GET.
            connection.sendall(('GET ' + path + ' HTTP/1.1\r\nHost: ' + host + '\r\nConnection: close\r\n\r\n').encode())
            with http.client.HTTPResponse(connection) as response:
                try:
                    response.begin()
                except http.client.HTTPException:
                    raise h.SafetyError(f'Invalid HTTP response from origin {host}; publication stopped.') from None
                status = response.status
                # Do not follow redirects or download the application response body.
    h.require(status in (200, 204, 301, 302, 303, 307, 308, 401, 403)
              or host == 'hooks.' + c['DOMAIN'] and status == 404,
              f'Origin is not ready for {host} (HTTP {status}); check service, TLS and local routing before publishing.')


class Wizard:
    def __init__(self, state_dir):
        self.directory = state_dir
        self.file = state_dir / 'wizard.json'
        self.environment = state_dir / 'host.env'
        self.state = json.loads(self.file.read_text()) if self.file.exists() else {'completed': [], 'selected': []}
        os.environ['HOMELAB_ENV_FILE'] = str(self.environment)

    def mark(self, step):
        self.state['completed'] = sorted(set(self.state.get('completed', []) + [step]))
        h.atomic(self.file, json.dumps(self.state, indent=2) + '\n', 0o600)

    def c(self):
        return h.config()

    def require_step(self, *steps):
        for step in steps:
            h.require(step in self.state.get('completed', []), 'Complete step ' + step + ' first.')

    def controller(self, action, *args):
        h.run([sys.executable, h.ROOT / 'scripts/lib/homelab.py', action, *args])

    def settings(self):
        print('\nHost settings. Values are saved outside Git; use back to cancel this form.')
        print('Read-only discovery for this form (keep device serials private):')
        for command in (['ip', '-brief', 'address'], ['ip', 'route'],
                        ['lsblk', '-o', 'NAME,MODEL,SERIAL,SIZE,FSTYPE,UUID,MOUNTPOINTS']):
            if h.shutil.which(command[0]):
                h.run(command)
            else:
                print(command[0] + ' is unavailable; bootstrap can install it before you return.')
        c = h.read_env(h.ROOT / '.env.example')
        if self.environment.exists(): c.update(h.read_env(self.environment))
        c.update(REPO_ROOT=str(h.ROOT), HOMELAB_CONFIG_DIR=str(self.directory))
        c['DOMAIN'] = prompt('Service domain (example: lab.yourdomain.tld)',
                            '' if c['DOMAIN'] == 'example.com' else c['DOMAIN'],
                            lambda x: bool(re.fullmatch(r'[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', x)) and '.' in x and x != 'example.com')
        def ipv4(value):
            try:
                address = ipaddress.ip_address(value)
                return address.version == 4 and address.is_private and not address.is_loopback and not address.is_link_local
            except ValueError: return False
        c['LAN_IP'] = prompt('Reserved server LAN IPv4', '' if c['LAN_IP'] == '192.0.2.10' else c['LAN_IP'], ipv4)
        c['LAN_INTERFACE'] = prompt('LAN interface (see ip -brief address)',
                                   '' if c['LAN_INTERFACE'] == 'SET_ME' else c['LAN_INTERFACE'],
                                   lambda x: bool(re.fullmatch(r'[A-Za-z0-9_.-]{1,15}', x)))
        while True:
            try:
                c['LAN_CIDR'] = str(ipaddress.IPv4Network(prompt('Actual LAN CIDR', '' if c['LAN_CIDR'] == '192.0.2.0/24' else c['LAN_CIDR'])))
                h.require(ipaddress.ip_address(c['LAN_IP']) in ipaddress.ip_network(c['LAN_CIDR']), 'Server IP must belong to LAN CIDR')
                break
            except ValueError: print('Use an IPv4 network CIDR, such as 192.168.1.0/24.')
        c['ADMIN_CIDRS'] = prompt('Private administration source CIDRs', c['LAN_CIDR'] + ',100.64.0.0/10')
        ranges = [ipaddress.ip_network(x) for x in c['ADMIN_CIDRS'].split(',')]
        h.require(all(x.prefixlen > 0 for x in ranges), 'Default-route administration allowlists are forbidden')
        c['ACME_EMAIL'] = prompt('Certificate contact email', '' if c['ACME_EMAIL'] == 'admin@example.com' else c['ACME_EMAIL'],
                                 lambda x: bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', x)))
        c['ACME_SERVER'] = 'https://acme-v02.api.letsencrypt.org/directory'
        print('Production DNS-01 certificates will be requested after credentials are verified. No inbound router forwarding is needed.')
        previous_data = c['DATA_ROOT']
        for key in ('APPDATA_ROOT', 'SECRETS_ROOT', 'RUNTIME_ROOT', 'DATA_ROOT', 'MEDIA_ROOT'):
            c[key] = prompt(key, c[key])
        if c['RESTIC_REPOSITORY'] == previous_data + '/backups/restic':
            c['RESTIC_REPOSITORY'] = c['DATA_ROOT'] + '/backups/restic'
        for key in ('DATA_UUID', 'MEDIA_UUID'):
            c[key] = prompt(key + ' (from lsblk, never a /dev/sdX name)', '' if c[key] == 'SET_ME' else c[key],
                            lambda x: bool(re.fullmatch(r'[A-Za-z0-9-]+', x)) and x != 'SET_ME')
        h.require(c['DATA_UUID'] != c['MEDIA_UUID'], 'Use different media and secondary-data UUIDs')
        c['ADMIN_SSH_USER'] = prompt('Existing non-root SSH administrator', os.environ.get('SUDO_USER', ''),
                                     lambda x: bool(re.fullmatch(r'[a-z_][a-z0-9_-]*', x)) and x != 'root')
        import pwd
        try:
            administrator = pwd.getpwnam(c['ADMIN_SSH_USER'])
        except KeyError:
            raise h.SafetyError('Create the non-root OS administrator and verify key-based SSH before continuing.') from None
        h.require(administrator.pw_uid > 0, 'SSH administrator must be non-root')
        c['ADMIN_SSH_HOST'] = c['LAN_IP']
        # First-run remote exposure remains off; revisiting settings does not silently reopen it.
        if not self.environment.exists(): c.update(IDENTITY_READY='no', REMOTE_READY='no', REMOTE_APPS='')
        save_environment(self.environment, c)
        self.mark('settings')
        print('Saved host.env with mode 0600. No tracked source files changed.')

    def bootstrap(self):
        print('\nInstall Debian host tools, Docker Engine/Compose, security updates and time sync. No disks or application containers are changed.')
        h.run([h.ROOT / 'scripts/bootstrap.sh', '--dry-run'])
        if yes('Install these prerequisites now?'):
            h.run([h.ROOT / 'scripts/bootstrap.sh'])
            self.mark('bootstrap')

    def storage(self):
        self.require_step('settings', 'bootstrap')
        c = self.c()
        h.run(['lsblk', '-o', 'NAME,MODEL,SERIAL,SIZE,FSTYPE,UUID,MOUNTPOINTS'])
        h.mount_storage(c, dry=True)
        print('Data UUID:', c['DATA_UUID'], '->', c['DATA_ROOT'])
        print('Retained media UUID:', c['MEDIA_UUID'], '->', c['MEDIA_ROOT'])
        entered = prompt('To mount and persist these TWO existing filesystems, type the retained MEDIA_UUID (back cancels)')
        h.require(entered == c['MEDIA_UUID'], 'Confirmation did not match; no storage changes made')
        h.mount_storage(c, confirmed=True)
        h.configure_storage(c, confirmed=True)
        path = Path(c['MEDIA_ROOT']) / 'media/movies'
        info = path.stat()
        print(f'Retained movie directory ownership: UID={info.st_uid}, GID={info.st_gid}, mode={oct(info.st_mode & 0o777)}')
        print('Select numeric IDs compatible with the retained permissions; no recursive changes are performed.')
        c['PUID'] = prompt('Container media UID', c['PUID'] if info.st_uid == 0 else str(info.st_uid), lambda x: x.isdigit() and int(x) > 0)
        c['PGID'] = prompt('Container media GID', c['PGID'] if info.st_gid == 0 else str(info.st_gid), lambda x: x.isdigit() and int(x) > 0)
        save_environment(self.environment, c)
        h.check_media(c)
        self.mark('storage')

    def setup(self):
        self.require_step('storage')
        self.controller('setup', '--dry-run')
        if yes('Create missing host identities, folders, encrypted-backup keys and Docker networks (no app startup)?'):
            self.controller('setup')
            self.mark('setup')

    def credentials(self):
        self.require_step('setup')
        c = self.c(); root = Path(c['SECRETS_ROOT'])
        print('\nCloudflare DNS token: Zone DNS Edit + Zone Read, restricted to your zone. The token is hidden and stored only in secrets.')
        dns = secret('Cloudflare DNS API token', root / 'cloudflare-dns-token')
        zone_name = prompt('Cloudflare zone name (registered domain, not necessarily the service subdomain)',
                           self.state.get('cloudflare', {}).get('zone_name', ''))
        zone = zone_for_domain(cloudflare(dns), zone_name, c['DOMAIN'])
        h.atomic(root / 'cloudflare-dns-token', dns + '\n', 0o600)
        self.state['cloudflare'] = {'zone_name': zone_name, 'zone_id': zone['id'], 'account_id': zone['account']['id']}
        basic = root / 'admin-htpasswd'
        if not basic.exists():
            username = prompt('Private dashboard login name', 'homelab-admin', lambda x: bool(re.fullmatch(r'[a-zA-Z0-9_-]+', x)))
            password = new_password('Private dashboard password')
            hashed = h.run(['htpasswd', '-niB', username], capture=True, input=password + '\n')
            h.require(hashed.startswith(username + ':$2'), 'htpasswd did not return a bcrypt entry')
            h.atomic(basic, hashed, 0o600)
        bootstrap = root / 'authentik-bootstrap.env'
        if not bootstrap.read_text().strip():
            h.require(not (Path(c['APPDATA_ROOT']) / 'authentik-postgres/PG_VERSION').exists(),
                      'Authentik is already initialized. Use its existing administrator/API token; bootstrap will not reset it.')
            password = new_password('New Authentik akadmin password')
            token = h.secrets.token_hex(32)
            print('Generating the password hash with the pinned Authentik image (one-off command, no server/worker startup).')
            hashed = hash_authentik_password(c, password)
            h.atomic(bootstrap, "AUTHENTIK_BOOTSTRAP_PASSWORD_HASH='" + hashed + "'\nAUTHENTIK_BOOTSTRAP_TOKEN=" + token
                     + '\nAUTHENTIK_BOOTSTRAP_EMAIL=' + c['ACME_EMAIL'] + '\n', 0o600)
        adguard_file = root / 'adguard-admin.json'
        if not adguard_file.exists():
            username = prompt('New AdGuard administrator login', 'homelab-admin', lambda x: bool(re.fullmatch(r'[a-zA-Z0-9_-]+', x)))
            password = new_password('New AdGuard native administrator password')
            upstreams = [x.strip() for x in prompt('AdGuard DNS upstreams (comma-separated)', 'https://cloudflare-dns.com/dns-query').split(',') if x.strip()]
            h.require(upstreams, 'At least one DNS upstream is required')
            h.atomic(adguard_file, json.dumps({'username': username, 'password': password, 'upstreams': upstreams}) + '\n', 0o600)
        print('Credentials saved privately. Authentik akadmin will initialize with your chosen password; its API token is never printed.')
        self.mark('credentials')

    def foundation(self):
        self.require_step('credentials')
        self.controller('check-install', '--stack', 'infrastructure')
        self.controller('check-install', '--stack', 'identity')
        if yes('Start Traefik, AdGuard and Authentik/PostgreSQL now?'):
            self.controller('deploy-foundation')
            self.mark('foundation')

    def identity(self):
        self.require_step('foundation')
        c = self.c()
        dns = json.loads((Path(c['SECRETS_ROOT']) / 'adguard-admin.json').read_text())
        if yes('Configure AdGuard native admin, chosen upstreams and exact split-DNS service names by local API?'):
            names = list(REMOTE) + ['plex', 'bazarr', 'traefik', 'adguard', 'monitor', 'docker', 'terminal']
            adguard(AdGuardAPI(dns['username'], dns['password']), dns['username'], dns['password'],
                    c['DOMAIN'], c['LAN_IP'], names, dns['upstreams'])
            self.mark('dns-provisioned')
        self.require_step('dns-provisioned')
        bootstrap = Path(c['SECRETS_ROOT']) / 'authentik-bootstrap.env'
        lines = dict(line.split('=', 1) for line in bootstrap.read_text().splitlines() if '=' in line)
        token = lines.get('AUTHENTIK_BOOTSTRAP_TOKEN') or secret('Existing Authentik administrator API token (session only)')
        api = JsonAPI('http://127.0.0.1:9000/api/v3', token)
        print('Waiting for local Authentik bootstrap; a failed request never publishes any routes.')
        wait_for_identity(api, progress=print)
        if yes('Create HomeLab groups, group-gated app providers, outpost links and required MFA enrollment?'):
            authentik(api, c['DOMAIN'], progress=print)
            self.mark('identity-provisioned')
        self.require_step('identity-provisioned')
        print('\nOpen https://auth.' + c['DOMAIN'] + ' locally; log in as akadmin with your chosen password.')
        print('Enroll MFA on your device, save recovery codes offline, and test one allowed and one denied user.')
        print('AdGuard owner, upstreams and exact service rewrites were configured through its loopback API. Verify DNS from a LAN client.')
        print('Configure router DHCP DNS and split-DNS rewrites only after testing; SSH by numeric LAN IP must still work.')
        if yes('Have you completed MFA enrollment, policy tests and local AdGuard/DNS onboarding?'):
            c['IDENTITY_READY'] = 'yes'; save_environment(self.environment, c)
            self.controller('render')
            self.mark('identity')

    def applications(self):
        self.require_step('identity')
        choices = prompt('Stacks to start (comma-separated; AI opt-in)',
                         ','.join(self.state.get('selected') or ['media', 'storage', 'monitoring', 'management', 'automation']))
        names = [x.strip() for x in choices.split(',') if x.strip()]
        h.require(len(names) == len(set(names)) and set(names) <= set(APP_STACKS), 'Choose only listed application stacks')
        c = self.c()
        for name in names: h.gates(c, name)
        profiles = prompt('Persistent optional Compose profiles (bazarr,admin; none disables both)',
                          c.get('COMPOSE_PROFILES') or 'none')
        profiles = '' if profiles == 'none' else profiles
        h.configured_profiles({**c, 'COMPOSE_PROFILES': profiles})
        available = set(names) | set(self.state.get('selected', []))
        h.require('bazarr' not in profiles.split(',') or 'media' in available,
                  'Select the media stack for Bazarr')
        h.require('admin' not in profiles.split(',') or 'management' in available,
                  'Select the management stack for Dockge')
        print('Selected:', ', '.join(names) or '(none)')
        if not yes('Start these stacks now?'): return
        c['COMPOSE_PROFILES'] = profiles
        save_environment(self.environment, c)
        if c['REMOTE_APPS']:
            print('Closing current remote routes during application changes; re-enable only after local onboarding.')
            self.state['desired_remote_apps'] = c['REMOTE_APPS'].split(',')
            c.update(REMOTE_APPS='', REMOTE_READY='no')
            save_environment(self.environment, c)
            self.controller('render')
            h.compose(c, 'infrastructure', '--profile', 'tunnel', 'stop', 'cloudflared')
        self.state['native_stacks'] = sorted(set(self.state.get('native_stacks', [])) - set(names))
        self.state['completed'] = [x for x in self.state['completed'] if x != 'native-accounts']
        self.mark('applications-changing')
        for name in names:
            self.controller('deploy', '--stack', name)
            self.state['selected'] = sorted(set(self.state.get('selected', []) + [name]))
            self.mark('applications')
        print('\nLocal application setup checklist:')
        print('Plex: sign in/claim server, add /data/media/movies and /data/media/tv; do not redownload or reorganize media.')
        print('*arr/qBittorrent: native login/API keys, movies/tv categories, one /data parent and hardlinks; no automatic destructive cleanup.')
        print('File manager, Kuma, Beszel and n8n: complete native owner setup and choose permissions/notification credentials.')
        print('AI (if selected): create the WebUI owner with the external bootstrap override in docs/ai.md; signup stays disabled. No Ollama model is pulled by the installer.')
        print('These service-specific/browser/device steps are not claimed as completed by container startup.')
        if yes('Have you completed and tested the selected applications\' native accounts and settings?'):
            self.state['native_stacks'] = sorted(set(self.state.get('native_stacks', []) + names))
            self.mark('native-accounts')

    def tunnel_credentials(self):
        self.require_step('credentials')
        c = self.c(); root = Path(c['SECRETS_ROOT']); path = root / 'tunnel-credentials'
        print('\nOnly locally managed tunnels are accepted. A dashboard connector token cannot control local ingress.')
        token = secret('Cloudflare account Tunnel Edit token (session only; not saved)')
        api = cloudflare(token)
        if path.exists():
            credentials = verify_local_tunnel(api, json.loads(path.read_text()))
        else:
            mode = prompt('Create a local tunnel via API, or import local credentials JSON', 'create', lambda x: x in ('create', 'import'))
            if mode == 'create':
                account = self.state['cloudflare']['account_id']
                name = prompt('Unique tunnel name', 'homelab')
                h.require(yes(f'Create a LOCAL tunnel named {name} in account {account}?'), 'Tunnel creation cancelled')
                pending = self.directory / 'tunnel-pending.json'
                h.require(not pending.exists(), 'An earlier creation attempt is pending. Inspect protected tunnel-pending.json/account state; do not create another tunnel blindly.')
                credentials = create_local_tunnel(api, account, name,
                    lambda data: h.atomic(pending, json.dumps(data) + '\n', 0o600))
            else:
                source = Path(prompt('Absolute path to locally managed tunnel credentials JSON'))
                h.require(source.is_absolute() and source.is_file(), 'Choose an existing JSON file')
                credentials = verify_local_tunnel(api, json.loads(source.read_text()))
            h.atomic(path, json.dumps(credentials) + '\n', 0o600)
            os.chown(path, 65532, 65532)
        os.chown(path, 65532, 65532)
        path.chmod(0o600)
        c['TUNNEL_ID'] = credentials['TunnelID']; save_environment(self.environment, c)
        self.mark('tunnel-credentials')
        print('Local tunnel credentials stored. No ingress configuration was uploaded to Cloudflare.')

    def remote(self):
        self.require_step('identity', 'tunnel-credentials')
        c = self.c()
        print('Remote candidates:', ', '.join(REMOTE))
        print('Never exported: Plex streaming, Dockge, terminal, Traefik dashboard, AdGuard, Beszel, SSH, databases.')
        names = selected_apps(prompt('Exact app names to publish (none disables all)', c['REMOTE_APPS'] or ','.join(self.state.get('desired_remote_apps', []))))
        deployed = set(self.state.get('selected', [])) | {'identity', 'infrastructure'}
        h.require(all(REMOTE[name] in deployed for name in names), 'Start and configure each selected app stack first')
        native = set(self.state.get('native_stacks', [])) | {'identity'}
        h.require(all(REMOTE[name] in native for name in names), 'Complete local native onboarding for every selected application stack first')
        if 'hooks' in names:
            h.require(yes('Does every published production webhook enforce a secret header/signature, without interactive Cloudflare Access challenges?'),
                      'Authenticated workflow setup is required before publishing hooks')
        print('Local routes:', ', '.join(name + '.' + c['DOMAIN'] for name in names) or '(none)')
        print('DNS CNAMEs must exist on Cloudflare; the installer creates exact records by API. Ingress/origin rules remain only in local runtime files.')
        if not yes('Apply this reviewed remote allowlist and start/stop the tunnel accordingly?'): return
        for name in names: tls_origin(c, name + '.' + c['DOMAIN'])
        if names:
            data = validate_tunnel_credentials(json.loads((Path(c['SECRETS_ROOT']) / 'tunnel-credentials').read_text()))
            h.require(data['TunnelID'] == c['TUNNEL_ID'], 'Configured tunnel ID differs from its credentials')
            token = (Path(c['SECRETS_ROOT']) / 'cloudflare-dns-token').read_text().strip()
            ensure_dns(cloudflare(token), self.state['cloudflare']['zone_id'], c['TUNNEL_ID'],
                       [name + '.' + c['DOMAIN'] for name in names])
        # Close the old transport before changing routes. A failure leaves it stopped, never broadly exposed.
        h.compose(c, 'infrastructure', '--profile', 'tunnel', 'stop', 'cloudflared')
        c.update(REMOTE_APPS=','.join(names), REMOTE_READY='yes' if names else 'no')
        save_environment(self.environment, c); self.controller('render')
        if names:
            h.compose(c, 'infrastructure', '--profile', 'tunnel', 'run', '--rm', '--no-deps', 'cloudflared',
                      'tunnel', '--config', '/etc/cloudflared/config.yml', 'ingress', 'validate')
            h.compose(c, 'infrastructure', '--profile', 'tunnel', 'up', '-d', '--no-deps', '--force-recreate', 'cloudflared')
        self.state['desired_remote_apps'] = names
        self.mark('remote')
        print('Remote allowlist applied locally. Removed hostnames may retain DNS but have no matching local route (404).')

    def disable_remote(self):
        c = self.c()
        if not yes('Immediately remove all local tunnel routes and stop cloudflared?'): return
        c.update(REMOTE_APPS='', REMOTE_READY='no')
        save_environment(self.environment, c)
        self.controller('render')
        # Closing the local routers first also works if Docker control subsequently fails.
        h.compose(c, 'infrastructure', '--profile', 'tunnel', 'stop', 'cloudflared')
        self.mark('remote-disabled')
        print('Tunnel disabled locally. No Cloudflare DNS records were deleted.')

    def operations(self):
        self.require_step('applications')
        self.controller('backup-init') if yes('Initialize the configured Restic repository now (only if not initialized)?') else None
        if yes('Run an application-consistent backup and integrity sample now?'):
            self.controller('backup'); self.controller('backup-check'); self.mark('backup')
        if yes('Install systemd units and Docker LAN-startup guard (no services are enabled or restarted)?'):
            h.run([sys.executable, h.ROOT / 'scripts/lib/install_systemd.py'])
            self.mark('units')
        if yes('Review and apply the Docker-compatible firewall? Keep console and a second SSH session open.'):
            h.run([sys.executable, h.ROOT / 'scripts/lib/firewall.py', '--dry-run'])
            if yes('Does ADMIN_CIDRS include this SSH client, and is console recovery available?'):
                h.run([sys.executable, h.ROOT / 'scripts/lib/firewall.py', '--apply'])
                self.mark('firewall')
        if 'units' in self.state['completed'] and yes('Enable mount-guard and host-monitor timers?'):
            h.run(['systemctl', 'enable', '--now', 'homelab-mount-guard.timer', 'homelab-monitor.timer'])
        if 'units' in self.state['completed'] and 'media' in self.state.get('selected', []) and yes('Enable verified-mount media startup on boot?'):
            h.run(['systemctl', 'enable', '--now', 'homelab-media.service'])
        if 'units' in self.state['completed']:
            for stack in ('storage', 'management', 'ai'):
                if stack in self.state.get('selected', []) and yes(f'Enable verified-mount {stack} startup on boot?'):
                    h.run(['systemctl', 'enable', '--now', f'homelab-{stack}.service'])
        if 'units' in self.state['completed'] and 'firewall' in self.state['completed'] and yes('Persist the already-tested firewall at boot?'):
            h.run(['systemctl', 'enable', 'homelab-firewall.service'])
        if 'units' in self.state['completed'] and 'backup' in self.state['completed']:
            print('A staged restore drill is still required; no live application/media restore is automated.')
            if yes('Have you verified a staged restore/db import and stored the recovery key offline? Enable backup timers?'):
                h.run(['systemctl', 'enable', '--now', 'homelab-backup.timer', 'homelab-backup-check.timer'])
                self.mark('backup-scheduled')
        self.mark('operations')

    def update(self):
        record = self.directory / 'release.json'
        h.require(record.exists(), 'Use install.sh --repo OWNER/REPOSITORY for a release-managed installation; this development checkout has no managed release.')
        state = json.loads(record.read_text())
        import releases as launcher
        try:
            if state.get('private'):
                os.environ['GITHUB_TOKEN'] = secret('GitHub read-only source token (session only)')
            latest = launcher.latest_release(state['repository'])
            print('Installed:', state['release'], '| latest stable:', latest)
            if latest == state['release']:
                print('Already on the latest published release. No source changed.'); return
            print('Modified release files block updates. A successful current-release backup is required after setup.')
            if yes('Review and install the new release source now? Applications redeploy only in a separate explicit step.'):
                command = [sys.executable, str(h.ROOT / 'scripts/lib/releases.py'), '--repo', state['repository'],
                           '--target', str(h.ROOT), '--state-dir', str(self.directory), '--update']
                if state.get('private'): command.append('--private')
                os.execv(sys.executable, command)
        except launcher.InstallError as exc:
            raise h.SafetyError(str(exc)) from None
        finally:
            os.environ.pop('GITHUB_TOKEN', None)

    def menu(self):
        actions = [
            ('settings', 'Host settings and external configuration', self.settings),
            ('bootstrap', 'Install Debian/Docker prerequisites', self.bootstrap),
            ('storage', 'Verify/mount storage and choose media IDs', self.storage),
            ('setup', 'Prepare fresh users, folders, keys and networks', self.setup),
            ('credentials', 'Configure Cloudflare DNS and initial admin credentials', self.credentials),
            ('foundation', 'Validate and start infrastructure + identity', self.foundation),
            ('identity', 'Provision SSO policies and finish MFA/DNS locally', self.identity),
            ('applications', 'Choose/start applications and guide native setup', self.applications),
            ('tunnel-credentials', 'Create/import a LOCALLY managed Cloudflare Tunnel', self.tunnel_credentials),
            ('remote', 'Choose local ingress allowlist and publish exact DNS names', self.remote),
            ('operations', 'Backup, timers, boot services and firewall', self.operations),
            ('update', 'Check/review a stable GitHub release update', self.update)]
        print('HomeLab console installer. Host-only files:', self.directory)
        print('The OS/internal disk formatting must already be complete. Existing external media is never erased or repermissioned.')
        print('Production installs use verified release archives; local settings are outside source. Type back in a form to return.')
        while True:
            print('\n--- Installation progress ---')
            for i, (key, label, _) in enumerate(actions, 1):
                print(f'{i:2}. [{"done" if key in self.state.get("completed", []) else "    "}] {label}')
            print(' p. Read-only hardware discovery\n d. Disable remote access immediately\n 0. Exit safely; resume later with the same installer')
            choice = prompt('Choose a step', '0')
            if choice == '0': return
            try:
                if choice == 'p': h.preflight(); continue
                if choice == 'd': self.disable_remote(); continue
                h.require(choice.isdigit() and 1 <= int(choice) <= len(actions), 'Choose a listed step')
                actions[int(choice) - 1][2]()
            except Back:
                print('Returned to menu. Earlier completed actions are retained.')
            except KeyboardInterrupt:
                print('\nInterrupted step. Review its output; recorded preparation can be resumed.')
            except (h.SafetyError, APIError, OSError, ValueError, KeyError, subprocess.CalledProcessError) as exc:
                print('Step stopped:', str(exc))
                print('Resolve the issue and retry; no destructive reset is offered.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', default='/etc/homelab')
    args = parser.parse_args()
    h.require(sys.stdin.isatty(), 'An interactive terminal is required')
    h.root_only()
    os_info = Path('/etc/os-release').read_text()
    h.require(re.search(r'^ID=debian$', os_info, re.MULTILINE) and re.search(r'^VERSION_CODENAME=trixie$', os_info, re.MULTILINE),
              'The interactive installer targets Debian 13 trixie.')
    directory = Path(args.state_dir)
    h.require(directory.is_absolute() and directory.resolve() == directory and not any(x in str(directory) for x in (' ', '\n', '\r')),
              'Use a dedicated canonical configuration directory without spaces')
    h.require(directory != h.ROOT and h.ROOT not in directory.parents and directory not in h.ROOT.parents,
              'Host configuration must be outside the Git checkout')
    defaults = h.read_env(h.ROOT / '.env.example')
    defaults['HOMELAB_CONFIG_DIR'] = str(directory)
    h.validate_paths(defaults)
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    h.require(directory.stat().st_uid == os.geteuid(), 'Installer configuration directory must be root-owned')
    h.require(directory.stat().st_mode & 0o077 == 0, 'Installer configuration directory must be mode 0700')
    import fcntl
    with open(directory / 'wizard.lock', 'a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise h.SafetyError('Another interactive installer is running')
        Wizard(directory).menu()


if __name__ == '__main__':
    try: main()
    except (h.SafetyError, APIError, OSError, ValueError) as exc: sys.exit('ERROR: ' + str(exc))
    except (KeyboardInterrupt, EOFError): sys.exit('\nInstaller closed; completed state retained.')
