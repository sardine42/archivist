---
name: deploy
description: 构建静态网站并部署到生产服务器，然后提交所有改动并推送到 GitHub。
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
3. 暂存相关文件：`git add`（`.gitignore` 排除的路径需 `-f`）
4. 提交，commit message 需简洁概括本次所有改动

### 3. 推送到远程

```bash
git push github main
```

(远程名为 `github`，远程分支为 `main`；必要时 `--force-with-lease`)
