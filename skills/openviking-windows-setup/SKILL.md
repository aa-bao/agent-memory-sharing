---
name: openviking-windows-setup
description: Deploy, configure, and troubleshoot the OpenViking memory server on Windows for multi-agent memory sharing (Claude Code / Codex / dsh / ZCode / opencode). Use when installing OpenViking, wiring agents to it, configuring embedding/VLM/rerank providers (e.g. SiliconFlow), or debugging "server won't start", "memory extraction returns nothing", "recall returns empty", or "Studio is blank" on Windows — especially inside WorkBuddy, whose injected shims break server startup and plugin installs. Covers the safe-delete shim workaround, the python-vs-json extraction protocol fix, the VLM capability floor, agent-stage vs user-stage memory routing, non-ASCII writes, the Studio MIME bug and its two-key auth model, and logon autostart (without which memory dies silently).
---

# OpenViking on Windows — deploy, wire, debug

OpenViking is an agent-native context/memory database. The server runs locally; agents talk to it over
HTTP + a stdio MCP proxy. This skill encodes the Windows-specific hard-won details.

**Reference implementation**: this repository. Start at `SOP.md` (reproducible build), then
`TROUBLESHOOTING.md` (symptom → cause → fix). Scripts live in `scripts/` and auto-detect paths —
nothing is hardcoded to a username.

---

## 1. The host shim problem (READ FIRST)

WorkBuddy and similar hosts inject two interception layers into child processes:

| Injected | Value | Hijacks |
|---|---|---|
| `PYTHONPATH` | `...\cli\vendor\shim` (contains `sitecustomize.py`) | Python `os.remove` |
| `NODE_OPTIONS` | `--require=.../node-language-shim.cjs` | Node `fs.unlink` |

**Symptoms it causes:**

- `openviking-server` → `Application startup failed. Exiting.` with
  `[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED] {"count":199,"threshold":50,...}`
  (the server cleaning stale RocksDB / `.openviking.pid` lock files gets blocked)
- `dsh plugin add` / `pnpm` → the same `SAFE_DELETE_BULK_CONFIRM_REQUIRED count:199 threshold:50`
- Any bulk delete in a tool call → the whole command aborts before doing its real work

**Fix — clear both layers before starting the server:**

```bash
unset NODE_OPTIONS; unset PYTHONPATH; unset PYTHONSTARTUP
export CODEBUDDY_SAFE_DELETE_ENABLED=0
```

- `CODEBUDDY_SAFE_DELETE_ENABLED=0` is the **official master switch** (`sitecustomize.py` reads it).
- ⚠️ **`unset NODE_OPTIONS` alone does NOT fix the Python side** — the shim is loaded via
  `PYTHONPATH`/`sitecustomize`, so `PYTHONPATH` must be cleared too.
- Never combine a delete with a server start in the same tool call — if the guard fires, the start never runs.

Put this in a launcher (`scripts/start-ov.sh` / `start-ov.cmd`) and always call that, rather than
inlining the clear-and-start logic.

## 2. Install (Windows has no official installer)

`install.sh` hardcodes macOS/Linux (`exit 1`, no bypass). Install per-agent instead:

```powershell
uv tool install openviking
# then each agent's own plugin manager, e.g.:
claude plugin install openviking
dsh plugin --profile web add ...     # clear the shim first
```

Codex: install through Codex's own marketplace mechanism — **do not hand-edit `config.toml`**.

```bash
python scripts/install-codex-plugin.py
```

It creates a local marketplace under `~/.codex/local-marketplaces/openviking/` whose manifest
points at the plugin, then runs `codex plugin marketplace add` + `codex plugin add`. Codex
writes the `config.toml` sections (marketplace + `[plugins."openviking-memory@openviking"]`)
itself.

Two silent traps:

1. **`source.path` in the marketplace manifest must be relative and inside the marketplace
   root.** `./plugins/openviking-memory` works; an absolute path or `../..` makes the plugin
   disappear from `codex plugin list` with no error.
