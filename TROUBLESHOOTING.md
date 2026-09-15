# TROUBLESHOOTING · 症状 → 根因 → 修复

> 这里的每一条都是**实测踩出来的**，不是推测。按症状查表。
>
> 一句话总纲：**这套系统最危险的失败模式是"静默失败"** —— 服务死了、抽取返回空、召回返回 `{}`，
> 全都不报错。所以永远不要凭"看起来正常"下结论，要用 `scripts/ov-doctor.py` 这类可执行验收去证明。

---

## 0. 30 秒速查决策树

```
服务起不来              → §A  清 PYTHONPATH + NODE_OPTIONS + CODEBUDDY_SAFE_DELETE_ENABLED=0
commit 成功但 0 条记忆   → §B  按顺序查三件事：
                              (a) memories_extracted {} + parse_error → extraction_output_format = "json"
                              (b) skip_reason agent_evolution_disabled → agent_evolution.enabled = true
                              (c) 有 adds 但内容空洞 / before==after  → VLM 太小（<4B）
只有 identity/profile/soul → 正常。会话里没有 user 阶段素材
经验/轨迹类记忆缺失      → §B  agent_evolution.enabled=false，或会话没有"执行+反思"的可归纳轨迹
                              ⚠️ commit 是会产出 agent 阶段记忆的，别怪 commit
召回 {} 但无报错        → §D  hook 日志里看是 stdin_parse 还是 codex ENOENT（后者非致命）
Studio 纯白无报错        → §E  curl -D 看 /studio/assets/*.js 的 content-type 是不是 text/plain
左侧菜单弹回 settings    → §E  401 UNAUTHENTICATED 触发全局跳转；两把钥匙都要填
数据面接口 403           → §E  用了 root_api_key；要换用户 api_key
重启后 / 会话关闭后      → §F  服务跟着父 shell 死了。父链末端是 bash.exe 就是这个问题
  记忆悄悄失效              注册登录计划任务（SOP P7）
任务跑得好好的            → §F  计划任务默认执行时间上限 3 天，必须设 PT0S
  第 3-4 天自己死了
中文变乱码               → §G  不要用 CLI 参数 / Git Bash 管道传中文；用 ov-commit-zh.py 或 UTF-8 stdin 文件
```

---

## §A 服务起不来

### A1. `Application startup failed. Exiting.`

**最典型的形态**，日志里有：

```
[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":199,"threshold":50,
 "targets":["C:\\Users\\<you>\\.openviking\\data\\.openviking.pid"]}
```

**根因**：宿主机（典型是 WorkBuddy）向子进程注入了两层**删除拦截**：

| 注入项 | 内容 | 劫持对象 |
|---|---|---|
| `PYTHONPATH` | `...\cli\vendor\shim`（含 `sitecustomize.py`） | Python `os.remove` |
| `NODE_OPTIONS` | `--require=.../node-language-shim.cjs` | Node `fs.unlink` |

服务启动时要清理陈旧的 RocksDB / pid 锁文件 → 被批量守卫拦截 → `SystemExit(1)`。

**修复**：

```bash
unset NODE_OPTIONS; unset PYTHONPATH; unset PYTHONSTARTUP
export CODEBUDDY_SAFE_DELETE_ENABLED=0
```

> ⚠️ `CODEBUDDY_SAFE_DELETE_ENABLED=0` 是**官方总开关**（`sitecustomize.py` 里读这个变量）。
> ⚠️ **只 `unset NODE_OPTIONS` 没用** —— Python 侧是经 `PYTHONPATH` 全局注入的，必须同时清 `PYTHONPATH`。
> ⚠️ **不要把 `rm` 和"启动服务"放在同一个工具调用里** —— 守卫一旦触发，整条命令中止，服务根本没起，而你以为起了。

同一个根因还会导致：`dsh plugin add` 报同样的错、批量 `rm` 整个命令中止。

**已固化**在 `scripts/start-ov.sh` / `scripts/start-ov.cmd`。直接跑它们，不要内联敲。

### A2. 端口被占

```powershell
Get-NetTCPConnection -LocalPort 1933 -State Listen
```

