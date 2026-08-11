# 定时调度运行手册

日报、周报、月报均支持两条独立路径。业务步骤相同，但子 Agent 机制不同，禁止混用。

| | A. 会话内 | B. 会话外 |
|---|---|---|
| 调度者 | 现有 Codex 主会话中的 Scheduled 任务 | 操作系统 crontab |
| 编排者 | 主会话 `$run-digest` | Python `digest_runner` |
| 判断型工作 | Codex subagent | `codex exec` CLI 子进程 |
| 确定性工作 | 分步 `archivist` CLI | 端到端 `archivist digest run*` |
| 结果位置 | 当前主会话 | `logs/cron/` + 通知后端 |
| 适合 | 需要查看、干预 subagent 过程 | 无人值守、没有存活主会话 |

两条路径共用 `/tmp/archivist-digest.lock`。digest 文件存在检查是第二层幂等护栏，不能替代运行期互斥。

## 1. 通用前置

```bash
.venv/bin/archivist --help
.venv/bin/archivist digest list
codex login status
```

- 目标时区为 Asia/Shanghai。
- 推送后端由 `config.local.yaml` 的 `notify.backend` 控制（`lark` / `none`）。
- 真实运行会产生 Agent、网络、推送和部署成本；启用前先手工跑通对应路径。

## 2. 路径 A：会话内主会话调度

在 ChatGPT 桌面端把 Scheduled 任务创建在**现有主聊天中**，不要创建每次启动新聊天的 standalone task。选择当前本地项目、主 checkout、workspace write 和网络权限，并确认 subagent 功能已启用。

按下列时间（Asia/Shanghai）创建三个绑定同一现有主聊天的任务：

```text
# 工作日 10:03
$run-digest 在当前主会话中运行今天的 Archivist 日报。判断型步骤必须使用 subagent，禁止调用 archivist digest run 或 codex exec。报告成功、空日、已存在、锁冲突、部分精读失败或整体失败，不要静默。

# 周二 10:30
$run-digest weekly 在当前主会话中运行上一个 ISO 周的 Archivist 周报。综述必须使用 subagent，禁止调用 archivist digest run-weekly 或 codex exec。完整报告结局，不要静默。

# 每月 1 日 10:00
$run-digest monthly 在当前主会话中运行上一个自然月的 Archivist 月报。综述必须使用 subagent，禁止调用 archivist digest run-monthly 或 codex exec。完整报告结局，不要静默。
```

`$run-digest` 的职责：

1. 主会话启动持续运行的 `flock` lock-holder，并在整个流程结束前保持它存活。
2. 主会话运行抓取、评分落库、prepare/write、推送和部署等确定性 CLI。
3. 日报派生一个评分 subagent、每篇一个精读 subagent、一个综述 subagent；周报/月报只派生综述 subagent。
4. 主会话等待并核验子 Agent 产物；精读以 `reading.md` 是否落库为准，失败论文只重试一次。
5. 主会话在 `finally` 清理临时目录并释放锁，把完整结局返回当前聊天。

Codex CLI 和 IDE 不提供 Scheduled 管理界面；只能用它们手工测试 `$run-digest`。查看、暂停、恢复或删除会话内任务，在 ChatGPT 桌面端 Scheduled 界面完成。

## 3. 路径 B：会话外系统定时

会话外路径不依赖存活聊天。cron wrapper 持锁并调用端到端 CLI；Python 只在评分、精读和综述处通过 `src/archivist/services/codex_runner.py` 启动 `codex exec --ephemeral`。

### 启用、查看和关闭

```bash
scripts/cron/install.sh
scripts/cron/install.sh --show
scripts/cron/install.sh --uninstall
```

安装的任务：

| 任务 | 触发 | wrapper |
|---|---|---|
| daily | Mon–Fri 10:00 | `scripts/cron/daily-digest.sh` |
| weekly | Tue 10:30 | `scripts/cron/weekly-digest.sh` |
| monthly | 每月 1 日 10:00 | `scripts/cron/monthly-digest.sh` |

wrapper 使用 `flock -n`（daily）或 `flock -w 3600`（weekly/monthly），区分 exit 0、75 和其他错误，并把结局写入 `logs/cron/`。

### 手工测试会话外路径

```bash
scripts/cron/daily-digest.sh
flock -n -E 75 /tmp/archivist-digest.lock \
  .venv/bin/archivist digest run --date 2026-05-21
```

仅当用户明确要求覆盖已有 digest 时追加 `--force`。

### macOS 注意

- BSD cron 忽略 `CRON_TZ`，任务按系统时区运行；确认系统时区为 Asia/Shanghai，或调整小时。
- `/usr/sbin/cron` 需要“完全磁盘访问”。
- wrapper 依赖 `flock`；确保运行环境已安装并位于 cron 的 PATH 中。

## 4. 两条路径如何共存

1. 会话内主会话和会话外 wrapper 都必须持有 `/tmp/archivist-digest.lock` 才能执行。
2. 谁先拿到锁谁运行；另一方报告锁冲突并退出。
3. 锁释放后，后启动者还会通过对应的 daily、weekly 或 monthly digest 文件命中幂等跳过。

10:00 与 10:03 的错峰只降低竞争概率，不是安全机制。常规建议只启用一条路径。

## 5. 排障

| 现象 | 检查 |
|---|---|
| 会话内未运行 | Scheduled 是否绑定现有聊天、本地项目是否在线、subagent 是否启用 |
| 会话内卡住 | 主会话的 subagent 列表和 lock-holder execution session |
| 会话外未运行 | `logs/cron/daily-digest-<date>.log`、cron PATH、Codex 登录状态 |
| 精读数不对 | `deep-read failures` 记录及 `archive/papers/<year>/<slug>/reading.md` |
| 锁冲突 | exit 75；确认另一条路径是否正在运行 |
| 推送没收到 | `config.local.yaml` 的 `notify.backend` 与 lark 配置 |

任何空日、跳过、锁冲突、部分精读失败或整体失败都不能静默。
