<#
.SYNOPSIS
    把 OpenViking server 注册为真正的 Windows 服务（用 NSSM）。

.DESCRIPTION
    适用于"能创建服务"的运行环境（你本机的**管理员 PowerShell**）。
    本脚本会：
      1. 探测 nssm.exe / openviking-server.exe / SILICONFLOW_KEY
      2. nssm install 创建服务（这一步会通知 SCM，服务立即可被识别）
      3. 写入 NSSM 参数：无窗口、崩溃自愈（AppExit=Restart）、
         开机自启（Start=auto）、日志重定向、环境变量（含 HOME 与 key）
      4. 启动服务

    注意：在某些受限宿主（如 AI 工具内置的 PowerShell 沙箱）里，
    CreateService 相关的 API 可能被拦截（sc.exe 黑名单、nssm install
    进程被杀），此时要换到本机真实的管理员 PowerShell 运行本脚本。
    如果服务注册表项已经存在但 SCM 没识别（纯注册表写入的情况），
    重启一次系统即可让 SCM 在启动时加载它。

.PARAMETER ServiceName
    服务名，默认 OpenVikingServer。

.PARAMETER NssmPath
    nssm.exe 路径。缺省时自动探测（仓库 binaries、~/.workbuddy/binaries/nssm、PATH）。

.PARAMETER OvbServerPath
    openviking-server.exe 路径。缺省时探测 uv 工具环境。

.PARAMETER EnvFile
    .env 路径，用于读取 SILICONFLOW_KEY。缺省时探测脚本同级 / 仓库根的 .env。

.PARAMETER SkipStart
    只注册不启动。

.EXAMPLE
    # 在本机管理员 PowerShell 里：
    powershell -ExecutionPolicy Bypass -File scripts/install-nssm.ps1
#>
[CmdletBinding()]
param(
    [string]$ServiceName   = 'OpenVikingServer',
    [string]$NssmPath      = '',
    [string]$OvbServerPath = '',
    [string]$EnvFile       = '',
    [switch]$SkipStart
)

$ErrorActionPreference = 'Stop'

# ---------- 工具函数 ----------
function Find-Nssm {
    param([string]$Hint)
    if ($Hint -and (Test-Path $Hint)) { return $Hint }
    $candidates = @(
        (Join-Path $PSScriptRoot '..\binaries\nssm\nssm.exe'),
        (Join-Path $PSScriptRoot '..\..\binaries\nssm\nssm.exe'),
        "$env:USERPROFILE\.workbuddy\binaries\nssm\nssm.exe",
        'nssm.exe'
    )
    foreach ($c in $candidates) {
        $r = Resolve-Path $c -ErrorAction SilentlyContinue
        if ($r) { return $r.Path }
    }
    return ''
}

function Find-OvbServer {
    param([string]$Hint)
    if ($Hint -and (Test-Path $Hint)) { return $Hint }
    $candidates = @(
        "$env:APPDATA\uv\tools\openviking\Scripts\openviking-server.exe",
        (Join-Path $PSScriptRoot '..' 'openviking-server.exe')
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) { return $c }
    }
    # 最后尝试从 PATH
    $p = Get-Command openviking-server.exe -ErrorAction SilentlyContinue
    if ($p) { return $p.Source }
    return ''
}

function Get-KeyFromEnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) { return '' }
    $kv = @{}
    foreach ($l in (Get-Content $Path -Encoding UTF8)) {
        if ($l -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$') {
            $kv[$Matches[1]] = $Matches[2].Trim('"''')
        }
    }
    if ($kv.ContainsKey('SILICONFLOW_KEY')) { return $kv['SILICONFLOW_KEY'] }
    return ''
}

# ---------- 0) 需要管理员 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Error "请以管理员身份运行 PowerShell（右键 -> 以管理员身份运行），否则无法注册服务。"
    exit 1
}

# ---------- 1) 探测 ----------
$nssm = Find-Nssm -Hint $NssmPath
if (-not $nssm) {
    Write-Error "找不到 nssm.exe。请从 https://nssm.cc/release/nssm-2.24.zip 下载，解压后把 nssm.exe 放到本脚本能探测到的位置（如 $env:USERPROFILE\.workbuddy\binaries\nssm\nssm.exe），或用 -NssmPath 指定。"
    exit 1
}
Write-Host "[ok] nssm = $nssm" -ForegroundColor Green

$ovbin = Find-OvbServer -Hint $OvbServerPath
if (-not $ovbin) {
    Write-Error "找不到 openviking-server.exe。请先安装 OpenViking（uv tool install openviking）。"
    exit 1
}
$appDir = Split-Path $ovbin
Write-Host "[ok] openviking-server = $ovbin" -ForegroundColor Green

