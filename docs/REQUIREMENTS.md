# nanobot 核心 — 需求文档

> 状态：**活跃** | 最后更新：2026-02-27

---

## 一、项目概述

nanobot 是一个超轻量级个人 AI 助手框架。本文档记录 `local` 分支上针对个人使用场景的需求改进，这些需求不一定会合入上游。

### 分支策略

```
main     ← 跟上游 HKUDS/nanobot 同步
local    ← 本地自定义改动（基于 main）
```

---

## 二、已完成需求

### 2.1 消息 timestamp 精确化
- **状态**: ✅ 已完成 (commit `81d4947`)
- **描述**: 消息的 timestamp 在创建时立即记录，而非批量保存时统一记录
- **影响文件**: `agent/context.py`

### 2.2 Token Usage Tracking
- **状态**: ✅ 已完成 (commits `18f39a7`, `9a10747`, `8f0cc2d`)
- **描述**: Agent loop 累计每次 LLM 调用的 token usage，通过 stderr JSON 输出
- **影响文件**: `agent/loop.py`

### 2.3 Max Iterations 消息持久化
- **状态**: ✅ 已完成 (commit `dae3b53`)
- **描述**: 达到最大迭代次数时的提示消息写入 JSONL
- **影响文件**: `agent/loop.py`

### 2.4 防止孤立 tool_result
- **状态**: ✅ 已完成 (commit `c14804d`)
- **描述**: History 窗口截断不再产生孤立的 tool_result 消息
- **影响文件**: `session/manager.py`

### 2.5 exec 工具拒绝后台命令
- **状态**: ✅ 已完成 (commit `d2a5769`)
- **描述**: 检测并拒绝含 `&` 后台操作符的 shell 命令
- **影响文件**: `agent/tools/shell.py`

### 2.6 Session 自修复 — 未完成 tool_call 链 + 错误消息清理（Phase 8）
- **状态**: ✅ 已完成
- **描述**: `get_history()` 增强为三阶段清理：(1) 开头对齐（已有）、(2) 错误消息剥离、(3) 未完成 tool_call 链移除。解决 agent 自杀/崩溃后 session 无法恢复的问题。
- **影响文件**: `session/manager.py`
- **触发场景**: Agent 通过 exec 执行 `kill` 杀掉自身进程，tool_call 已写入 JSONL 但 tool_result 永远不会写入。重启后 Anthropic API 拒绝不完整的 tool_use/tool_result 配对。

---

## 三、SDK 化改造 — Worker 直接调用 Agent（Backlog #6）

### 3.1 需求背景

当前 web-chat 的 Worker 通过 CLI 子进程调用 nanobot：

```python
# worker.py 当前方式
proc = subprocess.Popen(
    ['nanobot', 'agent', '-m', message, '--no-markdown', '-s', session_key],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ...
)
```

**存在的问题**：
1. **信息传递不便**：usage 数据只能通过 stderr JSON 传递，progress 通过 stdout 的 `↳` 前缀行传递，需要逐行解析
2. **容易出错**：stdout/stderr 混杂了日志、进度、usage 等多种数据，解析逻辑脆弱
3. **功能受限**：无法获取结构化的中间状态（如当前正在调用的工具名、参数等）
4. **进程管理复杂**：需要处理 PIPE fd 继承、后台进程卡死等问题（Backlog #5 的 exec `&` 问题就是因此产生）
5. **资源浪费**：每次调用都启动新 Python 进程，加载配置、初始化 provider

### 3.2 目标

提供一个 Python SDK，让 Worker 可以直接在进程内调用 Agent，获取结构化的回调：

```python
# worker.py 目标方式
from nanobot.sdk import AgentRunner, AgentCallbacks

class MyCallbacks(AgentCallbacks):
    def on_progress(self, text: str, tool_hint: bool = False): ...
    def on_message_saved(self, message: dict): ...
    def on_usage(self, usage: dict): ...
    def on_done(self, final_content: str): ...
    def on_error(self, error: str): ...

runner = AgentRunner.from_config()
result = await runner.run(
    message="你好",
    session_key="webchat:1234",
    callbacks=MyCallbacks(),
)
```

### 3.3 设计要求

1. **向后兼容**：CLI 调用方式继续工作，SDK 是新增的调用方式
2. **回调驱动**：所有中间状态通过回调通知（progress、tool 调用、usage 等）
3. **结构化数据**：回调参数是 Python 对象，不是需要解析的字符串
4. **与 Backlog #7 联动**：SDK 的回调机制天然支持实时 session 持久化
5. **与 Backlog #8 联动**：usage 通过回调统一输出，不再依赖 stderr

### 3.4 非目标

- 不修改 nanobot 的 gateway 命令（那是 IM channel 网关，不是 web-chat 的 gateway）
- 不修改 CLI 交互模式的行为
- 不改变 session JSONL 的存储格式

---

## 四、实时 Session 持久化（Backlog #7）

### 4.1 需求背景

当前 session 的持久化流程：

```
用户发消息 → _process_message() → _run_agent_loop() → _save_turn() → session.save()
                                    ↑                     ↑
                                    │ 可能运行数分钟       │ 只在循环结束后执行
                                    │ 中途异常 = 全部丢失  │
```

**问题**：
1. `_run_agent_loop` 可能运行数分钟（多轮工具调用），期间所有消息只在内存中
2. 如果进程异常退出（crash、kill、OOM），内存中的消息全部丢失
3. 丢失的不仅是对话记录，还有已执行的文件修改操作的上下文
4. 用户无法根据 session 记录继续之前的工作，因为记录与实际执行不一致
5. CLI 模式和 Web 模式都有此问题

### 4.2 目标

每条消息（user/assistant/tool）在产生时**立即**追加到 session JSONL 文件，而非等到整个 turn 完成后批量写入。

### 4.3 设计要求

1. **增量追加**：每条消息追加写入 JSONL，不重写整个文件
2. **原子性**：单条消息的写入是原子的（一行 JSON + flush）
3. **不影响 LLM cache**：消息列表仍然是 append-only，不影响 LLM 的 cache 效率
4. **metadata 更新**：metadata 行（第一行）在 turn 结束时更新（`last_consolidated` 等）
5. **与 SDK 联动**：SDK 的 `on_message_saved` 回调在每条消息写入后触发
6. **CLI 模式同样生效**：不仅限于 Web 模式

### 4.4 当前持久化流程（需改造）

```python
# 当前 loop.py — _process_message
history = session.get_history(...)
messages = self.context.build_messages(history, current_message, ...)
final_content, _, all_msgs = await self._run_agent_loop(messages, ...)
self._save_turn(session, all_msgs, 1 + len(history))  # ← 只在这里保存
self.sessions.save(session)                             # ← 重写整个 JSONL
```

### 4.5 目标持久化流程

```python
# 目标 — 每条消息实时写入
# 1. user 消息写入
session.append_message(user_msg)  # 立即追加到 JSONL

# 2. agent loop 中每条消息实时写入
async def _run_agent_loop(...):
    while ...:
        response = await provider.chat(...)
        assistant_msg = context.add_assistant_message(...)
        session.append_message(assistant_msg)  # 立即追加

        for tool_call in response.tool_calls:
            result = await tools.execute(...)
            tool_msg = context.add_tool_result(...)
            session.append_message(tool_msg)  # 立即追加

# 3. turn 结束后只更新 metadata
session.update_metadata()  # 更新 last_consolidated 等
```

---

## 五、统一 Token 用量记录（Backlog #8）

### 5.1 需求背景

当前 token 用量的记录方式因调用方式不同而分裂：

| 调用方式 | 用量记录 | 存储位置 |
|----------|----------|----------|
| Web UI | ✅ 有 | Worker 解析 stderr → Gateway → SQLite |
| CLI 单次 (`-m`) | ❌ 无 | stderr 输出到终端后丢弃 |
| CLI 交互模式 | ❌ 无 | stderr 输出到终端后丢弃 |
| IM channels (gateway) | ❌ 无 | 不经过 Worker |
| Cron 任务 | ❌ 无 | 不经过 Worker |

**问题**：
1. 只有 Web UI 有用量记录，其他模式完全没有
2. 用量记录依赖 Worker 解析 stderr，架构脆弱
3. 无法统计全局真实用量（CLI 用量可能占比很大）

### 5.2 目标

Token 用量在 nanobot 核心层统一记录，不依赖外部 Worker 或 stderr 解析。所有调用方式（CLI、Web、IM、Cron）都自动记录。

### 5.3 设计要求

1. **核心层记录**：usage 记录逻辑在 `agent/loop.py` 中，不在外部 Worker
2. **统一存储**：所有模式的 usage 写入同一个 SQLite 数据库
3. **回调通知**：SDK 模式下通过回调通知 usage；CLI 模式下直接写入
4. **向后兼容**：stderr JSON 输出可保留（作为调试信息），但不再是主要记录方式
5. **与 web-chat 兼容**：web-chat 的 UsageIndicator 继续工作，数据源从 Worker 传递改为直接查询 SQLite

### 5.4 当前 vs 目标数据流

**当前**（仅 Web UI 有效）：
```
agent loop → stderr JSON → Worker 解析 → SSE → Gateway → SQLite
```

**目标**（所有模式统一）：
```
agent loop → UsageRecorder.record() → SQLite (直接写入)
           → callbacks.on_usage()    → 通知调用方（可选）
```

---

## 六、三个需求的关联关系

Backlog #6、#7、#8 是高度关联的，应该作为一个整体来设计和实施：

```
┌─────────────────────────────────────────────────────┐
│              Backlog #6: SDK 化改造                   │
│                                                     │
│  提供结构化的回调机制，是 #7 和 #8 的基础设施           │
│                                                     │
│  ┌──────────────────┐   ┌──────────────────────┐    │
│  │ Backlog #7:      │   │ Backlog #8:          │    │
│  │ 实时持久化        │   │ 统一 Token 记录       │    │
│  │                  │   │                      │    │
│  │ on_message_saved │   │ on_usage             │    │
│  │ 回调 → 追加JSONL  │   │ 回调 → 写入SQLite    │    │
│  └──────────────────┘   └──────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

**实施顺序**：
1. 先做 #7（实时持久化）— 改动 session/manager.py 和 loop.py，风险最低
2. 再做 #8（统一 token 记录）— 引入 UsageRecorder，改动 loop.py
3. 最后做 #6（SDK）— 封装 AgentRunner，改动 Worker

但从架构设计角度，三者应该一起设计，确保接口一致。

---

## 七A、图片存储架构改进（Phase 15）

### 7A.1 需求背景

当前图片处理存在两个问题：

**问题 1：飞书通道图片下载路径不规范**

飞书（及 Telegram、Discord）通道下载的图片保存在 `~/.nanobot/media/`，这是一个 workspace 之外的路径，不在标准的 workspace 管理范围内。Web-chat 的上传图片则保存在 `~/.nanobot/workspace/uploads/<date>/`。路径不统一，管理混乱。

**问题 2：图片 base64 嵌入 session JSONL 导致文件膨胀**

`ContextBuilder._build_user_content()` 将图片文件读取为 base64 字符串嵌入到 user message 的 `content` 字段中（`data:image/jpeg;base64,...`）。这个 message 随后通过 `SessionManager.append_message()` 写入 session JSONL 文件。

一张 5MB 的图片 base64 编码后约 6.7MB，直接写入 JSONL。以飞书 session 为例，一次带图消息就让 JSONL 膨胀数 MB。随着图片消息增多，JSONL 文件会快速增长，影响：
- 磁盘空间浪费
- session 加载变慢（`_load()` 需要解析每行 JSON）
- `save()` 全量重写时间更长
- `get_history()` 返回的消息列表包含巨大 base64 字符串

### 7A.2 目标

1. **统一媒体存储路径**：所有通道（飞书/Telegram/Discord）的媒体文件统一保存到 `~/.nanobot/workspace/uploads/<date>/`
2. **Session JSONL 去 base64**：session 持久化时，图片内容用文件路径引用替代 base64 字符串；加载历史时按需还原为 base64

### 7A.3 设计方案

#### 7A.3.1 统一媒体存储路径

所有通道的 `media_dir` 从 `~/.nanobot/media/` 改为 `~/.nanobot/workspace/uploads/<date>/`（与 web-chat 一致）。

**影响文件**：
- `channels/feishu.py` — `_download_and_save_media()` 中的 `media_dir`
- `channels/telegram.py` — 媒体下载路径
- `channels/discord.py` — 媒体下载路径

#### 7A.3.2 Session JSONL 图片引用化

**核心思路**：session 持久化时将 `data:mime;base64,...` 替换为 `file:///path/to/image`；加载历史时将 `file:///path` 还原为 base64。

