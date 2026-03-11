"""Tests for dynamic Telegram onboarding helpers in local chat."""

from __future__ import annotations

import json
from pathlib import Path

from ductor_bot.cli_commands.local_chat import _parse_user_ids, _update_telegram_config
from ductor_bot.workspace.paths import DuctorPaths


def _make_paths(tmp_path: Path) -> DuctorPaths:
    home = tmp_path / "home"
    fw = tmp_path / "fw"
    fw.mkdir(parents=True, exist_ok=True)
    return DuctorPaths(ductor_home=home, home_defaults=fw / "workspace", framework_root=fw)


def test_parse_user_ids_valid() -> None:
    assert _parse_user_ids("123, 456") == [123, 456]


def test_parse_user_ids_invalid() -> None:
    assert _parse_user_ids("abc,123") is None
    assert _parse_user_ids("0") is None
    assert _parse_user_ids("") is None


def test_update_telegram_config_writes_token_users_and_channels(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(json.dumps({"channels": {"enabled": ["codex"]}}), encoding="utf-8")

    ok = _update_telegram_config(paths, token="12345678:ABCDEFGHIJKLMNOPQRSTUVWX123456", user_ids=[1, 2])
    assert ok is True

    data = json.loads(paths.config_path.read_text(encoding="utf-8"))
    assert data["telegram_token"] == "12345678:ABCDEFGHIJKLMNOPQRSTUVWX123456"
    assert data["allowed_user_ids"] == [1, 2]
    assert "telegram" in [str(item).lower() for item in data["channels"]["enabled"]]