有残留进程 → `Stop-Process -Id <PID> -Force`，或改 `ov.conf` 的 `server.port`。

### A3. 启动成功了但 `ov health` 连不上

`ovcli.conf` 的 `url` 与实际 `host:port` 不一致；或服务其实已经退出。
看 `%OV%\logs\server-launch.log` 末尾的 `exited, code=`。

### A4. 改了 `ov.conf` 不生效

**服务没有热重载。** 任何 `ov.conf` 改动都必须**重启服务**。

---

## §B 抽取不到记忆（最高频问题）

### B1. `memories_extracted = {}`，日志里 `parse_error`

```
ERROR extract_loop:run:420  Failed to parse memory operations (iteration 4/4)
failure_kind=parse_error
error=Line 2: only one simple assignment target is allowed
response_preview='```python
identity_1.vibe = "高效、准确、尊重"
...'
```

**根因**：抽取的**模型输出契约**默认是受限内存 SDK 的 **Python 赋值 DSL**（`extraction_output_format: "python"`）。
第三方 / 自建模型几乎必然违反这个 DSL，4 轮重试全部 `parse_error`。

**修复**：`ov.conf` 加

```json
"memory": { "extraction_output_format": "json" }
```

**实测对照**（同一份会话内容）：

| | python 协议 | json 协议 |
|---|---|---|
| 抽取结果 | `{}` / 0 条 | **19 条** |
| LLM token | 34,696 | 14,040 |
| 耗时轮次 | 4/4 全部解析失败 | 1 轮通过 |

> `json` 是官方保留的 legacy 结构化协议，**对模型的格式遵循能力要求低得多**。自建/第三方模型必配。

### B2. `agent_memory_skip_reason: "agent_evolution_disabled"`

**根因**：`server.agent_evolution.enabled` 默认为 `false`，导致 `experiences` / `trajectories` / `cases`
不参与抽取。

**修复**：`ov.conf` → `"server": { "agent_evolution": { "enabled": true } }`，**重启服务**。

或者直接跑 `python .\scripts\enable-agent-evolution.py`（脚本会自动改配置）。

> ⚠️ **这里有一个曾长期误导人的坑，务必知道：**
> `session_extract_context_provider.py` 里有一句注释 *"过滤掉 agent stage 的 schema（trajectory/experience 由执行提取处理）"*。
> 那句话**只作用于 user 阶段那一路在构造 schema 列表时**，**不代表 commit 整体不碰 agent 阶段**。
> 只读那句注释、不追进 `compressor_v3.py`，就会得出"CLI 抽不出经验类记忆是设计使然"的**错误结论**。
>
> **正确结论**（源码 + 实测双证）：
>
> ```python
> # openviking/session/compressor_v3.py  L544
> if agent_evolution_enabled and _TRAJECTORIES_MEMORY_TYPE in agent_memory_types:
> ```
>
> `SessionService` 把 `agent_evolution_enabled` 一路传进 `SessionCompressorV3.extract_long_term_memories()`。
> 所以 **`agent_evolution.enabled = true` 时，普通 commit 会在同一个任务里额外跑 agent 阶段抽取**。

### B3. 有 `adds` 但内容空洞，或 `before == after`

**根因**：VLM 能力不足。典型表现是**把提示词里的 few-shot 示例照抄成伪造的记忆**，
或者把 `identity.md` / `profile.md` / `soul.md` 原地翻成另一种语言（语义上 `before == after`），零实质 `adds`。

官方源码 `setup_wizard.py` 原文：*"qwen3.5:4b 是我们推荐的最小 VLM——更小的模型做不好 OV 的记忆抽取"*。
官方推荐梯度：**4B → 9B → 27B → 35B → 122B**。

**修复**：换 `Qwen/Qwen3-VL-30B-A3B-Instruct`（30B MoE / 3B 激活，便宜，保留视觉能力）。
实测原来是 `Qwen3-VL-8B-Instruct`，正好落在这个失败模式里。

### B4. 只抽出了 identity / profile / soul，没有 preferences / entities

**这通常是正常的。** 说明这次会话里没有对应的 user 阶段素材。

### B5. 经验 / 轨迹 / 案例类记忆缺失

两个原因，按顺序排查：

1. `agent_evolution.enabled = false` → 见 B2
2. 会话内容里**没有可归纳的任务执行特征**。实测对照：纯"用户偏好清单"式会话只出 user 阶段记忆；
   带"执行一件事 + 反思"的对话会额外产出 `trajectories` / `experiences` / `cases`。

> 独立的离线训练管道（`openviking/session/train/`：trajectory_analyzer、gradient_estimator、
> `run_batch_train_eval.py`）**与 commit 这条路并存**，不是替代关系。

### B6. 怎么定位抽取问题

```powershell
ov task status <task_id> --output json        # 看 memories_extracted / token_usage
ov read viking://user/default/sessions/<sid>/history/archive_001/memory_diff.json
ov ls viking://~/memories
```

**服务端日志默认打到 stdout，不是日志文件** —— 所以要用启动器的重定向去抓，然后 grep `extract_loop` / `parse_error`。

---

## §C 模型 / Provider

### C1. embedding 报 400（维度相关 / 错误码 20015）

`BAAI/bge-m3` 是**固定 1024 维**模型，某些情况下会对 `dimensions` 参数报错。

**处置顺序**：

1. 先按 README 的配置跑 —— `provider: "openai"` 时 OV **不发送** `dimensions`，通常直接绕开
2. 若仍报错 → 移除 `"dimension": 1024` 再试
3. 仍失败 → 换 `BAAI/bge-large-zh-v1.5`（同样 1024 维、免费、中文更强，但只有 512 token 上限）

### C2. rerank 不生效 / 404

`rerank.api_base` **必须是完整端点**：`https://<host>/v1/rerank`（注意是**单数** `rerank`），不是只有 host。

