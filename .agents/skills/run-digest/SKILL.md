---
name: run-digest
description: 在当前 Codex 主会话内编排 ArXiv 日报、周报或月报。用户要求 run digest、跑日报/周报/月报、补跑某个周期，或会话内定时任务触发 digest 时使用；确定性步骤由主会话执行，评分、逐篇精读和综述必须委派给 subagent，禁止回落到 codex exec 子进程。
---

# 会话内 Digest 编排

由主会话持有全流程状态和互斥锁。日报把批量评分、逐篇精读、综述交给 subagent；周报和月报只把综述交给 subagent。确定性命令由主会话运行。

## 硬边界

- 在项目根运行命令，使用 `.venv/bin/archivist` 和相对路径。
- 会话内路径必须使用当前会话的 subagent/delegation 能力；禁止调用 `codex exec`，也禁止调用会启动 CLI 子 Agent 的 `archivist digest run*`。
- 若当前 surface 没有 subagent 能力，停止并说明该路径不可用；不要静默切换到会话外路径。
- 未指定周期时运行日报。日报日期默认为 Asia/Shanghai 的今天；周报默认上一个 ISO 周；月报默认上一个自然月。
- 显式标识分别校验为 `YYYY-MM-DD`、`YYYY-Www`、`YYYY-MM`；仅当用户明确要求时覆盖已有产物。
- 短暂错误（网络、429、IncompleteRead、超时）最多重试两次；其他错误不自行修改代码或配置。

## 0. 建立运行上下文并持锁

1. 按周期检查目标文件：`archive/digests/<YYYY>/daily/<DATE>.md`、`weekly/<WEEK>.md` 或 `monthly/<MONTH>.md`。已存在且用户未要求覆盖时，报告“已存在”并结束。
2. 先运行 `mkdir -p tmp`，再用 `mktemp -d tmp/session-digest.XXXXXX` 创建本次临时目录并记录精确路径。
3. 通过一个持续运行的 shell execution session 获取共享锁，并保留该 execution session id：
   ```bash
   flock -n -E 75 /tmp/archivist-digest.lock sh -c 'echo LOCK_ACQUIRED; while :; do sleep 30; done'
   ```
   命令输出 `LOCK_ACQUIRED` 后继续工作，不要等待它自然结束。若退出码为 75，说明会话外任务正在运行；报告锁冲突并结束。
4. 从此处开始按 `finally` 语义工作：无论成功、失败或用户中断，都要向保留的 execution session 发送中断或终止它，确认 lock-holder 已退出，再删除本次精确的临时目录。不要用单次 `exec 9>...`，因为文件描述符不会跨工具调用保持。

## A. 日报

### 1. 抓取候选

运行：

```bash
.venv/bin/archivist digest fetch-candidates --date <DATE>
```

读取 `archive/papers/_candidates_<DATE>.json`。`total_candidates == 0` 时发送一条空日状态通知，释放锁并结束；不要评分、生成 stub、推送 digest 或部署。

### 2. 委派评分

派生一个 subagent，等待其完成。任务必须包含：

- 读取候选文件、`src/archivist/services/digest_prompts/score_papers.md` 和 `archive/criteria/scoring-criteria.md`。
- 对全部候选评分，严格按 prompt 的 JSON schema 输出。
- 把纯 JSON 数组写入 `<TMP_DIR>/scores.json`。
- 不调用 `codex exec`，不修改 `meta.json`、criteria 或其他归档元数据。

主会话校验 `scores.json` 是合法 JSON，且返回条数与候选数一致；失败时对评分 subagent 最多重试两次。然后运行：

```bash
.venv/bin/archivist arxiv apply-scores --date <DATE> --scores <TMP_DIR>/scores.json > <TMP_DIR>/shortlist.json
```

读取 `shortlist` 和 `filtered_rec_academia`，后者必须在最终结果中可见，不能静默丢弃。

### 3. 并行委派精读

