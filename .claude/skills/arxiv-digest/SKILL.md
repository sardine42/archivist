---
name: arxiv-digest
description: Arxiv 日报全流程。**已迁移为脚本编排**，请直接调用 archivist digest run。
argument-hint: "[YYYY-MM-DD]"
---

# Arxiv 日报全流程（已迁移为脚本）

⚠️ **不要再通过 Skill 触发该流程**。日报现在由 `archivist digest run` 命令端到端编排，
内部按需调用 `claude -p` 处理评分、精读、综述三个判断性步骤。

## 使用方式

```bash
.venv/bin/archivist digest run                      # 跑今天
.venv/bin/archivist digest run --date 2026-04-12    # 跑指定日期
```

## 自动化

可参考以下 cron 模板配置自动触发：

- `scripts/cron/daily-digest.sh` — 包装 `archivist digest run`，加 flock + Lark 通知
- crontab 表项：`0 9 * * 2-6  /path/to/archivist/scripts/cron/daily-digest.sh`

## 内部步骤（仅供参考，由 `digest_runner.py` 实现）

1. `archivist arxiv fetch` — 抓取 + 关键词预过滤 + 去重
2. `claude -p (sonnet)` — 单次调用批量评分 10-30 篇候选并落盘到 `papers_brief/`
3. `claude -p "/read-paper <id>" (opus)` — 串行精读前 6 篇 score≥7 的论文
4. `archivist digest daily-prepare` → `claude -p (sonnet)` 出主题/综述 → `archivist digest daily-write`
5. `lark-cli` 推送日报 + Pin
6. `archivist deploy` 构建 + rsync 到线上

## 评分标准

由 `digest_runner` 运行时从 `archive/criteria/scoring-criteria.md` 加载并拼入 score prompt 模板。该文件 gitignored（属于随用户反馈持续演化的个人数据），通过 `/refine-rubric` skill 迭代。
