# 架构与数据流

> 本文解释"为什么这么搭"。执行步骤在 [`../SOP.md`](../SOP.md)。

---

## 1. 一句话定位

OpenViking 是一个面向 AI Agent 的**上下文数据库**：把 Memory / Resource / Skill 三类上下文统一挂到
`viking://` 虚拟文件系统下，用 `ls / tree / read / write / grep` 这种文件语义操作，
配合**目录感知的语义检索**与 **L0/L1/L2 渐进式加载**。

本仓库负责的是**把它在 Windows 上跑通**，并让多个 agent 共用同一份记忆。

---

## 2. 分层

```
┌─ L0 宿主层 ────────────────────────────────────────────────────────────┐
│  Claude Code    Codex       dsh        ZCode      opencode             │
└────┬──────────────┬──────────┬───────────┬───────────┬─────────────────┘
     │ 9 hooks      │ 4-5 hook │ Cordis     │ 4 hooks   │ 7 plugin hooks │
     │              │          │ 同进程插件  │           │                 │
┌────▼──────────────▼──────────▼───────────▼───────────▼─────────────────┐
│ L1 接入层（全部官方现成插件，零自研）                                     │
│  · 各 agent 原生 hook / plugin                                         │
│  · 统一 stdio MCP proxy → openviking_{find,search,read,remember,…}     │
│  · 统一经验类 skill                                                    │
└────────────────────────────┬───────────────────────────────────────────┘
                             │ HTTP 127.0.0.1:1933  (Bearer / X-OpenViking-Actor-Peer)
┌────────────────────────────▼───────────────────────────────────────────┐
│ L2 记忆中枢：OpenViking Server                                          │
│  Retrieve   意图分析 / 层级检索 / Rerank                                │
│  Session    消息 / 压缩 / 记忆提交                                       │
│  Parse      文档解析 / 树构建 / 异步语义生成                              │
│  Compressor Schema 驱动提取 / LLM 去重决策                                │
│  Storage    AGFS 存内容 + 向量库存索引                                    │
└────────────────────────────┬───────────────────────────────────────────┘
                             │
┌────────────────────────────▼───────────────────────────────────────────┐
│ L3 模型 Provider（示例：硅基流动，可换任意 OpenAI 兼容端点）              │
│  embedding  BAAI/bge-m3                 1024d 免费                     │
│  rerank     BAAI/bge-reranker-v2-m3     免费                           │
│  vlm        Qwen/Qwen3-VL-30B-A3B       记忆抽取靠它                    │
└────────────────────────────────────────────────────────────────────────┘
```

**关键架构决策：零自研代码。** 早期方案里曾计划在 L2 与 L4 之间写一层"编排路由"（peer 归一化 /
锚点桥接 / 过期检测 / 预算治理）。后来发现官方内置的 schema 已经覆盖了这些诉求，于是整层降级为**声明式 YAML**。
详见 [`decisions.md`](decisions.md) §3。

---

## 3. 三层上下文模型（省 token 的核心机制）

| 层 | 文件 | 内容 | 体量 | 用途 |
|---|---|---|---|---|
| **L0** | `.abstract.md` | 一句话摘要 | ~100 token | 快速判断相关性 |
| **L1** | `.overview.md` | 结构 + 要点 | ~2k token | 规划阶段决策 |
| **L2** | `*.md` | 完整原文 | 按需 | 确认相关后才读 |

---

## 4. 记忆提取流水线（`session.commit()`）

分两阶段，**同步归档 + 异步提取**：

- **Phase 1（同步，立即返回 `task_id`）**
  递增 `compression_index` → 写 `messages.jsonl` 到归档目录 → 清空当前消息列表
- **Phase 2（异步后台）**
  LLM 生成结构化摘要写 `.abstract.md` / `.overview.md` → 按 memory policy 提取长期记忆 →
  写 `memory_diff.json` 审计日志 → 更新 `active_count` → 写 `.done`

提取内部流程：

```
消息 → LLM 提取候选 → 向量预过滤找相似 → LLM 去重决策 → 写 AGFS → 向量化
```

**去重决策矩阵**（这是记忆不腐烂的关键设计）：

