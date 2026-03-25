# §76 Gateway 层语音自动 ASR 识别 — 插件注册架构

> TODO: a69ac328
> 状态: 需求已对齐，待排期开发
> **P0** | [← 返回索引](../REQUIREMENTS.md)

## Summary

Gateway 层实现 ASR 插件注册架构，支持语音消息自动识别。Gateway 不反向依赖 skill，通过插件注册目录解耦。Skill 自行按 schema 写注册 JSON，Gateway 启动时扫描加载。

## 背景

飞书 gateway 收到语音消息时需自动做 ASR 识别，当前依赖 agent 侧 skill 手动处理。

## 方案: ASR 插件注册架构

1. **注册目录**: `~/.nanobot/plugins/asr/`
2. **注册方式**: Skill 自己按 schema 写 JSON 到该目录（gateway 不反向依赖 skill）
3. **Gateway 加载**: 启动时扫描目录下所有 JSON，找第一个 `enabled: true` 的引擎
4. **调用方式**: subprocess 调注册文件中声明的脚本路径
5. **引擎降级**: skill 脚本内部的事（如 feishu-parser 内部多引擎降级），对 gateway 只是一个 plugin
6. **返回值**: `recognition` + `engine`（实际用的引擎名）+ `duration` 等
7. **开关**: JSON 注册文件有 `enabled` 字段，后续可 web 前端配置（本次不做前端）
8. **成功格式**: `[audio: file_key, duration, recognition="识别文本", engine=飞书]`
9. **失败降级**: 保持原有 `[audio: file_key]` 格式，不阻塞消息处理

## 注册文件示例

```json
{
  "engine": "feishu",
  "enabled": true,
  "script": "~/.nanobot/workspace/skills/feishu-parser/scripts/asr.py",
  "args_schema": {"file_key": "str", "duration": "int"},
  "output_schema": {"recognition": "str", "engine": "str"},
  "timeout": 30
}
```

## Glossary

| 术语 | 说明 |
|------|------|
| ASR | Automatic Speech Recognition，语音自动识别 |
| ASR Registry | Gateway 维护的语音识别引擎注册表，解耦 gateway 与具体 skill |
| 注册文件 | JSON 格式，描述引擎名、脚本路径、参数/输出 schema、超时、开关 |
| 降级 | 无注册引擎或调用失败时，回退到 `[audio: file_key]` |

## 验收 Checklist

- [ ] 👤 飞书发语音，agent 自动识别并复述
- [ ] 👤 ASR 失败/未注册时消息正常送达
- [ ] ✅ Gateway 代码不 import skill 模块（代码检查）
- [ ] ✅ 注册文件格式规范文档化
- [ ] ✅ feishu-parser SKILL.md 有 ASR 脚本接口规范
- [ ] ✅ 返回值包含 engine 字段（标识实际用的引擎）