2. **Hand-writing `[mcp_servers.openviking-memory]` alone does nothing.** Without the
   marketplace/plugin registration Codex never merges the plugin's `.mcp.json`, and the
   session reports "no available resources or resource templates" — looks like a broken
   server, actually an un-enabled plugin. `plugin_hooks = true` is the *old* switch; new
   Codex reads `hooks` (on by default).

Then **restart Codex** and run `/hooks` inside it to approve the hooks — Codex stores a
`trusted_hash` per hook and silently skips unapproved ones. `/mcp` should list
`openviking-memory` with 15 tools.

Network: direct `github.com` may fail behind a firewall — set `HTTPS_PROXY` / `HTTP_PROXY`.
The TOS mirror `ovrelease.tos-cn-beijing.volces.com` usually works direct.

## 3. Credentials — two different keys

| Key | Where | Purpose |
|---|---|---|
| `root_api_key` | `ov.conf` `server.root_api_key` | admin only. **Data-plane calls give 403** |
| user `api_key` | `ovcli.conf` `api_key` | data plane; mint with `ov admin register-user <acct> <user>` |

Set the provider key as a **user-level env var** (`setx SILICONFLOW_KEY "sk-..."`) and reference it in
`ov.conf` as `${SILICONFLOW_KEY}` so no plaintext secret lands in the config.
Tighten `ovcli.conf`: `icacls "<path>" /inheritance:r /grant:r "<user>:F"`.

## 4. Provider config gotchas

- `provider: "openai"` → OV **does NOT send `dimensions`**. Convenient: `BAAI/bge-m3` (1024-d, free)
  *rejects* `dimensions` (error 20015) — the `openai` provider sidesteps this exactly.
- **`rerank.api_base` must be the full endpoint** (`https://<host>/v1/rerank`, singular `rerank`).
- **No separate `llm` slot exists** — memory extraction reuses the `vlm` slot in **text** mode.
- Probe endpoints *before* configuring — `401` means "path correct, missing credentials", `404` means wrong path.

## 5. THE extraction fix: `python` → `json` protocol

By far the highest-impact setting. The default is `"python"` (a restricted internal memory-SDK
assignment DSL). Third-party / self-hosted models usually fail it every retry:

```
ERROR extract_loop:run:420 Failed to parse memory operations (iteration 4/4)
failure_kind=parse_error  error=Line 2: only one simple assignment target is allowed
```

Add to `ov.conf`:

```json
"memory": { "extraction_output_format": "json" }
```

Measured on identical input: **19 memories vs 0**, and **14k tokens vs 35k**.

## 6. VLM capability floor

