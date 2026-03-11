# Provider 体系架构

> 本文件包含 ProviderPool 动态切换机制和 Cache Control 策略的架构设计。

## 本文件索引

| 章节 | 标题 |
|------|------|
| §七 | ProviderPool — 运行时 Provider 动态切换 |
| §十三 | Cache Control 策略与 Usage Cache 字段 |

---

## 七、ProviderPool — 运行时 Provider 动态切换（Phase 16）

### 7.1 设计原则

1. **引入 ProviderPool 代理类**：实现 `LLMProvider` 接口，作为所有已配置 Provider 的统一门面
2. **AgentLoop 无感知**：AgentLoop 仍调用 `self.provider.chat()`，ProviderPool 内部路由到当前 active provider
3. **纯运行时状态**：切换 Provider 不修改 `config.json`，仅改变内存中的 active 指针
4. **向后兼容**：单 Provider 场景下，ProviderPool 退化为只有一个条目的 Pool，行为不变

### 7.2 架构图

```
config.json (静态声明)
  └── providers
        ├── anthropic       = { apiKey, apiBase }
        ├── anthropic_proxy = { apiKey, apiBase }
        ├── deepseek        = { apiKey }
        └── ...

启动时:
  _make_provider(config)
    → 遍历所有 providers，为每个有 apiKey 的构建 LLMProvider 实例
    → ProviderPool(
        providers={
          "anthropic": (LiteLLMProvider(...), "claude-opus-4-6"),
          "deepseek":  (LiteLLMProvider(...), "deepseek-chat"),
          ...
        },
        active_provider="anthropic",
        active_model="claude-opus-4-6",
      )

AgentLoop.__init__(provider=pool)
  → self.provider = pool
  → self.model = pool.active_model
```

### 7.3 ProviderPool 类设计

```python
class ProviderPool(LLMProvider):
    """运行时 Provider 动态切换池。

    实现 LLMProvider 接口，AgentLoop 将其视为普通 Provider。
    内部维护多个 Provider 实例，通过 active 指针路由请求。
    """

    def __init__(
        self,
        providers: dict[str, tuple[LLMProvider, str]],  # name → (provider, default_model)
        active_provider: str,
        active_model: str,
    ):
        self._providers = providers          # 所有已构建的 Provider
        self._active_provider = active_provider
        self._active_model = active_model

    # ── Properties ──────────────────────────────────────────

    @property
    def active_provider(self) -> str:
        """当前激活的 Provider 名称。"""
        return self._active_provider

    @property
    def active_model(self) -> str:
        """当前激活的模型名称。"""
        return self._active_model

    @property
    def available(self) -> dict[str, str]:
        """所有可用的 Provider 及其默认模型。"""
        return {name: model for name, (_, model) in self._providers.items()}

    # ── Methods ─────────────────────────────────────────────

    def switch(self, provider: str, model: str | None = None) -> None:
        """切换到指定 Provider（可选指定模型）。

        Args:
            provider: Provider 名称，必须在 available 中
            model: 可选模型名；不传则使用该 Provider 的默认模型
        """
        if provider not in self._providers:
            raise ValueError(f"Unknown provider: {provider}")
        self._active_provider = provider
        _, default_model = self._providers[provider]
        self._active_model = model or default_model

    async def chat(self, messages, **kwargs) -> "LLMResponse":
        """路由到当前 active Provider 的 chat 方法。

        忽略调用方传入的 model 参数，始终使用 active_model。
        """
        provider_instance, _ = self._providers[self._active_provider]
        kwargs["model"] = self._active_model
        return await provider_instance.chat(messages, **kwargs)
```

### 7.4 `/provider` 斜杠命令

在 `AgentLoop._process_message` 中处理，与现有斜杠命令（`/history`、`/compact` 等）同级：

| 命令 | 行为 | 示例 |
|------|------|------|
| `/provider` | 显示当前状态：active provider、active model、所有可用 provider | `/provider` |
| `/provider <name>` | 切换到指定 Provider，使用其默认模型 | `/provider deepseek` |
| `/provider <name> <model>` | 切换到指定 Provider 和模型 | `/provider anthropic claude-sonnet-4-20250514` |

**实现要点**：

