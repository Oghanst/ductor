# Ductor 钉钉通道集成计划（初始化）

## 目标
在不破坏现有自动化核心（会话、任务、Cron、Webhook、多智能体）的前提下，为 `ductor` 增加原生钉钉通道能力。

## 初始范围
- 引入 `ductor_bot/channel/dingtalk/` 模块骨架。
- 定义钉钉事件到 `Envelope` 的映射边界。
- 复用现有 orchestrator/session/task 主流程，不重复造轮子。
- 采用“文档先行 + 闭包迭代”的推进方式。

## 开发分支
- `feature/ductor-dingtalk-interface`

## 第一阶段里程碑
1. 完成 transport 抽象接入与 channel 注册扩展。
2. 实现钉钉适配器 MVP（文本收发，单账号）。
3. 增加可靠性能力（去重、并发锁、重试、降级）。
4. 与 tasks/cron/webhooks 串联并补齐端到端验证。

## Round-1 Delivered (2026-03-08)
- Added `ductor_bot/channel/` runtime extension point for transport registration.
- Introduced `DingTalkTransport` placeholder implementing `TransportAdapter`.
- Added config scaffolding:
  - `channels.enabled` for channel list.
  - `dingtalk.*` block for future credentials and routing fields.
- Added verification:
  - unit tests in `tests/channel/test_runtime.py`.
  - self-check script: `python -m ductor_bot.channel.selfcheck`.
