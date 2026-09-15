# 选型与决策记录

> 记录"为什么是现在这样"。每条都附证据来源，区分**实测**与**文档**。
> 若你要改这里的任何一个决定，请先读完对应条目的"推翻它需要什么证据"。

---

## 决策总览

| # | 决策 | 结论 | 依据 |
|---|---|---|---|
| D1 | 是否与已有的跨 agent 记忆系统共存 | **不共存，迁移到单一系统** | 实测：Hook 点冲突会导致 token 双注入 + 记忆分裂 |
| D2 | 模型 Provider | **OpenAI 兼容端点 + 免费 BAAI 系列 + 30B MoE VLM** | 端点探测 + 官方能力下限 |
| D3 | 是否自研 L3 编排层 | **不自研**，改声明式 YAML | 官方内置 schema 已覆盖 |
| D4 | 记忆分区（peer）策略 | **默认全局共享**，按需隔离 | 互通是目标；隔离是例外 |
| D5 | 抽取输出协议 | **`json`** | 实测 0 条 vs 19 条 |
| D6 | VLM 能力下限 | **≥ 4B，实操用 30B MoE** | 官方源码 + 实测失败模式 |

---

## D1 · 不要两套记忆系统并存

### 背景

一台机器上往往**已经**存在一套跨 agent 记忆系统（通过 hook / plugin 接进了若干 agent）。
新的记忆中枢与它**抢的是同一批 hook 点**。

### 冲突矩阵

| 冲突点 | 后果 |
|---|---|
| 同一 hook 点被两套系统占用（SessionStart / UserPromptSubmit / Stop） | **token 双倍注入**（两边都注入画像 + 召回） |
| 两边都 commit 会话 | **同一段上下文被存两份**，长期看存储与索引都膨胀 |
| 两边都有 agent 侧记忆技能 | 模型在"该查哪个记忆"上摇摆 |
| 两边都用 repo 维度分区 | 概念重叠（bank vs peer） |
| 两边的"修正机制"不同 | 用户要记两套纠正语法 |

### 结论

**先处理冲突，再上新的。** 三个选项：

| 选项 | 做法 | 代价 |
|---|---|---|
| 并行双写 | 两套都留 | ❌ token 双倍、记忆分裂，**负收益** |
| 硬切换 | 卸旧上新 | ⚠️ 一次性断档；旧系统的独有能力丢失 |
| 分层共存 | 各管一块 | ⚠️ 边界复杂，仍有重复 |
| **灰度对照**（推荐） | 只在**一个** agent 上接新系统，且设只读、零写入，并跑 1 周用数据决策 | 极小 |

**灰度技巧**：选**同进程插件形态**的 agent（如 dsh 的 Cordis 插件）试点 ——
它最稳、有离线待写队列，且设 `syncTurns: false` 时**天然零写入**，不会污染新库，随时可退。

### 卸载前必须先证明"零损失"

删除任何已有系统前，**先验证它到底存了多少数据**：

```powershell
Get-ChildItem -Recurse -Force "$env:USERPROFILE\.<system>\profiles"   # 数据目录是否为空？
Get-Process | Where-Object { $_.ProcessName -like "*node*" }          # daemon 是否在跑？
```

> 实测经验：某次迁移中，旧系统的 `profiles` 是**空目录**、daemon **未运行**、全盘无
> `.sqlite/.db/.jsonl`。**它从未积累过任何记忆** —— 删它不是"迁移"，是"清理一个空装置"。
> **"已接入"不等于"有数据"。** 先证明再动手。

### 回滚设计

**全程不要用 `rm`，一律"移出"到备份目录**，物理上可 100% 还原：

```
<backup>\<system>-YYYY-MM-DD\
├── <system>-home\        ← 原目录完整副本
├── <改动过的配置文件>      ← 原文
└── _removed\             ← 实际移出物（原件，非副本）
```

### 顺带的安全检查

迁移时顺手 grep 一遍被改动过的配置文件里的**明文密钥** ——
这类文件常被分享 / 备份 / 截图。发现即改为环境变量注入。

---

## D2 · 模型 Provider

### 硬约束

| # | 约束 | 说明 |
|---|---|---|
| 1 | **必须有 embedding 接口** | 只有 chat 的服务（典型：DeepSeek）**直接出局**，本地代理也一样 |
| 2 | **VLM 质量决定抽取质量** | 见 D6 |
| 3 | **非火山引擎后端一般无 rerank** | 会降级用向量分，仍可用但质量下降 |

### 端点探测（先于配置做）

配置前先探端点是否存在——**用 HTTP 状态码区分"路径错"和"缺凭证"**：

```bash
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://api.siliconflow.cn/v1/embeddings
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://api.siliconflow.cn/v1/rerank
curl -sS -o /dev/null -w "%{http_code}\n" -X POST https://api.siliconflow.cn/v1/chat/completions
```

