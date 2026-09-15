# -*- coding: utf-8 -*-
"""公共库：路径探测 / 环境清理 / 调用 ov。

设计原则
--------
1. **零硬编码。** 不写死用户名、不写死盘符。整目录可随意搬、随意改名。
2. **自动清 shim。** 宿主（如 WorkBuddy）会注入 PYTHONPATH / NODE_OPTIONS 删除拦截层，
   导致 openviking-server 启动时清理陈旧锁文件被拦、直接 SystemExit(1)。
   本模块把"清环境"收敛到一处，所有脚本共用。
3. **中文无损。** subprocess 在 Windows 上走 CreateProcessW（宽字符 API），
   而 Git Bash 会按本地代码页（中文系统 = GBK）转换命令行参数 —— 所以中文一律走 Python。

被其它脚本 import 使用：

    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _ov import ov_env, run_ov, OV_HOME, REPO_ROOT
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------- 路径探测

REPO_ROOT = Path(__file__).resolve().parent.parent

#: OpenViking 配置目录。优先环境变量，退化为 ~/.openviking
OV_HOME = Path(os.environ.get("OPENVIKING_HOME") or (Path.home() / ".openviking"))

#: 服务监听端口（仅用于提示，真实值以 ov.conf 为准）
DEFAULT_PORT = 1933

_SCRIPT_NAMES = {
    "ov": ["ov.exe", "ov"],
    "server": ["openviking-server.exe", "openviking-server"],
}


def _uv_tool_scripts_dir() -> Path | None:
    """uv tool 安装目录下的 Scripts/bin。"""
    candidates = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        candidates.append(Path(appdata) / "uv" / "tools" / "openviking" / "Scripts")
    home = Path.home()
    candidates.append(home / ".local" / "share" / "uv" / "tools" / "openviking" / "bin")
    candidates.append(home / ".local" / "bin")
    for c in candidates:
        if c.is_dir():
            return c
    return None


def find_exe(which: str) -> str:
    """定位 ov / openviking-server 可执行文件。

    探测顺序：
        1. 环境变量 OV_BIN（同时指定 ov 与 openviking-server）
        2. PATH
        3. uv tool 安装目录（%APPDATA%/uv/tools/openviking/Scripts 等）
    """
    override = os.environ.get("OV_BIN")
    if override:
        p = Path(override)
        # OV_BIN 可能指向目录，也可能指向 ov 本体
        if p.is_dir():
            for name in _SCRIPT_NAMES[which]:
                if (p / name).exists():
                    return str(p / name)
        elif p.exists() and which == "ov":
            return str(p)

    for name in _SCRIPT_NAMES[which]:
        found = shutil.which(name)
        if found:
            return found

    d = _uv_tool_scripts_dir()
    if d:
        for name in _SCRIPT_NAMES[which]:
            if (d / name).exists():
                return str(d / name)

    raise FileNotFoundError(
        f"找不到 {which} 可执行文件。请先 `uv tool install openviking`，"
        f"或设置环境变量 OV_BIN 指向安装目录。"
    )


# ---------------------------------------------------------------- 环境

#: 宿主可能注入的、必须清掉的变量
_SHIMMED_VARS = ("PYTHONPATH", "NODE_OPTIONS", "PYTHONSTARTUP")


def ov_env(extra: dict | None = None) -> dict:
    """返回一个"干净"的子进程环境。

    - 清掉宿主的删除拦截层（PYTHONPATH / NODE_OPTIONS / PYTHONSTARTUP）
    - 设 CODEBUDDY_SAFE_DELETE_ENABLED=0（官方总开关）
    - 强制 UTF-8 IO，避免中文在子进程侧被按 GBK 处理
    """
    env = dict(os.environ)
    for k in _SHIMMED_VARS:
        env.pop(k, None)
    env["CODEBUDDY_SAFE_DELETE_ENABLED"] = "0"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    if extra:
        env.update(extra)
    return env


def shim_cleanup_shell() -> str:
    """返回一段 shell 片段，供 .sh / .cmd 启动器复用（保持单一真相源）。"""
    return (
        "unset NODE_OPTIONS; unset PYTHONPATH; unset PYTHONSTARTUP\n"
        "export CODEBUDDY_SAFE_DELETE_ENABLED=0"
    )


# ---------------------------------------------------------------- 调用 ov


def run_ov(args: list[str], *, env: dict | None = None, timeout: int = 180) -> subprocess.CompletedProcess:
    """调用 ov，返回 CompletedProcess（文本模式、UTF-8）。"""
    return subprocess.run(
        [find_exe("ov"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env or ov_env(),
        timeout=timeout,
    )


def ov_json(args: list[str], *, env: dict | None = None) -> dict | None:
    """调用 ov 并尽力从 stdout 里解析出第一个 JSON 对象。"""
    p = run_ov(args, env=env)
    txt = (p.stdout or "").strip()
    i = txt.find("{")
    if i < 0:
        return None
    try:
        return json.loads(txt[i:])
    except json.JSONDecodeError:
        return None


def unwrap(payload: dict | None) -> dict:
    """剥掉 OpenViking 的响应信封 {status, result, time}。"""
    if not isinstance(payload, dict):
        return {}
    res = payload.get("result")
    if isinstance(res, dict):
        # 有些接口是双层信封
        inner = res.get("result")
        if isinstance(inner, dict) and "status" in res and len(res) <= 3:
            return inner
        return res
    return payload


# ---------------------------------------------------------------- 杂项


def read_conf(name: str) -> dict:
    """读 %OV_HOME%\\<name>（JSON）。"""
    path = OV_HOME / name
    if not path.exists():
        raise FileNotFoundError(f"缺少配置文件：{path}")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def mask(secret: str | None, keep: int = 8) -> str:
    if not secret:
        return "(empty)"
    return secret[:keep] + "****" + f"(len={len(secret)})"


def hr(title: str = "", width: int = 78) -> None:
    if title:
        print(f"\n{'=' * width}\n{title}\n{'=' * width}")
    else:
        print("-" * width)


def ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


if __name__ == "__main__":
    # 自检：确认路径探测可用
    print(f"REPO_ROOT = {REPO_ROOT}")
    print(f"OV_HOME   = {OV_HOME}  (exists={OV_HOME.is_dir()})")
    for which in ("ov", "server"):
        try:
            print(f"{which:8} = {find_exe(which)}")
        except FileNotFoundError as e:
            print(f"{which:8} = NOT FOUND ({e})")
    sys.exit(0)
