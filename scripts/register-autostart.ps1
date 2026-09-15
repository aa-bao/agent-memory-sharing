<#
.SYNOPSIS
    注册 OpenViking 登录自启计划任务。

.DESCRIPTION
    这是整套搭建里最高价值的一步。

    手动起的服务会跟着 shell 会话一起死；而服务一旦死掉，**没有任何地方会报错** ——
    agent 的 hook 会优雅降级，安静地停止召回和写入。一切看起来都正常，只有记忆死了。

    关键参数（都不需要管理员权限，也不需要保存密码）：
      触发器        AtLogOn（当前用户）  数据在用户目录，且需要用户的 SILICONFLOW_KEY
      运行级别      Limited              不需要提权 => 注册时不用 UAC
      登录类型      Interactive          免存密码
      执行时间上限  PT0S（无限制）       ⚠️ 默认是 3 天，到点被系统强杀。常驻服务必须设 0
      多实例        IgnoreNew            防重复启动抢端口

    启动器用 launch-hidden.ps1（PowerShell 以 -WindowStyle Hidden 调起 server），
    所以服务在后台静默运行，桌面上不会冒出一个小黑窗。
    如果仍想要「真正的 Windows 服务（开机即起、登出不死、无窗口）」，见 docs/decisions.md 的 NSSM 方案。

.PARAMETER TaskName
    计划任务名，默认 OpenVikingMemoryServer。

.PARAMETER Launcher
    启动器路径。默认 <仓库根>\scripts\launch-hidden.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\register-autostart.ps1

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\scripts\register-autostart.ps1 -TaskName MyOvServer
#>
[CmdletBinding()]
param(
    [string]$TaskName = "OpenVikingMemoryServer",
    [string]$Launcher = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $Launcher) { $Launcher = Join-Path $repoRoot "scripts\launch-hidden.ps1" }

Write-Host "=== register OpenViking autostart ===" -ForegroundColor Cyan

if (-not (Test-Path -LiteralPath $Launcher)) {
    Write-Host "ERROR: launcher not found: $Launcher" -ForegroundColor Red
    exit 1
}

$me = "$env:USERDOMAIN\$env:USERNAME"
Write-Host "task name   = $TaskName"
Write-Host "launcher    = $Launcher"
Write-Host "run as      = $me"

# 幂等：先移除同名任务
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "removed     = existing task replaced" -ForegroundColor Yellow
} else {
    Write-Host "removed     = (none existed)"
}

# 用 powershell -WindowStyle Hidden 调起启动器，这样 server 在后台静默运行，
# 不会在桌面上弹出小黑窗。-WorkingDirectory 设到脚本所在目录，方便它读取同目录 .env。
$psExe = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
if (-not $psExe) { $psExe = "powershell.exe" }
$psArgs = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Launcher`""

$action = New-ScheduledTaskAction `
    -Execute $psExe `
    -Argument $psArgs `
    -WorkingDirectory (Split-Path -Parent $Launcher)

$trigger = New-ScheduledTaskTrigger -AtLogOn -User $me

# ExecutionTimeLimit = 0 (PT0S) 是必须的：默认 3 天会被系统强杀
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew

$principal = New-ScheduledTaskPrincipal `
    -UserId $me -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal `
    -Description "OpenViking memory server (default 127.0.0.1:1933) - auto start at logon" | Out-Null

$t = Get-ScheduledTask -TaskName $TaskName
Write-Host ""
Write-Host "registered  = OK" -ForegroundColor Green
Write-Host "state       = $($t.State)"
Write-Host "runlevel    = $($t.Principal.RunLevel) / logontype=$($t.Principal.LogonType)"
Write-Host "timelimit   = $($t.Settings.ExecutionTimeLimit)   # 期望 PT0S"
Write-Host "trigger     = $($t.Triggers[0].CimClass.CimClassName)"
Write-Host ""
Write-Host "verify with:  python .\scripts\ov-doctor.py --parent-chain" -ForegroundColor Cyan
Write-Host "start now:    Start-ScheduledTask -TaskName $TaskName" -ForegroundColor Cyan
Write-Host "remove:       Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false" -ForegroundColor Cyan

if ($t.Settings.ExecutionTimeLimit -ne "PT0S") {
    Write-Host ""
    Write-Host "WARNING: ExecutionTimeLimit is not PT0S - the task will be force-killed by Windows." -ForegroundColor Red
}
