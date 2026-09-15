# agent-memory-sharing

**让多个 coding agent 共用同一份长期记忆。** 一次搭建，本机所有 agent（Claude Code / Codex / dsh / ZCode / opencode …）共享同一个记忆库：A 里说过的偏好，B 里立刻知道；昨天踩过的坑，今天不会再踩。

> 这是给 **AI agent 阅读并执行** 的仓库。核心是 [`SOP.md`](SOP.md) —— 一份分阶段、可复现、每步都带验收命令的搭建流程。
> 如果你是 AI agent，直接跳到 [§ 给 Agent 的执行指令](#给-agent-的执行指令)。

---

## 1. 它解决什么问题

| 没有它 | 有了它 |
|---|---|
| 每个 agent 各自记各自的，换个工具就得重新交代一遍背景 | 一份记忆，五个 agent 同时读写 |
| 偏好/约定/踩坑只存在于某次会话的上下文里，会话一关就没了 | 自动抽取成结构化记忆，跨会话、跨项目、跨 agent 长期存活 |
| 每次开场都要重新解释项目背景，token 白烧 | 开场自动注入画像 + 召回相关记忆，token 反而下降 |
| 你以为它在记，其实服务早挂了 —— 而且**不会有任何报错** | 计划任务常驻 + 健康自检，挂了能立刻发现 |

底座是 [OpenViking](https://github.com/volcengine/OpenViking)（Agent 原生上下文数据库，AGPL-3.0），
agent 通过 **HTTP API + stdio MCP proxy** 接入。本仓库负责的是**把它在 Windows 上真正跑通、跑稳、可复现**——
官方安装器不支持 Windows，且 Windows 上有一批会让服务"静默失效"的坑（详见 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)）。

---

## 2. 最终形态

```
┌───────────────────────────────────────────────────────────────┐
│  Agent 层                                                      │
│  Claude Code │ Codex │ dsh │ ZCode │ opencode │ …             │
│  各自的 hook / plugin 上报会话 ─────────────┐                   │
└────────────────────────────────────────────┼───────────────────┘
                                             │ 统一凭据链
                                             │ OPENVIKING_* → ovcli.conf → ov.conf
┌────────────────────────────────────────────▼───────────────────┐
│  接入层（全部官方现成插件，零自研）                              │
│  hooks（SessionStart / UserPromptSubmit / Stop / SessionEnd）   │
│  + 统一 stdio MCP proxy（15 个工具：find/search/read/remember…） │
└────────────────────────────────────────────┬───────────────────┘
                                             │ HTTP 127.0.0.1:1933
┌────────────────────────────────────────────▼───────────────────┐
│  OpenViking Server（openviking-server）                        │
│  Retrieve 意图分析 / 层级检索 / Rerank                          │
│  Session  归档 / 压缩 / 记忆提交                                 │
│  Compressor  Schema 驱动抽取 / LLM 去重决策                      │
│  Storage   AGFS 内容(本地) + 向量库 索引(本地)                    │
└────────────────────────────────────────────┬───────────────────┘
                                             │
┌────────────────────────────────────────────▼───────────────────┐
│  Provider 层（示例用硅基流动，可换任意 OpenAI 兼容端点）           │
│  embedding: BAAI/bge-m3 (1024d, 免费)                          │
│  rerank   : BAAI/bge-reranker-v2-m3 (免费)                     │
│  vlm      : Qwen/Qwen3-VL-30B-A3B-Instruct ← 记忆抽取靠它       │
└────────────────────────────────────────────────────────────────┘
```

**存储不跨 agent 做点对点同步，只做「中心化单一真相源」。** 各 agent 的会话格式（明文 jsonl / zstd / SQLite）无法无损互转，点对点同步是 N² 复杂度且必然丢信息。统一写入一个中枢才是可行解。

---

## 3. 前置要求

| 项 | 要求 | 检查命令 |
|---|---|---|
| 操作系统 | Windows 10 / 11（思路同样适用于 macOS / Linux，但本仓库的坑位都以 Windows 为准） | `winver` |
| Python | ≥ 3.10 | `python --version` |
| uv | 任意近期版本 | `uv --version` |
| 一个 OpenAI 兼容的模型服务 | 必须同时提供 **embedding** + **chat(VLM)**；有 rerank 更好 | 见 [`docs/decisions.md`](docs/decisions.md) |
| 网络 | 能访问该模型服务；如需访问 GitHub，准备好代理 | — |
| 磁盘 | 约 2 GB（服务 + 向量库 + 记忆数据） | — |

> ⚠️ **模型是关键依赖，不是可选项。** DeepSeek 这类"只有 chat、没有 embedding"的通道**不可用**。
> 具体选型与免费额度限制见 [`docs/decisions.md`](docs/decisions.md)。

---

## 4. 快速开始（Happy Path）

完整版在 [`SOP.md`](SOP.md)，这里是浓缩版。**每一步都有验收命令，验收不过不要往下走。**

```powershell
# ── 步骤 1  安装 ──────────────────────────────────────────────
uv tool install openviking
ov --version
# 期望：打印版本号。失败 → TROUBLESHOOTING "ov 命令不存在"

# ── 步骤 2  配置 provider key（不要写进配置文件）─────────────
setx SILICONFLOW_KEY "sk-你的key"      # 之后需新开终端才生效

# ── 步骤 3  写配置 ────────────────────────────────────────────
#   把 config/ov.conf.example 复制到 %USERPROFILE%\.openviking\ov.conf
#   把 config/ovcli.conf.example 复制到 %USERPROFILE%\.openviking\ovcli.conf
#   并把 root_api_key 换成一串你自己的随机字符串

# ── 步骤 4  起服务（必须先清 WorkBuddy 注入的 shim！）─────────
$env:CODEBUDDY_SAFE_DELETE_ENABLED=0
.\scripts\start-ov.cmd

# ── 步骤 5  验收：服务起来了 ──────────────────────────────────
ov health          # 期望 connected / healthy
ov status          # 期望各组件全绿

# ── 步骤 6  签发数据面用户密钥 ────────────────────────────────
ov admin register-user default default      # 把输出的 api_key 写进 ovcli.conf

# ── 步骤 7  验收：记忆闭环（写 → 抽取 → 召回）────────────────
python .\scripts\ov-doctor.py          # 一键体检，会逐项报 OK/FAIL
```

七个步骤全绿之后，进入 [`SOP.md`](SOP.md) 的 **P5 接入 agent**，把各 agent 的 hook 挂上。

---

## 5. 给 Agent 的执行指令

> **如果你是一个 AI agent，请按以下方式使用本仓库。**

### 5.1 你要做的事

1. **先读 [`SOP.md`](SOP.md) 全文再动手**，不要凭印象跳步。
2. 按 **P0 → P8 顺序执行**，每个阶段的 `验收` 子节都有可执行命令。
3. **验收失败就停下来修**，去 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) 按症状查表。不要"先跳过、以后再修"——这些坑里有一半是**静默失败**，跳过等于埋雷。
4. 遇到 Windows 相关报错，优先查 TROUBLESHOOTING，那里的每一条都是实测踩出来的，不是猜测。