**写入时（`SessionManager.append_message` / `_prepare_entry`）**：
- 检查 message content 是否为 list 类型（多模态消息）
- 对每个 `type: "image_url"` 的 item，如果 URL 以 `data:` 开头：
  - 解码 base64 数据，保存为文件到 `workspace/uploads/<date>/<hash>.jpg`
  - 将 URL 替换为 `file:///absolute/path/to/image.jpg`
- 写入 JSONL 时只保存文件引用，不保存 base64

**读取时（`Session.get_history`）**：
- 对每个 `type: "image_url"` 的 item，如果 URL 以 `file:///` 开头：
  - 读取文件内容，编码为 base64
  - 将 URL 还原为 `data:mime;base64,...`
- 如果文件不存在，跳过该图片（graceful degradation）

**优点**：
- session JSONL 文件大小回归正常（每条图片消息只多几十字节的文件路径）
- 图片文件统一管理在 `uploads/` 目录下
- LLM 调用时仍然使用 base64（API 要求），但 session 持久化不保存
- 向后兼容：旧 session 中已有的 `data:` base64 仍然能正常加载

### 7A.4 设计要求

1. **向后兼容**：旧 session 中已有的 base64 内容不受影响，仍可正常加载
2. **文件命名**：使用内容 hash（如 MD5 前 12 位）避免重复保存相同图片
3. **MIME 类型保留**：文件引用中需保留 MIME 信息，以便还原时正确设置 `data:` 前缀
4. **错误处理**：文件不存在时 graceful degradation（跳过该图片，log warning）
5. **不影响 LLM 调用**：`build_messages()` 返回的消息列表中图片仍为 base64 格式

---

## 七、实时 Token 用量记录（Phase 4）

### 7.1 需求背景

Phase 2（统一 Token 记录）解决了"所有调用方式都能记录 usage"的问题，但记录方式仍然是**批量模式**：

```python
# 当前 _run_agent_loop 末尾
accumulated_usage = {"prompt_tokens": 0, ...}  # 内存累加

while iteration < max_iterations:
    response = await provider.chat(...)
    accumulated_usage["prompt_tokens"] += response.usage["prompt_tokens"]  # 累加到内存
    # ... 可能运行数分钟 ...

# 循环结束后才一次性写入 SQLite
if usage_recorder:
    usage_recorder.record(**accumulated_usage)  # ← 只在这里写入
```

**问题**：与 Phase 1 解决的 session 持久化问题完全一样——如果 agent 执行中途异常退出（crash/kill/OOM），`accumulated_usage` 全部丢失，SQLite 中不会有任何记录。

### 7.2 目标

每次 LLM 调用（`provider.chat()`）返回后，**立即**将该次调用的 token usage 写入 SQLite，而非累加到内存最后批量写入。

### 7.3 设计要求

1. **逐次写入**：每次 `provider.chat()` 返回后立即调用 `usage_recorder.record()`
2. **时间戳对齐**：usage 记录的时间戳与对应的 assistant 消息 timestamp 一致（取当前时间），便于通过 `session_key + timestamp` 关联
3. **单次 LLM 调用粒度**：每条 usage 记录对应一次 LLM 调用（`llm_calls=1`），不再是整个 turn 的汇总
4. **汇总保留**：stderr JSON 输出和 `callbacks.on_usage` 仍然输出整个 turn 的汇总（向后兼容 + 调试）
5. **幂等安全**：不会产生重复记录（每次 LLM 调用只写一次）

### 7.4 数据流对比

**Phase 2（当前）**：
```
LLM call 1 → 累加到内存
LLM call 2 → 累加到内存
LLM call 3 → 累加到内存
循环结束 → 一次性写入 SQLite（1 条记录）
中途崩溃 → 全部丢失 ❌
```

**Phase 4（目标）**：
```
LLM call 1 → 立即写入 SQLite（1 条记录）
LLM call 2 → 立即写入 SQLite（1 条记录）
LLM call 3 → 立即写入 SQLite（1 条记录）
循环结束 → stderr/callback 输出汇总
中途崩溃 → 已写入的记录保留 ✅
```

### 7.5 对现有系统的影响

- **analytics.db schema**：不变（每条记录仍然是 `session_key + model + tokens + timestamps`）
- **Web-chat UsageIndicator**：不变（查询的是 SUM 聚合，多条细粒度记录的 SUM 等于原来一条汇总记录）
- **Web-chat UsagePage**：不变（同理，聚合查询兼容）
- **stderr JSON 输出**：保留汇总输出（向后兼容）
- **callbacks.on_usage**：保留汇总输出（Worker 用于 SSE 通知）

---

## 八、LLM 调用详情日志（web-chat Backlog #15）

### 8.1 需求背景

当前 `analytics.db` 的 `token_usage` 表只记录了每次 LLM 调用的 **token 数量**（prompt_tokens / completion_tokens），但没有记录**具体发送了什么内容、返回了什么内容**。

统计数据显示 token 消耗巨大（450 次调用累计 5100 万 tokens，平均每次 prompt 约 11.4 万 tokens），但无法判断：
- 系统 prompt 占比多少？能否精简？
- 历史消息占比多少？memory_window 是否过大？
- 工具调用结果占比多少？truncation 阈值是否合理？
- 哪些 session 的 prompt 增长最快？是否需要更积极的 consolidation？

### 8.2 目标

每次 LLM 调用（`provider.chat()`）时，将完整的 **messages（prompt）** 和 **response** 记录到日志文件，供后续离线分析。

### 8.3 设计方案

#### 存储格式：JSONL 按天分文件

```
~/.nanobot/workspace/llm-logs/
├── 2026-02-27.jsonl
├── 2026-02-28.jsonl
└── ...
```

每行一个 JSON 对象：

```json
{
  "timestamp": "2026-02-27T01:31:32.606304",
  "session_key": "webchat:1772126509",
  "model": "claude-opus-4-6",
  "iteration": 3,
  "prompt_tokens": 23380,
  "completion_tokens": 79,
  "total_tokens": 23459,
  "messages_count": 15,
  "system_prompt_chars": 12500,
  "messages": [ ... ],
  "response": {
    "content": "...",
    "tool_calls": [ ... ],
    "finish_reason": "tool_use",
    "usage": { ... }
  }
}
```

#### 为什么选 JSONL 文件而非 SQLite BLOB

| 方案 | 优点 | 缺点 |
|------|------|------|
| JSONL 按天分文件 | 简单、可 grep、可压缩、不影响 analytics.db 性能 | 需要额外脚本做关联查询 |
| SQLite BLOB | 查询方便 | DB 膨胀严重（预估 ~27MB/天），影响读取性能 |
| 每次调用一个文件 | 最灵活 | 文件数量爆炸 |

#### 存储量估算

- 平均单次 messages JSON: ~550KB
- 每天约 50 次调用: ~27MB/天（不压缩）
- 可选：旧日志自动 gzip 压缩

#### SQLite 关联

`token_usage` 表新增 `detail_file` 和 `detail_line` 字段（可选），指向 JSONL 文件中的具体行号，方便从汇总数据追溯到详情。

### 8.4 设计要求

1. **默认开启**：每次 LLM 调用自动记录，无需手动配置
2. **不影响性能**：异步/非阻塞写入，不拖慢 agent loop
3. **按天分文件**：便于管理和清理
4. **包含完整 messages**：系统 prompt + 历史 + 当前消息 + 工具结果
5. **包含完整 response**：content + tool_calls + finish_reason + usage
6. **额外统计字段**：messages_count、system_prompt_chars（方便快速分析不用解析完整 JSON）
7. **向后兼容**：不修改现有 `token_usage` 表 schema（新增字段用 ALTER TABLE，兼容旧数据）

### 8.5 实现位置

修改 `nanobot/agent/loop.py` 的 `_run_agent_loop` 方法，在每次 `provider.chat()` 返回后、usage 记录之后，将 messages + response 写入 JSONL。

可封装为 `nanobot/usage/detail_logger.py` 独立模块。

### 8.6 非目标

- 不提供 Web UI 查看详情的功能（后续可做）
- 不自动分析/报告（后续可做分析脚本）
- 不压缩当天日志（只压缩历史日志，后续可做）

---

## 九、文件访问审计日志（Phase 7）

### 9.1 需求背景

nanobot 作为 AI 助手，拥有对文件系统的完整读写权限。Agent 可以通过多种工具对文件进行操作：

| 工具 | 文件操作类型 | 风险等级 |
|------|------------|---------|
| `read_file` | 读取文件内容 | 低（信息泄露） |
| `write_file` | 创建/覆盖文件 | 高（数据破坏） |
| `edit_file` | 修改文件内容 | 高（数据篡改） |
| `list_dir` | 列出目录内容 | 低（信息探测） |
| `exec` | 间接文件操作（cat/cp/mv/rm/tee/重定向等） | 高（任意操作） |
| MCP 工具 | 取决于具体 MCP 服务 | 不确定 |

当前缺乏统一的审计机制：
1. **无法事后追溯**：如果 agent 执行了意外的文件操作（误删、覆盖重要文件），无法快速定位何时、哪个 session 执行了什么操作
2. **无法安全分析**：无法统计 agent 的文件访问模式，识别异常行为
3. **无法合规审查**：对于敏感文件（如 SSH 密钥、配置文件、密码文件），无法追踪访问记录

### 9.2 目标

为所有涉及文件读写的工具调用增加审计日志，记录完整的操作上下文，供事后分析和安全审查。

### 9.3 审计范围

#### 9.3.1 直接文件操作工具（必须审计）

| 工具 | 审计内容 |
|------|---------|
| `read_file` | 路径、文件大小、成功/失败、错误信息 |
| `write_file` | 路径、写入字节数、是否新建文件、成功/失败 |
| `edit_file` | 路径、old_text 摘要（前80字符）、new_text 摘要（前80字符）、成功/失败 |
| `list_dir` | 路径、条目数、成功/失败 |

#### 9.3.2 间接文件操作（必须审计）

| 工具 | 审计内容 |
|------|---------|
| `exec` | 完整命令、工作目录、退出码、是否被安全守卫拦截 |

#### 9.3.3 其他工具（可选审计，为完整性记录）

| 工具 | 审计内容 |
|------|---------|
| `web_fetch` | URL、状态码（不涉及本地文件，但记录网络数据获取） |
| `web_search` | 搜索查询（不涉及文件，但记录信息获取行为） |
| `spawn` | 任务描述（子 agent 的具体操作由其自身审计） |
| `cron` | action + 配置（定时任务可能触发文件操作） |
| `message` | 目标渠道 + chat_id（不涉及文件，但记录信息输出） |
| MCP 工具 | 工具名 + 参数摘要 |

### 9.4 设计方案

#### 9.4.1 审计日志格式

采用 **JSONL 按天分文件**，与 LLM 详情日志（Phase 6）保持一致的存储策略：

