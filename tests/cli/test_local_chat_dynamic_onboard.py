"""Tests for dynamic Telegram onboarding helpers in local chat."""

from __future__ import annotations

import json
from pathlib import Path

from ductor_bot.cli_commands.local_chat import (
    ChatRuntime,
    LocalChatSettings,
    _build_setup_message,
    _default_model_for_provider,
    _parse_user_ids,
    _update_provider_model_config,
    _telegram_configured,
    _update_telegram_config,
)
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


def test_telegram_configured_false_when_missing(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(json.dumps({}), encoding="utf-8")
    assert _telegram_configured(paths) is False


def test_telegram_configured_true_when_token_and_users_present(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(
        json.dumps(
            {
                "telegram_token": "12345678:ABCDEFGHIJKLMNOPQRSTUVWX123456",
                "allowed_user_ids": [1, 2],
            }
        ),
        encoding="utf-8",
    )
    assert _telegram_configured(paths) is True


def test_build_setup_message_includes_status_and_command(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(json.dumps({}), encoding="utf-8")
    settings = LocalChatSettings(
        host="127.0.0.1",
        port=8741,
        token="abc",
        chat_id=1,
        channel_id=None,
        ductor_home=paths.ductor_home,
        dry_run=False,
        probe=False,
        reset_state=False,
        auth_timeout=10,
    )
    runtime = ChatRuntime(
        ws_url="ws://127.0.0.1:8741/ws",
        active_provider="cfuse",
        active_model="antchat/Qwen3-Coder-480B-A35B-Instruct",
    )
    message = _build_setup_message(settings, runtime)
    assert "Setup Panel" in message
    assert "Active provider: cfuse" in message
    assert "Telegram: not configured" in message
    assert "/telegram <token> <user_id[,user_id]>" in message
    assert "/provider cfuse" in message
    assert "/model antchat/Qwen3-Coder-480B-A35B-Instruct" in message


def test_default_model_for_provider_cfuse() -> None:
    assert _default_model_for_provider("cfuse") == "antchat/Qwen3-Coder-480B-A35B-Instruct"


def test_update_provider_model_config_persists_values(tmp_path: Path) -> None:
    paths = _make_paths(tmp_path)
    paths.config_path.parent.mkdir(parents=True, exist_ok=True)
    paths.config_path.write_text(json.dumps({}), encoding="utf-8")
    ok = _update_provider_model_config(
        paths,
        provider="cfuse",
        model="antchat/Qwen3-Coder-480B-A35B-Instruct",
    )
    assert ok is True
    data = json.loads(paths.config_path.read_text(encoding="utf-8"))
    assert data["provider"] == "cfuse"
    assert data["model"] == "antchat/Qwen3-Coder-480B-A35B-Instruct"
