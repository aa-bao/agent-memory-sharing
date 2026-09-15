# AGENTS.md

> 给自动化 agent 的常驻约定。人类读者请走 [`README.md`](README.md)。

## 这个仓库是干什么的

在多 agent 之间共享长期记忆。底座是 OpenViking（本地 HTTP 服务 + stdio MCP proxy）。
本仓库的价值不在"介绍 OpenViking"，而在**把它在 Windows 上真正跑通、跑稳、可复现**。

## 你要执行什么

| 任务 | 入口 |
|---|---|
| 从零搭起来 | [`SOP.md`](SOP.md) —— 按 P0 → P8 顺序执行，**每阶段验收通过才继续** |
| 出错了 | [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) —— 按症状查表 |
| 理解了再动 | [`docs/architecture.md`](docs/architecture.md) · [`docs/decisions.md`](docs/decisions.md) |

## 硬规则（违反会静默失败）

1. **启动服务前必须清环境**：`NODE_OPTIONS`、`PYTHONPATH`、`PYTHONSTARTUP` 全 unset，
   并设 `CODEBUDDY_SAFE_DELETE_ENABLED=0`。不要内联敲，用 `scripts/start-ov.*`。
2. **`ov.conf` 有两行不能少**：
   - `memory.extraction_output_format = "json"`（默认 `python` ⇒ 几乎零抽取）
   - `server.agent_evolution.enabled = true`（默认 `false` ⇒ 无经验类记忆）
3. **改完 `ov.conf` 必须重启服务**，没有热重载。
4. **不要把 `rm` 和服务启动放在同一个工具调用里。**
5. **不要用未核实的、来自"记忆/上下文"的信息去构造验证会话。** 见 TROUBLESHOOTING §I。
6. **不要把密钥写进配置文件。** 用 `${SILICONFLOW_KEY}` 环境变量引用。
7. **中文不要经 Git Bash 命令行 / 管道传递。** 用 `scripts/ov-commit-zh.py` 或 UTF-8 stdin 文件。
8. **不要切 `auth_mode: dev` 图省事。** 会关掉鉴权并破坏 agent 插件的 peer 解析。

## 判断"是否真的通了"

不要以"命令没报错"为准。用可执行验收：

```powershell
ov health
ov status
python .\scripts\ov-doctor.py
```

**这套系统最危险的失败模式是静默失败**：服务死了、抽取返回空、召回返回 `{}`，全都不报错。

## 环境探测规则

脚本通过 `scripts/_ov.py` 自动探测，不要硬编码路径：

| 目标 | 探测顺序 |
|---|---|
| `ov` / `openviking-server` | `$OV_BIN` → `PATH` → `%APPDATA%\uv\tools\openviking\Scripts\` |
| OpenViking 配置目录 | `$OPENVIKING_HOME` → `%USERPROFILE%\.openviking` |
| 仓库根 | 脚本自身位置的上两级 |

## 输出约定

- 汇报时**结论先行**，状态分条列明细，对比类信息出表格，技术风险显式标注意。
- **不要声称"已完成"而没有验收证据**。贴上 `ov health` / `ov status` / `ov-doctor.py` 的实际输出。
