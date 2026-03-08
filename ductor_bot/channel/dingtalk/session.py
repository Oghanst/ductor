"""Session-key helpers for DingTalk inbound events."""

from __future__ import annotations

import hashlib

from ductor_bot.channel.dingtalk.ingress import ChannelInboundEvent
from ductor_bot.session import SessionKey


def build_dingtalk_session_key(event: ChannelInboundEvent) -> SessionKey:
    """Build a stable SessionKey for DingTalk events.

    DingTalk identifiers are often strings; map them to stable ints so
    SessionKey remains compatible with existing storage/lock semantics.
    """
    chat_key = event.chat_id or event.user_id or event.event_id
    chat_id = _parse_int(chat_key) or _stable_int(f"dingtalk:{event.tenant_id}:{chat_key}")
    topic_id = None
    if event.thread_id:
        topic_id = _parse_int(event.thread_id)
        if topic_id is None:
            topic_id = _stable_int(
                f"dingtalk:{event.tenant_id}:{event.chat_id}:{event.thread_id}"
            )
    return SessionKey(chat_id=chat_id, topic_id=topic_id)


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.isdigit():
        try:
            return int(stripped)
        except (TypeError, ValueError):
            return None
    return None


def _stable_int(value: str) -> int:
    digest = hashlib.sha1(value.encode("utf-8")).digest()
    raw = int.from_bytes(digest[:8], "big", signed=False)
    return raw & 0x7FFF_FFFF_FFFF_FFFF