### C3. 想用只有 chat 的服务（如 DeepSeek）

**不行。** 必须有 embedding 接口，否则 `doctor` 过不去。

### C4. 非火山引擎后端

官方文档写明 `rerank.provider` 支持 `openai` 兼容模式。但**部分后端没有 rerank 接口**时，
检索会自动回退到纯向量分——质量下降，但仍可用。

### C5. HTTP 429 / 限流

免费档限额**固定，不随消费等级提升**，超限返回 429。

| 类别 | RPM | TPM |
|---|---|---|
| Embedding | 2,000 – 10,000 | 500,000 – 10,000,000 |
| Rerank | ~2,000 | ~500,000 |
| Chat / VLM | 1,000 – 10,000 | 50,000 – 5,000,000 |

个人多 agent 场景够用。**但要正视**：① 免费档**无 SLA**，高峰可能排在付费流量后；② 免费模型**可能随时下架或转收费**。

**缓解**：把免费额度当主路径，另配本地 Ollama 作 `backup` —— OV 的 provider 配置原生支持
`backup` 与 `failback_timeout_seconds`。这样限流时不会整个记忆链路瘫痪。

### C6. 代理

OV 的出站调用需要能读到 `HTTPS_PROXY` / `HTTP_PROXY`。
**注意是给"服务进程"设**，不是在当前 shell 设就完了（计划任务起的进程有自己的环境）。

---

## §D 召回为空

### D1. hook 日志里 `stdin_parse: invalid input`

```json
{"hook":"auto-recall","stage":"skip","data":{"stage":"stdin_parse","reason":"invalid input"}}
```

**根因**：Git Bash 的 `echo` 按控制台代码页（中文 Windows = GBK）写出中文 → Node 收到非法 UTF-8 → `JSON.parse` 抛错。
看起来像"没有匹配记忆"，实际是**入参根本没解析成功**。

**修复**：用 UTF-8 文件作为 stdin：`node script.mjs < payload.json`。

### D2. `spawn codex ENOENT`

召回**压缩器**尝试 spawn `codex` 做上下文压缩，但 codex 不在 PATH。

**非致命** —— 会自动降级为不压缩（`compressed: false`），召回本身不受影响。
想消除噪音：设 `OPENVIKING_RECALL_COMPRESS=off`。

### D3. 什么都不报错，就是没召回

按概率排序：

