# Week3 IPO 招股说明书股本变化抽取与验证流程

## 1. 项目简介

本项目围绕 8 家 IPO 公司招股说明书，建立一套从 PDF 到结构化结果、再到验证与人工复核的可复现流程。项目目标不是只生成一批 JSON，而是形成完整闭环：

```text
PDF
→ 页面与章节定位
→ 候选事件包生成
→ 事件类型分类
→ 三类结构化抽取
→ JSON Schema / Pydantic 校验
→ 数值 Cross-check
→ 自动结果与人工 Gold 比较
→ 失败样本进入人工复核队列
```

三类核心结构化对象为：

- `subscription_flow`：增资、定向发行、设立出资等资本流量事件；
- `share_transfer_flow`：股权转让，包括转让方、受让方、比例、价款等；
- `equity_snapshot`：关键时点的股权或股本结构存量快照。

项目同时保留人工 Gold、自动输出、比较结果和人工复核记录，避免将人工修正后的结果与自动结果混在一起。

---

## 2. 样本范围

本项目固定使用以下 8 家公司，不扩展新样本：

| 证券代码 | 公司简称 | PDF 文件名 |
|---|---|---|
| 603418 | 友升股份 | `603418_友升股份_IPO招股说明书.pdf` |
| 301563 | 云汉芯城 | `301563_云汉芯城_IPO招股说明书.pdf` |
| 301581 | 黄山谷捷 | `301581_黄山谷捷_IPO招股说明书.pdf` |
| 688758 | 赛分科技 | `688758_赛分科技_IPO招股说明书.pdf` |
| 688775 | 影石创新 | `688775_影石创新_IPO招股说明书.pdf` |
| 920100 | 三协电机 | `920100_三协电机_IPO招股说明书.pdf` |
| 920116 | 星图测控 | `920116_星图测控_IPO招股说明书.pdf` |
| 001282 | 三联锻造 | `001282_三联锻造_IPO招股说明书.pdf` |

PDF 文件默认放在：

```text
data/pdfs/
```

仓库中建议只提交 `data/pdf_manifest.csv` 和公司别名配置，不提交体积较大的 PDF 原文件。

---

## 3. 仓库结构

```text
week3/
├── README.md
├── requirements.txt
├── rule_coverage.md
│
├── data/
│   ├── pdf_manifest.csv
│   ├── company_aliases.csv
│   └── pdfs/                         # 本地放置 PDF，通常不提交 GitHub
│
├── manual_gold/
│   ├── subscription_flow_gold.jsonl
│   ├── share_transfer_flow_gold.jsonl
│   ├── equity_snapshot_gold.jsonl
│   ├── equity_snapshot_gold_nested.jsonl
│   ├── cross_check_gold.jsonl
│   ├── cross_check_summary.csv
│   ├── annotation_index.csv
│   ├── manual_review_queue.csv
│   ├── gold_coverage.csv
│   ├── event_packages/
│   └── fields/
│       ├── subscription_flow/
│       ├── share_transfer_flow/
│       └── equity_snapshot/
│
├── pipeline/
│   ├── 页面定位与候选包生成脚本
│   ├── 三类结构化抽取脚本
│   ├── JSON Schema 与 Pydantic 校验脚本
│   ├── Cross-check 与 Gold 比较脚本
│   ├── 批量运行与证据汇总脚本
│   └── 最终提交检查脚本
│
├── schemas/
│   ├── subscription_flow.schema.json
│   ├── share_transfer_flow.schema.json
│   └── equity_snapshot.schema.json
│
├── prompts/
│   ├── system_prompt.md
│   ├── user_prompt_template.md
│   ├── prompt_variants.md
│   └── ai_usage_statement.md
│
├── outputs/
│   ├── candidates/
│   ├── structured/
│   ├── validation/
│   ├── logs/
│   └── review_queue/
│
└── evaluation/
    ├── coverage_audit.csv
    ├── coverage_summary.csv
    ├── final_week3_metrics.csv
    ├── row_match.csv
    ├── event_summary.csv
    ├── numeric_cross_check.csv
    ├── error_analysis.md
    ├── field_level/
    ├── schema_validation_summary.csv
    ├── schema_consistency_report_v2.csv
    ├── failure_to_review_trace.csv
    ├── event_type_classification.csv
    ├── event_type_summary.csv
    ├── event_type_confusion_matrix.csv
    ├── section_location_metrics.csv
    ├── candidate_package_metrics.csv
    ├── rule_coverage_metrics.csv
    ├── prompt_sensitivity.md
    ├── baseline_comparison.md
    └── good_standard_evidence.md
```

---

