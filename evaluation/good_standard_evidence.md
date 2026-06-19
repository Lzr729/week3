# 较好标准证据索引

## 1. 稳定定位章节和候选事件包

- 页面定位指标：`evaluation/section_location_metrics.csv`
- 候选事件包指标：`evaluation/candidate_package_metrics.csv`
- 页面审计结果：V2.1 recall=1.0，precision=0.2857
- 候选包回归集：TP=71，FP=0，FN=0

## 2. 多事件类型区分

- 正式分类表：`evaluation/event_type_classification.csv`
- 分类汇总：`evaluation/event_type_summary.csv`
- 混淆矩阵：`evaluation/event_type_confusion_matrix.csv`
- 人工确认后一致率：0.9859154929577465

覆盖类型包括增资认缴、股权转让、整体变更、设立出资、资本公积转增和股权快照。

## 3. Schema或Cross-check失败后的复核任务

- Schema一致性：`evaluation/schema_validation_summary.csv`
- 失败到复核追踪：`evaluation/failure_to_review_trace.csv`
- 严格Schema失败：9
- 已链接复核任务：9

## 4. 规则覆盖率和失败边界

- 量化表：`evaluation/rule_coverage_metrics.csv`
- 详细规则说明：`rule_coverage.md`
- 错误分析：`evaluation/error_analysis.md`

## 5. Prompt敏感性

- 提示词版本：`prompts/prompt_variants.md`
- 说明：`evaluation/prompt_sensitivity.md`
- AI使用声明：`prompts/ai_usage_statement.md`

## 6. 自动结果与基准口径差异

- 基准差异说明：`evaluation/baseline_comparison.md`
- 字段差异：`evaluation/row_match.csv`
- 事件汇总：`evaluation/event_summary.csv`
- 数值检查：`evaluation/numeric_cross_check.csv`

## 结论

上述文件共同覆盖第三周“较好标准”的五个要点。候选包100%结果属于当前8家公司回归集，不声称为独立盲测；页面定位结果明确披露高召回、低精度边界。
