# Recovery without media damage

Run maintenance commands from the recovered source directory with `HOMELAB_ENV_FILE` pointing to the recovered host settings. Keep affected applications stopped until their state and credentials are ready.

## Restore application state

1. Keep the external media disk physically disconnected during OS reinstallation. Install Debian manually, recover SSH, reconnect disks, and use read-only preflight to verify their recorded UUIDs. Never format an unknown disk or infer identity from /dev/sdX numbering.
2. Recover the matching release source and its protected release record from a trusted backup, or download and verify the matching release assets. Install Docker/Restic, recover the **offline** Restic password/backend access, configure the verified mounts and .env, and keep all services stopped. If the secondary backup drive is lost, use the offsite repository.
3. Choose a specific snapshot ID using restic snapshots under the protected backend environment. Run `make restore SNAPSHOT=<id> TARGET=<RUNTIME_ROOT>/restore/<new-name>`. The target must not exist and must be inside runtime/restore. Restic verifies restored data. The script never overlays live paths or the media library. Inspect directory nesting: absolute original paths are represented under the staging tree.
4. Verify the snapshot manifest/time, expected secrets, versions and database files before promotion. Copy only the intended **application configuration/state** into empty application destinations while all affected writers are stopped, preserving recorded ownership. No recursive permission rewrite of existing media is allowed. Recover the external host configuration directory and recorded release identity on installer-managed hosts, select its HOMELAB_ENV_FILE, then regenerate ignored stack .env symlinks with prepare; do not blindly reuse stale absolute symlinks.
5. For portable PostgreSQL recovery, create a new empty database directory for the matching image/version, initialize with the recovered password, then import the verified snapshot's corresponding dump with psql -v ON_ERROR_STOP=1. Stop dependent Authentik/n8n writers throughout. Do not mix an old logical dump with a newer physical state. Alternatively restore the cold physical directory using the **same PostgreSQL major version** first; major upgrades need pg_dump/restore or documented pg_upgrade.
6. Restore Authentik database and secret key together; n8n database/state and encryption key together; Open WebUI database and session/OAuth key together. SQLite app directories were copied only after stopping their containers. Restore Plex/*arr/qBittorrent configuration without touching /mnt/plex-media contents. Test native accounts and imports on isolated routes before enabling remote access.
7. Start infrastructure/identity, verify authentication, validate media mount, then start applications incrementally. Test hardlinked files by metadata, do not redownload them. Re-enable timers only after a successful new backup and staged restore verification.

## Practice recovery

A non-destructive restore drill should use a disposable empty staging target and isolated Compose project/networks/ports. Do not point test apps at the real media mount or production database paths. Validate a database import into a disposable database, file checksums, account login and restored credentials without sending real webhook actions. Record date, snapshot ID, duration and result in an untracked operations log. Repository tests do not replace this drill.

## Roll back an update

Restore the previous reviewed Compose pins and matching pre-upgrade database/app state into clean appdata paths after stopping writers. Keep failed state for investigation. Container image downgrade alone cannot undo schema migrations; never assume reverse migrations exist. Media requires no restoration because it was excluded and never overwritten.
