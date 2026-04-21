# 配置分层

- `config.yaml` — 框架默认配置（关键词、公司列表、标签、打分权重等），受 Git 管理，可公开发布
- `config.local.yaml` — 个人覆盖层（deploy host、site base_url、lark notify_user_id 等），**gitignored**
- `archive/` — 运行时数据（论文、日报、benchmark、DAG、criteria、内部文档），**整个目录 gitignored**
- `load_config()` 运行时 deep-merge `config.local.yaml` 到 `config.yaml` 上

**禁止把用户特有信息（绝对路径、host、user_id、域名等）写入受 Git 管理的文件**。如需引用项目根，
SKILL.md 中用相对路径（如 `.venv/bin/archivist`、`scripts/download-paper.py`）。

# Git 工作流

单分支仓库，本地 `main` 直接关联远程 `github/main`。

```bash
git add <files>
git commit -m "..."
git push github main            # 必要时 --force-with-lease
```

## 关键规则

- **提交前 check**：`git status -s` 确认没误入 `archive/`、`config.local.yaml`、`_site/` 等 gitignored 内容
- **force push**：远程只有你自己在推，必要时用 `--force-with-lease`（比 `--force` 更安全，会检查远端是否被他人改过）
- **不要把绝对路径或用户信息写进受管理的文件**（否则会作为敏感信息推到 GitHub）

# Agent 写入元数据规则

所有元数据写入**必须通过 `archivist` CLI**，禁止 agent 直接调 python 脚本或用 Edit 工具改 meta.json / criteria 以外的数据文件：

- 写 paper meta：`archivist paper edit <slug> ...` 或 `archivist paper apply-reading <data.json>`
- 写 DAG / benchmark：通过 `archivist paper apply-reading`
- 查 DAG 节点：`archivist dag list-nodes`
- 列 pending 反馈：`archivist rubric list-pending`

唯一例外：`archive/criteria/` 下的评分标准文件由 `/refine-rubric` skill 直接 Edit（criteria 是 agent 在对话中多轮确认后的产物，不经 CLI）。

# 评分反馈闭环

用户可以通过两个路径提交论文评分反馈：

**CLI-only**（纯数据录入）：
```bash
archivist paper edit <slug> --rating N --rating-reason "..."
```
只写 meta.json，不触发任何 criteria 改动。数据累积到 `archivist rubric list-pending` 可见。

**Agent 会话驱动**（更新评分标准）：
在 Claude Code 会话中用自然语言提反馈（例："这篇只值 5 分，它只是比赛介绍"），或显式调用 `/refine-rubric <slug>`。Agent 会：
1. 通过 CLI 写入 rating + rating_reason
2. 若 rating 与 auto_score 不符，询问"是否更新评分标准"
3. 若同意，读 paper 精读报告 + 两份 criteria，多轮对话确认改动
4. Edit 对应 criteria 文件（`archive/criteria/scoring-criteria.md` 或 `reading-criteria.md`）
5. `archivist paper edit <slug> --feedback-consumed` 标记处理完成

`archive/criteria/` 与 `archive/papers/` 都是 gitignored 个人数据，反馈闭环不产生 git commit。
