# -*- coding: utf-8 -*-
"""为 Codex 生成 hooks.json —— 把插件模板里的 ${PLUGIN_ROOT} 替换为本机绝对路径。

用法：
    python make-codex-hooks.py
    python make-codex-hooks.py --plugin-root "C:/Users/me/.codex/plugins/openviking-memory"
    python make-codex-hooks.py --check      # 只做前置检查，不写文件

为什么需要它：
    部分 codex 构建**没有 `plugin add` 子命令**，官方安装器又不支持 Windows，
    于是只能手动把插件复制到 %USERPROFILE%\\.codex\\plugins\\openviking-memory，
    再自己把 hooks 模板里的 ${PLUGIN_ROOT} 占位符替换成绝对路径。

前置条件（脚本会检查）
---------------------
1. 插件已在 <plugin-root>，且存在 hooks/hooks.json
2. %USERPROFILE%\\.codex\\config.toml 里有：
       [features]
       plugin_hooks = true

   以及 MCP 注册：
       [mcp_servers.openviking-memory]
       command = "node"
       args = ["<plugin-root>/servers/mcp-proxy.mjs"]
       cwd = "<plugin-root>"

       [plugins."openviking-memory@openviking"]
       enabled = true

   缺了这些 hook 不会生效。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

CODEX_HOME = Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))
DEFAULT_PLUGIN_ROOT = CODEX_HOME / "plugins" / "openviking-memory"

CONFIG_TOML_SNIPPET = """\
[features]
plugin_hooks = true

[mcp_servers.openviking-memory]
command = "node"
args = ["{root}/servers/mcp-proxy.mjs"]
cwd = "{root}"

[plugins."openviking-memory@openviking"]
enabled = true
"""


def main() -> int:
    ap = argparse.ArgumentParser(description="为 Codex 生成 hooks.json")
    ap.add_argument("--plugin-root", default=str(DEFAULT_PLUGIN_ROOT),
                    help=f"插件目录（默认 {DEFAULT_PLUGIN_ROOT}）")
    ap.add_argument("--check", action="store_true", help="只检查，不写文件")
    args = ap.parse_args()

    plugin_root = Path(args.plugin_root).expanduser().resolve()
    plugin_root_fwd = plugin_root.as_posix()
    src = plugin_root / "hooks" / "hooks.json"
    out = CODEX_HOME / "hooks.json"

    print("=== Codex hooks 生成 ===")
    print(f"  plugin root = {plugin_root}")
    print(f"  hooks src   = {src}")
    print(f"  hooks out   = {out}")

    problems = []
    if not plugin_root.is_dir():
        problems.append(f"插件目录不存在：{plugin_root}")
    if not src.exists():
        problems.append(f"缺少 hooks 模板：{src}")
    config_toml = CODEX_HOME / "config.toml"
    if not config_toml.exists():
        problems.append(f"缺少 {config_toml}（需要 [features] plugin_hooks = true）")

    if problems:
        print()
        print("!! 前置条件不满足：")
        for p in problems:
            print("   -", p)
        print()
        print("需要写进 config.toml 的内容：")
        print(CONFIG_TOML_SNIPPET.format(root=plugin_root_fwd))
        return 1

    raw = src.read_text(encoding="utf-8").replace("${PLUGIN_ROOT}", plugin_root_fwd)
    data = json.loads(raw)

    print()
    print("  将注册的 hook：")
    for event, entries in (data.get("hooks") or {}).items():
        for entry in entries:
            for h in entry.get("hooks", []):
                print(f"    {event:20} -> {h.get('command')}")

    if args.check:
        print()
        print("(--check) 未写入")
        return 0

    if out.exists():
        backup = out.with_suffix(".json.bak")
        backup.write_text(out.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\n  已备份原文件 -> {backup}")

    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\n  [OK] 已写入 {out}")
    print()
    print("  ⚠️ hook 只在 Codex **启动时**载入 → 必须重启 Codex 才有真实会话召回。")
    print("     手工调脚本验证成功 ≠ 真实 hook 生效。")
    print(f"     另外确认 {config_toml} 里有 [features] plugin_hooks = true")
    return 0


if __name__ == "__main__":
    sys.exit(main())
