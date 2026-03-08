"""Channel runtime transport wiring tests."""

from ductor_bot.channel.runtime import build_bus_transports
from ductor_bot.config import AgentConfig


def test_build_bus_transports_default_only_telegram() -> None:
    config = AgentConfig()

    transports = build_bus_transports(config, object())  # type: ignore[arg-type]

    assert [type(item).__name__ for item in transports] == ["TelegramTransport"]


def test_build_bus_transports_with_dingtalk_enabled_and_configured() -> None:
    config = AgentConfig(
        channels={"enabled": ["telegram", "dingtalk"]},
        dingtalk={
            "enabled": True,
            "app_key": "app-key",
            "app_secret": "app-secret",
            "agent_id": "agent-id",
        },
    )

    transports = build_bus_transports(config, object())  # type: ignore[arg-type]

    assert [type(item).__name__ for item in transports] == [
        "TelegramTransport",
        "DingTalkTransport",
    ]


def test_build_bus_transports_skips_dingtalk_when_credentials_incomplete() -> None:
    config = AgentConfig(
        channels={"enabled": ["telegram", "dingtalk"]},
        dingtalk={
            "enabled": True,
            "app_key": "",
            "app_secret": "app-secret",
            "agent_id": "agent-id",
        },
    )

    transports = build_bus_transports(config, object())  # type: ignore[arg-type]

    assert [type(item).__name__ for item in transports] == ["TelegramTransport"]
