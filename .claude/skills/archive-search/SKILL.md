---
name: archive-search
description: 文档库检索。搜索论文精读报告、元数据和项目文档。
argument-hint: "<关键词>"
---

# 文档库检索

搜索 `archive/` 中的论文和文档。

## 参数
- `$ARGUMENTS`: 检索关键词

## 检索方式

1. 搜索精读报告：用 Grep 搜索 `archive/papers/` 下的 `reading.md`
2. 搜索元数据：用 Grep 搜索 `meta.json`
3. 搜索项目文档：用 Grep 搜索 `archive/docs/` 下的 `*.md`
4. 搜索日报：用 Grep 搜索 `archive/digests/`

汇总结果，展示匹配的文档标题、路径和相关上下文。
