# ADR 0001: Match Dockge discovery to existing Compose projects

Status: Accepted

## Context

Compose projects have explicit names such as `homelab-media`, while their source directories are named `stacks/media`. Dockge 1.5.0 keys its discovered stacks by directory name and joins them to Docker projects by name. Scanning `REPO_ROOT/stacks` therefore creates an inactive `media` entry and a separate unmanaged `homelab-media` entry.

## Decision

Render a dedicated `RUNTIME_ROOT/dockge-stacks` directory containing absolute directory symlinks named `homelab-<stack>` that point to the canonical source directories. Mount the view read-only in Dockge at the same absolute path as on the host. Keep the source mount read-only and preserve the existing `.env` symlinks.

The renderer only creates missing expected links. Existing files, unexpected entries and links with different targets cause a failure rather than automatic replacement or deletion. This view does not enable optional profiles or bypass mount guards.

## Consequences

Existing projects, volumes and networks keep their names. No source copies drift from the installed release, and host settings remain outside Git. A source update still requires explicit Dockge recreation to refresh its source bind. Dockge's full socket authority remains root-equivalent despite the read-only filesystem mounts.

The independent Dockge status behavior for successful one-shot containers remains: an `exited(0)` guard can cause an aggregate `exited` label. Operators must inspect container states rather than deleting guards or restarting working stacks to clear that label.

## Evidence

The [Dockge 1.5.0 stack implementation](https://github.com/louislam/dockge/blob/1.5.0/backend/stack.ts) uses directory names in `getStackList`, matches `composeStack.Name`, follows symlinks with `stat`, and treats any exited container as an exited stack in `statusConvert`.
