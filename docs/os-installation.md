# Manual operating system installation

Use a Debian 13 (trixie) minimal netinst image from [Debian](https://www.debian.org/distrib/). Verify it against Debian's published signed checksums. The bootstrap supports Debian 13 specifically.

1. Physically disconnect the existing external media HDD before any reinstall. Back up any wanted data on the internal disks before formatting them. Application accounts and configuration will be created from scratch.
2. Identify internal drives by model/serial, not enumeration order. Manually choose the primary OS target and the secondary internal data target. Both internal disks may be formatted by the administrator during this fresh installation; use ext4 for the secondary data filesystem. The external disk must remain disconnected. No repository script performs partitioning or formatting.
3. Install standard system utilities and SSH server only; deselect all desktop environments. Prefer a non-root administrator and sudo. Keep console access available.
4. Install an administrator public key in authorized_keys with owner-only SSH directory permissions. Verify a second SSH session using that key before disabling password authentication.
5. Create an sshd_config.d drop-in with `PermitRootLogin no`, `PasswordAuthentication no`, `KbdInteractiveAuthentication no` and an appropriate AllowUsers value. Verify with `sshd -t`, reload ssh, and keep the original session open until the second login succeeds. Do not change the SSH port unless firewall documentation is updated.
6. Give the server a router DHCP reservation, set timezone Europe/Budapest, and initially retain working external DNS. Do not make the host depend exclusively on AdGuard before recovery DNS is tested.
7. Reconnect drives only after OS installation is complete. Discover UUIDs and inspect current mounts read-only. Let the wizard mount the explicitly confirmed existing ext4 UUIDs, or mount them manually after verifying their identities. Do not run a filesystem repair, reserved-block change or permission migration automatically.
8. Download the verified install.sh release asset and follow the [installation guide](installation.md) for prerequisites, numeric service identities, configuration and application startup.
