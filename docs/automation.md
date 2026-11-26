# n8n automation

Deploy the automation stack only after identity setup. PostgreSQL and a persistent file-loaded N8N_ENCRYPTION_KEY preserve credentials. The editor is https://n8n.DOMAIN behind ForwardAuth plus n8n's native owner login. Set owner credentials during local first run; community native OIDC is not assumed. The editor and public webhook address are separate. Review N8N_PROXY_HOPS against the actual proxy chain; current defaults treat Traefik as the one trusted proxy and tunnel entrypoint does not trust supplied client forwarding headers.

## Webhooks

The generated remote hooks router permits only `/webhook/`, not /webhook-test, UI or arbitrary API endpoints. Set N8N_WEBHOOK_URL through the reviewed Compose configuration if changing the hostname. Every published workflow must implement a secret header, signature and/or another appropriate authentication method; a random-looking webhook path is not sufficient. Configure Cloudflare rules to avoid interactive challenges on signed machine requests. Execution history defaults to seven days. Sensitive data in execution logs must be considered part of the encrypted backup.

## Example workflows

Import JSON files from `config/n8n/workflows`. They are inactive, use manual triggers or an authenticated webhook, and contain no credentials or destructive actions. Set the URLs/credential selectors in the editor before execution. Service availability and media health use HTTP checks; low disk/backup notifications consume the host monitor payload, with authentication selected explicitly; Ollama uses its internal API and requires a manually pulled model. Add your notification node (email, Matrix, Discord, etc.) only after configuring its credentials. No workflow is activated automatically.

Host monitor-webhook.json contains an HTTPS production webhook URL and optional headers as a protected JSON object. Make the n8n Webhook node use matching Header Auth credentials before activating. Alerts include mount availability, capacity thresholds, missing/stale backups and optional SMART. Add rate limiting/deduplication in n8n to avoid repeated alerts. Uptime Kuma can send service notifications directly without n8n, so failures of the automation stack need not silence every notification.

## State and task runners

Backups stop n8n writers, dump PostgreSQL and include .n8n state plus the encryption key in Restic. Restore database and key together; see [recovery](disaster-recovery.md). Execution payloads and workflow credentials may be sensitive; do not export live workflow credential data into Git.

Startup waits for n8n's `/healthz/readiness` endpoint. The shipped image provides the JavaScript task runner; Python Code-node execution is not configured. Add a separately reviewed external runner if needed.
