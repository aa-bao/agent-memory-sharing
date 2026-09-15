# SOP · 可复现搭建流程

> 目标：在一台干净的 Windows 机器上，从零搭起一个**多 agent 共享记忆库**，并保证它**在重启后依然活着**。
>
> 每个阶段都有 `验收` 子节。**验收不过，不要进入下一阶段。**
>
> 约定：`%OV%` = `%USERPROFILE%\.openviking`；所有命令默认在仓库根目录执行。
> PowerShell 里遇到"另一个程序已锁定文件"这类瞬时错误，**重试一次**即可。

---

## §0 先做两个决策（10 分钟，省掉后面几小时）

### 决策 1：记忆按什么分区？（peer 策略）

peer 是记忆的分区键。**选错了会导致"装了但互相看不见"——而且现象是静默无召回，最难排查。**

| 方案 | 效果 | 怎么配 |
|---|---|---|
| **A. 全局共享**（多 agent 互通的首选） | 所有 agent、所有项目共用一份记忆 | **什么都不用做**。不在 git 仓库里跑 ⇒ 自动落到 user 级空间 |
| B. 按仓库分区 | 不同仓库记忆互不串味 | 在仓库根放 `%OV%\..\..\.openviking\config.json`：`{"version":1,"peer":{"id":"my-project"}}` |
| C. 精确指定 | 多工作区场景强制归属 | 设环境变量 `OPENVIKING_PEER_ID` |

> ⚠️ **本仓库的默认假设是 A（全局共享）**，因为目标是"互通"。
> 如果各 agent 的 peer 推导结果不一致，会出现"A 写的 B 读不到"。用 `ov doctor` 或 `scripts/ov-doctor.py` 核对 peer。

**防串味**（想共享又要限制召回范围）：`OPENVIKING_RECALL_PEER_SCOPE=actor`。

### 决策 2：用哪个模型服务？

硬约束三条，先记死：

1. **必须有 embedding 接口。** 只有 chat 的服务（典型：DeepSeek）**直接出局**。
2. **抽取质量由 VLM 决定。** 官方明确：小于 4B 的 VLM 做不好记忆抽取——它们会把提示词里的 few-shot 示例**照抄成伪造的记忆**。
3. **非火山引擎后端一般没有 rerank**。没有 rerank 会自动降级用向量分，仍可用、质量下降。

推荐配置（本仓库实测可用，免费档够个人用）：

| 槽位 | 模型 | 说明 |
|---|---|---|
| embedding | `BAAI/bge-m3` | 1024 维，免费。`provider: openai` 时 OV **不发送** `dimensions` 参数，正好绕开 bge-m3 拒绝该参数的问题 |
| rerank | `BAAI/bge-reranker-v2-m3` | `api_base` 必须是**完整端点** `.../v1/rerank`（单数 rerank） |
| vlm | `Qwen/Qwen3-VL-30B-A3B-Instruct` | 30B MoE / 3B 激活，便宜且远超 4B 能力下限 |

完整选型对比与额度限制见 [`docs/decisions.md`](docs/decisions.md)。

---

## P0 · 环境前置检查

**目的**：确认基础件齐备，避免装到一半才发现缺东西。

```powershell
python --version          # 需要 >= 3.10
uv --version
where.exe ov              # 现在应该找不到，装完 P1 才有
Test-NetConnection api.siliconflow.cn -Port 443   # 或你自己的 provider 域名
```

**验收**

- [ ] Python ≥ 3.10
- [ ] `uv` 可用
- [ ] 能连通 provider 域名

**失败**

| 现象 | 处理 |
|---|---|
| 没有 uv | `powershell -c "irm https://astral.sh/uv/install.ps1 \| iex"` |
| provider 域名连不通 | 需要代理 → 设 `HTTPS_PROXY` / `HTTP_PROXY`（注意：**要给服务进程也能读到**） |

---

## P1 · 安装 OpenViking

```powershell
uv tool install openviking
ov --version
openviking-server --version
```

**验收**

