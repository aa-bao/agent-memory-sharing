# -*- coding: utf-8 -*-
"""手动安装 OpenViking 的 ZCode 记忆集成（Windows 版）。

用法：
    python install-zcode.py
    python install-zcode.py --src "<path-to>/examples/zcode-memory-plugin"
    python install-zcode.py --dry-run

为什么需要它：
    官方安装器（install.sh）**硬编码只支持 macOS / Linux**（`exit 1`，无绕过开关）。
    本脚本复刻官方安装器 `install_zcode` 的落点，把插件文件铺好、
    生成 %USERPROFILE%\\.zcode\\hooks.json 与 mcp.json、
    再合并进 ZCode 真正读取的 %USERPROFILE%\\.zcode\\cli\\config.json。

安全设计：
    合并 `cli/config.json` 前会校验：顶层必须是 JSON 对象、必须是合法 JSON，
    否则**拒绝覆盖**。原文件会备份为 config.json.bak。
    已存在且非 OpenViking 管理的同名 MCP server 会被**跳过**，不覆盖用户配置。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

ZCODE_HOME = Path(os.environ.get("ZCODE_HOME") or (Path.home() / ".zcode"))
PLUGIN_DST = Path(os.environ.get("OPENVIKING_HOME") or (Path.home() / ".openviking")) / "agent-integrations" / "zcode"

# 常见插件源位置（官方 marketplace 克隆目录 / 手动放置）
SRC_CANDIDATES = [
    Path.home() / ".codex" / ".tmp" / "marketplaces" / "openviking" / "examples" / "zcode-memory-plugin",
    Path.home() / ".openviking" / "agent-integrations" / "zcode-src",
    Path.home() / ".openviking" / "examples" / "zcode-memory-plugin",
]


def find_src(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_dir():
            raise SystemExit(f"[ERR] 指定的插件源目录不存在：{p}")
        return p
    for c in SRC_CANDIDATES:
        if c.is_dir():
            return c
    raise SystemExit(
        "[ERR] 找不到 zcode-memory-plugin 源目录。请用 --src 指定。\n"
        "      通常它在官方仓库 examples/zcode-memory-plugin，先克隆：\n"
        "        git clone https://github.com/volcengine/OpenViking.git\n"
        "        python install-zcode.py --src OpenViking/examples/zcode-memory-plugin"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description="手动安装 ZCode 记忆集成（Windows）")
    ap.add_argument("--src", default=None, help="zcode-memory-plugin 源目录")
    ap.add_argument("--dry-run", action="store_true", help="只显示计划，不写文件")
    args = ap.parse_args()

    src = find_src(args.src)
    print("=== ZCode 集成安装 ===")
    print(f"  src = {src}")
    print(f"  dst = {PLUGIN_DST}")

    plugin_root = PLUGIN_DST.as_posix()

    if args.dry_run:
        print("  (dry-run) 将复制插件目录，并生成 hooks.json / mcp.json，合并 cli/config.json")
        return 0

    # ---- 1. 落地插件文件 ----
    if PLUGIN_DST.is_dir():
        shutil.rmtree(PLUGIN_DST)
    shutil.copytree(src, PLUGIN_DST)
    print(f"  [OK] 插件已复制 -> {PLUGIN_DST}")

    ZCODE_HOME.mkdir(parents=True, exist_ok=True)

    # ---- 2. 生成 hooks.json / mcp.json ----
    def render(name: str, out_name: str) -> dict:
        p = PLUGIN_DST / name
        if not p.exists():
            raise SystemExit(f"[ERR] 插件里缺少 {name}")
        tpl = json.loads(p.read_text(encoding="utf-8").replace("${ZCODE_PLUGIN_ROOT}", plugin_root))
        out = ZCODE_HOME / out_name
        out.write_text(json.dumps(tpl, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"  [OK] 已写入 {out}")
        return tpl

    hooks = render("hooks/hooks.json", "hooks.json")
    mcp = render(".mcp.json", "mcp.json")

    # ---- 3. 合并进 cli/config.json（ZCode 实际读取处）----
    cfg_path = ZCODE_HOME / "cli" / "config.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)

    config = {}
    existed = cfg_path.exists()
    if existed:
        try:
            config = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise SystemExit(f"[ERR] {cfg_path} 不是合法 JSON，拒绝覆盖：{e}")
        if not isinstance(config, dict):
            raise SystemExit(f"[ERR] {cfg_path} 顶层不是对象，拒绝覆盖")

    config.setdefault("hooks", {})
    config["hooks"]["enabled"] = True
    config["hooks"].setdefault("events", {})
    for event, handlers in (hooks.get("hooks") or {}).items():
        kept = [g for g in config["hooks"]["events"].get(event, [])
                if "openviking-memory" not in json.dumps(g)]
        config["hooks"]["events"][event] = kept + handlers

    config.setdefault("mcp", {}).setdefault("servers", {})
    for name, server in (mcp.get("mcpServers") or {}).items():
        cur = config["mcp"]["servers"].get(name)
        if cur and "openviking-memory" not in json.dumps(cur):
            print(f"  [SKIP] MCP {name} 已存在且非 OpenViking 管理，跳过")
            continue
        config["mcp"]["servers"][name] = server

    if existed:
        shutil.copyfile(cfg_path, str(cfg_path) + ".bak")
        print(f"  [OK] 已备份 -> {cfg_path}.bak")

    cfg_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"  [OK] 已合并 -> {cfg_path}")
    print(f"       hooks.enabled = {config['hooks']['enabled']}")
    print(f"       hooks.events  = {list(config['hooks']['events'].keys())}")
    print(f"       mcp.servers   = {list(config['mcp']['servers'].keys())}")
    print()
    print("  ⚠️ 必须重启 ZCode，hook 才会载入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