```
~/.nanobot/workspace/audit-logs/
├── 2026-02-27.jsonl
├── 2026-02-28.jsonl
└── ...
```

每行一个 JSON 对象：

```json
{
  "timestamp": "2026-02-27T12:30:45.123456",
  "session_key": "webchat:1772126509",
  "channel": "feishu",
  "chat_id": "ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "tool": "write_file",
  "action": "write",
  "params": {
    "path": "/Users/zhangxingcheng/.nanobot/workspace/memory/MEMORY.md"
  },
  "result": {
    "success": true,
    "bytes_written": 1234,
    "is_new_file": false
  },
  "resolved_path": "/Users/zhangxingcheng/.nanobot/workspace/memory/MEMORY.md",
  "error": null
}
```

#### 9.4.2 实现架构 — ToolRegistry 拦截层

在 `ToolRegistry.execute()` 方法中统一拦截所有工具调用，在工具执行前后记录审计日志。这样：
- **零侵入**：不需要修改任何具体工具的代码
- **全覆盖**：所有注册的工具（包括 MCP 工具）自动被审计
- **统一格式**：所有审计记录格式一致

```python
class ToolRegistry:
    async def execute(self, name: str, params: dict) -> str:
        # 1. 工具执行前：记录请求
        # 2. 执行工具
        result = await tool.execute(**params)
        # 3. 工具执行后：记录结果（成功/失败）
        # 4. 写入审计日志
        return result
```

#### 9.4.3 审计日志模块

新增 `nanobot/audit/logger.py`：

```python
class AuditLogger:
    """文件访问审计日志记录器。"""
    
    def __init__(self, log_dir: Path | None = None, enabled: bool = True):
        ...
    
    def log(self, entry: AuditEntry) -> None:
        """写入一条审计日志。"""
        ...
```

#### 9.4.4 审计上下文传递

ToolRegistry 需要知道当前的 session_key、channel、chat_id 等上下文信息。通过在 `_process_message()` 中设置 ToolRegistry 的审计上下文来实现：

```python
self.tools.set_audit_context(session_key=key, channel=msg.channel, chat_id=msg.chat_id)
```

### 9.5 设计要求

1. **全面覆盖**：所有通过 ToolRegistry 执行的工具调用都被审计（包括 MCP 工具）
2. **零侵入**：不修改具体工具类的代码，在 ToolRegistry 层统一拦截
3. **低开销**：审计日志写入是同步 append 操作，不阻塞工具执行
4. **可追溯**：每条记录包含完整上下文（session、channel、时间、路径、结果）
5. **可分析**：JSONL 格式便于 grep/jq 查询和统计分析
6. **可配置**：支持 enabled=False 禁用（默认开启）
7. **按天分文件**：便于管理和清理

### 9.6 非目标

- 不提供实时告警功能（后续可做）
- 不提供 Web UI 查看审计日志的功能（后续可做）
- 不对审计日志做加密或签名（个人使用场景，不需要防篡改）

### 9.7 存储量估算

- 平均每条审计记录：~500 字节
- 每天约 200-500 次工具调用：~100-250KB/天
- 远小于 LLM 详情日志（~27MB/天），存储压力很小

---

## 十、多飞书租户支持（Phase 9）

### 10.1 需求背景

用户有多个飞书租户（不同公司/组织），希望 nanobot 的 gateway 能同时接入这些租户，每个租户对应一个独立的飞书机器人。

当前限制：
- `config.json` 的 `channels.feishu` 只支持**一套** `appId/appSecret`
- `ChannelManager` 只创建**一个** `FeishuChannel` 实例
- 无法同时连接多个飞书机器人

### 10.2 目标

支持在 config.json 中配置**多个飞书租户**，每个租户有独立的 appId/appSecret，ChannelManager 为每个租户创建独立的 FeishuChannel 实例，各实例的 session 互不干扰。

### 10.3 Config 格式设计

**向后兼容**：支持旧格式（单个对象）和新格式（数组）。

旧格式（继续支持）：
```json
{
  "channels": {
    "feishu": {
      "enabled": true,
      "appId": "cli_a92bafd6...",
      "appSecret": "5FqTGicr3X..."
    }
  }
}
```

新格式（数组，多租户）：
```json
{
  "channels": {
    "feishu": [
      {
        "name": "personal",
        "enabled": true,
        "appId": "cli_a92bafd6...",
        "appSecret": "5FqTGicr3X...",
        "allowFrom": []
      },
      {
        "name": "company-x",
        "enabled": true,
        "appId": "cli_b1234567...",
        "appSecret": "xxxxxx...",
        "allowFrom": []
      }
    ]
  }
}
```

### 10.4 Session 隔离

- 单租户（旧格式/无 name）：session_key = `feishu:{chat_id}`（不变）
- 多租户（有 name）：session_key = `feishu.{name}:{chat_id}`
  - 例如 `feishu.personal:ou_abc123`、`feishu.company-x:ou_def456`
- 不同租户的 `open_id` 可能重复，通过 name 前缀区分

### 10.5 Outbound 路由

- 每个 FeishuChannel 实例注册到 ChannelManager 时使用唯一 key：
  - 单租户：`feishu`
  - 多租户：`feishu.personal`、`feishu.company-x`
- InboundMessage 的 `channel` 字段设为该实例的 key（如 `feishu.personal`）
- OutboundMessage 的 `channel` 字段匹配 → 精确路由到正确的 FeishuChannel 实例

### 10.6 设计要求

1. **向后兼容**：单个 FeishuConfig 对象继续工作，行为不变
2. **独立实例**：每个租户有独立的 FeishuChannel（独立的 lark client、ws 连接、dedup cache）
3. **Session 隔离**：不同租户的 session 通过 channel name 前缀区分
4. **Outbound 精确路由**：回复消息路由到正确的租户实例
5. **日志区分**：每个实例的日志包含租户 name，便于排查
6. **通用设计**：改动方式对其他 channel 也适用（如未来多 Telegram bot）

### 10.7 影响范围

| 文件 | 改动 |
|------|------|
| `config/schema.py` | FeishuConfig 新增 `name` 字段；ChannelsConfig.feishu 类型改为 `FeishuConfig \| list[FeishuConfig]` |
| `channels/manager.py` | `_init_channels()` 飞书部分支持列表，创建多个实例 |
| `channels/feishu.py` | FeishuChannel.name 支持自定义（如 `feishu.personal`） |
| `channels/base.py` | 可能无需改动（name 已是实例属性） |
| `config.json` | 用户按需改为数组格式 |

### 10.8 非目标

- 不为其他 channel 做多实例支持（本次只做飞书，但设计上不阻碍扩展）
- 不做跨租户消息转发
- 不做租户级别的权限隔离（所有租户共享同一 workspace/memory）

---

## 十一、media 参数支持（Phase 10）

### 11.1 需求背景

web-chat 需要支持用户发送图片，利用 Claude 多模态能力理解图片内容。当前 `process_direct()` 和 `AgentRunner.run()` 不支持传入媒体附件。

### 11.2 目标

为 `process_direct()` 和 `AgentRunner.run()` 增加 `media: list[str] | None` 参数，透传给 `_build_user_content()`，使 Agent 能处理带图片的用户消息。

### 11.3 设计要求

1. **透传**: `media` 参数从 SDK 层 → `process_direct()` → `InboundMessage` → `_build_user_content()`
2. **向后兼容**: 默认 `media=None`，不影响现有调用
3. **不修改 `_build_user_content`**: 该方法已有 media 处理逻辑（base64 编码图片为 `image_url` content block）

### 11.4 影响文件

| 文件 | 改动 |
|------|------|
| `agent/loop.py` | `process_direct()` 新增 `media` 参数，传入 `InboundMessage` |
| `sdk/runner.py` | `AgentRunner.run()` 新增 `media` 参数，透传给 `process_direct()` |

---

## 十二、LLM API 速率限制重试机制（Phase 11）

### 12.1 需求背景

近期偶尔出现 Anthropic API 速率限制错误，导致 agent 任务直接中断：

```
Error calling LLM: litellm.RateLimitError: AnthropicException -
{"error":{"type":"<nil>","message":"This request would exceed your organization's
rate limit of 400,000 output tokens per minute..."}}
```

这类错误是暂时性的（等待一段时间后限额恢复即可重试），但当前代码中 `provider.chat()` 调用没有重试机制，一旦触发就直接抛异常导致整个任务失败。

### 12.2 目标

在 `_run_agent_loop` 中为 `provider.chat()` 调用增加指数退避重试机制，自动处理速率限制和暂时性错误，避免任务因偶发限流而中断。

### 12.3 设计要求

1. **可重试错误类型**：
   - `RateLimitError` / HTTP 429 — API 速率限制
   - `APIConnectionError` / HTTP 5xx — 暂时性服务端错误
   - `APITimeoutError` — 请求超时
2. **指数退避**：初始等待 10 秒，每次翻倍（10s → 20s → 40s → 80s → 160s）
3. **最大重试次数**：5 次
4. **进度通知**：重试时通过 `on_progress` 通知用户
5. **日志记录**：每次重试记录 warning 日志
6. **最终失败**：超过最大重试次数后仍抛出原始异常
7. **不影响其他错误**：非暂时性错误（如 AuthenticationError、InvalidRequestError）不重试，立即抛出

### 12.4 影响文件

| 文件 | 改动 |
|------|------|
| `agent/loop.py` | 新增 `_chat_with_retry()` 和 `_is_retryable()` 方法 |

### 12.5 非目标

- 不修改 provider 层代码（重试在 agent loop 层处理）
- 不做全局速率限制器
- 不做请求队列或并发控制

---

## 十三、/new 命令重构 — 新建 Session（Phase 12）

### 13.1 需求背景

当前 `/new` 命令的行为是：归档当前 session 的记忆到 MEMORY.md/HISTORY.md，然后清空 session 消息。这实际上是一个"刷新/归档"操作，而非"新建"操作。

用户希望 `/new` 的语义更直观——**创建一个全新的 session，后续对话不带之前的记录**。原来的归档行为改名为 `/flush`。

### 13.2 目标

1. **`/flush`**：原 `/new` 的行为——归档当前 session 记忆，清空消息
2. **`/new`**：创建新 session，后续对话使用新 session，不带之前的历史

### 13.3 各通道行为

| 通道 | `/flush` 行为 | `/new` 行为 |
|------|-------------|------------|
| Gateway（飞书/Telegram 等） | 归档当前 session 记忆，清空消息（原 `/new`） | 为当前 chat_id 创建新 session（新 session_key），后续消息路由到新 session |
| CLI | 归档当前 session 记忆，清空消息 | 将当前 `cli_direct.jsonl` 改名为带时间戳的归档文件，创建新的空 `cli_direct.jsonl` |
| Web UI | 归档当前 session 记忆，清空消息 | 创建新 session 并切换到新 session（前端调用 createSession API） |

### 13.4 Gateway `/new` 的设计

Gateway 通道（飞书、Telegram 等）的 session_key 由 `channel:chat_id` 决定。`/new` 需要改变 session_key 的映射：

- 当前 session_key: `feishu:ou_abc123`
- `/new` 后新 session_key: `feishu:ou_abc123_1740000000`（追加时间戳后缀）
- 需要维护一个 `chat_id → current_session_key` 的映射表
- 映射关系持久化到 `~/.nanobot/workspace/sessions/_routing.json`

### 13.5 CLI `/new` 的设计

- 当前文件: `sessions/cli_direct.jsonl`
- `/new` 后: 将 `cli_direct.jsonl` 改名为 `cli_direct_1740000000.jsonl`，创建新的空 `cli_direct.jsonl`
- 内存中的 session 缓存也需要失效重建

### 13.6 Web UI `/new` 的设计