| 层级 | 决策 | 含义 |
|---|---|---|
| Candidate | `skip` | 候选重复，丢弃 |
| Candidate | `create` | 新建；必要时先删冲突旧记忆 |
| Candidate | `none` | 不建候选，只处理已有 |
| Existing item | `merge` | 候选内容并入指定已有记忆 |
| Existing item | `delete` | 删掉冲突的旧记忆 |

> ⚠️ **异步是双刃剑**：不阻塞对话的前提是接受"提交后短时间内记忆还没抽取完"。
> 所以验证时必须**轮询** `task status`，不能 commit 完立刻断言"抽取失败"。

---

## 5. 记忆类型（两阶段）

| 阶段 | 记忆类型 | 触发方式 |
|---|---|---|
| `user` | identity / profile / soul / preferences / entities / events | `commit` 自动抽取 |
| `agent` | experiences / trajectories / cases | `commit` 自动抽取，**需 `agent_evolution.enabled = true`** |

> `experiences` 会启用完整的 Agent Evolution 流程并自动激活 `cases` + `trajectories`；
> **没有 `experiences` 时显式传入的 `cases`/`trajectories` 会被静默忽略**（不报错，容易踩坑）。

---

## 6. 存储布局

```
viking://user/{uid}/sessions/{sid}/
├── messages.jsonl
├── .abstract.md / .overview.md
├── history/archive_001/
│   ├── messages.jsonl
│   ├── .abstract.md / .overview.md
│   ├── memory_diff.json        ← 审计日志：这次到底抽了什么
│   └── .done
└── tools/{tool_id}/tool.json

viking://~/memories/
├── profile.md / identity.md / soul.md
└── preferences/ entities/ events/ cases/ trajectories/ experiences/
```

**双层存储**：

- **AGFS 存内容**（L0/L1/L2 全文、多媒体）—— 后端 `localfs` / `s3fs` / `memory`
- **向量库只存索引**（uri / parent_uri / context_type / is_leaf / vector / abstract / name / …）
  —— **不存文件内容**，靠 URI 回引。后端 `local` / `http` / `volcengine`

---

## 7. 检索机制

| | `find()` | `search()` |
|---|---|---|
| 会话上下文 | 无 | 带 `session_id` |
| LLM 意图分析 | 不走 | 走（生成 0–5 个 TypedQuery，按 MEMORY / RESOURCE / SKILL 分类） |
| 后续流程 | 单查询直出 | HierarchicalRetriever 递归目录 → 全局 topk=10 → Rerank |
| 延迟 | 低 | 较高 |
| 适用 | 简单查询 | 复杂任务 |

两者都返回 `MatchedContext{uri, context_type, is_leaf, abstract, score}`。

**token 预算旋钮**：

| 旋钮 | 典型值 | 作用 |
|---|---|---|
| `profileTokenBudget` | 6000–10000 | 开场画像注入上限 |
| `recallTokenBudget` / `tokenBudget` | 2000 | 每轮召回注入上限 |
| `autoRecall.maxContentChars` | 500 | 单条记忆注入的字符上限 |
| `preferAbstract` | true | 优先注入 L0 摘要而非正文 |
| `tool_output_externalization.threshold_chars` | 20000 | 超过则工具输出落盘，只留 stub + ref |

---

## 8. 对外接口

**HTTP API**（默认 `http://localhost:1933`）：

| 组 | 关键端点 |
|---|---|
| 系统 | `GET /health`、`GET /ready`、`/api/v1/system/status`、`/api/v1/system/consistency` |
| 文件系统 | `GET /api/v1/fs/ls\|tree\|stat\|attrs`、`POST /api/v1/fs/mkdir\|cp\|mv`、`DELETE /api/v1/fs` |
| 内容 | `GET /api/v1/content/read`(L2)、`/abstract`(L0)、`/overview`(L1)、`POST /api/v1/content/write\|batch-write\|reindex` |
| 资源 | `POST /api/v1/resources/temp_upload`、`POST /api/v1/resources` |
| 技能 | `GET\|POST /api/v1/skills`、`POST /api/v1/skills/find\|validate` |
| 会话 / 记忆 | `POST /api/v1/sessions`、`POST /api/v1/sessions/{id}/commit`、`/extract`、`/messages` |
| 检索 | `POST /api/v1/search/find`、`/search/search`、`/search/grep`、`/search/glob` |
| 观测 | `GET /api/v1/tasks/{id}`、`/api/v1/observer/*`、`/metrics`（Prometheus） |

