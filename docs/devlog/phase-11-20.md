# Phase 11-20 归档

## 本文件索引

| Phase | 标题 | 日期 |
|-------|------|------|
| 11 | LLM API 速率限制重试机制 | 2026-02-27 |
| 12 | /new 命令重构 — 新建 Session | 2026-02-27 |
| 13 | /stop 命令 — 取消运行中的任务 | 2026-02-28 |
| 14 | 大图片自动压缩 | 2026-02-27 |
| 15 | 图片存储架构改进 | 2026-02-27 |
| 16 | ProviderPool — 运行时 Provider 动态切换 | 2026-02-28 |
| 17 | 飞书合并转发消息（merge_forward）解析 | 2026-02-28 |
| 18 | 飞书通道文件附件发送修复 | 2026-03-01 |
| 19 | Gateway 并发执行 + User Injection + Per-Session Provider | 2026-03-01 |
| 20 | /session 状态查询命令 | 2026-03-01 |

---

## Phase 11: LLM API 速率限制重试机制 ✅

### 需求来源
- nanobot REQUIREMENTS.md §十二
- 用户报告 Anthropic API 偶发 RateLimitError 导致任务中断

### 目标
在 `_run_agent_loop` 中为 `provider.chat()` 调用增加指数退避重试，自动处理暂时性错误。

### 任务清单

- ✅ **T11.1** `agent/loop.py` — 新增 `_is_retryable()` 静态方法
  - 判断异常是否为暂时性可重试错误
  - 支持: RateLimitError, APIConnectionError, APITimeoutError, HTTP 429/5xx
  - 字符串回退: "rate limit", "overloaded", "capacity"

- ✅ **T11.2** `agent/loop.py` — 新增 `_chat_with_retry()` 异步方法
  - 包裹 `provider.chat()` 调用
  - 指数退避: 10s → 20s → 40s → 80s → 160s
  - 最多重试 5 次
  - 重试时通过 progress_fn 通知用户（best-effort，不影响重试逻辑）
  - 重试时记录 warning 日志

- ✅ **T11.3** `_run_agent_loop` 中替换 `provider.chat()` 为 `_chat_with_retry()`

- ✅ **T11.4** 单元测试 — 26 项全部通过
  - `_is_retryable()`: 19 项（6 类名匹配 + 5 状态码 + 3 消息匹配 + 5 非重试）
  - `_chat_with_retry()`: 7 项（首次成功、重试成功、指数退避、超限失败、非重试即抛、进度通知、进度错误不影响重试）
  - 现有测试无回归: 132 passed / 20 failed（与改动前一致）

- ✅ **T11.5** Git 提交 + 合并 + 文档更新
  - commit `777c2d5` on feat/llm-retry → merged to local

---

## Phase 12: /new 命令重构 — 新建 Session ✅

### 需求来源
- nanobot REQUIREMENTS.md §十三
- 用户希望 `/new` 语义更直观：创建全新 session，而非归档+清空

### 目标
- `/new` → `/flush`：归档当前 session 记忆（原 `/new` 行为）
- 新 `/new`：创建新 session，后续对话不带之前的记录

### 任务清单

- ✅ **T12.1** `agent/loop.py` — 将原 `/new` 逻辑改为 `/flush`
  - 命令名从 `/new` 改为 `/flush`
  - 更新 `/help` 输出

- ✅ **T12.2** `session/manager.py` — 新增 `create_new_session()` 和路由映射
  - `create_new_session(channel, chat_id, old_key)`: 归档旧 session 文件（加时间戳后缀），创建新空 session
  - `resolve_session_key(natural_key)`: 通过路由表解析实际 session key
  - 路由映射持久化到 `sessions/_routing.json`

- ✅ **T12.3** `agent/loop.py` — 新增 `/new` 处理逻辑
  - 调用 `sessions.create_new_session()` 归档旧文件 + 创建新 session
  - `_process_message` 中通过 `resolve_session_key()` 解析路由
  - Gateway/CLI 通道统一处理

