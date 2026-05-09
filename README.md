# CCode Launcher (Curses UI)

这是一个基于 curses 的 Claude Code 启动器，提供键盘全操作的终端界面，用于配置 Base URL、API Key、模型选择和常用开关，并直接启动 `claude` 命令。

工具的目的是方便启动claude code时快速选择想要使用的模型。模型网关可以使用litellm proxy 或者 CLI Proxy API

![](screenshot.png)
## 功能特点

- 终端 curses UI，键盘全操作
- 主界面选择 OPUS / SONNET / HAIKU 的 owned_by 与 model_id
- 配置界面编辑 Base URL / API Key 与常用开关
- 自动保存配置到 `~/.ccode/config.json`
- 直接启动 `claude` 并传递额外参数
- Remote/Web 模式：启动共享 FastAPI hub，通过 tmux 保留多个 Claude Code 会话并在浏览器中选择操作
- 动画 Logo（颜色渐变 / 流光 / 轻微波浪）

## 安装

推荐使用 [uv](https://github.com/astral-sh/uv) 安装为全局工具：

```bash
uv tool install ccoding
```

或使用 [pipx](https://pipx.pypa.io)：

```bash
pipx install ccoding
```

安装后直接运行：

```bash
ccode
ccode --chrome   # 传递额外参数给 claude
ccode-remote     # 查看 remote hub 状态和存活会话
```

## 开发者运行方式

需要 Python >=3.10。Remote/Web 模式依赖 FastAPI、uvicorn、pexpect，并需要系统中已安装 `tmux` 和 `claude`；Windows 建议在 WSL 中使用 remote 模式。

```bash
git clone https://github.com/8DE4732A/ccode
cd ccode
uv sync
uv run ccode
```

## 快捷键（主界面）

- `↑ / ↓`：切换 OPUS / SONNET / HAIKU 行
- `← / →`：切换 owned_by / model_id 字段
- `a / d`：循环切换当前字段的可选项
- `b`：刷新模型列表
- `r`：切换 Remote/Web 模式
- `c`：进入配置界面
- `Enter`：校验并启动本地 `claude`；Remote/Web 模式开启时启动本地 Web 服务
- `q`：退出

> 提示：首次使用需要先在配置界面设置 Base URL 与 API Key，然后返回主界面按 `b` 获取模型列表。

## 快捷键（配置界面）

- `↑ / ↓`：移动字段焦点
- `Enter / Space`：切换开关项
- 文本输入：编辑 BASE_URL / API_KEY、remote host/port/prefix/token
- `g`：在 Remote token 字段重新生成访问 token
- `ESC`：自动保存并返回主界面

## 配置文件

配置自动保存在：

```
~/.ccode/config.json
```

示例结构：

```json
{
  "base_url": "http://127.0.0.1:8317",
  "api_key": "",
  "models": {
    "opus": {"owned_by": null, "id": null},
    "sonnet": {"owned_by": null, "id": null},
    "haiku": {"owned_by": null, "id": null}
  },
  "toggles": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "0",
    "DISABLE_COST_WARNINGS": "1",
    "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"
  },
  "remote": {
    "enabled": false,
    "host": "127.0.0.1",
    "port": 8765,
    "token": "",
    "session_name": "ccode-claude",
    "reuse_session": true
  }
}
```

## Remote/Web 模式

Remote/Web 模式默认只监听 `127.0.0.1`。token 完全来自用户配置，不会在每次启动时随机生成；如果未配置 token，remote 启动会提示先在配置页设置，或在 TOKEN 字段按 `g` 生成一次并保存。

终端只输出不带 token 的本地访问地址。浏览器打开页面后需要手动输入 token，前端会用 `sessionStorage` 做浏览器会话级缓存；关闭该浏览器会话后需要重新输入。所有 session list、session API 和 WebSocket 连接仍然需要 token。

每个启用 Remote/Web 的 `ccode` 进程都会创建一个唯一 tmux session，session 名由配置里的 `session_name` 前缀、时间戳和进程号组成，例如 `ccode-claude-20260509-153022-12345`。本机只启动一个共享 remote hub，多个本地 `ccode` 进程会复用同一个 host/port/token。浏览器认证后会先显示 session 列表，选择某个 session 后进入 terminal，也可以返回列表重新选择。

session registry 保存在 `~/.ccode/remote_sessions.json`，只记录 tmux session、cwd、args、创建时间和 owner pid，不保存 API key、token 或环境变量。刷新 session 列表时会实时检查 tmux 是否仍存在，并自动隐藏和清理已结束的 session。

浏览器和多个本地终端可以同时 attach 到同一个 tmux session，输入会互相影响，这是预期行为。可用 `Ctrl+B` 然后 `D` 从本地终端 detach；remote hub 会独立留在后台运行，tmux session 不会因为浏览器断开而被 kill。

如果通过 Cloudflare Tunnel、Nginx、Caddy 等反向代理暴露服务，建议额外叠加 Cloudflare Access、Basic Auth 或 OAuth2 proxy。监听 `0.0.0.0` 会把所有 remote sessions 暴露给网络内可达主机，请保护好访问入口和 token。

示例：

```bash
cloudflared tunnel --url http://127.0.0.1:8765
```

remote hub 日志写入 `~/.ccode/remote_server.log`，记录 hub 启停、session 注册、session API 和 WebSocket 连接状态，不记录 token。启动 hub 前如果日志超过 5 MiB 会自动轮转，默认保留 3 个历史文件（`.1`、`.2`、`.3`）。可用 `ccode-remote` 管理 remote hub 并查看监听地址、日志位置、日志大小和存活 tmux sessions；不传子命令时默认等同于 `status`：

```bash
ccode-remote
ccode-remote status
ccode-remote start
ccode-remote restart
ccode-remote stop
```

## 工作原理

1. 从 `~/.ccode/config.json` 读取配置（首次运行自动生成）。
2. 根据 Base URL + API Key 拉取模型列表。
3. 选择 OPUS / SONNET / HAIKU 的 owned_by / model_id。
4. 启动 `claude`，并注入相关环境变量。

## 许可证

MIT
