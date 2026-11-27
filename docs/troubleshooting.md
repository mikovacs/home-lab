# Troubleshooting

| Symptom | Safe investigation |
| --- | --- |
| Media deployment blocked | Inspect findmnt --mountpoint, UUID, live /dev/disk/by-uuid link, ext4/rw mode, free space and required directory metadata. Do not create directories under an unmounted path. |
| Media guard fails in Compose | Device number must match the bound filesystem. Check original UUID and mounts; do not remove the guard. |
| App permission error | Inspect numeric ownership and ACLs, chosen PUID/PGID and container UID. prepare changes ownership only for newly created appdata directories. No chown -R against existing media. |
| DNS unavailable | SSH by numeric IP; restore client DNS to router temporarily, inspect AdGuard through SSH tunnel. |
| Docker reports `cannot assign requested address` after boot | Check `ip -4 -brief address`, configured `LAN_IP`/`LAN_INTERFACE`, and `systemctl cat docker.service`. Install the repository's systemd units and Docker LAN-startup drop-in after a release update. The pre-start check must see the configured address before Docker restores published ports. Do not widen port bindings to all interfaces. |
| Docker waits or fails in the LAN pre-start check | Read `journalctl -b -u docker.service`; check the physical interface, DHCP/static address and `NETWORK_START_TIMEOUT`. Restore the configured LAN address before starting Docker. SSH/Tailscale on the host remains separate from Docker. |
| FileBrowser exits while validating OIDC | Verify Traefik and Authentik are available, then restart `homelab-storage.service`. Updated storage deployment waits for verified local discovery before starting FileBrowser. Inspect the provider/issuer and TLS certificate if the wait times out; do not disable certificate verification or authentication. |
| Port 53 occupied | Inspect ss output for an existing resolver; determine a deliberate host resolver plan before disabling any service. |
| TLS warnings | Staging ACME is untrusted; check zone token, DNS resolution, acme.json permissions and production transition. Do not disable verification for tunnel origins. |
| Remote publication stops at origin readiness | Compare a GET request through the LAN IP using the real service hostname and verified TLS. FileBrowser can return 404 to HEAD while GET works; use a release with the GET origin probe. A GET 404 or 5xx still requires routing/application repair before publication. |
| ForwardAuth loop / 404 | Verify exact provider external URL, embedded outpost assignment, auth hostname, callback router, cookies and group bindings. |
| Native API returns SSO HTML | Use internal service DNS/native API key or private access; do not globally exempt API paths. |
| qBittorrent rejects Host header | Configure its trusted server domains and reverse-proxy settings to the exact torrent hostname; keep CSRF/clickjacking/auth protections enabled. |
| Import copies instead of links | All download/import apps need one /data parent mount, same filesystem, permissions and hardlink option. Compare known file inodes read-only. |
| Container unhealthy/stopped after backup | Check journal and make status. Missing mounts block safe restart; validate and redeploy explicitly. |
| Backup full or failed | Inspect Restic repository, free capacity, offline keys, stopped-service restart results and timer logs. Verify a restore before retention prune. |
| AI OOM / Jellyfin stutter | Stop AI, measure memory pressure, choose smaller model or lower concurrency. The installer does not pull or upgrade Ollama models. |
| Beszel shows no host | Install the version-matched host agent with verified key/token and transport; hub alone is not a collector. |
| Beszel public key is empty and Add System does not save | Verify the route uses `private@file,headers@file,beszel-sso@file` and the admin-only `homelab-monitor` provider is provisioned. BasicAuth conflicts with PocketBase's `Authorization` token. Follow the migration in [monitoring](monitoring.md); do not expose the API anonymously. |
| Dockge changed YAML | Compare files with the recorded release; resolve edits through a reviewed release before further deployment. Do not discard changes to bypass integrity checks. |

Use make logs STACK=<stack> privately; first-run logs may contain one-time credentials. `make healthcheck` reports supported container health/state only, not end-to-end identity or media correctness. For emergency management use SSH and current Compose definitions. Never solve a problem by deleting volumes, pruning broadly, formatting a drive, bypassing guards or publishing a temporary anonymous admin route.
