---
name: digest-monthly
description: ArXiv 月报。**已迁移为脚本编排**，请直接调用 archivist digest run-monthly。
argument-hint: "[YYYY-MM]"
---

# ArXiv 月报（已迁移为脚本）

⚠️ **不要再通过 Skill 触发**。月报现在由 `archivist digest run-monthly` 命令端到端编排。

## 使用方式

```bash
.venv/bin/archivist digest run-monthly                       # 跑当前月
.venv/bin/archivist digest run-monthly --month 2026-04       # 跑指定月
```

## 自动化

cron 配置每月 1 号 10:00 自动触发上一月的月报：

- `scripts/cron/monthly-digest.sh`
- crontab 表项：`0 10 1 * *  /path/to/archivist/scripts/cron/monthly-digest.sh`

## 内部步骤（由 `digest_runner.run_monthly` 实现）

1. `archivist digest monthly-prepare` 聚合该月日报 + 周报 + 统计
2. `claude -p (sonnet)` 一次调用产出 Top 10 + 月度综述（800-1200 字）
3. `archivist digest monthly-write` 写 markdown + meta json
4. `lark-cli` 推送 + Pin
5. `archivist deploy`
