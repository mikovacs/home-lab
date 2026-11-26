# Storage preservation

```mermaid
flowchart TB
  OS[Primary internal drive] --> Repo[/srv/homelab/repository]
  OS --> State[/srv/homelab/appdata]
  OS --> Secrets[/srv/homelab/secrets]
  OS --> Runtime[/srv/homelab/runtime]
  Data[Secondary UUID mount] --> Files[/mnt/homelab-data/files + shared]
  Data --> Models[/mnt/homelab-data/ai/models]
  Data --> Backup[/mnt/homelab-data/backups]
  USB[Existing external ext4 UUID mount] --> Downloads[/mnt/plex-media/downloads]
  USB --> Library[/mnt/plex-media/media/movies + tv]
```

## Identify and mount the disks

Record UUID, serial, model, size, filesystem and mount point before making changes. Preserve the external disk's existing ext4 settings; no reserved-block tuning is performed. Legacy /movies and /series are left untouched. Do not choose /dev/sda1 or infer identities from size alone. Keep downloads and library on the external filesystem to preserve existing hardlinks.

For fresh disks, mount-storage probes both existing ext4 UUID devices before any action and requires --confirm-storage to mount them. It refuses to hide nonempty directories or mount the OS filesystem. It never creates filesystems. Mounts must exist before configure-storage proposes fstab entries; bootstrap never mounts disks. configure-storage accepts only ext4 and requires --confirm-storage to edit /etc/fstab. It refuses conflicting entries and backs up the original. Manually review an existing non-ext4 secondary drive rather than coercing its fstab type. `nofail` permits emergency SSH boot without disks; services still fail their own UUID gates.

## Startup and missing disks

Media validation checks exact findmnt target, UUID, ext4, rw mode, a live matching block device, filesystem separation from /, required canonical directories, common device ID, POSIX traversal permissions, free capacity and visible entries. It does not create test files or links. Writeability warnings require manual ACL/ownership review. POSIX mode inspection cannot completely model ACLs or remote user mapping; validate a harmless approved upload separately. No recursive metadata changes are authorized by installation.

New fstab entries use `0 0`: no boot-time filesystem check/repair is scheduled. An existing nonzero pass number blocks configuration until manually reviewed; entries are never silently rewritten. [Debian systemd-fsck](https://manpages.debian.org/trixie/systemd/systemd-fsck.8.en.html) documents how nonzero pass numbers enable checks with automatic repair by default. Repair remains a separate manual decision.

The in-Compose guard independently checks expected UUID device number against the bind-mounted filesystem and validates canonical media directories, including their parents. Data consumers have a similar guard. Every bind uses create_host_path:false. Media, files, Homepage and AI do not auto-restart; install and explicitly enable the media systemd mount binding for media boot. Files, Homepage and AI still require explicit deployment after reboot. The mount timer attempts every affected stack even if one stop fails, and reports failures. Missing media also stops storage conservatively because its optional media overlay may be active. Remount/redeploy is explicit. Never point DATA_ROOT or MEDIA_ROOT at a folder on the OS drive.

## FileBrowser access

FileBrowser Quantum sees only personal and shared paths. Personal source is not automatically granted to every user; grant per-user scope in the UI. Shared source is granted to authenticated users. The optional media-readonly.yaml adds a separate media UUID guard and device bind. It must be reviewed, the media mount validated before use, and the media source explicitly added to the protected file manager config. Its mount is read-only. Do not mount appdata, secrets, Docker config, SSH keys or host /.

## Optional Samba

Samba is optional host integration. Install samba and samba-vfs-modules; create a dedicated local group/user and password using interactive smbpasswd; adapt config/samba/homelab.conf and run testparm. Bind only LAN interfaces, set `interfaces = lo <LAN-interface>` / `bind interfaces only = yes`, disable SMB1 and guest access, require signing where compatible. Add explicit LAN-only TCP 445 host firewall rules before the final drop; default firewall does not open Samba. Media share remains read-only. Add systemd RequiresMountsFor/BindsTo for both mounts before enabling smbd. Finder uses smb://<LAN-IP>/personal. No permission migration is automatic.
