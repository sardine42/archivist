---
name: read-paper
description: 论文下载、精读与归档。自动下载 Arxiv 论文 PDF（支持按 ID、标题搜索或本地路径），精读生成阅读报告，更新 Benchmark 和模型迭代图。无需预先下载，skill 内含完整的下载流程。
argument-hint: "<arxiv_id | 论文标题> [--title \"论文标题\"]"
---

# 论文精读与归档

## 参数
- `$ARGUMENTS`: Arxiv ID（如 `1234.56789`）、论文标题（如 `"OneRec Technical Report"`）或本地 PDF 路径，可选 `--title "论文标题"`

## 流程

### 1. 下载/定位 PDF

如果参数是 Arxiv ID（匹配 `\d{4}\.\d{4,5}` 格式）：
```bash
python3 scripts/download-paper.py <arxiv_id> --title "<标题>"
```
如果参数是论文标题（非 Arxiv ID 格式且非本地路径）：
```bash
python3 scripts/download-paper.py --search "<论文标题>"
```
如果参数是本地 PDF 路径：
```bash
python3 scripts/download-paper.py --local <pdf_path> --title "<标题>"
```

脚本输出论文目录路径（如 `archive/papers/2026/xxx/`），目录包含 `document.pdf` + `meta.json`。

### 1.5. 提取图片

从 PDF 中提取所有图表（矢量图 + 位图），保存到论文目录的 `figures/` 子目录：
```bash
uv run python3 scripts/extract-figures.py <paper_dir>/document.pdf <paper_dir>/figures
```
脚本输出 `figures/figures.json`（包含每张图的文件名、页码、caption）和 `figures/fig_01.png` 等图片文件。

精读时在正文对应位置插入图片引用：`![Figure 1: caption](figures/fig_01.png)`。根据 `figures.json` 中的 caption 和页码，将图片插入到精读文档中讨论该图的段落附近。

### 2. 精读论文

**必须直接用 Read 工具读 PDF**（`document.pdf`，pages 参数分页读取，每次最多 20 页）。**禁止使用 Python 库（pymupdf/fitz/PyPDF2 等）将 PDF 转换为文本。**

**纵向前置概念检索**：当论文引用了其他论文的关键概念或方法，且**必须理解前置论文内容才能准确解读当前论文**时，可用 Grep 工具在 `archive/papers/` 下搜索 `reading.md` 获取相关精读报告。仅在核心方案直接依赖前置工作时才检索。（与 Step 2.5 的**横向语义孪生检索**分工：这里是为了看懂当前论文，Step 2.5 是为了挖掘独立并发工作。）

生成中文精读文档 `reading.md`，要求：

**reading.md 为纯 Markdown 正文，不包含 YAML frontmatter。** 元数据通过 Step 4 的 JSON 写入 meta.json。

**精读原则**：

精读文档应当**详尽完整**，目标是让读者无需打开原文也能完全理解论文。预期长度 **3000-8000 字中文**（对应 PDF 6-15 页的论文）。如果论文非常技术（含大量公式/算法），可以更长。**宁可长不能短**——精读不是摘要。

**正文要求**：
- **正文必须使用中文撰写**，保留英文专有名词（如模型名称、技术术语）
- **完整章节结构**：建议章节为「研究动机与背景」「核心方法/模型架构」「关键技术细节」「实验设置」「主要实验结果」「消融与分析」「讨论与局限性」。可根据论文调整，但每个核心章节不可省略
- **方法部分要详尽**：
  - 完整描述模型架构、数据流、训练目标
  - 所有公式必须完整摘录（不得简化或省略符号）
  - **行间公式必须编号**：用 `\tag{N}` 编号，从 1 递增。示例：`$$\mathcal{L} = \sum_{i} \ell_i \tag{1}$$`
  - 关键算法用伪代码或步骤化描述
  - 解释每个公式的物理含义和设计动机
- **实验部分要完整**：
  - **所有实验表格必须保留**，用 Markdown 表格重现关键数值（不要只写"在 X 上提升 Y%"）
  - 列出所有数据集、baseline 模型、评估指标（含 metric 含义）
  - 所有超参数设置（学习率、batch size、训练步数、模型规模等）
  - 每个表格后紧跟**实验结论分析**，解释 why，不仅仅描述 what
  - 消融实验逐项分析每个组件的贡献
  - 工业 A/B 实验、scaling 实验等单独突出
- **重要图表**：使用 Step 1.5 提取的图片，在正文对应位置用 `![caption](figures/fig_XX.png)` 引用。参考 `figures/figures.json` 中的 caption 和页码确定插入位置。仅当提取的图片无法覆盖时才用 Mermaid 重绘
- **贴近原文**：忠实传达论文内容、术语、数据，避免过度概括。当原文用了某种表述/比喻时，保留它

