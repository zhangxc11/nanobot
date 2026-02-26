# nanobot 核心 — 需求文档

> 状态：**活跃** | 最后更新：2026-02-26

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

### 手动维护的 backlog

**note** 这个部分手动添加需求 backlog。被激活后，更新前序需求文档章节，推进开发。

（暂无待处理 backlog）

---

*本文档将随需求迭代持续更新。*
