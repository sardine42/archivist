# Archivist — 文件系统驱动的研究论文文档库

基于文件系统的论文归档与管理工具，专注于推荐系统和 LLM 领域的 ArXiv 论文追踪。
所有数据以 JSON / Markdown / PDF 形式存储在 `archive/` 下，无数据库依赖，原生支持 `grep` 检索；
对外通过 `archivist` CLI 和一组 Claude Code Skill 暴露。

## 核心功能

- **论文管理** — PDF 导入、文本/图片提取、元数据 (`meta.json`) 单一数据源、全文检索
- **ArXiv 日报 / 周报 / 月报** — 抓取 → LLM 评分 → 精读 → 综述 → 推送 → 部署的全自动流水线
- **Benchmark 排行榜** — 跟踪生成式与判别式推荐模型的实验数据，自动检测冲突，metric 别名归一化
- **模型迭代图 (DAG)** — 基于实验结论构建模型优劣关系，含 self-reported / historical 边的优先级仲裁
- **静态网站** — `archivist build` + `archivist deploy` 把全部内容打包成前端网站，rsync 到 Linux/nginx 源站
- **飞书推送** — 日报/周报/月报通过 `lark-cli` 发送到 DM 并 pin 消息

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.12+ |
| 包管理 | uv |
| CLI 框架 | Click |
| PDF 处理 | PyMuPDF |
| 网站渲染 | Jinja2 + Cytoscape.js + KaTeX |
| LLM 调用 | 本地 `claude -p` CLI（不走 Anthropic API）|
| 推送通知 | 飞书 (`lark-cli`) |
| 定时调度 | 系统 crontab + flock(1) |

## 安装

```bash
uv sync                # 使用 .venv，依赖见 pyproject.toml + uv.lock
.venv/bin/archivist init

# 配置用户字段（部署 host、站点 base_url、飞书 notify_user_id）
cp config.local.yaml.example config.local.yaml
$EDITOR config.local.yaml        # 按需填入；留空等于禁用对应功能
```

`config.yaml` 是框架默认配置（关键词、公司列表、标签、打分权重等），受 Git 管理；
`config.local.yaml` 是 gitignored 的个人覆盖层，`load_config()` 运行时 deep-merge 到 `config.yaml` 上。

## 存储布局

**整个 `archive/` 都是运行时数据 + 个人数据，不受 Git 管理**（仅框架代码进 git）。目录结构如下：

```
archive/                             # 全部 gitignored
├── papers/{year}/{slug}/            # 精读论文
│   ├── document.pdf
│   ├── meta.json                    # 单一元数据源
│   ├── reading.md                   # 中文精读报告（含公式 / 表格 / 图 / 语义孪生对比）
│   ├── figures/                     # 从 PDF 抽取的位图 / 矢量图 + figures.json
│   └── content.txt                  # PyMuPDF 抽取的全文（检索用）
├── papers_brief/{year}/{slug}/      # 仅评分摘要的论文
│   └── meta.json
├── docs/{slug}/                     # 内部文档（飞书 / PDF / MD）精读
│   ├── reading.md                   # 含 YAML frontmatter
│   └── figures/
├── digests/{year}/                  # 流水线产物：日报 / 周报 / 月报
│   ├── daily/{YYYY-MM-DD}.md|.json  # markdown + DigestMeta
│   ├── weekly/{YYYY-Www}.md|.json
│   └── monthly/{YYYY-MM}.md|.json
├── benchmarks/
│   ├── _index.json                  # 数据集 → 文件名索引
│   ├── conflicts.md                 # 历史冲突追加日志
│   └── {dataset-slug}.md            # 排行榜 markdown 表格
├── model-graph/
│   └── graph.json                   # DAG 节点 + 比较边 + 引用边
└── criteria/                        # 评分标准，随反馈演化
    ├── scoring-criteria.md          # 摘要评分
    └── reading-criteria.md          # 精读评分
```

## CLI 命令

