# -*- coding: utf-8 -*-
"""递归 dump 一个 viking:// 子树下的全部文件内容，用于审计记忆准确性。

用法：
    python ov-audit-tree.py [viking://user/default/memories/entities]

为什么需要它：
    构造验证会话时可能把**未经核实的旧上下文**写进记忆。VLM 无从判断真伪，
    它会忠实抽取 —— 于是假信息被索引、被召回，还带上 viking:// 引用。

    铁律：**发现一条假记忆，必须审计同批写入的全部条目** —— 它们来自同一个未核实来源。
    本脚本就是干这个的：把整棵子树摊开，逐条与实测值核对。
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ov import ov_env, run_ov  # noqa: E402

DEFAULT_ROOT = "viking://user/default/memories"


def walk(uri: str, env: dict, seen: set[str], depth: int = 0) -> None:
    if uri in seen or depth > 6:
        return
    seen.add(uri)

    p = run_ov(["ls", uri], env=env)
    out = (p.stdout or "") + (p.stderr or "")
    for m in re.finditer(r"viking://\S+", out):
        child = m.group(0).rstrip(".,")
        if child == uri or child in seen:
            continue
        if child.endswith(".md") or child.endswith(".json"):
            seen.add(child)
            body = run_ov(["read", child], env=env)
            rel = child.split("/memories/", 1)[-1] if "/memories/" in child else child
            print("=" * 70)
            print("[file]", rel)
            print("-" * 70)
            print((body.stdout or body.stderr or "").strip())
            print()
        else:
            walk(child, env, seen, depth + 1)


def main() -> int:
    root = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ROOT
    env = ov_env()
    print("root =", root)
    print()
    walk(root, env, set())
    return 0


if __name__ == "__main__":
    sys.exit(main())
