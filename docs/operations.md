# Daily operation

Most daily work happens in the browser. Use Homepage at `https://home.DOMAIN` to open applications and FileBrowser at `https://files.DOMAIN` to manage your configured folders. See [application setup](management.md) for the first login.

## Run maintenance commands

Run commands from the installed source directory, normally `/srv/homelab/repository`. Always select the host environment used by the installer. Replace `/etc/homelab/host.env` if you chose another location.

```sh
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make status
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make healthcheck STACK=storage
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make logs STACK=storage
```

`status` lists containers. `healthcheck` checks the selected stack's expected services and container health; it does not test browser login or file permissions. Logs can contain one-time passwords, so keep them private.

## Start and stop a stack

```sh
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make deploy-storage
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make stop STACK=storage
```

Replace `storage` with the required stack: `infrastructure`, `identity`, `media`, `automation`, `monitoring`, `management`, `remote-access` or `ai`. Finish the relevant installer steps first. Deployment checks prerequisites, recreates containers and waits for startup. It does not finish native application account setup.

Use the wizard for initial application selection. AI and the terminal need their [AI](ai.md) and [remote-access](remote-access.md) setup. Bazarr, Dockge and Compose overrides are opt-in and are not enabled by an ordinary stack deployment.

## After reboot or a missing disk

FileBrowser, Homepage and AI have automatic restart disabled. Start their stacks explicitly after the disks are mounted. Media starts on boot only if you enabled `homelab-media.service` through the installer. That service depends on the media mount.

The mount guard stops affected consumers when a required filesystem is missing. Reconnecting a disk does not restart them. Check the UUID and mount, then use `make deploy-<stack>` to recreate the affected containers. Do not create replacement folders on the OS disk or bypass the guard.

## Backups and updates

Use the wizard's operations step to initialize backups, install systemd units and select timers. A backup pauses applications while it saves consistent state. Follow [backup](backup.md) for scope and schedules.

Use the wizard's update step for a new source release. Settings and secrets remain in place; application redeployment is explicit. `make update STACK=<stack>` instead backs up state, pulls the image versions already selected by the current source and redeploys that stack. It does not download a new source release. See [releases](releases.md).

Inspect scheduled jobs with `systemctl list-timers 'homelab-*'` and backup logs with `sudo journalctl -u homelab-backup.service`. Use [troubleshooting](troubleshooting.md) when a check fails. SSH by the server's numeric LAN IP remains available independently of DNS and browser login.
