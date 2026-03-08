"""Quick self-check for channel transport wiring."""

from __future__ import annotations

from types import SimpleNamespace

from ductor_bot.channel.runtime import build_bus_transports
from ductor_bot.config import AgentConfig


def main() -> int:
    cfg = AgentConfig(
        channels={"enabled": ["telegram", "dingtalk"]},
        dingtalk={
            "enabled": True,
            "app_key": "demo-key",
            "app_secret": "demo-secret",
            "agent_id": "demo-agent",
        },
    )
    fake_bot = SimpleNamespace()
    transports = build_bus_transports(cfg, fake_bot)  # type: ignore[arg-type]
    names = [type(t).__name__ for t in transports]
    print("channel selfcheck ok")
    print("transports:", ", ".join(names))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
