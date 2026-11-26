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
```

They use disposable projects and temporary files, then remove their own resources. They check proxy routing with stub origins, refusal to start on invalid/missing storage, and container bind refresh after source replacement. They do not use real media, credentials or provider accounts, and do not replace the [installation checks](installation-checks.md) on the server.

See [adding a service](adding-services.md) for implementation requirements and [releases](releases.md) for packaging. CI validates and packages; it never deploys.
