# Optional local AI

AI is absent from deploy-core and deploy-all. Ollama and Open WebUI have no automatic restart and no public API port. Ollama storage is on the UUID-verified secondary drive; WebUI database/config is on the primary drive. n8n and Ollama share a dedicated AI bridge; additional trusted apps can join it explicitly. CPU-only inference is the shipped mode. The installer never runs `ollama pull`. Image layers may include model assets. Check the embedding and auxiliary-model download settings before enabling document features.

## Resources and models

Before `make deploy-ai`, run preflight and assess idle free RAM after core/media load. The controller requires at least 8 GiB total RAM, but this is only a minimum gate; free memory and model working set must also fit. AI_MEMORY defaults to 4g and AI_CPUS to 2; WebUI adds up to 1g by default. One loaded model and one inference at a time limit contention. A memory limit can still cause model OOM within the container; it does not make an oversized model fit. Stop AI if host swapping or Plex latency increases.

Suggested **classes** after inventory: 0.5–1.5B instruct Q4 for constrained systems with several free GiB; 3B Q4 only when more headroom remains; 7–8B models generally need substantially more RAM and patience on an older mini PC. Choose a currently maintained model and review its individual license before a manual `docker compose exec ollama ollama pull <reviewed-model:tag>` from `stacks/ai`. Model tags and performance change; no particular download or token-per-second claim is implied. HDD model loading will be slower; CPU decode speed depends strongly on unknown CPU instruction support.

## First administrator login

Open WebUI has signup disabled. Keep the tunnel disabled during first-owner setup. Changing `ENABLE_SIGNUP` alone can be ignored by stored application settings; do not edit the recorded release to bootstrap an owner.

Use the external bootstrap override instead (adapt paths to your host):

1. Copy `stacks/ai/bootstrap.example.yaml` to `/etc/homelab/webui-bootstrap.yaml`, mode 0600.
2. Create `SECRETS_ROOT/webui-bootstrap.env`, mode 0600, containing `WEBUI_ADMIN_EMAIL` and `WEBUI_ADMIN_PASSWORD` as one unquoted `KEY=value` per line. Choose a unique password of at least 14 characters, without line breaks. The override uses raw env-file parsing to preserve literal `$` and `#` characters.
3. From the source directory, run:

   ```sh
   sudo docker compose --env-file /etc/homelab/host.env -f stacks/ai/compose.yaml -f /etc/homelab/webui-bootstrap.yaml up -d --force-recreate --wait webui
   ```

4. Verify the native administrator login locally. Remove only those two temporary bootstrap files, then run `sudo env HOMELAB_ENV_FILE=/etc/homelab/host.env make deploy-ai` to recreate the ordinary service without bootstrap credentials. Keep signup disabled and confirm the stored UI setting before remote publication.

The [pinned startup code](https://github.com/open-webui/open-webui/blob/v0.13.5/backend/open_webui/main.py) creates this administrator only when no user exists; it does not reset an existing account. Confirm the first login before enabling access for other users. A persistent WEBUI_SECRET_KEY is exported from its protected file by the entrypoint. ForwardAuth gates the UI; native session/user roles still apply. Native OIDC requires a separately reviewed configuration; see [authentication](authentication.md).

## Internal API and documents

Ollama native endpoint: http://ollama:11434/api; OpenAI-compatible endpoint: http://ollama:11434/v1. No API authentication is supplied by Ollama on this bridge, so attach only trusted workloads. n8n may use HTTP Request with a manually selected installed model. Do not route the model API through Cloudflare or publish it for unauthenticated access.

RAG: select a small licensed embedding model after measuring RAM/disk, keep document sources explicitly scoped, and use Open WebUI's supported knowledge features. Index only expendable test documents initially. Uploaded documents and WebUI state need backup; recreate embedding/model weights when appropriate. Agent/MCP experiments belong in isolated optional applications with scoped credentials, no Docker socket, no automatic host shell execution and read-only media. Do not grant an LLM write access to the existing library.

## Optional acceleration

For hardware acceleration, detect the actual Intel/NVIDIA/AMD device and supported backend first. Do not assume Intel /dev/dri means Ollama support. Follow current upstream requirements for the chosen backend, preserve memory caps and document any device/runtime override with the host configuration. Plex GPU support is independent from Ollama backend support.
