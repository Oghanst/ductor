"""Build message-bus transports from channel configuration."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ductor_bot.bus.bus import TransportAdapter

if TYPE_CHECKING:
    from ductor_bot.bot.app import TelegramBot
    from ductor_bot.config import AgentConfig

logger = logging.getLogger(__name__)


def build_bus_transports(config: "AgentConfig", tg_bot: "TelegramBot") -> list[TransportAdapter]:
    """Build transports for the active runtime.

    Telegram runtime always includes Telegram transport. Optional extra channels
    are appended as passive transports to support phased rollout.
    """
    # Lazy imports avoid runtime circular dependency:
    # runtime -> telegram_transport -> bot.app -> runtime
    from ductor_bot.bus.telegram_transport import TelegramTransport
    from ductor_bot.channel.dingtalk.transport import DingTalkTransport

    transports: list[TransportAdapter] = [TelegramTransport(tg_bot)]
    enabled = {name.lower() for name in config.channels.enabled}

    if "dingtalk" in enabled:
        if not config.dingtalk.enabled:
            logger.warning(
                "channels.enabled includes dingtalk but dingtalk.enabled=false; skipping"
            )
        else:
            dingtalk_transport = DingTalkTransport(config.dingtalk)
            if dingtalk_transport.is_configured:
                transports.append(dingtalk_transport)
            else:
                logger.warning(
                    "dingtalk channel enabled but credentials are incomplete; skipping transport"
                )
    return transports
