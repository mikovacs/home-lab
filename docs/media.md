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
  LM --> P[Plex read-only]
  LT --> P
```

Radarr, Sonarr and qBittorrent all mount the **whole same parent** at /data. Never introduce separate /downloads and /movies bind mounts: Docker mount boundaries can break hardlink/atomic rename behavior even with the same underlying disk. Prowlarr only needs its configuration; Plex sees /data read-only. No remote path mapping should be needed with consistent container paths.

The LinuxServer applications use their bundled s6 process supervisor as PID 1. Keep `init: false` for these services; Docker's additional init process prevents s6 from starting. The mount guard uses Docker init independently.

## First setup

1. Claim Plex locally using its supported setup at LAN_IP:32400/web, retaining Plex authentication. Add movie library /data/media/movies and TV library /data/media/tv. Disable any automatic library-file deletion behavior you do not want. Native remote streaming is separate from Cloudflare; see [remote access](remote-access.md).
2. Set qBittorrent's initial random admin password immediately using private logs/local access. Set incomplete path /data/downloads/torrents/incomplete and completed path /data/downloads/torrents/complete. Create categories movies and tv with their matching complete subdirectories. Use listen port 6881 unless deliberately changing the container setting and firewall. Set a ratio/time seeding policy appropriate to trackers; default cleanup must not delete media.
3. In Radarr set root folder /data/media/movies; in Sonarr /data/media/tv. Enable use hardlinks instead of copy and completed download handling. Add qBittorrent at http://qbittorrent:8080 with native credentials and distinct categories. Retain native authentication/API keys. Set download-client remove-completed behavior off until the seeding/cleanup policy has been tested.
4. Use existing-library import, choosing the existing root folders. Do not trigger a missing-media search or quality upgrade. Keep imported movies/series unmonitored initially if you do not want automatic downloads/replacements. Review profiles, upgrade cutoff and naming before enabling monitoring; no automatic size-reduction/transcoding workflow is supplied.
5. Configure Prowlarr indexers and sync only explicitly selected applications using native API keys and internal service names. No indexer credentials are included. Optional Bazarr starts with `docker compose --profile bazarr up -d` from stacks/media after mount validation; subtitle writes are expected, media cleanup is not.

## Check hardlinks

Hardlink validation is read-only by default: compare `stat` device/inode/link counts for a **known existing** download/library pair. Matching device+inode and link count at least 2 prove the relationship. Do not assume different filenames imply different data. The controller verifies path/device compatibility without creating a probe. To test an actual import, an administrator may choose a small expendable legal test file outside the existing library and inspect its inode; do not use the real collection as a test fixture.

Delete neither side automatically. After seeding, removal of a torrent/download hardlink leaves the imported library link, but only after you verify the files are linked and the client no longer needs them. Never use destructive rsync or broad recursive deletion against the collection.

## Optional hardware transcoding

For Intel Quick Sync, inspect /dev/dri and render GID, confirm supported codecs/driver with vainfo, set RENDER_GID and optionally DRI_PATH, then review acceleration.yaml. Start with both Compose files explicitly and preserve the mount guard. The standard deploy command does not automatically add this override; keep an approved local invocation or reviewed config change, and mirror it in systemd if needed. Hardware transcoding may require Plex Pass; verify current Plex licensing. Prefer Direct Play and test one representative codec before increasing concurrency.
