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

只做本地阅读不需要其他配置。需要生成静态网站并公开访问、或定时推送日报时才参考下方部署指南。

## 部署指南

按需启用以下三个模块：**静态网站 → 飞书推送 → cron 自动化**。三者独立，可分别跳过。

### 模块 A：静态网站（可选）

`archivist deploy` 做两件事：本地 `archivist build` → `_site/`，然后 rsync `_site/` 和 `archive/papers/` 到远程 host。

#### A.1 远程服务器准备

一台带公网 IP 的 Linux 机器，安装 nginx：

```bash
sudo apt install nginx rsync
```

建两个目录供 rsync 写入：

```bash
mkdir -p ~/site ~/archive/papers     # 对应 remote_site_path / remote_archive_path
```

`/etc/nginx/sites-available/archivist` 参考配置（把 `/reading/<year>/<slug>/figures/` 别名到 archive 目录，避免把几个 GB 的 figures 重复塞进 `_site/`）：

```nginx
server {
    listen 80;
    server_name example.com;       # 换成你的域名或 _
    root /path/to/site;
    index index.html;

    # 把论文 figures 从 archive 目录 alias 出来
    location ~ ^/reading/(\d+)/([^/]+)/figures/(.*)$ {
        alias /path/to/archive/papers/$1/$2/figures/$3;
    }

    # 前端使用目录式 URL：/reading/ → /reading/index.html
    location / {
        try_files $uri $uri/ $uri/index.html =404;
    }
}
```

启用后 `sudo nginx -t && sudo systemctl reload nginx`。

#### A.2 本地 → 远程 SSH 免密

`deploy` 命令底下直接调 `rsync -e ssh`，必须免密：

```bash
ssh-copy-id <user>@<host>
ssh <user>@<host> "echo ok"   # 确认不用输密码
```

#### A.3 填配置 + 首次部署

`config.local.yaml` 中填入：

```yaml
site:
  base_url: "https://your-domain.com"      # 没域名就填 http://<IP>
deploy:
  host: "<user>@<host>"
  remote_site_path: "~/site"                # 默认值，可不改
  remote_archive_path: "~/archive"
```

本地验证：

```bash
.venv/bin/archivist web                    # 本地 8080 预览
.venv/bin/archivist deploy                 # build + rsync 推到远程
```

成功后访问 `https://your-domain.com` 应看到论文列表页。

#### A.4 HTTPS（可选）

用 Cloudflare Flexible SSL 是最省事的方案：域名托管到 Cloudflare → 开代理 → SSL/TLS 设为 "Flexible"。nginx 仍然监听 80 端口，对外是 https。

### 模块 B：飞书推送（可选）

日报/周报/月报跑完后会把 markdown 发到指定飞书 DM 并 Pin。**需要自行配置 `lark-cli`**——这是一个独立的命令行工具，用来调飞书 Open API。

1. 按 `lark-cli` 官方流程初始化应用（创建应用、配 scope、登录授权）
2. 用 `lark-cli contact +get-user` 拿到要接收通知的用户 open_id（以 `ou_` 开头）
3. 填入 `config.local.yaml`：
   ```yaml
   lark:
     notify_user_id: "ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
   ```
4. 测试：`.venv/bin/archivist notify --text "hello"` 应收到飞书消息

留空则日报 Pipeline Step 5 静默跳过，不影响 build/deploy。

### 模块 C：cron 自动化（可选）

`scripts/cron/crontab.txt` 是模板。替换 `/path/to/archivist/` 为你的项目根路径，然后安装：

```bash
# 把 /path/to/archivist/ 替换成绝对路径
sed "s|/path/to/archivist|$PWD|g" scripts/cron/crontab.txt | crontab -
crontab -l     # 验证
```

三个任务（Asia/Shanghai）：

```cron
0 9  * * 2-6  .../scripts/cron/daily-digest.sh     # 周二~六 09:00
30 9 * * 2    .../scripts/cron/weekly-digest.sh    # 周二 09:30（上周周报）
0 10 1 * *    .../scripts/cron/monthly-digest.sh   # 每月 1 号 10:00
```

每个 wrapper 脚本：
- `flock` 拿 `/tmp/archivist-digest.lock` 互斥锁（日报非阻塞，周/月报等 1 小时）
- 调 `.venv/bin/archivist digest run*`
- 按 exit code 0 / 75 / 其他 发送飞书通知（复用模块 B）

**WSL 注意**：WSL 在 Windows 休眠时 cron 停摆，需要开 Windows 任务计划程序让 WSL 常驻，或把 archivist 跑在真正的 Linux 机器上。

### 最小化运行（不部署）

只想本地跑 `digest run` 把结果存到 `archive/`，不发布网站、不推飞书、不用 cron：