- [ ] `ov --version` 打印版本号
- [ ] `openviking-server --version` 打印版本号
- [ ] 可执行文件落在 `%APPDATA%\uv\tools\openviking\Scripts\`

**注意**

- 官方 `install.sh` **硬编码只支持 macOS / Linux**（`exit 1`，无绕过开关）。Windows 上**不要**去跑它，走 `uv tool install` + 各 agent 自己的插件管理器。
- 升级用 `uv tool install openviking --upgrade`（或 `--reinstall`）。**升级前先 `ovpack` 备份**。

---

## P2 · 配置 provider 与凭据

### P2.1 把 key 放进环境变量，不要放进配置文件

```powershell
setx SILICONFLOW_KEY "sk-你的key"
# setx 只影响新进程；当前会话临时生效：
$env:SILICONFLOW_KEY = "sk-你的key"
```

> ⚠️ 配置文件常被分享、备份、截图。用 `${SILICONFLOW_KEY}` 引用环境变量是**唯一推荐做法**。

### P2.2 写服务端配置 `%OV%\ov.conf`

从 [`config/ov.conf.example`](config/ov.conf.example) 复制，重点确认这几项：

```jsonc
{
  "embedding": { "dense": { "provider": "openai", "api_key": "${SILICONFLOW_KEY}",
                            "model": "BAAI/bge-m3", "dimension": 1024 } },
  "rerank":    { "provider": "openai", "api_base": "https://api.siliconflow.cn/v1/rerank",
                 "api_key": "${SILICONFLOW_KEY}", "model": "BAAI/bge-reranker-v2-m3" },
  "vlm":       { "provider": "openai", "api_key": "${SILICONFLOW_KEY}",
                 "model": "Qwen/Qwen3-VL-30B-A3B-Instruct" },

  "server": {
    "host": "127.0.0.1", "port": 1933,
    "root_api_key": "换成一串你自己的随机字符串",
    "agent_evolution": { "enabled": true }        // ← 默认 false，不改就永远抽不出经验类记忆
  },

  "memory": { "extraction_output_format": "json" } // ← 默认 "python"，不改基本等于零抽取
}
```

**这两个开关是整个搭建过程中收益最高的两行：**

| 配置 | 默认值 | 不改的后果 |
|---|---|---|
| `memory.extraction_output_format` | `"python"` | 抽取协议的模型输出契约是受限 Python 赋值 DSL，第三方模型 4 轮重试全部 `parse_error` ⇒ `memories_extracted = {}`。实测同一份会话：**改 json 前 0 条，改后 19 条** |
| `server.agent_evolution.enabled` | `false` | `experiences` / `trajectories` / `cases` 三类"agent 阶段记忆"永远为空 |

### P2.3 写客户端配置 `%OV%\ovcli.conf`

从 [`config/ovcli.conf.example`](config/ovcli.conf.example) 复制：

```json
{ "url": "http://127.0.0.1:1933", "root_api_key": "与 ov.conf 里一致", "api_key": "留空，P4 签发后回填" }
```

写完后收紧权限（防其他账户读取）：

```powershell
icacls "$env:USERPROFILE\.openviking\ovcli.conf" /inheritance:r /grant:r "$env:USERNAME:F"
```

### P2.4 关于"两把钥匙"（必须理解，否则 P6 会卡死）

| 钥匙 | 存哪 | 权限 |
|---|---|---|
| `root_api_key` | `ov.conf` → `server.root_api_key` | **只有管理权限**。拿它调数据面接口 → **403** |
| 用户 `api_key` | `ovcli.conf` → `api_key` | 数据面（记忆读写、检索、会话）。用 `ov admin register-user` 签发 |

**验收**

- [ ] `%OV%\ov.conf` 存在且是合法 JSON（`python -m json.tool` 过一遍）
- [ ] `%OV%\ovcli.conf` 存在
- [ ] `SILICONFLOW_KEY` 在新终端里 `echo $env:SILICONFLOW_KEY` 有值

---

## P3 · 打补丁并启动服务

**这是整个流程最容易失败的一步，也是本仓库存在的主要理由。** 三个补丁，按顺序做。

### P3.1 清理 WorkBuddy 注入的 shim（如果宿主机是 WorkBuddy）

WorkBuddy 会向子进程注入两层删除拦截：

| 注入项 | 内容 | 劫持对象 |
|---|---|---|
| `PYTHONPATH` | `...\cli\vendor\shim`（含 `sitecustomize.py`） | Python 的 `os.remove` |
| `NODE_OPTIONS` | `--require=.../node-language-shim.cjs` | Node 的 `fs.unlink` |

服务启动时要清理陈旧的 RocksDB / pid 锁文件，这一步被拦截后直接 `SystemExit(1)`，现象是：

```
Application startup failed. Exiting.
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":199,"threshold":50,...}
```

**修复**（已固化在 `scripts/start-ov.sh` / `scripts/start-ov.cmd`）：

```bash
unset NODE_OPTIONS; unset PYTHONPATH; unset PYTHONSTARTUP
export CODEBUDDY_SAFE_DELETE_ENABLED=0
```

> ⚠️ **`unset NODE_OPTIONS` 单独用没用** —— Python 侧是 `sitecustomize.py` 经 `PYTHONPATH` 全局注入的，必须同时清 `PYTHONPATH`。

### P3.2 修 Studio 空白页（`mimetypes` 补丁）

Windows 上 Python 的 `mimetypes` 会读注册表的文件关联，本机 `.js` 被登记成 `text/plain`：

```
mimetypes.guess_type('a.js')  ->  ('text/plain', None)   # 灾难
```

Studio 入口是 ES Module（`<script type="module">`），浏览器对 ES Module 有**强制 MIME 校验**——
MIME 不是 JavaScript 类型就**拒绝执行**，于是 `<div id="app">` 永远为空，**整页纯白且不报任何错**。

```powershell
python .\scripts\apply-mime-fix.py
```

它会在 site-packages 放一个 `.pth`，解释器启动时自动纠正映射（**不动注册表**，也不改 OV 源码）。

> ⚠️ `mimetypes` 只在进程启动时初始化一次 → **改完必须重启服务**。

### P3.3 启动

```powershell
$env:CODEBUDDY_SAFE_DELETE_ENABLED=0
.\scripts\start-ov.cmd            # 双击也行；输出写到 %OV%\logs\server-launch.log
```

> ⚠️ **不要用 `scripts/start-ov.sh` 长期跑** —— 那样起的进程会挂在当前 shell 会话上，会话一关服务就没了。它只用于排障。长期运行走 **P7 的计划任务**。

**验收**

```powershell
ov health          # 期望 connected / healthy
ov status          # 期望 queue / vectordb / models / lock / retrieval / filesystem 全绿
ov config validate # 可能提示"未知(自定义)"，无害
```

- [ ] 1933 端口在监听：`Get-NetTCPConnection -LocalPort 1933 -State Listen`
- [ ] `ov health` 通过
- [ ] `%OV%\logs\server-launch.log` 里没有 `Application startup failed`

**失败** → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) §A 服务起不来

---

## P4 · 验证记忆闭环（写 → 抽取 → 召回）

**目的**：证明"提交会话 → VLM 抽取 → 落库 → 召回"这条链路真的通了。
**这一步不做，你会以为它在记，其实什么都没发生。**

### P4.1 签发数据面用户密钥

```powershell
ov admin register-user default default
```

把输出的 `api_key` 回填进 `%OV%\ovcli.conf` 的 `api_key` 字段。

### P4.2 提交一条会话，触发抽取

```powershell
python .\scripts\ov-commit-zh.py .\examples\session-sample.json --wait
```

`ov-commit-zh.py` 做三件事：`session new` → `add-messages` → `commit` → 轮询 → 打印 `memory_diff` 摘要。

> **为什么必须用脚本而不是直接 `ov session add-messages <中文>`**：
> Git Bash 在 Windows 下按本地代码页（中文系统 = GBK）转换命令行参数和管道内容，中文传给 `ov.exe` 会变乱码，
> 表现为 OpenViking 侧解析失败。Python 3 的 `subprocess` 在 Windows 上走 `CreateProcessW`（宽字符 API），中文无损。

**验收**

- [ ] `task status` 里 `memories_extracted` **不是空对象**
- [ ] 打印出的 `### adds = N`（N > 0）
- [ ] `ov ls viking://~/memories` 能看到新增文件

