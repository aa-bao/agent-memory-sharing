# -*- coding: utf-8 -*-
"""修复 Studio 空白页 —— 在 OpenViking 的 site-packages 里放一个 MIME 补丁。

用法：
    python apply-mime-fix.py            # 安装补丁
    python apply-mime-fix.py --check    # 只检查当前映射与补丁是否存在
    python apply-mime-fix.py --remove   # 卸载补丁

背景
----
Studio 的入口是 ES Module：
    <script type="module" crossorigin src="/studio/assets/index-*.js"></script>

服务端用 Starlette 的 FileResponse(path) 投递静态资源且**未显式指定 media_type**，
于是 Starlette 调 mimetypes.guess_type() 推断类型。
而 Windows 上 Python 的 mimetypes **会读注册表**的文件关联 Content Type，
本机 `.js` 被登记成 text/plain：

    mimetypes.guess_type('a.js')  ->  ('text/plain', None)     # 实测

⚠️ 浏览器对 ES Module 有**强制 MIME 校验** —— MIME 不是 JavaScript 类型就拒绝执行该脚本。
   模块不执行 → <div id="app"> 永远为空 → **整页纯白，且不显示任何错误**。
   这解释了"为什么 CSS 正常、HTML 正常，却全白"。

修复方式
--------
在 venv 的 site-packages 放一个 .pth。Python 启动时会执行 .pth 里的 import 行，
从而在任何代码之前纠正映射。**不改 OpenViking 源码、不动注册表、不影响系统级文件关联。**

⚠️ mimetypes 只在进程启动时初始化一次 → **改完必须重启服务**。
"""

from __future__ import annotations

import argparse
import mimetypes
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _ov import find_exe, hr, ok, warn, fail  # noqa: E402

PTH_NAME = "zz_openviking_mime_fix.pth"
PTH_BODY = (
    "import mimetypes;"
    "mimetypes.add_type('text/javascript', '.js', strict=True);"
    "mimetypes.add_type('text/javascript', '.js', strict=False);"
    "mimetypes.add_type('text/javascript', '.mjs', strict=True);"
    "mimetypes.add_type('text/javascript', '.mjs', strict=False)"
)


def locate_site_packages() -> Path:
    """从 openviking-server 可执行文件反推 site-packages 目录。"""
    exe = Path(find_exe("server")).resolve()

    # Windows (uv tool): <tool>\Scripts\openviking-server.exe -> <tool>\Lib\site-packages
    # POSIX   (uv tool): <tool>/bin/openviking-server      -> <tool>/lib/pythonX.Y/site-packages
    tool_root = exe.parent.parent
    win_sp = tool_root / "Lib" / "site-packages"
    if win_sp.is_dir():
        return win_sp

    lib = tool_root / "lib"
    if lib.is_dir():
        for p in sorted(lib.glob("python*/site-packages")):
            return p

    # 兜底：跑一次解释器问它
    import json
    import subprocess

    py = exe.parent / ("python.exe" if os.name == "nt" else "python")
    if py.exists():
        out = subprocess.run(
            [str(py), "-c", "import sys,json;print(json.dumps(sys.path))"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
        ).stdout
        try:
            for entry in json.loads(out.strip()):
                if entry and Path(entry).is_dir() and "site-packages" in entry:
                    return Path(entry)
        except Exception:  # noqa: BLE001
            pass

    raise FileNotFoundError(f"无法定位 site-packages（server = {exe}）")


def check() -> int:
    hr("当前 Python 进程的 MIME 映射")
    for ext in (".js", ".mjs", ".css"):
        print(f"  guess_type('a{ext}') = {mimetypes.guess_type('a' + ext)}")

    hr("补丁状态")
    try:
        sp = locate_site_packages()
    except FileNotFoundError as e:
        fail(str(e))
        return 1
    print(f"  site-packages = {sp}")
    pth = sp / PTH_NAME
    if pth.exists():
        ok(f"{PTH_NAME} 已安装")
        print(f"     内容: {pth.read_text(encoding='utf-8').strip()[:120]}...")
    else:
        warn(f"{PTH_NAME} 未安装 → 运行 `python apply-mime-fix.py`")
    return 0


def install() -> int:
    hr("安装 MIME 补丁")
    sp = locate_site_packages()
    print(f"  site-packages = {sp}")

    pth = sp / PTH_NAME
    if pth.exists():
        ok(f"{PTH_NAME} 已存在，覆盖")
    pth.write_text(PTH_BODY + "\n", encoding="utf-8")
    ok(f"已写入 {pth}")

    print()
    print("  ⚠️ 必须重启服务才能生效（mimetypes 只在进程启动时初始化一次）")
    print("     验证： curl -sS -D - -o /dev/null http://127.0.0.1:1933/studio/assets/index-*.js")
    print("     期望： content-type: text/javascript; charset=utf-8")
    print("     ⚠️ 若之前访问过 Studio，浏览器可能注册了 Service Worker：")
    print("        用 Ctrl+Shift+R 硬刷新，或 DevTools → Application → Clear site data")
    return 0


def remove() -> int:
    hr("卸载 MIME 补丁")
    sp = locate_site_packages()
    pth = sp / PTH_NAME
    if pth.exists():
        pth.unlink()
        ok(f"已删除 {pth}（重启服务后生效）")
    else:
        warn("补丁本来就不存在")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="修复 Studio 空白页（MIME 补丁）")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--check", action="store_true", help="只检查")
    g.add_argument("--remove", action="store_true", help="卸载补丁")
    args = ap.parse_args()

    try:
        if args.check:
            return check()
        if args.remove:
            return remove()
        return install()
    except FileNotFoundError as e:
        fail(str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
