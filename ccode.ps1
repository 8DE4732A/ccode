#!/usr/bin/env pwsh

param(
    [string]$Option = "",
    [Parameter(ValueFromRemainingArguments=$true)]
    [string[]]$ClaudeArgs
)

# 设置控制台输出编码为 UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
chcp 65001

function Get-ConfigPath {
    # 首先尝试脚本当前目录的 config.yaml
    $scriptDir = Split-Path -Parent $MyInvocation.ScriptName
    $currentDirConfig = Join-Path $scriptDir "ccode_config.yaml"
    if (Test-Path $currentDirConfig) {
        return $currentDirConfig
    }
    # 如果当前目录没有，则从用户目录的 .ccode 文件夹读取
    $userHome = if ($IsWindows) { $env:USERPROFILE } else { $env:HOME }
    return Join-Path $userHome ".ccode" "ccode_config.yaml"
}

function Read-YamlConfig {
    param([string]$ConfigPath)
    if (-not (Test-Path $ConfigPath)) {
        Write-Error "配置文件未找到: $ConfigPath"
        exit 1
    }
    $config = @{
        options = @{}
        common = @{}
    }
    $content = Get-Content $ConfigPath -Raw
    $lines = $content -split "`r?`n"
    $currentSection = $null
    $currentOption = $null
    foreach ($line in $lines) {
        $line = $line.Trim()
        if ([string]::IsNullOrEmpty($line) -or $line.StartsWith('#')) {
            continue
        }
        if ($line.EndsWith(':') -and -not $line.Contains(' ')) {
            $sectionName = $line.TrimEnd(':')
            if ($sectionName -eq 'options' -or $sectionName -eq 'common') {
                $currentSection = $sectionName
                $currentOption = $null
            } else {
                if ($currentSection -eq 'options') {
                    $currentOption = $sectionName
                    $config.options[$currentOption] = @{}
                }
            }
        } elseif ($line.Contains(':')) {
            $parts = $line -split ':', 2
            $key = $parts[0].Trim()
            $value = $parts[1].Trim().Trim('"')
            if ($currentSection -eq 'common') {
                $config.common[$key] = $value
            } elseif ($currentSection -eq 'options' -and $currentOption) {
                $config.options[$currentOption][$key] = $value
            }
        }
    }
    return $config
}

function Set-EnvironmentVariables {
    param(
        [hashtable]$Variables
    )
    foreach ($key in $Variables.Keys) {
        $value = $Variables[$key]
        Set-Item -Path "env:$key" -Value $value
    }
}

function Show-Configuration {
    param(
        [string]$SelectedOption,
        [array]$AvailableOptions,
        [hashtable]$FinalEnv
    )
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host "Claude Code 启动工具" -ForegroundColor Green
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "当前配置: " -NoNewline -ForegroundColor Yellow
    Write-Host $SelectedOption -ForegroundColor Green
    Write-Host ""
    Write-Host "可用选项:" -ForegroundColor Yellow
    foreach ($opt in $AvailableOptions) {
        $marker = if ($opt -eq $SelectedOption) { "* " } else { "  " }
        Write-Host "$marker$opt" -ForegroundColor $(if ($opt -eq $SelectedOption) { "Green" } else { "White" })
    }
    Write-Host ""
    Write-Host "最终环境变量:" -ForegroundColor Yellow
    foreach ($key in $FinalEnv.Keys | Sort-Object) {
        $value = $FinalEnv[$key]
        # 对 ANTHROPIC_AUTH_TOKEN 进行脱敏处理
        if ($key -eq "ANTHROPIC_AUTH_TOKEN" -and $value -ne $null) {
            if ($value.Length -gt 6) {
                $visibleChars = 3
                $maskedLength = $value.Length - ($visibleChars * 2)
                if ($maskedLength -lt 1) { $maskedLength = 1 }
                $maskedValue = $value.Substring(0, $visibleChars) + ("*" * $maskedLength) + $value.Substring($value.Length - $visibleChars)
                Write-Host "  $key = $maskedValue" -ForegroundColor White
            } else {
                Write-Host "  $key = $("*" * $value.Length)" -ForegroundColor White
            }
        } else {
            Write-Host "  $key = $value" -ForegroundColor White
        }
    }
    Write-Host ""
    Write-Host "===========================================" -ForegroundColor Cyan
    Write-Host ""
}

# 主逻辑
try {
    $configPath = Get-ConfigPath
    $config = Read-YamlConfig -ConfigPath $configPath
    $availableOptions = @($config.options.Keys)
    if ($availableOptions.Count -eq 0) {
        Write-Error "配置文件中未找到任何选项"
        exit 1
    }
    # 确定要使用的选项
    if ([string]::IsNullOrEmpty($Option)) {
        $selectedOption = $availableOptions[0]
    } else {
        if ($config.options.ContainsKey($Option)) {
            $selectedOption = $Option
        } else {
            Write-Error "选项 '$Option' 不存在。可用选项: $($availableOptions -join ', ')"
            exit 1
        }
    }
    # 合并环境变量 (common + 选中的选项，选项优先级更高)
    $finalEnv = @{}
    # 先添加 common 环境变量
    foreach ($key in $config.common.Keys) {
        $finalEnv[$key] = $config.common[$key]
    }
    # 再添加选中选项的环境变量 (会覆盖 common 中的同名变量)
    foreach ($key in $config.options[$selectedOption].Keys) {
        $finalEnv[$key] = $config.options[$selectedOption][$key]
    }
    # 显示配置信息
    Show-Configuration -SelectedOption $selectedOption -AvailableOptions $availableOptions -FinalEnv $finalEnv
    # 设置环境变量
    Set-EnvironmentVariables -Variables $finalEnv
    # 启动 claude 命令
    Write-Host "正在启动 claude 命令..." -ForegroundColor Green
    if ($ClaudeArgs -and $ClaudeArgs.Count -gt 0) {
        Write-Host "附加参数: $($ClaudeArgs -join ' ')" -ForegroundColor Yellow
    }
    Write-Host ""
    if ($ClaudeArgs -and $ClaudeArgs.Count -gt 0) {
        & claude @ClaudeArgs
    } else {
        & claude
    }
} catch {
    Write-Error "执行过程中发生错误: $($_.Exception.Message)"
    exit 1
}