### P4.3 ⚠️ 构造验证会话的铁律

> **绝对不要用"记忆里的背景信息"去构造验证会话。**

VLM 无从判断真伪，它的职责就是忠实抽取。拿未核实的旧上下文当"用户陈述"喂进去，你会得到
**自信、被索引、被召回、还带 `viking://` 引用**的错误事实——**比没有记忆更糟**。

- 只用**实测值**（`$env:COMPUTERNAME`、`whoami`、`Get-NetIPAddress`），或明确标注为测试数据的内容。
- **发现一条假记忆，必须审计同批写入的全部条目** —— 它们来自同一个未核实来源。
- 修正要**删除 + 用正确的 key 重建**，不要原地改正文：URI 里嵌了 key，只改正文会留下一个"说谎的文件名"。
- 修完必须走**召回路径**验证，不能只 `ov read`（检索可能命中残留副本）。

### P4.4 一键体检

```powershell
python .\scripts\ov-doctor.py
```

逐项输出：服务连通性 / 组件健康 / 模型调用 / 抽取结果 / 记忆落盘 / 召回命中 / Studio 鉴权矩阵 / peer 解析。

**验收**

- [ ] `ov-doctor.py` 全项 OK

**失败** → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) §B 抽取不到记忆

---

## P5 · 接入 agent

