---
name: schedule-digest
description: 配置、查看或关闭 Archivist 的两种 digest 调度：会话内由现有主会话定时触发 $run-digest 并使用 subagent；会话外由系统 cron 触发 archivist digest run 系列命令并使用 codex exec CLI 子进程。用户要求 schedule digest、会话内调度、系统定时、启用或关闭日报/周报/月报时使用；启用前必须确认 LLM 与部署成本。
---

# 日报调度

严格区分两条路径，不混用子 Agent 机制：

- 会话内：定时回到同一个主会话，主会话用 `$run-digest` 编排 subagent。
- 会话外：操作系统 cron 调用 wrapper，Python 编排器用 `codex exec` 启动 CLI 子 Agent。

若用户没有指定路径，先询问选择哪一种。启用任何路径前，除非用户已明确授权，说明它会真实执行抓取、Agent 评分与精读、推送和部署，并确认成本。建议先手动跑通对应路径。

## 会话内调度

使用 ChatGPT 桌面端“在当前聊天中调度”的 Scheduled 任务，不要创建每次开启新聊天的 standalone task。Codex CLI 和 IDE 没有 Scheduled 管理界面；当前 surface 无创建能力时，只给出配置步骤，不声称已创建。

配置要求：

- 项目：当前本地项目，使用主 checkout，不使用隔离 worktree。
- 时间与 Prompt：
  ```text
  工作日 10:03：
  $run-digest 在当前主会话中运行今天的 Archivist 日报。判断型步骤必须使用 subagent，禁止调用 archivist digest run 或 codex exec。报告成功、空日、已存在、锁冲突、部分精读失败或整体失败，不要静默。

  周二 10:30：
  $run-digest weekly 在当前主会话中运行上一个 ISO 周的 Archivist 周报。综述必须使用 subagent，禁止调用 archivist digest run-weekly 或 codex exec。完整报告结局，不要静默。

  每月 1 日 10:00：
  $run-digest monthly 在当前主会话中运行上一个自然月的 Archivist 月报。综述必须使用 subagent，禁止调用 archivist digest run-monthly 或 codex exec。完整报告结局，不要静默。
  ```
- 权限：workspace write + 网络；确认 subagent 功能已启用。

查看、暂停、恢复或删除任务都在 Scheduled 界面完成。报告任务名称、下次运行时间和绑定的主会话。

## 会话外调度

系统 cron 是无主会话的 headless 路径。它通过 `scripts/cron/*-digest.sh` 调用 `.venv/bin/archivist digest run*`；Python 仅在评分、精读和综述处通过 `src/archivist/services/codex_runner.py` 启动 `codex exec`。

- 启用或刷新：`scripts/cron/install.sh`
- 查看：`scripts/cron/install.sh --show`
- 关闭：`scripts/cron/install.sh --uninstall`

这些命令会修改用户 crontab，仅在用户明确要求对应操作时执行。安装后报告实际任务、时区和日志路径 `logs/cron/`。

## 共存

两条路径共用 `/tmp/archivist-digest.lock`，日报文件存在检查是第二层幂等护栏。谁先拿到锁谁运行，另一条路径必须报告锁冲突并退出；不要依靠“错峰三分钟”替代互斥。常规建议二选一。
