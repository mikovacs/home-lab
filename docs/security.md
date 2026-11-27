# Security model

No public inbound forwarding is required. Bind host ports to a selected LAN IPv4, use source restrictions for private administration, keep databases unpublished and default tunnel allowlist empty. Preserve SSH by LAN IP and console recovery. ADMIN_CIDRS must reflect actual administrator devices/subnets; do not use 0.0.0.0/0. Application MFA belongs in Authentik; recovery basic auth is a separate protected path.

## Firewall

`configure-firewall` installs dedicated iptables/ip6tables chains without flushing unrelated tables. Host INPUT permits loopback, established flows, SSH from ADMIN_CIDRS, necessary ICMP, LAN DHCP client replies and Tailscale UDP, plus SSH arriving only from the dedicated terminal bridge, then drops. Docker-published IPv4 traffic is restricted in DOCKER-USER using original destination LAN_IP: LAN peers may reach documented ports; IPv4 ADMIN_CIDRS may reach HTTP/HTTPS, Plex and DNS (TCP/UDP 53). DNS requires an explicitly allowed source; use administrator device /32 addresses rather than the whole Tailscale range. IPv6 input is restricted and new forwarded traffic arriving on the configured physical NIC is denied. No IPv6 container publishing is supported. Preserve Docker's iptables backend; do not combine this design with UFW assumptions or Docker nftables backend.

Test firewall from a second allowed SSH session before enabling persistence. Keep console access. It intentionally does not enable Samba, alternate SSH ports, DHCP serving or subnet routing. Add explicit reviewed rules for those features. Existing firewall chains may further restrict traffic. For recovery remove only the jump to the HomeLab custom chain from console and diagnose; never flush Docker rules. The service reapplies on Docker startup/restart; verify after host upgrades.

## Secrets and accounts

Secrets use root-only 0700 parent storage and 0600 per-service files. Files needed by non-root containers use the appropriate application UID, including terminal UID 1000 and cloudflared UID 65532; other files stay root-owned. Docker Compose file secrets are bind mounts: uid/gid/mode overrides do not make an encrypted vault. Container env and docker inspect may reveal values exported from file secrets. Runtime files-oidc config is 0600 owned by the file-manager UID. Back up secrets using Restic encryption and keep Restic recovery material **off this server**.

Generate unique application accounts during local onboarding. No default passwords are committed. Authentik, n8n and Open WebUI keys persist; do not regenerate them casually. Rotate external DNS/tunnel credentials at their provider then replace protected files and restart consumers. Rotate n8n encryption key only with a supported credential migration; replacing it loses credential access. Changing PostgreSQL initialization password files does not update an existing database role: rotate through PostgreSQL and coordinate all clients. Keep old encrypted backups until a restore with the new keys succeeds.

## Container privileges

The Docker socket is root-equivalent. Traefik uses a private, unpublished proxy with POST disabled and only required GET API sections. GET container inspection still exposes metadata and environment; do not treat the proxy as a secret boundary against a compromised Traefik. Dockge's opt-in admin profile has full socket and app paths: compromise means host control. Its repo mount is read-only for ordinary operation; full Docker authority still lets a malicious root-equivalent user bypass guards. Never publish Dockge, even behind a public SSO layer.

LSIO init needs root before dropping to PUID/PGID. PostgreSQL's official entrypoint also initializes ownership as root. Avoid hardening flags that break documented startup; services that support it have non-root, dropped capabilities and read-only roots. ttyd runs only an SSH client and has no host root access. Optional hardware devices are narrowly mapped only after verification.

## Updates and installer access

Review releases and advisories monthly; use Renovate PRs. CI includes configuration, shell, YAML and secret checks, with no production deployment. The repository's MIT license does not replace each service's license; see [image sources](images.md).

Installer credentials/progress live in a root-owned 0700 host configuration directory outside Git. Secrets, that directory and the selected host env are included in encrypted backups. AdGuard native credentials and Authentik bootstrap API token are privileged persistent secrets; account/tunnel-management and private-source tokens are session-only. Authenticated Cloudflare, Authentik, AdGuard and monitor notification clients reject redirects rather than forwarding credentials or replaying mutations. Installer error handling redacts credentials; treat application logs and Docker metadata as sensitive. Release file-integrity checks block modified/unmanaged source; they do not substitute for trusting the release maintainer. See [installation](installation.md) and [releases](releases.md).
