# ADR 0002: Preserve Beszel native API authentication behind a private SSO route

Status: Accepted

## Context

Beszel uses PocketBase tokens in the `Authorization` header. The previous route required HTTP Basic authentication in that same header and removed it before forwarding requests. Account bootstrap can succeed without a native token, while authenticated operations such as fetching the public key and saving a system fail. Changing only `removeHeader` cannot make two authentication schemes share one header.

## Decision

Keep Beszel unpublished and restricted to `ADMIN_CIDRS`. Replace its BasicAuth middleware with a dedicated Authentik ForwardAuth middleware backed by the admin-only `homelab-monitor` application. Send only `Cookie` to the auth service; Traefik supplies its forwarded request metadata. Do not copy any authorization response header over the original request. Retain native PocketBase authentication for the application and API.

Give the exact monitor hostname's outpost callback a higher-priority local route with the same source restriction. Do not add monitor to the remote allowlist or create an API exemption. Other administrative routes retain their independent BasicAuth protection.

## Consequences

Beszel now depends on Authentik for normal browser access. Keep native recovery credentials and SSH access for recovery through reviewed local forwarding. Existing installations must provision the admin-only provider before switching routes, then clear cached browser BasicAuth credentials. Native OIDC remains a separate configuration task. The agent uses the existing local socket transport and is not granted Docker access by this change.

## Evidence and validation

- [PocketBase authentication](https://pocketbase.io/docs/authentication/) specifies `Authorization: YOUR_AUTH_TOKEN`.
- [Traefik BasicAuth](https://doc.traefik.io/traefik/middlewares/http/basicauth/) documents removal of the authorization header.
- [Traefik ForwardAuth](https://doc.traefik.io/traefik/middlewares/http/forwardauth/) documents request-header selection.

Static and unit regressions cover the admin-only provider, private callback and denied tunnel export. The isolated routing test uses stub auth/origin servers to check that a proxy cookie and a native token are both necessary, the auth service never receives the native token, and disallowed sources cannot reach either Beszel or its callback. This is not a live identity or host-agent acceptance test.