| 返回 | 含义 |
|---|---|
| `401` | ✅ **路径正确**，只缺凭证 |
| `404` | ❌ 路径错了 |

> 实测三个端点全部 `401` ⇒ 路径全部正确。
> 注意 rerank 是**单数** `rerank`，不是 `reranks`。

### 推荐组合

| 槽位 | 模型 | 维度 / 上限 | 价格 | 说明 |
|---|---|---|---|---|
| **Embedding** | `BAAI/bge-m3` | 1024 固定 / 8192 tok | 免费 | 多语言，与 OV 默认 1024 维正好吻合 |
| **Rerank** | `BAAI/bge-reranker-v2-m3` | 8K | 免费 | 多语言精排，与 bge-m3 同源 |
| **VLM** | `Qwen/Qwen3-VL-30B-A3B-Instruct` | — | 便宜 | 30B MoE / 3B 激活，保留视觉能力 |

**备选**：embedding 可换 `BAAI/bge-large-zh-v1.5`（同样 1024 维、中文更强，但只有 512 tok 上限）。

### ⚠️ `dimension` 参数的陷阱

`BAAI/bge-m3` 是**固定 1024 维**模型，部分客户端反馈它**会对 `dimensions` 参数报错**（错误码 20015）。

**处置顺序**：

1. 先按推荐配置跑 —— `provider: "openai"` 时 OV **不发送** `dimensions`，通常直接绕开
2. 若报维度相关 400 → 移除 `"dimension": 1024` 再试
3. 仍失败 → 换 `BAAI/bge-large-zh-v1.5`

### `rerank.api_base` 必须是完整端点

```
✅ https://api.siliconflow.cn/v1/rerank
❌ https://api.siliconflow.cn/v1
```

### 免费额度的真实限制

免费档限额**固定、不随消费等级提升**，超限返回 HTTP 429：

| 类别 | RPM | TPM |
|---|---|---|
| Embedding | 2,000 – 10,000 | 500,000 – 10,000,000 |
| Rerank | ~2,000 | ~500,000 |
| Chat / VLM | 1,000 – 10,000 | 50,000 – 5,000,000 |

**评估：够用。** OV 的 embedding 调用是"写入时索引 + 召回时向量化查询"，个人多 agent 场景绰绰有余。

**但要正视两点**：① 免费档**无 SLA**，高峰可能排队在付费流量之后；② 免费模型**随时可能下架或转收费**。

**缓解**：主路径用免费额度，另配**本地 Ollama 作 `backup`** —— OV 的 provider 配置原生支持
`backup` 与 `failback_timeout_seconds`。免费额度被限流时，整个记忆链路不会瘫痪。

---

## D3 · 不自研 L3 —— 因为官方已经内置了

### 背景

早期方案设计了一个自研的"符号锚点记忆"层，包含两个核心构件：

- **符号锚点**：记忆不贴代码正文，只贴 `repo + 符号限定名 + commit sha`，召回时由代码图谱提供"当前真相"
- **Stale Detector**：比对 `verified_against` 与当前 HEAD，标记过期记忆

### 发现：官方内置 schema 已覆盖

OpenViking 内置了 12 类记忆 schema（`openviking/prompts/templates/memory/*.yaml`）。
其中三类**直接命中**上述设计：

| 自研提案里的构件 | 官方对应物 | 官方字段 | 判定 |
|---|---|---|---|
| 符号锚点 | `trajectories`（`stage: agent`，仅追加） | **`retrieval_anchor`** + `trajectory_name` + `outcome` | ✅ 官方版"锚点" |
| Stale Detector（记忆过期检测） | `experiences` | **`supersedes`**（记录"本经验替代了哪条旧记忆"） | ✅ 官方版"失效替代" |
| 代码符号实体 | `entities` | `category` + `name` + `content`，`merge_op` 可配 | ✅ 可承载 |
| 可评估决策案例 | `cases` | `task_signature` + `rubric` + `evidence` | ✅ 可承载 |

**官方描述原文**：

> `trajectories`：定义"任务轨迹中提炼出哪些可复用的操作/契约"这一类轨迹型记忆
> 关键字段：`trajectory_name`、`outcome`、**`retrieval_anchor`**、`content`

> `experiences`：记录持久的执行经验**及其替代的旧记忆**
> 关键字段：`experience_name`、`content`、**`supersedes`**

### 对比

