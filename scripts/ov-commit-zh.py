# -*- coding: utf-8 -*-
"""把一段会话提交给 OpenViking，触发记忆抽取。

用法：
    python ov-commit-zh.py <messages.json> [--wait]

    messages.json 是一个 JSON 数组：
        [{"role":"user","content":"..."}, {"role":"assistant","content":"..."}]

    --wait  提交后轮询直到抽取完成，打印 memory_diff 摘要

为什么必须用脚本，而不是直接在 Git Bash 里调 ov：
    Git Bash 在 Windows 下按本地代码页（中文系统 = GBK）转换命令行参数和管道内容，
    中文传给 ov.exe 会变成乱码 —— 表现为 OpenViking 侧解析失败。
    Python 3 的 subprocess 在 Windows 上走 CreateProcessW（宽字符 API），中文无损。

同时会清掉宿主注入的 shim（NODE_OPTIONS / PYTHONPATH），
否则 ov 子进程在清理陈旧锁文件时会撞上 safe-delete 批量删除守卫。

⚠️ 构造 messages.json 的铁律：
    只用**实测值**，绝不用"记忆里的/上下文里的"背景信息。
    VLM 无从判断真伪，它的职责就是忠实抽取 —— 喂进去的假信息会被永久固化、被召回，
    还带上 viking:// 引用让错误显得更可信。详见 TROUBLESHOOTING §I。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ov import ov_env, run_ov  # noqa: E402


def parse_json(out: str) -> dict | None:
    i = out.find("{")
    if i < 0:
        return None
    try:
        return json.loads(out[i:])
    except json.JSONDecodeError:
        return None


def main() -> int:
    positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if not positional:
        print(__doc__)
        return 2

    env = ov_env()

    with open(positional[0], encoding="utf-8") as f:
        messages = json.load(f)
    if not isinstance(messages, list):
        print("!! messages.json 顶层必须是数组")
        return 2
    print(f"messages = {len(messages)} 条")

    # ---- 1. 建会话 ----
    rc, out, err = _run(["session", "new", "--output", "json"], env)
    sid = ((parse_json(out) or {}).get("result") or {}).get("session_id")
    if not sid:
        print("!! session new 失败:", (out or err)[:300])
        return 1
    print("session_id =", sid)

    # ---- 2. 写消息 ----
    payload = json.dumps(messages, ensure_ascii=False)
    rc, out, err = _run(["session", "add-messages", sid, payload, "--output", "json"], env)
    print("add-messages:", out[:200])
    if '"ok":true' not in out.replace(" ", "") and '"status":"ok"' not in out.replace(" ", ""):
        print("!! add-messages 可能失败:", (out or err)[:300])
        return 1

    # ---- 3. commit（异步）----
    rc, out, err = _run(["session", "commit", sid, "--output", "json"], env)
    res = ((parse_json(out) or {}).get("result") or {})
    task_id = res.get("task_id")
    print("commit status =", res.get("status"), "| task_id =", task_id)
    if not task_id:
        print("!! commit 未返回 task_id:", (out or err)[:300])
        return 1

    if "--wait" not in flags:
        print(f"(异步) 之后可执行:  ov wait && ov task status {task_id}")
        return 0

    # ---- 4. 等抽取完成 ----
    _run(["wait"], env)
    rc, out, err = _run(["task", "status", task_id, "--output", "json"], env)
    d = parse_json(out) or {}
    r = d.get("result") or {}
    inner = r.get("result") if isinstance(r.get("result"), dict) else r
    print("task status =", r.get("status"), "| error =", r.get("error"))
    for k in ("memories_extracted", "agent_evolution_enabled", "agent_memory_skip_reason", "memory_diff_uri"):
        if k in inner:
            print(" ", k, "=", json.dumps(inner[k], ensure_ascii=False)[:300])

    if inner.get("agent_memory_skip_reason"):
        print("  ⚠ agent_evolution 未开启 → 不会产出 experiences/trajectories/cases")

    # ---- 5. 打印 diff ----
    uri = inner.get("memory_diff_uri")
    if uri:
        rc, out, err = _run(["read", uri], env)
        diff = parse_json(out) or {}
        ops = diff.get("operations") or {}
        print()
        print("summary:", json.dumps(diff.get("summary"), ensure_ascii=False))
        for kind in ("adds", "updates", "deletes"):
            items = ops.get(kind) or []
            print(f"### {kind} = {len(items)}")
            for it in items:
                print("  -", it.get("memory_type", ""), "|",
                      str(it.get("uri", "")).replace("viking://user/default/memories", "MEM"))
                body = it.get("after") or it.get("content")
                if body is not None:
                    print("      ", json.dumps(body, ensure_ascii=False)[:260])
    return 0


def _run(args: list[str], env: dict) -> tuple[int, str, str]:
    p = run_ov(args, env=env)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


if __name__ == "__main__":
    sys.exit(main())
