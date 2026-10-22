---
name: deploy
description: 构建静态网站并部署到生产服务器，然后提交项目框架改动并推送到 GitHub。
---

# 部署与提交

构建网站、部署到服务器、提交 Git 改动并推送 main。

## 步骤

### 1. 构建并部署网站

```bash
source .venv/bin/activate && archivist deploy
```

确认输出中包含 `Deploy complete.`。

### 2. 提交 Git 改动

1. 确认当前在 main 分支：`git branch`
2. 查看改动：`git status -s` 和 `git diff --stat`
3. 只暂存本次项目框架相关文件：`git add <files>`。用户配置、归档内容和工作记录不得提交，也不得使用 `-f` 强制添加这些数据。
4. 运行 `uv run python scripts/check-repository-privacy.py`，并审阅 `git diff --cached`，确认没有个人信息或运行记录。
5. 提交信息只概括工程变化，不含个人会话、真实运行统计或部署记录；运行 `uv run python scripts/check-repository-privacy.py --message-file <file>` 检查拟提交信息后再提交。

### 3. 推送到远程

```bash
git push github main
```

(远程名为 `github`，远程分支为 `main`；必要时 `--force-with-lease`)