| 维度 | 自研 L3 | 官方 schema + 自定义 YAML |
|---|---|---|
| 代码量 | 一个常驻服务 + 校验逻辑 | **0 行代码**，1 个 YAML |
| 升级维护 | 跟着 OV 版本改接口 | 官方向后兼容，声明式 |
| 记忆提取 | 自己写 prompt 调 LLM | 复用官方压缩 / 抽取管线 |
| 过期检测 | 自己实现 | 用 `experiences.supersedes` |
| 召回 | 自己拼图谱结果 | 复用官方意图分析 + 层级检索 + rerank |
| 与其他 12 类记忆协同 | 天然割裂 | 同一套 registry、同一套召回 |
| 失败模式 | 新增一个可挂掉的服务 | 无新增进程 |

### 结论：要做，但降级为"约定 + 声明"，三个零代码动作

1. **一个 YAML**：声明自定义记忆类型，放 `memory.custom_templates_dir`
2. **一段文本**：在各 agent 的 `AGENTS.md` / `CLAUDE.md` 里加约定——
   "涉及代码决策的记忆，只写 `repo + symbol + commit` 锚点，不贴代码正文"
3. **（可选）一处 prompt 微调**：在 `compression.memory_extraction` 模板里强化"该保留什么、禁止保留什么"

> ⚠️ 自定义 schema 时，`directory` / `filename_template` 里的变量名**必须**对照内置模板
> （如 `entities.yaml`）的同名写法，**不要自创**。官方把改 `directory` / `merge_op` 标为"很高风险"——
> **只加新类型，不改内置**。

### 推翻它需要什么证据

官方移除了 `retrieval_anchor` / `supersedes` 字段，或自定义 schema 机制被废弃。

---

## D4 · 记忆分区（peer）策略

peer 是记忆的分区键。**选错的表现是"装了但互相看不见"——而且静默无召回，最难排查。**

| 方案 | 效果 | 怎么配 |
|---|---|---|
| **A. 全局共享**（互通场景首选） | 所有 agent、所有项目共用一份记忆 | **什么都不用做**。不在 git 仓库里跑 ⇒ 自动落到 user 级空间 |
| B. 按仓库分区 | 不同仓库互不串味 | 仓库根或工作区放 `.openviking/config.json`：`{"version":1,"peer":{"id":"my-project"}}` |
| C. 精确指定 | 多工作区强制归属 | 环境变量 `OPENVIKING_PEER_ID` |

**推导规则**：默认 `peer.source: "git"` → 取归一化的 `origin` URL
（`git@github.com:owner/repo.git` → `github.com-owner-repo`），**次选仓库根路径**，
不在仓库中则**不发送 peer**（落到 user 级空间 —— 对"互通"反而是好事）。

**防串味**：`OPENVIKING_RECALL_PEER_SCOPE=actor` 把召回限制在当前工作区。

> ⚠️ **不同 agent 的 peer 推导不统一 = 互通直接失败。**
> 验收标准就是"跨 agent 召回成功"，而不是"插件装上了"。

---

## D5 · 抽取输出协议必须改成 `json`

**默认 `extraction_output_format: "python"`** —— 抽取的模型输出契约是受限内存 SDK 的 **Python 赋值 DSL**。
第三方 / 自建模型几乎必然违规，4 轮重试全部 `parse_error`，结果是 `memories_extracted = {}`。

**实测对照**（同一份会话内容）：

| | python 协议 | json 协议 |
|---|---|---|
| 抽取结果 | `{}` / 0 条 | **19 条**（5 events / 9 entities / 5 preferences） |
| LLM token | 34,696 | 14,040 |
| 耗时轮次 | 4/4 全部解析失败 | 1 轮通过 |

**结论**：`json` 是官方保留的 legacy 结构化协议，**对模型的格式遵循能力要求低得多**。
自建 / 第三方模型**必配**。

> 这一条是整套搭建中**收益最高的单行配置**。

---

## D6 · VLM 能力下限

### 官方原文（`setup_wizard.py`）

> `qwen3.5:4b` 是最小推荐 VLM —— **更小的模型做不好 OV 的记忆抽取，它们会把提示词里的
> few-shot 示例照抄成伪造的记忆。**

官方推荐梯度：**4B → 9B → 27B → 35B → 122B**。

### 实测失败模式（8B VL 模型）

- 返回的 `identity.md` / `profile.md` / `soul.md` 只是被**翻译成另一种语言**（语义上 `before == after`）
- `adds` 为零 —— "3 条更新"但什么都没实质变化

**换 30B MoE 后正常**（`Qwen/Qwen3-VL-30B-A3B-Instruct`：30B 总参 / 3B 激活，便宜且保留视觉能力）。

### 结论

**VLM 不是"随便找个多模态模型"就行的槽位。** 它是记忆抽取质量的决定因素。

> ⚠️ 这里同时有个配套坑：`agent_evolution.enabled` 默认 `false`，
> 不改就**永远抽不出** `experiences` / `trajectories` / `cases`。
> 两者要一起改。

---

