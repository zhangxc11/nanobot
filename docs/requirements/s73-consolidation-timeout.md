# §73 Consolidation timeout 调整 + 失败 dump

> TODO: aa1b2fbb
> 状态: 需求已对齐，待排期开发
> **P0** | [← 返回索引](../REQUIREMENTS.md)

## Summary

Consolidation 超时从 600s 延长到 900s，timeout 失败时 dump 完整 API call 信息到 LLM logs，便于后续复现分析。不做降级策略。

## 背景

修复后成功率 93.3%，唯一生产失败类型是 timeout（feishu.ST.1774315758，3次重试全部超时，丢失90条消息归档）。

## 方案

1. `_CONSOLIDATION_TIMEOUT.read` 从 600s → 900s
2. Timeout 失败时 dump 完整 API call 信息到日志（当前 timeout 不记录到 LLM logs）

## Glossary

| 术语 | 说明 |
|------|------|
| consolidation | session 消息过多时的自动归档压缩流程 |

## 验收 Checklist

- [ ] ✅ timeout 参数已改为 900s（代码检查）
- [ ] ✅ timeout 失败时 LLM logs 有完整 dump 记录（代码检查）
- [ ] ✅ dev 环境上线后，跑 100 个 exec sleep 触发 consolidation，确认正常完成