**禁止行为**：
- ❌ 用一两句话概括方法部分（必须展开细节）
- ❌ 跳过任何实验表格
- ❌ 跳过公式（即使复杂也要完整摘录）
- ❌ 把多个章节合并为一段
- ❌ 只列出结论而不列出实验数据
- ❌ 用反引号 `` ` `` 包裹数学符号/公式（必须用 `$...$` 行内公式或 `$$...$$` 块级公式）

**讨论与局限性**：
- 必须包含一段讨论：论文的核心贡献、值得借鉴的设计、存在的局限/争议、与已有工作的差异
- 如果论文有明显的工业落地价值，单独说明部署细节和业务收益

**格式灵活**：正文没有严格格式要求，按论文内容自然组织，但要保证上述每个要求都被覆盖

### 2.5. 检索并对比已归档相关工作

**目标**：在文档库里挖掘与当前论文**问题 + 解法双同构**但作者未必互相引用的论文，把对比写进 `reading.md`。最大价值是发现"独立并发 / 殊途同归"，这类情况下 tag/keyword/`model_name` 词根匹配都抓不到，必须靠语义判断。

**跳过条件**：文档库里没有问题 + 解法双同构的论文时，**完全跳过本步骤**，不在 `reading.md` 中留任何空章节；终端仅输出一行 `Step 2.5: no semantically twin papers found in archive`。

#### 2.5.1 提炼当前论文的语义指纹

从 Step 2 刚写完的草稿中抽取两句话（仅内部使用，不落盘）：

- **核心问题 (Problem Statement)**：1-2 句中文，指出当前论文在解决的**结构性瓶颈的 root cause**，而非任务层描述。
- **核心解法路径 (Solution Recipe)**：1-2 句中文，用"能画成一张方法流程图"的粒度描述技术骨架——不是"用了 Transformer"这种过宽描述。

#### 2.5.2 广度枚举候选池（全量，不预过滤）

```bash
# 列出所有 deeply_read 论文的精简视图（一行一篇）
python3 - <<'PY'
import json, glob
for p in sorted(glob.glob("archive/papers/*/*/meta.json")):
    try: m = json.load(open(p))
    except: continue
    if not m.get("deeply_read"): continue
    aid = m.get("arxiv_id") or "—"
    mn = m.get("model_name") or "—"
    aff = (m.get("affiliations") or ["—"])[0]
    pd = m.get("published_date") or "—"
    summ = (m.get("one_line_summary_en") or "").strip()
    print(f"{aid} | {mn} | {aff} | {pd} | {summ}")
PY
```

规则：
- **唯一硬过滤**：`deeply_read: true`，排除当前论文自己；
- **不按 tag / category / keyword / model_name 词根筛**——这些字段在独立并发场景下不可靠；
- **不做时间窗过滤**——根据文档库规模分批加载候选。

#### 2.5.3 one-line summary 批量语义初筛

对照 2.5.1 的语义指纹，从候选池中挑出 **≤5 篇**同时满足两条的候选：

- **问题同构**：候选论文的问题陈述与当前论文指向**同一 root cause**（不是同一任务、不是同一行业）；
- **解法路径相近**：两者方法流程图能**抽象重合**——不是"都用了 Transformer / Attention"这种过宽共性。

**典型反例（均不入选）**：
- 仅共享 `industrial` + `ctr-prediction` tag，但解法不同；
- 都在"长序列建模"方向但路径不同（一方 retrieval-based TOP-k，一方量化压缩）；
- 问题相似但解法差异大（一方靠蒸馏，一方靠量化）；
- 只是 baseline 关系（当前论文用它作对比数据点），问题 + 解法并不同构 → 交给 Step 4 DAG 处理。

**至少列出 3 条"被剔除的近似候选 + 剔除理由"**（可在终端日志里输出），防止门槛放水。

#### 2.5.4 关系判断与是否加载对方精读（agent 自行决定）

对初筛候选，agent 先做两次便宜的检索：

- **(a) 当前论文 PDF**：Read 搜候选的 `arxiv_id` / `model_name`，判断是否被引用；
- **(b) 当前论文精读草稿**：Grep 搜候选的 `model_name`，判断原文讨论深度。

**是否加载对方 `reading.md` 由 agent 判断，不靠硬规则。** 原则：

**倾向加载**：
- 候选未被当前论文引用（独立并发）——本步骤最大价值，通常都要加载；
- 候选虽被引用，但原文仅在 related-work 或引文号一笔带过，无方法 / 指标层对比；
- 当前论文对候选仅用一句话概括（如 "we outperform X by Y%"），缺机制级讨论；
- agent 对两者共同 insight 和路径差异把握不深。

**倾向不加载**：
- 原文已有完整 table + 方法机制对比段落，再读对方精读没有增量信息；
- 候选属奠基性前置工作（>2 年前、已被反复引用），结构化对比由 Step 4 DAG 兜底；
- agent 读完 one-line summary 后判断问题 / 解法骨架实质偏离（初筛假阳性）。

**关键原则**：**节省 token 不是首要目标——5k-10k tokens 读一篇 reading.md 的代价远低于漏掉一个有价值孪生对比的代价。有疑虑就读**。

对决定加载的候选，用 Read 读其 `reading.md`，只看"研究动机与背景"和"核心方法 / 模型架构"两章（不用读实验部分），深度终判：问题陈述实质同构 AND 技术骨架实质相近。未通过的剔除。**最终保留 ≤3 篇**。

#### 2.5.5 写入对比章节

在 `reading.md` 的"核心贡献总结"之后、"讨论与局限性"之前新增 `## 与已归档相关工作的对比` 章节。每篇保留的候选一个子节，必须显式标注**关系类型**与**是否加载对方精读**。

