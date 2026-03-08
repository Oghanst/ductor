"""DingTalk inbound event normalization and validation."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Iterable
from urllib.parse import unquote_plus

from ductor_bot.bot.dedup import DedupeCache

if TYPE_CHECKING:
    from ductor_bot.config import DingTalkConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InboundMedia:
    """Normalized inbound media reference."""

    media_type: str
    media_id: str
    filename: str = ""
    url: str = ""


@dataclass(slots=True)
class ChannelInboundEvent:
    """Normalized inbound event for channel-agnostic processing."""

    channel: str
    tenant_id: str
    event_id: str
    event_ts: float
    user_id: str
    chat_id: str
    thread_id: str | None
    is_group: bool
    text: str
    media: list[InboundMedia] = field(default_factory=list)
    mentions: list[str] = field(default_factory=list)
    reply_to_event_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class DingTalkIngressError(RuntimeError):
    """Base error for DingTalk ingress."""


class DingTalkIngressUnauthorized(DingTalkIngressError):
    """Raised when authentication or authorization fails."""


class DingTalkIngressRejected(DingTalkIngressError):
    """Raised when payload validation fails (e.g., timestamp window)."""


@dataclass(slots=True)
class DingTalkSignatureConfig:
    """Signature verification options."""

    mode: str = "timestamp"  # "timestamp" or "body"
    encoding: str = "base64"  # "base64" or "hex"


@dataclass(slots=True)
class DingTalkIngressConfig:
    """Ingress settings derived from DingTalkConfig."""

    signing_secret: str
    verify_timestamp_window_sec: int
    dedup_ttl_sec: int
    dm_policy: str
    group_policy: str
    allow_users: list[str]
    allow_groups: list[str]
    corp_id: str
    signature_mode: str
    signature_encoding: str


class InflightGuard:
    """Async guard to serialize work per inflight key."""

    def __init__(self, *, ttl_sec: float = 300.0, max_entries: int = 2000) -> None:
        self._locks: dict[str, tuple[asyncio.Lock, float]] = {}
        self._ttl = max(0.0, ttl_sec)
        self._max = max(1, max_entries)
        self._dict_lock = asyncio.Lock()

    @asynccontextmanager
    async def acquire(self, key: str) -> Iterable[None]:
        lock = await self._get_lock(key)
        if lock.locked():
            logger.debug("DingTalk inflight wait key=%s", key)
        async with lock:
            yield

    async def _get_lock(self, key: str) -> asyncio.Lock:
        async with self._dict_lock:
            now = time.monotonic()
            self._prune(now)
            entry = self._locks.get(key)
            if entry is None:
                lock = asyncio.Lock()
            else:
                lock = entry[0]
            self._locks[key] = (lock, now)
            return lock

    def _prune(self, now: float) -> None:
        if self._ttl > 0:
            expired = [
                k
                for k, (lock, ts) in self._locks.items()
                if now - ts > self._ttl and not lock.locked()
            ]
            for key in expired:
                del self._locks[key]
        if len(self._locks) > self._max:
            idle = [k for k, (lock, _) in self._locks.items() if not lock.locked()]
            for key in idle[: max(1, len(idle) // 2)]:
                del self._locks[key]


def build_ingress_config(config: "DingTalkConfig") -> DingTalkIngressConfig:
    """Create a DingTalkIngressConfig from the global DingTalkConfig."""
    signing_secret = config.signing_secret or config.webhook_secret
    return DingTalkIngressConfig(
        signing_secret=signing_secret,
        verify_timestamp_window_sec=config.verify_timestamp_window_sec,
        dedup_ttl_sec=config.dedup_ttl_sec,
        dm_policy=config.dm_policy,
        group_policy=config.group_policy,
        allow_users=list(config.allow_users),
        allow_groups=list(config.allow_groups),
        corp_id=config.corp_id,
        signature_mode=config.signature_mode,
        signature_encoding=config.signature_encoding,
    )


def build_dingtalk_signature(
    secret: str,
    timestamp: str,
    *,
    config: DingTalkSignatureConfig | None = None,
    body: bytes | None = None,
) -> str:
    """Build the expected DingTalk signature with configurable payload rules."""
    if config is None:
        config = DingTalkSignatureConfig()
    payload: bytes
    if config.mode == "body":
        payload = body or b""
    else:
        payload = f"{timestamp}\n{secret}".encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).digest()
    if config.encoding == "hex":
        return digest.hex()
    return base64.b64encode(digest).decode("utf-8")


def verify_dingtalk_signature(
    secret: str,
    *,
    signature: str,
    timestamp: str,
    config: DingTalkSignatureConfig | None = None,
    body: bytes | None = None,
) -> bool:
    """Validate DingTalk signature using constant-time comparison."""
    if not secret or not signature:
        logger.warning("DingTalk signature missing secret or signature")
        return False
    expected = build_dingtalk_signature(secret, timestamp, config=config, body=body)
    # DingTalk signatures are sometimes URL-encoded or have "+" decoded as spaces.
    if "%" in signature:
        normalized = unquote_plus(signature)
    elif " " in signature and "+" not in signature:
        normalized = signature.replace(" ", "+")
    else:
        normalized = signature
    ok = hmac.compare_digest(normalized, expected)
    if not ok:
        logger.warning("DingTalk signature mismatch")
    return ok


def parse_event_timestamp(raw_value: Any, *, now: float | None = None) -> float:
    """Parse DingTalk timestamp value to seconds since epoch."""
    if now is None:
        now = time.time()
    if raw_value is None:
        return now
    try:
        ts = float(raw_value)
    except (TypeError, ValueError):
        return now
    # Heuristic: milliseconds if larger than 1e12 or 1e10
    if ts > 1e12:
        ts = ts / 1000.0
    elif ts > 1e10:
        ts = ts / 1000.0
    return ts


def validate_timestamp_window(event_ts: float, *, now: float, window_sec: int) -> bool:
    """Return True if event_ts is within the acceptable time window."""
    if window_sec <= 0:
        return True
    return abs(now - event_ts) <= window_sec


def normalize_dingtalk_event(
    raw: dict[str, Any],
    *,
    default_tenant: str = "",
    now: float | None = None,
) -> ChannelInboundEvent:
    """Normalize DingTalk webhook payload to ChannelInboundEvent."""
    tenant_id = _first_string(
        raw,
        "corpId",
        "corp_id",
        "tenantId",
        "tenant_id",
    ) or default_tenant

    event_id = _first_string(
        raw,
        "eventId",
        "event_id",
        "msgId",
        "msg_id",
        "messageId",
        "message_id",
    )
    event_ts = parse_event_timestamp(_first_value(raw, "createTime", "create_time", "timestamp"), now=now)

    user_id = _first_string(
        raw,
        "senderStaffId",
        "senderUserId",
        "senderId",
        "sender_staff_id",
        "sender_user_id",
        "sender_id",
    )

    chat_id = _first_string(
        raw,
        "conversationId",
        "chatId",
        "conversation_id",
        "chat_id",
    )

    thread_id = _first_string(
        raw,
        "sessionWebhook",
        "threadId",
        "session_webhook",
        "thread_id",
    )

    text_value = _extract_text(raw)
    mentions = _extract_mentions(raw)
    is_group = _extract_is_group(raw)
    reply_to = _first_string(raw, "replyMsgId", "reply_msg_id", "replyMessageId", "reply_message_id")

    if not event_id:
        event_id = _fallback_event_id(raw, tenant_id, chat_id, user_id, text_value, event_ts)
        logger.debug("DingTalk event_id missing, generated fallback=%s", event_id)

    return ChannelInboundEvent(
        channel="dingtalk",
        tenant_id=tenant_id,
        event_id=event_id,
        event_ts=event_ts,
        user_id=user_id,
        chat_id=chat_id,
        thread_id=thread_id,
        is_group=is_group,
        text=text_value,
        mentions=mentions,
        media=[],
        reply_to_event_id=reply_to,
        raw=_redact_raw(raw),
    )


def build_dedup_key(event: ChannelInboundEvent) -> str:
    """Build dedup key for DingTalk inbound events."""
    tenant = event.tenant_id or "unknown"
    return f"{tenant}:{event.event_id}"


class DingTalkWebhookIngress:
    """Ingress handler for DingTalk webhook payloads."""

    def __init__(
        self,
        config: DingTalkIngressConfig,
        *,
        on_event: Callable[[ChannelInboundEvent], Awaitable[None]],
        dedup: DedupeCache | None = None,
        inflight: InflightGuard | None = None,
        now: Callable[[], float] | None = None,
    ) -> None:
        self._config = config
        self._on_event = on_event
        self._dedup = dedup or DedupeCache(ttl_seconds=config.dedup_ttl_sec)
        self._inflight = inflight or InflightGuard(ttl_sec=max(60.0, config.dedup_ttl_sec))
        self._now = now or time.time

    async def handle_event(
        self,
        raw_event: dict[str, Any],
        *,
        signature: str,
        timestamp: str,
        nonce: str | None = None,
        body: bytes | None = None,
        tenant_id: str | None = None,
    ) -> bool:
        """Validate, normalize, deduplicate, and dispatch the inbound event."""
        _ = nonce  # Reserved for future replay protection.
        config = DingTalkSignatureConfig(
            mode=self._config.signature_mode,
            encoding=self._config.signature_encoding,
        )
        secret = self._config.signing_secret
        if secret:
            if not verify_dingtalk_signature(
                secret,
                signature=signature,
                timestamp=timestamp,
                config=config,
                body=body,
            ):
                raise DingTalkIngressUnauthorized("signature mismatch")

        event = normalize_dingtalk_event(
            raw_event,
            default_tenant=tenant_id or self._config.corp_id,
            now=self._now(),
        )
        if not validate_timestamp_window(
            event.event_ts,
            now=self._now(),
            window_sec=self._config.verify_timestamp_window_sec,
        ):
            raise DingTalkIngressRejected("timestamp outside allowed window")

        if not _authorize_sender(event, self._config):
            raise DingTalkIngressUnauthorized("sender not authorized")

        dedup_key = build_dedup_key(event)
        async with self._inflight.acquire(dedup_key):
            if self._dedup.check(dedup_key):
                logger.info(
                    "DingTalk ingress dedup skip event_id=%s tenant=%s",
                    event.event_id,
                    event.tenant_id,
                )
                return False
            await self._on_event(event)

        logger.info(
            "DingTalk ingress accepted event_id=%s tenant=%s chat=%s",
            event.event_id,
            event.tenant_id,
            event.chat_id,
        )
        return True


# -- helpers ---------------------------------------------------------------


def _first_value(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def _first_string(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key in raw and raw[key] not in (None, ""):
            return str(raw[key])
    return ""


def _extract_text(raw: dict[str, Any]) -> str:
    if isinstance(raw.get("text"), dict):
        content = raw["text"].get("content")
        if content:
            return str(content)
    for key in ("content", "text", "message", "msg"):
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_mentions(raw: dict[str, Any]) -> list[str]:
    mentions: list[str] = []
    for key in ("atUsers", "atUsersIds", "atUserIds", "at_user_ids"):
        value = raw.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    user = item.get("userId") or item.get("staffId") or item.get("id")
                    if user:
                        mentions.append(str(user))
                elif item:
                    mentions.append(str(item))
    return mentions


def _extract_is_group(raw: dict[str, Any]) -> bool:
    is_group = raw.get("isGroup")
    if isinstance(is_group, bool):
        return is_group
    conv_type = raw.get("conversationType")
    if conv_type in ("2", 2, "GROUP", "group"):
        return True
    chat_type = raw.get("chatType")
    if chat_type in ("group", "conversation", "2"):
        return True
    return False


def _fallback_event_id(
    raw: dict[str, Any],
    tenant_id: str,
    chat_id: str,
    user_id: str,
    text: str,
    event_ts: float,
) -> str:
    payload = {
        "tenant": tenant_id,
        "chat": chat_id,
        "user": user_id,
        "text": text,
        "ts": event_ts,
        "raw": raw,
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha1(encoded).hexdigest()[:20]


def _redact_raw(raw: dict[str, Any]) -> dict[str, Any]:
    redact_keys = {
        "signature",
        "sign",
        "signing_secret",
        "webhook_secret",
        "secret",
        "access_token",
        "accessToken",
        "token",
    }
    sanitized: dict[str, Any] = {}
    for key, value in raw.items():
        if key in redact_keys:
            sanitized[key] = "***"
        else:
            sanitized[key] = value
    return sanitized


def _authorize_sender(event: ChannelInboundEvent, config: DingTalkIngressConfig) -> bool:
    if event.is_group:
        if config.group_policy == "allowlist" and config.allow_groups:
            return event.chat_id in config.allow_groups
        if config.group_policy == "denylist" and event.chat_id in config.allow_groups:
            return False
        return True

    if config.dm_policy == "allowlist" and config.allow_users:
        return event.user_id in config.allow_users
    if config.dm_policy == "denylist" and event.user_id in config.allow_users:
        return False
    return True