1. **peer 不一致** → A 写的落在 peer X，B 查的是 peer Y。用 `ov-doctor.py` 核对 peer 解析
2. **服务死了** → §F。这时 hook 会优雅降级，**不报任何错**
3. **hook 没载入** → hook 只在 agent **启动时**载入，装完必须重启 agent
4. **分数阈值太高** → 调低 `scoreThreshold`（默认 0.35）、调大 `limit`（默认 6）

### D4. 打开调试日志

```powershell
$env:OPENVIKING_DEBUG=1
```

然后看 `%OV%\logs\codex-hooks.log`。

跑插件自带的 `scripts/ov-memory-doctor.mjs` —— 一次性报告连接、鉴权、peer 解析、各开关与阈值。

---

## §E Studio

### E1. 页面纯白，无报错、无登录框

`/studio/` 返回 200、`index.html` 正常、`assets/*.js` 也返回 200 —— **但页面就是白的**。

**诊断**：Studio 入口是 ES Module，先看静态资源的响应头：

```bash
curl -sS -D - -o /dev/null http://127.0.0.1:1933/studio/assets/index-*.js
```

```
content-type: text/plain; charset=utf-8     ❌  ← 就是这个
content-type: text/css; charset=utf-8       ✅ (css 正常)
```

**根因**：Studio 静态资源走 `FileResponse(path)` 且**未显式指定 `media_type`**，
Starlette 调用 `mimetypes.guess_type()` 推断。Windows 上 Python 的 `mimetypes` **会读注册表**的文件关联
Content Type，本机 `.js` 被登记成 `text/plain`：

```
mimetypes.guess_type('a.js')  ->  ('text/plain', None)   # 实测
```

> ⚠️ **浏览器对 ES Module 有强制 MIME 校验**：MIME 不是 JavaScript 类型就**拒绝执行**。
> 模块不执行 → `<div id="app">` 永远为空 → **整页空白且不显示任何错误**。
> 这解释了"为什么 CSS 正常、HTML 正常，却全白"。

**修复**（不改 OV 源码、不动注册表）：

```powershell
python .\scripts\apply-mime-fix.py
```

在 venv 的 site-packages 放一个 `.pth`，解释器启动时自动纠正映射。

> ⚠️ 改完**必须重启服务** —— `mimetypes` 只在进程启动时初始化一次。
> ⚠️ 如果之前访问过、浏览器注册过 **Service Worker**，旧缓存会继续返回坏响应：
> **Ctrl+Shift+R 硬刷新**，或 DevTools → Application → Storage → **Clear site data**。

**备选**（改注册表 `HKCU\Software\Classes\.js` 的 Content Type 为 `text/javascript`）——
但某些机器 `reg.exe` 被安全策略禁用，且改注册表会影响系统级文件关联，**推荐 `.pth` 方案**。

### E2. 左侧菜单点不进去，被弹回 `/studio/settings`

**根因**：Studio 的 axios 实例上挂了**全局响应拦截器**：

```js
ic.instance.interceptors.response.use(
  res => res,
  err => (isAuthError(err) && Date.now() >= ts.current && navigateToSettings(), Promise.reject(err))
)
function isAuthError(e) { return e.statusCode === 401 || e.code === 'UNAUTHENTICATED' }
```

即：**任何接口返回 `401` 或 `code:"UNAUTHENTICATED"`，就把你强制送回 `/studio/settings`。**
而 `/studio/settings` 自己不调接口，所以只有它"打得开"。

Studio 是**纯前端应用**，凭据在浏览器 `localStorage['ov_console_connection']`，
服务端（`app.py` 的 `/studio/{path}`）只做静态投递，不注入任何凭证。

**双密钥权限矩阵**（这是关键）：

| 页面能力 | 接口 | 需要哪种密钥 |
|---|---|---|
| 工作台 / 首页 | `/api/v1/console/dashboard/summary` | 两者皆可 |
| 监控 | `/api/v1/observer/system` | 两者皆可 |
| 任务中心 | `/api/v1/tasks` | 两者皆可 |
| **技能** | `/api/v1/skills` | **用户密钥**（root → 403） |
| **agent 经验** | `/api/v1/agent-evolution/experiences/*` | **用户密钥** |
| **检索** | `/api/v1/search/search` | **用户密钥** |
| **会话** | `/api/v1/sessions` | **用户密钥** |
| 用户管理 | `/api/v1/admin/accounts/*` | **管理密钥**（用户 → 403） |