Web UI 已有 createSession API，`/new` 命令在前端拦截：
- 调用 `createSession()` API 创建新 session
- 切换到新 session
- 不需要后端 agent loop 参与

### 13.7 设计要求

1. **`/flush` 完全等同于原 `/new`**：行为不变，只是命令名改了
2. **`/new` 是轻量操作**：不做记忆归档，只创建新 session
3. **Gateway 路由持久化**：重启后仍使用最新的 session_key 映射
4. **CLI 归档文件保留**：旧 session 文件改名保留，不删除
5. **`/help` 更新**：显示新的命令列表
6. **向后兼容**：不影响现有 session 文件格式

### 13.8 影响范围

| 文件 | 改动 |
|------|------|
| `agent/loop.py` | `/new` → `/flush` 重命名；新增 `/new` 处理逻辑（gateway + CLI） |
| `session/manager.py` | 新增 `archive_session()` 方法（CLI 改名文件）；新增 session 路由映射 |
| web-chat 前端 `messageStore.ts` | `/new` 前端拦截改为创建新 session + 切换 |

### 13.9 非目标

- 不改变 session JSONL 文件格式
- 不做 session 合并功能
- 不做 session 列表中的"归档"标记（后续可做）

---

---

## 十四、大图片自动压缩（Phase 14）

### 14.1 需求背景

LLM API（如 Anthropic Claude）对图片大小有限制，超过 5MB 的图片会被 API 拒绝。用户通过 IM（飞书/Telegram）或 Web UI 发送的高清照片经常超过此限制。

### 14.2 目标

在图片传给 LLM API 之前，自动检测文件大小，超过 5MB 的图片进行压缩（缩小尺寸 + 降低 JPEG 质量），确保不超过 API 限制。

### 14.3 设计方案

在 `ContextBuilder._build_user_content()` 中，读取图片文件后检查大小：
- **≤ ~3.75 MB**: 直接 base64 编码，原样传给 LLM
- **> ~3.75 MB**: 调用 `_compress_image()` 压缩后再 base64 编码

压缩策略（`_compress_image()`）：
1. **缩小尺寸**: 最长边超过 2048px 时等比缩放到 2048px
2. **降低质量**: 从 JPEG quality=85 开始，每次 -10，直到文件大小 ≤ ~3.75MB 或 quality=30
3. **格式转换**: RGBA/P/LA 等模式转为 RGB（JPEG 不支持透明通道）
4. **优雅降级**: Pillow 未安装时 log warning，原样发送

### 14.4 设计要求

1. **统一入口**: 压缩在 `_build_user_content()` 中处理，所有通道（飞书/Telegram/Web）统一生效
2. **阈值可配**: `IMAGE_MAX_BYTES` 类常量，默认 ~3.75 MB，base64后 5MB
3. **无损小图**: 小于阈值的图片不做任何处理
4. **日志记录**: 压缩时记录原始大小、压缩后大小、quality、尺寸变化
5. **新增依赖**: Pillow>=10.0.0,<12.0.0 加入 pyproject.toml

### 14.5 影响范围

| 文件 | 改动 |
|------|------|
| `agent/context.py` | `_build_user_content()` 增加大小检查 + `_compress_image()` 静态方法 |
| `pyproject.toml` | 新增 Pillow 依赖 |
| `tests/test_image_compress.py` | 8 项测试（压缩、格式转换、缩放、集成） |

### 14.6 非目标

- 不修改图片上传/下载流程（飞书/Web 端的图片获取逻辑不变）
- 不做图片格式转换优化（如 WebP 转换）
- 不做可配置的压缩参数（当前硬编码，后续可扩展）

---

## 十五、ProviderPool — 运行时 Provider 动态切换（Phase 16）

### 15.1 需求背景

Agent 单次任务的 token 消耗量大（平均每次 prompt 约 11.4 万 tokens），需要根据任务难度灵活切换不同 API 源以控制成本。例如：简单问答用低价模型，复杂编码任务用高端模型。

当前只能通过**修改 config.json + 重启**来切换 provider，无法在运行时动态切换。这意味着：
- 切换成本高（需要停服、编辑配置、重启）
- 无法按任务粒度选择模型
- 多 channel 共享同一 provider，无法独立调整

### 15.2 目标

1. **config.json 新增 `anthropic_proxy` provider 配置槽位**：支持通过代理访问 Anthropic API 的备用源
2. **引入 ProviderPool 运行时管理多个 provider 实例**：支持动态切换 active provider + model，无需修改配置文件或重启
3. **新增 `/provider` 斜杠命令**：全 channel 可用（webchat 前端、命令行、gateway），用于查看和切换当前 provider
4. **不修改 config.json 来切换**：config 只声明可用 API 源池，切换是纯运行时状态
5. **各 channel 独立**：webchat worker、gateway、命令行各自维护独立的 ProviderPool 状态，互不影响
6. **任务执行中禁止切换**：agent loop 正在运行时，`/provider` 命令拒绝切换并提示用户等待

### 15.3 设计要求

#### 15.3.1 ProviderPool 实现 LLMProvider 接口

ProviderPool 对外暴露与 LLMProvider 相同的接口，AgentLoop 无需感知底层是单个 provider 还是 pool。AgentLoop 调用 `provider.chat()` 时，ProviderPool 自动路由到当前 active provider。

#### 15.3.2 Pool.chat() 忽略调用方传入的 model

`Pool.chat()` 始终使用 active_model，忽略调用方（AgentLoop）传入的 model 参数。这确保切换 provider 后模型也同步切换，不会出现"用 proxy provider 但仍请求原模型"的不一致状态。

#### 15.3.3 Provider Entry 结构

每个 provider entry 是一个 `(provider_instance, default_model)` 二元组：
- `provider_instance`：实现 LLMProvider 接口的 provider 实例
- `default_model`：该 provider 的默认模型名称（来自 config）

#### 15.3.4 switch(provider, model?) 方法

- `switch(provider_name)` — 切换到指定 provider，使用该 provider 的 default_model
- `switch(provider_name, model)` — 切换到指定 provider，并覆盖使用指定 model
- 切换失败（provider_name 不存在）时抛出 ValueError

#### 15.3.5 available 属性

`available` 属性返回所有可用 provider 及其默认 model，格式为 `dict[str, str]`（`{provider_name: default_model}`）。

#### 15.3.6 `/provider` 斜杠命令

- **无参数**（`/provider`）：显示当前状态，包括 active provider + model、所有可用 provider 列表
- **有参数**（`/provider anthropic_proxy` 或 `/provider anthropic_proxy claude-3-haiku-20240307`）：切换到指定 provider（+ 可选 model）
- 切换成功后显示确认信息
- 切换失败（不存在的 provider / 任务执行中）显示错误信息

### 15.4 影响范围

| 文件 | 改动 |
|------|------|
| `providers/registry.py` | 新增 `anthropic_proxy` ProviderSpec，注册代理模式的 Anthropic provider |
| `config/schema.py` | `ProvidersConfig` 增加 `anthropic_proxy` 可选字段（api_key、base_url、model） |
| `providers/pool.py` | **新建** ProviderPool 类，实现 LLMProvider 接口，管理多 provider 实例的动态切换 |
| `providers/__init__.py` | 导出 ProviderPool |
| `cli/commands.py` | `_make_provider` 构建 ProviderPool（从 config 中读取所有已配置的 provider，组装为 pool） |
| `agent/loop.py` | 新增 `/provider` 斜杠命令处理逻辑 |

### 15.5 非目标

- 不做自动切换（如根据 token 用量自动降级）— 切换完全由用户手动触发
- 不做 provider 健康检查或故障转移
- 不持久化切换状态 — 重启后恢复为 config 中的默认 provider
- 不修改现有 provider 实现（Anthropic、OpenAI 等）

---

## 十六、飞书合并转发消息解析（Phase 17）

### 16.1 需求背景

用户在飞书中将其他群/会话的聊天记录通过「合并转发」发送给 nanobot 机器人时，当前代码只显示 `[merged forward messages]` 占位文本，无法获取转发的实际消息内容。

飞书合并转发消息的 `msg_type` 为 `merge_forward`，其 `content` 字段包含子消息的 ID 列表（`message_id_list` 数组），但不包含子消息的实际内容。需要通过飞书 Open API 逐条拉取子消息详情。

### 16.2 目标

解析 `merge_forward` 消息的 `content`，提取子消息 ID 列表，调用飞书 `GET /open-apis/im/v1/messages/{message_id}` API 逐条获取原始消息内容（文本/富文本/图片等），拼接为可读的文本格式传给 Agent。

### 16.3 设计方案

1. **content 结构**：`merge_forward` 的 content JSON 中包含 `message_id_list`（原消息 ID 数组）
2. **API 调用**：使用 `lark_oapi` SDK 的 `GetMessageRequest` 拉取单条消息详情
3. **子消息解析**：根据 `msg_type` 分别处理 — text / post / image / file / audio / media / interactive / system / share_chat / share_user 等
4. **格式化输出**：子消息拼接后用 `--- forwarded messages ---` / `--- end forwarded messages ---` 包裹
5. **媒体下载**：图片/文件/音频类子消息通过 `_download_and_save_media()` 下载，路径加入 `media_paths`
6. **嵌套处理**：嵌套 merge_forward 不递归，标记为 `[nested merged forward messages]`

### 16.4 设计要求

1. **`_get_message_detail_sync(message_id)`** — 同步方法，在 executor 中调用
   - 返回 `{msg_type, content, sender_id, create_time, message_id}` 或 `None`
   - API 失败、空响应、无效 JSON、异常均返回 `None` 并记录 warning
2. **`_resolve_merge_forward(content_json)`** — 异步方法
   - 提取 `message_id_list`，逐条调用 `_get_message_detail_sync()`
   - 跳过空 message_id
   - 返回 `(text, media_paths)` 元组
3. **`_on_message()`** — merge_forward 从联合分支中拆出，单独调用 `_resolve_merge_forward()`
4. **权限要求**：需要 `im:message` 或 `im:message:readonly` 权限；机器人需有权访问原会话
5. **Graceful degradation**：API 失败的子消息显示 `[message {id}: failed to fetch]`，不阻断其余消息

### 16.5 影响范围

| 文件 | 改动 |
|------|------|
| `channels/feishu.py` | import `GetMessageRequest` + `_get_message_detail_sync()` + `_resolve_merge_forward()` + `_on_message()` merge_forward 分支 |
| `tests/test_merge_forward.py` | 18 项新测试 |

### 16.6 非目标

- 不做子消息并发拉取（当前逐条顺序拉取，避免触发 API 限流）
- 不解析嵌套 merge_forward 的子消息（避免无限递归）
- 不持久化子消息内容缓存
- 不做发送者名称解析（当前只记录 sender_id，不调用用户信息 API）

---

## 十七、飞书通道文件附件发送修复（Phase 18）

> 来源：手动 backlog（2026-03-01）

### 问题描述

通过 `message` 工具的 `media` 参数向飞书用户发送文件附件（docx 等），飞书端未收到。文本消息正常送达，但附件丢失。中文/英文文件名均无效。3 次尝试均失败。

### 根因分析

1. **channel 名称不匹配**：多租户飞书 channel 注册名为 `feishu.ST` / `feishu.lab`，但 LLM 在 `message` 工具调用中传入 `channel: "feishu"`（不含租户后缀）
2. **`_dispatch_outbound` 精确匹配**：`self.channels.get("feishu")` 返回 None → 消息被 log warning 后丢弃
3. **`MessageTool.execute()` 误报成功**：`bus.publish_outbound()` 只是入队，不等待实际发送，返回 "Message sent..." 给 LLM
4. **LLM 不应覆盖 channel**：`MessageTool` 已有 `_default_channel`（由 `_set_tool_context` 设置为正确的 `feishu.lab`），但 LLM 显式传入 `channel: "feishu"` 覆盖了默认值