```python
# agent/loop.py — _process_message 中

if content.startswith("/provider"):
    parts = content.split()
    if len(parts) == 1:
        # 显示状态
        status = f"Active: {self.provider.active_provider} / {self.provider.active_model}\n"
        status += "Available:\n"
        for name, model in self.provider.available.items():
            marker = " ←" if name == self.provider.active_provider else ""
            status += f"  {name}: {model}{marker}\n"
        return status
    else:
        name = parts[1]
        model = parts[2] if len(parts) > 2 else None
        self.provider.switch(name, model)
        self.model = self.provider.active_model  # 同步更新 AgentLoop.model
        return f"Switched to {self.provider.active_provider} / {self.provider.active_model}"
```

**多端支持**：CLI 交互模式、Gateway（IM 渠道）、Web Chat 均可使用 `/provider` 命令，因为它在 `_process_message` 层处理，所有调用路径都经过此方法。

### 7.5 `_make_provider` 改造

```python
# cli/commands.py — _make_provider 改造

PROVIDER_DEFAULT_MODELS = {
    "anthropic": "claude-opus-4-6",
    "anthropic_proxy": "claude-opus-4-6",
    "deepseek": "deepseek-chat",
    "openai": "gpt-4o",
    "gemini": "gemini-2.5-pro",
    # ... 其他 well-known providers
}

def _make_provider(config) -> ProviderPool:
    """构建 ProviderPool，包含所有已配置的 Provider。"""
    providers = {}

    for name, spec in config.providers.items():
        if not spec.get("apiKey"):
            continue  # 跳过未配置 apiKey 的 provider

        provider_instance = _build_single_provider(name, spec)
        default_model = PROVIDER_DEFAULT_MODELS.get(name, spec.get("defaultModel", "unknown"))
        providers[name] = (provider_instance, default_model)

    # 确定初始 active provider：从 config.agents.defaults.model 推断
    configured_model = config.agents.get("defaults", {}).get("model", "")
    active_provider, active_model = _resolve_active(providers, configured_model)

    return ProviderPool(
        providers=providers,
        active_provider=active_provider,
        active_model=active_model,
    )
```

**向后兼容**：如果只配置了一个 Provider，ProviderPool 只有一个条目，行为与改造前完全一致。

### 7.6 模块结构

```
nanobot/providers/
├── __init__.py          # 导出 ProviderPool
├── pool.py              # ProviderPool 类
├── base.py              # LLMProvider 基类 + LLMResponse
├── litellm_provider.py  # LiteLLM 统一 Provider
├── custom_provider.py   # 自定义 OpenAI 兼容 Provider
├── registry.py          # Provider 注册表（新增 anthropic_proxy ProviderSpec）
```

### 7.7 文件变更清单

| 文件 | 改动类型 | Phase | 说明 |
|------|----------|-------|------|
| `providers/pool.py` | 新增 | 16 | ProviderPool 类，实现 LLMProvider 接口 |
| `providers/registry.py` | 修改 | 16 | 新增 `anthropic_proxy` ProviderSpec |
| `providers/__init__.py` | 修改 | 16 | 导出 ProviderPool |
| `config/schema.py` | 修改 | 16 | 支持多 Provider 声明的 schema 校验 |
| `cli/commands.py` | 修改 | 16 | `_make_provider` 改造，构建 ProviderPool；`PROVIDER_DEFAULT_MODELS` 字典 |
| `agent/loop.py` | 修改 | 16 | `/provider` 斜杠命令处理；`self.model` 同步更新 |
| `tests/test_provider_pool.py` | 新增 | 16 | ProviderPool 单元测试（switch、chat 路由、边界情况） |

### 7.8 Provider 层错误传播策略（Hotfix §26, §33）

`LiteLLMProvider.chat()` 的异常处理需区分可重试与不可重试错误：

```
LiteLLMProvider.chat()
  ├── 可重试错误 (RateLimitError, 5xx, timeout 等) → re-raise
  │     └── 由上层 _chat_with_retry() 捕获并指数退避重试
  └── 不可重试错误 (AuthError, InvalidRequest 等) → LLMResponse(finish_reason="error")
        └── 优雅降级，错误信息写入 session
```

`_is_retryable()` 在三处保持一致逻辑（全部委托给 `agent/retry.py` 共享模块）：
- `AgentLoop._is_retryable()` — 主循环重试判断
- `SubagentManager._is_retryable()` — 子 agent 重试判断
- `LiteLLMProvider._is_retryable()` — provider 层错误分类

