# Existing media onboarding

Never rename or move the legacy /movies and /series directories automatically. Main libraries remain media/movies and media/tv. Run `make media-validate` before every start. No script formats disks, reorganizes media, changes reserved blocks or recursively changes ownership. Inspect existing IDs and ACLs first; choose compatible PUID/PGID. If permissions genuinely block access, propose a narrowly scoped, separately approved migration rather than running chown -R.

```mermaid
flowchart LR
  QB[qBittorrent] --> I[/data/downloads/torrents/incomplete]
  I --> M[Complete category movies]
  I --> T[Complete category tv]
  M --> RM[Radarr import]
  T --> ST[Sonarr import]
  RM --> LM[/data/media/movies]
  ST --> LT[/data/media/tv]
  M -. same inode hardlink .-> LM
  T -. same inode hardlink .-> LT
  LM --> P[Jellyfin read-only]
  LT --> P
```

Radarr, Sonarr and qBittorrent all mount the **whole same parent** at /data. Never introduce separate /downloads and /movies bind mounts: Docker mount boundaries can break hardlink/atomic rename behavior even with the same underlying disk. Prowlarr only needs its configuration; Jellyfin sees /data read-only. No remote path mapping should be needed with consistent container paths.

The LinuxServer applications use their bundled s6 process supervisor as PID 1. Keep `init: false` for these services; Docker's additional init process prevents s6 from starting. The mount guard uses Docker init independently.

## First setup

1. Create the Jellyfin owner locally at LAN_IP:8096, retaining native authentication. Add movie library /data/media/movies and TV library /data/media/tv. Disable any automatic library-file deletion behavior you do not want. Native remote streaming is separate from Cloudflare; see [remote access](remote-access.md).
2. Set qBittorrent's initial random admin password immediately using private logs/local access. Set incomplete path /data/downloads/torrents/incomplete and completed path /data/downloads/torrents/complete. Create categories movies and tv with their matching complete subdirectories. Use listen port 6881 unless deliberately changing the container setting and firewall. Set a ratio/time seeding policy appropriate to trackers; default cleanup must not delete media.
3. In Radarr set root folder /data/media/movies; in Sonarr /data/media/tv. Enable use hardlinks instead of copy and completed download handling. Add qBittorrent at http://qbittorrent:8080 with native credentials and distinct categories. Retain native authentication/API keys. Set download-client remove-completed behavior off until the seeding/cleanup policy has been tested.
4. Use existing-library import, choosing the existing root folders. Do not trigger a missing-media search or quality upgrade. Keep imported movies/series unmonitored initially if you do not want automatic downloads/replacements. Review profiles, upgrade cutoff and naming before enabling monitoring; no automatic size-reduction/transcoding workflow is supplied.
5. Configure Prowlarr indexers and sync only explicitly selected applications using native API keys and internal service names. No indexer credentials are included. Retain `bazarr` in the host environment's `COMPOSE_PROFILES` to include optional Bazarr in normal media deployment and `homelab-media.service` boot startup. For one-time use, start it with `docker compose --profile bazarr up -d` from stacks/media after mount validation. Subtitle writes are expected, media cleanup is not.

## Check hardlinks

Hardlink validation is read-only by default: compare `stat` device/inode/link counts for a **known existing** download/library pair. Matching device+inode and link count at least 2 prove the relationship. Do not assume different filenames imply different data. The controller verifies path/device compatibility without creating a probe. To test an actual import, an administrator may choose a small expendable legal test file outside the existing library and inspect its inode; do not use the real collection as a test fixture.

Delete neither side automatically. After seeding, removal of a torrent/download hardlink leaves the imported library link, but only after you verify the files are linked and the client no longer needs them. Never use destructive rsync or broad recursive deletion against the collection.

## Jellyfin setup and access

Jellyfin runs as a normal media-stack service, with its own configuration/database in `APPDATA_ROOT/jellyfin` and disposable cache/transcodes in `RUNTIME_ROOT/jellyfin-cache`. Application backups include the configuration, but not the cache. The existing media bind is read-only at `/data`. Leave at least 2 GiB free on the configuration filesystem for Jellyfin's startup check, plus space for growing metadata and transcodes.

Finish native owner setup immediately through `http://LAN_IP:8096` or local `https://jellyfin.DOMAIN`. Create a separate non-administrator playback account, disable media deletion for it, and restrict it to the desired libraries. The shipped setup uses native Jellyfin authentication and Quick Connect. Authentik SSO requires a separately reviewed plugin/client integration; an interactive ForwardAuth layer is not placed in front of native client/API traffic.

Add movies at `/data/media/movies` and series at `/data/media/tv`; add legacy paths only when they actually contain the desired collection. Disable artwork/NFO storage beside the media, because the bind is read-only. Start with trickplay/chapter image extraction disabled. Prefer Direct Play before enabling CPU-heavy transcoding. The default memory cap is 2 GiB and the CPU cap follows `SERVICE_CPUS`.

Enter the server address manually in native clients. No discovery UDP ports or DLNA are enabled. Local HTTPS uses Traefik and split DNS; streaming is excluded from the Cloudflare Tunnel allowlist. Authorized Tailscale clients can use the same HTTPS address after their route, DNS and grants are configured. TCP 8096 is deliberately LAN-only. See [remote access](remote-access.md#jellyfin-streaming).

## Replace an existing Plex installation

Take an application backup before switching source releases. The release installer does this with the installed release so the old Plex container is paused consistently. After updating, stop and remove only the verified old Plex container before starting Jellyfin. Compose does not automatically remove a service that disappeared from its configuration; do not use broad orphan/container pruning.

For the default project and service name, inspect the Compose labels first:

```sh
sudo docker inspect homelab-media-plex-1 --format '{{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}}'
```

Expect `homelab-media plex`. Then stop and remove that container without deleting bind-mounted data:

```sh
sudo docker stop homelab-media-plex-1 && sudo docker rm homelab-media-plex-1
```

Keep `APPDATA_ROOT/plex` and the backup for recovery. Do not rename `/mnt/plex-media`, change its UUID/mount configuration, move existing libraries or alter their permissions. Plex viewing history and metadata are not automatically imported into Jellyfin. Update existing Kuma monitors and imported n8n workflows separately; source changes do not modify their saved application databases.

From the installed source directory, select the external host environment and run `make prepare` to create Jellyfin's new configuration/cache directories; existing ownership is untouched. Re-render the configuration, reapply the reviewed firewall, and start only the new `jellyfin` service through the controller's Compose wrapper after media mount validation. This avoids restarting unrelated media tools. The already-enabled media boot unit includes Jellyfin automatically; `COMPOSE_PROFILES=bazarr,admin` remains valid.

Add the exact local AdGuard rewrite `jellyfin.DOMAIN -> LAN_IP` before testing HTTPS. Remove the obsolete local `plex.DOMAIN` rewrite after confirming the new route. No public DNS or router change is required for LAN/Tailscale access. The updated firewall removes TCP 32400 access and permits Jellyfin TCP 8096 only from the LAN.

## Optional hardware transcoding

For Intel Quick Sync, inspect `/dev/dri` and the render GID, confirm supported codecs/driver with `vainfo`, set `RENDER_GID` and optionally `DRI_PATH`, then review `acceleration.yaml`. It adds the render device and supplementary group to Jellyfin's non-root user. Start with both Compose files explicitly and preserve the mount guard. The standard deploy command does not automatically add this override; keep an approved local invocation and mirror it in systemd if needed. Prefer Direct Play and test one representative codec before increasing concurrency.
