# Installation checks

Complete these checks on the server before relying on it. Skip optional features you have not enabled. Keep host-specific details and recovery material outside Git.

## Host and storage

- Confirm disk model/serial and UUIDs match the selected data and media mounts.
- Check that existing media ownership, permissions and known hardlinks are unchanged.
- Confirm SSH works by numeric LAN IP using your administrator key, including after reboot. Keep console access available.
- Check Docker/Compose, time synchronization, security updates and firewall rules.
- Check that storage-dependent services refuse startup when their mount is unavailable. Use a maintenance window without active writes; do not unplug a busy disk.

## Login and applications

- Open application names through AdGuard DNS and check that HTTPS certificates are trusted.
- Enroll Authentik MFA, save recovery material and test both an allowed and a denied account.
- Complete each application's native owner setup. No initial-registration page should be publicly reachable.
- Check FileBrowser personal/shared permissions with a harmless test file.
- Confirm Jellyfin reads the existing library. Test download/import hardlinks only with an expendable file, not the collection.
- Check n8n webhook authentication before activating workflows. Keep unconfigured examples inactive.
- Configure availability, disk and backup alerts. Check the Beszel host agent if enabled.

## Recovery and remote access

- Create a backup, run an integrity check and restore into a new staging directory. Test database imports separately and keep backup keys off the server.
- Check enabled timers and boot services. FileBrowser, Homepage and AI need explicit startup after reboot.
- If using Cloudflare Tunnel, test allowed hosts, rejection of private administration hosts, and the installer's remote-disable option.
- Check CPU, memory and disk usage with the selected stacks running. Enable AI or hardware acceleration only after checking their requirements.

Use [troubleshooting](troubleshooting.md) for failures and [daily operation](operations.md) for maintenance commands.