```
archivist
├── init                                       # 初始化目录结构
├── paper
│   ├── import <pdf> [--tags --category]       # 导入 PDF
│   ├── list [--tag --year --status --category]
│   ├── show <slug>
│   ├── edit <slug> [--rating --rating-reason --feedback-consumed --tags ...]
│   ├── apply-reading <data.json>              # 精读完成后写 meta + benchmark + DAG
│   ├── backfill -f <field> [--dry-run]        # 列出缺失字段的论文（供 agent 回填）
│   ├── note <slug>                            # $EDITOR 编辑 notes.md
│   ├── open <slug>                            # 系统查看器打开 PDF
│   └── remove <slug>
├── doc
│   └── add / list / show / remove
├── arxiv
│   ├── fetch [--date --from --to --categories] # 抓取 + 关键词预过滤
│   └── download <arxiv_id>                    # 单篇下载
├── digest
│   ├── run [--date]                           # 日报全流程
│   ├── run-weekly [--week]                    # 周报全流程
│   ├── run-monthly [--month]                  # 月报全流程
│   ├── daily-prepare / daily-write            # 底层 prepare / write 拆分
│   ├── weekly-prepare / weekly-write
│   ├── monthly-prepare / monthly-write
│   └── list                                   # 列出所有已生成 digest
├── dag
│   └── list-nodes                             # 列出 DAG 中已注册的模型节点
├── rubric
│   └── list-pending [--format table|json]     # 列出未处理的评分反馈（只读）
├── build [-o _site]                           # 构建静态网站
├── deploy [--host --output --skip-build]      # build + rsync 到远程源站
├── web [--host --port --debug]                # 本地预览服务器
├── notify --text "..."                        # cron wrapper 发飞书状态通知
├── search <query> [--type --tag]
├── tags / stats
```

## ArXiv 日报流水线（脚本编排）

```
archivist digest run
   ├─ Step 1: archivist arxiv fetch                            [pure code]
   ├─ Step 2: claude -p (sonnet) 评分 + 中英摘要                [LLM]
   ├─ Step 3: claude -p "/read-paper <id>" (opus) × N          [LLM]
   ├─ Step 4: digest daily-prepare → claude -p (sonnet) 综述 → daily-write [LLM]
   ├─ Step 5: lark-cli 推送日报 + Pin                          [pure code]
   └─ Step 6: archivist deploy                                  [pure code]
```

`digest run` 是确定性 Python orchestrator，仅在三个判断节点（评分 / 精读 / 综述）通过 `claude -p` 短暂出场。
所有提示词模板在 `src/archivist/services/digest_prompts/` 下，评分标准在 `archive/criteria/scoring-criteria.md`（gitignored，随用户反馈通过 `/refine-rubric` skill 迭代）。
空日（keyword filter 后无候选，或所有候选都已归档）会短路：不生成主题、不写 stub、不推送、不部署。

详细架构见 `src/archivist/services/digest_runner.py`。

## 自动化调度

cron + flock 串行调度，所有触发都通过 `~/.claude/settings.json` 的 allow list 自动放行。

```cron
# Asia/Shanghai
CRON_TZ=Asia/Shanghai

0 9 * * 2-6  /path/to/archivist/scripts/cron/daily-digest.sh    # 周二~周六 09:00（对齐 ArXiv 发布节奏，周二覆盖 Fri~Mon）
30 9 * * 2   /path/to/archivist/scripts/cron/weekly-digest.sh   # 周二 09:30（覆盖上一个 ISO 周，跟在日报后跑全 5 天）
0 10 1 * *   /path/to/archivist/scripts/cron/monthly-digest.sh  # 1 号 10:00（覆盖上月）
```

每个 wrapper 脚本：
1. `flock -n` (daily) / `flock -w 3600` (weekly/monthly) 拿 `/tmp/archivist-digest.lock` 互斥锁
2. 调用 `.venv/bin/archivist digest run*`
3. 区分 exit `0 / 75 / 其他` 三态，发飞书通知

详见 `scripts/cron/`。

## Claude Code Skills

| Skill | 说明 |
|-------|------|
| `/read-paper` | 下载/定位 PDF、精读、figure 抽取、语义孪生对比、benchmark/DAG 更新 |
| `/read-doc` | 内部文档精读 — 支持飞书 URL / 本地 PDF / Markdown 三种输入，归档到 `archive/docs/` |
| `/refine-rubric` | 评分标准反馈闭环 — 多轮对话更新 `archive/criteria/*.md` |
| `/deploy` | 构建网站 → rsync 到服务器 → 提交 main → push github |

日报 / 周报 / 月报流程已完全脚本化，**不要通过 Skill 触发**，直接调 `archivist digest run` / `run-weekly` / `run-monthly`。

`/read-paper` 由 `digest run` Step 3 通过 `claude -p "/read-paper <id>"` 串行调用（保留是因为
PDF 阅读、公式抽取、表格还原、figure 插入位置、语义孪生检索、benchmark 冲突解析、DAG 边推断都需要 agent 智能）。
Step 2.5 的"横向语义孪生检索"会在已归档论文中挖掘问题 + 解法双同构的独立并发工作，把对比写进 `reading.md`。

### 评分反馈闭环

两份评分标准都在 `archive/criteria/`（gitignored，属随用户使用持续演化的个人数据）：
- `scoring-criteria.md` — 摘要评分（digest pipeline 自动用）
- `reading-criteria.md` — 精读评分（read-paper skill 参考）

**两条反馈路径**：

