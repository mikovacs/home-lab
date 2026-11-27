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

Use the wizard for initial application selection. AI and the terminal need their [AI](ai.md) and [remote-access](remote-access.md) setup. Bazarr and Dockge remain opt-in: retain `COMPOSE_PROFILES=bazarr,admin` in the selected host environment to include them in normal media/management deployments, backups, stops and boot startup. The wizard asks for this selection in step 8; use `none` to leave both disabled. The shared stack `.env` links also make this selection available to Compose and Dockge. The controller ignores inherited shell profile settings and accepts only these two persistent profiles; tunnel and terminal activation remain explicit. Optional Compose override files are not enabled by this setting.

## After reboot or a missing disk

Media, FileBrowser, Homepage, Dockge and AI have Docker automatic restart disabled so daemon startup cannot bypass filesystem checks. Install the repository's systemd units, then enable only the configured stacks in installer step 11: `homelab-media.service`, `homelab-storage.service`, `homelab-management.service` and, after AI onboarding, `homelab-ai.service`. Installing unit files alone enables nothing. AI remains opt-in.

These units invoke the ordinary Compose deployment with the persisted profiles. Media is bound to the media mount; storage, management and AI are bound to the data mount. Startup rechecks UUIDs and prerequisites before recreating bind mounts. Enabled stacks start in media, storage, management, AI order; a missing media disk does not prevent data-only stacks from starting. Boot jobs wait for the controller's operation lock within the systemd startup timeout instead of colliding with another deployment or backup. A timeout is a failed job requiring inspection, not permission to bypass a check.

Enabled stack units also follow Docker service stop/restart operations and are wanted by Docker on startup. A Docker restart therefore repeats the same guarded deployment. Re-run `systemctl enable` for already-enabled media units after installing updated unit files so the Docker dependency link is added.

On an existing release-managed host, update the release, save the desired profiles outside source, run `make install-systemd` with `HOMELAB_ENV_FILE`, and enable the selected units. Start or restart them in the order above to test the configuration before rebooting. Recreating management also changes existing Dockge containers to guarded startup; do not reboot between updating that restart policy and enabling its unit. Verify `systemctl --failed`, container state, DNS and application login after reboot. Container startup alone does not prove browser authentication or data access.

The mount guard stops affected consumers when a required filesystem is missing. Reconnecting a disk does not restart them. Check the UUID and mount, then use `make deploy-<stack>` to recreate the affected containers. Do not create replacement folders on the OS disk or bypass the guard.

## Backups and updates

Use the wizard's operations step to initialize backups, install systemd units and select timers. A backup pauses applications while it saves consistent state. Follow [backup](backup.md) for scope and schedules.

Use the wizard's update step for a new source release. Settings and secrets remain in place; application redeployment is explicit. `make update STACK=<stack>` instead backs up state, pulls the image versions already selected by the current source and redeploys that stack. It does not download a new source release. See [releases](releases.md).

Inspect scheduled jobs with `systemctl list-timers 'homelab-*'` and backup logs with `sudo journalctl -u homelab-backup.service`. Use [troubleshooting](troubleshooting.md) when a check fails. SSH by the server's numeric LAN IP remains available independently of DNS and browser login.
