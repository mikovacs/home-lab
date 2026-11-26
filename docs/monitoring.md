# Monitoring

## Availability

Uptime Kuma provides HTTP/TCP/DNS availability and notifications. Create a native administrator through the protected local route, configure checks for Authentik/proxy/apps, certificate expiration warnings where supported, and DNS resolution through AdGuard. Browser-protected endpoints may redirect to SSO; monitor an intentional health endpoint internally or a TCP port rather than treating a login page as application health. Do not grant Kuma broad API credentials or Docker access by default.

## Host metrics

Beszel hub is private basic-authenticated plus native login. The hub alone does not collect host metrics: install its version-matched **host agent** using the official release binary and verified checksum. Follow https://beszel.dev/guide/agent-installation; use the agent command displayed by your local hub to obtain the correct public key/token. Avoid blindly executing an installer script. Put agent settings in a root-only environment file outside Git. The host agent can read CPU/RAM/network/temperature/mount metrics; Docker metrics require Docker API access and therefore root-equivalent trust. If enabling it, review that grant explicitly. No default agent receives Docker access here.

For a Unix socket connection use the shared RUNTIME_ROOT/beszel directory mapped to /beszel_socket in the hub, following the current agent's LISTEN/socket options. Set only the hub-generated KEY/TOKEN, install a host systemd unit with a suitable limited account and writable socket directory, and verify socket owner/mode without recursive changes. Alternatively use an agent loopback/Tailscale address and an explicitly restricted port. Detailed agent invocation is tied to the hub's version-generated connection instructions; no fabricated key is shipped.

## Host alerts and timers

`homelab-monitor.timer` runs monitor.py every 15 minutes. It reports UUID mount availability, disk percentage/free bytes, backup age and optional SMART health. Thresholds default to 80% warning / 90% critical. Configure smart-devices.json as a list of objects with device set to a discovered stable /dev/disk/by-id path and optional type sat for USB. Only smartctl -H is invoked; no tests or repairs. Unsupported hardware reports unavailable. Notifications use protected monitor-webhook.json with url and optional headers, HTTPS only. The script exits nonzero when alerts exist; inspect journalctl for failed units.

The mount guard timer checks every 30 seconds and stops affected storage consumers; it does not auto-restart when a disk returns. Existing bind mounts do not switch to a new filesystem on remount; explicitly redeploy after validation. Enable host smartd only after reviewing device support and notification settings. Use `sensors` when temperature support exists; sensor availability depends on the hardware.

Check backup timer status, last-success marker, weekly Restic integrity results, Docker status/log rotation and host memory pressure. Keep journald bounded with an administrator-reviewed SystemMaxUse setting if needed; default Docker logs already cap at 10m x 3 per service.
