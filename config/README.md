# config/

两个模板，复制到 `%USERPROFILE%\.openviking\` 后去掉 `.example` 后缀。

| 模板 | 目标位置 | 作用 |
|---|---|---|
| `ov.conf.example` | `%USERPROFILE%\.openviking\ov.conf` | **服务端**配置：模型 provider / 存储 / memory 开关 |
| `ovcli.conf.example` | `%USERPROFILE%\.openviking\ovcli.conf` | **客户端**配置：服务地址 + 两把钥匙 |

> 模板是**严格合法 JSON**（JSON 规范不支持注释），所以字段解释放在这里。
> 复制后请用 `python -m json.tool ov.conf` 校验一遍。

---

## `ov.conf` 字段说明

### embedding

| 字段 | 值 | 说明 |
|---|---|---|
| `provider` | `"openai"` | OpenAI 兼容模式。**关键副作用：OV 不发送 `dimensions` 参数**，正好绕开 `bge-m3` 拒绝该参数的问题 |
| `api_base` | provider 的 `/v1` 根 | 注意键名是 `api_base`，**不是** `base_url` |
| `api_key` | `"${SILICONFLOW_KEY}"` | 引用环境变量展开。**不要写明文** |
| `model` | `BAAI/bge-m3` | 1024 维固定 / 8192 tok，免费 |
| `dimension` | `1024` | 与 OV 默认一致。若上游报维度相关 400，先删掉这一行试试 |

### rerank

| 字段 | 值 | 说明 |
|---|---|---|
| `api_base` | `https://<host>/v1/rerank` | ⚠️ **必须是完整端点**，注意是**单数** `rerank`。只写 host 会 404 |
| `threshold` | `0.1` | 低于此分数的结果被丢弃 |

> 部分 provider 没有 rerank 接口。没有时 OV 会自动降级用向量分，仍可用、质量下降。

### vlm

**这是记忆抽取质量的决定性槽位**（OpenViking 没有独立的 `llm` 槽，抽取复用 `vlm` 的文本模式）。

| 字段 | 值 | 说明 |
|---|---|---|
| `model` | `Qwen/Qwen3-VL-30B-A3B-Instruct` | 30B MoE / 3B 激活。**小于 4B 的模型会把提示词里的 few-shot 示例照抄成伪造记忆** |
| `temperature` | `0.0` | 抽取要确定性 |
| `thinking` | `false` | 关掉思维链，省 token |

### storage

| 字段 | 说明 |
|---|---|
| `workspace` | 数据目录（AGFS 内容 + 向量库索引） |
| `agfs.backend` | `local` / `s3fs` / `memory`。个人本地用 `local` |
| `vectordb.backend` | `local` / `http` / `volcengine`。个人本地用 `local` |

> ⚠️ **不要把 `workspace` 放在 OneDrive / 网盘同步区** —— 同步进程会和文件锁打架。

### server

| 字段 | 说明 |
|---|---|
| `host` / `port` | 默认 `127.0.0.1:1933`。**只监听本地** |
| `root_api_key` | **管理密钥**。换成一串你自己的长随机字符串。为空时 OV 会推断成 `dev` 模式（无鉴权）——**别这么干** |
| `agent_evolution.enabled` | ⚠️ **默认 `false`**，不改就永远抽不出 `experiences` / `trajectories` / `cases` |

### memory

| 字段 | 值 | 说明 |
|---|---|---|
| `extraction_output_format` | `"json"` | ⚠️ **默认 `"python"`**（受限 Python 赋值 DSL），第三方模型几乎必然 `parse_error` ⇒ 抽取 0 条。**实测：改 json 前 0 条 / 改后 19 条** |
| `custom_templates_dir` | `~/.openviking/custom-memory` | 自定义记忆 schema 的目录，可选 |

其他可选开关（默认关，按需打开）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `extraction_enabled` | true | 总开关；关掉则只归档不抽取 |
| `session_skill_extraction_enabled` | false | commit 时顺带抽取可复用技能 |
| `link_enabled` | false | 记忆之间的关联链接抽取 |
| `eager_prefetch` | true | 预加载全部记忆内容（不向 LLM 暴露 read/search 工具） |
| `prefetch_search_topn` | 5 | 预取时读取的检索结果条数 |
| `experimental_memory_switch` | false | 加载实验性模板 |

---

## `ovcli.conf` 字段说明

| 字段 | 说明 |
|---|---|
| `url` | 服务地址，需与 `ov.conf` 的 `host:port` 一致 |
| `root_api_key` | 与 `ov.conf` 的 `server.root_api_key` **保持一致**。用于管理接口 |
| `api_key` | **用户密钥（数据面）**，先留空，P4 执行 `ov admin register-user default default` 后回填 |

> ⚠️ **两把钥匙不是一回事。** root 只有管理权限，拿它调数据面接口会 **403**。
> 而 Studio 的数据类页面（技能 / 检索 / 会话 / agent 经验）**必须**用用户密钥。

写完后收紧权限：

```powershell
icacls "$env:USERPROFILE\.openviking\ovcli.conf" /inheritance:r /grant:r "$env:USERNAME:F"
```
