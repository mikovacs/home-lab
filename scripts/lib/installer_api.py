"""Small stdlib clients for explicit Cloudflare and Authentik installer operations."""
import base64
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class APIError(RuntimeError):
    pass


def need(ok, message):
    if not ok:
        raise APIError(message)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # These fixed API endpoints need no redirects. Never forward credentials or
        # replay a provider mutation to a different endpoint, including HTTP downgrade.
        if fp is not None: fp.close()
        raise APIError('API redirect refused; verify the configured endpoint.')


def open_api(request, timeout=30):
    return urllib.request.build_opener(NoRedirect()).open(request, timeout=timeout)


class JsonAPI:
    def __init__(self, base, token, cloudflare=False):
        self.base, self.token, self.cloudflare = base.rstrip('/'), token, cloudflare
        need(self.base.startswith('https://') or self.base == 'http://127.0.0.1:9000/api/v3',
             'Authenticated API requests require HTTPS or the fixed local Authentik loopback endpoint.')

    def request(self, method, path, data=None):
        need(path.startswith('/') and not path.startswith('//'), 'Invalid API path')
        req = urllib.request.Request(self.base + path, method=method,
              data=json.dumps(data).encode() if data is not None else None,
              headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                       'Accept': 'application/json', 'User-Agent': 'HomeLab-installer'})
        try:
            with open_api(req, timeout=30) as response:
                body = response.read()
                result = json.loads(body) if body else {}
        except urllib.error.HTTPError as exc:
            exc.close()
            # Never print request headers, credential-bearing payloads or raw provider error bodies.
            raise APIError(f'API request failed (HTTP {exc.code}); check token permissions and service readiness.') from None
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise APIError('API unavailable or returned invalid JSON; no credentials were logged.') from None
        if self.cloudflare:
            need(isinstance(result, dict) and result.get('success') is True, 'Cloudflare rejected the request; check account/zone permissions.')
            return result['result']
        return result

    def listing(self, path):
        rows = []
        page = 1
        while True:
            result = self.request('GET', path + ('&' if '?' in path else '?') + 'page_size=100&page=' + str(page))
            rows.extend(result.get('results', []))
            pages = result.get('pagination', {}).get('total_pages', 1)
            if page >= pages:
                return rows
            page += 1
            need(page <= 100, 'Refusing unexpectedly large API pagination')


def cloudflare(token):
    return JsonAPI('https://api.cloudflare.com/client/v4', token, cloudflare=True)


def cf_id(value):
    need(isinstance(value, str) and re.fullmatch(r'[a-fA-F0-9]{32}', value), 'Expected a 32-character Cloudflare account/zone ID.')
    return value


def zone_for_domain(api, zone_name, domain):
    need(domain == zone_name or domain.endswith('.' + zone_name), 'Domain must belong to the selected Cloudflare zone.')
    rows = api.request('GET', '/zones?' + urllib.parse.urlencode({'name': zone_name, 'status': 'active'}))
    matches = [r for r in rows if r.get('name') == zone_name and r.get('status') == 'active']
    need(len(matches) == 1, 'Exactly one active Cloudflare zone is required; check nameservers and token scope.')
    return matches[0]


def validate_tunnel_credentials(data):
    need(isinstance(data, dict), 'Import a local tunnel credentials JSON object, not a dashboard connector token.')
    account = cf_id(data.get('AccountTag', ''))
    try:
        tunnel = str(uuid.UUID(data['TunnelID']))
        raw = base64.b64decode(data['TunnelSecret'], validate=True)
    except (KeyError, ValueError, TypeError):
        raise APIError('Invalid local tunnel credentials JSON. A dashboard connector token is not supported.') from None
    need(len(raw) >= 32, 'Tunnel secret must contain at least 32 random bytes.')
    return {'AccountTag': account, 'TunnelID': tunnel, 'TunnelSecret': data['TunnelSecret']}