- 响应信封统一 `{status, result, time}`；错误走 `{status:"error", error:{code,message}}`
- 认证：`Authorization: Bearer <key>` 或 `X-API-Key`；`/health`、`/ready` **免认证**
- 租户头：`X-OpenViking-Account`、`X-OpenViking-User`、`X-OpenViking-Actor-Peer`

**MCP**：官方 stdio MCP proxy，透传 **15 个工具**：

```
find / search / read / list / tree / grep / glob
remember / write / edit / add_resource
list_watches / cancel_watch / forget / health
```

**CLI**：`ov`（约 40 个命令组）与 `openviking-server init|doctor|start`。

**配置链**（优先级从高到低）：

```
OPENVIKING_* 环境变量  →  ~/.openviking/ovcli.conf  →  ~/.openviking/ov.conf  →  内置默认值
```

---

## 9. 各 agent 接入形态对比

| 维度 | Claude Code | Codex | dsh | ZCode | opencode |
|---|---|---|---|---|---|
| 接入形态 | marketplace 插件 | 插件 / 手动 | **Cordis 同进程插件** | 配置驱动合并 | npm 插件 |
| hook 数 | 9 | 4–5 | 4 个 Cordis 事件 | 4 | 7 plugin hook |
| 自动召回 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 召回后再摘要 | ✅ `claude -p` | ✅ `codex exec` | ❌ | ❌ | ❌ |
| 离线补偿队列 | ✅ | ❌ | ✅ 待写队列 | ✅ | ✅ |
| statusline | ✅ | ❌ | ❌ | ❌ | toast |

**值得注意的差异**：

- **Claude Code** 有本地再摘要，**实际注入 token 最低**
- **dsh** 是**同进程插件**（不是外挂 hook）→ 贴着会话走、注入对压缩可见、有待写队列，**最稳的接入形态**
- **ZCode** 是"每次 Stop 必 commit / keep0" → 写入最激进，可能产生碎片记忆

**接入方式统一的好处**：五个 agent 用同一套插件机制、同一条凭据链，**一份凭据驱动全部 agent**。

---

## 10. 数据存储与同步的取舍

**结论：不做 agent 之间的点对点同步，做中心化单一真相源。**

理由：五个 agent 的会话格式（明文 jsonl / zstd jsonl / SQLite / JSON 快照）**无法无损互转**，
点对点同步是 N² 复杂度且必然信息损失。

| 数据 | 存哪 | 谁写 | 谁读 |
|---|---|---|---|
| 长期记忆 | `viking://~/memories/` | 各 agent hook 自动 commit + 显式 `remember` | 全部 agent |
| 会话归档与摘要 | `viking://user/{uid}/sessions/{sid}/history/` | 各 agent 的 commit | 全部 agent（跨 agent 召回） |
| 语义索引 | 本地向量库 | OpenViking 自动同步 | 检索层 |
| 记忆变更审计 | `memory_diff.json` | Phase 2 后台 | 人工 / 脚本 |

**一致性与备份**：

1. AGFS 与向量库的 `rm` / `mv` 由 `VikingFS` 自动联动
2. 一致性由 `POST /api/v1/system/consistency` 主动校验
3. 备份用官方 `ovpack`（整库快照），**不要手工拷 AGFS 目录**

---

## 11. 明确排除的做法

| 不做的 | 为什么 |
|---|---|
| 写解析器去读各 agent 的会话文件 | 格式极度异构，会陷入泥潭。走 hook 事件流 |
| 把代码图谱当记忆塞进记忆库 | 图谱是**派生物**（随代码变、可重建），记忆是**原生资产**（删了就没了）。混装会让记忆库被符号淹没并迅速腐烂 |
| 做 agent 间的点对点记忆同步 | 见 §10 |
| 自研 L3 编排层 | 官方内置 schema 已覆盖，见 [`decisions.md`](decisions.md) §3 |
