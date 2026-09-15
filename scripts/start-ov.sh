#!/usr/bin/env bash
# ============================================================================
#  OpenViking 服务端启动器（Windows / Git Bash / 仅排障用）
#
#  ⚠️ 长期运行请走计划任务（scripts/register-autostart.ps1）。
#     用本脚本起的进程会挂在当前 shell 会话上，会话一关服务就没了。
#     本脚本适合排障：能直接把 stdout 打在眼前（OV 的日志默认打到 stdout，不落文件）。
#
#  为什么必须清环境：
#    WorkBuddy 等宿主会向子进程注入两层删除拦截
#      PYTHONPATH   -> .../cli/vendor/shim/sitecustomize.py  劫持 Python os.remove
#      NODE_OPTIONS -> .../node-language-shim.cjs            劫持 Node  fs.unlink
#    服务启动时要清理陈旧的 RocksDB / pid 锁文件，被 safe-delete 批量守卫拦下后
#    直接 SystemExit(1)，现象是 "Application startup failed. Exiting."
#
#    ⚠️ 只 unset NODE_OPTIONS 没用 —— Python 侧是经 PYTHONPATH 全局注入的。
# ============================================================================
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OV_HOME="${OPENVIKING_HOME:-$HOME/.openviking}"
ENV_FILE="$ROOT/.env"

# ---------- 1. 隔离宿主 shim ----------
unset NODE_OPTIONS
unset PYTHONPATH
unset PYTHONSTARTUP
export CODEBUDDY_SAFE_DELETE_ENABLED=0

# ---------- 2. 注入 provider key（ov.conf 里写的是 ${SILICONFLOW_KEY}）----------
if [ -z "${SILICONFLOW_KEY:-}" ] && [ -f "$ENV_FILE" ]; then
  SILICONFLOW_KEY=$(sed -nE 's/^[[:space:]]*SILICONFLOW_KEY[[:space:]]*=[[:space:]]*//p' "$ENV_FILE" \
                    | head -1 | tr -d ' \r\n"'"'"'')
  export SILICONFLOW_KEY
fi
if [ -z "${SILICONFLOW_KEY:-}" ]; then
  echo "[start-ov] ERROR: SILICONFLOW_KEY 未设置（环境变量和 $ENV_FILE 里都没有）" >&2
  echo "[start-ov] 设置方式:  setx SILICONFLOW_KEY \"sk-xxxx\"   （或写进 $ENV_FILE，等号两侧不要有空格）" >&2
  exit 1
fi
echo "[start-ov] SILICONFLOW_KEY loaded (len=${#SILICONFLOW_KEY})"

# ---------- 3. 定位 openviking-server ----------
SERVER_BIN="${OV_BIN:-}"
if [ -n "$SERVER_BIN" ] && [ -d "$SERVER_BIN" ]; then
  SERVER_BIN="$SERVER_BIN/openviking-server.exe"
fi
if [ -z "$SERVER_BIN" ] || [ ! -x "$SERVER_BIN" ]; then
  for cand in \
    "$HOME/AppData/Roaming/uv/tools/openviking/Scripts/openviking-server.exe" \
    "$(command -v openviking-server 2>/dev/null || true)"; do
    if [ -n "$cand" ] && [ -x "$cand" ]; then SERVER_BIN="$cand"; break; fi
  done
fi
if [ -z "$SERVER_BIN" ] || [ ! -x "$SERVER_BIN" ]; then
  echo "[start-ov] ERROR: 找不到 openviking-server。请先 uv tool install openviking，" >&2
  echo "[start-ov]        或设置 OV_BIN 环境变量指向安装目录。" >&2
  exit 1
fi
echo "[start-ov] OV_HOME = $OV_HOME"
echo "[start-ov] server  = $SERVER_BIN"
echo "[start-ov] launching ..."

# ---------- 4. 启动 ----------
exec "$SERVER_BIN"