- ✅ **T12.4** web-chat 前端 — `/new` 改为前端拦截
  - `/new`: 调用 `createSession()` API + 切换到新 session（纯前端）
  - `/flush`: 发送到后端（原 `/new` 的归档行为）
  - 更新 HELP_TEXT

- ✅ **T12.5** 测试验证 + Git 提交
  - 11 项新测试全部通过（resolve_session_key、create_new_session、routing persistence）
  - 前端 build 成功
  - nanobot commit `d26b27e` on feat/new-session → merged to local
  - web-chat commit `8155561` on main

### Git
- nanobot: commit `d26b27e` on feat/new-session → merged to local
- web-chat: commit `8155561` on main

---

## Phase 13: /stop 命令 — 取消运行中的任务 ✅

### 需求来源
- 用户请求：飞书端需要 `/stop` 功能取消正在执行的长任务

### 目标
在 gateway channel（飞书/Telegram 等）中支持 `/stop` 命令，取消当前正在执行的 agent 任务。

### 设计要点

**核心挑战**: `AgentLoop.run()` 原本是顺序处理消息的——消费一条、处理完、再消费下一条。当 agent 正在处理长任务时，`/stop` 命令会排在队列中等待，无法及时响应。

**解决方案**: 将 `_process_message()` 包装为 `asyncio.Task`，在任务运行期间继续监听队列中的 `/stop` 命令：

```
run() 主循环:
  1. 消费消息
  2. 如果是 /stop → _handle_stop() 取消 active task
  3. 否则 → 创建 asyncio.Task 执行 _process_message_safe()
  4. _wait_with_stop_listener(): 等待 task 完成，同时监听 /stop
     - /stop → 取消 task
     - 其他消息 → 放回队列（下次处理）
```

**关键实现**:
- `_active_task`: 当前运行的 asyncio.Task
- `_active_task_msg`: 当前正在处理的 InboundMessage（用于匹配 channel+chat_id）
- `_handle_stop()`: 匹配 channel+chat_id 后取消任务
- `_process_message_safe()`: 捕获 CancelledError，发送 "⏹ Task stopped." 友好提示
- `/stop` 只取消**同一 chat** 的任务，不同 chat 的 `/stop` 返回 "No active task"

### 任务清单

- ✅ **T13.1** `agent/loop.py` — run() 改造为 Task-based 并发处理
  - `_active_task` / `_active_task_msg` / `_active_task_session_key` 追踪
  - run() 中 `/stop` 直接拦截，不进入 _process_message
  - `_handle_stop()`: 匹配 channel+chat_id，取消 active task
  - `_wait_with_stop_listener()`: 等待 task + 监听 /stop，其他消息放回队列
  - `_process_message_safe()`: 包装 _process_message，捕获 CancelledError

- ✅ **T13.2** `agent/loop.py` — /help 和 _process_message 更新
  - /help 输出增加 `/stop — Stop the currently running task`
  - _process_message 中 `/stop` fallback（process_direct 调用时返回 "No active task"）

- ✅ **T13.3** `channels/telegram.py` — Telegram 适配
  - BOT_COMMANDS 增加 `/stop`
  - CommandHandler("stop") 注册到 _forward_command
  - _on_help 输出增加 `/stop`

- ✅ **T13.4** 测试验证 — 9 项全部通过
  - TestStopCommandDirect: /stop 直接调用返回 "No active task"（2 项）
  - TestHelpIncludesStop: /help 包含 /stop（1 项）
  - TestStopCancelsTask: _handle_stop 无任务/匹配取消/不同 chat 忽略/CancelledError 捕获（4 项）
  - TestActiveTaskTracking: 处理中 _active_task 已设置（1 项）
  - TestStopEndToEnd: run() 循环中 /stop 取消长任务（1 项）
  - 现有测试无回归: 108 passed（99 + 9 新）

