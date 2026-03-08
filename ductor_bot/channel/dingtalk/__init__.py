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
from ductor_bot.channel.dingtalk.transport import DingTalkTransport

__all__ = [
    "ChannelInboundEvent",
    "DingTalkIngressConfig",
    "DingTalkIngressError",
    "DingTalkIngressRejected",
    "DingTalkIngressUnauthorized",
    "DingTalkSignatureConfig",
    "DingTalkTransport",
    "DingTalkWebhookIngress",
    "build_ingress_config",
]
