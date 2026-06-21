---
name: schedule-digest
description: 在当前(remote-control)会话内 arm/disarm 日报自动调度 —— 注册一个会话内 cron(工作日触发 /run-digest),与系统 cron 经 flock + 幂等护栏互斥共存。触发词:启用会话内调度、arm digest、schedule digest、注册日报定时、关闭会话内调度。有 LLM + 部署成本,仅在用户明确放行后 arm。
---

# 会话内日报调度(arm / disarm)

把 `/run-digest`(subagent 版编排器)挂到**会话内 cron**(`CronCreate` 工具)上,让一个常驻的 remote-control 会话在工作日自动出日报。系统侧的对应物是 `scripts/cron/install.sh`(系统 crontab);两者二选一即可,同开也安全(见末节)。

## arm 前必须确认(除非用户已明确放行)

1. 这会**按时真实跑整条流水线**(抓取 → 评分 → 精读 → 综述 → 部署),有 LLM 与部署成本。
2. 建议先手动 `/run-digest` 跑通一次端到端,再 arm。
3. 当前会话是不是 remote-control 会话?会话内 cron **只在本会话存活时触发**(`durable: true` 也只是重启后恢复,不等于离线运行)。

确认前别直接调 `CronCreate`。

## arm(注册)

确认后调用 **`CronCreate`**:

- `cron`: `3 10 * * 1-5`
  —— 工作日 10:03(**本地时区**)。比系统 cron 的 10:00 晚 3 分:若系统 cron 已先出当日 digest,这次会命中 `run_daily` 幂等护栏直接跳过,安全冗余。
- `prompt`: `/run-digest`
- `recurring`: `true`
- `durable`: `true` —— 写入 `.claude/scheduled_tasks.json`,会话重启后恢复。用户要求「仅本次会话」则用 `false`。

注册成功后告诉用户:**job ID**、下次触发时刻、以及 **recurring 任务 7 天后自动过期**,需重新 arm。

## disarm(取消)

`CronList` → 找到 `prompt` 为 `/run-digest` 的 job → `CronDelete` 传它的 id。确认无误后回报已取消。

## status(查看)

直接 `CronList`,把 archivist 相关 job 的 cron 表达式 / 下次触发 / id 念给用户。

## 与系统 cron 的关系(为什么同开也不会双跑)

两条路径共用 `/tmp/archivist-digest.lock`(flock)+ `run_daily` 内置幂等护栏(`archive/digests/<年>/daily/<date>.md` 已存在即跳过):

- 谁先拿到锁谁跑,另一边拿不到锁 → 跳过;
- 即便错峰跑,后者见当日 digest 已存在 → 跳过。

所以**绝不双跑**。常规用法:常开机的本机用系统 cron(`install.sh`);需要「会话内自愈 + 实时推送」时用本技能在 remote-control 会话里 arm。详见 `docs/SCHEDULING.md`。
