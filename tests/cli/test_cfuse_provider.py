"""Tests for CfuseCLI provider: command building, send, and parsing."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from ductor_bot.cli.base import CLIConfig
from ductor_bot.cli.cfuse_provider import CfuseCLI, _parse_output
from ductor_bot.cli.stream_events import ResultEvent, StreamEvent


def _make_cli(monkeypatch: pytest.MonkeyPatch, **overrides: Any) -> CfuseCLI:
    monkeypatch.setattr("ductor_bot.cli.cfuse_provider.which", lambda _: "/usr/bin/cfuse")
    return CfuseCLI(
        CLIConfig(
            provider="cfuse",
            model=overrides.pop("model", "antchat/Qwen3-Coder-480B-A35B-Instruct"),
            working_dir=overrides.pop("working_dir", "/tmp/workspace"),
            **overrides,
        )
    )


class TestBuildCommand:
    def test_basic_structure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cli = _make_cli(monkeypatch)
        cmd = cli._build_command("hello")
        assert cmd[0] == "/usr/bin/cfuse"
        assert "-C" in cmd
        assert "-o" in cmd
        assert "text" in cmd
        assert "--approval-mode" in cmd
        assert "-m" in cmd
        assert cmd[-1] == "hello"

    def test_bypass_permissions_maps_to_yolo(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cli = _make_cli(monkeypatch, permission_mode="bypassPermissions")
        cmd = cli._build_command("hello")
        idx = cmd.index("--approval-mode")
        assert cmd[idx + 1] == "yolo"

    def test_non_full_access_enables_sandbox(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cli = _make_cli(monkeypatch, sandbox_mode="workspace-write")
        cmd = cli._build_command("hello")
        assert "-s" in cmd

    def test_full_access_omits_sandbox_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cli = _make_cli(monkeypatch, sandbox_mode="full-access")
        cmd = cli._build_command("hello")
        assert "-s" not in cmd

    def test_system_prompt_is_composed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cli = _make_cli(
            monkeypatch,
            system_prompt="Before",
            append_system_prompt="After",
        )
        cmd = cli._build_command("Middle")
        assert cmd[-1] == "Before\n\nMiddle\n\nAfter"


async def test_send_uses_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = _make_cli(monkeypatch)
    with patch("ductor_bot.cli.cfuse_provider.run_oneshot_subprocess", new_callable=AsyncMock) as run:
        run.return_value.result = "ok"
        response = await cli.send("hello")
    assert response.result == "ok"


async def test_send_streaming_falls_back_to_result_event(monkeypatch: pytest.MonkeyPatch) -> None:
    cli = _make_cli(monkeypatch)
    with patch.object(cli, "send", new_callable=AsyncMock) as send:
        send.return_value = _parse_output(b"hello", b"", 0)
        events = [event async for event in cli.send_streaming("hello")]
    assert len(events) == 1
    assert isinstance(events[0], ResultEvent)
    assert events[0].result == "hello"


def test_parse_output_success() -> None:
    response = _parse_output(b"hello world\n", b"", 0)
    assert response.result == "hello world"
    assert response.is_error is False


def test_parse_output_failure_uses_stderr() -> None:
    response = _parse_output(b"", b"permission denied", 1)
    assert response.result == "permission denied"
    assert response.is_error is True