### P5.1 通用原则

五个 agent 的**会话载体极度异构**（明文 jsonl / zstd jsonl / SQLite / JSON 快照），
**任何"自研脚本解析各 agent 会话"的方案都会陷入格式泥潭**。

全机只有两个真正的收敛点：**MCP** 和**生命周期事件**（`SessionStart` / `UserPromptSubmit` / `Stop`）。
所以接入一律走：**各 agent 官方插件的 hook + 统一 stdio MCP proxy**。

**一套凭据驱动全部 agent**，凭据链优先级：

```
OPENVIKING_* 环境变量  →  %OV%\ovcli.conf  →  %OV%\ov.conf  →  内置默认值
```

### P5.2 逐个接入

| Agent | 接入方式 | 备注 |
|---|---|---|
| **Claude Code** | `claude plugin install openviking`（marketplace） | 9 个 hook，召回后有本地再摘要，**实际注入 token 最低** |
| **Codex** | 部分构建**没有 `plugin add` 子命令** → 手动装：复制插件到 `%USERPROFILE%\.codex\plugins\openviking-memory`，跑 `python .\scripts\make-codex-hooks.py` 生成 `hooks.json`，并在 `config.toml` 注册 MCP | 需 `[features] plugin_hooks = true` |
| **dsh** | `dsh plugin --profile web add ...`（**先清 shim**） | **同进程 Cordis 插件，最稳的接入形态**：贴着会话走、注入对压缩可见、有待写队列 |
| **ZCode** | `python .\scripts\install-zcode.py` | 手动复刻官方安装器落点（官方不支持 Windows） |
| **opencode** | npm 插件 `@openviking/opencode-plugin` | 7 个 plugin hook |

Codex 的 `config.toml` 需要：

```toml
[features]
plugin_hooks = true

[mcp_servers.openviking-memory]
command = "node"
args = ["C:/Users/<you>/.codex/plugins/openviking-memory/servers/mcp-proxy.mjs"]
cwd = "C:/Users/<you>/.codex/plugins/openviking-memory"

[plugins."openviking-memory@openviking"]
enabled = true
```

### P5.3 验收（跨 agent 召回 —— 这才是"互通"的定义）

- [ ] 在 **agent A** 里说一条偏好
- [ ] 在 **agent B** 里提问，能被召回

**注意**：hook 只在 **agent 启动时载入**。装完之后**必须重启对应 agent** 才有真实会话召回——
手工调脚本验证成功 ≠ 真实 hook 生效。

**失败** → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) §D 召回为空

---

## P6 · Studio 可用性（双密钥）

**症状**：Studio 能打开，但点左侧「工作台 / 检索 / 技能 / agent 经验 / 会话」，
URL 立刻被改成 `/studio/settings`，页面进不去。服务端一切正常（`/studio/*` 全 200）。

**根因**（从前端 bundle 读出来的，不是猜的）：Studio 装了**全局 axios 响应拦截器**——
任何接口只要返回 `401` 或 `code:"UNAUTHENTICATED"`，就把你强制送回 `/studio/settings`。
而 `/studio/settings` 自己不调接口，所以只有它"打得开"。

