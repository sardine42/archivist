# 定时调度运行手册

日报/周报/月报有**两条独立的调度路径**,共用同一条流水线和同一套互斥护栏。两者**二选一**即可;同时开启也安全(见 [§4](#4-两条路径如何共存))。

| | A. 系统 cron | B. 会话内调度 |
|---|---|---|
| 触发者 | 操作系统 crontab | remote-control Claude 会话 |
| 执行体 | `.venv/bin/archivist digest run*`(headless) | `/run-digest` skill(subagent 编排) |
| 启用接口 | **`scripts/cron/install.sh`** | **`/schedule-digest`** skill |
| 适合 | 常开机的本机,无人值守 | 需要逐篇自愈 + 实时推送、可远程操控 |
| 自愈 | 脚本级重试 + 落日志(不改代码) | subagent 逐篇校验 reading.md + 保守重试 |

两条路径都跑同一条 6 步流水线:`fetch → score → deep-read → theme → push → deploy`。

---

## 1. 通用前置

```bash
# 项目根下,venv 已就绪
.venv/bin/archivist --help          # 自检 CLI 可用
.venv/bin/archivist digest list     # 看已有 digest
```

- 目标时区:Asia/Shanghai。覆盖范围由 `_coverage_range` 决定(周末感知:Mon→Fri、Tue→Sat–Mon、Wed/Thu/Fri→前一日)。
- 推送后端由 `config.local.yaml` 的 `notify.backend` 决定(`lark` / `none`)。当前为 `none`,推送是 no-op —— **但结局始终落 `logs/cron/` 日志,不会静默**。

---

## 2. 路径 A —— 系统 cron

### 启用 / 刷新(幂等)

```bash
scripts/cron/install.sh
crontab -l            # 核对已写入
```

`install.sh` 把 `scripts/cron/crontab.txt` 里的占位路径替换成当前项目根,写进 crontab 的一个带标记块(`# >>> archivist digest jobs ... >>>`),**不动你其它的 crontab 条目**。重复运行只替换这个块。

装好的三个任务:

| 任务 | 触发 | wrapper |
|---|---|---|
| daily | Mon–Fri 10:00 | `scripts/cron/daily-digest.sh` |
| weekly | Tue 10:30 | `scripts/cron/weekly-digest.sh` |
| monthly | 每月 1 号 10:00 | `scripts/cron/monthly-digest.sh` |

### 关闭 / 查看

```bash
scripts/cron/install.sh --uninstall   # 只删 archivist 块
scripts/cron/install.sh --show        # 打印已装的块
```

### 手动单跑(补做某天 / 调试)

```bash
scripts/cron/daily-digest.sh                       # 跑今天(走 wrapper:flock + 日志 + 结局)
.venv/bin/archivist digest run --date 2026-05-21    # 跑指定日(已存在则跳过)
.venv/bin/archivist digest run --date 2026-05-21 --force   # 强制重生成
```

### macOS 注意

- BSD cron **忽略 `CRON_TZ`** —— 任务按**系统时区**触发。确认本机时区是 Beijing(`date '+%Z %z'`),否则按差值调整 crontab 里的小时。
- `/usr/sbin/cron` 需要在「系统设置 ▸ 隐私与安全性 ▸ 完全磁盘访问」里授权,否则任务静默不跑。
- `install.sh` 在 macOS 上会把这两条提示打出来。

---

## 3. 路径 B —— 会话内调度

在一个 **remote-control 会话**(Claude APP/Web 通过 `/remote-control` 接管的本地会话)里:

```
/schedule-digest          # arm:注册会话内 cron → 工作日触发 /run-digest
```

该 skill 会先和你确认成本(真实跑流水线 + 部署),确认后调用 `CronCreate`:

- `cron = "3 10 * * 1-5"`(工作日 10:03 本地时区,比系统 cron 晚 3 分,做安全冗余)
- `prompt = "/run-digest"`,`recurring = true`,`durable = true`(写 `.claude/scheduled_tasks.json`,重启恢复)

**注意**:会话内 cron **只在该会话存活时触发**;`durable` 只保证重启后恢复,不等于离线无人也能跑。`recurring` 任务 **7 天后自动过期**,需重新 arm。

关闭 / 查看:`CronList` 找到 `prompt=/run-digest` 的 job → `CronDelete`(或对 skill 说「关闭会话内调度」)。

`/run-digest` 自身的编排细节(Step 0 幂等+锁 → 6 推送、逐篇校验 reading.md 的保守自愈)见 `.claude/skills/run-digest/SKILL.md`。

---

## 4. 两条路径如何共存

两条路径共享:

1. **文件锁** `/tmp/archivist-digest.lock`(`flock`)—— 同一时刻只有一个实例在跑,另一个拿不到锁就跳过;
2. **幂等护栏**(`run_daily` 内置)—— `archive/digests/<年>/daily/<date>.md` 已存在且非 `--force` 即跳过。

所以**同时开启也绝不双跑**:谁先拿锁谁出 digest,另一边要么抢不到锁、要么看到当日已存在,都干净跳过。常规建议二选一:

- 本机常开、无人值守 → **路径 A**;
- 想要逐篇自愈 + 实时推送 + 可远程操控 → **路径 B**(留一个 remote-control 会话常驻)。

---

## 5. 排障

| 现象 | 查这里 |
|---|---|
| 某天没出 digest | `logs/cron/daily-digest-<date>.log` 末尾的 `==== outcome: ... ====` 行 |
| 出了但精读数不对 | 同上日志里的 `⚠️ deep-read failures K/N: <ids>`;逐篇校验 `archive/papers/<年>/<slug>/reading.md` 是否存在 |
| 系统 cron 不触发(macOS) | 时区(`CRON_TZ` 被忽略)+ cron 的完全磁盘访问授权 |
| 推送没收到 | `config.local.yaml` 的 `notify.backend`(当前 `none` 是有意关闭);日志里仍有 outcome 行 |
| 锁冲突跳过 | 日志 `exit=75` / `另一 digest 实例正在运行` —— 正常的互斥,非故障 |

> 设计原则:即便推送关闭,**结局也必须留痕**(每个 wrapper 末尾都会把 outcome echo 进当天日志)。任何「跳过 / 空日 / 锁冲突 / 失败 / 精读未落库」都不静默。