## D7 · 启动方式：隐藏计划任务，而不是真 Windows 服务

### 背景

服务需要一个"在后台跑、不需要小黑窗、登录后自动起"的启动方式。`openviking-server` 是个普通控制台程序，直接注册计划任务跑 `.cmd` 会在交互会话里弹一个可见的控制台窗口。

### 为什么用"隐藏 PowerShell 包装器 + AtLogOn 计划任务"

- `launch-hidden.ps1` 用 `Start-Process -WindowStyle Hidden` 调起 server，桌面**不弹窗**；任务仍跑在当前用户会话，能拿到用户的 `SILICONFLOW_KEY`（持久用户环境变量 `HKCU:\Environment`）。
- 计划任务 `AtLogOn` 触发器负责"登录自启"；`-ExecutionTimeLimit ([TimeSpan]::Zero)` 让它常驻不被强杀；`Interactive` + `Limited` 让注册时**不用 UAC、不用存密码**。
- 零新增依赖，不下载任何东西。

### 已知边界（也是"有意设计"）

- 任务**只在登录时触发**，进程被杀后不会自启——这样你随时能手动停服务（`Stop-ScheduledTask` / 杀进程）。
- 用户**登出后**进程会随会话结束而退出（不是系统级常驻）。对"人一直登录着用 agent"的场景够用。

### 想要"真·后台服务"时：NSSM 真服务（本项目主路径）

如果场景需要**开机即起（早于登录）、登出不死、由 SCM 故障自愈**，用 NSSM 把 server 包成系统服务。本项目已落地此方案，直接用脚本：

```powershell
# 在本机管理员 PowerShell 里运行（脚本会探测 nssm / openviking-server / key）：
powershell -ExecutionPolicy Bypass -File scripts/install-nssm.ps1
```

`install-nssm.ps1` 会：nssm install 创建服务 → 写 NSSM 参数（无窗口、崩溃自愈 `AppExit=Restart`、开机自启 `Start=auto`、日志重定向）→ 注入环境变量（`SILICONFLOW_KEY` + `HOME=C:\Users\<you>` + `CODEBUDDY_SAFE_DELETE_ENABLED=0`）。

几个关键点：

- **身份用 `LocalSystem`**，配合 `HOME=C:\Users\<you>` 让 OpenViking 复用现有 `~/.openviking`（配置、数据、密钥全部一致），无需另配机器级环境变量。
- **`CODEBUDDY_SAFE_DELETE_ENABLED=0` 必须设**——否则 OpenViking 自己的清理动作可能被 safe-delete 守卫拦。
- **激活服务需要 `CreateService` 权限**：在受限宿主（如某些 AI 工具内置 PowerShell）里 `sc.exe` 在程序黑名单、`nssm install` / `New-Service` 会被宿主杀掉，SCM 也不会识别纯注册表写入的服务项。这种情况下，到**本机真实的管理员 PowerShell** 跑 `install-nssm.ps1` 即可（nssm install 会通知 SCM 加载）。若已用纯注册表方式写好服务项，重启系统也能让 SCM 在启动时加载。

---

## 附录 · 风险表

| # | 风险 | 等级 | 对策 |
|---|---|---|---|
| 1 | peer 分区键不统一 → 静默无召回 | **高** | 明确 peer 策略；验收标准定为"跨 agent 召回成功" |
| 2 | VLM 能力不足 → 抽取空洞 / 伪造示例 | **高** | 用 ≥4B，实操 30B MoE；配 `agent_evolution` |
| 3 | 假记忆污染 | **高** | 只用实测值构造验证会话；见 TROUBLESHOOTING §I |
| 4 | 服务静默死亡（跟随会话） | **高** | 注册登录计划任务；查父链末端是否为 `svchost.exe` |
| 5 | 计划任务 3 天默认上限 | 中 | `-ExecutionTimeLimit ([TimeSpan]::Zero)` |
| 6 | 免费模型随时转收费 / 下架 | 中 | 配 Ollama 作 `backup`，架构上可替换 |
| 7 | 免费档无 SLA、高峰排队、429 | 中 | 2000+ RPM 较宽裕；OV 有 `max_retries` |
| 8 | 代码图谱结果驻留 token 反增 | 中 | 图谱结果先落到 `viking://`，注入只给 URI + L0 摘要 |
| 9 | 两个图谱工具同挂致模型选择困难 | 中 | 同层竞品**只常驻一个** |
| 10 | OV 版本迭代快，配置键位漂移 | 中 | 锁版本；升级前 `ovpack` 备份 |
| 11 | 自定义 schema 改 `directory` / `merge_op` | 中 | 官方标为"很高风险"，**只加新类型、不改内置** |
| 12 | OpenViking 为 **AGPL-3.0** | 中 | 自用无碍；**对外提供服务需评估传染性** |