Studio 是**纯前端应用**，凭据存在浏览器 `localStorage`，服务端不注入任何凭证。

### 修复：在 Studio「连接设置」里把**两把钥匙**都填对

| 字段 | 值 |
|---|---|
| 服务地址 | `http://127.0.0.1:1933` |
| 账号 / 用户 | `default` / `default` |
| 管理员 API 密钥 | `ov.conf` 里的 `server.root_api_key` |
| 用户 API 密钥 | `ovcli.conf` 里的 `api_key` |

> **省事技巧**：只填管理员密钥保存 → 进「用户管理」→ 选中 `default / default` → 点「使用」，
> 会自动把 108 字符的用户密钥回填到字段里，不用手抄。

**必须区分两种失败**：

| 状态码 | 现象 | 含义 |
|---|---|---|
| **401** | 页面**被弹回** settings | 完全没凭据 |
| **403** | 页面**能打开但数据为空** | 密钥角色不对（比如只填了 root） |

**验收**

```powershell
python .\scripts\check-studio-auth.py     # 输出 401/403/200 权限矩阵 + 结论
```

- [ ] 数据类页面（技能 / 检索 / 会话 / agent 经验）均为 `200`

> ⚠️ **不要为了省事把 `auth_mode` 切 `dev`。** 那会关掉整个服务的鉴权，还可能破坏 agent 插件依赖
> 用户密钥做 peer 分区的身份解析。填两把钥匙的代价只是浏览器里填一次。

**失败** → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) §E Studio

---

## P7 · 开机自启（**必做，不是可选**）

> **这是整套流程里最高价值的一步。**
> 手动起的服务会跟着 shell 会话一起死。而服务一旦死掉，**没有任何地方会报错**——
> agent 的 hook 会优雅降级，安静地停止召回和写入。**一切看起来都正常，只有记忆死了。**

### P7.1 启动器说明

本仓库有两个启动器：

- `scripts/launch-hidden.ps1`（**计划任务默认用它**）：用 PowerShell 以 `-WindowStyle Hidden` 调起 server，桌面**不会弹小黑窗**；日志写到 `$OPENVIKING_HOME/logs/server-launch.log`。
- `scripts/start-ov.cmd`：前台启动器，方便你手动双击调试时看到实时输出。它刻意不用 `pause`（无窗口模式下会挂死），也不在日志里用 `%date%`（中文 Windows 批处理代码页是 GBK，星期几会乱码，用 `%time%`）。

两种启动器都会清掉 WorkBuddy 注入的 shim 环境变量、并解析 `SILICONFLOW_KEY`（进程环境变量优先，其次同目录 `.env`）。

### P7.2 注册登录时计划任务

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register-autostart.ps1
```

关键参数（**都不需要管理员权限，也不需要保存密码**）：

| 项 | 值 | 为什么 |
|---|---|---|
| 触发器 | `AtLogOn`（当前用户） | 数据在用户目录，且需要用户的 `SILICONFLOW_KEY` |
| 运行级别 | `Limited` | 不需要提权 ⇒ 注册时不用 UAC |
| 登录类型 | `Interactive` | 免存密码 |
| **执行时间上限** | **`PT0S`（无限制）** | ⚠️ **默认是 3 天**，到点被系统强杀。常驻服务必须设 0 |
| 多实例策略 | `IgnoreNew` | 防重复启动抢 1933 端口 |

### P7.3 验收：确认进程真的独立了

```powershell
python .\scripts\ov-doctor.py --parent-chain
```

健康的父链末端应该是 **Task Scheduler 的 `svchost.exe`**，而不是 `bash.exe`：

```
python.exe            ← 监听 1933（无可见窗口，MainWindowHandle=0）
  ↑ openviking-server.exe
  ↑ powershell.exe    ← launch-hidden.ps1，-WindowStyle Hidden，无小黑窗
  ↑ svchost.exe       ← Task Scheduler 本体 ✅ 已脱离任何会话
