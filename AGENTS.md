# Repository guidance

Single-node Debian 13 HomeLab, modular Docker Compose stacks. Keep source, comments and documentation in English. Use Python's standard library for the installer/controller; Bash handles entrypoint prerequisites and host package installation.

## Safety boundaries

- OS installation and disk formatting are manual. Never automate formatting, partitioning, repair, media deletion/reorganization or recursive permission changes on existing storage.
- Preserve external media and hardlinks. Keep common /data binds, UUID/device checks, create_host_path:false, guarded startup and systemd mount dependencies. Storage changes require explicit confirmation.
- Keep secrets outside Git (files 0600, directories 0700). Never evaluate host configuration as shell code. Do not publish databases, Docker APIs or private administration.
- Treat Docker socket access as host-root authority. Keep discovery behind its restricted proxy and management private. Cloudflare ingress is locally managed and explicitly allowlisted; do not replace conflicting DNS records or accept remote connector tokens as local credentials.
- Do not execute real-host deployments or provider mutations unless the user has authorized them. Tests use temporary files and mocked accounts/devices/APIs.

## Conventions

- install.sh is the public entrypoint. Shared implementation lives in scripts/lib; Compose files live in stacks/<module>.
- Use one host environment selected by HOMELAB_ENV_FILE; the wizard stores it outside the checkout. Stack .env symlinks must point to that same file.
- Keep production source at its recorded release. Never reset/clean/stash local edits to force an update. Back up prepared application state before changing release; restore only to a new staging directory.
- Pin maintained image versions in Compose, review upstream documentation/licenses, and keep AI opt-in. Do not add heavyweight infrastructure without an architectural reason.
- Keep documentation focused on the current system: setup, usage, configuration and recovery. Update it with behavior or architecture changes; do not add audit reports, decision histories or changelogs. Do not claim verification that has not occurred.

## Verification

Run make validate, make test, ShellCheck and shfmt on install.sh and scripts/*.sh, yamllint, and tests/check_docs.py. Add regression tests for changed safety behavior. CI validates and packages releases; it never deploys the HomeLab.
