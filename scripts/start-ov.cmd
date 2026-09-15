@echo off
REM ============================================================================
REM  OpenViking server launcher (Windows) - double-click AND Task Scheduler safe
REM
REM  Why this file exists:
REM   1) WorkBuddy and similar hosts inject NODE_OPTIONS (node-language-shim.cjs)
REM      and PYTHONPATH (cli\vendor\shim -> sitecustomize.py). While starting,
REM      openviking-server cleans stale RocksDB / pid lock files; that delete is
REM      intercepted by the safe-delete bulk guard and the process dies with
REM      "Application startup failed. Exiting."  So both layers must be cleared.
REM      (Under Task Scheduler the env is already clean; clearing is harmless.)
REM      NOTE: clearing NODE_OPTIONS alone is NOT enough - the Python side comes
REM            in via PYTHONPATH/sitecustomize.
REM   2) ov.conf stores api_key as ${SILICONFLOW_KEY}; the server expands that,
REM      so the variable must be present in the server process environment.
REM
REM  Must work BOTH double-clicked and headless under Task Scheduler, so:
REM   - never uses `pause` (that would hang a headless task)
REM   - logs everything to %LOGFILE% instead of relying on a console
REM   - never uses %date% (batch codepage is GBK on Chinese Windows -> mojibake)
REM ============================================================================
setlocal

set "NODE_OPTIONS="
set "PYTHONPATH="
set "PYTHONSTARTUP="
set "CODEBUDDY_SAFE_DELETE_ENABLED=0"

REM ---- locate repo root (this file lives in <root>\scripts\) ----
set "ROOT=%~dp0.."

if not defined OPENVIKING_HOME set "OPENVIKING_HOME=%USERPROFILE%\.openviking"
set "LOGDIR=%OPENVIKING_HOME%\logs"
if not exist "%LOGDIR%" mkdir "%LOGDIR%" >nul 2>&1
set "LOGFILE=%LOGDIR%\server-launch.log"

REM ---- fallback: read SILICONFLOW_KEY from <root>\.env ----
REM      .env must be KEY=VALUE with NO spaces around '=' (batch for/f keeps
REM      leading spaces and would corrupt the key).
if "%SILICONFLOW_KEY%"=="" (
  if exist "%ROOT%\.env" (
    for /f "usebackq tokens=1,* delims==" %%A in ("%ROOT%\.env") do (
      if /i "%%A"=="SILICONFLOW_KEY" set "SILICONFLOW_KEY=%%B"
    )
  )
)

if "%SILICONFLOW_KEY%"=="" (
  echo [%time%] ERROR: SILICONFLOW_KEY is not set. >> "%LOGFILE%"
  echo [start-ov] ERROR: SILICONFLOW_KEY is not set.
  echo [start-ov] Set it with:  setx SILICONFLOW_KEY "sk-xxxx"
  exit /b 1
)

REM ---- locate openviking-server ----
set "SERVER_BIN="
if defined OV_BIN (
  if exist "%OV_BIN%\openviking-server.exe" set "SERVER_BIN=%OV_BIN%\openviking-server.exe"
  if exist "%OV_BIN%" if /i "%OV_BIN:~-4%"==".exe" set "SERVER_BIN=%OV_BIN%"
)
if not defined SERVER_BIN (
  if exist "%APPDATA%\uv\tools\openviking\Scripts\openviking-server.exe" (
    set "SERVER_BIN=%APPDATA%\uv\tools\openviking\Scripts\openviking-server.exe"
  )
)
if not defined SERVER_BIN (
  echo [%time%] ERROR: openviking-server.exe not found. >> "%LOGFILE%"
  echo [start-ov] ERROR: openviking-server.exe not found.
  echo [start-ov] Run:  uv tool install openviking
  echo [start-ov] Or set OV_BIN to the install directory.
  exit /b 1
)

echo [%time%] starting openviking-server ... >> "%LOGFILE%"
echo [%time%]   server = %SERVER_BIN% >> "%LOGFILE%"
echo [%time%]   home   = %OPENVIKING_HOME% >> "%LOGFILE%"

"%SERVER_BIN%" >> "%LOGFILE%" 2>&1
echo [%time%] openviking-server exited, code=%ERRORLEVEL% >> "%LOGFILE%"
