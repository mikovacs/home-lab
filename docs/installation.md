# Installation

Use `install.sh` to launch the numbered Python console installer. It prepares Debian, configures supported services and starts selected stacks. OS installation and disk formatting remain manual.

## Start

Follow [OS preparation](os-installation.md): disconnect the external media disk while installing Debian 13 and preparing both internal disks. Establish sudo and key-based SSH before reconnecting media.

For the private `mikovacs/home-lab` repository, sign in to [GitHub Releases](https://github.com/mikovacs/home-lab/releases), select one release and download its `install.sh` and `SHA256SUMS` assets. Transfer both files to the same directory on the Debian server. Verify them there, review the script, then start:

```sh
sha256sum --ignore-missing -c SHA256SUMS
sudo bash install.sh --repo mikovacs/home-lab --private
```

The installer asks for a GitHub token with read access to this repository's release assets. Input is hidden and the token is not saved. Do not put it in the command line or a configuration file.

For a public fork, replace `OWNER/REPOSITORY` below. On minimal Debian, install the download tools, fetch both release assets, verify the checksum and review the script before running it:

```sh
sudo apt-get update
sudo apt-get install -y --no-install-recommends ca-certificates curl
curl --fail --location --proto =https https://github.com/OWNER/REPOSITORY/releases/latest/download/install.sh -o install.sh
curl --fail --location --proto =https https://github.com/OWNER/REPOSITORY/releases/latest/download/SHA256SUMS -o SHA256SUMS
sha256sum --ignore-missing -c SHA256SUMS
sudo bash install.sh --repo OWNER/REPOSITORY
```

The packaged installer contains its release manager; it needs no existing checkout or Python installation. Bash checks Debian 13, root and an interactive terminal, then asks before installing missing Python 3 and CA certificates. Docker installation is a later wizard step.

The manager downloads the published `homelab-source.tar.gz` asset, checks its SHA-256 against `SHA256SUMS`, extracts it safely and records file integrity. It uses stable `vMAJOR.MINOR.PATCH` releases, never a branch or Git clone. The first release must be [published separately](releases.md).

From a source checkout, `sudo ./install.sh` opens that checkout's wizard directly. The source script is not a standalone release asset. `--help` and `--dry-run` change nothing; dry-run prints a plan rather than validating the host. Download to a file instead of piping into Bash.

## Guided steps

| Step | Action |
| --- | --- |
| 1. Settings | Domain, LAN interface/address, UUIDs, paths, certificate email and existing SSH administrator |
| 2. Bootstrap | Debian tools, Docker/Compose, time synchronization and security updates |
| 3. Storage | Confirm existing ext4 devices, mount/persist UUIDs and select media-compatible numeric user/group IDs |
| 4. Preparation | Create missing service identities, new directories, keys and networks; start no applications |
| 5. Credentials | Verify Cloudflare zone/DNS token; collect private dashboard, Authentik and AdGuard credentials |
| 6. Foundation | Check prerequisites and start infrastructure/identity |
| 7. Identity/DNS | Provision AdGuard and Authentik; confirm MFA, access denials and router DNS setup |
| 8. Applications | Select stacks, start them and complete native account/media onboarding; AI is optional |
| 9. Tunnel | Create or verify locally managed tunnel credentials; optional for local-only operation |
| 10. Publication | Select exact hostnames, verify origin TLS, create DNS CNAMEs and start the tunnel |
| 11. Operations | Initialize/check backups; install units and explicitly select firewall, timers and boot services |
| 12. Update | Review a stable source release, back up prepared state and reopen the new wizard |

`p` runs read-only discovery; `d` closes local remote routes; `0` exits. Use `back` or `cancel` in ordinary forms. A blank secret cancels its step. After interruption, inspect the result before retrying a provider operation.

## Configuration and resume

Default host-only directory: `/etc/homelab`, root-owned mode 0700:

- `host.env`: nonsecret settings, mode 0600.
- `wizard.json`: progress and onboarding confirmations.
- `release.json`: repository, version, archive checksum and installed-file hashes.
- `tunnel-pending.json`: protected tunnel-creation recovery material.

Credential setup hashes the Authentik administrator password with the pinned image's Python password hasher, without starting PostgreSQL or the Authentik services. Password input is passed over standard input, not as a command argument. Existing saved credentials are retained when this step is retried.

Credentials live under `SECRETS_ROOT`. Stack `.env` files are ignored symlinks to the selected host environment; tracked Compose files stay unchanged. Resume through the wizard. For manual maintenance, use the same environment:

```sh
sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make status
```

Do not create a second configuration with divergent values. Choosing a custom data root also moves the default local Restic repository beneath that root; an explicitly configured external backend is retained. Preparation can resume with its recorded roots, UUIDs and user IDs; it does not reset existing state. After preparation, the settings form locks storage identity and domain/LAN settings. Changing them requires a separate migration of mounts, DNS, identity and routes. Application redeployment invalidates its native-onboarding approval and closes remote routes until explicitly approved again.

## Personal checks and failures

You still complete MFA enrollment/recovery, allowed/denied-user tests, router DHCP, Plex claim/libraries and other applications' native owners/API keys. Optional Tailscale, Beszel agent, Samba and terminal SSH keys require their documented manual setup. Open WebUI starts with signup disabled: use the external first-owner bootstrap override in [AI onboarding](ai.md) before publishing it. Backup scheduling requires a successful staged restore confirmation.

Missing mounts, secrets, conflicting provider records or failed TLS checks stop the affected step without deleting existing state. Never create media directories under an unmounted path. Ambiguous tunnel creation and partial DNS publication need the [remote-access recovery procedure](remote-access.md). Failed backups block source updates; source updates do not automatically redeploy applications.

Finish the [installation checks](installation-checks.md), including browser login, file access and a staged restore. Then use [daily operation](operations.md) for maintenance.
