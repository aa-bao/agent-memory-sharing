# -*- coding: utf-8 -*-
"""OpenViking Studio 凭证自检 —— 验证「管理密钥 / 用户密钥」分别能访问哪些页面能力。

用法：
    python check-studio-auth.py
    python check-studio-auth.py --base http://127.0.0.1:1933

读取 %USERPROFILE%\\.openviking\\ovcli.conf 里的 root_api_key 与 api_key，
对 Studio 各页面依赖的接口做三组探测（无密钥 / 仅管理密钥 / 仅用户密钥），输出权限矩阵。

判断依据：
    401  -> 前端全局拦截器会强制跳转 /studio/settings（表现为"页面进不去"）
    403  -> 密钥角色不对（能进页面，但数据为空/报错）
    200/400/405 -> 鉴权通过

背景（为什么会有 401 跳转）：
    Studio 的 axios 实例上挂了全局响应拦截器 —— 任何接口返回 401 或
    code:"UNAUTHENTICATED"，就把你强制送回 /studio/settings。
    而 /studio/settings 自己不调接口，所以只有它"打得开"。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ov import OV_HOME  # noqa: E402

# (页面, HTTP 方法, 路径, 请求体)
PAGES = [
    ("首页 / 工作台",           "GET",  "/api/v1/console/dashboard/summary", None),
    ("技能",                    "GET",  "/api/v1/skills", None),
    ("agent 经验-outcomes",     "GET",  "/api/v1/agent-evolution/experiences/outcomes", None),
    ("agent 经验-trajectories", "GET",  "/api/v1/agent-evolution/experiences/trajectories", None),
    ("检索",                    "POST", "/api/v1/search/search", {"query": "test", "limit": 3}),
    ("会话",                    "GET",  "/api/v1/sessions", None),
    ("监控",                    "GET",  "/api/v1/observer/system", None),
    ("任务中心",                "GET",  "/api/v1/tasks", None),
    ("请求日志",                "GET",  "/api/v1/console/tokens", None),
    ("用户管理",                "GET",  "/api/v1/admin/accounts/default/users", None),
    ("连接设置 /health",        "GET",  "/health", None),
]

OK_CODES = (200, 400, 405)


def make_caller(base: str):
    def call(path, key=None, method="GET", body=None):
        req = urllib.request.Request(base.rstrip("/") + path, method=method)
        req.add_header("Accept", "application/json")
        if key:
            req.add_header("X-API-Key", key)
            req.add_header("X-OpenViking-Account", "default")
            req.add_header("X-OpenViking-User", "default")
        data = None
        if body is not None:
            req.add_header("Content-Type", "application/json")
            data = json.dumps(body).encode()
        try:
            with urllib.request.urlopen(req, data=data, timeout=25) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code
        except Exception:  # noqa: BLE001
            return "ERR"

    return call


def main() -> int:
    ap = argparse.ArgumentParser(description="Studio 凭证自检")
    ap.add_argument("--base", default=None, help="服务地址，默认取 ovcli.conf 的 url")
    args = ap.parse_args()

    conf_path = OV_HOME / "ovcli.conf"
    if not conf_path.exists():
        raise SystemExit(f"缺少 {conf_path}")
    conf = json.loads(conf_path.read_text(encoding="utf-8"))
    root, user = conf.get("root_api_key"), conf.get("api_key")
    base = args.base or conf.get("url") or "http://127.0.0.1:1933"

    if not root or not user:
        print("⚠️ ovcli.conf 缺少 root_api_key / api_key —— 矩阵会不完整")
        print("   用户密钥：ov admin register-user default default → 回填 api_key\n")

    call = make_caller(base)
    print(f"base = {base}\n")
    print(f"{'页面':<26}{'无密钥':<9}{'仅管理密钥':<12}{'仅用户密钥':<12}结论")
    print("-" * 80)

    need_user = 0
    for name, method, path, body in PAGES:
        n = call(path, None, method, body)
        r = call(path, root, method, body)
        u = call(path, user, method, body) if user else "n/a"

        if n == 401 and r not in OK_CODES and u in OK_CODES:
            verdict = "需要【用户密钥】"
            need_user += 1
        elif n == 401 and r in OK_CODES and u not in OK_CODES:
            verdict = "需要【管理密钥】"
        elif r in OK_CODES and u in OK_CODES:
            verdict = "两种皆可"
        elif n == "ERR":
            verdict = "服务不可达"
        else:
            verdict = "检查服务状态"

        print(f"{name:<26}{str(n):<9}{str(r):<12}{str(u):<12}{verdict}")

    print()
    print("提示：")
    print("  401 = 页面会被强制跳回 /studio/settings（表现为'菜单点不进去'）")
    print("  403 = 能进页面但拿不到数据（表现为'打开了但空'）")
    print()
    if need_user:
        print(f"→ 有 {need_user} 个数据类页面需要【用户密钥】。")
        print("  去 Studio →「连接设置」把两把钥匙都填上（见 SOP P6）。")
        print("  省事技巧：先只填管理员密钥保存 → 用户管理 → 选中 default/default → 点「使用」自动回填。")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
