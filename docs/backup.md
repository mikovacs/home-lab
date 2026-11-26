# Backups

Restic runs on the host and saves encrypted application-state snapshots. The default repository is on the secondary data disk. Keep the Restic password and backend recovery credentials somewhere safe **off this server**.

## What is saved

| Included | Excluded |
| --- | --- |
| Installed source and host configuration, including release/progress records | External media and downloads |
| Application state and secrets | Personal/shared files managed through FileBrowser |
| Generated database dumps | AI model weights and Docker image layers |
| | Cache/log/transcode paths listed in `config/restic-excludes.txt` |

Arrange separate backups for excluded files that matter to you. A backup on the same server does not cover theft, fire or loss of both internal disks; keep an offsite copy as well.

## First backup

Use the installer's operations step, or run from the installed source directory:

```sh
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make backup-init
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make backup
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make backup-check
```

Initialize a repository once. Before enabling a schedule, follow the [recovery guide](disaster-recovery.md) to restore a snapshot into a new staging directory and check its files and databases.

## What happens during a backup

1. Record which services are running and stop application writers.
2. Dump running Authentik and n8n PostgreSQL databases, then stop those databases.
3. Save the stopped application state, configuration and secrets with Restic.
4. Recheck storage and restart the previously running services, waiting for startup.

Applications are unavailable during the snapshot, including Traefik and AdGuard. Keep the host on an independent DNS resolver so an offsite backup can resolve its destination while AdGuard is stopped. Schedule backups outside streaming and automation hours.

Restart is attempted even if the backup fails. The success marker advances only after the snapshot and all restarts succeed. If a restart fails, inspect the logs and mounts before redeploying.

A database that was already stopped gets a cold physical backup, not a fresh logical dump. Check the snapshot's dump manifest and timestamps before choosing a dump for recovery.

## Checks and schedules

`make backup-check` reads a 10% sample of repository data. Periodically run a full `restic check --read-data` with the protected backend environment. Neither check replaces a restore drill.

The installer can enable these persistent systemd timers after backup/restore confirmation:

- Backup: daily at 03:15 Europe/Budapest, with up to 20 minutes of random delay.
- Integrity sample: Sunday at 05:00 Europe/Budapest.

Read logs with `sudo journalctl -u homelab-backup.service`. `make backup ARGS=--dry-run` previews the commands without creating a snapshot.

## Retention and offsite storage

Retention keeps 7 daily, 4 weekly and 6 monthly snapshots by default. `make backup-retention ARGS=--dry-run` previews the command; `make backup-retention` applies it. This removes old encrypted backup snapshots, not media files. Retention is not scheduled automatically. Verify recovery before using it.

For an offsite backend, set `RESTIC_REPOSITORY` in the selected host environment and provide credentials in protected `SECRETS_ROOT/restic-env.json`, as a JSON map. The map accepts backend credentials such as AWS keys, not `RESTIC_*` or process execution overrides. SFTP host keys and access need separate trusted setup. This controller still requires the verified local data mount.

Initialize each destination deliberately. If you replicate snapshots to another repository, use supported Restic operations and check recovery at that destination. Never point a synchronization or cleanup job at the retained media library.