### 修复方案

#### F18.1 `_dispatch_outbound` channel 名称容错
- 精确匹配失败时，尝试前缀匹配（如 `"feishu"` 匹配 `"feishu.lab"` 或 `"feishu.ST"`）
- 如果前缀匹配到**唯一**结果，使用该 channel
- 如果匹配到**多个**结果，log warning 并丢弃（歧义）
- 添加 debug 日志记录 channel 路由结果

#### F18.2 `MessageTool` 参数保护
- 当 LLM 传入的 `channel` 不在已注册 channel 列表中时，回退到 `_default_channel`
- 或者：从 `message` 工具的 parameters schema 中**移除** `channel` 和 `chat_id` 参数，让 LLM 只能发送到当前会话（绝大多数场景）
- 保留 `media` 参数

#### F18.3 `MessageTool.execute()` 返回值改进
- 返回信息中包含实际使用的 channel 名称，便于调试
- 考虑：是否需要等待实际发送结果？（当前架构为 fire-and-forget）

### 不做什么

- 不改变 `OutboundMessage` 的数据结构
- 不影响 `_process_message` 最终响应的发送逻辑
- 不改变飞书 `send()` / `_upload_file_sync()` 的实现（已验证正确）

---

## 十八、Gateway 并发执行 + User Injection + Per-Session Provider（Phase 19）

### 18.1 需求背景

当前 `AgentLoop.run()` 是**严格串行**的——所有 channel（feishu.lab、feishu.ST、telegram、webchat 等）的消息进入**同一个 `MessageBus.inbound` 队列**，且每次只处理一条消息，处理完才取下一条。

**核心问题**：
1. **跨 session 阻塞**：feishu.lab 执行长任务时，feishu.ST 的消息排队等待，反之亦然
2. **同 session 追加消息无法 inject**：执行中再发消息只能放回队列等待，无法插入当前对话流
3. **Provider/Model 全局共享**：`self.provider` 和 `self.model` 是 AgentLoop 实例级变量，所有 session 共享。一个 session 切了 provider，影响所有 session

### 18.2 目标

1. **Per-session 并发**：不同 session 的消息可以**并行处理**，同一 session 内仍然串行（保证对话一致性）
2. **User Injection**：执行中收到同 session 的新消息时，在下一轮工具调用开始前插入到对话消息中（复用现有 `callbacks.check_user_input()` 机制）
3. **Per-session Provider/Model**：每个 session 可以独立选择 provider 和 model，`/provider` 命令只影响当前 session

### 18.3 设计要求

#### 18.3.1 并发 Dispatcher

`AgentLoop.run()` 重构为并发 dispatcher 模式：

```
bus.inbound → dispatcher → {
    session_key_1: worker_task_1 (running),
    session_key_2: worker_task_2 (running),
    session_key_3: (idle, no task)
}
```

- 维护 `active_sessions: dict[str, SessionWorker]`，key 是 session_key
- 新消息到来时：
  - 如果该 session 无 active task → 启动新 task
  - 如果该 session 有 active task → inject 到该 task
  - `/stop` → 取消对应 session 的 task
- task 完成后自动从 `active_sessions` 中移除

#### 18.3.2 SessionWorker 结构

```python
@dataclass
class SessionWorker:
    task: asyncio.Task
    callbacks: GatewayCallbacks
    session_key: str
    provider_name: str  # per-session provider
    model: str          # per-session model
```

#### 18.3.3 GatewayCallbacks

```python
class GatewayCallbacks(DefaultCallbacks):
    """Per-session callbacks for gateway mode, with inject queue."""
    def __init__(self):
        self._inject_queue: asyncio.Queue[str] = asyncio.Queue()
    
    async def check_user_input(self) -> str | None:
        try:
            return self._inject_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None
    
    async def inject(self, text: str, media: list[str] | None = None):
        # 将文本放入 inject queue，等待 _run_agent_loop 在下一轮工具调用后检查
        await self._inject_queue.put(text)
```

#### 18.3.4 Per-Session Provider/Model

- `ProviderPool` 新增 `_session_overrides: dict[str, tuple[str, str]]`（session_key → (provider_name, model)）
- `get_for_session(session_key)` 方法：优先返回 session override，否则返回全局 active
- `switch_for_session(session_key, provider_name, model?)` 方法：设置 per-session override
- `/provider` 命令改为调用 `switch_for_session()` 而非全局 `switch()`
- `_process_message` 中使用 `get_for_session()` 获取当前 session 的 provider/model

#### 18.3.5 Tool Context 并发安全

当前 `_set_tool_context()` 修改 `MessageTool`、`SpawnTool`、`CronTool` 的**实例变量**，并发时互相覆盖。

**方案**：每个并发 task 创建独立的 tool context，不修改共享的 tool 实例。

具体实现：
- `_process_message()` 接收 `provider` 和 `model` 参数（而非使用 `self.provider`、`self.model`）
- Tool context 通过 `contextvars.ContextVar` 或参数传递，避免修改实例变量
- `ToolRegistry` 支持 per-task audit context（而非实例级 `_audit_context`）

**最简方案（推荐）**：
- 为每个并发 task 创建**独立的 ToolRegistry 浅拷贝**，其中状态相关的 tool（MessageTool、SpawnTool、CronTool）创建新实例
- 无状态 tool（ReadFile、WriteFile、Exec 等）共享原始实例（它们是线程/协程安全的）
- 这样 `_set_tool_context()` 只影响当前 task 的 ToolRegistry 副本

#### 18.3.6 /stop 精确取消

- `/stop` 根据 `msg.channel + msg.chat_id` 解析 session_key，精确取消对应 session 的 task
- 不再依赖 `self._active_task`（全局单一 task 指针），改为查 `active_sessions`

### 18.4 影响范围

| 文件 | 改动 | 描述 |
|------|------|------|
| `agent/loop.py` | 重构 `run()` | 并发 dispatcher + SessionWorker + GatewayCallbacks |
| `agent/loop.py` | 重构 `_process_message()` | 接收 provider/model 参数，不用 self.provider/model |
| `agent/loop.py` | 重构 `_wait_with_stop_listener()` | 删除（不再需要，dispatcher 直接处理） |
| `agent/loop.py` | 重构 `_handle_stop()` | 从 active_sessions 查找并取消 |
| `agent/loop.py` | 重构 `_handle_provider_command()` | 改为 per-session switch |
| `agent/tools/message.py` | 并发安全 | 支持实例克隆或 context 参数化 |
| `agent/tools/spawn.py` | 并发安全 | 同上 |
| `agent/tools/cron.py` | 并发安全 | 同上 |
| `agent/tools/registry.py` | 并发安全 | 支持浅拷贝 + per-task audit context |
| `providers/pool.py` | per-session override | 新增 session override 机制 |

### 18.5 不做什么

- **不改变 `process_direct()` 和 SDK 调用方式**：SDK/CLI 模式不经过 `run()`，不受影响
- **不做 per-session 最大并发限制**：当前场景 session 数有限（2-3 个飞书租户），不需要限流
- **不做 session 优先级**：所有 session 平等
- **不改变 MessageBus 接口**：仍然是单一 inbound queue，dispatcher 在消费端分流
- **不做 inject 的 media 支持**：inject 只支持文本（图片等复杂内容作为新 turn 处理）
- **不持久化 per-session provider override**：重启后恢复为全局默认

### 18.6 测试设计

1. **并发执行测试**：两个 session 同时处理消息，互不阻塞
2. **User Injection 测试**：执行中发送新消息，验证 inject 到对话流
3. **Per-session Provider 测试**：两个 session 使用不同 provider
4. **`/stop` 精确取消测试**：只取消目标 session 的 task
5. **Tool context 隔离测试**：并发 task 的 MessageTool 不互相覆盖
6. **回归测试**：CLI/SDK 模式不受影响

---

## 二十、/new 归档方向反转 + Session 命名简化（Phase 21）

### 20.1 需求背景

当前 `/new` 命令的归档方向有问题：

**现状**：
```
/new 执行时:
1. 旧文件 rename: feishu.lab_ou_xxx.jsonl → feishu.lab_ou_xxx_1772366290.jsonl
2. 新文件创建: feishu.lab_ou_xxx.jsonl (空)
3. 新 session.key = "feishu.lab:ou_xxx" (不变!)
```

**问题**：
- `analytics.db` 的 `token_usage` 按 `session_key` 记录，`/new` 后 session_key 不变
- 导致 Usage 统计是**所有历史 session 的累加**，而非当前 session 的用量
- `/session` 命令显示的 Token 用量与当前 session 实际消息数不匹配

同时，当前飞书通道的 session 文件名过长（包含完整的 `ou_xxx` open_id），不够简洁。

### 20.2 目标

1. **反转归档方向**：`/new` 时旧文件保持原名不动，新文件使用带时间戳的新 key，这样旧文件的 session_key 与数据库记录天然对应
2. **Session 命名简化**：新 session 使用 `{channel}.{timestamp}` 格式（如 `feishu.lab.1709312640`），去掉冗长的 `chat_id`

### 20.3 设计方案

#### 20.3.1 反转归档方向

```
/new 执行时（新行为）:
1. 旧文件不动: feishu.lab_ou_xxx.jsonl 保持原样（key 和 usage 对应）
2. 新文件创建: feishu.lab.1709312640.jsonl（新 key）
3. 新 session.key = "feishu.lab.1709312640"
4. routing 表更新: "feishu.lab:ou_xxx" → "feishu.lab.1709312640"
```

#### 20.3.2 Session 命名简化

新 session key 格式：`{channel}.{unix_timestamp}`
- 飞书: `feishu.lab.1709312640`、`feishu.ST.1709312640`
- CLI: `cli.1709312640`
- Webchat: 不受影响（已经是 `webchat:timestamp` 格式）

#### 20.3.3 多次 /new 的行为

```
初始:   key = "feishu.lab:ou_xxx"  →  file: feishu.lab_ou_xxx.jsonl
/new 1: key = "feishu.lab.1709312640"  →  file: feishu.lab.1709312640.jsonl
        routing: "feishu.lab:ou_xxx" → "feishu.lab.1709312640"
/new 2: key = "feishu.lab.1709400000"  →  file: feishu.lab.1709400000.jsonl
        routing: "feishu.lab:ou_xxx" → "feishu.lab.1709400000"
        旧的 feishu.lab.1709312640.jsonl 不动
```

### 20.4 影响范围

| 文件 | 改动 |
|------|------|
| `session/manager.py` | `create_new_session()` 反转归档方向 + 新 key 命名 |
| `agent/loop.py` | `/flush` 路径中 `create_new_session()` 返回值处理；`/new` 返回值更新 |

### 20.5 不做什么

- 不修改 `analytics.db` 的 schema 或数据
- 不修改 webchat 的 session 创建逻辑（已经是独立 key）
- 不迁移历史归档文件（旧格式的归档文件保留原样）
- 不修改 `resolve_session_key()` 和 routing 表机制（已完备）

---

## 十九、/session 状态查询命令（Phase 20）

### 19.1 需求背景

用户在使用 nanobot 时，尤其是 Gateway 并发模式下多个 session 同时运行，需要一种快速方式查看当前 session 的基本信息和运行状态，以了解：
- 当前对话所在的 session key 是什么
- session 当前是在执行任务还是在等待输入
- 正在使用哪个 Provider/Model
- 对话历史的消息量和归档状态

### 19.2 功能描述

新增 `/session` 斜杠命令，输出当前 session 的状态信息：

| 字段 | 说明 |
|------|------|
| Session Key | 当前 session 的唯一标识 |
| 状态 | 🔄 执行中（正在处理任务） / 💤 空闲（等待输入） |
| Provider/Model | 当前 session 使用的 LLM provider 和 model |
| Token 用量 | 累计 prompt / completion / total tokens + LLM 调用次数 |
| 消息数 | 总消息数 + 未归档消息数 |
| 创建时间 | session 首次创建的时间 |
| 最后更新 | session 最近一次更新的时间 |