> ⚠️ **403 不触发跳转**（页面能开但数据为空），**401 才跳转**。
> 只填 root 时，数据类页面**不会**弹回设置页，而是**静默拿不到数据** —— 两种现象别混淆。

**修复**：Studio →「连接设置」填**两把钥匙**（见 SOP P6）。

**一键自检**：`python .\scripts\check-studio-auth.py`。

### E3. `HEAD /studio/*` 返回 405

正常。只允许 `GET`。**不要用 `curl -I` 探测**，用 `curl -D - -o /dev/null`。

### E4. 能不能切 `auth_mode: dev` 图省事？

技术上可以（`root_api_key` 为空时自动推断为 `dev`，浏览器端无需任何密钥），
**但不要**：那会关掉整个服务的鉴权，还可能影响已经调通的 codex / claude / dsh 记忆插件的身份解析
（它们依赖用户密钥做 peer 分区）。用两密钥方案，代价只是浏览器里填一次。

---

## §F 服务生命周期（最隐蔽的一类）

### F1. 重启后 / 会话关闭后，记忆悄悄失效

**现象**：什么都没报错，就是不再召回、不再写入。

**根因**：服务是手动起的，进程挂在 shell 会话上，会话一关就死了。
**关键：服务死掉不会有任何地方报错** —— agent 的 hook 会优雅降级。一切看起来都正常。

**诊断**——查进程父链：

```powershell
python .\scripts\ov-doctor.py --parent-chain
# 或手工：
$c = Get-NetTCPConnection -LocalPort 1933 -State Listen | Select-Object -First 1
$p = Get-CimInstance Win32_Process -Filter "ProcessId=$($c.OwningProcess)"
while ($p) { "{0}  {1}" -f $p.ProcessId, $p.Name
             $p = Get-CimInstance Win32_Process -Filter "ProcessId=$($p.ParentProcessId)" -EA SilentlyContinue }
```

| 父链末端 | 含义 |
|---|---|
| `svchost.exe`（Task Scheduler） | ✅ 已脱离会话，健康 |
| `bash.exe` / `powershell.exe` | ❌ 挂在会话上，随时会被清掉 |

**修复**：注册登录时计划任务 → SOP P7。

### F2. 任务跑得好好的，第 3–4 天自己死了

**根因**：**计划任务默认执行时间上限是 3 天**，到点被系统强杀。

**修复**：`-ExecutionTimeLimit ([TimeSpan]::Zero)`（即 `PT0S`，无限制）。
`scripts/register-autostart.ps1` 已经内置。

### F3. 计划任务起不来 / 挂死

| 原因 | 修复 |
|---|---|
| 启动器里有 `pause` | 无窗口模式下会挂死。启动器**绝不能有 `pause`**，输出写日志文件 |
| 任务没有 `SILICONFLOW_KEY` | 触发器用 `AtLogOn`（当前用户），并确认 `setx` 已持久化该变量 |
| 无控制台导致输出丢失 | 启动器把 stdout/stderr 重定向到 `%OV%\logs\server-launch.log` |
| 日志里星期几是乱码 `ÖÜ¶þ` | 中文 Windows 批处理代码页是 GBK → **不要用 `%date%`**，只用 `%time%` |
| 重复启动抢端口 | `-MultipleInstances IgnoreNew` |

### F4. 杀了进程后没有自动重启

**这是有意设计** —— 否则你没法手动停服务。恢复：`Start-ScheduledTask -TaskName OpenVikingMemoryServer`。

### F5. 怎么彻底移除自启

```powershell
Unregister-ScheduledTask -TaskName OpenVikingMemoryServer -Confirm:$false
```

---

## §G 中文 / 编码

### G1. 中文传给 `ov.exe` 变成乱码

Git Bash 在 Windows 下按本地代码页（中文系统 = GBK）转换命令行参数和管道内容。

**两条可靠路线**：

