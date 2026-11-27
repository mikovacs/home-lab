"""Bounded readiness checks for the host LAN address and local OIDC origin."""
import http.client
import ipaddress
import json
import re
import socket
import ssl
import subprocess
import time


def network_timeout(c):
    value = c.get('NETWORK_START_TIMEOUT', '180')
    if not value.isdigit() or not 1 <= int(value) <= 3600:
        raise ValueError('NETWORK_START_TIMEOUT must be 1–3600 seconds')
    return int(value)


def lan_settings(c):
    address = ipaddress.ip_address(c['LAN_IP'])
    interface = c['LAN_INTERFACE']
    if (address.version != 4 or not address.is_private or address.is_loopback
            or address.is_link_local or address.is_unspecified or address.is_multicast
            or address.is_reserved or str(address) == '192.0.2.10'):
        raise ValueError('Configure a private LAN IPv4 address before Docker startup')
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+', interface) or interface == 'SET_ME':
        raise ValueError('Configure LAN_INTERFACE before Docker startup')
    timeout = network_timeout(c)
    return address, interface, timeout


def wait_for_lan(c, dry=False):
    address, interface, timeout = lan_settings(c)
    if dry:
        print(f'DRY RUN: wait up to {timeout}s for {address} on {interface}')
        return
    print(f'Waiting for LAN address {address} on {interface} (up to {timeout}s)', flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                ['ip', '-j', '-4', 'address', 'show', 'dev', interface],
                capture_output=True, text=True, timeout=min(5, max(0.1, deadline - time.monotonic())),
            )
            links = json.loads(result.stdout) if result.returncode == 0 else []
            assigned = any(
                link.get('ifname') == interface and link.get('operstate') == 'UP'
                and any(item.get('local') == str(address)
                        and not {'tentative', 'dadfailed'} & set(item.get('flags', []))
                        for item in link.get('addr_info', []))
                for link in links
            )
            if assigned:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                    probe.bind((str(address), 0))
                print('Configured LAN address is assigned and bindable.', flush=True)
                return
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(min(1, max(0, deadline - time.monotonic())))
    raise TimeoutError(f'LAN address {address} did not become ready on {interface}; Docker startup blocked')


def probe_oidc(host, address, path, issuer, timeout):
    # Connect directly to the LAN origin while preserving SNI, Host and normal
    # certificate validation. Public DNS/Cloudflare must not satisfy this gate.
    context = ssl.create_default_context()
    with socket.create_connection((address, 443), timeout=timeout) as transport:
        with context.wrap_socket(transport, server_hostname=host) as secure:
            connection = http.client.HTTPSConnection(host, timeout=timeout, context=context)
            connection.sock = secure
            try:
                connection.request('GET', path, headers={'Accept': 'application/json'})
                response = connection.getresponse()
                if response.status != 200:
                    return False, f'HTTP {response.status}'
                body = response.read(65537)
                if len(body) > 65536:
                    return False, 'oversized discovery response'
                document = json.loads(body)
                valid = isinstance(document, dict) and document.get('issuer') == issuer
                return valid, 'ready' if valid else 'unexpected OIDC issuer'
            finally:
                connection.close()


def wait_for_files_oidc(c, dry=False):
    from pathlib import Path
    settings = Path(c['RUNTIME_ROOT']) / 'files.yaml'
    if not settings.is_file():
        return  # Compose refuses the missing bind; do not create any files.
    oidc = json.loads(settings.read_text()).get('auth', {}).get('methods', {}).get('oidc', {})
    if not oidc.get('enabled'):
        return
    host = 'auth.' + c['DOMAIN']
    issuer = f'https://{host}/application/o/files/'
    if oidc.get('issuerUrl') != issuer:
        raise ValueError('FileBrowser OIDC issuer differs from the rendered local Authentik provider')
    value = c.get('STACK_START_TIMEOUT', '600')
    if not value.isdigit() or not 1 <= int(value) <= 3600:
        raise ValueError('STACK_START_TIMEOUT must be 1–3600 seconds')
    if dry:
        print(f'DRY RUN: wait for verified OIDC discovery at {issuer}')
        return
    print(f'Waiting for local FileBrowser OIDC discovery at {issuer}', flush=True)
    deadline = time.monotonic() + int(value)
    reason = 'not ready'
    while time.monotonic() < deadline:
        try:
            ready, reason = probe_oidc(
                host, c['LAN_IP'], '/application/o/files/.well-known/openid-configuration',
                issuer, min(5, max(0.1, deadline - time.monotonic())),
            )
            if ready:
                print('Local Authentik OIDC discovery is ready.', flush=True)
                return
        except (OSError, ValueError, http.client.HTTPException) as exc:
            reason = type(exc).__name__  # Do not log response bodies or credentials.
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    raise TimeoutError(f'Local FileBrowser OIDC discovery did not become ready: {reason}')
