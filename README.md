# HomeLab

A home server built on Debian 13 and Docker Compose. It provides Jellyfin and media tools, a browser-based file manager, a dashboard, automation and monitoring. You choose which applications to run. Cloudflare Tunnel and local AI are optional.

The Bash entrypoint opens an interactive Python installer. It downloads a published GitHub release, prepares the host and starts your selected services. Settings, passwords and application data stay outside the source directory, so source updates do not replace them.

## Start here

1. Install Debian 13 manually. Disconnect the external media disk while installing the OS and formatting the internal disks.
2. Set up a non-root administrator, sudo and SSH access. Reconnect the media disk after OS installation.
3. Follow the [installation guide](docs/installation.md) to download and verify the release installer, then run:

   ```sh
   sudo bash install.sh --repo mikovacs/home-lab --private
   ```

4. Work through the menu: host settings, Docker, existing storage, credentials, identity/DNS, then applications.
5. Finish each application's first-login setup and the [installation checks](docs/installation-checks.md). Enable remote access and scheduled backups only when their setup is complete.

Download the installer from [GitHub Releases](https://github.com/mikovacs/home-lab/releases). For this private repository, sign in to download the assets and supply a read-only release-access token when prompted. If using a public fork, change the repository name and omit `--private`.

You need a published release, an existing ext4 media disk, a separate ext4 data disk, and a domain managed through Cloudflare DNS for the supplied HTTPS configuration. Cloudflare Tunnel is optional even when Cloudflare DNS is used for certificates. See [manual OS setup](docs/os-installation.md) and [hardware requirements](docs/hardware.md).

The installer never formats or repairs disks, reorganizes the media library or recursively changes its permissions. Applications can write only to their assigned paths. Jellyfin read media without write access.

## What you can use

Replace `DOMAIN` with your configured domain. These addresses work after the corresponding stack and local DNS are set up.

| Purpose | Application and address |
| --- | --- |
| Dashboard | Homepage — `https://home.DOMAIN` |
| Server files | FileBrowser Quantum — `https://files.DOMAIN` |
| Movies and TV | Jellyfin — `https://jellyfin.DOMAIN`; [library setup](docs/media.md) |
| Media management | Radarr, Sonarr, Prowlarr and qBittorrent; optional Bazarr |
| Accounts and MFA | Authentik — `https://auth.DOMAIN` |
| Automation | n8n — `https://n8n.DOMAIN` |
| Availability | Uptime Kuma — `https://status.DOMAIN` |
| Host metrics | Beszel — `https://monitor.DOMAIN`; host agent setup required |
| Local DNS | AdGuard — `https://adguard.DOMAIN` |
| Optional administration | Private Dockge and SSH-based web terminal |
| Optional local AI | Ollama and Open WebUI — `https://ai.DOMAIN` |
| Backups | Restic on the host, with optional systemd timers |

FileBrowser provides browsing, upload, download, copy, move, rename and delete for the configured personal and shared folders. It does not expose the whole server filesystem. See [dashboard and file management](docs/management.md).

## Everyday use

Use the installer menu for setup and source updates. For maintenance commands, run from the installed source directory and select the same host settings:

```sh
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make status
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make logs STACK=storage
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make backup
```

See [daily operation](docs/operations.md) for starting and stopping stacks, reboot behavior and logs. FileBrowser, Homepage and AI need explicit startup after reboot. Media can use the supplied mount-dependent boot service.

Backups include application state, configuration and secrets. They **do not include personal/shared files, media, downloads or AI model weights**. See [backup](docs/backup.md) before relying on them, and keep the [recovery guide](docs/disaster-recovery.md) and backup password available off the server.

## Documentation

| Task | Guide |
| --- | --- |
| Install or resume setup | [Installation](docs/installation.md) |
| Understand paths and settings | [Configuration](docs/configuration.md), [storage](docs/storage.md) |
| Set up DNS, HTTPS and login | [Networking](docs/networking.md), [authentication](docs/authentication.md) |
| Use applications | [Files and dashboard](docs/management.md), [media](docs/media.md), [automation](docs/automation.md), [monitoring](docs/monitoring.md), [AI](docs/ai.md) |
| Access the server remotely | [Remote access](docs/remote-access.md) |
| Maintain and recover | [Operations](docs/operations.md), [releases](docs/releases.md), [backup](docs/backup.md), [recovery](docs/disaster-recovery.md), [troubleshooting](docs/troubleshooting.md) |
| Understand or change the implementation | [Architecture](docs/architecture.md), [security](docs/security.md), [development](docs/development.md), [image sources](docs/images.md) |

## Repository layout

- `install.sh`: public entrypoint.
- `scripts/`: host setup and Python installer/controller.
- `stacks/`: Docker Compose modules and optional overrides.
- `config/`: systemd units and configuration templates.
- `docs/`: setup and operating instructions.
- `tests/`: automated checks and opt-in Docker tests.

CI checks the source and packages releases. It never deploys the server.
