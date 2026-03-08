"""DingTalk channel placeholders."""

from ductor_bot.channel.dingtalk.ingress import (
    ChannelInboundEvent,
    DingTalkIngressConfig,
    DingTalkIngressError,
    DingTalkIngressRejected,
    DingTalkIngressUnauthorized,
    DingTalkSignatureConfig,
    DingTalkWebhookIngress,
    build_ingress_config,
)
from ductor_bot.channel.dingtalk.session import build_dingtalk_session_key
from ductor_bot.channel.dingtalk.transport import DingTalkTransport
from ductor_bot.channel.dingtalk.webhook import DingTalkWebhookHandler

__all__ = [
    "ChannelInboundEvent",
    "DingTalkIngressConfig",
    "DingTalkIngressError",
    "DingTalkIngressRejected",
    "DingTalkIngressUnauthorized",
    "DingTalkSignatureConfig",
    "DingTalkTransport",
    "DingTalkWebhookIngress",
    "DingTalkWebhookHandler",
    "build_dingtalk_session_key",
    "build_ingress_config",
]
