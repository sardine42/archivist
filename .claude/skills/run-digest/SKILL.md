---
name: run-digest
description: 会话内 ArXiv 日报编排(subagent 版)。确定性步骤直接跑脚本,只有评分/精读/综述三处派生 subagent;含逐篇保守自愈与结果推送。供会话内定时调度(CronCreate / loop)触发,与系统 cron 经 flock + 幂等护栏互斥,绝不双跑。触发词:run digest、跑日报、会话内日报、digest 调度。
---

# 会话内日报编排(subagent 版)

你是日报**编排器**。原则:**无需 LLM 的步骤用你自己的 Bash 直接跑脚本;只有评分、精读、综述三处用 Agent 工具派生 subagent。**

- 所有命令在**项目根**下执行,用 `.venv/bin/archivist`(勿写绝对路径)。
- 目标日期 `<DATE>`:默认今天(Asia/Shanghai);调度/用户给了日期就用它。
- **保守自愈**:短暂错误(网络 / IncompleteRead / 429 / 超时)最多重试 2 次;其余错误或重试仍失败 → **不自行改代码或配置**,推送告警(错误摘要 + 对应日志路径)交用户处理。

与系统 cron 的关系:两条路径共用 `/tmp/archivist-digest.lock` 与幂等护栏(`run_daily` 内置),本技能只是把同一条流水线在会话内用 subagent 重新编排,**不替代** headless 的 `archivist digest run`。

---

## Step 0 — 幂等 + 互斥锁(Bash)

1. 若 `archive/digests/2026/daily/<DATE>.md` 已存在 → 推送「📅 `<DATE>` 日报已存在(系统 cron 已完成),跳过」,**结束**。
2. 取文件锁(整个流程持锁,避免与系统 cron 并发):
   ```bash
   exec 9>/tmp/archivist-digest.lock
   flock -n 9 || { echo LOCKED; exit 75; }
   ```
   拿不到锁 → 推送「⏭️ 另一 digest 实例正在运行,本次跳过」,**结束**。

## Step 1 — 抓取候选(Bash)

```bash
.venv/bin/archivist digest fetch-candidates --date <DATE>
```
- 输出 JSON 的 `total_candidates == 0` → **空日**:推送「📭 `<DATE>` 空日(0 候选)」,**结束**(不评分 / 不精读 / 不部署)。
- 候选写在 `archive/papers/_candidates_<DATE>.json`。

## Step 2 — 评分(subagent · LLM)

派生**一个** subagent,任务:
> 读 `archive/papers/_candidates_<DATE>.json` 的 `candidates` 数组,按 `src/archivist/services/digest_prompts/score_papers.md` 的口径 + `archive/criteria/scoring-criteria.md` 的标准逐篇打分,把分数 JSON 数组写到 `/tmp/scores_<DATE>.json`。

每篇对象 schema:
```
{ arxiv_id, score(1-10 整数), category[ generative-rec|discriminative-rec|llm|other ],
  tags[], proposed_tags[], summary_zh, summary_en, score_reason, skip_reason, model_name }
```

## Step 2b — 落库 + 精读短名单(Bash)

```bash
.venv/bin/archivist arxiv apply-scores --date <DATE> --scores /tmp/scores_<DATE>.json
```
- 写 brief(score≥4 且未归档),并回吐 `shortlist`(score≥7、未归档、top-6)。
- **工业界过滤**:推荐算法论文(generative-rec / discriminative-rec)仅在有工业界参与时才进 `shortlist`;纯学术推荐论文只写 brief、不精读(LLM / other 类不受影响)。工业界判定 = 评分时打的 `industrial` tag(Step 2 评分已据摘要判断,所以这步零额外成本)。被过滤的篇目在输出 JSON 的 `filtered_rec_academia` 里列出——不静默。由 `config.yaml` 的 `arxiv.deep_read_rec_requires_industry` 控制(默认 true)。
- `shortlist` 为空 → 跳到 Step 4(有候选但无精读项,仍出日报)。

## Step 3 — 精读(并行 subagents · LLM)

对 `shortlist` 里**每篇**,在**同一条消息里并行**派生 subagent,prompt 即:
```
/read-paper <arxiv_id> --title "<title>"
```
`/read-paper` 自带下载 → 精读 → figure 抽取 → 语义孪生 → `apply-reading` 落库,**自包含**。

**逐篇校验 + 保守自愈**(关键):subagent 正常返回**不等于**精读成功——它可能干净退出却没落库(PDF 下载/读取失败)。每篇返回后**必须校验** `archive/papers/2026/` 下存在 arxiv_id 匹配该篇且**含 `reading.md`** 的目录:
- 校验通过 → 记为成功。
- subagent 报错 **或** 校验不通过(没 reading.md)→ 视为失败,只重试**那一篇** 1 次。
- 重试后仍未落库 → 记下该篇为失败、**继续其余**(不阻断整批),并在 Step 6 推送里列出失败篇目。


## Step 4 — 综述(Bash + subagent · LLM)

1. Bash:`.venv/bin/archivist digest daily-prepare --date <DATE> > /tmp/prepare_<DATE>.json`
2. subagent:读 `/tmp/prepare_<DATE>.json` + `src/archivist/services/digest_prompts/daily_theme.md`,输出 `{ theme, theme_tags[], highlights[], summary }` 到 `/tmp/theme_<DATE>.json`。
3. Bash:`.venv/bin/archivist digest daily-write --date <DATE> --json /tmp/theme_<DATE>.json`

## Step 5 — 部署(Bash)

```bash
.venv/bin/archivist deploy
```
(host 取自 config;失败按「保守自愈」处理——重试短暂错误,否则告警,但**不**因部署失败回滚已写好的日报。)

## Step 6 — 推送结果(编排器自己)

推送一条总结,形如:
> ✅ `<DATE>` 日报完成 —《<主题>》;N 篇候选 / K 篇精读 / 已部署。失败精读:<若有>。

---

## 失败处理总则(保守)

- 任一 Bash 步非零退出:先判断是否**短暂错误** → 重试 ≤2;否则推送告警(stderr 摘要 + `logs/` 对应路径),**不改代码/配置**。
- subagent 失败:评分 / 综述重试 ≤2;精读按 Step 3 逐篇处理。
- 任何「跳过 / 空日 / 锁冲突 / 失败」都要推送一条说明,别静默。
