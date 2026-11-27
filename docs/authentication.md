# Identity and authorization

The installer waits for Authentik bootstrap and default blueprints, then provisions through its loopback API: HomeLab access groups, single-application ForwardAuth providers, application/group bindings, embedded outpost links and required MFA enrollment. It adds the bootstrap administrator to `homelab-admins` without making ordinary access groups superusers.

Enroll your MFA device, retain offline recovery material, create a named administrator and test both allowed and denied users before confirming `IDENTITY_READY=yes`. Router DNS and browser acceptance remain personal steps. For manual recovery, Authentik port 9000 is loopback-only; use SSH forwarding.

## Application access

| Application | Shipped authentication |
| --- | --- |
| Authentik | Native accounts and MFA |
| Homepage | ForwardAuth; `homelab-users` or `homelab-admins` |
| Radarr, Sonarr, Prowlarr, qBittorrent, optional Bazarr | Admin-group ForwardAuth; retain native login/API keys |
| Plex | Plex native authentication |
| FileBrowser Quantum | Native accounts; optional native OIDC configured separately |
| n8n editor | Native owner plus `automation-users`/admin ForwardAuth |
| n8n production webhooks | Workflow authentication; no interactive SSO |
| Open WebUI | Native accounts plus `ai-users`/admin ForwardAuth |
| Kuma | `homelab-users`/admin ForwardAuth plus native owner |
| Dockge, Beszel, Traefik, AdGuard, terminal | Private source restriction and independent basic auth; native login where applicable |
| PostgreSQL, Ollama | Internal networking; no public route |

The `media-users` group is created but not automatically granted media-administration access. File-manager permissions are separate from ForwardAuth groups. Create native owners locally before remote publication.

ForwardAuth uses Authentik's embedded outpost at `server:9000`, with exact external hostnames and dedicated callback routes. APIs are not globally exempted. Internal clients use Docker service names and native API keys. Use private access or a separately reviewed authenticated route for external API clients.

## Optional native OIDC

ForwardAuth controls access to an application but does not automatically sign in to its native account. Prefer native OIDC where supported; applications with an explicit external-authentication mode can delegate browser authentication to the proxy after verifying every access path is protected. Never use a blanket LAN or shared-container-subnet authentication bypass. Internal API credentials remain separate, server-managed secrets. The free n8n Community edition retains its native account because SSO is not included; Plex retains its Plex account. Recovery credentials must remain available independently of Authentik.

Radarr 7 supports one native OIDC user. Create a separate confidential OAuth2/OpenID application with slug `radarr-oidc`, an allowed-user binding, an available signing key, and subject mode **Based on the User's Email**. Use strict authorization redirect `https://radarr.DOMAIN/signin-oidc` and strict post-logout redirect `https://radarr.DOMAIN/signout-callback-oidc`. Keep the existing HomeLab ForwardAuth application. Configure Radarr with authentication **OIDC**, authentication required **Enabled**, authority `https://auth.DOMAIN/application/o/radarr-oidc/`, the new provider's client ID and secret, the selected user's exact email, and scopes `openid profile email`. Keep the client secret outside Git and chat logs. Verify discovery returns HTTP 200 from inside Radarr before saving, then test fresh sign-in and rejection of another user. See the [Authentik Radarr integration](https://integrations.goauthentik.io/media/radarr/) and [container DNS path](networking.md).

For FileBrowser, create a confidential authorization-code provider with callback `https://files.DOMAIN/api/auth/oidc/callback`, slug `files`, scopes `openid/profile/email` and a groups claim. Store `clientId`/`clientSecret` in protected `SECRETS_ROOT/files-oidc.json`. Run `make render` with the selected host environment and redeploy storage. The renderer restricts allowed groups, retains password recovery and leaves personal-source grants explicit; shared access is granted to authenticated users. Verify actual per-user permissions.

Open WebUI OIDC is not preconfigured. `ENABLE_OAUTH_PERSISTENT_CONFIG=true` allows its administrator to configure OAuth under **Admin Settings > Authentication** and retain those settings in the application database across container recreation. The flag does not create an identity provider, enable OAuth signup or grant access to users. The shipped default remains ForwardAuth plus native login.

For native OIDC, create a separate confidential Authentik provider with a restricted user/group binding and strict callback `https://ai.DOMAIN/oauth/oidc/callback`. In Open WebUI, enter the provider's discovery URL, client ID, client secret, that callback and scopes `openid email profile`; restart WebUI after saving provider connection settings. Retain the native recovery login and keep new users pending until their access is deliberately approved. Linking an existing account by email requires a trusted identity provider and a verified matching email; do not enable unrestricted account merging. Keep both local and remote ForwardAuth protections during initial setup and test the complete login flow before reviewing any removal.

The client secret is stored in Open WebUI's external application database, so protect its appdata and backups as credentials; never put it in Git or diagnostic output. See the [upstream SSO documentation](https://docs.openwebui.com/features/authentication-access/auth/sso/) and [pinned configuration implementation](https://github.com/open-webui/open-webui/blob/v0.13.5/backend/open_webui/config.py).

## Bootstrap and recovery

The installer uses the pinned Authentik image's `hash_password` command with password input over stdin. A protected worker env file stores the resulting hash and bootstrap API token, not the raw password. Changing bootstrap variables later does not reset an initialized account. Revoke/rotate the privileged token deliberately after provisioning.

The MFA setup creates an explicit TOTP enrollment stage and refuses conflicting existing enrollment configuration. Do not replace custom identity policies merely to make provisioning pass.

If Authentik is unavailable, use SSH, private basic-authenticated dashboards and native credentials through local forwarding. Inspect the pinned server's recovery commands with `docker compose exec server ak --help` from the identity stack. Protect and invalidate recovery output. Restore the identity database and secret key together; never publish an emergency anonymous route.
