# Hardware and prerequisites

Use a Debian 13 server with a primary OS/application drive, a separate ext4 data drive and the existing external ext4 media drive. The server needs a stable LAN IPv4 address and internet access for packages, container images and certificates.

## Inspect the host

Use the installer's `p` option, or run `make preflight` on the server. This reads hardware, storage, networking and software information without changing the host. Sudo allows more process and device information to be shown. Missing tools are reported.

Before setup, identify:

- CPU architecture, cores and available RAM/swap.
- Each disk's model, serial, filesystem, UUID, mount point and free space.
- Existing media ownership and permissions, to choose compatible service IDs.
- LAN interface, IP address and subnet.
- Existing listeners on ports 22, 53, 80, 443, 32400 and 6881.
- Docker/Compose versions and the firewall backend, if already installed.

Keep this host inventory outside Git. Never identify a disk only by size or a name such as `/dev/sda`.

## Memory and acceleration

Start infrastructure and identity first, then add applications while watching available memory. Below 4 GiB, use fewer services or add RAM. On 4–8 GiB systems, omit AI initially. AI deployment requires at least 8 GiB total RAM, plus enough free memory for the chosen model after other workloads are running. Container limits do not make an oversized model fit.

For Intel media acceleration, inspect `lspci`, `/dev/dri` and the `render` group. Use `vainfo` with the matching driver to check codec support. A render device alone does not prove that transcoding works. See [media setup](media.md) for the optional device override.

SMART over USB depends on the adapter and may require `smartctl -a -d sat <device>`. Use the device identified during discovery. Unsupported SMART access does not justify changing or repairing the filesystem.