# OV home：优先 $env:OV_HOME，否则 ~/.openviking
# 注意：这里用 if 语句而不是 $x = if(...){} —— 后者是 PowerShell 7+ 语法，5.1 会报语法错误
if ($env:OV_HOME) {
    $ovhome = $env:OV_HOME
} else {
    $ovhome = Join-Path $env:USERPROFILE '.openviking'
}
$logDir = Join-Path $ovhome 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Force -Path $logDir | Out-Null }

# key：进程环境变量优先，其次 .env
$key = $env:SILICONFLOW_KEY
if (-not $key) {
    if (-not $EnvFile) {
        $EnvFile = Join-Path $PSScriptRoot '..\.env'
        if (-not (Test-Path $EnvFile)) { $EnvFile = Join-Path $PSScriptRoot '.env' }
    }
    $key = Get-KeyFromEnvFile -Path $EnvFile
}
if (-not $key) {
    Write-Error "找不到 SILICONFLOW_KEY：请设置环境变量，或在 .env 里提供。"
    exit 1
}
Write-Host "[ok] SILICONFLOW_KEY = (len $($key.Length))" -ForegroundColor Green

# ---------- 2) 若已存在则移除 ----------
if (Get-Service $ServiceName -ErrorAction SilentlyContinue) {
    Write-Host "[*] 服务已存在，先移除" -ForegroundColor Yellow
    & $nssm remove $ServiceName confirm 2>&1 | Out-Null
}

# ---------- 3) nssm install（通知 SCM 加载服务） ----------
Write-Host "[*] nssm install $ServiceName ..." -ForegroundColor Cyan
& $nssm install $ServiceName $ovbin 2>&1 | ForEach-Object { Write-Host "    $_" }
if ($LASTEXITCODE -ne 0) { Write-Error "nssm install 失败（rc=$LASTEXITCODE）"; exit 1 }

# ---------- 4) 写 NSSM 参数 ----------
$base = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName"
$pKey = Join-Path $base 'Parameters'
if (-not (Test-Path $pKey)) { New-Item -Path $base -Name Parameters -Force | Out-Null }

# 基础（install 已设 Application/AppDirectory，这里再显式写一份更稳）
New-ItemProperty -Path $pKey -Name Application -Value $ovbin           -PropertyType String  -Force | Out-Null
New-ItemProperty -Path $pKey -Name AppDirectory -Value $appDir         -PropertyType String  -Force | Out-Null

# 崩溃自愈：进程退出后由 NSSM 自动重启
if (-not (Test-Path "$pKey\AppExit")) { New-Item -Path $pKey -Name AppExit -Force | Out-Null }
New-ItemProperty -Path "$pKey\AppExit" -Name Default -Value 'Restart'  -PropertyType String  -Force | Out-Null

# 日志重定向（便于排查）
New-ItemProperty -Path $pKey -Name AppStdout     -Value (Join-Path $logDir 'nssm-stdout.log') -PropertyType String -Force | Out-Null
New-ItemProperty -Path $pKey -Name AppStderr     -Value (Join-Path $logDir 'nssm-stderr.log') -PropertyType String -Force | Out-Null
New-ItemProperty -Path $pKey -Name AppRotateFiles -Value 1             -PropertyType DWord    -Force | Out-Null
New-ItemProperty -Path $pKey -Name AppRotateBytes -Value 1048576      -PropertyType DWord    -Force | Out-Null

# 环境变量（multi-string）：key + HOME 指向现有 ~/.openviking
$envs = @(
    "SILICONFLOW_KEY=$key",
    "HOME=$env:USERPROFILE",
    "USERPROFILE=$env:USERPROFILE",
    "CODEBUDDY_SAFE_DELETE_ENABLED=0"
)
New-ItemProperty -Path $pKey -Name AppEnvironmentExtra -Value $envs -PropertyType MultiString -Force | Out-Null

# 服务启动类型：自动（开机即起）
New-ItemProperty -Path $base -Name Start -Value 2 -PropertyType DWord -Force | Out-Null

Write-Host "[ok] 参数已写入" -ForegroundColor Green

# ---------- 5) 启动 ----------
if ($SkipStart) {
    Write-Host "[*] 已跳过启动（SkipStart）。手动启动：Start-Service $ServiceName" -ForegroundColor Yellow
} else {
    Write-Host "[*] 启动服务 ..." -ForegroundColor Cyan
    & $nssm start $ServiceName 2>&1 | ForEach-Object { Write-Host "    $_" }
    Start-Sleep -Seconds 3
    $s = Get-Service $ServiceName -ErrorAction SilentlyContinue
    if ($s -and $s.Status -eq 'Running') {
        Write-Host "[ok] 服务运行中（状态=$($s.Status)）。访问 http://localhost:1933/studio/" -ForegroundColor Green
    } else {
        Write-Warning "服务未处于 Running 状态（$($s.Status)）。查看 $logDir\nssm-stderr.log 排查。"
    }
}
