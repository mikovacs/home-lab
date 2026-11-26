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

For FileBrowser, create a confidential authorization-code provider with callback `https://files.DOMAIN/api/auth/oidc/callback`, slug `files`, scopes `openid/profile/email` and a groups claim. Store `clientId`/`clientSecret` in protected `SECRETS_ROOT/files-oidc.json`. Run `make render` with the selected host environment and redeploy storage. The renderer restricts allowed groups, retains password recovery and leaves personal-source grants explicit; shared access is granted to authenticated users. Verify actual per-user permissions.

Open WebUI OIDC is not preconfigured. Follow its upstream OAuth documentation, protect client secrets, restrict signup/groups and review removal of ForwardAuth from both local and remote AI routers. The shipped default remains ForwardAuth plus native login.

## Bootstrap and recovery

The installer uses the pinned Authentik image's `hash_password` command with password input over stdin. A protected worker env file stores the resulting hash and bootstrap API token, not the raw password. Changing bootstrap variables later does not reset an initialized account. Revoke/rotate the privileged token deliberately after provisioning.

The MFA setup creates an explicit TOTP enrollment stage and refuses conflicting existing enrollment configuration. Do not replace custom identity policies merely to make provisioning pass.

If Authentik is unavailable, use SSH, private basic-authenticated dashboards and native credentials through local forwarding. Inspect the pinned server's recovery commands with `docker compose exec server ak --help` from the identity stack. Protect and invalidate recovery output. Restore the identity database and secret key together; never publish an emergency anonymous route.
