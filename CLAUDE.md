# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

**ccoding** — 基于 curses 的 Claude Code 启动器 TUI，用于配置 LiteLLM proxy / CLI Proxy API 的模型网关参数，并直接启动 `claude` 命令。PyPI 包名 `ccoding`，Python import 包名 `ccode`，命令行入口 `ccode`。

## 常用命令

```bash
uv sync                        # 安装/同步依赖
uv run ccode                   # 运行
uv run python -m ccode         # 模块方式运行（调试）
uv build                       # 构建 wheel / sdist
```

无测试框架，无 lint 配置。

## 架构

`src layout`，包在 `src/ccode/`，模块职责如下：

- **`config.py`** — 所有持久化逻辑。配置文件路径 `~/.ccode/config.json`。`toggles` 字段存储任意环境变量键值对（`dict[str, str]`）。提供 `load_env_schema()` / `env_schema_keys()` / `env_schema_description()` / `env_schema_enum()` 四个函数，从 `env_schema.json` 读取已知变量的元数据。
- **`api.py`** — 无状态函数层：HTTP 拉取 `/v1/models`、构建启动环境变量 `build_env()`、调用 `subprocess.run(["claude", ...])` 启动 claude。
- **`logo.py`** — LOGOS 数据（3 种 ASCII 字体）及 4 种渲染风格函数（wave / pulse / glitch / rain），接收独立参数，不依赖 `CursesApp`。
- **`ui.py`** — `CursesApp` 类，持有全部 UI 状态。配置界面的 field 列表由 `_config_fields()` 动态构建（`base_url`、`api_key`、每个 toggle key、`__add__` 新增行），支持运行时增删 toggle。新增 toggle 时 key 输入框实时展示候选列表，Tab 键补全，候选来源为 `env_schema.json` 的 217 个已知变量。
- **`cli.py`** — 入口，`main(argv)` 构造 `CursesApp` 并调用 `curses.wrapper`。
- **`env_schema.json`** — 从官方文档生成的 Claude Code 环境变量 JSON Schema，含 `description` 和 `enum` 字段，供 UI 补全和提示使用。更新 claude code 版本后如需同步新变量，手动编辑此文件的 `properties` 节。

## 关键数据流

1. `load_config()` 读取 `~/.ccode/config.json`，toggles 值统一为字符串。
2. 主界面 → `fetch_models()` 拉取可用模型 → 用户选择 opus/sonnet/haiku 的 owned_by 和 model_id。
3. 启动时 `build_env()` 将 `base_url`、`api_key`、model id、toggles 全部注入环境变量，执行 `claude`。
4. 配置界面 `ESC` 自动保存后刷新模型列表。

## 注意事项

- `TOGGLE_LABELS` 常量已移除，配置界面 toggles 完全动态，从 `config["toggles"]` 渲染。
- toggles 旧格式（`int` 值 0/1）在 `load_config()` 中自动迁移为字符串。
- Windows 上 curses 依赖 `windows-curses`，在 `pyproject.toml` 中已通过 platform marker 声明。