**模板 A — 独立并发**（agent 已加载对方 reading.md）：

```markdown
### [[arxiv_id]] 候选论文标题（机构，发表日期）

*关系：独立并发（本文未引用候选工作，两者殊途同归）· 已加载对方精读*

- **共同关注的问题**：……
- **相近的技术骨架**：……
- **本文的差异与推进**：……
- **可比的方法 / 实验差异**：……
```

**模板 B — 显式引用且原文已充分对比**（未加载对方精读，直接转录）：

```markdown
### [[arxiv_id]] 基线论文标题（机构，发表日期）

*关系：显式引用，原文 §4.3 已充分对比 · 未加载对方精读*

原文报告：在示例数据集上本方法 NDCG@10 0.XX 对比基线方法 0.YY（+Z%）；
核心机制差异见原文 §3.1。详细精读见 [[arxiv_id]]。
```

**模板 C — 显式引用但原文未展开对比**（已加载对方精读补充叙事）：

```markdown
### [[arxiv_id]] 候选论文标题（机构，发表日期）

*关系：显式引用但原文未展开对比（仅在 related work 简要提及）· 已加载对方精读*

（同模板 A 的四条结构）
```

**要求**：
- 子节标题必须以 `[[arxiv_id]]` 开头（渲染时会被替换为指向 `/reading/<year>/<slug>/` 的 wiki 链接）；
- 每子节 200-500 字（模板 B 可更短）；
- 数据点必须来自原文或已归档 `reading.md`，**禁止编造**；
- 与 Step 4 的 `dag.edges` 互补：本章节是叙事性对比，DAG 是结构化对比，两者都要做。

### 3. 写入 reading.md

用 Write 工具写入论文目录。

### 4. 更新元数据、Benchmark 和 DAG

**标签选择**：从 `config.yaml` 的 `tags` 部分选择结构化标签，优先使用已有标签。仅当论文方向与现有标签显著不同时才创建新标签。每篇论文至少 1 个 task 标签 + 1 个 method 标签 + 1 个 scene 标签。

**公司归类**（仅工业界论文）：
1. 检查作者所属机构（affiliations）
2. 读取 `config.yaml` 的 `companies` 列表
3. 如果机构匹配现有公司条目，无需操作（系统会自动归类）
4. 如果是**新的工业公司**（不在列表中），追加新条目到 `config.yaml`：
   ```yaml
   companies:
     ...
     - name: <显示名，如 Snowflake>
       keywords: [<匹配关键词，如 Snowflake>]
   ```
5. 学术机构（大学/研究所）不需要加入公司列表

新增公司后系统会自动重新加载，无需重启。

将以下数据整理为 JSON，写入临时文件，调用 CLI：

```bash
.venv/bin/archivist paper apply-reading /tmp/paper-update-<arxiv_id>.json
```

JSON 格式参见 [update-data-schema.md](update-data-schema.md)。

**与 Step 2.5 的配合**：Step 2.5 发现的"显式引用"关系（当前论文已把对方作为 baseline 对比），必须同时在 `dag.edges` 中登记结构化对比边；叙事对比（`reading.md` 章节）和结构化对比（DAG）两者不替代。独立并发（本文未引用对方）一般不登记 edge——DAG 记录的是论文自身声明的对比关系。

### 5. 部署到网站

```bash
.venv/bin/archivist deploy
```

### 6. 完成输出

报告：论文标题、阅读报告路径、Benchmark/DAG 更新条数、冲突信息（如有）。
