# -*- coding: utf-8 -*-
"""应用两个必改的配置开关（幂等，可重复执行）。

用法：
    python enable-agent-evolution.py            # 改 ov.conf（默认）
    python enable-agent-evolution.py --dry-run  # 只看会改什么

改动内容
--------
1. server.agent_evolution.enabled = true
   默认 false。不改就**永远抽不出** experiences / trajectories / cases 三类 agent 阶段记忆。

2. memory.extraction_output_format = "json"
   默认 "python" —— 抽取的模型输出契约是受限内存 SDK 的 Python 赋值 DSL，
   第三方模型几乎必然违规，4 轮重试全部 parse_error，结果是 memories_extracted = {}。
   实测同一份会话：改 json 前 0 条 / 改后 19 条。

⚠️ 改完必须重启服务（没有热重载）。本脚本会自动备份原文件为 ov.conf.bak。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ov import OV_HOME, fail, hr, ok, warn  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="应用必改的 ov.conf 开关")
    ap.add_argument("--dry-run", action="store_true", help="只显示将要做的改动")
    args = ap.parse_args()

    conf_path = OV_HOME / "ov.conf"
    hr("应用配置开关")
    print(f"  ov.conf = {conf_path}")

    if not conf_path.exists():
        fail("ov.conf 不存在。先从 config/ov.conf.example 复制一份过去。")
        return 1

    with open(conf_path, encoding="utf-8") as f:
        cfg = json.load(f)

    changes: list[str] = []

    srv = cfg.setdefault("server", {})
    ae = srv.setdefault("agent_evolution", {})
    if ae.get("enabled") is not True:
        ae["enabled"] = True
        changes.append("server.agent_evolution.enabled -> true")

    mem = cfg.setdefault("memory", {})
    if mem.get("extraction_output_format") != "json":
        mem["extraction_output_format"] = "json"
        changes.append('memory.extraction_output_format -> "json"')

    if not changes:
        ok("两项开关都已经是目标值，无需改动")
    else:
        for c in changes:
            print(f"  将修改: {c}")
        if args.dry_run:
            warn("dry-run，未写入")
            return 0

        backup = conf_path.with_suffix(".conf.bak")
        shutil.copy2(conf_path, backup)
        ok(f"已备份 -> {backup}")

        with open(conf_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
            f.write("\n")
        ok(f"已写入 {conf_path}")

    hr("当前状态")
    print("  server.agent_evolution        =", json.dumps(srv.get("agent_evolution"), ensure_ascii=False))
    print("  memory.extraction_output_format =", json.dumps(mem.get("extraction_output_format"), ensure_ascii=False))

    print()
    print("  ⚠️ 必须重启服务才能生效。")
    print("     重启后跑 `python ov-doctor.py` 确认。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
