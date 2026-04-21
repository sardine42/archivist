---
name: refine-rubric
description: 根据用户对论文的评分反馈（rating）优化评分标准（criteria）。当用户在对话中提交评分并与自动评分（score / reading_score）不符时触发；通过多轮对话与用户确认 criteria 改动，最小增量更新 `archive/criteria/scoring-criteria.md`（摘要）或 `archive/criteria/reading-criteria.md`（精读）。触发词：评分反馈、rating、评分不准、评分标准、refine rubric。
argument-hint: "[<slug>] | [--all-pending]"
---

# 评分标准反馈闭环

## 核心原则

- **Agent 驱动**：criteria 更新必须经过多轮对话确认，不能一次性直接写入
- **差值即触发**：任何 `rating != auto_score` 的情况都需问用户"是否更新评分标准"
- **最小增量**：每次只修改相关段落，不大范围重写
- **跨 criteria 判断**：每条反馈可能影响摘要或精读 criteria，判断信号在哪份 criteria 的可见信息里可靠可判断
- **CLI 不触发**：CLI 只写入 rating 数据；本 skill 是 criteria 更新的唯一路径

## 参数

- `$ARGUMENTS`：论文 slug（优先），或 `--all-pending` 批量处理所有未消费反馈
- 若用户在对话中已给出具体 rating 和 reason，直接从对话提取

## 工作流

### Step 1. 收集/确认反馈

**单篇模式**（参数是 slug 或用户在对话中指定某篇）：
- 若用户已给出 rating + 原因：直接进入 Step 2
- 若缺失：向用户询问
  ```
  "这篇论文你想给几分？理由是什么？
  （当前自动评分：摘要 <score>、精读 <reading_score>）"
  ```

**批量模式**（`--all-pending`）：
- 运行 `archivist rubric list-pending --format json` 拿到所有未消费反馈
- 向用户展示清单（`[精读/摘要] slug | auto vs rating | reason`）并询问"按顺序处理还是挑选几篇"
- 对选中的每篇走 Step 3-7 单篇流程
- 若多条反馈指向同一方向（例如多个 paper 都提"两阶段方法不该拿高分"），主动建议**聚合为单条 criteria 改动**，减少碎片化

### Step 2. 提交反馈到 meta.json

```bash
.venv/bin/archivist paper edit <slug> \
  --rating <N> --rating-reason "<reason>"
```

### Step 3. 询问是否更新评分标准

计算 `auto_score`：
- `deeply_read=True` → `auto_score = reading_score`
- `deeply_read=False` → `auto_score = score`

**若 `rating == auto_score`**：无分歧，直接 Step 7 标记 `--feedback-consumed` 并结束。

**若 `rating != auto_score`**（差 1 分也算）：
```
"你给了 <rating>，自动给了 <auto_score>（<摘要/精读>评分）。
想不想根据这条反馈更新评分标准？
  • 更新 → 我会读完 criteria 后提议 1-2 个方案，和你多轮确认
  • 不更新 → 反馈标记为已处理，本轮结束"
```

- 用户选"不更新" → 直接 Step 7（只标记，不改 criteria）
- 用户选"更新" → 继续 Step 4

### Step 4. 加载上下文

读以下内容作为推理输入：

1. **Paper meta**：已有（从 Step 1 或直接读 `archive/papers/**/meta.json`）
2. **精读报告**（若 `deeply_read=True`）：
   ```
   Read archive/papers/<year>/<slug>/reading.md 的"讨论与局限性"/"限制"章节（最后若干段）
   ```
   重点看 agent 自己是否已识别相关弱点——这常是 rubric 盲区的信号
3. **主 criteria 文件**（按 `deeply_read` 路由）：
   - `deeply_read=False` → `archive/criteria/scoring-criteria.md`
   - `deeply_read=True`  → `archive/criteria/reading-criteria.md`
4. **另一份 criteria**：一眼读过，判断反馈是否也该影响另一阶段评分
5. **（可选）类似先例**：用 `/archive-search` 找 rating 或 score 相近的历史 paper，看是否存在模式

### Step 5. 推理 + 向用户提议改动

**判断核心问题**：
- auto 为什么给 X？
- rating 隐含什么规则缺失/盲区？
- **这条反馈依赖的信号，在哪份 criteria 的可见信息里可靠可判断？**

**信息可见性边界**：
| 阶段 | Agent 可见信息 | 能可靠判断 | 判断不了 |
|---|---|---|---|
| 摘要评分 | title + abstract + affiliations + keywords | 主题相关性、是否工业、是否命名模型、比赛论文识别 | 方法论瓶颈、消融问题、scaling 路线、实验细节 |
| 精读评分 | 完整 PDF + 自己写的 reading.md | 所有 | 无 |

**提议方式**（1-2 个候选方案）：
- 用自然语言先解释"为什么要改 / 不改"
- 然后给出具体 draft diff（原段落 + 改后段落）
- 主动解释为什么另一份 criteria 不动（避免用户误以为漏掉）

示例提议：
```
"我建议只改精读 criteria（reading-criteria.md）。
摘要 criteria 不改，因为摘要阶段只看 abstract 无法可靠判断 <具体信号>。

方案 A（窄改）：在 7-8 分段加降分规则 —— ..."
方案 B（宽改）：加独立维度段 —— ..."

倾向 A，因为 <trade-off 说明>。你怎么看？"
```

### Step 6. 多轮交互确认

- 等用户反馈：采纳 / 微调 / 拒绝 / 换角度
- 根据意见迭代 draft
- 直到用户明确"就这样改"

### Step 7. 应用改动 + 标记

**若用户在 Step 3/6 选"更新"**：
- 用 `Edit` 工具修改目标 criteria 文件（`archive/criteria/scoring-criteria.md` 或 `archive/criteria/reading-criteria.md`）
- 修改的段落末尾追加可追溯注释：
  ```
  <!-- refined from rating feedback: <slug> -->
  ```
- 汇报修改位置（文件名、哪几行）

**若用户选"不更新"**：跳过修改，直接标记。

**始终执行标记**（无论是否改了 criteria）：
```bash
.venv/bin/archivist paper edit <slug> --feedback-consumed
```

**告诉用户**：
- "已更新 `archive/criteria/<file>.md`，标记反馈为已处理"
- 或 "反馈已标记为已处理，未改 criteria"
- `archive/criteria/` 与 paper meta.json 都是 gitignored，无需 git commit

## 关键约束（写入 criteria 时必守）

- ❌ 不在不相关段落做"顺手清理"；只动本次反馈相关的段落
- ❌ 不输出"重写整份 criteria"级别的 diff
- ✅ old_block 必须在文件中唯一且完整匹配（避免错位编辑）
- ✅ 修改末尾加 `<!-- refined from rating feedback: <slug> -->` 溯源注释
- ✅ 冲突/矛盾处（反馈与现有规则相悖）讲清楚后让用户决定，不自行和稀泥

## 错误处理

- paper slug 不存在 → 用 `archivist paper list` 帮用户找
- 无 rating / rating 非法 → 回到 Step 1 询问
- 用户意图不明 → 停下来问，不自行推测
