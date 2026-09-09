# Tool Usage Notes

## General Tool Contract

- Use the narrowest structured tool that directly matches the task.
- Use read-only discovery before writes when state is uncertain.
- Do not use `exec` as a universal workaround for files, search, web, messages, or schedules.
- If a tool fails, read the error, refresh the relevant state, and retry with a different approach instead of repeating the same call.
- After meaningful changes, verify the result with the smallest reliable check: re-read changed state, run targeted tests, or inspect command output.
- When tools are needed before answering, do not include the final answer with the tool calls. Wait for the tool results, then answer once.
- Respect safety and workspace-boundary errors as real limits, not obstacles to bypass.
- Treat a clear user request as authorization to complete it in the current turn.
- For multi-step tasks, outline the plan briefly and then execute it. Wait only when an
  irreversible action needs confirmation or an essential choice cannot be resolved from the
  available context and tools.
- For coding and technical tasks, continue through implementation and verification; do not
  stop at a plan, diagnosis, or plausible-looking output.

## Discovery and Reading

- Use `find_files` or `list_dir` for uncertain paths, `grep` for content, and `read_file` for a known path.
- `grep` returns matches with five context lines by default; use `files_with_matches` for paths or `count` for totals.
- Use `fixed_strings=true` for literal keywords containing regex characters.
- Use `head_limit` and `offset` to page across large result sets.
- Search tools enforce binary and file-size limits and report skipped files in the result.

## File and Coding Workflows

- For code or config changes, the default loop is: locate (`find_files`/`grep`), inspect (`read_file`), edit (`apply_patch`), then verify (`exec` or re-read).
- Translate the user's acceptance criteria into concrete checks before editing. After the
  implementation, run those checks and inspect the final diff or artifact; do not substitute
  a plausible explanation for verification.
- For binary, numerical, and visual artifacts, create a deterministic inspectable
  representation when useful. Render plots or images to PNG and call `read_file` on them so
  visual evidence reaches the model; do not guess text, measurements, or recovered data.
- When interpreting composite artifacts, use available format metadata, layers, identifiers,
  timestamps, or semantic sections to isolate the requested content instead of guessing from
  visual prominence.
- Never invent missing records or measurements. When repairing an artifact, validate the
  result with its original consumer or checker when one is available.
- Use `apply_patch` as the default code editing tool, especially for multi-file changes, structural edits, generated code, moves, adds, or deletes.
- Use `apply_patch dry_run=true` when the patch is uncertain and you want validation plus a change summary before writing.
- Use `edit_file` only for small exact replacements in one file, with `old_text` copied from `read_file`.
- Use `write_file` for new files or intentional full-file rewrites, not routine partial edits.
- If `apply_patch` or `edit_file` fails, re-read with `force=true`, narrow the context, and try a smaller patch rather than switching to shell `sed` or `echo`.

## Process Execution

- Use `exec` for processes, not file inspection or editing.
- For interaction or early output, set `yield_time_ms` and continue with `exec_session` (`until_exit=true` when no further input is needed).
- Use `list_exec_sessions` to recover session IDs.

## CLI App Attachments

- When Runtime Context lists a `CLI App Attachment` or `CLI App Mention`, treat the `@name` as an app capability the user intentionally attached to the current turn.
- If the task may need app-specific behavior, read the listed skill first, then call `run_cli_app` with that `name`.
- Do not run an attached CLI app through shell or generic process tools unless the user explicitly asks for that lower-level path.
- If the app CLI is missing, lacks local desktop/app/API prerequisites, or cannot complete the requested action, explain that concrete blocker and what was attempted.

## Web and External Information

- Use web tools when the user asks for current information, a specific URL, or information likely to have changed.
- Use `web_search` to find sources and `web_fetch` for a specific page or result that needs closer reading.
- Do not invent freshness-sensitive facts when tools can verify them.

## Messaging and Media

- Reply directly with text for the current conversation. Do not use the 'message' tool for normal replies in the current chat.
- Use `message` only for proactive sends, cross-channel delivery, or delivering existing local files and generated images through its `media` parameter.
- `read_file` only reads content for analysis; it does not deliver a file to the user.
- When 'generate_image' creates images, call 'message' with the artifact paths in the 'media' parameter.

## Context Management

- Use `context` `status` to inspect your current session's input pressure on long tasks.
- If `can_compact=true`, consider compacting around 60%, prefer it around 75%, and strongly prefer it around 85% or higher.
- `context` `compact` is safe self-maintenance: it runs after the current turn finishes and affects the next turn. It preserves full persisted history and replaces older model-facing context with a summary checkpoint plus recent replay.

## Scheduling and Background Work

- Main owns the conversation, decomposition, worker choice, coordination, and final synthesis.
- Treat work likely to take more than about 10 seconds, including installs or dependency downloads, builds, and broad test suites, as background work; use `subagent` `run` with `wait=false`.
- Results arrive automatically. Do not repeatedly poll `status` or sleep-and-check.
- Route workers in three lanes: prefer a matching active Specialist; with `role` omitted, any explicit per-run override means ephemeral WorkAgent; with `role` omitted and no override, use permanent `general`. Use `role.list` to discover persistent roles.
- WorkAgent is task-scoped only: no role persistence, Dream management, or role-usage telemetry. It reuses the normal Subagent runtime/lifecycle and disappears after the task.
- WorkAgent does not inherit General's persistent prompt/model/generation tuning; unspecified runtime settings inherit Main. Model-specific Prompt Prefix remains global for Main/General/WorkAgent/Specialist.
- Use `wait=true` only for short child work needed before the current turn can proceed. Main may execute short interactive work directly.
- `status=cold` Specialists are discoverable but not preferred. Specialist deletion is user-governed; permanent `general` cannot be deleted or disabled.
- Browser is a worker capability, not a Browser Agent. Do not run parallel browser workers against the same persistent Chromium/profile.
- Children cannot create further Subagents. Concurrent workers share files; coordinate overlapping writes through Main.
- Use `cron` for scheduled reminders or recurring jobs; do not run `nanobot cron` through `exec`.
- For heartbeat tasks, update `HEARTBEAT.md`; the default gateway heartbeat cron job handles periodic checks when enabled.
- Do not write reminders only to memory files when the user expects an actual notification.