- `config.local.yaml` 三个字段全部留空
- 跑 `archivist digest run` — pipeline 会在 Step 5 (push) / Step 6 (deploy) 静默跳过
- 结果落在 `archive/papers/` + `archive/digests/`，用 `archivist web` 本地预览

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
│   ├── import <pdf> [--tags --category --allow-new-tag]   # 导入 PDF（tags 走白名单校验）
│   ├── list [--tag --year --status --category]
│   ├── show <slug>
│   ├── edit <slug> [--rating --rating-reason --feedback-consumed --tags --proposed-tags --allow-new-tag ...]
│   ├── apply-reading <data.json>              # 精读完成后写 meta + benchmark + DAG（tags 走白名单校验）
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
├── tag
│   ├── list-pending [--threshold N]           # 列 LLM 提案的 proposed_tags（≥N 篇标记 ready-to-promote）
│   ├── promote <tag> [--gloss "..."]          # 加入 config.yaml 白名单 + 把命中论文的 proposed_tags 迁移到 tags
│   ├── alias <old> <new>                      # 把 proposed[old] 改写为 tags[new]（处理 LLM 提的同义词）
│   └── reject <tag>                           # 从所有论文 proposed_tags 中删除该 tag
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
   ├─ Step 2: claude -p (opus) 评分 + 中英摘要                  [LLM]
   ├─ Step 3: claude -p "/read-paper <id>" (opus) × N          [LLM]
   ├─ Step 4: digest daily-prepare → claude -p (opus) 综述 → daily-write [LLM]
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

### 跨项目访问（MCP server）

`archivist-mcp` 是只读 stdio MCP server，让其它 Claude Code 项目里的 agent 也能查这个文档库
（搜论文、读精读报告、查 DAG / digest / 标签等）。写路径仍然只在本项目里走。

注册（用户级，所有项目共享）：

```bash
claude mcp add archivist /path/to/my_archive/.venv/bin/archivist-mcp \
  -e ARCHIVIST_ROOT=/path/to/my_archive --scope user
```

之后在任意项目里以 `mcp__archivist__<tool>` 调用。三层渐进披露设计：tier 1 批量加载 →
tier 2 单篇精读 → tier 3 PDF 原文（rare），让 agent 按需控制 context 开销。

| Tool | 用途 | 渐进层级 |
|------|------|---------|
| `load_papers` | 按 tag/category/year 批量拉 meta + 双语一句话总结，默认按 `published_date` 倒序 | tier 1（批量） |
| `search_papers` | 按关键字模糊搜索 | — |
| `get_paper` | 单篇完整 meta（默认隐藏 score_reason 等内部字段） | — |
| `get_paper_reading` | 单篇精读 reading.md（5–15k 字符） | tier 2（按需） |
| `get_paper_pdf` | 返回 PDF 路径 + 页数；agent 用 Read tool 读指定页 | tier 3（罕见） |
| `search_docs` / `get_doc` | 内部文档检索与读取 | — |
| `list_tags` | 标签白名单 | — |
| `search_models` / `get_model` | DAG 节点与引用/比较边 | — |
| `list_digests` / `get_digest` | 日/周/月报 | — |

实现见 `src/archivist/mcp_server.py`，所有 tool 都直接复用 `services/` 里的纯读函数。
`load_papers` 的 description 在 server 启动时把当前 tag 白名单 + 4 个 category 烤进去，
agent 不需要先调 `list_tags` 就能直接选——加 tag 后重启 MCP server 自动反映。

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

### Tag 体系与受控演化

`config.yaml` 中的 `tags:` 是一个**扁平白名单**（无维度分组）。每个 tag 满足三条：语义边界清晰（无同义词）、覆盖 ≥3 篇论文（单篇专属概念走 `keywords` 字段）、与 `category` 字段正交（不重复 task 维度信息）。

**写入边界全部走白名单校验**：

- `archivist paper edit --tags`、`paper import --tags`、`paper apply-reading` — 未知 tag 直接报错并提示最相近的 3 个候选；可用 `--allow-new-tag X` 显式放行
- LLM 自动打 tag（digest pipeline）— 校验后未知 tag 自动落入 `meta.proposed_tags`，**不报错**，给 LLM 一条提案通路

**LLM 提案 → 人工审核入册**：

```bash
archivist tag list-pending             # 看哪些提案累积了多少篇（≥3 篇会标 [ready to promote]）
archivist tag promote <tag>            # 加入白名单 + 把所有命中论文的 proposed_tags 迁移到 tags
archivist tag alias <old> <new>        # 把 proposed[old] 改写为 tags[new]（处理同义词）
archivist tag reject <tag>             # 噪声 / 单篇专属概念，从所有 proposed_tags 中删除
```

`promote` 会自动 append 到 `config.yaml` 末尾并重置内存中的 config 缓存，无需重启进程。`config.yaml` 受 Git 管理，所以 promote 的 tag 会跟随 commit 同步给所有用户。

**全量重打** `scripts/retag-papers.py`：用 LLM 读每篇 `reading.md`（不重跑精读流程），按当前白名单输出新 tag。dry-run 出 diff，`--apply` 走 `archivist paper edit` 落库。

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