- ✅ **T13.5** Git 提交 + 合并 + Gateway 重启
  - commit `cbed25b` on feat/stop-command → merged to local
  - Gateway 已重启，飞书 WebSocket 已连接

### 影响范围

| 文件 | 改动 |
|------|------|
| `agent/loop.py` | run() 改造 + 新增 _handle_stop/_wait_with_stop_listener/_process_message_safe + /help 更新 |
| `channels/telegram.py` | BOT_COMMANDS + CommandHandler + help 文本 |
| `tests/test_stop_command.py` | 9 项新测试 |

### 注意事项
- **飞书 channel 无需改动**: 飞书用户发送 `/stop` 文本消息，由 FeishuChannel._on_message → bus → AgentLoop.run() 处理
- **Web-chat 已有 kill API**: web-chat 的 `/stop` 按钮通过 worker kill API 实现，不受此改动影响
- **CLI 交互模式**: `/stop` 在 CLI 中也可用（通过 run() 循环），但 CLI 用户更习惯 Ctrl+C

---

## Phase 14: 大图片自动压缩 (2026-02-27) ✅

### 需求来源
- nanobot REQUIREMENTS.md §十四
- 用户报告：飞书/Web 端发送大图片（>5MB）时 LLM API 拒绝

### 目标
图片超过 5MB 时自动压缩（缩小尺寸 + 降低 JPEG 质量），确保不超过 LLM API 限制。

### 设计要点
- **统一入口**: `ContextBuilder._build_user_content()` 中检查文件大小
- **压缩策略**: 最长边缩至 2048px + JPEG quality 从 85 递减至 30
- **格式处理**: RGBA/P/LA → RGB 转换（JPEG 不支持透明通道）
- **优雅降级**: Pillow 未安装时 log warning，原样发送
- **新增依赖**: Pillow>=10.0.0,<12.0.0

### 任务清单

- ✅ **T14.1** `agent/context.py` — `_build_user_content()` 增加大小检查
  - 读取文件后检查 `len(raw) > IMAGE_MAX_BYTES`
  - 超过阈值调用 `_compress_image()` 压缩

- ✅ **T14.2** `agent/context.py` — 新增 `_compress_image()` 静态方法
  - Step 1: 缩小尺寸（最长边 > max_dimension 时等比缩放）
  - Step 2: JPEG quality 递减编码（85 → 75 → ... → 30）
  - 返回 `(compressed_bytes, "image/jpeg")`
  - Pillow 未安装时 graceful fallback

- ✅ **T14.3** `pyproject.toml` — 新增 Pillow 依赖

- ✅ **T14.4** 测试验证 — 8 项全部通过
  - TestCompressImage: 5 项（小图不压缩、大图压缩、RGBA→RGB、大尺寸缩放、自定义 target_bytes）
  - TestBuildUserContent: 3 项（无 media、小图包含、非图片跳过）
  - 现有 context 测试无回归

- ✅ **T14.5** Git 提交 + 合并 + 文档更新
  - commit `2b9c260` on feat/image-compress → merged to local

### 影响范围

| 文件 | 改动 |
|------|------|
| `agent/context.py` | `_build_user_content()` 大小检查 + `_compress_image()` 静态方法 + import io/logger |
| `pyproject.toml` | 新增 `Pillow>=10.0.0,<12.0.0` |
| `tests/test_image_compress.py` | 8 项新测试 |

---

## Phase 15: 图片存储架构改进 (2026-02-27) ✅

### 需求来源
- nanobot REQUIREMENTS.md §七A
- 用户报告：飞书图片不在 workspace 下载目录中 + session JSONL 因 base64 膨胀

### 目标
1. 统一所有通道的媒体文件存储路径到 `workspace/uploads/<date>/`
2. Session JSONL 中用文件路径引用替代 base64，读取时按需还原

### 设计要点