1. **CLI 录入**（纯数据）：
   ```bash
   archivist paper edit <slug> --rating N --rating-reason "..."
   ```
   只写 meta.json，不触发 criteria 改动；累积在 `archivist rubric list-pending` 中待后续处理。

2. **Agent 会话驱动**（更新评分标准）：
   在 Claude Code 会话中以自然语言提反馈（如"这篇只值 5 分，它只是比赛介绍"）或显式 `/refine-rubric <slug>`。Agent 会：
   - CLI 写入 rating + rating_reason
   - 若 rating ≠ auto_score，询问是否更新评分标准
   - 读 criteria + 精读报告 limitations → 提议 1-2 个改动方案
   - 多轮对话确认 → Edit 对应 criteria 文件 → `--feedback-consumed` 标记

criteria 与 meta.json 都 gitignored，反馈闭环不产生 git commit。

## 项目结构

```
src/archivist/
├── cli.py                       # Click CLI 入口
├── config.py                    # 路径常量 + load_config + deploy/lark 设置
├── models.py                    # PaperMeta / DocMeta / DigestMeta / DAGNode / DAGEdge / CitationEdge / ModelGraph
├── utils.py
├── services/
│   ├── paper_store.py           # 论文 CRUD（papers/ 与 papers_brief/）
│   ├── doc_store.py             # 项目文档归档
│   ├── arxiv_fetch.py           # ArXiv API + 下载 PDF（含 10 分钟重试）
│   ├── arxiv_scorer.py          # pre_filter + dedup + archive_scored_paper
│   ├── pdf_extract.py           # PyMuPDF 文本/图片抽取
│   ├── benchmark.py             # 排行榜 + metric 别名归一化 + 冲突解析
│   ├── dag.py                   # 模型迭代图（self-reported vs historical 优先级仲裁）
│   ├── reading_apply.py         # 统一精读写入：meta + benchmark + DAG + progress
│   ├── feedback.py              # collect_corrections（rating != auto_score 的未消费反馈）
│   ├── digest.py                # daily/weekly/monthly prepare + write
│   ├── digest_runner.py         # 端到端 orchestrator (Step 1-6)
│   ├── digest_prompts/          # claude -p prompt 模板（score / daily_theme / weekly_theme / monthly_theme）
│   ├── claude_runner.py         # `claude -p` 唯一封装（run_claude / run_claude_json）
│   └── lark_push.py             # 飞书推送 + Pin
└── web/
    ├── build.py                 # 静态站点构建
    ├── data.py                  # 准备 reading/graph/benchmark/digest 数据 + model-index
    ├── routes/reading.py        # 路由 + markdown 渲染（wiki 链接 / LaTeX 防御 / TOC）
    ├── static/style.css
    └── templates/
        ├── base.html
        ├── reading/index.html / detail.html / digest_detail.html
        ├── docs/index.html / detail.html     # /docs/ 内部文档
        ├── graph.html           # Cytoscape.js + dagre
        └── benchmark.html       # 含过滤 + 冲突弹窗

scripts/
├── download-paper.py            # /read-paper 内部用：ArXiv ID / --search / --local 三模式
├── extract-figures.py           # /read-paper 内部用：抽取 figures + 生成 figures.json
└── cron/
    ├── daily-digest.sh          # flock -n 非阻塞
    ├── weekly-digest.sh         # flock -w 3600 阻塞等日报
    ├── monthly-digest.sh        # flock -w 3600
    └── crontab.txt              # crontab 安装文件

.claude/skills/
├── read-paper/
│   ├── SKILL.md                 # 精读 Skill（含 Step 2.5 语义孪生检索）
│   └── update-data-schema.md    # paper apply-reading 的 JSON schema
├── read-doc/SKILL.md            # 飞书 / PDF / Markdown 精读
├── refine-rubric/SKILL.md       # 评分标准反馈闭环
└── deploy/SKILL.md              # build + deploy + git push

archive/criteria/                 # gitignored — 个人评分标准，随反馈演化
├── scoring-criteria.md           # 摘要评分（digest_runner 拼到 score prompt）
└── reading-criteria.md           # 精读评分（/read-paper 参考）
```

## 设计理念

1. **文件系统即数据库** — 每篇论文独立目录，自包含可移植，`grep` 即查询
2. **单一元数据源** — `meta.json` 是唯一真相，reading.md 是纯 markdown
3. **脚本编排 + Agent 边界明确** — 确定性步骤走 Python；判断性步骤走 `claude -p`，输入输出 JSON 化
4. **Markdown 优先** — Benchmark 排行榜、精读报告、日报均为 Markdown，人类可读且 Git 友好
5. **本地 LLM CLI** — 不走 Anthropic API，复用 Claude Code 的 settings.json / allow list / 工具空间
