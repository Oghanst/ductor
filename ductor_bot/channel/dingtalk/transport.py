"""DingTalk delivery adapter (phase-1 webhook reply support)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiohttp import ClientSession, ClientTimeout

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
        reply_webhook = str(envelope.metadata.get("reply_webhook") or "").strip()
        if reply_webhook:
            await self._send_via_webhook(reply_webhook, envelope.result_text)
            return
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

    async def _send_via_webhook(self, webhook_url: str, text: str) -> None:
        if not text:
            logger.info("DingTalk transport skip empty text webhook=%s", _shorten(webhook_url))
            return
        payload: dict[str, Any] = {
            "msgtype": "text",
            "text": {"content": text},
        }
        timeout = ClientTimeout(total=10)
        async with ClientSession(timeout=timeout) as session:
            async with session.post(webhook_url, json=payload) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    logger.warning(
                        "DingTalk webhook send failed status=%s body=%s",
                        resp.status,
                        body[:200],
                    )
                    raise RuntimeError(f"DingTalk webhook send failed ({resp.status})")


def _shorten(url: str, *, max_len: int = 48) -> str:
    if len(url) <= max_len:
        return url
    return f"{url[: max_len - 3]}..."