def create_local_tunnel(api, account, name, save_pending):
    account = cf_id(account)
    need(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,62}', name), 'Use a simple unique tunnel name.')
    existing = api.request('GET', f'/accounts/{account}/cfd_tunnel?' + urllib.parse.urlencode({'name': name, 'is_deleted': 'false'}))
    need(not existing, 'A tunnel with that name already exists. Import its local credentials instead; it is never replaced.')
    secret = base64.b64encode(secrets.token_bytes(32)).decode()
    # Preserve the generated secret before the POST, including ambiguous network failures.
    save_pending({'AccountTag': account, 'TunnelSecret': secret, 'name': name})
    result = api.request('POST', f'/accounts/{account}/cfd_tunnel',
                         {'name': name, 'config_src': 'local', 'tunnel_secret': secret})
    need(result.get('config_src') == 'local', 'Cloudflare did not create a locally managed tunnel; do not enable it.')
    return validate_tunnel_credentials({'AccountTag': account, 'TunnelSecret': secret, 'TunnelID': result['id']})


def verify_local_tunnel(api, credentials):
    data = validate_tunnel_credentials(credentials)
    result = api.request('GET', f'/accounts/{data["AccountTag"]}/cfd_tunnel/{data["TunnelID"]}')
    need(result.get('config_src') == 'local' and not result.get('deleted_at'),
         'Tunnel must be active and locally managed. Dashboard tokens/remote configuration are rejected.')
    return data


def ensure_dns(api, zone_id, tunnel_id, hostnames):
    zone_id, tunnel_id = cf_id(zone_id), str(uuid.UUID(tunnel_id))
    planned = []
    for host in sorted(set(hostnames)):
        need(re.fullmatch(r'[a-z0-9][a-z0-9.-]+', host) and '*' not in host, 'Only exact DNS hostnames are allowed.')
        rows = api.request('GET', f'/zones/{zone_id}/dns_records?' + urllib.parse.urlencode({'name': host}))
        content = tunnel_id + '.cfargotunnel.com'
        if rows:
            need(len(rows) == 1 and rows[0].get('type') == 'CNAME'
                 and rows[0].get('content', '').rstrip('.') == content and rows[0].get('proxied') is True,
                 'Conflicting DNS record for ' + host + '; no existing record is overwritten.')
        else:
            planned.append({'type': 'CNAME', 'name': host, 'content': content, 'ttl': 1,
                            'proxied': True, 'comment': 'HomeLab locally managed tunnel'})
    # Validate every conflict before making any DNS write.
    for record in planned:
        api.request('POST', f'/zones/{zone_id}/dns_records', record)
    return [r['name'] for r in planned]


def wait_for_identity(api, attempts=60, delay=5, progress=lambda message: None):
    """API authentication can work before the worker has installed its default blueprints."""
    required_flows = {'default-provider-authorization-implicit-consent', 'default-provider-invalidation-flow'}
    for attempt in range(attempts):
        try:
            users = api.listing('/core/users/?username=akadmin')
            flows = api.listing('/flows/instances/')
            mappings = api.listing('/propertymappings/provider/scope/')
            stages = api.listing('/stages/authenticator/validate/')
            outposts = api.listing('/outposts/instances/')
            if (any(u.get('username') == 'akadmin' for u in users)
                and required_flows <= {f.get('slug') for f in flows}
                and any(m.get('managed') == 'goauthentik.io/providers/proxy/scope-proxy' for m in mappings)
                and any(s.get('name') == 'default-authentication-mfa-validation' for s in stages)
                and any(o.get('managed') == 'goauthentik.io/outposts/embedded'
                        or o.get('name') == 'authentik Embedded Outpost' for o in outposts)):
                return
        except APIError:
            pass
        if attempt + 1 < attempts:
            progress(f'Waiting for Authentik bootstrap and default blueprints ({attempt + 1}/{attempts})...')
            time.sleep(delay)
    raise APIError('Authentik bootstrap/default blueprints are not ready; inspect server/worker logs and retry. No identity policies were changed.')


