# §74 config.json extraHeaders 空字符串致 Pydantic 验证失败

> TODO: 0b23ced8
> 状态: 需求已对齐，待排期开发
> **P0** | [← 返回索引](../REQUIREMENTS.md)

## Summary

修复 save_config 的 model_dump 将 None 序列化为空字符串导致 Pydantic 验证失败的 bug。

## 背景

save_config 的 model_dump 在某些情况下将 None 序列化为空字符串，导致 dev worker 重启失败。已临时删除空字符串字段，根因待查。

## 方案

- 在 save_config 或 model 层面修复 None → 空字符串的序列化问题
- 确保 None 值字段不写入或写入 null

## Glossary

| 术语 | 说明 |
|------|------|
| save_config | nanobot 保存运行时配置到 config.json 的函数 |
| extraHeaders | config 中用于 LLM API 额外 header 的字段 |

## 验收 Checklist

- [ ] ✅ save_config 后 config.json 不出现空字符串字段
- [ ] ✅ None 值字段正确处理（不写入或写入 null）
- [ ] ✅ dev worker 可正常重启加载
- [ ] ✅ 验证后 config.json 恢复原状（⚠️ 验证过程不能改坏 config）
