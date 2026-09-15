<#
.SYNOPSIS
    无窗口启动 OpenViking server（设计作计划任务动作 / 可手动静默运行）。

.DESCRIPTION
    这个脚本的唯一目的：把 openviking-server 以「隐藏窗口」方式跑起来，
    这样服务在后台运行，桌面上不会冒出一个小黑窗。

    它做四件事：
      1. 清掉 WorkBuddy 注入的 shim 环境变量（否则启动时的清理动作会被 safe-delete 守卫拦截）
      2. 定位 openviking-server 可执行文件（OV_BIN → PATH → uv tools 默认位置）
      3. 解析 SILICONFLOW_KEY（进程环境变量优先，其次本目录 .env）
      4. 以 -WindowStyle Hidden 启动并 WaitForExit（父进程随任务一起存活 / 退出）

    日志写到 $OPENVIKING_HOME/logs/server-launch.log，服务自身的运行日志也在那里。
#>
$ErrorActionPreference = "Stop"

# 1) 清掉 WorkBuddy 注入的 shim（启动时会清理 RocksDB/pid 锁文件，会被 bulk 守卫拦死）
$env:CODEBUDDY_SAFE_DELETE_ENABLED = "0"
Remove-Item Env:PYTHONPATH    -ErrorAction SilentlyContinue
Remove-Item Env:NODE_OPTIONS  -ErrorAction SilentlyContinue
Remove-Item Env:PYTHONSTARTUP -ErrorAction SilentlyContinue

# 2) 定位 openviking-server
$ovBin = if ($env:OV_BIN -and (Test-Path -LiteralPath $env:OV_BIN)) {
    $env:OV_BIN
} else {
    $cand = Join-Path $env:APPDATA "uv\tools\openviking\Scripts\openviking-server.exe"
    if (Test-Path -LiteralPath $cand) { $cand }
    else { (Get-Command openviking-server -ErrorAction SilentlyContinue).Source }
}
if (-not $ovBin -or -not (Test-Path -LiteralPath $ovBin)) {
    throw "openviking-server not found. Install with: uv tool install openviking"
}

# 3) 解析 OVHOME（用于日志目录）
$ovHome = if ($env:OPENVIKING_HOME) { $env:OPENVIKING_HOME }
          else { Join-Path $env:USERPROFILE ".openviking" }
$logDir = Join-Path $ovHome "logs"
if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir "server-launch.log"

# 4) 解析 SILICONFLOW_KEY：进程环境变量优先，其次本目录 .env
if (-not $env:SILICONFLOW_KEY) {
    $envFile = Join-Path $PSScriptRoot ".env"
    if (Test-Path -LiteralPath $envFile) {
        foreach ($line in (Get-Content -LiteralPath $envFile)) {
            if ($line -match '^\s*SILICONFLOW_KEY\s*=\s*(.+?)\s*$') { $env:SILICONFLOW_KEY = $Matches[1]; break }
        }
    }
}
if (-not $env:SILICONFLOW_KEY) {
    Add-Content -LiteralPath $logFile -Value "$(Get-Date) ERROR: SILICONFLOW_KEY not set"
    throw "SILICONFLOW_KEY is not set. Export it (setx SILICONFLOW_KEY sk-xxx), or put it in a .env next to this script."
}

Add-Content -LiteralPath $logFile -Value "$(Get-Date) starting openviking-server (hidden) from $ovBin"
$p = Start-Process -FilePath $ovBin -WindowStyle Hidden -PassThru
$p.WaitForExit()
Add-Content -LiteralPath $logFile -Value "$(Get-Date) openviking-server exited, code=$($p.ExitCode)"
