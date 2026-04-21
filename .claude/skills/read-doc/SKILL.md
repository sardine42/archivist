---
name: read-doc
description: 内部文档精读与归档。支持飞书文档、PDF、Markdown 三种输入，精读生成阅读报告，提取图表，归档到个人文档。
argument-hint: "<飞书URL | PDF路径 | MD路径> [--title \"标题\"]"
---

# 内部文档精读与归档

## 参数
- `$ARGUMENTS`: 飞书文档 URL、本地 PDF 路径或本地 Markdown 路径，可选 `--title "文档标题"`

## 流程

### 1. 获取文档内容

根据参数类型分三路处理：

**飞书 URL**（匹配 `larkoffice.com` 或 `feishu.cn`）：
1. 解析 URL 中的 token
2. `/wiki/TOKEN` 格式需先查询实际文档 token：
   ```bash
   lark-cli wiki spaces get_node --params '{"token":"<wiki_token>"}' --as user
   ```
   从返回的 `node.obj_token` 获取真实 token。
3. 获取文档 markdown：
   ```bash
   lark-cli docs +fetch --doc <token> --as user
   ```
4. 从返回 JSON 的 `data.markdown` 和 `data.title` 提取内容，保存到 `/tmp/doc-<slug>.md`
5. 创建归档目录 `archive/docs/<slug>/`

**本地 PDF**（匹配 `.pdf` 后缀）：
1. 从文件名或 `--title` 派生 slug
2. 创建归档目录 `archive/docs/<slug>/`
3. 复制 PDF 到 `archive/docs/<slug>/document.pdf`

**本地 Markdown**（匹配 `.md` 后缀）：
1. 从文件名或 `--title` 派生 slug
2. 创建归档目录 `archive/docs/<slug>/`

### 2. 提取图表

**PDF 输入**：
```bash
uv run python3 scripts/extract-figures.py archive/docs/<slug>/document.pdf archive/docs/<slug>/figures
```

**飞书文档**：从 Step 1 获取的 markdown 中提取所有媒体，逐个下载到 `archive/docs/<slug>/figures/`：

1. 用正则提取所有 `<image token="XXX"/>` 中的 token
2. 用正则提取所有 `<whiteboard token="XXX"/>` 中的 token
3. 逐个下载图片：
   ```bash
   lark-cli docs +media-download --token <image_token> --output archive/docs/<slug>/figures/fig_01 --as user
   ```
4. 逐个下载画板缩略图：
   ```bash
   lark-cli docs +media-download --type whiteboard --token <wb_token> --output archive/docs/<slug>/figures/wb_01 --as user
   ```
5. 将 markdown 中的 `<image token="XXX" .../>` 替换为 `![Figure N](figures/fig_01.png)`
6. 将 `<whiteboard token="XXX" .../>` 替换为 `![Whiteboard N](figures/wb_01.png)`
7. `<lark-table>` 等飞书特殊格式转换为标准 Markdown 表格
8. 将处理后的 markdown 保存到 `/tmp/doc-<slug>-processed.md`

**Markdown 输入**：跳过此步。

### 3. 精读文档

**必须直接用 Read 工具读取原始内容**：PDF 用 pages 参数分页读取（每次最多 20 页），飞书/Markdown 读取处理后的文件。

生成中文精读文档，要求与论文精读完全一致：

**精读原则**：

精读文档应当**详尽完整**，目标是让读者无需打开原文也能完全理解文档。预期长度 **3000-8000 字中文**。如果文档非常技术（含大量公式/算法/实验），可以更长。**宁可长不能短**。

**正文要求**：
- **正文必须使用中文撰写**，保留英文专有名词
- **完整章节结构**：按文档内容自然组织，建议包含「背景与动机」「核心技术方案」「关键技术细节」「实验与效果」「工程优化」「讨论与局限性」等章节
- **方法部分要详尽**：完整描述架构、数据流、训练目标；所有公式完整摘录并编号
- **实验部分要完整**：所有实验表格用 Markdown 表格保留，每个表格后附实验结论分析
- **重要图表**：
  - PDF 输入：使用 Step 2 提取的图片，在正文对应位置用 `![caption](figures/fig_XX.png)` 引用
  - 飞书输入：使用 Step 2 下载的图片/画板缩略图引用
  - **不可跳过任何重要图表**——如果图片下载失败，必须用文字详细描述其内容
- **贴近原文**：忠实传达文档内容，避免过度概括

**禁止行为**：
- ❌ 用一两句话概括方法部分
- ❌ 跳过任何实验表格
- ❌ 跳过公式
- ❌ 跳过图表（必须保留或文字描述）
- ❌ 用反引号 `` ` `` 包裹数学符号/公式（必须用 `$...$` 或 `$$...$$`）

### 4. 写入 reading.md

用 Write 工具写入 `archive/docs/<slug>/reading.md`，格式：

```markdown
---
title: "文档标题"
source: "来源URL或文件路径"
type: "internal-doc"
date: "YYYY-MM-DD"
team: "团队/作者"
summary: "一句话总结"
---

精读正文...
```

- `date`：文档的日期（从文档内容中提取，或用今天的日期）
- `team`：从文档内容中提取作者/团队信息
- `summary`：一句话概括核心内容

### 5. 部署到网站

```bash
.venv/bin/archivist deploy
```

### 6. 完成输出

报告：文档标题、归档路径（`archive/docs/<slug>/`）、提取图片数量。
