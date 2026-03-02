# nanobot 部署指南

> 本文档面向新环境部署 nanobot 全套系统（核心 + Web Chat + Skills），基于 [zhangxc11](https://github.com/zhangxc11) 的定制版本。

---

## 一、系统要求

| 项目 | 要求 |
|------|------|
| OS | macOS (arm64) / Linux |
| Python | ≥ 3.11 |
| Node.js | ≥ 18 (Web Chat 前端构建) |
| Git | ≥ 2.30 |

## 二、仓库总览

| 仓库 | 说明 | 地址 |
|------|------|------|
| **nanobot** | 核心框架（含 gateway/CLI/agent） | [zhangxc11/nanobot](https://github.com/zhangxc11/nanobot) |
| **nanobot-web-chat** | Web Chat UI（React 前端 + Python 后端） | [zhangxc11/nanobot-web-chat](https://github.com/zhangxc11/nanobot-web-chat) |
| **nanobot-skills** | 辅助 Skill 集合（dev-workflow / restart-*） | [zhangxc11/nanobot-skills](https://github.com/zhangxc11/nanobot-skills) |
| **nanobot-feishu-docs** | 飞书文档操作 Skill | [zhangxc11/nanobot-feishu-docs](https://github.com/zhangxc11/nanobot-feishu-docs) |
| **nanobot-feishu-messenger** | 飞书消息发送 Skill | [zhangxc11/nanobot-feishu-messenger](https://github.com/zhangxc11/nanobot-feishu-messenger) |
| **nanobot-feishu-parser** | 飞书消息解析 Skill | [zhangxc11/nanobot-feishu-parser](https://github.com/zhangxc11/nanobot-feishu-parser) |

### 分支说明

- `nanobot` 仓库：`main` 分支为定制版本，`upstream_main` 为上游 [HKUDS/nanobot](https://github.com/HKUDS/nanobot) 的原始代码
- 其他仓库均使用 `main` 分支

---

## 三、安装步骤

### 3.1 安装 nanobot 核心

```bash
# 克隆仓库
git clone git@github.com:zhangxc11/nanobot.git
cd nanobot

# (可选) 添加上游 remote 以跟踪更新
git remote add upstream https://github.com/HKUDS/nanobot.git

# 安装（开发模式）
pip install -e ".[dev]"

# 验证安装
nanobot --help
```

### 3.2 初始化 nanobot

```bash
# 首次运行，自动创建 ~/.nanobot/ 目录结构
nanobot init

# 编辑配置文件
vim ~/.nanobot/config.json
```

#### config.json 关键配置

```jsonc
{
  // LLM Provider
  "provider": {
    "name": "anthropic",        // 或 openai, volcengine, deepseek 等
    "model": "claude-sonnet-4-20250514",
    "apiKey": "sk-xxx",
    "baseUrl": "https://api.anthropic.com"  // 可选，自定义 endpoint
  },

  // (可选) 多 Provider 配置
  "providers": [
    { "name": "anthropic", "model": "claude-sonnet-4-20250514", "apiKey": "sk-xxx" },
    { "name": "openai", "model": "gpt-4o", "apiKey": "sk-xxx" }
  ],

  // (可选) 飞书通道
  "channels": {
    "feishu": [
      {
        "enabled": true,
        "name": "lab",
        "appId": "cli_xxx",
        "appSecret": "xxx",
        "allowFrom": ["ou_xxx"]
      }
    ]
  }
}
```

> ⚠️ `config.json` 包含敏感凭证，请勿提交到 Git 或分享给他人。

### 3.3 安装 Web Chat

```bash
cd ~/.nanobot/workspace

# 克隆
git clone git@github.com:zhangxc11/nanobot-web-chat.git web-chat
cd web-chat

# 安装前端依赖并构建
cd frontend
npm install
npm run build
cd ..

# 启动服务（webserver:8081 + worker:8082）
python3 webserver.py &
python3 worker.py &

# 或使用 restart.sh（需先安装 restart-webchat skill）
# bash restart.sh all
```

访问 http://localhost:8081 即可使用 Web Chat。

### 3.4 安装 Skills

所有 Skill 安装到 `~/.nanobot/workspace/skills/` 目录下。

```bash
cd ~/.nanobot/workspace/skills

# ── 辅助 Skill 集合 ──
git clone git@github.com:zhangxc11/nanobot-skills.git _nanobot-skills
ln -s _nanobot-skills/dev-workflow dev-workflow
ln -s _nanobot-skills/restart-gateway restart-gateway
ln -s _nanobot-skills/restart-webchat restart-webchat

# ── 飞书 Skills（需要飞书应用凭证）──
git clone git@github.com:zhangxc11/nanobot-feishu-docs.git feishu-docs
git clone git@github.com:zhangxc11/nanobot-feishu-messenger.git feishu-messenger
git clone git@github.com:zhangxc11/nanobot-feishu-parser.git feishu-parser
```

#### 飞书 Skill 依赖

```bash
pip install lark-oapi requests
```

飞书 Skill 从 `~/.nanobot/config.json` 的 `channels.feishu` 配置中读取应用凭证，需要在飞书开放平台创建应用并配置：
- `appId` / `appSecret`
- 开通权限：`im:message`（消息读写）、`docx:document`（文档操作，feishu-docs 需要）

### 3.5 启动 Gateway（IM 通道）

```bash
# 启动 gateway（飞书/Telegram/DingTalk 等 IM 通道）
nanobot gateway

# 后台启动（使用 nohup 或 screen/tmux）
nohup nanobot gateway > ~/.nanobot/gateway.log 2>&1 &
```

> ⚠️ `nanobot gateway` **没有** `--daemonize` 选项，需要用 nohup/screen/tmux 等方式后台运行。

---

## 四、目录结构总览

安装完成后的目录结构：

```
~/.nanobot/
├── config.json                    # 核心配置（Provider + Channels）
├── workspace/
│   ├── memory/
│   │   ├── MEMORY.md              # 长期记忆
│   │   └── HISTORY.md             # 事件日志
│   ├── sessions/                  # 会话存档（.jsonl）
│   ├── web-chat/                  # Web Chat UI
│   │   ├── frontend/              # React 前端
│   │   ├── webserver.py           # API 网关 (:8081)
│   │   ├── worker.py              # Agent 执行器 (:8082)
│   │   └── restart.sh             # 服务管理脚本
│   └── skills/
│       ├── dev-workflow/           # → _nanobot-skills/dev-workflow
│       ├── restart-gateway/        # → _nanobot-skills/restart-gateway
│       ├── restart-webchat/        # → _nanobot-skills/restart-webchat
│       ├── feishu-docs/            # 飞书文档操作
│       ├── feishu-messenger/       # 飞书消息发送
│       ├── feishu-parser/          # 飞书消息解析
│       └── _nanobot-skills/        # nanobot-skills 仓库（被软链接引用）
```

---

## 五、服务管理

### 启动/停止 Web Chat

```bash
cd ~/.nanobot/workspace/web-chat
bash restart.sh status    # 查看服务状态
bash restart.sh all       # 重启 webserver + worker
bash restart.sh stop      # 停止所有服务
```

### 重启 Gateway

```bash
# 方法1: 直接重启
pkill -f "nanobot gateway"
nohup nanobot gateway > ~/.nanobot/gateway.log 2>&1 &

# 方法2: 通过 restart-gateway skill（适用于 IM 通道内的 agent）
bash ~/.nanobot/workspace/skills/restart-gateway/scripts/restart_gateway.sh
```

---

## 六、更新

### 更新 nanobot 核心

```bash
cd /path/to/nanobot
git pull origin main
pip install -e ".[dev]"
```

### 同步上游更新

```bash
cd /path/to/nanobot
git fetch upstream
git merge upstream/main    # 或 git rebase upstream/main
```

### 更新 Skills

```bash
cd ~/.nanobot/workspace/skills/_nanobot-skills && git pull
cd ~/.nanobot/workspace/skills/feishu-docs && git pull
cd ~/.nanobot/workspace/skills/feishu-messenger && git pull
cd ~/.nanobot/workspace/skills/feishu-parser && git pull
```

### 更新 Web Chat

```bash
cd ~/.nanobot/workspace/web-chat
git pull
cd frontend && npm run build && cd ..
bash restart.sh all
```

---

## 七、常见问题

### Q: `nanobot gateway` 启动后立即退出？
检查 `config.json` 中的 channel 配置是否正确，特别是 `appId`/`appSecret`。查看日志：
```bash
tail -f ~/.nanobot/gateway.log
```

### Q: Web Chat 打不开？
确认 webserver 和 worker 都在运行：
```bash
bash ~/.nanobot/workspace/web-chat/restart.sh status
```

### Q: 飞书 Skill 报错 "lark_oapi not installed"？
```bash
pip install lark-oapi
```

### Q: 飞书 Skill 报错 "app not found"？
检查 `~/.nanobot/config.json` 中 `channels.feishu` 数组是否包含对应 `name` 的应用配置。

---

## 八、相关文档

- [nanobot 核心 README](https://github.com/zhangxc11/nanobot/blob/main/README.md)
- [nanobot 上游仓库](https://github.com/HKUDS/nanobot)
- [nanobot-skills 安装说明](https://github.com/zhangxc11/nanobot-skills/blob/main/README.md)