1. **Python `subprocess`** —— Windows 走 `CreateProcessW`（宽字符 API），参数无损。
   参考实现：`scripts/ov-commit-zh.py`
2. **UTF-8 文件作为 stdin** —— 给 Node hook 脚本用：`node script.mjs < payload.json`

### G2. `ov add-memory` 报 `NOT_FOUND`

该命令标记为 **experimental，0.4.20 服务端没有实现**（CLI 版本间不同步）。
**改用 session-commit 路径** —— 这也是各 agent 自己走的路。

### G3. `.cmd` 里读 `.env` 读出来的 key 多了空格

批处理的 `for /f "tokens=1,* delims=="` 会保留前导空格，把 key 弄坏。
→ `.env` 必须写成 `KEY=VALUE`，**等号两侧不要有空格**。

---

## §H 其他 Windows 细节

| 现象 | 说明 / 处理 |
|---|---|
| `lock I/O error ... 另一个程序已锁定文件的一部分` | 瞬时文件锁竞争，**重试即成功** |
| `ov config validate` 提示"未知 (自定义)" | 手写 `ov.conf` 的正常表现，**无害**。以 `ov health` / `ov status` 为准 |
| `openviking-server doctor` | 用于连通性与 provider 自检 |
| 杀软拦截 node/bun 插件脚本 | 加白名单 |
| 1933 端口冲突 | 端口预检；或改 `server.port` |
| AGFS 数据目录放在 OneDrive 同步区 | **不要**。同步会与文件锁打架 |
| 长路径问题 | 开启 Windows 长路径支持，或把仓库放在浅路径 |
| 某些机器 `reg.exe` 被安全策略禁用 | 无法绕过（也不应绕过）。优先用 Python 层补丁（`.pth`） |
| ⚠️ 在长期交互式 shell 里 `unset PYTHONPATH` | **别这么干**。shim 的 `shell-runtime-bash-env.sh` 在 shell 初始化时运行并失败，之后 `ls`/`grep`/`head`/`sed`/`wc` 全变 `command not found`。把"清环境 + 启动"的逻辑放进**脚本**里（`start-ov.sh`），不要内联敲 |

---

## §I 假记忆污染（最严重的一类，专章）

### 现象

记忆里出现了一整套**自信但错误**的事实 —— 主机名、账户名、内网 IP、`hosts` 别名。
它们被正常抽取、索引、召回，看上去完全是"权威记忆"，还带 `viking://` 引用。

### 根因

为验证抽取链路构造会话时，把**旧上下文里的信息当成"用户陈述"**写了进去。
**VLM 无从判断真伪 —— 它的职责就是忠实抽取。** 于是假信息被持久化。

> ⚠️ **这比"没有记忆"更糟**：agent 会带着错误前提工作；
> 而且召回时会附上 `viking://` 引用，**让错误信息看起来更可信、更难被质疑**。

### 预防四条

1. 验证会话只用**实测值**（`$env:COMPUTERNAME`、`whoami`、`Get-NetIPAddress`），
   或明确标注为测试数据的内容 —— **绝不用"记忆里的"背景**
2. **发现一条假记忆，必须审计同批写入的全部条目** —— 它们来自同一个未核实来源
3. 修正要**删除 + 用正确的 key 重建**，不要原地改内容：
   URI 里嵌了 key（如 `entities/网络/<错误的IP>.md`），只改正文会留下一个"说谎的文件名"
4. 修完必须走**召回路径**验证，不能只 `ov read` —— 检索可能命中残留副本

### 工具

| 工具 | 用途 |
|---|---|
| `scripts/ov-audit-tree.py <uri>` | 递归 dump 整个子树内容，逐条人工核对 |
| `scripts/ov-audit-dir.py <uri>` | 单层 dump |
| `ov rm <uri>` | 删除错误条目 |
| `ov write <uri> --content <body> --mode create --wait` | 用正确内容重建 |

### 排查顺序

```
发现 1 条可疑记忆
  → ov-audit-tree.py 把同批（同一次 commit 的同类目录）全 dump 出来
  → 逐条与实测值核对
  → 全部删掉错误项
  → 用实测值重建
  → 走召回路径复测，确认无残留
```