为 shortlist 中每篇论文派生一个独立 subagent；在可用槽位内并行，超出部分分批排队。每个任务使用：

```text
$read-paper <arxiv_id> --title "<title>" --generated-by <MODEL_ID> --reasoning-effort <EFFORT>
```

派发前从主会话 `turn_context` 确认精确的 `model` 与 `collaboration_mode.settings.reasoning_effort`，不要凭配置文件或模型家族名猜测。明确要求 subagent 完整执行 `$read-paper`，原样写入这两个署名参数，且不得调用 `codex exec`。等待全部 subagent 后，不相信返回文字，逐篇在 `archive/papers/*/*/meta.json` 中按去版本号的 `arxiv_id` 定位，并确认同目录存在 `reading.md`，同时校验 `generated_by` 与 `reasoning_effort` 均已精确落库。

- 校验成功：记录成功。
- subagent 报错或未落库：只重试该论文一次。
- 重试仍失败：记录失败并继续，不阻断其余论文。

### 4. 委派日报综述

运行：

```bash
.venv/bin/archivist digest daily-prepare --date <DATE> > <TMP_DIR>/prepare.json
```

若 `stats.total == 0`，发送空日状态通知，释放锁并结束。否则派生一个 subagent，等待其完成。要求它：

- 读取 `prepare.json`、`src/archivist/services/digest_prompts/daily_theme.md` 和 `config.yaml` 的标签白名单。
- 输出 `{theme, theme_tags, highlights, summary}`。
- 把纯 JSON 对象写入 `<TMP_DIR>/theme.json`。
- 不调用 `codex exec`，不写 digest 或其他归档数据。

校验 JSON；失败时最多重试两次。然后运行：

```bash
.venv/bin/archivist digest daily-write --date <DATE> --json <TMP_DIR>/theme.json
```

### 5. 推送与部署

从 `prepare.json` 读取 `stats.total`，运行确定性收尾命令：

```bash
.venv/bin/archivist digest publish --period daily --id <DATE> --paper-count <TOTAL>
```

推送失败不应撤销已写日报；部署失败也不回滚归档。记录失败阶段并向用户报告。

## B. 周报与月报

周报和月报不抓取、不评分、不精读，只聚合已有日报。分别运行：

```bash
.venv/bin/archivist digest weekly-prepare --week <WEEK> > <TMP_DIR>/prepare.json
.venv/bin/archivist digest monthly-prepare --month <MONTH> > <TMP_DIR>/prepare.json
```

从周报 `daily_reports[*].paper_count` 求和，或读取月报 `stats.total_papers`。总数为 0 时报告空周期并结束，不写 stub、不推送、不部署。

派生一个综述 subagent，要求它读取 `prepare.json`、对应的 `weekly_theme.md` 或 `monthly_theme.md`，以及 `config.yaml` 的标签白名单；把符合 `{theme, theme_tags, highlights, summary}` schema 的纯 JSON 写入 `<TMP_DIR>/theme.json`。禁止调用 `codex exec` 或直接写 digest。校验失败最多重试两次。

根据周期运行确定性收尾：

```bash
.venv/bin/archivist digest weekly-write --week <WEEK> --json <TMP_DIR>/theme.json
.venv/bin/archivist digest publish --period weekly --id <WEEK> --paper-count <TOTAL>

.venv/bin/archivist digest monthly-write --month <MONTH> --json <TMP_DIR>/theme.json
.venv/bin/archivist digest publish --period monthly --id <MONTH> --paper-count <TOTAL>
```

只运行所选周期对应的两条命令。推送或部署失败不撤销已经写入的 digest。

## C. 收尾

终止 lock-holder execution session，确认锁已释放，再删除本次临时目录。日报汇报日期、候选数、brief 数、成功/失败精读 ID、过滤论文、产物路径、推送和部署结果；周报/月报汇报周期、聚合数量、产物路径、推送和部署结果。任何空周期、已存在、锁冲突、部分失败或整体失败都必须在主会话中明确可见。
