# Development

The installer and controller use Python's standard library. Bash handles the public entrypoint and Debian package installation. Each application module has its own Compose file under `stacks/`.

Keep host settings, credentials and application state outside the checkout. Use temporary directories and fake device/account/API boundaries for automated tests. Do not deploy to a real host or change provider accounts as part of validation.

## Required checks

Install Python 3, Docker with the Compose plugin, Make, ShellCheck, shfmt and yamllint. From the repository root, run:

```sh
make validate
make test
shellcheck install.sh scripts/*.sh
shfmt -d -i 2 -ci install.sh scripts/*.sh
yamllint .
python3 tests/check_docs.py
```

Compose validation needs the CLI but no running daemon or host secrets. The Python tests use temporary files and mocked external boundaries. They cover release extraction, the connected installation flow, storage controls, provisioning and recovery failures.

## Optional Docker tests

These tests require a running Docker daemon and can download the pinned images:

```sh
python3 tests/runtime_routes.py
python3 tests/runtime_storage.py
python3 tests/runtime_bind_refresh.py
python3 tests/runtime_authentik_hash.py
python3 tests/runtime_media_init.py
python3 tests/runtime_jellyfin.py
```

They use disposable projects and temporary files, then remove their own resources. They check proxy routing with stub origins, refusal to start on invalid/missing storage, container bind refresh after source replacement, Authentik password hashing before PostgreSQL exists, and LinuxServer PID 1/HTTP startup with disposable state. They do not use real media, credentials or provider accounts, and do not replace the [installation checks](installation-checks.md) on the server.

The routing test also checks Docker DNS resolution of the identity hostname and local HTTPS reachability before and after remote access is disabled. It uses a stub identity origin and an unverified test certificate, so it does not validate real OIDC flows or production certificate trust.

The media startup test uses the Docker daemon's native architecture, no external network and tmpfs application state. Run it on the target architecture when validating that platform; emulation is not proof of target-host compatibility. The Jellyfin test checks non-root startup, read-only media and clean shutdown with disposable state.

See [adding a service](adding-services.md) for implementation requirements and [releases](releases.md) for packaging. CI validates and packages; it never deploys.