**统一存储路径**：
- 飞书/Telegram/Discord 的 `media_dir` 从 `~/.nanobot/media/` 改为 `~/.nanobot/workspace/uploads/<date>/`
- 文件命名保持各通道原有逻辑（image_key、file_id 等）

**Session JSONL 去 base64**：
- `SessionManager._prepare_entry()` 中检测 `data:` base64 图片 URL
- 将 base64 解码后保存为文件，URL 替换为 `file:///path`（带 MIME 元数据）
- `Session.get_history()` 中检测 `file:///` URL，读取文件还原为 base64
- 向后兼容：旧 session 中已有的 `data:` base64 仍可正常加载和使用

**文件引用格式**：
```
file:///absolute/path/to/image.jpg?mime=image/jpeg
```

### 任务清单

- ✅ **T15.1** 统一媒体存储路径 — 修改三个通道 (commit `11b1298`)
  - `channels/feishu.py` — `_download_and_save_media()` 中 media_dir 改为 workspace/uploads/<date>/
  - `channels/telegram.py` — 媒体下载路径同步修改
  - `channels/discord.py` — 媒体下载路径同步修改

- ✅ **T15.2** `session/manager.py` — _prepare_entry() 增加 base64 提取与文件保存 (commit `11b1298`)
  - `_extract_and_save_images()`: 检测 content 为 list 且包含 `type: "image_url"` 的 item
  - `_save_base64_image()`: 对 `data:mime;base64,...` URL 解码 → 保存文件 → 替换为 `file:///` 引用
  - 文件保存到 `workspace/uploads/<date>/<hash>.<ext>`
  - 文件名用内容 hash（MD5 前 12 位）避免重复

- ✅ **T15.3** `session/manager.py` — get_history() 增加文件引用还原 (commit `11b1298`)
  - `_restore_image_refs()`: 检测 `file:///` URL → 读取文件 → base64 编码 → 还原为 `data:mime;base64,...`
  - `_load_file_as_data_url()`: 文件不存在时 graceful degradation（log warning，移除该图片 item）

- ✅ **T15.4** 测试验证 (commit `11b1298`)
  - 24 项测试全部通过:
    - _save_base64_image: 5 项（JPEG/PNG 保存、去重、无效 URL、目录创建）
    - _extract_and_save_images: 5 项（字符串不变、None 不变、提取 base64、file ref 透传、多图片）
    - _restore_image_refs: 5 项（字符串不变、还原 file ref、丢弃缺失文件、data URL 透传、混合 ref）
    - _prepare_entry 集成: 4 项（图片消息、文本消息、assistant 消息、tool 截断）
    - get_history 集成: 3 项（还原 file ref、向后兼容 data URL、缺失文件优雅处理）
    - 完整 round-trip: 2 项（保存→加载→还原、JSONL 大小验证）
  - 现有测试无回归: 184 passed / 20 failed（与改动前一致）

- ✅ **T15.5** Git 提交 + 合并 + 文档更新
  - commit `11b1298` on feat/image-storage → merged to local

### 影响范围

| 文件 | 改动 |
|------|------|
| `channels/feishu.py` | media_dir 路径改为 workspace/uploads/<date>/ |
| `channels/telegram.py` | media_dir 路径改为 workspace/uploads/<date>/ |
| `channels/discord.py` | media_dir 路径改为 workspace/uploads/<date>/ |
| `session/manager.py` | 新增 4 个模块级函数 + _prepare_entry 集成 + get_history 集成 |
| `tests/test_image_storage.py` | 24 项新测试 |

---

## Phase 16: ProviderPool — 运行时 Provider 动态切换 ✅

### 需求来源
- 用户需求：agent token 消耗量大，需要根据任务难度切换不同 API 源控制成本
- 不同 channel（webchat、gateway、命令行）独立维护 provider 状态
- 不修改 config.json 来切换，纯运行时状态

### 目标
1. 新增 `anthropic_proxy` provider 配置槽位
2. 引入 ProviderPool 类，实现 LLMProvider 接口，支持运行时切换 active provider + model
3. 新增 `/provider` 斜杠命令（全 channel 可用）
4. 任务执行中禁止切换

