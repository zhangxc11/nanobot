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
  "chat_id": "ou_2fba93da1d059fd2520c2f385743f175",
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
- **≤ 5MB**: 直接 base64 编码，原样传给 LLM
- **> 5MB**: 调用 `_compress_image()` 压缩后再 base64 编码

压缩策略（`_compress_image()`）：
1. **缩小尺寸**: 最长边超过 2048px 时等比缩放到 2048px
2. **降低质量**: 从 JPEG quality=85 开始，每次 -10，直到文件大小 ≤ 5MB 或 quality=30
3. **格式转换**: RGBA/P/LA 等模式转为 RGB（JPEG 不支持透明通道）
4. **优雅降级**: Pillow 未安装时 log warning，原样发送

### 14.4 设计要求

1. **统一入口**: 压缩在 `_build_user_content()` 中处理，所有通道（飞书/Telegram/Web）统一生效
2. **阈值可配**: `IMAGE_MAX_BYTES` 类常量，默认 5MB
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

### 手动维护的 backlog

**note** 这个部分手动添加需求 backlog。被激活后，更新前序需求文档章节，推进开发。

（当前无待处理 backlog）

---

*本文档将随需求迭代持续更新。*
