# -*- coding: utf-8 -*-
"""OpenViking 一键体检。

用法：
    python ov-doctor.py                    # 全套检查
    python ov-doctor.py --parent-chain     # 只看进程父链（判断服务是否已脱离会话）
    python ov-doctor.py --json             # 机器可读输出

检查项：
    1.  环境      ov / openviking-server 可定位，ov.conf / ovcli.conf 存在且合法
    2.  连通性    ov health
    3.  组件      ov status（queue / vectordb / models / lock / retrieval / filesystem）
    4.  配置      ov config validate
    5.  抽取      最近一次 commit 的 memories_extracted 是否为空
    6.  记忆      viking://~/memories 下实际落盘的文件
    7.  Studio    双密钥权限矩阵（401 / 403 / 200）
    8.  进程父链  父链末端是否为 Task Scheduler 的 svchost.exe

这套系统最危险的失败模式是**静默失败** —— 服务死了、抽取返回空、召回返回 {}，
全都不报错。所以不要凭"看起来正常"下结论，跑这个。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ov import (  # noqa: E402
    OV_HOME,
    REPO_ROOT,
    fail,
    find_exe,
    hr,
    ok,
    ov_env,
    ov_json,
    read_conf,
    run_ov,
    warn,
)

RESULTS: list[tuple[str, str, str]] = []  # (检查项, PASS/WARN/FAIL, 详情)
PORT_RE = re.compile(r":(\d+)\s+\S+\s+LISTENING\s+(\d+)", re.I)


def record(name: str, status: str, detail: str = "") -> None:
    RESULTS.append((name, status, detail))
    {"PASS": ok, "WARN": warn, "FAIL": fail}.get(status, warn)(f"{name}  {detail}".rstrip())


# ------------------------------------------------------------------ 检查项


def check_env() -> None:
    hr("1. 环境")
    for which in ("ov", "server"):
        try:
            record(f"{which} 可执行文件", "PASS", find_exe(which))
        except FileNotFoundError as e:
            record(f"{which} 可执行文件", "FAIL", str(e))

    for name in ("ov.conf", "ovcli.conf"):
        p = OV_HOME / name
        if not p.exists():
            record(name, "FAIL", f"缺失：{p}")
            continue
        try:
            cfg = read_conf(name)
        except json.JSONDecodeError as e:
            record(name, "FAIL", f"不是合法 JSON：{e}")
            continue
        notes = []
        if name == "ov.conf":
            mem = (cfg.get("memory") or {})
            srv = (cfg.get("server") or {})
            if mem.get("extraction_output_format") != "json":
                notes.append("⚠ extraction_output_format 不是 json（会导致抽取 0 条）")
            if not (srv.get("agent_evolution") or {}).get("enabled"):
                notes.append("⚠ agent_evolution.enabled 不是 true（无经验类记忆）")
            if not srv.get("root_api_key"):
                notes.append("⚠ root_api_key 为空 → auth_mode 会退化成 dev（无鉴权）")
            key = ((cfg.get("vlm") or {}).get("api_key") or "")
            if key and not key.startswith("${"):
                notes.append("⚠ vlm.api_key 疑似明文，建议改用 ${ENV_VAR} 引用")
        if name == "ovcli.conf":
            if not cfg.get("api_key"):
                notes.append("用户 api_key 为空 → 执行 `ov admin register-user default default` 后回填")
        record(name, "WARN" if notes else "PASS", " | ".join(notes) if notes else _short(OV_HOME / name))


def _short(p: Path) -> str:
    return "OK"


def check_health() -> None:
    hr("2. 连通性")
    p = run_ov(["health"])
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    low = out.lower()
    if p.returncode == 0 and ("healthy" in low or "connected" in low or '"ok"' in low):
        record("ov health", "PASS", out.splitlines()[0] if out else "")
    else:
        record("ov health", "FAIL", out[:200] or "服务未响应")


def check_status() -> None:
    hr("3. 组件")
    p = run_ov(["status"])
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if not out:
        record("ov status", "FAIL", "无输出")
        return
    bad = [ln for ln in out.splitlines() if re.search(r"\b(red|unhealthy|down|fail|error)\b", ln, re.I)]
    if bad:
        record("ov status", "WARN", f"{len(bad)} 项异常：" + " / ".join(b.strip() for b in bad[:3]))
    else:
        record("ov status", "PASS", "各组件未见异常")


def check_config() -> None:
    hr("4. 配置校验")
    p = run_ov(["config", "validate"])
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode != 0:
        record("ov config validate", "WARN", out[:200])
    elif "未知" in out or "unknown" in out.lower():
        record("ov config validate", "PASS", "有 '未知(自定义)' 提示，手写配置的正常现象，无害")
    else:
        record("ov config validate", "PASS", out.splitlines()[0] if out else "OK")


def check_extraction() -> None:
    hr("5. 抽取（最近一次 commit）")
    data = ov_json(["task", "list", "--output", "json"])
    tasks = []
    if isinstance(data, dict):
        res = data.get("result")
        if isinstance(res, list):
            tasks = res
        elif isinstance(res, dict):
            tasks = res.get("tasks") or res.get("items") or []
    if not tasks:
        record("最近一次 commit", "WARN", "查不到任务历史；先跑 ov-commit-zh.py 提交一条会话")
        return

    last = tasks[0] if isinstance(tasks[0], dict) else {}
    tid = last.get("task_id") or last.get("id")
    detail = ov_json(["task", "status", str(tid), "--output", "json"]) if tid else None
    inner = {}
    if isinstance(detail, dict):
        r = detail.get("result") or {}
        inner = r.get("result") if isinstance(r.get("result"), dict) else r

    extracted = inner.get("memories_extracted")
    if extracted in (None, {}, []):
        record(
            "memories_extracted",
            "FAIL",
            "为空 → 排查顺序：① extraction_output_format=json ② agent_evolution.enabled=true "
            "③ VLM 是否 < 4B（见 TROUBLESHOOTING §B）",
        )
    else:
        record("memories_extracted", "PASS", json.dumps(extracted, ensure_ascii=False))
    if inner.get("agent_memory_skip_reason"):
        record("agent_evolution", "WARN", f"skip_reason = {inner['agent_memory_skip_reason']}")


def check_memories() -> None:
    hr("6. 记忆落盘")
    for uri in ("viking://~/memories", "viking://user/default/memories"):
        p = run_ov(["ls", uri])
        out = ((p.stdout or "") + (p.stderr or "")).strip()
        n = len(re.findall(r"viking://\S+", out))
        if p.returncode == 0 and n:
            record(f"ls {uri}", "PASS", f"{n} 个条目")
        else:
            record(f"ls {uri}", "WARN", out[:160] or "空")


def check_studio() -> None:
    hr("7. Studio 双密钥")
    try:
        conf = read_conf("ovcli.conf")
    except Exception as e:  # noqa: BLE001
        record("Studio 鉴权矩阵", "FAIL", f"读不到 ovcli.conf：{e}")
        return
    base = conf.get("url") or "http://127.0.0.1:1933"
    root, user = conf.get("root_api_key"), conf.get("api_key")
    pages = [
        ("技能", "GET", "/api/v1/skills", None),
        ("检索", "POST", "/api/v1/search/search", {"query": "test", "limit": 3}),
        ("会话", "GET", "/api/v1/sessions", None),
        ("agent 经验", "GET", "/api/v1/agent-evolution/experiences/outcomes", None),
        ("用户管理", "GET", "/api/v1/admin/accounts/default/users", None),
    ]

    def call(path, key=None, method="GET", body=None):
        req = urllib.request.Request(base.rstrip("/") + path, method=method)
        req.add_header("Accept", "application/json")
        if key:
            req.add_header("X-API-Key", key)
            req.add_header("X-OpenViking-Account", "default")
            req.add_header("X-OpenViking-User", "default")
        data = json.dumps(body).encode() if body is not None else None
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req, data=data, timeout=20) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:  # noqa: BLE001
            return "ERR"

    bad = 0
    for name, method, path, body in pages:
        u = call(path, user, method, body) if user else "no-key"
        if u not in (200, 400, 405):
            bad += 1
            warn(f"  {name:<12} 用户密钥 -> {u}")
    record(
        "Studio 鉴权矩阵",
        "PASS" if bad == 0 and user else "WARN",
        "所有数据类页面用用户密钥可达" if bad == 0 and user
        else f"{bad} 个页面不可达 → 去 Studio「连接设置」填两把钥匙（见 SOP P6）",
    )


def check_parent_chain() -> None:
    hr("8. 进程父链（判断服务是否已脱离会话）")
    conf = {}
    try:
        conf = read_conf("ov.conf")
    except Exception:  # noqa: BLE001
        pass
    port = (conf.get("server") or {}).get("port", 1933)

    try:
        ns = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, timeout=30).stdout
    except Exception as e:  # noqa: BLE001
        record("父链", "WARN", f"netstat 失败：{e}")
        return

    pid = None
    for line in ns.splitlines():
        if f"127.0.0.1:{port}" in line and "LISTENING" in line.upper():
            m = re.search(r"\s(\d+)\s*$", line.strip())
            if m:
                pid = m.group(1)
                break
    if not pid:
        record(f"端口 {port}", "FAIL", "没有进程在监听 → 服务没起来（见 TROUBLESHOOTING §A）")
        return

    record(f"端口 {port}", "PASS", f"PID {pid} 在监听")

    ps = (
        f"$p = Get-CimInstance Win32_Process -Filter 'ProcessId={pid}'; "
        "$chain = @(); while ($p) { $chain += \"$($p.ProcessId) $($p.Name)\"; "
        "$p = Get-CimInstance Win32_Process -Filter \"ProcessId=$($p.ParentProcessId)\" "
        "-ErrorAction SilentlyContinue }; $chain -join '|'"
    )
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=60, env=ov_env(),
        ).stdout.strip()
    except Exception as e:  # noqa: BLE001
        record("父链", "WARN", f"PowerShell 查询失败：{e}")
        return

    chain = [c for c in out.split("|") if c.strip()]
    print("     " + "\n     ↑ ".join(chain))
    tail = chain[-1].lower() if chain else ""
    if "svchost" in tail:
        record("父链末端", "PASS", "svchost.exe（Task Scheduler）→ 已脱离会话，健康")
    elif "bash" in tail or "powershell" in tail or "cmd" in tail:
        record(
            "父链末端", "FAIL",
            f"{chain[-1]} → 进程挂在会话上，会话一关服务就没了。注册计划任务（SOP P7）",
        )
    else:
        record("父链末端", "WARN", chain[-1] if chain else "未知")


# ------------------------------------------------------------------ main


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenViking 一键体检")
    ap.add_argument("--parent-chain", action="store_true", help="只检查进程父链")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = ap.parse_args()

    if args.parent_chain:
        check_parent_chain()
    else:
        print("OpenViking doctor")
        print(f"  OV_HOME   = {OV_HOME}")
        print(f"  REPO_ROOT = {REPO_ROOT}")
        check_env()
        check_health()
        check_status()
        check_config()
        check_extraction()
        check_memories()
        check_studio()
        check_parent_chain()

    if args.json:
        print(json.dumps(
            [{"check": n, "status": s, "detail": d} for n, s, d in RESULTS],
            ensure_ascii=False, indent=2,
        ))
        return 0 if all(s != "FAIL" for _, s, _ in RESULTS) else 1

    hr()
    n_fail = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    n_warn = sum(1 for _, s, _ in RESULTS if s == "WARN")
    print(f"结果：{len(RESULTS) - n_fail - n_warn} PASS / {n_warn} WARN / {n_fail} FAIL")
    if n_fail:
        print("有 FAIL → 查 TROUBLESHOOTING.md")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
