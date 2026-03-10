# Local Chat TUI (draft)

## Goal
Provide a local terminal UI client that talks to the ductor WebSocket API and
supports sending messages, streaming responses, basic status display, and
session persistence.

## WebSocket API Summary
Based on the server contract in `ductor_bot/api/server.py`:

- Connect to `ws://<host>:<port>/ws`
- Send plaintext auth: `{"type": "auth", "token": "...", "e2e_pk": "<b64>"}`
  - Optional: `chat_id` and `channel_id`
- Receive plaintext `auth_ok` with `chat_id`, `channel_id`, `e2e_pk`,
  `active_provider`, `active_model`
- Post-auth frames are encrypted and use:
  - `{"type": "message", "text": "..."}` for user input
  - Stream events: `text_delta`, `tool_activity`, `system_status`
  - Final `result` with optional files
  - `{"type": "abort"}` to cancel (expect `abort_ok`)

## TUI Layout
Minimal three-panel layout:

- Chat panel: recent messages (user/assistant/system)
- Status panel: endpoint, chat/channel IDs, provider/model, tool activity
- Command panel: `/help`, `/abort`, `/exit`

Input remains a simple blocking prompt to keep the first version stable.

## Session/State
Persist last-used host/port/chat_id/channel_id under the active `DUCTOR_HOME`
workspace as `local_chat.json`. Defaults fall back to the API config when
present.

## Isolation Guidance
To avoid impacting a running service, use an isolated home and the recommended
backup port:

- `--home ~/.ductor-local-chat-test`
- `--port 8841`

## Usage (draft)
Typical usage with an isolated home and backup port:

`ductor local chat --port 8841 --home ~/.ductor-local-chat-test`

Ensure the API token is available in the API config (or pass `--token`).

Preflight settings without connecting:

`ductor local chat --dry-run --port 8841 --home ~/.ductor-local-chat-test`

Probe the API (connect + auth, then exit):

`ductor local chat --probe --port 8841 --home ~/.ductor-local-chat-test`

On success, the probe also persists the resolved host/port/chat_id/channel_id
to `local_chat.json` under the selected `DUCTOR_HOME`.
