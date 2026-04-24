# `archivist paper apply-reading` 输入 JSON 格式

传给 `.venv/bin/archivist paper apply-reading` 的 JSON 文件格式：

```json
{
  "arxiv_id": "1234.56789",
  "paper_dir": "archive/papers/2026/xxx/",
  "meta": {
    "authors": ["Author1", "Author2"],
    "affiliations": ["Google Research", "MIT"],
    "category": ["generative-rec"],
    "model_name": "DreamRec",
    "score": 9,
    "reading_score": 8,
    "reading_score_reason": "1-2句精读打分理由",
    "one_line_summary": "一句中文总结",
    "one_line_summary_en": "One English sentence",
    "keywords": ["关键词1", "关键词2"],
    "tags": ["generative-retrieval", "semantic-id", "industrial"],
    "url": "https://arxiv.org/abs/1234.56789",
    "generated_by": "claude-opus-4-7"
  },
  "benchmarks": [
    {
      "dataset": "Amazon-Beauty",
      "model": "DreamRec",
      "metrics": {"NDCG@10": 0.053, "HR@10": 0.089},
      "category": "generative-rec",
      "is_proposed": true
    },
    {
      "dataset": "Amazon-Beauty",
      "model": "SASRec",
      "metrics": {"NDCG@10": 0.048, "HR@10": 0.082},
      "category": "discriminative-rec",
      "is_proposed": false
    }
  ],
  "dag": {
    "model_name": "DreamRec",
    "paper_title": "论文标题",
    "paper_date": "2026-03-01",
    "category": ["generative-rec"],
    "cites_papers": ["2302.xxxxx", "2401.yyyyy"],
    "edges": [
      {
        "source": "SASRec",
        "target": "DreamRec",
        "summary": "DreamRec 在 3 个数据集上均优于 SASRec",
        "datasets": {
          "Amazon-Beauty": "NDCG@10: 0.048→0.053(+10.4%)",
          "Movielens-1M": "NDCG@10: 0.112→0.118(+5.4%)"
        },
        "is_self_reported": true
      }
    ]
  }
}
```

## 字段说明

### meta
- `category`: **必填**，论文分类（**数组**），元素从 `"generative-rec"` / `"discriminative-rec"` / `"llm"` / `"other"` 中选，至少一项。
  - 普通推荐算法论文：`["generative-rec"]` 或 `["discriminative-rec"]`（按输入输出形式判断，详见 `benchmarks[].category` 的判断规范）
  - 通用序列建模架构论文（典型：**HSTU**）：既能做生成式序列推荐又能做 CTR 判别式排序时，写 `["generative-rec", "discriminative-rec"]`。双 category 会让论文同时出现在两个 tab、两张 benchmark 榜单里
  - LLM 相关但非推荐：`["llm"]`
  - 其他：`["other"]`
- `model_name`: 论文提出的核心模型/方法的缩写名称（如 "DreamRec"、"QuaSID"）。**如果论文不提出新模型结构**（如可复现性研究、综述、工程实践报告），留空 `""`，此时跳过 DAG 注册
- `score`: 摘要评分（从已有 meta.json 继承，不修改）
- `reading_score`: **必填**，精读评分 (1-10)，参考 `archive/criteria/reading-criteria.md` 中的精读评分标准
- `reading_score_reason`: **必填**，精读打分理由（1-2句话，说明为什么给这个分数，内部排查用）
- `url`: ArXiv 链接
- `generated_by`: **必填**，执行本次精读的 Claude 模型 ID（如 `"claude-opus-4-7"`、`"claude-sonnet-4-6"`）。从 system prompt 里读取自己的模型标识，不要手编。该字段用于详情页展示"由谁精读"。

### benchmarks
- **指标值统一使用小数制**：如 Recall@10=0.0648 而非 6.48，NDCG@10=0.0384 而非 3.84。系统会自动将 >1 的值除以 100
- **仅公开学术数据集**（Amazon-Beauty, Movielens-1M 等），不含工业数据集
- 论文实验表中的**所有模型都要录入**（含 baseline 和本文提出模型的各种变体）
- 一篇论文可以有多条 benchmark 记录（如消融变体 APAO-Pointwise / APAO-Pairwise，不同骨干 DACT (TIGER) / DACT (LCRec) 等），**benchmark 不需要合并变体**，与 DAG "一篇论文一个节点" 的规则不同
- `category`: 单值字符串 `"generative-rec"` 或 `"discriminative-rec"`（每条 entry 对应**一组实验配置**，单值）。按模型**输入输出形式**区分：
  - **generative-rec（生成式）**：模型输入是用户行为序列（item ID 序列），输出是下一个 item 或 item 排名。包括：
    - 自回归生成式推荐：TIGER、OneRec、P5、LC-Rec
    - 序列推荐：SASRec、GRU4Rec、BERT4Rec、Caser
    - 图/对比学习推荐：LightGCN、BM3、CL4SRec、S3-Rec
    - LLM-based 推荐：AgenticRec、TallRec、LLaRA、MLLMRec-R1
    - 对话式推荐：UniCRS、RAR
    - 可解释推荐：SELLER、PETER、PEPLER
    - Tokenizer/SID 方法：RQ-VAE、QuaSID、FORGE
  - **discriminative-rec（判别式）**：模型输入是用户特征 + 物品特征（稠密/稀疏特征向量），输出是 CTR/CVR 等分值。包括：
    - 特征交互排序模型：DCNv2、DLRM、DeepFM、Wukong
    - 工业排序架构：RankMixer、HiFormer、TokenMixer-Large、MixFormer、OneTrans
  - **通用架构（如 HSTU）同时跑两类实验时**：**提交两条 benchmarks 记录**，分别带 `category: "generative-rec"`（对应 Recall@K / NDCG@K 指标）和 `category: "discriminative-rec"`（对应 AUC / LogLoss 指标）。不要把两种指标合进同一条记录。
  - **简单判断**：如果论文的实验指标是 Recall@K / NDCG@K / HR@K，通常是 generative-rec；如果是 AUC / LogLoss / UAUC，通常是 discriminative-rec
