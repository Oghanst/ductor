"""Tests for DingTalk webhook HTTP handler."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from ductor_bot.channel.dingtalk.webhook import DingTalkWebhookHandler
from ductor_bot.config import DingTalkConfig
from ductor_bot.orchestrator.registry import OrchestratorResult


@asynccontextmanager
async def _make_client(**config_overrides: Any) -> AsyncIterator[tuple[TestClient, AsyncMock]]:
    orchestrator = AsyncMock()
    orchestrator.handle_message.return_value = OrchestratorResult(text="pong")

    config = DingTalkConfig(**config_overrides)
    handler = DingTalkWebhookHandler(config, orchestrator=orchestrator)

    app = web.Application()
    app[web.AppKey("_orch")] = orchestrator
    app.router.add_post("/dingtalk", handler.handle)

    test_server = TestServer(app)
    client = TestClient(test_server)
    await client.start_server()
    try:
        yield client, orchestrator
    finally:
        await client.close()


class TestDingTalkWebhookHandler:
    def test_empty_body_returns_ok(self) -> None:
        async def _run() -> None:
            async with _make_client() as (client, _orch):
                resp = await client.post("/dingtalk")
                assert resp.status == 200
                data = await resp.json()
                assert data["errcode"] == 0

        asyncio.run(_run())

    def test_invalid_json_returns_400(self) -> None:
        async def _run() -> None:
            async with _make_client() as (client, _orch):
                resp = await client.post(
                    "/dingtalk",
                    headers={"Content-Type": "application/json"},
                    data="not json{{",
                )
                assert resp.status == 400
                data = await resp.json()
                assert data["error"] == "invalid_json"

        asyncio.run(_run())

    def test_non_object_returns_400(self) -> None:
        async def _run() -> None:
            async with _make_client() as (client, _orch):
                resp = await client.post(
                    "/dingtalk",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps([1, 2, 3]),
                )
                assert resp.status == 400
                data = await resp.json()
                assert data["error"] == "body_must_be_object"

        asyncio.run(_run())

    def test_missing_signature_returns_401(self) -> None:
        async def _run() -> None:
            async with _make_client(signing_secret="secret") as (client, _orch):
                payload = {
                    "eventId": "evt-1",
                    "createTime": 1710000000123,
                    "senderStaffId": "user-1",
                    "conversationId": "chat-1",
                    "conversationType": "2",
                    "text": {"content": "hello"},
                    "timestamp": "1710000000123",
                }
                resp = await client.post(
                    "/dingtalk",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(payload),
                )
                assert resp.status == 401
                data = await resp.json()
                assert data["error"] == "unauthorized"

        asyncio.run(_run())

    def test_valid_event_dispatches(self) -> None:
        async def _run() -> None:
            async with _make_client(verify_timestamp_window_sec=0) as (client, orch):
                payload = {
                    "eventId": "evt-2",
                    "createTime": 1710000000123,
                    "senderStaffId": "user-1",
                    "conversationId": "chat-1",
                    "conversationType": "2",
                    "text": {"content": "hello"},
                }
                resp = await client.post(
                    "/dingtalk",
                    headers={"Content-Type": "application/json"},
                    data=json.dumps(payload),
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["errcode"] == 0
                orch.handle_message.assert_awaited()

        asyncio.run(_run())