### 19.3 技术设计

- 在 `AgentLoop` 中新增 `_handle_session_command()` 方法
- Gateway 并发模式（`run()`）：检查 `active_sessions` 字典中是否有该 session 的活跃 task
- CLI/直接调用模式（`_process_message()`）：始终显示空闲（无 active_sessions 上下文）
- Provider 信息通过 `ProviderPool.get_session_provider_name()` / `get_session_model()` 获取
- 消息统计从 `Session` 对象的 `messages` 和 `last_consolidated` 字段获取
- Token 用量通过 `UsageRecorder.get_session_usage(session_key)` 从 `analytics.db` 聚合查询

### 19.4 影响范围

| 文件 | 改动 |
|------|------|
| `agent/loop.py` | 新增 `_handle_session_command()`；`run()` 和 `_process_message()` 中添加命令路由；更新 `/help` 文本 |

### 19.5 不做什么

- 不支持查看其他 session 的状态（只查看当前 session）
- 不支持列出所有活跃 session
- 不修改 session 的任何状态（纯只读查询）

---

## 二十一、LLM 错误响应持久化与前端展示（Phase 23）

### 21.1 需求背景

Phase 22 合并 upstream 时引入了 `finish_reason="error"` 防护逻辑：当 LLM API 返回错误响应时，upstream 选择**不存储**到 session JSONL，以防止 error 消息污染后续 LLM context（#1303）。

但这导致了新问题：
1. **Web 前端看不到错误信息** — JSONL 中无记录，页面刷新后错误消息消失
2. **SSE 流无错误内容** — 正常发送 `done` 事件但不含错误文本
3. **错误只存在于日志** — 用户完全不知道发生了什么

### 21.2 目标

在保持 upstream 的 "防止 context 中毒" 安全机制的同时，让错误消息对用户可见。

### 21.3 设计方案

**核心思路**：利用 `get_history()` Phase 2 的错误消息过滤机制（已有），将错误消息以特定前缀存储到 JSONL。这样：
- 存储层有记录 → 前端可展示
- `get_history()` 自动过滤 → 不进入 LLM context

**后端改动（`loop.py`）**：
1. `finish_reason="error"` 分支中，将错误消息以 `"Error calling LLM: {text}"` 前缀存入 JSONL
2. 调用 `callbacks.on_message()` 通知前端实时展示
3. 调用 `on_progress("❌ {text}")` 发送 SSE progress 事件

**前端改动（web-chat `MessageItem.tsx`）**：
1. 检测 `"Error calling LLM:"` 前缀的 assistant 消息
2. 剥离前缀，显示干净的错误文本 + ❌ 图标
3. 错误气泡使用红色调背景和边框

### 21.4 安全机制

- `get_history()` Phase 2 已有过滤逻辑：`m["content"].startswith("Error calling LLM:")` 的 assistant 消息自动剥离
- 此机制在 Phase 8 实现（Session 自修复），Phase 22 合并 upstream 时保留
- 因此错误消息**永远不会进入 LLM context**，不会造成 upstream 担心的 "permanent 400 loops"

### 21.5 影响范围

| 文件 | 改动 |
|------|------|
| `nanobot/agent/loop.py` | `finish_reason="error"` 分支：存储 + callback 通知 |
| web-chat `MessageItem.tsx` | 错误消息检测与样式化（❌ 图标 + 红色气泡） |
| web-chat `MessageList.module.css` | `.errorBubble` / `.errorIcon` / `.errorText` 样式 |
| `tests/test_error_response.py` | 5 个新测试 |

### 21.6 与 Phase 22 的关系

本 Phase 是 Phase 22 merge 的后续修正。Phase 22 合并时对 `finish_reason="error"` 取舍为 "合入 upstream"，但 upstream 的方案（不存储）与 local 的 web-chat 前端配合不佳。本 Phase 在保留 upstream 安全意图的基础上，改进了用户体验。

---

## 二十二、AgentLoop 迭代预算软限制提醒（Phase 25a）

### 22.1 需求背景

`AgentLoop._run_agent_loop()` 有 `max_iterations` 硬限制（默认 40，config 可配）。当迭代次数耗尽时，循环直接截断，LLM 没有任何预警，无法优雅地保存工作状态。

这在 eval-bench 批量构造场景中是**最频繁**的问题：
- batch_build Gen3~5 调度 session 因迭代截断无法完成任务
- QA R2 dispatch_1 也因此被截断
- 每次截断后需要主控手动补全状态和缺失文件

**核心痛点**：LLM 不知道自己还剩多少迭代预算，无法在耗尽前做收尾工作（保存状态、输出中间结果）。

### 22.2 目标

在迭代次数接近上限时，向 LLM 注入 system 提醒消息，告知剩余预算，引导其优雅收尾。

### 22.3 设计方案

在 `_run_agent_loop()` 的 `while` 循环内，当剩余迭代次数达到阈值时，注入一条 system 消息：

```python
remaining = max_iterations - iteration
if remaining == budget_alert_threshold:
    budget_msg = {
        "role": "system",
        "content": f"⚠️ Budget alert: You have {remaining} tool call iterations remaining "
                   f"(out of {max_iterations}). Please prioritize saving your work state "
                   f"and wrapping up gracefully."
    }
    messages.append(budget_msg)
```

**阈值策略**：
- 固定阈值：`remaining == 10`（当 max_iterations >= 20 时）
- 短任务保护：当 `max_iterations < 20` 时，阈值为 `max(3, max_iterations // 4)`
- 只触发一次（`remaining == threshold`，非 `<=`），避免重复注入

### 22.4 设计要求

1. **单次注入**：只在 `remaining == threshold` 时注入一次，不重复
2. **不持久化**：budget alert 消息不写入 session JSONL（仅存在于当前 turn 的 messages 列表中）
3. **不影响正常流程**：注入消息后继续正常循环，不改变任何其他逻辑
4. **阈值可预测**：固定规则，不依赖外部配置（简化实现）
5. **对所有调用方式生效**：CLI、Web、Gateway、SDK 统一行为

### 22.5 影响范围

| 文件 | 改动 |
|------|------|
| `nanobot/agent/loop.py` | `_run_agent_loop()` 循环内新增 budget alert 逻辑（~10 行） |
| `tests/test_budget_alert.py` | 新增测试：阈值触发、单次注入、短任务保护 |

### 22.6 不做什么

- 不修改 `max_iterations` 的默认值或配置方式
- 不做"自动延长迭代"功能
- 不做 callback 通知（仅注入 system 消息给 LLM）

---

## 二十三、exec 工具动态超时参数（Phase 25b）

### 23.1 需求背景

当前 `ExecTool` 的超时时间在初始化时固定（默认 60s，config 可配），LLM 调用时只能传 `command` 和 `working_dir` 两个参数，无法根据命令特性动态指定超时。

在 eval-bench 批量构造场景中，以下操作经常超时：
- `git clone` 大仓库（可能需要 2-5 分钟）
- 复杂的 Python 脚本执行（数据处理、文件复制）
- `tar`/`zip` 压缩大目录

**核心痛点**：LLM 知道某个命令需要更长时间，但无法告知 exec 工具调整超时。

### 23.2 目标

为 `exec` 工具新增可选的 `timeout` 参数，允许 LLM 在调用时动态指定超时时间，覆盖实例默认值。

### 23.3 设计方案

1. **参数定义**：在 `ExecTool.parameters` 的 `properties` 中新增 `timeout` 字段：
   ```json
   {
     "timeout": {
       "type": "integer",
       "description": "Optional timeout in seconds for this command (overrides default). Use for long-running commands like git clone, large file operations, etc."
     }
   }
   ```
   注意：不加入 `required` 列表（可选参数）。

2. **执行逻辑**：`execute()` 方法中优先使用调用时传入的 `timeout`，否则 fallback 到 `self.timeout`：
   ```python
   async def execute(self, command: str, working_dir: str | None = None, timeout: int | None = None) -> str:
       effective_timeout = timeout if timeout is not None else self.timeout
       # ... 使用 effective_timeout ...
   ```

3. **安全上限**：设置硬上限（如 600s = 10 分钟），防止 LLM 设置过大的超时值导致进程长时间挂起。

### 23.4 设计要求

1. **向后兼容**：`timeout` 参数可选，不传时行为不变
2. **安全上限**：`min(timeout, MAX_TIMEOUT)` 防止滥用，`MAX_TIMEOUT = 600`
3. **错误消息更新**：超时错误消息中显示实际使用的超时值
4. **审计日志**：如果使用了自定义 timeout，在审计日志中记录

### 23.5 影响范围

| 文件 | 改动 |
|------|------|
| `nanobot/agent/tools/shell.py` | `parameters` 新增 `timeout`；`execute()` 方法支持动态超时 |
| `tests/test_exec_timeout.py` | 新增测试：动态超时、默认 fallback、安全上限 |

### 23.6 不做什么

- 不修改 config 中的默认超时配置方式
- 不做命令级自动超时估算（由 LLM 自行判断）
- 不影响 deny_patterns / allow_patterns 等安全检查逻辑

---

## 二十四、spawn subagent 能力增强（Phase 26）

### 24.1 需求背景

spawn subagent 是 nanobot 的后台任务执行机制，允许主 agent 将独立任务委托给子 agent 在后台完成。但在 eval-bench 批量构造的实际使用中，spawn 几乎无法胜任需要文件 I/O 的任务，导致不得不用三层编排（主控 → 调度 session → worker session）的 workaround。

**当前 spawn 的限制**：
1. **15 轮硬限制**：`max_iterations = 15` 硬编码在 `subagent.py` 中，稍复杂的任务根本完不成
2. **无 session 持久化**：纯内存运行，所有消息在 subagent 完成后丢失，无法回溯调试
3. **工具可能不生效**：用户反馈 write_file/exec 调用不生效（需验证根因）
4. **无迭代预算提醒**：subagent 不享有主 agent loop 的 budget alert 机制
5. **无 LLM 重试**：subagent 的 `provider.chat()` 调用无重试机制，遇到 rate limit 直接失败
6. **无 usage 记录**：subagent 的 token 消耗不被记录到 analytics.db

**对编排简化的帮助**：如果 spawn 可靠，可以把三层架构简化为两层甚至一层：
- 不需要 curl 启动 worker session 的 workaround
- spawn 完成后通过内部 message bus 自动通知主 agent
- 错误处理更简单——异常直接在结果中报告

### 24.2 目标

增强 spawn subagent 的能力，使其能可靠地完成中等复杂度的文件 I/O 任务。

### 24.3 子需求

#### 24.3.1 max_iterations 可配置

将 `max_iterations` 从硬编码 15 改为可配置：
- spawn 工具新增可选 `max_iterations` 参数，LLM 可按任务复杂度指定
- 默认值从 15 提升到 30（更实用的默认值）
- 硬上限 100（防止 LLM 设置过大值导致失控）
- SubagentManager 构造函数接受 `default_max_iterations` 参数

#### 24.3.2 Session 持久化（可选）

subagent 的消息可选写入 session JSONL，便于调试和回溯：
- 新增 `persist` 参数（默认 `false`，保持向后兼容）
- persist=true 时，subagent 消息写入 session JSONL
- session_key 格式：`subagent:{parent_key_sanitized}_{task_id}`
  - 包含父 session 信息，便于前端识别和分组
  - 示例：`subagent:webchat_1772030778_a1b2c3d4`
  - 无父 session 时退化为 `subagent:{task_id}`
- 持久化使用现有 `SessionManager.append_message()` 机制
- subagent 完成后，session 文件保留（不自动清理）
- 前端通过 `subagent` channel 前缀识别，归入独立的「🤖 子任务」分组

