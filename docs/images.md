# Image sources and maintenance

Version pins live in Compose files, including the custom terminal's Dockerfile base. Use the links below for upstream setup, updates and licensing. Review upstream release notes, advisories, architecture support and license files before changing pins.

| Component | Upstream documentation |
| --- | --- |
| Traefik | https://doc.traefik.io/traefik/setup/docker/ |
| cloudflared | https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/do-more-with-tunnels/local-management/configuration-file/ |
| AdGuard Home | https://github.com/AdguardTeam/AdGuardHome/wiki/Docker |
| Authentik | https://docs.goauthentik.io/install-config/install/docker-compose/ |
| PostgreSQL | https://hub.docker.com/_/postgres |
| Plex | https://docs.linuxserver.io/images/docker-plex/ |
| Radarr | https://docs.linuxserver.io/images/docker-radarr/ |
| Sonarr | https://docs.linuxserver.io/images/docker-sonarr/ |
| Prowlarr | https://docs.linuxserver.io/images/docker-prowlarr/ |
| qBittorrent | https://docs.linuxserver.io/images/docker-qbittorrent/ |
| Bazarr | https://docs.linuxserver.io/images/docker-bazarr/ |
| FileBrowser Quantum slim | https://github.com/gtsteffaniak/filebrowser and https://filebrowserquantum.com/en/docs/getting-started/docker/ |
| n8n | https://docs.n8n.io/hosting/configuration/supported-databases-settings/ and https://docs.n8n.io/sustainable-use-license/ |
| Uptime Kuma | https://github.com/louislam/uptime-kuma/wiki |
| Beszel | https://beszel.dev/guide/getting-started and https://beszel.dev/guide/agent-installation |
| Homepage | https://gethomepage.dev/installation/docker/ |
| Dockge | https://github.com/louislam/dockge |
| ttyd | https://github.com/tsl0922/ttyd |
| Tailscale host | https://tailscale.com/kb/1031/install-linux |
| Ollama | https://docs.ollama.com/docker |
| Open WebUI | https://docs.openwebui.com/getting-started/quick-start/ and https://github.com/open-webui/open-webui/blob/v0.13.5/LICENSE |
| Restic host | https://restic.readthedocs.io/en/stable/ |
| Docker socket proxy | https://github.com/Tecnativa/docker-socket-proxy |
| Python mount guard | https://hub.docker.com/_/python |

Beszel needs a separately configured host agent. Restic and Tailscale are host tools, not Compose images.

Review licensing separately for Plex, n8n and Open WebUI, hosted service/account terms for Cloudflare and Tailscale, and each downloaded AI model. The repository's MIT license does not cover these dependencies. Paid features are not assumed by the shipped configuration.

Debian packages follow configured security updates. The terminal base is pinned, while apt package versions can advance at rebuild time; rebuild and review it regularly. Image pins and Compose validation do not prove runtime compatibility or absence of vulnerabilities.
