# -*- coding: utf-8 -*-
"""把 OpenViking Memory 插件装进 Codex —— 走 Codex 原生的 marketplace 机制。

为什么需要这个脚本
------------------
早期文档推荐的做法是「把插件拷到 %USERPROFILE%\\.codex\\plugins\\openviking-memory，
再手工往 config.toml 里写 [mcp_servers.*] 和 [features] plugin_hooks = true」。
这条路在较新的 Codex 上**会失败**，而且失败得很安静：

  - 只写 [mcp_servers.*] 而不注册 marketplace/plugin，Codex 不会把插件的
    .mcp.json 并进来，会话里表现为「可用资源和资源模板都是空的」——
    看起来像 OpenViking 没配好，其实是插件根本没启用。
  - `plugin_hooks` 是旧版 Codex 的开关；新版默认开 hook，识别的是 `hooks`。

正确姿势是让 Codex 自己装：注册一个本地 marketplace，再 `plugin add`。
本脚本把这件事自动化，并把「本地 marketplace 的相对路径」这个坑处理好。

关键坑（实测）
--------------
marketplace.json 里插件的 `source.path` **必须是相对路径且落在 marketplace 根目录内**：

    ./plugins/openviking-memory      ✅ 被接受
    C:/Users/me/.codex/plugins/...   ❌ 插件列表里直接不出现
    ../../plugins/openviking-memory  ❌ 同上

所以脚本会在 marketplace 根目录下建一个 `plugins/<name>` 的目录联接
（junction，Windows 下不需要管理员）指回真正的插件目录，再写相对路径。

用法
----
    python scripts/install-codex-plugin.py            # 装
    python scripts/install-codex-plugin.py --check    # 只看会做什么
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN_NAME = "openviking-memory"
MARKETPLACE_NAME = "openviking"
IS_WINDOWS = os.name == "nt"


def hr(title: str = "") -> None:
    print()
    if title:
        print(f"=== {title} ===")


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def find_codex_cli(explicit: str | None) -> str | None:
    """按 $CODEX_CLI -> PATH -> Codex 自带 bin 目录的顺序找 codex 可执行文件。"""
    if explicit:
        p = Path(explicit).expanduser()
        return str(p) if p.exists() else None

    env = os.environ.get("CODEX_CLI")
    if env and Path(env).exists():
        return env

    found = shutil.which("codex")
    if found:
        return found

    # Codex 桌面版自带 CLI：%LOCALAPPDATA%\OpenAI\Codex\bin\<hash>\codex.exe
    local = os.environ.get("LOCALAPPDATA")
    if local:
        base = Path(local) / "OpenAI" / "Codex" / "bin"
        if base.is_dir():
            cands = sorted(base.glob("*/codex.exe"),
                           key=lambda p: p.stat().st_mtime, reverse=True)
            if cands:
                return str(cands[0])
    return None


def run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def ensure_plugin_link(marketplace_root: Path, plugin_src: Path,
                       dry: bool) -> Path:
    """在 marketplace 根目录下建 plugins/<name>，指回插件目录。

    优先用 junction（Windows，免管理员）；失败就退回复制。
    """
    plugins_dir = marketplace_root / "plugins"
    link = plugins_dir / PLUGIN_NAME

    if link.exists():
        print(f"  [skip] {link} 已存在")
        return link

    if dry:
        print(f"  [dry ] 会创建 {link} -> {plugin_src}")
        return link

    plugins_dir.mkdir(parents=True, exist_ok=True)

    if IS_WINDOWS:
        r = run(["cmd", "/c", "mklink", "/J", str(link), str(plugin_src)])
        if r.returncode == 0 and link.exists():
            print(f"  [ok  ] junction {link} -> {plugin_src}")
            return link
        print(f"  [warn] mklink 失败（{r.stderr.strip() or r.stdout.strip()}），改复制")

    try:
        shutil.copytree(plugin_src, link)
        print(f"  [ok  ] 已复制到 {link}")
    except Exception as e:                                  # noqa: BLE001
        print(f"  [err ] 无法建立 {link}: {e}")
        raise
    return link


def main() -> int:
    ap = argparse.ArgumentParser(description="把 OpenViking Memory 插件装进 Codex")
    ap.add_argument("--codex", help="codex 可执行文件路径（默认自动探测）")
    ap.add_argument("--plugin-src", default=str(codex_home() / "plugins" / PLUGIN_NAME),
                    help="插件源码目录")
    ap.add_argument("--marketplace-root",
                    default=str(codex_home() / "local-marketplaces" / MARKETPLACE_NAME),
                    help="本地 marketplace 根目录")
    ap.add_argument("--check", action="store_true", help="只检查、不落地")
    args = ap.parse_args()

    plugin_src = Path(args.plugin_src).expanduser().resolve()
    mp_root = Path(args.marketplace_root).expanduser().resolve()

    hr("环境")
    cli = find_codex_cli(args.codex)
    print(f"  codex CLI     : {cli or '未找到'}")
    if cli:
        v = run([cli, "--version"])
        print(f"  codex version : {(v.stdout or v.stderr).strip()}")
    print(f"  插件源码      : {plugin_src}")
    print(f"  marketplace   : {mp_root}")

    problems = []
    if not cli:
        problems.append("找不到 codex CLI —— 用 --codex 指定，或把 codex 放进 PATH")
    if not plugin_src.is_dir():
        problems.append(f"插件源码目录不存在：{plugin_src}")
    elif not (plugin_src / ".codex-plugin" / "plugin.json").is_file():
        problems.append(f"不像一个 Codex 插件（缺 .codex-plugin/plugin.json）：{plugin_src}")
    elif not (plugin_src / ".mcp.json").is_file():
        problems.append(f"缺 .mcp.json（MCP 就是靠它注册的）：{plugin_src}")

    if problems:
        print()
        print("!! 前置条件不满足：")
        for p in problems:
            print("   -", p)
        return 1

    manifest = plugin_src / ".codex-plugin" / "plugin.json"
    try:
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version", "?")
    except Exception:                                       # noqa: BLE001
        version = "?"
    print(f"  插件版本      : {version}")

    hr("建立 marketplace 目录")
    ensure_plugin_link(mp_root, plugin_src, args.check)

    manifest_path = mp_root / ".agents" / "plugins" / "marketplace.json"
    payload = {
        "name": MARKETPLACE_NAME,
        "interface": {"displayName": "OpenViking"},
        "plugins": [{
            "name": PLUGIN_NAME,
            # 必须相对、且在 marketplace 根目录内（见模块 docstring）
            "source": {"source": "local", "path": f"./plugins/{PLUGIN_NAME}"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_USE"},
            "category": "Productivity",
        }],
    }
    if args.check:
        print(f"  [dry ] 会写入 {manifest_path}")
    else:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
        print(f"  [ok  ] 已写入 {manifest_path}")

    if args.check:
        hr("--check 结束（未落地）")
        return 0

    hr("注册 marketplace 并安装插件")
    for cmd, label in [
        ([cli, "plugin", "marketplace", "add", str(mp_root)], "marketplace add"),
        ([cli, "plugin", "add", f"{PLUGIN_NAME}@{MARKETPLACE_NAME}"], "plugin add"),
    ]:
        r = run(cmd, stdin=subprocess.DEVNULL)
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        ok = r.returncode == 0 and "Error" not in out
        print(f"  [{'ok ' if ok else 'err'}] {label}: {out[:300]}")

    hr("验收")
    r = run([cli, "plugin", "list"])
    seg = [ln for ln in (r.stdout or "").splitlines()
           if MARKETPLACE_NAME in ln or PLUGIN_NAME in ln]
    if seg:
        for ln in seg:
            print("  " + ln)
    else:
        print("  (plugin list 里没有 openviking —— 安装可能没生效)")

    print()
    r = run([cli, "mcp", "list"])
    if PLUGIN_NAME in (r.stdout or ""):
        print(f"  [ok  ] MCP 已注册：{PLUGIN_NAME}")
    else:
        print(f"  [err ] MCP 里看不到 {PLUGIN_NAME}")

    print("""
下一步（必须人工做，脚本代替不了）：
  1. 重启 Codex —— 插件和 hook 只在启动时载入
  2. 在 Codex 里执行 /hooks，审阅并批准这些 hook
     （Codex 记 trusted_hash，装完/改过 hooks.json 都要重新批准一次）
  3. /mcp 里应该能看到 openviking-memory

自检：python scripts/ov-doctor.py
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
