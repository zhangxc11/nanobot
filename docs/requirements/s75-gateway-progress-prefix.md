# §75 Gateway progress 中间回复加前缀标识

> TODO: 9404ffc9
> 状态: 需求已对齐，待排期开发
> **P2** | [← 返回索引](../REQUIREMENTS.md)

## Summary

给 gateway 的 progress 中间回复加 💭 前缀，区分中间回复和最终回复。仅影响飞书端显示，不影响 dump jsonl。

## 背景

agent 多轮迭代时，中间轮次文本通过 on_progress 发到飞书，最终回复通过 publish_outbound 发送。两条消息内容相似易混淆。

## 方案

- loop.py L789-793 的 `_progress_fn(clean)` 发送前加 💭 前缀
- 仅影响飞书端显示，不影响 dump jsonl

## Glossary

| 术语 | 说明 |
|------|------|
| progress 回复 | agent 多轮迭代时中间轮次通过 on_progress 发到飞书的文本 |
| 最终回复 | 通过 publish_outbound 发送的最终结果 |

## 验收 Checklist

- [ ] 👤 飞书收到的中间回复带 💭 前缀（人工验证）
- [ ] 👤 最终回复不带 💭 前缀（人工验证）
- [ ] ✅ session jsonl 中 dump 的内容不含 💭 前缀（代码检查）
- [ ] ✅ web-chat 渠道不受影响
