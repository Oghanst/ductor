"""Local TUI chat client for the ductor WebSocket API."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aiohttp import ClientSession, WSMsgType
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from ductor_bot.cli_commands.api_cmd import api_install_hint, nacl_available
from ductor_bot.infra.json_store import atomic_json_save
from ductor_bot.workspace.paths import DuctorPaths, resolve_paths

_console = Console()

_LOCAL_SUBCOMMANDS = frozenset({"chat"})
_STATE_FILENAME = "local_chat.json"
_MAX_MESSAGES = 50


@dataclass(slots=True)
class LocalChatState:
    """Persisted defaults for the local chat client."""

    host: str
    port: int
    chat_id: int | None
    channel_id: int | None


@dataclass(slots=True)
class LocalChatSettings:
    """Resolved settings for a local chat session."""

    host: str
    port: int
    token: str
    chat_id: int | None
    channel_id: int | None
    ductor_home: Path
    dry_run: bool
    probe: bool

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}/ws"


@dataclass(slots=True)
class ChatMessage:
    """Single chat line."""

    role: str
    text: str


@dataclass(slots=True)
class ChatRuntime:
    """Runtime info for rendering status."""

    ws_url: str
    chat_id: int | None = None
    channel_id: int | None = None
    active_provider: str | None = None
    active_model: str | None = None
    system_status: str | None = None
    tool_activity: str | None = None
    last_error: str | None = None


class _ArgError(Exception):
    pass


def _parse_local_subcommand(args: list[str]) -> tuple[str | None, list[str]]:
    """Extract the subcommand and remaining args after 'local'."""
    found = False
    sub: str | None = None
    rest: list[str] = []
    for a in args:
        if not found and a.startswith("-"):
            continue
        if not found and a == "local":
            found = True
            continue
        if found and sub is None:
            sub = a if a in _LOCAL_SUBCOMMANDS else None
            if sub is None:
                return None, []
            continue
        if found and sub is not None:
            rest.append(a)
    if found and sub is None:
        return None, []
    return sub, rest


def print_local_help() -> None:
    """Print local chat subcommand help."""
    _console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold green", min_width=30)
    table.add_column()
    table.add_row("ductor local chat", "Start local TUI chat client")
    table.add_row("--host 127.0.0.1", "API host override")
    table.add_row("--port 8841", "API port override")
    table.add_row("--token <token>", "API token override")
    table.add_row("--chat-id <id>", "Override chat_id")
    table.add_row("--channel-id <id>", "Override channel_id (topic)")
    table.add_row("--home <path>", "Use alternate DUCTOR_HOME")
    table.add_row("--dry-run", "Print resolved settings and exit")
    table.add_row("--probe", "Connect, authenticate, print status, then exit")
    _console.print(
        Panel(table, title="[bold]Local Chat Commands[/bold]", border_style="blue"),
    )
    _console.print()


def cmd_local(args: list[str]) -> None:
    """Handle 'ductor local ...' commands."""
    sub, rest = _parse_local_subcommand(args)
    if sub is None:
        print_local_help()
        return
    if sub == "chat":
        local_chat(rest)
        return
    print_local_help()


def _pop_arg_value(args: list[str], name: str) -> str | None:
    if name not in args:
        return None
    idx = args.index(name)
    if idx + 1 >= len(args):
        raise _ArgError(f"Missing value for {name}")
    value = args[idx + 1]
    del args[idx : idx + 2]
    return value


def _pop_flag(args: list[str], name: str) -> bool:
    if name not in args:
        return False
    args.remove(name)
    return True


def _parse_int(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise _ArgError(f"Invalid {name}: {value}") from exc


def _load_api_config(paths: DuctorPaths) -> dict[str, Any]:
    if not paths.config_path.exists():
        return {}
    try:
        data = json.loads(paths.config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    api_cfg = data.get("api", {})
    return api_cfg if isinstance(api_cfg, dict) else {}


def _state_path(paths: DuctorPaths) -> Path:
    return paths.workspace / _STATE_FILENAME


def _load_state(paths: DuctorPaths) -> LocalChatState | None:
    path = _state_path(paths)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    host = data.get("host")
    port = data.get("port")
    if not isinstance(host, str) or not isinstance(port, int):
        return None
    chat_id = data.get("chat_id")
    channel_id = data.get("channel_id")
    return LocalChatState(
        host=host,
        port=port,
        chat_id=chat_id if isinstance(chat_id, int) else None,
        channel_id=channel_id if isinstance(channel_id, int) else None,
    )


def _save_state(paths: DuctorPaths, *, host: str, port: int, chat_id: int | None, channel_id: int | None) -> None:
    path = _state_path(paths)
    payload = {
        "host": host,
        "port": port,
        "chat_id": chat_id,
        "channel_id": channel_id,
    }
    try:
        atomic_json_save(path, payload)
    except OSError:
        return


def _resolve_settings(rest: list[str]) -> LocalChatSettings | None:
    if "--help" in rest or "-h" in rest:
        print_local_help()
        return None

    rest = rest[:]
    try:
        host_raw = _pop_arg_value(rest, "--host")
        port_raw = _pop_arg_value(rest, "--port")
        token_raw = _pop_arg_value(rest, "--token")
        chat_id_raw = _pop_arg_value(rest, "--chat-id")
        channel_id_raw = _pop_arg_value(rest, "--channel-id")
        home_raw = _pop_arg_value(rest, "--home")
    except _ArgError as exc:
        _console.print(f"[bold red]{exc}[/bold red]")
        return None

    dry_run = _pop_flag(rest, "--dry-run")
    probe = _pop_flag(rest, "--probe")

    if rest:
        _console.print(f"[bold red]Unknown arguments:[/bold red] {' '.join(rest)}")
        return None

    try:
        port = _parse_int(port_raw, "port")
        chat_id = _parse_int(chat_id_raw, "chat-id")
        channel_id = _parse_int(channel_id_raw, "channel-id")
    except _ArgError as exc:
        _console.print(f"[bold red]{exc}[/bold red]")
        return None

    ductor_home = Path(home_raw).expanduser() if home_raw else None
    paths = resolve_paths(ductor_home=ductor_home)
    api_cfg = _load_api_config(paths)
    state = _load_state(paths)

    host = host_raw or (state.host if state else None) or str(api_cfg.get("host", "127.0.0.1"))
    resolved_port = port or (state.port if state else None) or int(api_cfg.get("port", 8741))
    token = token_raw or str(api_cfg.get("token", ""))

    resolved_chat_id = chat_id
    if resolved_chat_id is None and state and state.chat_id is not None:
        resolved_chat_id = state.chat_id
    if resolved_chat_id is None:
        cfg_chat = api_cfg.get("chat_id")
        resolved_chat_id = cfg_chat if isinstance(cfg_chat, int) else None

    resolved_channel_id = channel_id
    if resolved_channel_id is None and state and state.channel_id is not None:
        resolved_channel_id = state.channel_id

    if dry_run and probe:
        _console.print("[bold red]Choose either --dry-run or --probe, not both.[/bold red]")
        return None

    if not token:
        if not dry_run:
            _console.print("[bold red]API token not found.[/bold red]")
            _console.print("Run 'ductor api enable' and restart the bot, or pass --token.")
            return None
        token = ""

    if api_cfg and not api_cfg.get("enabled", False):
        _console.print("[yellow]Warning:[/yellow] API is disabled in config.")
        _console.print("Run 'ductor api enable' and restart the bot to start the server.")

    if resolved_chat_id is not None and resolved_chat_id <= 0:
        resolved_chat_id = None
    if resolved_channel_id is not None and resolved_channel_id <= 0:
        resolved_channel_id = None

    return LocalChatSettings(
        host=host,
        port=resolved_port,
        token=token,
        chat_id=resolved_chat_id,
        channel_id=resolved_channel_id,
        ductor_home=paths.ductor_home,
        dry_run=dry_run,
        probe=probe,
    )


def local_chat(rest: list[str]) -> None:
    """Start the local chat TUI client."""
    if not nacl_available():
        hint = api_install_hint()
        _console.print(
            Panel(
                "[bold yellow]PyNaCl is required for the API client (E2E encryption).[/bold yellow]"
                f"\n\nInstall it with:\n\n  [bold]{hint}[/bold]",
                title="[bold]Missing dependency[/bold]",
                border_style="yellow",
                padding=(1, 2),
            ),
        )
        return

    settings = _resolve_settings(rest)
    if settings is None:
        return

    if settings.dry_run:
        _print_dry_run(settings)
        return

    if settings.probe:
        asyncio.run(_probe(settings))
        return

    asyncio.run(_run_chat(settings))


def _render_screen(
    messages: list[ChatMessage],
    runtime: ChatRuntime,
) -> None:
    _console.clear()

    chat_body = Text()
    if not messages:
        chat_body.append("(no messages yet)")
    else:
        for msg in messages[-_MAX_MESSAGES:]:
            if msg.role == "user":
                label = "You"
                style = "bold green"
            elif msg.role == "assistant":
                label = "Ductor"
                style = "bold cyan"
            else:
                label = "System"
                style = "bold yellow"
            chat_body.append(f"{label}: ", style=style)
            chat_body.append(msg.text)
            chat_body.append("\n\n")

    status = Text()
    status.append(f"Endpoint: {runtime.ws_url}\n")
    status.append(f"Chat ID: {runtime.chat_id or 'default'}\n")
    status.append(f"Channel ID: {runtime.channel_id or 'none'}\n")
    if runtime.active_provider or runtime.active_model:
        status.append(
            f"Active: {runtime.active_provider or 'unknown'} / {runtime.active_model or 'unknown'}\n",
        )
    if runtime.system_status:
        status.append(f"System: {runtime.system_status}\n")
    if runtime.tool_activity:
        status.append(f"Tool: {runtime.tool_activity}\n")
    if runtime.last_error:
        status.append(f"Error: {runtime.last_error}\n")

    commands = Text("/help  /abort  /exit")

    _console.print(Panel(chat_body, title="Local Chat", border_style="blue"))
    _console.print(Panel(status, title="Status", border_style="green"))
    _console.print(Panel(commands, title="Commands", border_style="magenta"))


def _append_system(messages: list[ChatMessage], text: str) -> None:
    messages.append(ChatMessage(role="system", text=text))


def _build_auth_payload(settings: LocalChatSettings, e2e_pk: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": "auth", "token": settings.token, "e2e_pk": e2e_pk}
    if settings.chat_id:
        payload["chat_id"] = settings.chat_id
    if settings.channel_id:
        payload["channel_id"] = settings.channel_id
    return payload


def _note_result_files(message: ChatMessage, files: list[object] | None) -> None:
    if not files:
        return
    names: list[str] = []
    for entry in files:
        if isinstance(entry, dict):
            name = entry.get("name")
            if isinstance(name, str):
                names.append(name)
    if names:
        message.text = f"{message.text}\n\nFiles: {', '.join(names)}"


def _print_dry_run(settings: LocalChatSettings) -> None:
    paths = resolve_paths(ductor_home=settings.ductor_home)
    status = Table(show_header=False, box=None, padding=(0, 2))
    status.add_column(style="bold cyan", min_width=18)
    status.add_column()
    status.add_row("Endpoint", settings.ws_url)
    status.add_row("Chat ID", str(settings.chat_id or "default"))
    status.add_row("Channel ID", str(settings.channel_id or "none"))
    status.add_row("Token", "present" if settings.token else "missing")
    status.add_row("DUCTOR_HOME", str(settings.ductor_home))
    status.add_row("State File", str(_state_path(paths)))
    _console.print(Panel(status, title="[bold]Local Chat Dry Run[/bold]", border_style="green"))


def _print_probe(settings: LocalChatSettings, auth_resp: dict[str, Any]) -> None:
    status = Table(show_header=False, box=None, padding=(0, 2))
    status.add_column(style="bold cyan", min_width=18)
    status.add_column()
    status.add_row("Endpoint", settings.ws_url)
    status.add_row("Chat ID", str(auth_resp.get("chat_id") or "default"))
    status.add_row("Channel ID", str(auth_resp.get("channel_id") or "none"))
    status.add_row("Provider", str(auth_resp.get("active_provider") or "unknown"))
    status.add_row("Model", str(auth_resp.get("active_model") or "unknown"))
    status.add_row("Status", "auth_ok")
    _console.print(Panel(status, title="[bold]Local Chat Probe[/bold]", border_style="green"))


async def _probe(settings: LocalChatSettings) -> None:
    from ductor_bot.api.crypto import E2ESession

    async with ClientSession() as session:
        try:
            ws = await session.ws_connect(settings.ws_url)
        except Exception as exc:
            _console.print(f"[bold red]Failed to connect:[/bold red] {exc}")
            return

        async with ws:
            e2e = E2ESession()
            await ws.send_json(_build_auth_payload(settings, e2e.local_pk_b64))
            try:
                auth_resp = await ws.receive_json()
            except Exception as exc:
                _console.print(f"[bold red]Auth response error:[/bold red] {exc}")
                return
            if not isinstance(auth_resp, dict) or auth_resp.get("type") != "auth_ok":
                _console.print(f"[bold red]Auth failed:[/bold red] {auth_resp}")
                return
            e2e_pk = auth_resp.get("e2e_pk")
            if not isinstance(e2e_pk, str):
                _console.print("[bold red]Auth response missing e2e_pk.[/bold red]")
                return
            e2e.set_remote_key(e2e_pk)
            _print_probe(settings, auth_resp)

async def _run_chat(settings: LocalChatSettings) -> None:
    from ductor_bot.api.crypto import E2ESession

    messages: list[ChatMessage] = []
    runtime = ChatRuntime(ws_url=settings.ws_url)

    async with ClientSession() as session:
        try:
            ws = await session.ws_connect(settings.ws_url)
        except Exception as exc:
            _console.print(f"[bold red]Failed to connect:[/bold red] {exc}")
            return

        async with ws:
            e2e = E2ESession()
            await ws.send_json(_build_auth_payload(settings, e2e.local_pk_b64))
            auth_resp = await ws.receive_json()
            if auth_resp.get("type") != "auth_ok":
                _console.print(f"[bold red]Auth failed:[/bold red] {auth_resp}")
                return
            e2e.set_remote_key(str(auth_resp["e2e_pk"]))

            runtime.chat_id = auth_resp.get("chat_id")
            runtime.channel_id = auth_resp.get("channel_id")
            runtime.active_provider = auth_resp.get("active_provider")
            runtime.active_model = auth_resp.get("active_model")

            paths = resolve_paths(ductor_home=settings.ductor_home)
            _save_state(
                paths,
                host=settings.host,
                port=settings.port,
                chat_id=runtime.chat_id if isinstance(runtime.chat_id, int) else None,
                channel_id=runtime.channel_id if isinstance(runtime.channel_id, int) else None,
            )

            _append_system(messages, "Connected. Type /help for commands.")

            while True:
                _render_screen(messages, runtime)
                try:
                    user_input = await asyncio.to_thread(_console.input, "You> ")
                except (EOFError, KeyboardInterrupt):
                    _append_system(messages, "Session closed.")
                    break

                text = user_input.strip()
                if not text:
                    continue
                if text in {"/exit", "/quit"}:
                    _append_system(messages, "Session closed.")
                    break
                if text == "/help":
                    _append_system(messages, "Commands: /help, /abort, /exit")
                    continue

                if text == "/abort":
                    await ws.send_str(e2e.encrypt({"type": "abort"}))
                    await _handle_abort(ws, e2e, messages, runtime)
                    continue

                user_msg = ChatMessage(role="user", text=text)
                messages.append(user_msg)
                assistant_msg = ChatMessage(role="assistant", text="")
                messages.append(assistant_msg)

                await ws.send_str(e2e.encrypt({"type": "message", "text": text}))
                await _handle_stream(ws, e2e, messages, assistant_msg, runtime)


async def _handle_abort(
    ws: Any,
    e2e: Any,
    messages: list[ChatMessage],
    runtime: ChatRuntime,
) -> None:
    while True:
        msg = await ws.receive()
        if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
            runtime.last_error = "Connection closed"
            _append_system(messages, "Connection closed.")
            return
        if msg.type != WSMsgType.TEXT:
            continue
        data = e2e.decrypt(msg.data)
        msg_type = str(data.get("type", ""))
        if msg_type == "abort_ok":
            killed = data.get("killed", 0)
            _append_system(messages, f"Abort acknowledged (killed: {killed}).")
            return
        if msg_type == "error":
            runtime.last_error = str(data.get("message", "Unknown error"))
            _append_system(messages, f"Error: {runtime.last_error}")
            return


async def _handle_stream(
    ws: Any,
    e2e: Any,
    messages: list[ChatMessage],
    assistant_msg: ChatMessage,
    runtime: ChatRuntime,
) -> None:
    got_delta = False
    while True:
        msg = await ws.receive()
        if msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR):
            runtime.last_error = "Connection closed"
            _append_system(messages, "Connection closed.")
            return
        if msg.type != WSMsgType.TEXT:
            continue
        data = e2e.decrypt(msg.data)
        msg_type = str(data.get("type", ""))
        if msg_type == "text_delta":
            delta = str(data.get("data", ""))
            assistant_msg.text += delta
            got_delta = True
            _render_screen(messages, runtime)
            continue
        if msg_type == "tool_activity":
            runtime.tool_activity = str(data.get("data", ""))
            _render_screen(messages, runtime)
            continue
        if msg_type == "system_status":
            runtime.system_status = str(data.get("data", ""))
            _render_screen(messages, runtime)
            continue
        if msg_type == "result":
            if not got_delta:
                assistant_msg.text = str(data.get("text", ""))
            _note_result_files(assistant_msg, data.get("files"))
            return
        if msg_type == "error":
            runtime.last_error = str(data.get("message", "Unknown error"))
            _append_system(messages, f"Error: {runtime.last_error}")
            return
