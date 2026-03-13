"""Entry point: python -m ductor_bot."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import shutil
import signal
import sys
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

# Re-exports from cli_commands — referenced by main() dispatch and by
# tests that patch ductor_bot.__main__.<name>.
from ductor_bot.cli_commands.agents import cmd_agents as _cmd_agents
from ductor_bot.cli_commands.api_cmd import cmd_api as _cmd_api
from ductor_bot.cli_commands.docker import cmd_docker as _cmd_docker
from ductor_bot.cli_commands.lifecycle import (  # noqa: F401
    _re_exec_bot,
)
from ductor_bot.cli_commands.lifecycle import (
    cmd_restart as _cmd_restart,
)
from ductor_bot.cli_commands.lifecycle import (
    start_bot as _start_bot,
)
from ductor_bot.cli_commands.lifecycle import (
    stop_bot as _stop_bot,
)
from ductor_bot.cli_commands.lifecycle import (
    uninstall as _uninstall,
)
from ductor_bot.cli_commands.lifecycle import (
    upgrade as _upgrade,
)
from ductor_bot.cli_commands.local_chat import cmd_local as _cmd_local
from ductor_bot.cli_commands.service import cmd_service as _cmd_service
from ductor_bot.cli_commands.status import (
    print_status as _print_status,
)
from ductor_bot.cli_commands.status import (
    print_usage as _print_usage,
)
from ductor_bot.config import (
    DEFAULT_EMPTY_GEMINI_API_KEY,
    AgentConfig,
    deep_merge_config,
    update_config_file,
)
from ductor_bot.infra.json_store import atomic_json_save
from ductor_bot.workspace.init import init_workspace
from ductor_bot.workspace.paths import resolve_paths

logger = logging.getLogger(__name__)

_console = Console()


def _apply_global_home_override(args: list[str]) -> list[str]:
    """Apply global ``--home`` override via ``DUCTOR_HOME`` and return cleaned args."""
    cleaned: list[str] = []
    idx = 0
    while idx < len(args):
        current = args[idx]
        if current == "--home":
            if idx + 1 >= len(args):
                _console.print("[bold red]Missing value for --home[/bold red]")
                sys.exit(2)
            os.environ["DUCTOR_HOME"] = str(Path(args[idx + 1]).expanduser())
            idx += 2
            continue
        if current.startswith("--home="):
            _, value = current.split("=", 1)
            if not value:
                _console.print("[bold red]Missing value for --home[/bold red]")
                sys.exit(2)
            os.environ["DUCTOR_HOME"] = str(Path(value).expanduser())
            idx += 1
            continue
        cleaned.append(current)
        idx += 1
    return cleaned


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _is_configured() -> bool:
    """Check if bot has a valid configuration."""
    paths = resolve_paths()
    if not paths.config_path.exists():
        return False
    try:
        data = json.loads(paths.config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if _has_valid_telegram_config_data(data):
        return True
    return _has_api_enabled_data(data)


def _has_valid_telegram_config_data(data: dict[str, object]) -> bool:
    token = data.get("telegram_token", "")
    users = data.get("allowed_user_ids", [])
    return bool(token) and not str(token).startswith("YOUR_") and bool(users)


def _read_config_data(config_path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _load_telegram_ready_config(config_path: Path) -> AgentConfig | None:
    data = _read_config_data(config_path)
    if data is None or not _has_valid_telegram_config_data(data):
        return None
    try:
        return AgentConfig.model_validate(data)
    except Exception:
        logger.exception("Failed to validate config while enabling Telegram channel")
        return None


def _has_api_enabled_data(data: dict[str, object]) -> bool:
    api = data.get("api")
    return isinstance(api, dict) and bool(api.get("enabled", False))


def _has_valid_telegram_config(config: AgentConfig) -> bool:
    return bool(config.telegram_token) and not config.telegram_token.startswith("YOUR_") and bool(
        config.allowed_user_ids
    )


def _fallback_model_for_provider(provider: str) -> str:
    if provider == "codex":
        return "gpt-5.2-codex"
    if provider == "cfuse":
        return "antchat/Qwen3-Coder-480B-A35B-Instruct"
    if provider == "gemini":
        return "auto"
    return "sonnet"


def _auto_select_authenticated_provider(config: AgentConfig, *, config_path: Path) -> None:
    """When current provider is unavailable, switch to an authenticated one."""
    from ductor_bot.cli.auth import check_all_auth

    auth = check_all_auth()
    authenticated = {name for name, result in auth.items() if result.is_authenticated}
    if not authenticated:
        return
    if config.provider in authenticated:
        return

    for candidate in ("codex", "claude", "cfuse", "gemini"):
        if candidate in authenticated:
            selected = candidate
            break
    else:
        return

    selected_model = _fallback_model_for_provider(selected)
    config.provider = selected
    config.model = selected_model
    update_config_file(config_path, provider=selected, model=selected_model)
    _console.print(
        "[yellow]Provider auto-selected for API-only mode:[/yellow] "
        f"{selected} ({selected_model})"
    )


def _ensure_api_enabled_for_local_runtime(config: AgentConfig, *, config_path: Path) -> None:
    """Ensure API is enabled so local chat can bootstrap without Telegram onboarding."""
    if config.api.enabled:
        return
    config.api.enabled = True
    if config_path.exists():
        update_config_file(config_path, api=config.api.model_dump(mode="json"))
    _console.print(
        "[yellow]API was disabled; enabled automatically for local runtime bootstrap.[/yellow]"
    )


def load_config() -> AgentConfig:
    """Load, auto-create, and smart-merge the bot config.

    Resolution order:
    1. ``~/.ductor/config/config.json`` (canonical location)
    2. Copy from ``config.example.json`` in the framework root on first start
    3. Fall back to Pydantic defaults if example file is missing

    On every load the config is deep-merged with current Pydantic defaults
    so that new fields from framework updates are added without destroying
    user settings.
    """
    paths = resolve_paths()
    config_path = paths.config_path

    first_start = not config_path.exists()

    if first_start:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        example = paths.config_example_path
        if example.is_file():
            shutil.copy2(example, config_path)
            logger.info("Created config from config.example.json at %s", config_path)
        else:
            defaults = AgentConfig().model_dump(mode="json")
            defaults["gemini_api_key"] = DEFAULT_EMPTY_GEMINI_API_KEY
            defaults.pop("api", None)  # Beta: only written by `ductor api enable`
            atomic_json_save(config_path, defaults)
            logger.info("Created default config at %s", config_path)

    try:
        user_data: dict[str, object] = json.loads(config_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        logger.exception("Failed to parse config at %s", config_path)
        sys.exit(1)

    normalized_existing = False
    if user_data.get("gemini_api_key") is None:
        user_data["gemini_api_key"] = DEFAULT_EMPTY_GEMINI_API_KEY
        normalized_existing = True

    defaults = AgentConfig().model_dump(mode="json")
    defaults["gemini_api_key"] = DEFAULT_EMPTY_GEMINI_API_KEY
    defaults.pop("api", None)  # Beta: only written by `ductor api enable`
    merged, changed = deep_merge_config(user_data, defaults)
    changed = changed or normalized_existing
    resolved_home = str(paths.ductor_home)
    if merged.get("ductor_home") != resolved_home:
        merged["ductor_home"] = resolved_home
        changed = True

    if changed:
        atomic_json_save(config_path, merged)
        logger.info("Extended config with new default fields")

    init_workspace(paths)
    return AgentConfig.model_validate(merged)


# ---------------------------------------------------------------------------
# Bot lifecycle
# ---------------------------------------------------------------------------


async def run_telegram(config: AgentConfig) -> int:
    """Validate config and run the bot via AgentSupervisor.

    The supervisor manages the main agent and dynamically created sub-agents
    from ``agents.json``.  If no sub-agents are defined, the supervisor runs
    only the main agent — behaviour is identical to the old single-bot path.

    Returns the exit code from the bot (``0`` = clean, ``42`` = restart requested).
    """
    paths = resolve_paths(ductor_home=config.ductor_home)

    if not _has_valid_telegram_config(config):
        _ensure_api_enabled_for_local_runtime(config, config_path=paths.config_path)
        _console.print(
            "[bold yellow]Telegram config is incomplete. "
            "Starting API-only runtime; configure Telegram later from local chat.[/bold yellow]"
        )
        return await run_api_only(config)

    from ductor_bot.bot.sender import send_rich
    from ductor_bot.infra.pidlock import acquire_lock, release_lock
    from ductor_bot.multiagent.supervisor import AgentSupervisor

    acquire_lock(pid_file=paths.ductor_home / "bot.pid", kill_existing=True)

    supervisor = AgentSupervisor(config)
    supervisor.set_notification_sender(send_rich)
    exit_code = 0
    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()
    installed_signals: list[signal.Signals] = []

    def _request_shutdown() -> None:
        if current_task is not None and not current_task.done():
            current_task.cancel()

    if current_task is not None and sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_shutdown)
            except (NotImplementedError, RuntimeError, ValueError):
                continue
            installed_signals.append(sig)

    try:
        exit_code = await supervisor.start()
    except asyncio.CancelledError:
        logger.info("Termination signal received, shutting down gracefully...")
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    finally:
        for sig in installed_signals:
            loop.remove_signal_handler(sig)
        await supervisor.stop_all()
        release_lock(pid_file=paths.ductor_home / "bot.pid")
    return exit_code


async def run_api_only(config: AgentConfig) -> int:
    """Run orchestrator + WebSocket API without Telegram initialization."""
    paths = resolve_paths(ductor_home=config.ductor_home)

    from ductor_bot.infra.pidlock import acquire_lock, release_lock
    from ductor_bot.bot.app import TelegramBot
    from ductor_bot.orchestrator.core import Orchestrator

    acquire_lock(pid_file=paths.ductor_home / "bot.pid", kill_existing=True)
    orch: Orchestrator | None = None
    telegram_bot: TelegramBot | None = None
    telegram_task: asyncio.Task[int] | None = None
    next_telegram_retry_at = 0.0
    loop = asyncio.get_running_loop()
    current_task = asyncio.current_task()
    installed_signals: list[signal.Signals] = []

    def _request_shutdown() -> None:
        if current_task is not None and not current_task.done():
            current_task.cancel()

    if current_task is not None and sys.platform != "win32":
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, _request_shutdown)
            except (NotImplementedError, RuntimeError, ValueError):
                continue
            installed_signals.append(sig)

    try:
        _auto_select_authenticated_provider(config, config_path=paths.config_path)
        logger.info("Starting API-only runtime (Telegram disabled)")
        orch = await Orchestrator.create(config, agent_name="main")
        while True:
            if telegram_task is None and loop.time() >= next_telegram_retry_at:
                tg_config = _load_telegram_ready_config(paths.config_path)
                if tg_config is not None:
                    telegram_bot = TelegramBot(tg_config, agent_name="main")
                    telegram_bot.attach_orchestrator(orch)
                    telegram_task = asyncio.create_task(telegram_bot.run(), name="telegram-frontend")
                    _console.print(
                        "[green]Telegram config detected. Telegram channel enabled "
                        "(TUI remains active).[/green]"
                    )

            if telegram_task is not None and telegram_task.done():
                try:
                    exit_code = telegram_task.result()
                except Exception:
                    logger.exception("Telegram frontend crashed; will retry")
                else:
                    logger.warning("Telegram frontend exited (code=%s); will retry", exit_code)
                with contextlib.suppress(Exception):
                    if telegram_bot is not None:
                        await telegram_bot.shutdown()
                telegram_bot = None
                telegram_task = None
                next_telegram_retry_at = loop.time() + 10.0

            await asyncio.sleep(1.0)
    except asyncio.CancelledError:
        logger.info("Termination signal received, shutting down API-only runtime...")
    except KeyboardInterrupt:
        logger.info("Shutting down API-only runtime...")
    finally:
        for sig in installed_signals:
            loop.remove_signal_handler(sig)
        if telegram_task is not None and not telegram_task.done():
            telegram_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
                await asyncio.wait_for(telegram_task, timeout=3.0)
        if telegram_bot is not None:
            with contextlib.suppress(Exception):
                await telegram_bot.shutdown()
        if orch is not None:
            await orch.shutdown()
        release_lock(pid_file=paths.ductor_home / "bot.pid")
    return 0


# ---------------------------------------------------------------------------
# CLI command handlers
# ---------------------------------------------------------------------------


def _cmd_status() -> None:
    """Show bot status or hint to configure."""
    from rich.panel import Panel

    _console.print()
    if _is_configured():
        _print_status()
    else:
        _console.print(
            Panel(
                "[bold yellow]Not configured.[/bold yellow]\n\n"
                "Run [bold]ductor[/bold] to start local runtime and configure channels dynamically.",
                title="[bold]Status[/bold]",
                border_style="yellow",
                padding=(1, 2),
            ),
        )
    _console.print()


def _cmd_setup(verbose: bool) -> None:
    """Legacy setup command: onboarding removed, start runtime directly."""
    _console.print(
        "[yellow]Interactive onboarding is deprecated. "
        "Use local chat (/telegram ...) for dynamic Telegram setup.[/yellow]"
    )
    _start_bot(verbose)


def _default_action(verbose: bool) -> None:
    """Start runtime directly; Telegram onboarding is now dynamic and optional."""
    _start_bot(verbose)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

_COMMANDS: dict[str, str] = {
    "help": "help",
    "status": "status",
    "stop": "stop",
    "restart": "restart",
    "upgrade": "upgrade",
    "uninstall": "uninstall",
    "onboarding": "setup",
    "reset": "setup",
    "service": "service",
    "docker": "docker",
    "api": "api",
    "agents": "agents",
    "local": "local",
}

_Action = Callable[[], None]


def main() -> None:
    """CLI entry point."""
    args = _apply_global_home_override(sys.argv[1:])
    commands = [a for a in args if not a.startswith("-")]
    verbose = "--verbose" in args or "-v" in args

    if "--help" in args or "-h" in args:
        commands.append("help")

    # Resolve first matching command
    action = next((_COMMANDS[c] for c in commands if c in _COMMANDS), None)

    dispatch: dict[str, _Action] = {
        "help": _print_usage,
        "status": _cmd_status,
        "stop": _stop_bot,
        "restart": _cmd_restart,
        "upgrade": _upgrade,
        "uninstall": _uninstall,
        "setup": lambda: _cmd_setup(verbose),
        "service": lambda: _cmd_service(args),
        "docker": lambda: _cmd_docker(args),
        "api": lambda: _cmd_api(args),
        "agents": lambda: _cmd_agents(args),
        "local": lambda: _cmd_local(args),
    }

    handler = dispatch.get(action) if action else None
    if handler is not None:
        handler()
    else:
        _default_action(verbose)


if __name__ == "__main__":
    main()
