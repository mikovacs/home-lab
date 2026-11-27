"""Opt-in direct Jellyfin HTTPS checks and a pinned, DNS-only IPv4 updater."""
import argparse
import fcntl
import ipaddress
import json
from pathlib import Path
import subprocess
import sys
import urllib.parse

import homelab as h
from installer_api import APIError, cf_id, cloudflare


def hostname(c):
    return 'jellyfin.' + c['DOMAIN']


def state_path(c):
    return h.configuration_directory(c) / 'jellyfin-ddns.json'


def public_ipv4(value):
    try:
        address = ipaddress.IPv4Address(value.strip())
    except (ValueError, AttributeError):
        raise h.SafetyError('Invalid external IPv4 response') from None
    h.require(address.is_global and not address.is_multicast,
              'External address must be a globally routable unicast IPv4 address')
    return str(address)


def curl(c, url, *options):
    # Fixed HTTPS endpoints, no environment proxy, redirects or credential output.
    return h.run(['curl', '-q', '--noproxy', '*', '-4', '--interface', c['LAN_IP'],
                  '--connect-timeout', '5', '--max-time', '15', '--max-filesize', '65536',
                  '--silent', '--show-error', *options, url], capture=True)


def discover_ip(c):
    trace = curl(c, 'https://www.cloudflare.com/cdn-cgi/trace', '--fail')
    values = [line[3:] for line in trace.splitlines() if line.startswith('ip=')]
    h.require(len(values) == 1, 'External IPv4 discovery returned no unique address')
    first = public_ipv4(values[0])
    second = public_ipv4(curl(c, 'https://api.ipify.org', '--fail'))
    h.require(first == second, 'External IPv4 services disagree; DNS was not changed')
    return first


def probe(c, path, host=None):
    name = hostname(c)
    options = ['--connect-to', f'{name}:443:{c["LAN_IP"]}:9443',
               '--write-out', '\n%{http_code}']
    if host:
        options += ['--header', 'Host: ' + host]
    output = curl(c, 'https://' + name + path, *options)
    body, code = output.rsplit('\n', 1)
    return int(code), body


def readiness(c):
    h.require(probe(c, '/health')[0] == 200, 'Public Jellyfin listener health/TLS check failed')
    code, body = probe(c, '/System/Info/Public')
    h.require(code == 200 and json.loads(body).get('StartupWizardCompleted') is True,
              'Finish Jellyfin native account setup before publishing')
    h.require(probe(c, '/Users')[0] == 401, 'Jellyfin must require native authentication')
    for name in ('docker', 'adguard', 'traefik', 'auth', 'home', 'monitor'):
        h.require(probe(c, '/', name + '.' + c['DOMAIN'])[0] in (404, 421),
                  'A non-Jellyfin hostname is reachable on the public listener')
    print('Public listener TLS, Jellyfin setup/authentication and hostname isolation passed.')


def record(api, zone, name):
    rows = api.request('GET', f'/zones/{cf_id(zone)}/dns_records?' +
                       urllib.parse.urlencode({'name': name, 'per_page': 100}))
    h.require(isinstance(rows, list) and len(rows) == 1,
              'Expected exactly one existing Jellyfin DNS record; no records changed')
    row = rows[0]
    h.require(row.get('name') == name and row.get('type') == 'A'
              and row.get('proxied') is False, 'Jellyfin requires one DNS-only A record; review conflicts')
    cf_id(row.get('id', ''))
    ipaddress.IPv4Address(row['content'])
    return row


def initialize(c, api, zone, expected):
    path = state_path(c)
    h.require(not path.exists() and not path.is_symlink(), 'DDNS binding already exists; review it first')
    expected = str(ipaddress.IPv4Address(expected))
    row = record(api, zone, hostname(c))
    h.require(row['content'] == expected, 'DNS differs from the explicitly reviewed initial address')
    state = {'hostname': hostname(c), 'zone_id': cf_id(zone), 'record_id': row['id'],
             'last_ip': expected, 'initial_record': row}
    h.atomic(path, json.dumps(state, indent=2) + '\n', 0o600)
    print('Bound existing DNS-only record. DNS has not changed.')


def update(c, api, *, apply=False):
    path = state_path(c)
    h.require(path.is_file() and not path.is_symlink(), 'Initialize the reviewed DNS record binding first')
    state = json.loads(path.read_text())
    h.require(state['hostname'] == hostname(c), 'DDNS binding hostname does not match host configuration')
    cf_id(state['record_id'])
    address = discover_ip(c)
    row = record(api, state['zone_id'], state['hostname'])
    h.require(row['id'] == state['record_id'], 'DNS record was replaced; refusing automatic adoption')
    # Recover an interrupted successful PATCH without overwriting an unrelated address.
    h.require(row['content'] in (state['last_ip'], address),
              'DNS content changed outside this updater; review before overwriting')
    readiness(c)
    if row['content'] != address:
        print(f'Jellyfin DNS-only IPv4: {row["content"]} -> {address}')
        if not apply:
            print('Review only; DNS has not changed.')
            return
        result = api.request('PATCH', f'/zones/{state["zone_id"]}/dns_records/{state["record_id"]}',
                             {'content': address, 'proxied': False, 'ttl': 300})
        h.require(result.get('id') == state['record_id'] and result.get('content') == address
                  and result.get('proxied') is False, 'DNS update response did not match requested state')
        confirmed = record(api, state['zone_id'], state['hostname'])
        h.require(confirmed['id'] == state['record_id'] and confirmed['content'] == address,
                  'DNS read-back verification failed')
    else:
        print('Jellyfin DNS-only IPv4 is current: ' + address)
    if apply:
        state['last_ip'] = address
        h.atomic(path, json.dumps(state, indent=2) + '\n', 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('check', 'init', 'update'))
    parser.add_argument('--zone-id')
    parser.add_argument('--expected-ip')
    parser.add_argument('--apply', action='store_true', help='Write the reviewed DNS update')
    args = parser.parse_args()
    h.root_only()
    c = h.config()
    h.require(c['JELLYFIN_PUBLIC_ENABLED'] == 'yes', 'Public Jellyfin is disabled in host configuration')
    with open(Path(c['RUNTIME_ROOT']) / 'operation.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'check':
            readiness(c)
            print('Confirmed external IPv4: ' + discover_ip(c))
            return
        token_path = Path(c['SECRETS_ROOT']) / 'cloudflare-dns-token'
        h.require(token_path.is_file() and not token_path.is_symlink()
                  and token_path.stat().st_mode & 0o077 == 0, 'DNS token must be a private regular file')
        api = cloudflare(token_path.read_text().strip())
        if args.action == 'init':
            h.require(args.zone_id and args.expected_ip, 'init requires --zone-id and --expected-ip')
            initialize(c, api, args.zone_id, args.expected_ip)
        else:
            update(c, api, apply=args.apply)


if __name__ == '__main__':
    try:
        main()
    except (h.SafetyError, APIError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        # subprocess exceptions can include arguments; none contain credentials here.
        print('Jellyfin public access check/update stopped: ' + str(exc), file=sys.stderr)
        sys.exit(1)