#### 24.3.3 迭代预算提醒

复用主 agent loop 的 `_budget_alert_threshold()` 机制：
- subagent 循环内注入 budget alert system 消息
- 阈值计算复用 `_budget_alert_threshold()` 函数

#### 24.3.4 LLM 重试机制

subagent 的 `provider.chat()` 调用增加重试：
- 复用 `AgentLoop._is_retryable()` 和指数退避逻辑
- 最大重试 3 次（subagent 场景下不需要 5 次那么多）
- 重试间隔：5s → 10s → 20s

#### 24.3.5 Usage 记录

subagent 的 token 消耗写入 analytics.db：
- SubagentManager 接受 `usage_recorder` 参数
- 每次 LLM 调用后立即写入（复用 Phase 4 的逐次写入模式）
- session_key 格式：`subagent:{parent_key_sanitized}_{task_id}`（与 persist session key 一致）

#### 24.3.6 工具执行验证

验证并修复 subagent 的工具执行问题：
- 编写集成测试验证 write_file/exec/read_file 在 subagent 中正常工作
- 如发现根因问题则修复

### 24.4 设计要求

1. **向后兼容**：spawn 工具的现有调用方式不变，新参数均为可选
2. **安全上限**：max_iterations 硬上限 100，防止失控
3. **不引入死锁风险**：subagent 在当前进程内执行，不经过 web-chat worker 队列，因此不与 B2（worker 并发限制）冲突
4. **最小侵入**：改动集中在 `subagent.py` 和 `spawn.py`，不影响主 agent loop
5. **可观测性**：persist 模式下可通过 session 文件回溯 subagent 执行过程

### 24.5 影响范围

| 文件 | 改动 |
|------|------|
| `agent/subagent.py` | max_iterations 可配 + persist + budget alert + retry + usage |
| `agent/tools/spawn.py` | parameters 新增 max_iterations/persist；透传给 SubagentManager |
| `agent/loop.py` | SubagentManager 构造时传入 usage_recorder + session_manager |
| `tests/test_subagent.py` | 新增测试套件 |

### 24.6 不做什么

- 不做 subagent 间通信（subagent 完成后通过 bus 通知主 agent，已有机制）
- 不做 subagent 嵌套（subagent 不能再 spawn subagent）
- 不做 subagent 并发限制（当前场景下不需要）
- 不修改 subagent 的工具集（保持与主 agent 一致，但不含 message/spawn/cron）

---

## §二十五 ProviderPool 接口同步防护 (Phase 27)

> 来源: reasoning_effort bug 复盘
> 状态: ✅ 已完成 (Phase 27, commit `38d6bf8`)

### 25.1 问题描述

Phase 22 merge upstream 时，`LLMProvider.chat()` 接口新增 `reasoning_effort` 参数，但 local 独有的 `ProviderPool.chat()` 未同步更新签名。导致 Phase 26 的 subagent 全部失败（3月5日~7日）。

### 25.2 需求

1. ProviderPool.chat() 应能自动透传 upstream 新增的任何参数，无需手动同步
2. SubagentManager._chat_with_retry() 应与 AgentLoop._chat_with_retry() 保持一致的参数传递模式
3. 每次 merge upstream 后应有自动化检查确保所有 LLMProvider 实现的签名一致
4. 维护一份 merge 后必检清单文档

### 25.3 设计方案

#### 25.3.1 ProviderPool **kwargs 透传

将 `chat()` 签名改为 `(self, messages, **kwargs)`，透传所有参数给底层 provider。`model` 参数始终被覆盖为 `self._active_model`。

#### 25.3.2 SubagentManager 条件传递

`_chat_with_retry()` 使用 `if param is not None: kwargs[key] = param` 模式，与 `AgentLoop._chat_with_retry()` 一致。

#### 25.3.3 接口签名一致性测试

`TestProviderInterfaceConsistency` 测试类，检查所有 LLMProvider 实现（ProviderPool、LiteLLMProvider、CustomProvider）的 chat() 签名与 base.py 一致。

#### 25.3.4 Merge 后必检清单

在 `docs/LOCAL_CHANGES.md` 末尾维护清单，列出每次 merge 后需要检查的 local 自定义代码。

### 25.4 影响范围

| 文件 | 改动 |
|------|------|
| `providers/pool.py` | chat() → **kwargs 透传 |
| `agent/subagent.py` | _chat_with_retry() 条件传递 |
| `tests/test_provider_pool.py` | +5 新测试 |
| `docs/LOCAL_CHANGES.md` | Merge 必检清单 |

---

## §二十六 LiteLLMProvider 错误吞没导致 Retry 机制失效 (Hotfix)

### 26.1 问题描述

Phase 11 实现了 `AgentLoop._chat_with_retry()` 指数退避重试机制，Phase 26 在 SubagentManager 中也复制了相同逻辑。但**两者从未真正生效过**。

**根因**：`LiteLLMProvider.chat()` 的 `except Exception as e` 捕获了所有异常（包括 `RateLimitError`），将其包装为 `LLMResponse(content="Error calling LLM: ...", finish_reason="error")` 返回。上层 `_chat_with_retry()` 收到的是正常返回值而非异常，永远不会进入 retry 分支。

**现象**：
- 遇到 rate limit 时直接返回错误消息写入 session，无任何重试
- 错误消息出现双重前缀：`"Error calling LLM: Error calling LLM: litellm.RateLimitError..."`（provider 层 + loop 层各加一次）
- 该 bug 自 Phase 11 引入以来一直存在

### 26.2 修复方案

在 `LiteLLMProvider` 中新增 `_is_retryable()` 静态方法（镜像 `AgentLoop._is_retryable()` 逻辑），对可重试错误 **re-raise** 让上层处理：

- **可重试错误**（re-raise）：`RateLimitError`、`APIConnectionError`、`APITimeoutError`、`Timeout`、`ServiceUnavailableError`、`InternalServerError`、状态码 429/500/502/503/504/529、消息含 "rate limit"/"overloaded"/"capacity"
- **不可重试错误**（保持原行为）：返回 `LLMResponse(finish_reason="error")` 优雅降级

### 26.3 影响范围

| 文件 | 改动 |
|------|------|
| `providers/litellm_provider.py` | 新增 `_is_retryable()` + 修改 except 分支逻辑 |

### 26.4 验证

- 34 个 retry 相关测试通过
- 39 个 provider 相关测试通过
- Gateway 重启后生效

---

## §二十七 弱网环境 LLM API 稳定性增强 (Phase 28)

### 27.1 问题描述

弱网条件下频繁出现两类错误导致 session 中断：
1. `litellm.InternalServerError: AnthropicException - Server disconnected`
2. `litellm.Timeout: AnthropicException - Connection timed out. Timeout passed=600.0`

### 27.2 诊断结果

| # | 问题 | 严重性 |
|---|------|--------|
| 1 | litellm 默认 `request_timeout=6000s`，未覆盖，弱网下单次请求卡 100 分钟 | 🔴 高 |
| 2 | 无独立 connect timeout，TCP 握手阶段也用全局 timeout | 🔴 高 |
| 3 | AgentLoop 重试延迟 10/20/40/80/160s，对 disconnected 瞬时错误等待过久 | 🟡 中 |
| 4 | subagent `_is_retryable()` 遗漏 `"Timeout"` 类名 | 🟡 中 |
| 5 | `_is_retryable()` 缺少 "disconnected"/"connection reset" 字符串匹配 | 🟡 中 |

### 27.3 需求

1. **合理超时**：设置 connect timeout 30s + read timeout 300s（通过 litellm `timeout` 参数）
2. **智能重试延迟**：区分 disconnected（快重试 2/4/8s）和 rate limit（慢重试 10/20/40s）
3. **统一 `_is_retryable()`**：提取为共享函数，AgentLoop 和 subagent 复用，补全遗漏模式
4. **增加重试次数**：AgentLoop 从 5 次增到 7 次，subagent 从 3 次增到 5 次
5. **litellm 层面启用 `num_retries=2`**：连接级快速重试，不等待

### 27.4 影响范围

| 文件 | 改动 |
|------|------|
| `providers/litellm_provider.py` | 添加 timeout 参数、num_retries |
| `agent/loop.py` | 重构重试逻辑，使用共享 `_is_retryable`，智能延迟 |
| `agent/subagent.py` | 使用共享 `_is_retryable`，增加重试次数 |
| `agent/retry.py` (新) | 提取共享重试工具函数 |

### 27.5 不做什么

- 不改变 streaming vs non-streaming 模式
- 不改变错误 response 写入 session 的行为
- 不修改 config schema（超时值暂硬编码，后续可配置化）

---

## §二十八 SpawnTool session_key 传递 Bug 修复 (Phase 29)

### 28.1 问题描述

`SpawnTool.set_context()` 从 `channel + ":" + chat_id` 拼接 `_session_key`，但在以下场景中 channel:chat_id 与实际 session_key 不一致：

| 场景 | channel | chat_id | 实际 session_key | SpawnTool 拼出的 key |
|------|---------|---------|-----------------|---------------------|
| web worker | `web` | `1772941119` | `webchat:1772941119` | `web:1772941119` ❌ |
| 飞书 routing 后 | `feishu.lab` | `ou_xxx` | `feishu.lab.1772855249` | `feishu.lab:ou_xxx` ❌ |
| CLI routing 后 | `cli` | `direct` | `cli.1772605898` | `cli:direct` ❌ |

**影响**：subagent session_key 格式为 `subagent:{parent_sanitized}_{task_id}`，parent_sanitized 来自错误的 key，导致前端 `resolveParent()` 无法还原父子关系，subagent session 不会显示为父 session 的子节点。

### 28.2 根因

`_set_tool_context()` 已经接收了正确的 `session_key` 参数（经过 routing 解析后的真正 key），但没有传给 `spawn_tool.set_context()`。

### 28.3 修复方案

1. `SpawnTool.set_context()` 新增 `session_key` 可选参数，优先使用传入值
2. `_set_tool_context()` 将 `session_key` 传给 `spawn_tool.set_context()`

### 28.4 影响范围

| 文件 | 改动 |
|------|------|
| `agent/tools/spawn.py` | `set_context()` 新增 `session_key` 参数 |
| `agent/loop.py` | `_set_tool_context()` 传入 `session_key` |

---

## §二十九 Session 间消息传递机制 (Phase 30)

### 29.1 问题描述

subagent 完成后通过 `_announce_result()` 发 `InboundMessage(channel="system", chat_id="web:xxx")` 到 bus。存在三个问题：

1. **session_key 不匹配**：`session_key` = `"system:web:xxx"`，跟 `active_sessions` 中的 `"web:xxx"` 不匹配，导致 gateway 模式下不会走 inject 而是创建新 session
2. **web worker 消息丢失**：web worker 模式下 bus 没有 consumer，消息直接丢失
3. **CLI 消息丢失**：CLI 单消息模式下同样丢失

### 29.2 需求

引入 `SessionMessenger` 机制：session 间可以互相发送消息。如果目标 session 正在执行则 inject，空闲则触发新一轮执行。

### 29.3 设计方案

#### 29.3.1 SessionMessenger Protocol (callbacks.py)

新增 `SessionMessenger` Protocol，定义 `send_to_session(target_key, content, source_key)` 方法。

#### 29.3.2 GatewaySessionMessenger (loop.py)

Gateway 模式下的实现：持有 `active_sessions` 引用，running session → inject，idle → publish InboundMessage with `session_key_override`。

#### 29.3.3 WorkerSessionMessenger (worker.py)

Web worker 模式下的实现：running task → inject queue，idle → `_run_task_sdk()` 触发新任务。

#### 29.3.4 SubagentManager 改造

