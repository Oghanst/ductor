# Ductor DingTalk Integration Plan (Bootstrap)

## Goal
Add native DingTalk channel support to ductor while preserving the existing automation core (sessions, tasks, cron, webhooks, multi-agent).

## Scope (initial)
- Introduce `ductor_bot/channel/dingtalk/` module scaffold.
- Define inbound envelope mapping from DingTalk events.
- Reuse existing orchestrator/session/task pipelines.
- Implement docs-first development with hourly closure iterations.

## Branch
- `feature/ductor-dingtalk-interface`

## First milestones
1. Transport abstraction and channel registry extension.
2. DingTalk adapter MVP (text in/out, one account).
3. Reliability layer (dedup, inflight lock, retry, fallback).
4. Integration with tasks/cron/webhooks and end-to-end tests.