### 任务清单

- ✅ **T16.1** `providers/registry.py` — 新增 `anthropic_proxy` ProviderSpec
- ✅ **T16.2** `config/schema.py` — `ProvidersConfig` 增加 `anthropic_proxy` 字段
- ✅ **T16.3** `providers/pool.py` — **新建** ProviderPool 类（LLMProvider 接口 + 运行时切换）
- ✅ **T16.4** `providers/__init__.py` — 导出 ProviderPool
- ✅ **T16.5** `cli/commands.py` — `_make_provider` 改为构建 ProviderPool
- ✅ **T16.6** `agent/loop.py` — 新增 `/provider` 斜杠命令 + `/help` 更新
- ✅ **T16.7** 测试验证 — 22 项 ProviderPool 测试 + 5 项命令测试全部通过，无回归
- ✅ **T16.8** Git 提交
  - commit `e31c837` on feat/provider-pool → merged to local

### 影响范围

| 文件 | 改动 |
|------|------|
| `providers/registry.py` | 新增 `anthropic_proxy` ProviderSpec |
| `config/schema.py` | `ProvidersConfig` 增加 `anthropic_proxy` 字段 |
| `providers/pool.py` | **新建** ProviderPool 类（LLMProvider 接口代理 + 运行时切换） |
| `providers/__init__.py` | 导出 ProviderPool |
| `cli/commands.py` | `_make_provider` 改为构建 ProviderPool + PROVIDER_DEFAULT_MODELS |
| `agent/loop.py` | `/provider` 斜杠命令 + `/help` 更新 |
| `tests/test_provider_pool.py` | 22 项新测试 |

---

## Phase 17: 飞书合并转发消息（merge_forward）解析 ✅

### 需求来源
- nanobot REQUIREMENTS.md Backlog: 飞书合并转发消息解析
- 用户在飞书中将聊天记录通过「合并转发」发送给 nanobot 时，当前只显示 `[merged forward messages]` 占位文本

### 目标
解析 `merge_forward` 消息的子消息 ID 列表，调用飞书 API 逐条获取原始消息内容，拼接为可读文本格式传给 Agent。

### 技术方案
1. **merge_forward content 结构**: content JSON 包含 `message_id_list`（子消息 ID 数组）
2. **API 调用**: 使用 `lark_oapi` SDK 的 `GetMessageRequest` 获取单条消息详情
3. **子消息解析**: 支持 text / post / image / file / interactive / system 等多种类型
4. **格式化输出**: 将子消息拼接为 `--- forwarded messages ---` 包裹的格式
5. **错误处理**: 权限不足/消息不存在时 graceful degradation
6. **嵌套处理**: 嵌套 merge_forward 不递归，标记为 `[nested merged forward messages]`

### 任务清单

- ✅ **T17.1** `channels/feishu.py` — 新增 `_get_message_detail_sync()` 方法
  - 使用 `GetMessageRequest` 获取单条消息详情
  - 返回 dict(msg_type, content, sender_id, create_time, message_id) 或 None
  - 同步方法（在 executor 中调用）

- ✅ **T17.2** `channels/feishu.py` — 新增 `_resolve_merge_forward()` 异步方法
  - 解析 merge_forward 的 content JSON，提取 `message_id_list`
  - 逐条调用 `_get_message_detail_sync()` 获取子消息
  - 根据子消息 msg_type 提取文本内容（复用已有的 `_extract_post_content`, `_extract_share_card_content` 等）
  - 图片/文件类型调用 `_download_and_save_media()` 下载
  - 拼接为 `--- forwarded messages ---` 包裹格式返回

- ✅ **T17.3** `channels/feishu.py` — `_on_message()` 中 merge_forward 分支改为调用 `_resolve_merge_forward()`
  - 从 `share_chat/share_user/interactive/...` 联合分支中拆出 merge_forward
  - 单独处理，支持返回 media_paths

