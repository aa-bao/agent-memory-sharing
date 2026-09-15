# agent-memory-sharing

让本机的多个 coding agent 共用一份长期记忆。

底座是 [OpenViking](https://github.com/volcengine/OpenViking)（Agent 原生上下文数据库，AGPL-3.0）。本仓库不重写它的文档，只解决一件事：让它统一你电脑的所有 agent 记忆，不管你切换哪个 agent 上下文都能直接注入。

装完之后，你在 Claude Code 里提过一次的偏好，切到 Codex、dsh、zcode 等 agent 上下文中直接注入记忆，反过来也一样。

直接给 agent 部署即可。

---

## 给 agent 的入口

把 [`SOP.md`](SOP.md) 读完再动手，按 P0 → P8 顺序走。每阶段的验收命令都要真跑，别凭印象跳过，这里一半的故障是静默的。

验收失败去 [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md) 按症状查表，那里每条都是实测踩出来的。想省事可以先跑 `scripts/install.ps1`（P1–P4 的幂等版），但跑完照样要过验收。

脚本不写死路径。`scripts/_ov.py` 负责探测 `ov` 可执行文件和配置目录，所以整个仓库可以随便搬、随便改名。常驻约定看 [`AGENTS.md`](AGENTS.md)，篇幅短，适合塞进上下文。

## 禁止做的事

禁止把 API key 明文写进 `ov.conf`，用 `${SILICONFLOW_KEY}` 引用环境变量。

禁止拿旧上下文里的背景信息去构造验证会话。本项目踩过的坑：VLM 无从判断真伪，会把你的错误表述当事实忠实抽取、永久固化，还附上引用让错误显得更可信。详见 TROUBLESHOOTING 的「假记忆污染」。

禁止在同一条命令里既删文件又启动服务。safe-delete 守卫会拦在删除那步，整条命令中止，服务根本没起来，而你以为它起了。

禁止为了修 Studio 空白页就把 `auth_mode` 改成 `dev`。那等于关掉整个服务的鉴权，还会破坏插件依赖的用户密钥身份解析。空白页是 MIME 问题，`scripts/apply-mime-fix.py` 能修。

`.env` 也别提交，`.gitignore` 里写好了。

## 配置要求

和 openviking 一样，一个 embedding 模型，一个 VLM 模型；

选型理由和免费额度在 [`docs/decisions.md`](docs/decisions.md)。

## 文件在哪

```
SOP.md              搭建主流程，P0–P8，每阶段带验收
TROUBLESHOOTING.md  症状 → 根因 → 修法
AGENTS.md           给 agent 的常驻约定
docs/               架构分层、选型决策
config/             ov.conf / ovcli.conf 模板，逐字段有注释
scripts/            启动器、体检、审计、各 agent 接入
skills/             可直接加载的技能包
examples/           安全的验证会话样例
```

最该先看的两个脚本：`ov-doctor.py` 一键体检（连通性、模型、抽取、召回、Studio 鉴权逐项报 OK/FAIL），`ov-commit-zh.py` 提交中文会话触发抽取。

## 搭完之后自查

- `ov health` 返回 connected
- `ov status` 各组件全绿
- 提交一次会话，`memories_extracted` 不是空对象
- `ov ls viking://~/memories` 能看到落盘文件，召回也能命中刚写的内容
- Studio 打得开，左侧菜单点得进去（不弹回 settings）
- 服务进程父链末端是 Task Scheduler 的 `svchost.exe`，不是 `bash.exe`
- 重启机器后 `ov health` 不用手动操作就能过。这条最容易漏，漏了就白装

## 说明

OpenViking 是 AGPL-3.0。自己机器上用没问题，对外提供服务或分发之前先做许可证评估。

里面的内容都是 Windows 实测沉淀，配置键位会随 OpenViking 版本漂移，升级前先 `ovpack` 备份。这个仓库本身没带许可证文件，要公开分发的话自行补一个。