### 5.2 环境适配（本仓库已去硬编码）

脚本都通过 `scripts/_ov.py` 自动探测路径，不再写死用户名：

| 需要的东西 | 探测顺序 |
|---|---|
| `ov` / `openviking-server` 可执行文件 | 环境变量 `OV_BIN` → `PATH` 里的 `ov` → `%APPDATA%\uv\tools\openviking\Scripts\` |
| OpenViking 配置目录 | 环境变量 `OPENVIKING_HOME` → `%USERPROFILE%\.openviking` |
| 仓库根目录 | 脚本自身位置的上两级 |

所以整目录可以随意搬、随意改名，不会失效。

### 5.3 绝对不要做的事

| 禁止 | 原因 |
|---|---|
| ❌ 把 API key 写进 `ov.conf` 明文 | 用 `${SILICONFLOW_KEY}` 引用环境变量，配置文件常被分享/截图 |
| ❌ 把本仓库的 `.env` 提交上去 | 已在 `.gitignore`，但别绕过它 |
| ❌ 用"记忆里的背景信息"去构造验证会话 | 这是本仓库踩过最惨的一次坑 —— VLM 会把它当事实忠实抽取、永久固化、并附上引用让错误显得更可信。详见 TROUBLESHOOTING §假记忆污染 |
| ❌ 在同一个工具调用里既 `rm` 又启动服务 | safe-delete 守卫会先拦截删除、整条命令中止，服务根本没起来，而你以为它起了 |
| ❌ 为了让 Studio 能用就把 `auth_mode` 切 `dev` | 那会关掉整个服务的鉴权，还会破坏 agent 插件依赖的用户密钥身份解析 |

---

## 6. 仓库结构

```
agent-memory-sharing/
├── README.md                     ← 你在这里
├── SOP.md                        ★ 可复现搭建流程（P0-P8，每步带验收）
├── TROUBLESHOOTING.md            ★ 症状 → 根因 → 修复 决策树
├── AGENTS.md                     给 agent 的简短常驻约定
├── docs/
│   ├── architecture.md           分层架构与数据流
│   └── decisions.md              选型决策记录（为什么是这些模型 / 为什么不自研）
├── config/
│   ├── ov.conf.example           服务端配置模板
│   └── ovcli.conf.example        客户端连接与凭据模板
├── scripts/
│   ├── _ov.py                    公共库：路径探测 / shim 清理 / 调 ov
│   ├── install.ps1               一键安装（幂等）
│   ├── start-ov.cmd / .sh        启动器（含 shim 规避）
│   ├── register-autostart.ps1    注册登录自启计划任务
│   ├── ov-doctor.py              ★ 一键体检：连通性 / 模型 / 抽取 / 召回
│   ├── ov-commit-zh.py           安全提交中文会话，触发抽取
│   ├── ov-audit-tree.py          递归审计记忆内容
│   ├── ov-audit-dir.py           单层审计
│   ├── apply-mime-fix.py         修 Studio 空白页（MIME 补丁）
│   ├── enable-agent-evolution.py 打开 agent 阶段记忆抽取
│   ├── make-codex-hooks.py       为 Codex 生成 hooks.json
│   ├── install-zcode.py          手动安装 ZCode 集成（Windows 版）
│   └── check-studio-auth.py      Studio 双密钥权限矩阵自检
└── skills/
    └── openviking-windows-setup/SKILL.md   可加载的 agent 技能包