> **设计约束**：provider 层必须让可重试异常穿透，否则上层 retry 机制无法感知错误。

#### 7.8.1 不可重试消息模式排除（§33）

某些异常类名匹配可重试类（如 `ServiceUnavailableError`），但错误消息内容表明是配置/认证类错误，不应重试。`is_retryable()` 在类名/状态码匹配**之前**先检查 `_NON_RETRYABLE_MSG_PATTERNS` 排除列表：

```
is_retryable(error)
  ├── Step 1: 消息排除检查 — 匹配 "model_not_found"/"invalid_api_key" 等 → False
  ├── Step 2: 类名匹配 — ServiceUnavailableError/RateLimitError 等 → True
  ├── Step 3: 状态码匹配 — 429/5xx → True
  └── Step 4: 消息模式匹配 — "rate limit"/"overloaded" 等 → True
```

排除检查的优先级高于所有可重试判断，确保配置错误不被无意义重试。

---


## 十三、Cache Control 策略与 Usage Cache 字段 (§32)

### 13.1 Cache Control Breakpoint 策略

Anthropic API 限制每次请求最多 4 个 `cache_control` breakpoint。旧实现对所有 `role: "system"` 消息加 breakpoint，spawn 多个 subagent 后（每个 subagent 结果注入为 system 消息）轻易超限。

**新策略 — 精准 3 breakpoint**：

```
请求结构:
  tools:     [..., tool_N {cache_control ← #1}]     ← 缓存 tool 定义
  messages:  [system_prompt {cache_control ← #2},    ← 缓存 system prompt
              ...中间消息（无 breakpoint）...,
              last_msg {cache_control ← #3}]         ← 缓存对话历史
```

| Breakpoint | 位置 | 缓存效果 |
|------------|------|----------|
| #1 | `tools[-1]` | 跨 session 复用 tool 定义（tools 在所有 session 中相同） |
| #2 | `messages[0]` | 跨 session 复用 system prompt（大量 skills/context 文本） |
| #3 | `messages[-1]` | 同 session 多轮对话复用历史前缀 |

中间 system 消息（subagent 结果、budget alert 等）**不加** breakpoint，确保总数 ≤ 3。

### 13.2 Usage Cache 字段数据链路

```
LiteLLM Response
  └─ _parse_response()
      ├─ cache_creation_input_tokens  ← getattr(usage, field, 0) or 0
      └─ cache_read_input_tokens
          │
          ▼
  accumulated_usage (loop.py)
          │
          ├─── on_usage() callback → web-chat worker → SSE → frontend
          │
          └─── UsageRecorder.record() → SQLite token_usage 表
                                          │
                                          ▼
                                    AnalyticsDB (web-chat)
                                    ├── get_global_usage()
                                    ├── get_session_usage()
                                    ├── get_daily_usage()
                                    └── by_model / by_session 聚合
```

### 13.3 SQLite Schema Migration

两个独立的 migration 点（分别在 nanobot core 和 web-chat 中）：

- **`UsageRecorder._migrate()`** (nanobot core `usage/recorder.py`)
- **`AnalyticsDB._migrate()`** (web-chat `analytics.py`)

Migration 策略：
1. `PRAGMA table_info(token_usage)` 获取已有列名集合
2. 遍历 `_MIGRATION_SQL` 列表，提取目标列名
3. 列名不存在 → `ALTER TABLE ADD COLUMN ... DEFAULT 0`
4. 已存在 → 跳过（幂等）
5. `try/except OperationalError` 处理并发竞争

此策略确保：
- **全新部署**：`CREATE TABLE` 包含所有列，migration 无操作
- **已有部署升级**：自动 ALTER TABLE，旧数据 cache 字段默认 0
- **重复执行**：幂等，不报错

---


## §二十一 LLM 连接超时拆分 (§42)

### 设计

`_LLM_TIMEOUT` 从 `float(120.0)` 改为 `httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0)`。

**参数说明**：
- `connect=30.0` — 连接超时 30 秒，快速检测网络断开
- `read=120.0` — 读取超时 120 秒，大上下文补全需要时间
- `write=30.0` — 写入超时 30 秒
- `pool=30.0` — 连接池超时 30 秒

**兼容性**：LiteLLM 支持 `httpx.Timeout` 对象作为 `timeout` 参数，内部传递给 httpx client。