```

- [ ] `Get-ScheduledTaskInfo -TaskName OpenVikingMemoryServer` → `LastTaskResult = 267009`（正在运行）或 `0`（正常）
- [ ] 父链末端是 `svchost.exe`，且 server 进程 `MainWindowHandle = 0`（没有弹出控制台窗口）
- [ ] **重启机器后 `ov health` 自动通过**（终极验收）

> 计划任务**只在登录时触发**，杀掉进程后不会自动重启——这是**有意设计**的，否则你没法手动停服务。
> 恢复用 `Start-ScheduledTask -TaskName OpenVikingMemoryServer`。

**失败** → [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) §F 服务跑几天就死

---

## P8 · 运维、审计与备份

### 日常自检

```powershell
ov health                 # 连通性 + 认证
ov status                 # 组件健康 + 向量数
ov observer models        # 模型调用统计与 token 花费
ov observer queue         # 队列积压
python .\scripts\ov-doctor.py    # 上面的合体 + 抽取/召回/Studio/peer
```

> 所有 `ov` 命令在 WorkBuddy 环境下建议前置 `$env:CODEBUDDY_SAFE_DELETE_ENABLED=0`，避免 shim 干扰。

### 排查"抽取不生效"

1. 看任务：`ov task status <task_id> --output json` → `memories_extracted` 是否为空
2. **看服务端 stdout**（不是日志文件，OV 默认打到 stdout）→ 搜 `extract_loop` / `parse_error`
3. 读 diff：`ov read viking://user/default/sessions/<sid>/history/archive_001/memory_diff.json`
4. 查现存记忆：`ov ls viking://~/memories`

> `before == after` 表示模型被告知要编辑、但产出零实质内容（典型是 VLM 太小）。

### 审计记忆内容

```powershell
python .\scripts\ov-audit-tree.py viking://user/default/memories/entities
python .\scripts\ov-audit-dir.py  viking://user/default/memories/events/2026/09/15
```

### 备份与迁移

用官方 `ovpack`（整库快照），**不要手工拷 AGFS 目录**：

```
export_ovpack / import_ovpack / backup_ovpack / restore_ovpack
```

一致性巡检：`POST /api/v1/system/consistency`（校验文件系统 ↔ 向量库一致性）。

### 可调参数（按需打开，默认关）

`ov.conf` 的 `memory` 段：

| 参数 | 默认 | 说明 |
|---|---|---|
| `extraction_enabled` | true | 总开关；关掉则只归档不抽取 |
| `session_skill_extraction_enabled` | false | commit 时顺带抽取可复用技能 |
| `link_enabled` | false | 记忆之间的关联链接抽取 |
| `eager_prefetch` | true | 预加载全部记忆内容（不向 LLM 暴露 read/search 工具） |
| `prefetch_search_topn` | 5 | 预取读取的检索结果条数 |
| `experimental_memory_switch` | false | 加载实验性模板 |

---

## 附录 A · 全流程速查

```
P0  python/uv/网络          → 验收：三件齐
P1  uv tool install         → 验收：ov --version
P2  ov.conf + ovcli.conf    → 验收：JSON 合法 + KEY 在环境变量
      ★ extraction_output_format = "json"
      ★ agent_evolution.enabled  = true
P3  shim 清理 + MIME 补丁 + 启动 → 验收：ov health
P4  签发用户密钥 + 提交会话  → 验收：memories_extracted 非空
P5  各 agent 挂 hook/MCP    → 验收：跨 agent 召回成功
P6  Studio 双密钥           → 验收：check-studio-auth 全 200
P7  计划任务自启（必做）     → 验收：重启后 ov health 自动通过
P8  自检 / 审计 / ovpack    → 长期
```

## 附录 B · 本机关键路径

```
%OV%                                   = %USERPROFILE%\.openviking
%OV%\ov.conf                            服务端配置（模型 / 存储 / memory 开关）
%OV%\ovcli.conf                         客户端连接与凭证
%OV%\data\                              数据目录（agfs / vectordb）
%OV%\logs\server-launch.log             启动器日志（启动时间 / 退出码）
%OV%\logs\codex-hooks.log               记忆 hook 日志（召回诊断，需 OPENVIKING_DEBUG=1）
<uv-tools>\openviking\Scripts\          ov.exe / openviking-server.exe
<uv-tools>\openviking\Lib\site-packages\zz_openviking_mime_fix.pth    MIME 补丁
```
