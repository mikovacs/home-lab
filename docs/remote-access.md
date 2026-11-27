# Remote access

Cloudflare Tunnel is optional. The [installer](installation.md) keeps ingress and Traefik routes in local runtime files; Cloudflare receives exact DNS records, not uploaded application-route configuration. No inbound router forwarding is required.

## Tunnel credentials

Use a **locally managed** tunnel. A Zero Trust connector token is for remote management and is not accepted. The wizard can:

- Create a tunnel with an account-scoped Cloudflare Tunnel Edit API token.
- Import tunnel JSON and verify local management through the API; verification needs account tunnel read permission.

The management token is session-only. A separate zone-scoped DNS Edit + Zone Read token is retained for DNS-01 certificates and exact CNAME creation. The wizard verifies the active zone contains the service domain.

Tunnel JSON is stored as `SECRETS_ROOT/tunnel-credentials`, mode 0600, owned by container UID/GID 65532; its parent remains root-only. Do not substitute a remote connector token or wildcard ingress.

## Publish selected applications

Allowed names: `auth`, `home`, `files`, `radarr`, `sonarr`, `prowlarr`, `torrent`, `n8n`, `ai`, `status`, `hooks`. The default list is empty. Include `auth` for remote browser login. Native onboarding and identity acceptance must be complete first.

The wizard verifies each local HTTPS origin with a GET request, checks all DNS conflicts before writing, creates exact CNAMEs, saves the host allowlist, validates generated ingress and recreates cloudflared. The origin probe connects directly to the LAN IP while verifying the service hostname and certificate; it reads response headers without following redirects or downloading the body. This supports applications such as FileBrowser that serve GET but reject HEAD. An unexpected status stops publication and is included in the error; ordinary application 404/5xx responses still fail. Only the hooks probe permits 404 for its nonexistent production webhook path. The wizard never overwrites conflicting DNS records. Local tunnel routers and ingress have a 404 fallback; private admin routers are absent from that entrypoint. Origin TLS verification stays enabled.

`hooks` permits only production `/webhook/` requests. Each workflow needs header/signature authentication; the path alone is not protection. Editor, test-webhook and Authentik callback routes are excluded on the hooks host. Tunnel callback exceptions exist only for selected hosts using ForwardAuth. Cloudflare Access is optional and must not introduce an interactive challenge for machine clients without a deliberate token design.

Use console option `d` to close local remote routes. Removed names can retain DNS records but return local 404. For manual route changes, select the same host environment, render, validate ingress and recreate cloudflared; restarting alone may not apply changed Compose settings.

Jellyfin streaming, Dockge, terminal, Beszel, Traefik dashboard, AdGuard, SSH and databases cannot be selected. Use private access for administration and separately configured media access.

## Interrupted provider operations

A tunnel creation timeout may mean creation succeeded remotely. The generated secret is saved **before** the POST in protected `tunnel-pending.json`. Inspect the account/name, recover its UUID, and reconstruct/import JSON with `AccountTag`, `TunnelID` and the preserved `TunnelSecret`. Do not repeatedly create new tunnels while the outcome is unknown.

A network failure can leave some DNS records created. Retrying reuses exact matches and still refuses conflicts; it does not delete unrelated records. Keep pending recovery material protected and included in encrypted configuration backups.

## Tailscale and private terminal

Tailscale is a manual host integration. Follow [official Linux installation](https://tailscale.com/kb/1031/install-linux), review its signed package repository, authorize devices and restrict grants to required SSH/HTTPS access. The installer does not install or enroll it. No auth key belongs in Git.

HTTPS binds the LAN IP. A Tailscale address alone does not make that listener reachable: configure routing to the LAN IP or review a listener override. `ADMIN_CIDRS` defaults include `100.64.0.0/10`; narrow this to administrator devices where possible and enforce tailnet grants.

For access only to this server, advertise and approve `LAN_IP/32`, not the entire LAN. Enable persistent IPv4 forwarding on the host. With Docker services on the same host, disable subnet source NAT (`tailscale set --snat-subnet-routes=false`) so Traefik sees the administrator's Tailscale address instead of a Docker bridge gateway. Add the administrator device's /32 to `ADMIN_CIDRS`, preserve the LAN recovery range, and render the configuration. Docker replies return through their host gateway; routes to other LAN devices require a separate return-routing review.

Restrict tailnet grants to the administrator devices and the server's required HTTPS, DNS (TCP/UDP 53), and optional SSH ports. Configure a restricted nameserver for `DOMAIN` pointing to `LAN_IP`; keep `--accept-dns=false` on the server to retain its independent resolver. The HomeLab firewall permits DNS from configured IPv4 admin sources. Test both DNS protocols and private HTTPS through Tailscale after applying the firewall and after Docker/Tailscale restarts, before enabling firewall persistence. Test again from a separate network before relying on remote recovery.

The optional ttyd container runs an SSH client as UID 1000. Provision a dedicated non-root SSH target and independently verified host key. Under `SECRETS_ROOT/terminal-ssh`, store `id_ed25519` and `known_hosts`, readable by UID 1000, directory 0700/files 0600. Keep the parent root-only and never reuse your main admin private key.

Set `ADMIN_SSH_USER/HOST`, then use `make deploy-remote-access` with the selected host environment. Private IP rules and basic auth protect ttyd; SSH uses strict host-key checking. Its dedicated `TERMINAL_BRIDGE` receives the host SSH exception and is its default gateway, requiring Compose 2.33.1+. The bridge name must differ from the physical NIC. SSH by numeric LAN IP remains the recovery path.

## Jellyfin streaming

The native app connects to the server URL you configure. It does not automatically bypass a tunnel when playback begins. Keep Jellyfin out of the public Cloudflare Tunnel routes: Cloudflare's [Tunnel FAQ](https://developers.cloudflare.com/cloudflare-one/faq/cloudflare-tunnels-faq/) explains the paid-service requirements for video through public hostname routes. DNS-only records are different from proxied/tunnel traffic.

For personal mobile devices, use [Tailscale](https://jellyfin.org/docs/general/post-install/networking/tailscale/) and the existing private route to `LAN_IP`, restricted DNS and the local `https://jellyfin.DOMAIN` endpoint. Each client needs enrollment and explicit grants; do not grant family playback devices SSH or private administration merely to enable streaming. Tailscale attempts a direct encrypted connection and can fall back to a relay, so verify actual throughput. Adding a device does not automatically extend this installation's existing grants or firewall source allowlist.

For remote TVs or users without a VPN client, separately configure a public HTTPS reverse proxy with Jellyfin native authentication. Direct home hosting requires a reachable public address, appropriate DNS (DNS-only if hosted at Cloudflare), router forwarding and narrowly scoped firewall/proxy exposure. CGNAT may prevent this; a separately hosted reverse proxy with a private backend connection is another option. Do not simply forward the existing shared Traefik entrypoint, which also serves other local applications. This configuration does not enable any public media endpoint automatically.