Official `setup_wizard.py`: *"`qwen3.5:4b` is the smallest VLM we recommend — smaller models fail OV's
memory extraction (they copy the prompt's few-shot examples into fabricated memories)"*.
Recommended ladder: 4B → 9B → 27B → 35B → 122B.

Observed failure of an 8B VL model: it returned `identity.md` / `profile.md` / `soul.md` merely
translated into another language (`before == after` semantically), zero `adds`.
Band-aid fix: use `Qwen/Qwen3-VL-30B-A3B-Instruct` (30B MoE, 3B active — cheap, keeps vision).

Also required: `"server": {"agent_evolution": {"enabled": true}}` — the **default `false`** silently
yields empty agent-stage memory with `agent_memory_skip_reason: "agent_evolution_disabled"`.

## 7. Memory stages — commit produces BOTH stages

Source proof — `openviking/session/compressor_v3.py`:

```python
if agent_evolution_enabled and _TRAJECTORIES_MEMORY_TYPE in agent_memory_types:   # L544
```

`SessionService` threads `agent_evolution_enabled` into
`SessionCompressorV3.extract_long_term_memories(...)` (`service/session_service.py` L210 / L480).

| Stage | Types | Produced by |
|---|---|---|
| `user` | identity, profile, soul, preferences, entities, events | `ov session commit` (automatic) |
| `agent` | experiences, trajectories, cases | **also `ov session commit`** — requires `server.agent_evolution.enabled = true` |

**Trap that caused a wrong conclusion once:** `session_extract_context_provider.py` carries the comment
*"filter out agent-stage schemas (trajectory/experience are handled by execution extraction)"*.
That filter applies **only to the user-stage schema list being assembled in that code path** — it does
not mean commit skips the agent stage. Reading that one comment without following into `compressor_v3`
produces the wrong answer.

Content caveat: output still depends on the session containing a generalisable execution trace —
a pure "here are my preferences" list yields user-stage memories only, while a "do a task + reflect"
exchange also yields trajectories / experiences / cases.

## 8. Verify the loop (don't trust "it installed")

```bash
ov health ; ov status ; ov config validate
ov session new --output json                    # grab session_id
ov session add-messages <sid> '<json array>'    # [{role,content},...]
ov session commit <sid> --output json           # returns task_id (ASYNC)
ov wait ; ov task status <task_id> --output json  # look at memories_extracted / token_usage
ov read viking://user/default/sessions/<sid>/history/archive_001/memory_diff.json
ov ls viking://~/memories
```

Or just run `python scripts/ov-doctor.py` — it checks all of the above plus Studio auth and the
process parent chain.

**Debugging extraction quality**: server logs go to **stdout, not a log file** — capture the launcher's
output and grep `extract_loop` / `parse_error`. `memory_diff.json` shows adds/updates;
`before == after` means the model was told to edit but produced nothing meaningful.

**Pitfall — recall returns `{}`**: two distinct causes.
1. Hook log shows `{"stage":"stdin_parse","reason":"invalid input"}` → your JSON never parsed.
   Git Bash `echo` encodes non-ASCII in the console codepage (GBK), producing invalid UTF-8.
   **Pipe from a UTF-8 file** (`node script.mjs < payload.json`) instead of `echo '...' |`.
2. `spawn codex ENOENT` → the recall *compressor* can't find the codex CLI. Non-fatal;
   degrades to uncompressed. Silence with `OPENVIKING_RECALL_COMPRESS=off`.

Enable `OPENVIKING_DEBUG=1` to get `~/.openviking/logs/codex-hooks.log`.

### ⚠️ The false-memory trap — read before verifying anything

**Never seed a verification session with unverified background context written as if the *user* said it.**
The VLM will faithfully extract it into durable memory, and you end up with confident, wrong facts that
recall serves back forever — worse than having no memory at all, because recall attaches a `viking://`
reference that makes the error look authoritative.

Rules:

- Seed verification sessions with **measured values** (`$env:COMPUTERNAME`, `whoami`, `Get-NetIPAddress`)
  or content explicitly labelled as test data — never with remembered context.
- **One bad memory means the whole batch needs auditing.** They came from the same unverified source.
  Dump every sibling written in that commit with `scripts/ov-audit-tree.py`.
- Repair by **delete + re-create under the correct key**, not by editing the body in place: URIs embed
  the key (`entities/网络/<wrong-ip>.md`), so a body-only edit leaves a lying filename behind.
- Confirm the fix through the **recall path**, not just `ov read` — retrieval can surface a stale copy.

## 9. Peer / namespace

- Peer is derived from the git origin URL or cwd. **No git repo → no peer → memories land in the
  user-level space.** For multi-agent memory *sharing* that is usually what you want.
- For per-project isolation, add `.openviking/config.json`: `{"version":1,"peer":{"id":"my-project"}}`.
- ⚠️ Inconsistent peer derivation across agents causes silent zero recall — always verify with a real
  cross-agent recall, not by checking "the plugin is installed".

## 10. Studio web UI is blank — Windows `mimetypes` says `.js` is `text/plain`

**Symptom**: `http://localhost:1933/studio/` renders a **totally blank page** — no error, no login form.
`/studio/` returns 200, `index.html` is fine, `assets/*.js` also returns 200. CSS works. Still blank.

**Root cause**: Studio assets are served by `FileResponse(path)` with **no explicit `media_type`**, so
Starlette calls `mimetypes.guess_type()`. On Windows, Python's `mimetypes` reads the **registry** file
associations, and `.js` is registered as `text/plain`:

```
mimetypes.guess_type('a.js')  -> ('text/plain', None)   # observed
```

> ⚠️ **Browsers enforce strict MIME checking for ES modules** — a non-JavaScript MIME type means the
> module is **refused execution**. The script never runs, `<div id="app">` stays empty, and you get a
> silent fully-blank page.

**Fix (no OV source edit, no registry edit)** — `python scripts/apply-mime-fix.py` drops a `.pth` into
the tool's site-packages so the interpreter patches the mapping at startup:

```python
import mimetypes; mimetypes.add_type('text/javascript', '.js', strict=True); mimetypes.add_type('text/javascript', '.js', strict=False); mimetypes.add_type('text/javascript', '.mjs', strict=True); mimetypes.add_type('text/javascript', '.mjs', strict=False)
```

Verify `content-type: text/javascript; charset=utf-8`, then **restart the server** — `mimetypes`
initializes once per process.

`/` 302-redirects to `/studio/`. If a **service worker** was registered by an earlier visit, the stale
cache keeps serving the bad responses — hard-refresh (Ctrl+Shift+R) or DevTools → Application →
**Clear site data**.

## 11. Studio loads, but every sidebar link bounces back to `/studio/settings`

**Root cause** (read out of the bundled frontend, not guessed). Studio installs a **global axios
response interceptor**: **any `401` / `UNAUTHENTICATED` response force-redirects to `/studio/settings`**
— and `/studio/settings` makes no API calls, which is why it is the only page that "works".

Studio is a **pure client-side app**: credentials live in `localStorage['ov_console_connection']`.
The server injects nothing. Empty credential → every call 401s → every page bounces.

**OpenViking has a two-key model**:

| Capability | Endpoint | Needs |
|---|---|---|
| dashboard / monitoring / tasks | `/api/v1/console/*`, `/api/v1/observer/*`, `/api/v1/tasks` | either key |
| **skills** | `/api/v1/skills` | **user key** (root → 403) |
| **agent experience** | `/api/v1/agent-evolution/experiences/*` | **user key** |
| **retrieval** | `/api/v1/search/search` | **user key** |
| **sessions** | `/api/v1/sessions` | **user key** |
| user management | `/api/v1/admin/accounts/*` | **root key** |

> ⚠️ Distinguish the failure modes: **401 → redirect to settings**, **403 → page opens but shows no
> data**. If only the root key is set, data pages fail *silently* rather than bouncing.

**Fix** — fill both keys in Studio → 连接设置. Shortcut: save with only the admin key, then 用户管理 →
select `default / default` → click **使用** to auto-fill the user key.

**Verify** with `python scripts/check-studio-auth.py` (401 / 403 / 200 matrix + verdict).

**Do not "fix" this by switching `auth_mode` to `dev`** — it disables auth entirely *and* can break the
agent memory plugins, which rely on a user key for peer/namespace partitioning.

## 12. Autostart — without it, memory dies silently

**Highest-value operational fix in this skill.** A manually-started server dies with the shell/session
that spawned it, and when it goes down **nothing reports an error** — the agent hooks degrade gracefully
and simply stop recalling/writing. Everything looks fine while memory is dead.

Check the parent chain. Healthy = ends at Task Scheduler's `svchost.exe`, not at your shell:

```powershell
python scripts/ov-doctor.py --parent-chain
```

Register a logon-triggered task (no admin rights, no stored password):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register-autostart.ps1
```

⚠️ **`-ExecutionTimeLimit ([TimeSpan]::Zero)` is mandatory.** The Task Scheduler default is **3 days**,
after which Windows force-kills the task. A memory server that quietly dies on day 4 is a nasty bug.

⚠️ **The launcher must not use `pause`** — it hangs forever when the task runs it headless. Send output
to a log file instead. Also avoid `%date%` in that log on a Chinese Windows: the batch codepage is GBK,
so the weekday lands in the file as mojibake. Use `%time%` only.

The trigger is **logon-only**, so killing the process does not restart it — intentional, otherwise you
could never stop the service by hand.

## 13. Writing non-ASCII text into OpenViking

Git Bash on Windows converts command-line arguments and pipe contents using the local codepage (GBK on a
Chinese system), so Chinese text handed to `ov.exe` arrives as mojibake. Two reliable routes:

1. **Python `subprocess`** — Windows uses `CreateProcessW` (wide-char API), so arguments survive intact.
   Reference: `scripts/ov-commit-zh.py`.
2. **UTF-8 file as stdin** — for the Node hook scripts: `node script.mjs < payload.json`.

Note: `ov add-memory` is marked **experimental and absent from some server builds** — the CLI may return
`NOT_FOUND`. Use the session-commit path instead; that is what the agents themselves do.

A `.cmd` reading `.env` must have `KEY=VALUE` with **no spaces** — batch `for /f "tokens=1,* delims=="`
keeps leading spaces and corrupts the key.

## 14. Minor Windows issues

- Transient file lock: `lock I/O error ... another program has locked a portion of the file` — **just retry**.
- `config validate` may report "未知 (自定义)" for a hand-written `ov.conf` — harmless; trust
  `ov health` / `ov status`.
- Server must be restarted after **any** `ov.conf` change (no hot reload).
- `HEAD` is not allowed on `/studio/*` (405, GET only) — use `curl -D - -o /dev/null`, not `curl -I`.
- Some machines **blacklist `reg.exe`** by security policy. Prefer Python-layer fixes (`.pth`).
- ⚠️ **Do not `unset PYTHONPATH` in a long-lived interactive shell**: the shim's
  `shell-runtime-bash-env.sh` runs at shell init and fails, after which coreutils
  (`ls`/`grep`/`head`/`sed`/`wc`) become `command not found`. Prefer running the clear-and-start logic
  inside a **script**.

## 15. Reusable troubleshooting tree

```
server won't start        -> clear PYTHONPATH + NODE_OPTIONS, set CODEBUDDY_SAFE_DELETE_ENABLED=0
403 on data-plane calls   -> use the user-level api_key, not root_api_key
doctor: api_key (none)    -> ov admin register-user <acct> <user> -> ovcli.conf api_key
commit OK but 0 memories  -> (a) memories_extracted {} + parse_error -> extraction_output_format=json
                             (b) skip_reason agent_evolution_disabled -> enable it
                             (c) empty adds, no error -> VLM too small (<4B, or ~8B VL behaving like it)
adds only identity/profile/soul -> normal; session had no user-stage material
experience/trajectory missing   -> agent_evolution.enabled=false (set true) OR session has no
                                   generalisable execution trace.
                                   NOTE: commit DOES produce agent-stage memory; do not blame commit
recall {} with no error         -> check hook log for stdin_parse: invalid input (encoding) vs codex ENOENT (non-fatal)
Studio blank, no errors         -> curl -D - the /studio/assets/*.js ; text/plain instead of text/javascript
                                   => mimetypes .pth fix + restart
sidebar links bounce to         -> 401 UNAUTHENTICATED triggers a global "go to /settings" interceptor
  /studio/settings                  fill BOTH keys in 连接设置
                                   403 = wrong key role (silent empty page), 401 = bounce
recall/write stops after a      -> server died with its parent shell. Check the process parent chain;
  reboot or session close           if it ends at bash.exe instead of svchost.exe, register the
                                   logon scheduled task (see §12). No error is surfaced.
task ran fine, dead on day 3-4  -> scheduled task default ExecutionTimeLimit. Set it to PT0S (§12).
Chinese text arrives as mojibake -> don't pass it as a CLI arg / pipe through Git Bash; use
                                   ov-commit-zh.py (Python subprocess) or a UTF-8 stdin file (§13).
```
