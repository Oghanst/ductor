"""Tests for DingTalk inbound normalization and ingress checks."""

import asyncio

from ductor_bot.channel.dingtalk.ingress import (
    DingTalkIngressConfig,
    DingTalkSignatureConfig,
    DingTalkWebhookIngress,
    build_dingtalk_signature,
    normalize_dingtalk_event,
    parse_event_timestamp,
    validate_timestamp_window,
    verify_dingtalk_signature,
)
from ductor_bot.channel.dingtalk.session import build_dingtalk_session_key


def test_signature_timestamp_mode() -> None:
    secret = "demo-secret"
    timestamp = "1710000000000"
    cfg = DingTalkSignatureConfig(mode="timestamp", encoding="base64")

    signature = build_dingtalk_signature(secret, timestamp, config=cfg)

    assert verify_dingtalk_signature(
        secret,
        signature=signature,
        timestamp=timestamp,
        config=cfg,
    )
    assert verify_dingtalk_signature(
        secret,
        signature=signature.replace("+", " "),
        timestamp=timestamp,
        config=cfg,
    )


def test_parse_timestamp_ms_to_seconds() -> None:
    raw_ms = 1710000000123

    parsed = parse_event_timestamp(raw_ms, now=1.0)

    assert parsed == 1710000000.123


def test_timestamp_window_validation() -> None:
    assert validate_timestamp_window(100.0, now=110.0, window_sec=15)
    assert not validate_timestamp_window(100.0, now=140.0, window_sec=15)


def test_normalize_event_basic() -> None:
    raw = {
        "eventId": "evt-1",
        "createTime": 1710000000123,
        "senderStaffId": "user-1",
        "conversationId": "chat-1",
        "conversationType": "2",
        "text": {"content": "hello"},
        "atUsers": [{"userId": "user-2"}],
    }

    event = normalize_dingtalk_event(raw, default_tenant="corp-1", now=1710000000.0)

    assert event.event_id == "evt-1"
    assert event.tenant_id == "corp-1"
    assert event.is_group is True
    assert event.text == "hello"
    assert event.mentions == ["user-2"]


def test_normalize_event_reply_webhook() -> None:
    raw = {
        "eventId": "evt-2",
        "createTime": 1710000000123,
        "senderStaffId": "user-1",
        "conversationId": "chat-1",
        "sessionWebhook": "https://example.invalid/session",
        "text": {"content": "ping"},
    }

    event = normalize_dingtalk_event(raw, default_tenant="corp-1", now=1710000000.0)

    assert event.reply_webhook == "https://example.invalid/session"


def test_build_session_key_stable_int() -> None:
    raw = {
        "eventId": "evt-3",
        "createTime": 1710000000123,
        "senderStaffId": "user-1",
        "conversationId": "chat-xyz",
        "text": {"content": "hello"},
    }

    event = normalize_dingtalk_event(raw, default_tenant="corp-1", now=1710000000.0)
    key = build_dingtalk_session_key(event)

    assert isinstance(key.chat_id, int)


def test_ingress_dedup_skips_second_delivery() -> None:
    handled = []
    now = 1710000000.0

    async def _handler(event) -> None:
        handled.append(event.event_id)

    config = DingTalkIngressConfig(
        signing_secret="",
        verify_timestamp_window_sec=300,
        dedup_ttl_sec=120,
        dm_policy="allowlist",
        group_policy="allowlist",
        allow_users=[],
        allow_groups=[],
        corp_id="corp-1",
        signature_mode="timestamp",
        signature_encoding="base64",
    )

    ingress = DingTalkWebhookIngress(config, on_event=_handler, now=lambda: now)

    raw = {
        "eventId": "evt-dup",
        "createTime": int(now * 1000),
        "senderStaffId": "user-1",
        "conversationId": "chat-1",
        "conversationType": "2",
        "text": {"content": "hello"},
    }

    asyncio.run(
        ingress.handle_event(
            raw,
            signature="",
            timestamp=str(int(now * 1000)),
            nonce="n",
        )
    )
    asyncio.run(
        ingress.handle_event(
            raw,
            signature="",
            timestamp=str(int(now * 1000)),
            nonce="n",
        )
    )

    assert handled == ["evt-dup"]
