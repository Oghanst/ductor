"""DingTalk delivery adapter (phase-1 placeholder)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ductor_bot.bus.envelope import Envelope
    from ductor_bot.config import DingTalkConfig

logger = logging.getLogger(__name__)


class DingTalkTransport:
    """Implements TransportAdapter with no-op delivery for initial scaffolding."""

    def __init__(self, config: "DingTalkConfig") -> None:
        self._config = config

    @property
    def is_configured(self) -> bool:
        """Whether minimal DingTalk credentials are present."""
        return bool(self._config.app_key and self._config.app_secret and self._config.agent_id)

    async def deliver(self, envelope: "Envelope") -> None:
        logger.info(
            "DingTalk transport placeholder: envelope=%s chat_id=%s status=%s",
            envelope.origin.value,
            envelope.chat_id,
            envelope.status,
        )

    async def deliver_broadcast(self, envelope: "Envelope") -> None:
        logger.info(
            "DingTalk transport placeholder(broadcast): envelope=%s status=%s",
            envelope.origin.value,
            envelope.status,
        )
