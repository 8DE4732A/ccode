# Claude Code 启动工具

这是一个简单的启动工具，用于快速切换和管理不同的 Claude Code 配置。支持 PowerShell 和 Bash 两种脚本。

## 功能特点

- ✅ 多配置管理（anyrouter, seed, xiaomi 等）
- ✅ Common + Option 配置合并（Option 优先级更高）
- ✅ Token 脱敏显示（保护敏感信息）
- ✅ 环境变量自动设置
- ✅ 彩色输出，显示当前激活配置

## 文件说明

- `ccode.ps1` - PowerShell 版本（Windows 推荐）
- `ccode.sh` - Bash 版本（macOS/Linux 推荐，兼容 bash 3）
- `ccode_config.yaml` - 配置文件

## 安装和使用

### PowerShell 版本（Windows）

```powershell
# 1. 设置执行权限（如果需要）
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 2. 运行（使用默认配置/第一个配置）
.\ccode.ps1

# 3. 指定配置运行
.\ccode.ps1 xiaomi
.\ccode.ps1 seed
.\ccode.ps1 anyrouter
```

### Bash 版本（macOS/Linux）

```bash
# 1. 赋予执行权限
chmod +x ccode.sh

# 2. 运行（使用默认配置/第一个配置）
./ccode.sh

# 3. 指定配置运行
./ccode.sh xiaomi
./ccode.sh seed
./ccode.sh anyrouter
```

## 配置文件格式 (`ccode_config.yaml`)

```yaml
options:
  anyrouter:
    ANTHROPIC_AUTH_TOKEN: "sk-xxx"
    ANTHROPIC_BASE_URL: "https://anyrouter.top"

  xiaomi:
    ANTHROPIC_AUTH_TOKEN: "sk-xxx"
    ANTHROPIC_BASE_URL: "https://api.xiaomimimo.com/anthropic"
    ANTHROPIC_DEFAULT_OPUS_MODEL: "mimo-v2-flash"
    ANTHROPIC_DEFAULT_SONNET_MODEL: "mimo-v2-flash"
    ANTHROPIC_DEFAULT_HAIKU_MODEL: "mimo-v2-flash"

  seed:
    ANTHROPIC_AUTH_TOKEN: "xxx"
    ANTHROPIC_BASE_URL: "https://ark.cn-beijing.volces.com/api/compatible"
    API_TIMEOUT_MS: "3000000"
    ANTHROPIC_MODEL: "doubao-seed-code-preview-251028"

common:
  CLAUDE_CODE_ENABLE_TELEMETRY: 0
  DISABLE_COST_WARNINGS: 1
```

## 配置说明

### Options 部分
定义不同的配置方案，每个方案有自己的环境变量。必需字段：
- `ANTHROPIC_AUTH_TOKEN` - API 认证令牌
- `ANTHROPIC_BASE_URL` - API 服务地址

### Common 部分
定义所有配置共享的环境变量，会被 options 中的配置覆盖。

## 输出示例

```
===========================================
Claude Code 启动工具
===========================================

当前配置: xiaomi

可用选项:
  anyrouter
  seed
* xiaomi

最终环境变量:
 ANTHROPIC_AUTH_TOKEN = sk-********************
 ANTHROPIC_BASE_URL = https://api.xiaomimimo.com/anthropic
 ANTHROPIC_DEFAULT_HAIKU_MODEL = mimo-v2-flash
 ANTHROPIC_DEFAULT_OPUS_MODEL = mimo-v2-flash
 ANTHROPIC_DEFAULT_SONNET_MODEL = mimo-v2-flash
 CLAUDE_CODE_ENABLE_TELEMETRY = 0
 DISABLE_COST_WARNINGS = 1

===========================================

正在设置环境变量...
环境变量设置完成

正在启动 claude 命令...
```

## 工作原理

1. 读取同目录下的 `ccode_config.yaml`（如果没有，尝试 `~/.ccode/ccode_config.yaml`）
2. 解析 YAML 配置文件
3. 根据参数选择对应的配置选项
4. 显示配置信息（token 自动脱敏）
5. 设置环境变量
6. 启动 `claude` 命令

## 注意事项

1. **环境变量作用范围**：脚本在当前 shell 进程中设置环境变量，所以必须通过 `source` 方式或确保在子 shell 中继承

2. **macOS bash 版本**：macOS 默认 bash 是 3.2 版本，不支持关联数组。bash 版本已做兼容处理

3. **配置文件位置**：
   - 优先使用脚本同目录的 `ccode_config.yaml`
   - 如果不存在，使用 `~/.ccode/ccode_config.yaml`

4. **Token 脱敏**：显示时会保留前后各3个字符，中间用 `*` 替换

## 常见问题

**Q: 为什么环境变量没有生效？**
A: 确保 claude 命令已经安装并在 PATH 中。检查环境变量是否被正确设置（macOS 可能需要在 `.zshrc` 或 `.bash_profile` 中配置）

**Q: 如何添加新配置？**
A: 在 `ccode_config.yaml` 的 `options:` 下添加新配置，格式参考已有配置

**Q: PowerShell 和 Bash 版本有区别吗？**
A: 功能完全相同，只是运行环境不同。Windows 用户用 `.ps1`，macOS/Linux 用户用 `.sh`

## 许可证

MIT
