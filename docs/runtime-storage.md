# Runtime Storage and Persistence

Use this page before installing runtime dependencies or choosing where generated state should live.

## Container filesystem vs persistent data

The gateway container filesystem is replaceable. A normal restart keeps it, but container recreation or image rebuild can remove interactive changes.

The base Compose deployment persists `~/.nanobot` at `/home/nanobot/.nanobot`. Project workspaces may also be mounted separately depending on deployment.

## What should persist

Prefer persistent storage for runtime-installed assets that are intentionally reusable across container recreation, such as large download caches or tool-managed assets.

Good locations are explicit persistent paths, for example:

```text
/home/nanobot/.nanobot/cache/<tool>
<mounted-project-workspace>/.cache/<tool>
```

Choose the agent data volume for agent-owned runtime cache. Choose a project workspace only when the dependency is genuinely project-scoped.

Dream-managed specialist state is agent-owned durable state and lives under the agent workspace:

```text
agents/roles.json             # Dream-managed specialist definitions
agents/role_candidates.json   # recurring-responsibility evidence
agents/role_usage.json        # runtime launch/recency telemetry
```

The role and candidate files participate in Dream's durable audit/versioning path. Usage telemetry is deliberately runtime-generated rather than Dream-authored, so it is advisory state rather than a source of role instructions.

## What belongs in the image

Stable system libraries and dependencies required for normal operation belong in the Dockerfile or another build layer after they have been validated interactively.

Interactive `apt` installs are useful for diagnosis or experimentation, but they are not durable architecture by themselves.

## Browser-specific rule

The standard browser deployment is intentionally split:

```text
gateway: Playwright client
browser sidecar: Chromium + persistent browser profile
```

Do not download a second Chromium into the gateway merely to use the standard browser capability. The sidecar already owns the browser binary and profile.

A separate browser install may still be appropriate for a different, explicitly chosen workflow; document that decision instead of silently replacing the sidecar design.
