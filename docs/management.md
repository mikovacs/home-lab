# Dashboard and file management

## Homepage

Open `https://home.DOMAIN` after the management stack is started. Homepage links to your applications and shows basic host resource information. Links to optional applications can remain visible while those applications are stopped.

Configuration is generated under `RUNTIME_ROOT/homepage`. `make render` regenerates it, so keep permanent nonsecret layout changes in the renderer. Homepage has no Docker socket and cannot read personal file contents.

Optional API widget examples are in `config/homepage/widgets.example.yaml`. Supply credentials through protected configuration, never Git. Widgets use internal service addresses and native API tokens; do not send them through browser SSO. A Plex widget requires a Plex token with access to that account.

## FileBrowser Quantum

Open `https://files.DOMAIN` after the storage stack is started. You can browse, upload, download, copy, move, rename and delete files in its configured folders. The slim image omits bundled document/video preview helpers.

For the first login:

1. Read the generated `admin` password in the private container logs:

   ```sh
   sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make logs STACK=storage
   ```

2. Sign in through the browser and replace the bootstrap password with a unique password of at least 14 characters.
3. Grant your account access to the personal source and check its scope. The shared source is available to authenticated users.
4. Check access with a harmless test file in the intended folder before enabling remote access.

Do not set a permanent `adminPassword` in rendered configuration: it can reset the password at startup. See the [upstream password flow](https://filebrowserquantum.com/en/docs/configuration/authentication/password/).

The normal sources are `DATA_ROOT/files` and `DATA_ROOT/shared`. The host root, application databases and secrets are not exposed. An optional read-only media source requires the guarded override described in [storage](storage.md). Native local login is the default; [Authentik OIDC](authentication.md) is optional.

Use LAN or private access for large transfers. Remote uploads through Cloudflare are subject to its request limits and proxy timeouts. FileBrowser-visible personal/shared files are **not included** in the application-state backup; arrange a separate backup for them if needed.

## Optional Dockge

Dockge is a private Docker administration interface, disabled by default through its Compose profile. It operates the same stack files and host environment as the installer. Docker socket access gives it full control over the host; enable it only for trusted administrators.

The production source is mounted read-only. Make Compose changes in development, publish a reviewed release, then update through the installer. Dockge's normal file discovery does not enable optional overrides such as Plex acceleration.

After a source release is replaced, recreate Dockge explicitly with its admin profile so its directory bind points to the new source. Keep `REPO_ROOT` and the stack paths consistent inside and outside the container. See [releases](releases.md) and [security](security.md).
