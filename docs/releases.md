# Stable releases and updates

Production downloads release assets, without cloning Git. Settings, secrets and rendered files remain outside source. A published stable `vMAJOR.MINOR.PATCH` release is required.

## Publish

Commit reviewed source, run validation/tests, then push a stable version tag. The [release workflow](../.github/workflows/release.yaml) publishes standalone `install.sh`, Git-tracked `homelab-source.tar.gz` and `SHA256SUMS`. Ignored host settings/secrets are not packaged. CI never deploys the server.

The installer downloads assets through GitHub's API, verifies the archive checksum, rejects unsafe paths/links/special files and records installed-file hashes and permissions. Private tokens are session-only and are stripped on cross-host download redirects. Git is not required to obtain production source. Checksums detect corruption; trust in the release maintainer is still required.

## Update

Choose the console's update option or rerun the packaged launcher:

```sh
sudo bash install.sh --repo mikovacs/home-lab --private --update
```

For a public fork, change the repository name and omit `--private`. Normal resume verifies installed files offline; update checks need GitHub access. Modified/extra files, unexpected symlinks, unmanaged targets and lower versions block updates. Checking the same version verifies that its archive has not changed. The selected host `.env` symlinks are the only permitted generated source entries.

The new archive is downloaded and verified in staging first. After confirmation, prepared application state is backed up with the old controller. Backup failure leaves source unchanged. The old source directory is preserved as a sibling `.NAME-previous-<id>`; the staged release replaces the configured source path and its record is saved. Host settings and secrets stay in place.

**Applications are not automatically redeployed.** Review release notes, revisit foundation/application steps and start selected stacks explicitly. Deployment recreates containers so source/storage binds attach to current paths. Recreate optional Dockge explicitly with its admin profile after a source replacement; its existing directory bind still points to the preserved old source.

Make source changes in development, then publish a reviewed release. Do not edit production source through Dockge or discard local edits to force an update. Automatic source updates require an installer-managed release and its matching release record.

## Interrupted switch and rollback

`release-pending.json` records an in-progress directory switch. Its presence blocks automatic continuation. Inspect its target/previous-source paths, recorded hashes and current `release.json` before reconciling manually; do not delete application state. The old source is retained, and no arbitrary existing directory is removed. A failed download/check leaves the installed release unchanged.

Rollback needs the intended source and matching release record, plus pre-update application/database state if migrations ran. An image downgrade cannot undo schema changes. Follow [staged recovery](disaster-recovery.md). Preserve old source until acceptance; its removal is a separate deliberate maintenance task.