```

---

## 7. 验收清单

搭完之后，这 8 条必须全过。少一条都说明链路是断的：

- [ ] `ov health` → `connected` / healthy
- [ ] `ov status` → queue / vectordb / models / lock / retrieval / filesystem 全绿
- [ ] 提交一次会话后，`memories_extracted` **不是空对象**（空了 ⇒ 抽取协议或 VLM 能力不足）
- [ ] `ov ls viking://~/memories` 能看到实际落盘的记忆文件
- [ ] 召回路径（`auto-recall.mjs` 或 `ov find`）能命中刚写入的内容
- [ ] Studio（`http://127.0.0.1:1933/studio/`）能打开，左侧菜单**点得进去**（不弹回 settings）
- [ ] 服务进程父链末端是 Task Scheduler 的 `svchost.exe`，**不是** `bash.exe`
- [ ] 重启一次机器，`ov health` 无需手动操作即可通过 ← **最容易漏、也最致命的一条**

---

## 8. 相关文档

| 想了解 | 去哪 |
|---|---|
| 怎么一步步搭起来 | [`SOP.md`](SOP.md) |
| 报错了 / 现象不对 | [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) |
| 为什么这么设计、为什么选这些模型 | [`docs/decisions.md`](docs/decisions.md) |
| 内部数据流与分层 | [`docs/architecture.md`](docs/architecture.md) |

---

## 9. 说明

- 底座 OpenViking 采用 **AGPL-3.0**。个人本地自用无碍；**若要对外提供服务（SaaS / 分发）需先做许可证评估**。
- 本仓库内容为实操沉淀（Windows 环境下实测）。服务持续迭代，配置键位可能随版本漂移——升级前请先 `ovpack` 备份。
- 本仓库自身未附许可证文件，如需公开分发请自行补充。