def authentik(api, domain, progress=lambda message: None):
    """Provision group-gated browser apps using the embedded outpost; no API exemptions."""
    def exactly(rows, message):
        need(len(rows) == 1, message)
        return rows[0]

    groups = {}
    for name in ('homelab-admins', 'homelab-users', 'media-users', 'automation-users', 'ai-users'):
        rows = [r for r in api.listing('/core/groups/?' + urllib.parse.urlencode({'name': name})) if r['name'] == name]
        need(len(rows) <= 1, 'Duplicate identity group: ' + name)
        need(not rows or not rows[0].get('is_superuser'), 'Existing HomeLab access group must not grant Authentik superuser rights: ' + name)
        groups[name] = rows[0] if rows else api.request('POST', '/core/groups/', {'name': name, 'is_superuser': False})
    admins = [r for r in api.listing('/core/users/?username=akadmin') if r.get('username') == 'akadmin']
    need(len(admins) == 1, 'Bootstrap akadmin user is unavailable; review initial identity setup.')
    admin = admins[0]
    api.request('PATCH', '/core/users/' + str(admin['pk']) + '/',
                {'groups': sorted(set(admin.get('groups', []) + [groups['homelab-admins']['pk']]))})
    flows = api.listing('/flows/instances/')
    def flow(slug):
        return exactly([r for r in flows if r['slug'] == slug], 'Missing built-in flow: ' + slug)['pk']
    authorization = flow('default-provider-authorization-implicit-consent')
    invalidation = flow('default-provider-invalidation-flow')
    mappings = [r['pk'] for r in api.listing('/propertymappings/provider/scope/')
                if r.get('managed') == 'goauthentik.io/providers/proxy/scope-proxy']
    need(mappings, 'Built-in proxy scope mapping is unavailable; check Authentik initialization.')
    access = {'home': ['homelab-users'], 'radarr': [], 'sonarr': [], 'prowlarr': [],
              'torrent': [], 'bazarr': [], 'n8n': ['automation-users'], 'ai': ['ai-users'],
              'status': ['homelab-users']}
    provider_ids = []
    for host, extra_groups in access.items():
        progress('Configuring group-gated SSO: ' + host + '.' + domain)
        name, slug = 'HomeLab - ' + host, 'homelab-' + host
        matches = [r for r in api.listing('/providers/proxy/?' + urllib.parse.urlencode({'search': name})) if r['name'] == name]
        need(len(matches) <= 1, 'Duplicate HomeLab provider: ' + host)
        body = {'name': name, 'mode': 'forward_single', 'external_host': f'https://{host}.{domain}',
                'authorization_flow': authorization, 'invalidation_flow': invalidation,
                'property_mappings': mappings, 'skip_path_regex': '', 'intercept_header_auth': False}
        provider = matches[0] if matches else api.request('POST', '/providers/proxy/', body)
        need(provider.get('mode') == 'forward_single' and provider.get('external_host') == body['external_host']
             and not provider.get('skip_path_regex'), 'Existing provider differs; review it manually: ' + host)
        provider_ids.append(provider['pk'])
        apps = [r for r in api.listing('/core/applications/?' + urllib.parse.urlencode({'slug': slug, 'superuser_full_list': 'true'})) if r['slug'] == slug]
        need(len(apps) <= 1, 'Duplicate HomeLab application: ' + host)
        app = apps[0] if apps else api.request('POST', '/core/applications/',
              {'name': name, 'slug': slug, 'provider': provider['pk'], 'policy_engine_mode': 'any'})
        need(app.get('provider') == provider['pk'] and app.get('name') == name and app.get('policy_engine_mode') == 'any',
             'Existing application differs; review it manually: ' + host)
        allowed = {groups[g]['pk'] for g in ['homelab-admins'] + extra_groups}
        bindings = api.listing('/policies/bindings/?' + urllib.parse.urlencode({'target': app['pk']}))
        need(all(b.get('group') in allowed and not b.get('policy') and not b.get('user')
                 and b.get('enabled') is True and not b.get('negate', False) for b in bindings),
             'Unexpected identity policy binding; review it manually: ' + host)
        existing_groups = {b['group'] for b in bindings}
        for order, group in enumerate(sorted(allowed)):
            if group not in existing_groups:
                api.request('POST', '/policies/bindings/', {'target': app['pk'], 'group': group,
                            'order': len(bindings) + order, 'enabled': True, 'negate': False})
    outposts = api.listing('/outposts/instances/')
    embedded = exactly([r for r in outposts if r.get('managed') == 'goauthentik.io/outposts/embedded'
                        or r.get('name') == 'authentik Embedded Outpost'], 'Expected one embedded Authentik outpost')
    api.request('PATCH', '/outposts/instances/' + embedded['pk'] + '/',
                {'providers': sorted(set(embedded.get('providers', []) + provider_ids)),
                 'config': {**embedded.get('config', {}), 'authentik_host': 'https://auth.' + domain}})
    # akadmin is still subject to MFA in the authentication flow. Enrollment is a personal-device step.
    stages = api.listing('/stages/authenticator/validate/')
    stage = exactly([r for r in stages if r.get('name') == 'default-authentication-mfa-validation'], 'Default MFA validation stage unavailable')
    enrollment_name = 'homelab-totp-enrollment'
    enrollments = [r for r in api.listing('/stages/authenticator/totp/?name=' + enrollment_name)
                   if r.get('name') == enrollment_name]
    need(len(enrollments) <= 1, 'Duplicate HomeLab TOTP enrollment stage')
    enrollment = enrollments[0] if enrollments else api.request('POST', '/stages/authenticator/totp/',
                  {'name': enrollment_name, 'friendly_name': 'Authenticator app', 'digits': 6})
    configured = stage.get('configuration_stages', [])
    need(not configured or configured == [enrollment['pk']],
         'Existing MFA enrollment policy differs; review it before replacing any configuration stages.')
    api.request('PATCH', '/stages/authenticator/validate/' + stage['pk'] + '/',
                {'not_configured_action': 'configure', 'configuration_stages': [enrollment['pk']],
                 'device_classes': ['totp', 'webauthn', 'static']})
    return groups