- ✅ **T17.4** 测试验证 — 18 项全部通过
  - _get_message_detail_sync: 7 项（成功、API失败、空items、None items、异常、无效JSON、无sender）
  - _resolve_merge_forward: 10 项（文本消息、空列表、无列表、API失败优雅降级、混合类型、图片子消息、嵌套转发、富文本、全部失败、跳过空ID）
  - _extract_share_card_content fallback: 1 项
  - 现有测试无回归: 236 passed / 20 failed（与改动前一致）

- ✅ **T17.5** Git 提交 + 合并
  - commit `67845aa` on feat/merge-forward → merged to local

### 影响范围

| 文件 | 改动 |
|------|------|
| `channels/feishu.py` | import `GetMessageRequest` + `_get_message_detail_sync()` + `_resolve_merge_forward()` + `_on_message()` merge_forward 分支 |
| `tests/test_merge_forward.py` | 18 项新测试 |

---

## Phase 18: 飞书通道文件附件发送修复 ✅

### 需求来源
- nanobot REQUIREMENTS.md §十七：飞书通道文件附件发送修复
- Backlog: 2026-03-01 用户在 feishu.lab 通道发送 docx 文件 3 次均失败

### 根因
LLM 调用 `message` 工具时传入 `channel: "feishu"` 覆盖了默认的 `"feishu.lab"`，导致 `_dispatch_outbound` 找不到匹配的 channel（注册名为 `feishu.lab`/`feishu.ST`），消息被丢弃。`MessageTool.execute()` 误报成功（fire-and-forget）。

### 任务清单

- ✅ **T18.1** `channels/manager.py` — `_resolve_channel()` channel 名称容错
  - 精确匹配失败时尝试前缀匹配（`name + "."` 前缀）
  - 唯一匹配 → 使用；多个匹配 → 丢弃并 warning
  - 添加 debug 日志

- ✅ **T18.2** `agent/tools/message.py` — 移除 `channel`/`chat_id` 参数暴露
  - 从 parameters schema 中移除 `channel` 和 `chat_id`
  - `execute()` 始终使用 `_default_channel` 和 `_default_chat_id`
  - 保留内部 `set_context()` 机制和 `**kwargs` 兼容

- ✅ **T18.3** 测试验证 — 13 项全部通过
  - _resolve_channel: 7 项（精确匹配、前缀单匹配、前缀歧义、无匹配、无点分隔符、精确优先）
  - MessageTool routing: 6 项（忽略 LLM channel、使用默认、media 传递、无 context 报错、sent_in_turn 追踪、schema 检查）
  - 回归测试：249 passed / 20 failed（与改动前一致）

- ✅ **T18.4** Git 提交 — commit `d650c10` on local

### 影响范围

| 文件 | 改动 |
|------|------|
| `channels/manager.py` | 新增 `_resolve_channel()` + `_dispatch_outbound` 使用它 |
| `agent/tools/message.py` | parameters schema 移除 channel/chat_id；execute() 忽略 LLM 传入值 |
| `tests/test_message_routing.py` | 13 项新测试 |

---

## Phase 19: Gateway 并发执行 + User Injection + Per-Session Provider

> 需求：REQUIREMENTS.md §十八 | 架构：ARCHITECTURE.md §八
> 分支：`feat/concurrent-gateway`
> 开始时间：2026-03-01

### 目标

1. 不同 session 的消息并行处理，互不阻塞
2. 同 session 执行中追加消息通过 inject 机制插入对话流
3. Provider/Model per-session 独立

### 任务清单

- ✅ **T19.1** `providers/pool.py` — Per-session provider override (commit `62ce01d`)
  - 新增 `_session_overrides: dict[str, tuple[str, str]]`
  - 新增 `get_for_session(session_key)` → `(LLMProvider, str)`
  - 新增 `switch_for_session(session_key, provider_name, model?)`
  - 新增 `clear_session_override(session_key)`
  - 单元测试