## 4. 环境要求

推荐环境：

- Python 3.11
- Windows PowerShell 或兼容终端
- 依赖见 `requirements.txt`

创建虚拟环境并安装依赖：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

所有脚本使用相对路径，不依赖个人电脑的绝对路径。

---

## 5. 从零运行

### 5.1 准备 PDF

将 8 份招股说明书放入：

```text
data/pdfs/
```

文件名应与 `data/pdf_manifest.csv` 一致。

检查文件是否齐全：

```powershell
Import-Csv data\pdf_manifest.csv |
ForEach-Object {
    [PSCustomObject]@{
        company_code = $_.company_code
        pdf_exists = Test-Path $_.pdf_path
    }
} |
Format-Table -AutoSize
```

### 5.2 运行批量流程

推荐入口：

```powershell
python pipeline\run_week3_batch_and_audit.py
```

该流程依次完成：

1. 读取 PDF；
2. 定位历史沿革、股本形成及变化等候选页面；
3. 生成冻结版候选事件包；
4. 执行三类结构化抽取；
5. 运行 Schema 和数值检查；
6. 生成覆盖审计、日志和人工复核队列。

### 5.3 汇总人工 Gold 和比较结果

```powershell
python pipeline\aggregate_submission_evidence.py
```

主要输出：

```text
manual_gold/annotation_index.csv
manual_gold/cross_check_summary.csv
evaluation/row_match.csv
evaluation/event_summary.csv
```

### 5.4 运行严格 Pydantic 校验

```powershell
python pipeline\validate_with_pydantic.py
```

输出：

```text
outputs/validation/pydantic_validation_all.csv
```

### 5.5 最终提交检查

```powershell
python pipeline\check_submission_readiness.py
```

提交前理想结果：

```text
missing_files=0
gold_coverage_pending=0
manifest_missing=0
submission_ready=True
```

---

## 6. 自动流程与人工介入点

### 6.1 自动完成的步骤

- PDF 文本读取；
- 章节和候选页面定位；
- 候选事件包切分；
- 事件类型初步分类；
- 三类结构化记录生成；
- JSON Schema 校验；
- 严格 Pydantic 校验；
- 数值 Cross-check；
- 自动结果与 Gold 的字段比较；
- 失败记录和低置信度记录进入复核队列。

### 6.2 人工完成的步骤

- 回到 PDF 核对候选事件边界和证据页；
- 确认主体是否为发行人或发行人前身；
- 核对日期、金额、股数、比例和单位；
- 确认 71 个候选事件包的事件级分类；
- 对代表性字段级 Gold 进行 PDF 回源复核；
- 对 Schema 失败、Cross-check 失败和复杂表格进行人工判断；
- 记录不确定性，而不是将无法确认的字段硬填为正确。

人工修改不会覆盖自动原始输出。自动结果、人工 Gold 和比较结果分别保存在不同目录。

---

## 7. 人工 Gold 的范围

本项目的人工标注分为两个层级。

### 7.1 事件级人工审阅

- 候选事件包总数：71；
- 已完成人工事件级审阅：71；
- 待审阅：0；
- 审阅状态见 `manual_gold/gold_coverage.csv`。

事件级审阅用于确认候选是否属于项目范围、事件类型是否合理，以及是否需要进一步处理。

### 7.2 字段级人工 Gold

字段级人工 Gold 采用分层代表性标注：

| 类型 | 字段级 Gold 数量 |
|---|---:|
| `subscription_flow` | 6 |
| `share_transfer_flow` | 2 |
| `equity_snapshot` | 2 |
| 合计 | 10 |

这 10 条代表性记录覆盖 8 家公司和 3 类核心结构化对象。每条记录包含 PDF 页码、证据原文、标准化字段和人工核对依据。

本项目不声称 49 条核心自动记录均已完成完整字段级人工标注。未进入字段级 Gold 的候选包仍保留在覆盖表和人工复核队列中，不被视为已经自动确认。

---

## 8. 方法说明

### 8.1 规则与程序的分工

规则主要负责：

- 章节标题和关键词定位；
- 页面召回；
- 候选事件包边界切分；
- 事件类型初步判断；
- 日期、金额、比例和单位识别；
- 三类结构化对象的基础抽取；
- 数值关系检查。

详细规则和失败边界见：

```text
rule_coverage.md
evaluation/rule_coverage_metrics.csv
```

### 8.2 LLM / ChatGPT 的使用

交互式 ChatGPT 用于：

- 讨论字段定义和 Schema 设计；
- 生成部分代码草案；
- 分析运行报错；
- 整理部分 Gold 草案；
- 帮助总结规则边界和错误类型。