class AdGuardAPI:
    """AdGuard is reached only through the existing loopback setup binding."""
    def __init__(self, username, password):
        self.authorization = 'Basic ' + base64.b64encode((username + ':' + password).encode()).decode()

    def request(self, method, path, data=None):
        need(path.startswith('/') and not path.startswith('//'), 'Invalid local API path')
        req = urllib.request.Request('http://127.0.0.1:3001/control' + path, method=method,
              data=json.dumps(data).encode() if data is not None else None,
              headers={'Authorization': self.authorization, 'Content-Type': 'application/json'})
        try:
            with open_api(req, timeout=30) as response:
                body = response.read().strip()
                if body.startswith((b'{', b'[')):
                    return json.loads(body)
                need(method != 'GET', 'AdGuard returned a setup/login page instead of API JSON')
                return {}
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError) as exc:
            if isinstance(exc, urllib.error.HTTPError): exc.close()
            raise APIError('Local AdGuard API unavailable or rejected authentication/configuration; no credentials were logged.') from None


def adguard(api, username, password, domain, lan_ip, hostnames, upstreams):
    try:
        api.request('GET', '/status')
    except APIError:
        # This endpoint is available only before first configuration. Do not reset an installed server.
        api.request('GET', '/install/get_addresses')
        api.request('POST', '/install/configure', {'web': {'ip': '0.0.0.0', 'port': 3000},
                    'dns': {'ip': '0.0.0.0', 'port': 53}, 'username': username, 'password': password})
    existing = api.request('GET', '/rewrite/list')
    need(isinstance(existing, list), 'AdGuard rewrite listing was not ready; retry after initial startup')
    planned = []
    for name in sorted(set(hostnames)):
        need(re.fullmatch(r'[a-z][a-z0-9-]*', name), 'Only explicit local DNS labels are allowed')
        fqdn = name + '.' + domain
        matches = [r for r in existing if r.get('domain') == fqdn]
        if matches:
            need(len(matches) == 1 and matches[0].get('answer') == lan_ip and matches[0].get('enabled', True),
                 'Conflicting AdGuard rewrite: ' + fqdn + '; no existing rewrite is overwritten.')
        else:
            planned.append({'domain': fqdn, 'answer': lan_ip})
    need(upstreams and all(isinstance(x, str) and x and '\n' not in x for x in upstreams), 'Choose DNS upstreams explicitly')
    api.request('POST', '/dns_config', {'upstream_dns': upstreams})
    for record in planned:
        api.request('POST', '/rewrite/add', record)
    return planned
