# Configuration reference

The installer stores host settings in `/etc/homelab/host.env`, outside the source directory. `HOMELAB_ENV_FILE` selects that file for maintenance commands. Systemd units and generated stack `.env` symlinks use the same settings.

Use the wizard for initial setup. For manual edits:

- Keep all keys from `.env.example`; use plain, unquoted `KEY=value` lines.
- Do not use shell substitution or variable interpolation, and never source the file as shell code.
- Do not edit per-stack `.env` files separately: they are symlinks to the selected host environment.
- Store passwords and tokens in `SECRETS_ROOT`, not in the environment file or Compose YAML.

The low-level `make init` command creates missing configuration for manual setup/recovery and does not overwrite an existing file.

## Settings

| Settings | Meaning |
| --- | --- |
| HOMELAB_ENV_FILE (process environment) | Select the actual host env file; fallback is checkout/.env for manual operation |
| HOMELAB_CONFIG_DIR | Host-only settings/release/progress directory, outside all source/data/state roots |
| REPO_ROOT, APPDATA_ROOT, SECRETS_ROOT, RUNTIME_ROOT | Separate absolute canonical paths on primary drive; no overlap or symlink roots |
| DATA_ROOT, DATA_UUID | Freshly prepared secondary ext4 filesystem mount and discovered UUID |
| MEDIA_ROOT, MEDIA_UUID | Existing external ext4 mount and UUID; /mnt/plex-media by default |
| PUID, PGID | Numeric service IDs chosen to match retained media access; never automatically repermission media |
| MEDIA_USER, MEDIA_GROUP | Names used only when creating missing numeric service identities; collisions fail safely |
| DOMAIN, LAN_IP, LAN_INTERFACE, LAN_CIDR | Real domain/zone, server IPv4, physical NIC, router subnet |
| ADMIN_CIDRS | Comma-separated LAN/Tailscale source CIDRs, evaluated without trusting client X-Forwarded-For |
| PROXY_NETWORK, AI_NETWORK, TUNNEL_NETWORK | External bridge names created by configure-network |
| ACME_EMAIL, ACME_SERVER | Certificate contact/endpoint; example uses staging, wizard selects production |
| IDENTITY_READY | Set yes only after providers, outpost, policy groups and MFA work |
| TUNNEL_ID, REMOTE_APPS, REMOTE_READY | Local tunnel credentials identity, explicit comma-separated names and acceptance gate |
| TERMINAL_BRIDGE | Dedicated terminal-to-host bridge interface name, at most 15 characters |
| ADMIN_SSH_USER, ADMIN_SSH_HOST | Non-root host target for private terminal |
| TZ | Container timezone, default Europe/Budapest; timer schedules explicitly use Budapest |
| DISK_WARN, DISK_CRITICAL, MIN_MEDIA_FREE_GIB | Disk thresholds and media start free-space floor |
| SERVICE_MEMORY, SERVICE_CPUS | Global defaults for services without dedicated overrides |
| POSTGRES_MEMORY, AUTHENTIK_MEMORY, JELLYFIN_MEMORY, MEDIA_SERVICE_MEMORY, N8N_MEMORY | Optional stack-specific container memory overrides in .env |
| STACK_START_TIMEOUT | Compose startup wait in seconds, default 600; containers are recreated to refresh binds |
| AI_MEMORY, AI_CPUS, WEBUI_MEMORY | AI workload caps; explicit activation still required |
| N8N_HISTORY_HOURS | Execution retention, 168 hours by default |
| RESTIC_REPOSITORY, RESTIC_KEEP_DAILY/WEEKLY/MONTHLY | Encrypted backup backend and retention |

Application addresses use DOMAIN; common addresses are listed in the [README](../README.md), with the complete route definitions in Compose and the renderer. Rename them consistently in Compose labels, renderer, Authentik providers and DNS. Explicit hostnames and ports are deliberate access policy; do not introduce wildcard tunnel ingress. Default qBittorrent peer port is 6881; if changing it, also update its listen settings and firewall rules. Keep all optional overrides in the one selected host environment for Dockge parity.

## Secret files

Secrets are individual files in `SECRETS_ROOT`. Generated keys are never overwritten. External files: cloudflare-dns-token, admin-htpasswd, optional tunnel-credentials, files-oidc.json, terminal-ssh/{id_ed25519,known_hosts}, restic-env.json, monitor-webhook.json and smart-devices.json. See the respective guides. Never put credentials in .env or tracked YAML.
