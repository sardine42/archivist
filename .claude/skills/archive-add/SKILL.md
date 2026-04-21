---
name: archive-add
description: 文档归档。将对话总结或 Markdown 文档归档到文档库。
argument-hint: "[pdf路径 | --doc] [--title \"标题\"]"
disable-model-invocation: true
---

# 文档归档

将文档归档到 `archive/`。

## 参数
- `$ARGUMENTS`: PDF 路径（归档论文）或 `--doc`（归档对话总结）

## 归档对话总结

1. 生成 Markdown 总结，保存到 `/tmp/archive-doc-<timestamp>.md`
2. 执行归档：
```bash
python3 scripts/archive-doc.py /tmp/archive-doc-<timestamp>.md --title "<标题>" --tags "<标签>" --category "<分类>"
```

## 归档 PDF 论文

```bash
python3 scripts/download-paper.py --local <pdf路径> --title "<标题>"
```

## 对话总结格式

```markdown
# <对话主题>

## 背景
## 主要内容
## 关键结论
## 相关文件
```

总结使用中文。
