<#
.SYNOPSIS
    OpenViking 多 agent 共享记忆库 —— 一键引导安装（幂等，可重复执行）。

.DESCRIPTION
    做这些事：
      1. 检查前置（python / uv）
      2. uv tool install openviking
      3. 创建 %USERPROFILE%\.openviking\，并从 config\*.example 生成 ov.conf / ovcli.conf
         （若已存在则**不覆盖**，只提示；root_api_key 自动生成随机串）
      4. 应用 Studio MIME 补丁
      5. 应用两个必改开关（agent_evolution + extraction_output_format=json）

    不做这些事（需要你手动做，因为涉及密钥/交互）：
      - 设置 SILICONFLOW_KEY 环境变量
      - 签发数据面用户密钥
      - 注册开机自启计划任务（跑 register-autostart.ps1）
      - 接入各 agent

.PARAMETER SkipInstall
    跳过 `uv tool install`（已装好时用）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
#>
[CmdletBinding()]
param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$ovHome = if ($env:OPENVIKING_HOME) { $env:OPENVIKING_HOME } else { Join-Path $env:USERPROFILE ".openviking" }
$python = if ($env:OV_PYTHON) { $env:OV_PYTHON } else { "python" }

function Step($n, $t) { Write-Host ""; Write-Host "== $n. $t" -ForegroundColor Cyan }
function Ok($m)   { Write-Host "   [OK]   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "   [WARN] $m" -ForegroundColor Yellow }
function Bad($m)  { Write-Host "   [FAIL] $m" -ForegroundColor Red }
function Need($m) { Write-Host "   [TODO] $m" -ForegroundColor Magenta }

Write-Host "OpenViking 共享记忆库 —— 引导安装" -ForegroundColor White
Write-Host "  仓库根   = $repoRoot"
Write-Host "  配置目录 = $ovHome"

# ---------------------------------------------------------------- 1. 前置
Step 1 "前置检查"
try {
    $pv = (& $python --version 2>&1).ToString()
    Ok "$python -> $pv"
} catch {
    Bad "找不到 python。装一个 >= 3.10 的版本，或用 -OV_PYTHON 指定路径。"
    exit 1
}
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Bad "找不到 uv。安装： powershell -c `"irm https://astral.sh/uv/install.ps1 | iex`""
    exit 1
}
Ok "uv -> $($uv.Source)"

# ---------------------------------------------------------------- 2. 安装
Step 2 "安装 OpenViking"
if ($SkipInstall) {
    Warn "-SkipInstall，跳过"
} else {
    & uv tool install openviking
    if ($LASTEXITCODE -ne 0) { Warn "uv tool install 返回 $LASTEXITCODE（可能已安装，继续）" }
}
$ovExe = Join-Path $env:APPDATA "uv\tools\openviking\Scripts\ov.exe"
if (Test-Path $ovExe) { Ok "ov -> $ovExe" } else { Warn "未在默认位置找到 ov.exe，后续命令可能需要调整 PATH" }

# ---------------------------------------------------------------- 3. 配置
Step 3 "生成配置"
if (-not (Test-Path $ovHome)) { New-Item -ItemType Directory -Path $ovHome -Force | Out-Null; Ok "已创建 $ovHome" }

$rand = -join ((48..57) + (97..122) | Get-Random -Count 40 | ForEach-Object { [char]$_ })

$ovConf = Join-Path $ovHome "ov.conf"
if (Test-Path $ovConf) {
    Warn "ov.conf 已存在，**不覆盖**（改动请手工合并 config\ov.conf.example）"
} else {
    Copy-Item (Join-Path $repoRoot "config\ov.conf.example") $ovConf
    Ok "已生成 $ovConf"
}

$cliConf = Join-Path $ovHome "ovcli.conf"
if (Test-Path $cliConf) {
    Warn "ovcli.conf 已存在，不覆盖"
} else {
    Copy-Item (Join-Path $repoRoot "config\ovcli.conf.example") $cliConf
    Ok "已生成 $cliConf"
}

# 把占位符换成随机 root_api_key（两边一致）
foreach ($f in @($ovConf, $cliConf)) {
    $raw = Get-Content -LiteralPath $f -Raw -Encoding UTF8
    if ($raw -match "REPLACE_WITH") {
        $raw = $raw -replace "REPLACE_WITH_A_LONG_RANDOM_STRING", $rand
        $raw = $raw -replace "REPLACE_WITH_THE_SAME_VALUE_AS_server\.root_api_key_IN_ov\.conf", $rand
        Set-Content -LiteralPath $f -Value $raw -Encoding UTF8 -NoNewline
        Ok "已生成随机 root_api_key -> $(Split-Path -Leaf $f)"
    }
}

# 收紧权限
try {
    & icacls $cliConf /inheritance:r /grant:r "$env:USERNAME:F" | Out-Null
    Ok "已收紧 ovcli.conf 权限"
} catch { Warn "icacls 收紧权限失败（可忽略）" }

# ---------------------------------------------------------------- 4. MIME 补丁
Step 4 "Studio MIME 补丁（修空白页）"
& $python (Join-Path $repoRoot "scripts\apply-mime-fix.py")

# ---------------------------------------------------------------- 5. 必改开关
Step 5 "应用必改开关（agent_evolution + json 协议）"
& $python (Join-Path $repoRoot "scripts\enable-agent-evolution.py")

# ---------------------------------------------------------------- 下一步
Write-Host ""
Write-Host "============================================================" -ForegroundColor White
Write-Host " 安装完成。接下来手动做这几步：" -ForegroundColor White
Write-Host "============================================================" -ForegroundColor White
Need '1) 设置 provider key:   setx SILICONFLOW_KEY "sk-xxxx"   （然后**新开一个终端**）'
Need '2) 启动服务:            $env:CODEBUDDY_SAFE_DELETE_ENABLED=0; .\scripts\start-ov.cmd'
Need '3) 验收:                ov health ; python .\scripts\ov-doctor.py'
Need '4) 签发用户密钥:        ov admin register-user default default  → 回填 ovcli.conf 的 api_key'
Need "5) 注册自启:            powershell -ExecutionPolicy Bypass -File .\scripts\register-autostart.ps1"
Write-Host ""
Write-Host " 完整流程见 SOP.md ；报错见 TROUBLESHOOTING.md" -ForegroundColor Cyan