最终 8 家公司的批量自动结果由本地冻结规则脚本生成，运行过程中不依赖模型 API。

AI 生成的草案不直接视为人工 Gold。只有在本人回到 PDF 核对页码、原文、主体、日期、数值和单位后，记录才标记为 `GOLD_CREATED`。

详细说明见：

```text
prompts/ai_usage_statement.md
evaluation/prompt_sensitivity.md
```

本项目没有进行可重复的模型 API 温度、随机种子和多次采样实验，因此不报告虚构的 Prompt 统计准确率。

---

## 9. 主要结果

### 9.1 公司和模块覆盖

| 指标 | 结果 |
|---|---:|
| 公司覆盖 | 8 / 8 |
| 公司 × 核心模块 | 24 |
| 全部候选事件包 | 71 |
| 三类核心自动结构化记录 | 49 |
| 未覆盖核心候选包 | 0 |

`company_coverage_complete=True` 只表示公司和模块已进入流程，不表示所有字段均自动正确。

### 9.2 页面定位

在友升股份 82 页人工审计样本上：

| 版本 | Precision | Recall | F1 |
|---|---:|---:|---:|
| V1 | 27.08% | 92.86% | 41.94% |
| V2.1 | 28.57% | 100.00% | 44.44% |

V2.1 实现相关页面零漏检，但仍存在较多误选页面。当前页面定位策略属于高召回、低精度的候选页召回层。

该指标只基于友升股份 82 页人工审计样本，不能表述为 8 家公司全部页面的独立测试结果。

### 9.3 候选事件包

在当前 8 家公司开发／回归集上：

| 指标 | 结果 |
|---|---:|
| 人工事件包 Gold | 71 |
| 自动候选事件包 | 71 |
| TP | 71 |
| FP | 0 |
| FN | 0 |
| Precision | 100% |
| Recall | 100% |
| F1 | 100% |

事件类型、主页面边界、支持页面和完整页面集合在当前回归集上均完全匹配。

该结果用于验证当前流程的回归一致性，不作为独立盲测准确率。

### 9.4 事件类型分类

71 个候选事件包经人工确认后的结果：

| 指标 | 结果 |
|---|---:|
| 分类一致 | 70 |
| 分类不一致 | 1 |
| 一致率 | 98.59% |

覆盖的主要事件类型包括：

- 增资认缴；
- 股权转让；
- 整体变更；
- 设立出资；
- 资本公积转增；
- 股权结构快照；
- 历史背景；
- 超出范围记录。

详细结果见：

```text
evaluation/event_type_classification.csv
evaluation/event_type_summary.csv
evaluation/event_type_confusion_matrix.csv
```

### 9.5 Schema 校验

正式 JSON Schema 与严格 Pydantic 均检查 49 条自动结构化记录，结果一致：

| 状态 | 数量 |
|---|---:|
| VALID | 40 |
| INVALID | 9 |

9 条无效记录均属于 `share_transfer_flow`，失败原因是：

```text
transfers 为空列表
```

即自动流程识别到了股权转让事件，但未能抽取具体的“转让方—受让方”明细。

这些记录没有被删除，也没有手工改写为通过，而是保留原始自动结果并全部进入人工复核队列。

相关文件：

```text
evaluation/schema_validation_summary.csv
evaluation/schema_consistency_report_v2.csv
evaluation/failure_to_review_trace.csv
outputs/validation/pydantic_validation_all.csv
```

### 9.6 数值 Cross-check

| 状态 | 数量 |
|---|---:|
| PASS | 27 |
| FAIL | 3 |
| SKIP | 159 |
| 合计 | 189 |

`FAIL` 记录保留 `expected`、`actual` 和 `difference`，用于人工复核。

`SKIP` 表示当前结构化记录缺少完成该检查所需的字段，或该检查对该事件不适用，不等同于数值错误。

详细结果见：

```text
evaluation/numeric_cross_check.csv
manual_gold/cross_check_summary.csv
```

### 9.7 自动结果与人工 Gold 差异

自动结果不会为了追求 100% 而手工覆盖。示例：

| 记录 | 字段匹配情况 |
|---|---:|
| `603418_share_transfer_flow_001` | 14 / 22，63.64% |
| `688775_equity_snapshot_002` | 7 / 10，70.00% |

主要差异集中在：

- 复杂股权转让明细；
- 零对价、代持和同一控制关系；
- 股东表格完整性；
- 跨页或非连续页快照；
- 日期口径；
- 数量、金额和单位归一化。

详细差异见：

```text
evaluation/row_match.csv
evaluation/event_summary.csv
evaluation/field_level/
evaluation/baseline_comparison.md
```

