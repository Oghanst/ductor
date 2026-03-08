"""HTTP webhook handler for DingTalk inbound events."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from ductor_bot.channel.dingtalk.ingress import (
    DingTalkIngressError,
    DingTalkIngressRejected,
    DingTalkIngressUnauthorized,
    DingTalkWebhookIngress,
    build_ingress_config,
)
from ductor_bot.channel.dingtalk.session import build_dingtalk_session_key
from ductor_bot.channel.dingtalk.transport import DingTalkTransport
from ductor_bot.log_context import set_log_context
from ductor_bot.bus.envelope import Envelope, Origin

if TYPE_CHECKING:
    from ductor_bot.channel.dingtalk.ingress import ChannelInboundEvent
    from ductor_bot.config import DingTalkConfig
    from ductor_bot.orchestrator.core import Orchestrator
    from ductor_bot.session.key import SessionKey

logger = logging.getLogger(__name__)


class DingTalkWebhookHandler:
    """Parse and dispatch DingTalk webhook requests via the orchestrator."""

    def __init__(
        self,
        config: "DingTalkConfig",
        *,
        orchestrator: "Orchestrator",
        transport: DingTalkTransport | None = None,
    ) -> None:
        self._config = config
        self._orchestrator = orchestrator
        self._transport = transport or DingTalkTransport(config)
        self._ingress = DingTalkWebhookIngress(
            build_ingress_config(config),
            on_event=self._on_event,
        )

    async def handle(self, request: web.Request) -> web.Response:
        """Handle a DingTalk webhook HTTP request."""
        set_log_context(operation="wh")
        payload_result = await self._parse_body(request)
        if isinstance(payload_result, web.Response):
            return payload_result

        payload, raw_body = payload_result

        signature = _extract_auth_value(
            request,
            payload,
            "sign",
            "signature",
            "x-dingtalk-signature",
            "x-dingtalk-sign",
        )
        timestamp = _extract_auth_value(
            request,
            payload,
            "timestamp",
            "timeStamp",
            "x-dingtalk-timestamp",
            "x-acs-dingtalk-timestamp",
        )
        nonce = _extract_auth_value(
            request,
            payload,
            "nonce",
            "x-dingtalk-nonce",
        )
        tenant = _extract_auth_value(
            request,
            payload,
            "corpId",
            "corp_id",
            "tenantId",
            "tenant_id",
        )

        try:
            accepted = await self._ingress.handle_event(
                payload,
                signature=signature,
                timestamp=timestamp,
                nonce=nonce or None,
                body=raw_body,
                tenant_id=tenant or None,
            )
        except DingTalkIngressUnauthorized as exc:
            logger.warning("DingTalk ingress unauthorized: %s", exc)
            return _error_response("unauthorized", status=401)
        except DingTalkIngressRejected as exc:
            logger.warning("DingTalk ingress rejected: %s", exc)
            return _error_response("rejected", status=400)
        except DingTalkIngressError as exc:
            logger.warning("DingTalk ingress error: %s", exc)
            return _error_response("invalid_request", status=400)
        except Exception:
            logger.exception("DingTalk ingress handler failed")
            return _error_response("server_error", status=500)

        if not accepted:
            logger.info("DingTalk ingress dedup skip")
        return _ack_response()

    async def _on_event(self, event: "ChannelInboundEvent") -> None:
        if not event.text:
            logger.info(
                "DingTalk ingress skipped: empty text event_id=%s", event.event_id
            )
            return

        key = build_dingtalk_session_key(event)
        set_log_context(operation="msg", chat_id=key.chat_id)

        result = await self._orchestrator.handle_message(key, event.text)
        await self._deliver_result(event, key, result.text)

    async def _deliver_result(
        self,
        event: "ChannelInboundEvent",
        key: "SessionKey",
        text: str,
    ) -> None:
        if not text:
            logger.info("DingTalk response empty; skip delivery event_id=%s", event.event_id)
            return

        envelope = Envelope(
            origin=Origin.USER,
            chat_id=key.chat_id,
            topic_id=key.topic_id,
            result_text=text,
            status="ok",
            metadata={
                "channel": "dingtalk",
                "tenant_id": event.tenant_id,
                "event_id": event.event_id,
                "reply_webhook": event.reply_webhook,
            },
        )
        await self._transport.deliver(envelope)

    async def _parse_body(
        self, request: web.Request
    ) -> tuple[dict[str, Any], bytes] | web.Response:
        """Parse JSON body into dict, return error response on failure."""
        raw_body = await request.read()
        if not raw_body:
            return {}, raw_body

        try:
            payload: Any = json.loads(raw_body)
        except (json.JSONDecodeError, ValueError):
            logger.warning("DingTalk ingress rejected: invalid JSON")
            return _error_response("invalid_json", status=400)

        if not isinstance(payload, dict):
            logger.warning("DingTalk ingress rejected: body not object")
            return _error_response("body_must_be_object", status=400)

        return payload, raw_body


def _extract_auth_value(
    request: web.Request,
    payload: dict[str, Any],
    *keys: str,
) -> str:
    for key in keys:
        header_val = request.headers.get(key)
        if header_val:
            return str(header_val)
        query_val = request.rel_url.query.get(key)
        if query_val:
            return str(query_val)
        if key in payload and payload[key] not in (None, ""):
            return str(payload[key])
    return ""


def _ack_response() -> web.Response:
    return web.json_response({"errcode": 0, "errmsg": "ok"})


def _error_response(error: str, *, status: int = 400) -> web.Response:
    return web.json_response({"errcode": 1, "error": error}, status=status)
