# -*- coding: utf-8 -*-
"""审计一个 viking:// 目录下所有文件的内容（单层，用于核对某一天的 events 等）。

用法：
    python ov-audit-dir.py [viking://user/default/memories/events/2026/09/15]

与 ov-audit-tree.py 的区别：本脚本**只扫一层**，适合已知目录结构、只想快速核对一批文件。
"""

from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ov import ov_env, run_ov  # noqa: E402

DEFAULT_AREA = "viking://user/default/memories"


def main() -> int:
    area = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_AREA
    env = ov_env()

    p = run_ov(["ls", area], env=env)
    out = (p.stdout or "") + (p.stderr or "")
    uris = re.findall(r"viking://\S+", out)
    print("area  =", area)
    print("files =", len(uris))
    for u in uris:
        body = run_ov(["read", u], env=env)
        print()
        print("=" * 70)
        print("[file]", u.rsplit("/", 1)[-1])
        print("-" * 70)
        print((body.stdout or body.stderr or "").strip())
    return 0


if __name__ == "__main__":
    sys.exit(main())