---

## 10. 失败处理与人工复核队列

流程在以下情况生成复核任务：

- JSON Schema 无效；
- 严格 Pydantic 无效；
- 数值 Cross-check 失败；
- 自动结果字段缺失；
- 规则命中但置信度较低；
- 复杂表格无法稳定解析；
- 候选事件边界或主体关系不确定。

Schema 失败追踪结果：

```text
Schema 失败记录：9
已生成复核任务：9
缺失复核任务：0
```

复核队列：

```text
outputs/review_queue/week3_review_queue.csv
```

失败到复核任务的逐条追踪：

```text
evaluation/failure_to_review_trace.csv
```

当前流程能够自动发现失败并生成复核任务，但不会自动伪造缺失的股权转让明细。

---

## 11. 与基准口径的差异

本项目使用人工 Gold 作为字段级基准，同时保留自动结果和差异表。

对于教师基础示例、自动规则和人工判断之间可能存在的差异，采用以下处理原则：

1. 回到 PDF 核对页码和原文；
2. 区分原文披露值与计算值；
3. 明确单位和标准化口径；
4. 不因 Schema 通过而默认业务数值正确；
5. 不因自动结果失败而删除记录；
6. 将无法确定的事项保留在复核队列。

具体案例见：

```text
evaluation/baseline_comparison.md
evaluation/error_analysis.md
```

---

## 12. 已知限制

1. 页面定位偏向高召回，仍会误选目录、风险提示和合同条款等页面。
2. 复杂股权转让可能识别到事件，但无法稳定抽出转让方和受让方明细。
3. 跨页表格、图示股权结构和非连续页快照仍依赖人工核对。
4. “股、万股、元、万元、百分比”等单位需要额外标准化和数值校验。
5. 71 个候选包完全匹配属于当前样本回归结果，不代表对新公司同样达到 100%。
6. 字段级 Gold 是代表性分层标注，不是 49 条核心记录的全量专家级标注。
7. 若扩展到 50 家公司，最先可能出现的问题是章节标题差异、PDF 表格解析失败、复杂历史沿革事件边界和跨页股东表。

---

## 13. Week3 验收要求对应关系

| 验收要求 | 对应文件或结果 |
|---|---|
| 8 家公司全部覆盖 | `evaluation/coverage_summary.csv` |
| 人工 Gold、自动输出、比较结果分开 | `manual_gold/`、`outputs/structured/`、`evaluation/` |
| Gold 有页码和原文证据 | `manual_gold/fields/`、`manual_gold/annotation_index.csv` |
| 自动流程可运行且无绝对路径 | `pipeline/`、本 README 第 5 节 |
| Pydantic Schema 校验 | `outputs/validation/pydantic_validation_all.csv` |
| 带数字 Cross-check | `evaluation/numeric_cross_check.csv` |
| 自动结果与 Gold 差异表 | `evaluation/row_match.csv`、`evaluation/field_level/` |
| Prompt 或规则说明 | `rule_coverage.md`、`prompts/` |
| 失败样本和人工复核队列 | `outputs/review_queue/week3_review_queue.csv` |
| 稳定定位章节和候选包 | `evaluation/section_location_metrics.csv`、`candidate_package_metrics.csv` |
| 区分多种事件类型 | `evaluation/event_type_summary.csv` |
| Schema 失败后生成复核任务 | `evaluation/failure_to_review_trace.csv` |
| 规则覆盖率和提示词敏感性 | `evaluation/rule_coverage_metrics.csv`、`prompt_sensitivity.md` |
| 解释与基准不一致项目 | `evaluation/baseline_comparison.md` |
| 较好标准证据索引 | `evaluation/good_standard_evidence.md` |

---

## 14. 提交状态

最终就绪检查命令：

```powershell
python pipeline\check_submission_readiness.py
```

当前结果：

```text
missing_files=0
gold_coverage_pending=0
manifest_missing=0
submission_ready=True
```

因此当前版本已具备 Week3 提交条件。

---

## 15. 结果解读原则

本项目不以“所有结果均通过”作为唯一目标，而以以下原则作为质量标准：

- 能回到 PDF 复核；
- 能区分人工 Gold 和自动结果；
- 能量化自动结果与 Gold 的差异；
- 能发现 Schema 和数值错误；
- 能保留失败记录；
- 能把失败转化为人工复核任务；
- 能说明规则覆盖范围和失败边界；
- 能在不夸大结果的前提下复现整个流程。

当前流程尚未达到完全 Agent 化，但已经形成可信、可审计、可扩展的 Week3 股本变化抽取闭环。
