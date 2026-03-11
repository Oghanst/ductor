"""API server management CLI subcommands (``ductor api ...``)."""

from __future__ import annotations

import json
from collections.abc import Callable

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from ductor_bot.config import _BIND_ALL_INTERFACES
from ductor_bot.workspace.paths import resolve_paths

_console = Console()

_API_SUBCOMMANDS = frozenset({"enable", "disable"})


def _parse_api_subcommand(args: list[str]) -> str | None:
    """Extract the subcommand after 'api' from CLI args."""
    found = False
    for a in args:
        if a.startswith("-"):
            continue
        if not found and a == "api":
            found = True
            continue
        if found:
            return a if a in _API_SUBCOMMANDS else None
    return None


def print_api_help() -> None:
    """Print the API subcommand help table with current status."""
    _console.print()
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column(style="bold green", min_width=30)
    table.add_column()
    table.add_row("ductor api enable", "Enable the WebSocket API server")
    table.add_row("ductor api disable", "Disable the WebSocket API server")

    # Show current status
    paths = resolve_paths()
    status = "[dim]not configured[/dim]"
    if paths.config_path.exists():
        try:
            data = json.loads(paths.config_path.read_text(encoding="utf-8"))
            api_cfg = data.get("api", {})
            if isinstance(api_cfg, dict) and api_cfg.get("enabled"):
                port = api_cfg.get("port", 8741)
                status = f"[green]enabled[/green] (port {port})"
            elif isinstance(api_cfg, dict):
                status = "[dim]disabled[/dim]"
        except (json.JSONDecodeError, OSError):
            pass

    _console.print(
        Panel(
            table,
            title="[bold]API Commands[/bold] [dim](beta)[/dim]",
            border_style="blue",
            padding=(1, 0),
        ),
    )
    _console.print(f"  Status: {status}")
    _console.print()


def nacl_available() -> bool:
    """Check if PyNaCl is importable."""
    from importlib.util import find_spec

    return find_spec("nacl.public") is not None


def api_install_hint() -> str:
    """Return the install command for PyNaCl based on install mode."""
    from ductor_bot.infra.install import detect_install_mode

    mode = detect_install_mode()
    if mode == "pipx":
        return "pipx inject ductor PyNaCl"
    return "pip install ductor[api]"


def api_enable() -> None:
    """Enable the API server: check deps, write config, generate token."""
    from ductor_bot.cli_commands.docker import docker_read_config

    if not nacl_available():
        hint = api_install_hint()
        _console.print(
            Panel(
                "[bold yellow]PyNaCl is required for the API server (E2E encryption).[/bold yellow]"
                f"\n\nInstall it with:\n\n  [bold]{hint}[/bold]"
                "\n\nThen run [bold]ductor api enable[/bold] again.",
                title="[bold]Missing dependency[/bold]",
                border_style="yellow",
                padding=(1, 2),
            ),
        )
        return

    # Bootstrap config/workspace when running in a fresh DUCTOR_HOME.
    from ductor_bot.workspace.paths import resolve_paths as _resolve_paths

    if not _resolve_paths().config_path.exists():
        from ductor_bot.__main__ import load_config

        load_config()

    result = docker_read_config()
    if result is None:
        return
    config_path, data = result

    import secrets as _secrets

    api = data.get("api", {})
    if not isinstance(api, dict):
        api = {}
    api["enabled"] = True
    if not api.get("token"):
        api["token"] = _secrets.token_urlsafe(32)
    api.setdefault("host", _BIND_ALL_INTERFACES)
    api.setdefault("port", 8741)
    api.setdefault("chat_id", 0)
    api.setdefault("allow_public", False)
    from ductor_bot.infra.json_store import atomic_json_save

    data["api"] = api
    atomic_json_save(config_path, data)

    _console.print(
        Panel(
            "[bold green]API server enabled.[/bold green]\n\n"
            f"  Host:   [cyan]{api['host']}[/cyan]\n"
            f"  Port:   [cyan]{api['port']}[/cyan]\n"
            f"  Token:  [cyan]{api['token']}[/cyan]\n\n"
            "[dim]Restart the bot to start the API server.[/dim]\n"
            "[dim]Designed for use with Tailscale or other private networks.[/dim]",
            title="[bold]API Server[/bold] [dim](beta)[/dim]",
            border_style="green",
            padding=(1, 2),
        ),
    )


def api_disable() -> None:
    """Disable the API server in config."""
    from ductor_bot.cli_commands.docker import docker_read_config

    result = docker_read_config()
    if result is None:
        return
    config_path, data = result

    api = data.get("api", {})
    if not isinstance(api, dict):
        api = {}
    from ductor_bot.infra.json_store import atomic_json_save

    api["enabled"] = False
    data["api"] = api
    atomic_json_save(config_path, data)
    _console.print("API server: [dim]disabled[/dim]")
    _console.print("[dim]Restart the bot to apply.[/dim]")


def cmd_api(args: list[str]) -> None:
    """Handle 'ductor api <subcommand>'."""
    sub = _parse_api_subcommand(args)
    if sub is None:
        print_api_help()
        return

    dispatch: dict[str, Callable[[], None]] = {
        "enable": api_enable,
        "disable": api_disable,
    }
    _console.print()
    dispatch[sub]()
    _console.print()