- ✅ **T19.2** Tool clone 机制 (commit `a5f4118`)
  - `agent/tools/message.py` — 新增 `clone()` 方法
  - `agent/tools/spawn.py` — 新增 `clone()` 方法
  - `agent/tools/cron.py` — 新增 `clone()` 方法
  - `agent/tools/registry.py` — 新增 `clone_for_session()` 方法
    - 共享无状态 tool 引用
    - 克隆有状态 tool（Message、Spawn、Cron）
    - 独立 audit context
  - 单元测试

- ✅ **T19.3** `agent/callbacks.py` — GatewayCallbacks (commit `fa26148`)
  - 新增 `GatewayCallbacks(DefaultCallbacks)` 类
    - `_inject_queue: asyncio.Queue[str]`
    - `check_user_input()` — 非阻塞检查 inject queue
    - `inject(text)` — 放入 inject queue
    - `on_progress()` — 转发到 bus outbound
  - 单元测试

- ✅ **T19.4** `agent/loop.py` — `_process_message()` 参数化 (commit `607bd6d`)
  - 新增 `provider`, `model`, `tools` 可选参数
  - 内部使用传入参数而非 `self.provider`/`self.model`/`self.tools`
  - `_run_agent_loop()` 同步改为使用传入的 provider/model
  - `_chat_with_retry()` 接收 provider 参数
  - `_consolidate_memory()` 使用传入的 provider/model
  - `_set_tool_context()` 操作传入的 tools 而非 `self.tools`
  - 确保 `process_direct()` 路径不受影响（fallback 到 self.*）
  - 回归测试

- ✅ **T19.5** `agent/loop.py` — 并发 Dispatcher `run()` 重构 (commit `346659e`)
  - `active_sessions: dict[str, SessionWorker]` 管理
  - 消息路由：new session → create task; active session → inject; /stop → cancel
  - `/provider` 改为 per-session switch
  - task done callback 清理 active_sessions
  - 删除 `_wait_with_stop_listener()` 和 `_active_task*` 全局指针
  - 集成测试

- ✅ **T19.6** 集成测试 + 回归测试 (commit `14b0221`)
  - 并发执行：两个 session 同时处理
  - User injection：执行中追加消息
  - Per-session provider：不同 session 不同 model
  - /stop 精确取消
  - CLI/SDK 模式回归
  - 现有测试全部通过

- ✅ **T19.7** Git 提交 + 文档更新 (merged to `local`)
  - commit to `feat/concurrent-gateway`
  - merge to `local`
  - 更新 MEMORY.md

---

## Phase 20: /session 状态查询命令 ✅

> 需求：REQUIREMENTS.md §十九 | 架构：ARCHITECTURE.md §九
> 分支：直接在 `local` 上开发（小功能）
> 完成时间：2026-03-01

### 目标

提供 `/session` 斜杠命令，让用户快速查看当前 session 的名称、执行状态、Provider/Model、消息统计等信息。

### 任务清单

- ✅ **T20.1** `agent/loop.py` — 新增 `_handle_session_command()` 方法 (commit `14e7738`)
  - 显示 session key
  - 显示执行状态：🔄 执行中 / 💤 空闲（基于 `active_sessions` 字典）
  - 显示当前 provider / model
  - 显示消息数（总数 + 未归档数）
  - 显示创建时间 / 最后更新时间
  - Gateway 并发模式：检查 `active_sessions` 中的 task 状态
  - CLI/直接调用模式：始终显示空闲（无 active_sessions）
  - 更新 `/help` 文本包含 `/session`
- ✅ **T20.2** `agent/loop.py` — `/session` 输出增加累计 Token 用量 (commit `a31eb07`)
  - 通过 `UsageRecorder.get_session_usage()` 查询 analytics.db
  - 显示 prompt / completion / total tokens + LLM 调用次数

---