- 新增 `session_messenger` 参数
- `_announce_result()` 优先使用 SessionMessenger，fallback 到 bus publish（加 `session_key_override`）
- `spawn()` 的 `session_key`（父 session key）传递到 `_run_subagent` 和 `_announce_result`

#### 29.3.5 Inject 前缀

- Gateway/Worker inject 时加 `[Message from user during execution]` 前缀
- SessionMessenger inject 时加 `[Message from session {source_key}]` 前缀
- loop.py inject checkpoint 兜底：无前缀的消息加默认前缀

### 29.4 影响范围

| 文件 | 改动 |
|------|------|
| `agent/callbacks.py` | 新增 `SessionMessenger` Protocol |
| `agent/subagent.py` | 新增 `session_messenger` 参数，改造 `_announce_result` |
| `agent/loop.py` | 新增参数透传，inject 前缀兜底，`GatewaySessionMessenger` 实现 |
| web-chat `worker.py` | `WorkerSessionMessenger` 实现，inject 前缀 |
| `tests/test_session_messenger.py` | 8 个新测试 |

### 29.5 不做什么

- 不修改 `bus/events.py`（`session_key_override` 已存在）
- 不修改 InboundMessage 数据结构
- 不做跨进程消息传递（仅进程内）
- **不将 SessionMessenger 暴露为 agent 可用的通用工具** — 跨 session 通信能力目前仅作为内部底层机制，在受限场景（subagent 回报）中使用。agent 自主跨 session 发送消息的行为不可控，暂不开放

---

## §30 Subagent 回报消息 role 修正 + announce 模板优化

### 30.1 背景

Phase 30 引入 SessionMessenger 后，subagent 回报消息在两条路径上都以 `role: "user"` 进入父 session：
- **inject 路径**（父 session 正在运行）：inject 队列只传递 `str`，checkpoint 处硬编码 `role: "user"`
- **trigger 路径**（父 session 已 idle）：通过 `InboundMessage` 触发 `_process_message`，`build_messages` 硬编码 `role: "user"`

导致 agent 把 subagent 回报当成用户新指令执行，产生重复工作或误操作。

### 30.2 需求

1. subagent 回报消息以 `role: "system"` 注入，而非 `role: "user"`
2. role 判定通过结构化通道信息（inject dict 的 role 字段 / InboundMessage 的 channel 字段），不依赖内容文本
3. announce 模板改为通知式，引导 agent 结合上下文自主决策，避免主动重复执行
4. spawn 工具 persist 默认值改为 true

### 30.3 设计

#### 30.3.1 inject 队列扩展 (callbacks.py)

inject 队列从 `Queue[str]` 改为 `Queue[str | dict]`：
- `str`：用户消息（向后兼容），checkpoint 处 `role: "user"`
- `dict`：结构化消息（含 `role` 和 `content` 键），checkpoint 处使用 dict 中的 role

#### 30.3.2 GatewaySessionMessenger inject 改造 (loop.py)

inject 时传 `{"role": "system", "content": prefixed}` 而非纯字符串。

#### 30.3.3 trigger 路径 role 修正 (loop.py)

`_process_message` 中判断 `msg.channel == "session_messenger"` → 将 `build_messages` 输出的最后一条消息 role 改为 `"system"`。

#### 30.3.4 announce 模板重写 (subagent.py)

从指令式改为通知式，引导 agent 结合上下文决策：
- 不再要求 "Summarize this naturally"
- 明确告知 "如果不用响应，不用有输出"
- 明确告知 "不要重复已完成的工作"

### 30.4 影响范围

| 文件 | 改动 |
|------|------|
| `agent/callbacks.py` | inject 队列 `str` → `str \| dict`，类型注解更新 |
| `agent/loop.py` | inject checkpoint 处理 dict + GatewaySessionMessenger inject dict + _process_message channel 判断 |
| `agent/subagent.py` | announce_content 模板重写 |
| `agent/tools/spawn.py` | persist 默认值 → True |

---

## §31 Bug Fix: unhashable type 'slice' on subagent result injection (Hotfix)

### 31.1 问题描述

当 subagent 完成后通过 `SessionMessenger` 注入结果到主 session 时，主 session 在 tool 执行后的 "User injection checkpoint" 处取出注入消息，执行 progress 回调时崩溃。

**错误**：`TypeError: unhashable type: 'slice'`

**根因**：`loop.py` 第 508 行（原始行号）：
```python
await _progress_fn(f"📝 User: {injected[:80]}")
```

Phase 30 引入 `SessionMessenger` 后，`injected` 可能是 `dict`（`{"role": "system", "content": "..."}`）而非字符串。对 dict 做 `[:80]` 切片操作触发 `TypeError`。

**影响**：不丢数据（inject_msg 已正确持久化到 messages），但 progress 回调崩溃导致整个 task 报错退出，主 session 后续 LLM 调用被中断。

### 31.2 修复

将 `injected[:80]` 改为 `inject_msg['content'][:80]`。`inject_msg["content"]` 在 dict 和 string 两个分支中都已被赋值为字符串，安全可切片。

### 31.3 影响范围

| 文件 | 改动 |
|------|------|
| `agent/loop.py` | 1 行：`_progress_fn` 中 `injected[:80]` → `inject_msg['content'][:80]` |

---

## §32 Cache Control 策略优化 + Usage Cache 字段 (Phase 32)

### 32.1 问题描述

当前 cache control 和 usage 记录存在三个问题：

1. **cache_control breakpoint 超限**：`_apply_cache_control()` 对所有 `role: "system"` 的消息注入 breakpoint，但 nanobot 中 system 消息不止一条（subagent 回报、budget alert 等均以 system 注入）。spawn 3+ subagent 后容易超过 Anthropic 的 **4 breakpoint 上限**，导致 API 报错或静默丢弃多余 breakpoint。
2. **cache usage 字段丢失**：`_parse_response()` 只提取 `prompt_tokens` / `completion_tokens` / `total_tokens`，Anthropic 返回的 `cache_creation_input_tokens` 和 `cache_read_input_tokens` 被丢弃，无法追踪缓存命中率。
3. **前端无缓存信息**：数据链路中缺少 cache 字段，前端无法展示缓存命中情况（前端改造在 web-chat 仓库实施，此处只记录 nanobot core 的改动需求）。

### 32.2 目标

1. **cache_control 策略重写**：从"所有 system 消息加 breakpoint"改为精准 3 breakpoint，最大化缓存复用且不超限
2. **全链路增加 cache usage 字段**：provider → loop → subagent → SQLite，完整传递 cache 统计
3. **前端展示增强**（web-chat 仓库实施，此处仅记录 nanobot core 侧的数据支撑需求）

### 32.3 子需求

#### 32.3.1 cache_control 策略重写

新策略使用精准 3 breakpoint：

| Breakpoint | 位置 | 用途 | 复用范围 |
|-----------|------|------|---------|
| **#1** | `tools[-1]` | 缓存 tool 定义 | 跨 session（tools 几乎不变） |
| **#2** | `messages[0]`（首条 system prompt） | 缓存 system prompt | 跨 session（system prompt 几乎不变） |
| **#3** | `messages[-1]` 最后一个 content block | 缓存已有对话历史 | 同 session 内多轮迭代 |

设计要求：
- **其他 system 消息不加 breakpoint**：subagent 回报、budget alert 等 system 消息不注入 `cache_control`
- **仅对支持 cache 的 provider 生效**：`_supports_cache_control()` 返回 True 时才执行策略
- **边界安全**：消息只有 1 条时不加 #3；没有 tools 时不加 #1；content 为空或非标准格式时跳过，不报错

#### 32.3.2 Usage cache 字段全链路传递

**Provider 层**（`litellm_provider.py`）：
- `_parse_response()` 提取 `cache_creation_input_tokens` + `cache_read_input_tokens`
- 使用 `getattr(response.usage, field, 0) or 0` 兼容非 Anthropic provider

**Loop 层**（`loop.py`）：
- `accumulated_usage` 增加 `cache_creation_input_tokens` 和 `cache_read_input_tokens` 字段累加
- `usage_recorder.record()` 传递 cache 字段

**Subagent 层**（`subagent.py`）：
- 同步增加 cache 字段累加和 `usage_recorder.record()` 传递

**SQLite schema**（`recorder.py`）：
- `record()` 方法新增 `cache_creation_input_tokens=0` 和 `cache_read_input_tokens=0` 参数
- Schema migration：`ALTER TABLE token_usage ADD COLUMN cache_creation_input_tokens INTEGER DEFAULT 0`（同理 `cache_read_input_tokens`）
- 检测列是否存在，不存在则 ALTER TABLE（向后兼容）

### 32.4 影响范围

| 文件 | 改动 |
|------|------|
| `providers/litellm_provider.py` | `_apply_cache_control()` 策略重写 + `_parse_response()` 增加 cache 字段提取 |
| `agent/loop.py` | `accumulated_usage` 增加 cache 字段 + `usage_recorder.record()` 传递 cache 字段 |
| `agent/subagent.py` | `usage_recorder.record()` 传递 cache 字段 |
| `usage/recorder.py` | `record()` 新增参数 + schema migration |

### 32.5 不做什么

- 不修改 `providers/base.py`（`usage: dict[str, int]` 已足够灵活，无需改类型）
- 不修改 `providers/registry.py`
- 前端展示改造（UsageIndicator、UsagePage、MessageItem）在 web-chat 仓库独立实施
- 不做 cache 命中率的自动告警或策略自适应

---

### 手动维护的 backlog

**note** 这个部分手动添加需求 backlog。被激活后，更新前序需求文档章节，推进开发。

（暂无）

---

## §33 ServiceUnavailableError 误判为可重试错误 (Bug Fix)

### 33.1 问题描述

`litellm.ServiceUnavailableError` 被 `retry.py` 的 `_RETRYABLE_CLASSES` 无条件归为可重试错误。但某些 `ServiceUnavailableError`（和 `InternalServerError`）实际上包含的是**不可重试的配置/API 错误**，例如：

```
litellm.ServiceUnavailableError: AnthropicException - {
  "error": {
    "type": "model_not_found",
    "message": "分组 全模型纯官key 下模型 claude-opus-4-6 无可用渠道（distributor）..."
  }
}
```

这类错误的语义是"模型不存在"或"API 配置错误"，重试 7 次（等待 5~60 秒）毫无意义，只会浪费时间和让用户等待。

### 33.2 根因

`retry.py` 的 `is_retryable()` 仅按异常类名判断，缺少**排除规则**：对于类名匹配可重试类的异常，如果错误消息表明是配置/认证/模型不存在类错误，应判定为不可重试。

### 33.3 需求

在 `is_retryable()` 中新增**不可重试消息模式**（deny list）：即使异常类名或状态码匹配可重试条件，如果错误消息包含以下模式之一，直接返回 `False`：

- `model_not_found` — 模型不存在
- `无可用渠道` — 代理/网关无可用渠道
- `invalid_api_key` / `invalid api key` — API key 无效
- `authentication` / `unauthorized` — 认证失败
- `permission denied` / `access denied` — 权限不足
- `invalid_request` / `invalid request` — 请求格式错误
- `model not found` — 模型不存在（英文变体）
- `does not exist` — 资源不存在
- `not supported` — 不支持的操作

### 33.4 影响范围

| 文件 | 改动 |
|------|------|
| `agent/retry.py` | 新增 `_NON_RETRYABLE_MSG_PATTERNS` + `is_retryable()` 增加排除检查 |
| `tests/test_retry.py` | 新增不可重试消息模式测试 |

### 33.5 不做什么

- 不修改 `_RETRYABLE_CLASSES`（`ServiceUnavailableError` 大多数情况确实可重试）
- 不修改重试次数或延迟策略
- 不修改 provider 层或 loop 层逻辑

---

*本文档将随需求迭代持续更新。*
