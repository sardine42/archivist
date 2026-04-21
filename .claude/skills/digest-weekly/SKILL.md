---
name: digest-weekly
description: ArXiv 周报。**已迁移为脚本编排**，请直接调用 archivist digest run-weekly。
argument-hint: "[YYYY-Www]"
---

# ArXiv 周报（已迁移为脚本）

⚠️ **不要再通过 Skill 触发**。周报现在由 `archivist digest run-weekly` 命令端到端编排。

## 使用方式

```bash
.venv/bin/archivist digest run-weekly                       # 跑当前 ISO 周
.venv/bin/archivist digest run-weekly --week 2026-W15       # 跑指定周
```

## 自动化

cron 配置每周二 09:30 自动触发上一周的周报（跟在日报后跑完整 5 天）：

- `scripts/cron/weekly-digest.sh`
- crontab 表项：`30 9 * * 2  /path/to/archivist/scripts/cron/weekly-digest.sh`

## 内部步骤（由 `digest_runner.run_weekly` 实现）

1. `archivist digest weekly-prepare` 聚合该周日报元数据
2. `claude -p (sonnet)` 一次调用产出 `{theme, theme_tags, highlights, summary}`
3. `archivist digest weekly-write` 写 markdown + meta json
4. `lark-cli` 推送 + Pin
5. `archivist deploy`