- `is_proposed`: 本文提出的模型为 true（含变体）

### dag

#### 录入前必须先查询已有节点
在准备 DAG JSON 之前，**必须先运行**：
```bash
.venv/bin/archivist dag list-nodes
```
查看图中已有的模型节点列表。编写 `edges` 时：
- 如果论文中的 baseline 模型在已有节点中已存在，**必须使用完全相同的名称**（如已有 `DCNv2` 则不要写 `DCN-V2` 或 `DCNv2+DIN`）
- 如果 baseline 模型不在已有列表中，使用该模型最广为人知的简称

#### 字段说明
- **所有数据集**（含工业数据集），工业数据集在可视化中标注
- `model_name`: **仅填本论文提出的模型**，不要为 baseline 模型注册节点（baseline 通过 edge 的 source 自动创建）
- `edges`: 每对模型一条边，`datasets` 字典记录各数据集对比
- `summary`: 总体概括技术原因
- `datasets` value: 该数据集上的具体指标和提升幅度
- `is_self_reported`: 本文模型 vs baseline = true
- `cites_papers`: 仅 arxiv ID（去掉版本号）

#### 系统迭代边
- 如果本论文是某个已有系统的后续版本（如 OneRec-Think 基于 OneRec），即使论文实验表中没有直接对比，也应添加从前代系统到本模型的边
- 判断依据：论文明确提到在某系统架构基础上改进、同一团队/公司的系统演进、论文标题或摘要中提到前代系统名
- 先通过 `--list-nodes` 查看已有节点，如果前代系统已在图中，添加迭代边

#### 一篇论文一个节点（重要）
- **一篇论文通常只注册一个 `model_name`**：即论文的核心贡献模型
- **不同尺寸变体只保留最佳/上线版本**：如论文提出 Small/Large 两个版本，只注册在 A/B 实验中上线或指标最好的那个，不要注册多个
- **子组件不单独建节点**：如论文提出模型 A 及其子模块 B，只注册 A
- **例外**：论文提出了架构差异巨大的多种模型（如 encoder-decoder 和 decoder-only 两种完全不同的架构），可分别注册

#### 模型节点命名规则（重要）

**核心原则**：DAG 中每个节点代表一个**独立发表的模型/系统**。节点名必须是简洁的模型名，不带括号描述、训练配置、部署变体等修饰。

**命名规范**：
- 使用模型的官方简称：`TIGER`、`SASRec`、`OneRec`、`DACT`
- **禁止在名称中加括号描述**：用 `CascadedRec` 而不是 `CascadedRec (Kuaishou Traditional)`；用 `GRID` 而不是 `GRID (Snapchat SID)`；用 `HHSFT` 而不是 `HHSFT (UniScale)`
- **禁止在名称中加斜杠拼接**：用 `RQ-VAE` 而不是 `TIGER/RECFORMER (RQ-VAE Tokenizer)`

**不得作为独立节点的类型**（直接使用基础模型名）：

| 类型 | 错误示例 | 正确做法 |
|------|----------|----------|
| 训练策略变体 | `TIGER (FT/FT)`, `SASRec w/ LoRA` | 用 `TIGER`, `SASRec` |
| 消融变体 | `DACT w/o CDIM`, `OneRec w/o RL` | 不建节点 |
| 骨干标注 | `DACT (TIGER)`, `DACT (LCRec)` | 只建一个 `DACT` |
| 量化/部署变体 | `OneRec-V2 (FP8)`, `OneRec-V2 (FP16)` | 用 `OneRec-V2` |
| 场景/表面变体 | `CDKD Student (Homepage)`, `Control Model (Radio)` | 用 `Zero-shot CDKD`, `Baseline` |
| 尺寸变体 | `OneTrans_S`, `OneTrans_L` | 用 `OneTrans`（除非不同尺寸是独立发表的模型） |
| 组合配置 | `RankMixer+Transformer`, `DCNv2+DIN`, `STCA+RankMixer` | 拆分为独立的边：如 `STCA+RankMixer → MixFormer` 应拆为 `STCA → MixFormer` 和 `RankMixer → MixFormer` 两条边 |

**版本号的处理**：
- 同一系统的迭代版本（V1, V2）是独立节点：`OneRec` 和 `OneRec-V2` 都可以
- 但**第一版不要加 V1 后缀**：当 V2 论文将原系统称为 V1 时，DAG 中仍用原名 `OneRec`，不要新建 `OneRec-V1`

**edge 的 source/target 同样遵循上述规则**。

**判断标准**：该名称能否在论文标题或摘要中作为一个可检索的模型名？如果不能，通常不应作为节点。

#### 训练策略变体的例外
- **默认不建节点**，直接用基础模型名
- **例外：当训练策略变体显著优于基础模型时可保留**（核心指标提升 >10%），说明该变体有方法论价值，可建立从基础模型到该变体的边

### 冲突处理
- Benchmark: proposed 优先于 baseline，同优先级先到先得
- DAG: self-reported 高优先级，两条高优先级边冲突时新论文优先
- 冲突由脚本自动记录到 conflicts.md
