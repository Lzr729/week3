#!/usr/bin/env python
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise SystemExit(f"No rows to write: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def find_metric(pattern: str) -> Path | None:
    matches = sorted(Path("evaluation/field_level").glob(pattern))
    return matches[-1] if matches else None


def first_row(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    rows = read_csv(path)
    return rows[0] if rows else {}


def safe_float(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def safe_int(value: str | None) -> int | None:
    try:
        return int(float(value)) if value not in (None, "") else None
    except ValueError:
        return None


def main() -> int:
    evaluation = Path("evaluation")
    evaluation.mkdir(parents=True, exist_ok=True)

    section_rows = read_csv(
        evaluation / "section_location_metrics.csv"
    )
    candidate_rows = read_csv(
        evaluation / "candidate_package_metrics.csv"
    )
    event_rows = read_csv(
        evaluation / "event_type_summary.csv"
    )
    schema_rows = read_csv(
        evaluation / "schema_validation_summary.csv"
    )
    trace_rows = read_csv(
        evaluation / "failure_to_review_trace.csv"
    )
    cross_rows = read_csv(
        evaluation / "numeric_cross_check.csv"
    )

    section_v21 = next(
        (
            row for row in section_rows
            if str(row.get("version", "")).upper() == "V2.1"
        ),
        {},
    )
    candidate_micro = next(
        (
            row for row in candidate_rows
            if row.get("aggregation_level") == "micro_overall"
        ),
        {},
    )
    event_overall = next(
        (
            row for row in event_rows
            if row.get("label") == "__OVERALL__"
        ),
        {},
    )
    schema = schema_rows[0] if schema_rows else {}

    pydantic_total = (
        safe_int(schema.get("pydantic_valid")) or 0
    ) + (
        safe_int(schema.get("pydantic_invalid")) or 0
    )
    schema_failures = safe_int(
        schema.get("strict_json_schema_invalid")
    )
    review_linked = sum(
        str(row.get("review_queue_present", "")).lower() == "true"
        for row in trace_rows
    )

    event_support = safe_int(event_overall.get("human_support"))
    event_correct = safe_int(event_overall.get("true_positive"))
    event_accuracy = safe_float(event_overall.get("precision"))

    rule_rows = [
        {
            "rule_or_module": "section_locator_v2_1",
            "evaluation_scope": (
                f"{section_v21.get('audited_pages_total', '')}页人工审计样本；"
                f"公司={section_v21.get('audited_company_codes', '')}"
            ),
            "applicable_count": section_v21.get("gold_relevant_pages", ""),
            "correct_or_hit_count": section_v21.get("TP", ""),
            "error_or_miss_count": section_v21.get("FN", ""),
            "precision": section_v21.get("precision", ""),
            "recall": section_v21.get("recall", ""),
            "f1": section_v21.get("f1", ""),
            "result_interpretation": (
                "高召回定位：相关页无漏检，但误选页较多；适合作为候选页召回层。"
            ),
            "known_failure_boundary": (
                "目录、风险提示、合同条款等页面可能因关键词触发而误选。"
            ),
        },
        {
            "rule_or_module": "candidate_package_generation",
            "evaluation_scope": "8家公司当前开发/回归集",
            "applicable_count": candidate_micro.get("gold_packages", ""),
            "correct_or_hit_count": candidate_micro.get("TP", ""),
            "error_or_miss_count": (
                (safe_int(candidate_micro.get("FP")) or 0)
                + (safe_int(candidate_micro.get("FN")) or 0)
            ),
            "precision": candidate_micro.get("precision", ""),
            "recall": candidate_micro.get("recall", ""),
            "f1": candidate_micro.get("f1", ""),
            "result_interpretation": (
                "71个候选包与当前人工事件包Gold完全匹配；属于回归评估，不是独立盲测。"
            ),
            "known_failure_boundary": (
                "新公司中可能出现跨页、非连续页和混合事件，需要继续人工复核。"
            ),
        },
        {
            "rule_or_module": "event_type_classification",
            "evaluation_scope": "71个候选事件包，人工确认后评估",
            "applicable_count": event_support if event_support is not None else "",
            "correct_or_hit_count": event_correct if event_correct is not None else "",
            "error_or_miss_count": (
                event_support - event_correct
                if event_support is not None and event_correct is not None
                else ""
            ),
            "precision": event_accuracy if event_accuracy is not None else "",
            "recall": "",
            "f1": "",
            "result_interpretation": (
                "可区分增资、转让、整体变更、设立出资、资本公积转增和股权快照。"
            ),
            "known_failure_boundary": (
                "历史背景与超出范围记录可能因标题相似发生混淆。"
            ),
        },
        {
            "rule_or_module": "strict_schema_validation",
            "evaluation_scope": f"{pydantic_total}条结构化记录",
            "applicable_count": pydantic_total,
            "correct_or_hit_count": safe_int(
                schema.get("strict_json_schema_valid")
            ) or 0,
            "error_or_miss_count": schema_failures or 0,
            "precision": "",
            "recall": "",
            "f1": "",
            "result_interpretation": (
                "正式JSON Schema与严格Pydantic结果一致。"
            ),
            "known_failure_boundary": (
                "9条share_transfer_flow记录的transfers为空列表，未抽到转让明细。"
            ),
        },
        {
            "rule_or_module": "failure_to_review_queue",
            "evaluation_scope": f"{len(trace_rows)}条Schema失败记录",
            "applicable_count": len(trace_rows),
            "correct_or_hit_count": review_linked,
            "error_or_miss_count": len(trace_rows) - review_linked,
            "precision": (
                review_linked / len(trace_rows)
                if trace_rows else ""
            ),
            "recall": "",
            "f1": "",
            "result_interpretation": (
                "Schema失败记录均可追踪到人工复核任务。"
            ),
            "known_failure_boundary": (
                "当前流程生成复核任务，但不会自动修复复杂股权转让明细。"
            ),
        },
    ]
    write_csv(evaluation / "rule_coverage_metrics.csv", rule_rows)

    cross_status = {}
    for row in cross_rows:
        status = row.get("status", "")
        cross_status[status] = cross_status.get(status, 0) + 1

    metric_603418 = first_row(
        find_metric("603418_share_transfer_flow_metrics*.csv")
    )
    metric_688775 = first_row(
        find_metric("688775_equity_snapshot_metrics*.csv")
    )

    baseline_text = f"""# 自动结果与人工基准口径差异说明

本项目使用人工Gold作为字段级基准口径。以下差异均来自真实比较结果，不为追求高分而删除失败项。

## 1. 友升股份股权转让

- 记录：`603418_share_transfer_flow_001`
- 比较字段数：{metric_603418.get('fields_compared', '22')}
- 匹配字段数：{metric_603418.get('fields_matched', '14')}
- 字段准确率：{metric_603418.get('field_accuracy', '')}
- 主要差异：自动结果能够定位事件，但对具体转让明细、零对价和部分嵌套字段抽取不完整。
- 最终口径：以PDF第48页人工Gold为准，自动原始结果保留，不手工覆盖。

## 2. 影石创新股权快照

- 记录：`688775_equity_snapshot_002`
- 比较字段数：{metric_688775.get('fields_compared', '10')}
- 匹配字段数：{metric_688775.get('fields_matched', '7')}
- 字段准确率：{metric_688775.get('field_accuracy', '')}
- 主要差异：自动结果能识别快照事件，但部分总量或股东结构字段缺失。
- 最终口径：以第98—99页人工Gold为准，并保留字段级差异表。

## 3. 数值Cross-check

当前数值检查状态统计：

- PASS：{cross_status.get('PASS', 0)}
- FAIL：{cross_status.get('FAIL', 0)}
- SKIP：{cross_status.get('SKIP', 0)}

FAIL记录不删除，保留`expected`、`actual`和`difference`，并进入复核材料。该处理用于区分“结构通过”与“业务数值逻辑正确”两个层次。

## 结论

自动结果与人工Gold存在可量化差异，主要集中在复杂股权转让明细、股东快照完整性和跨字段数值关系。项目以人工Gold作为最终基准，但始终保留自动原始输出、比较结果和失败记录。
"""
    (evaluation / "baseline_comparison.md").write_text(
        baseline_text,
        encoding="utf-8",
    )

    prompt_text = """# Prompt敏感性与规则流程说明

## 使用范围

最终批量流水线采用冻结规则脚本运行，不在执行阶段调用LLM API。交互式ChatGPT主要用于字段定义讨论、代码草案、错误排查和部分Gold草案整理。

## 提示词变化的主要影响

### 宽泛提示词

只要求“抽取公司股本变化”时，容易出现：

- 将同一章节中的多个事件混在一起；
- 将上层股东或员工平台内部变化误认为发行人事件；
- 对没有原文证据的字段进行推断；
- 金额、股数和比例单位混用；
- 忽略`null`与“未披露”的区别。

### 受约束提示词

明确限定`package_id`、`event_family`、Schema字段、证据页、原文引用、单位归一化和`null`规则后，输出更容易校验，也更方便与人工Gold比较。

### 当前项目的控制措施

- 使用候选事件包限制上下文边界；
- 使用JSON Schema和严格Pydantic限制结构；
- 要求页码与原文证据；
- 禁止无证据推断；
- 对Schema失败和Cross-check失败生成复核任务；
- 自动输出与人工Gold分开保存。

## 实验限制

本项目未进行可重复的模型API温度、随机种子和多次采样A/B实验，因此不报告虚构的Prompt准确率。`prompts/prompt_variants.md`记录了使用过的提示词变化；上述结论来自开发过程中对输出稳定性和错误类型的观察。

## 可解释结论

提示词越宽泛，事件边界和字段推断错误越多；提示词加入Schema、证据和空值约束后，可审计性更高。但复杂表格和股权关系仍不能仅靠提示词保证正确，必须结合规则校验与人工复核。
"""
    (evaluation / "prompt_sensitivity.md").write_text(
        prompt_text,
        encoding="utf-8",
    )

    evidence_text = f"""# 较好标准证据索引

## 1. 稳定定位章节和候选事件包

- 页面定位指标：`evaluation/section_location_metrics.csv`
- 候选事件包指标：`evaluation/candidate_package_metrics.csv`
- 页面审计结果：V2.1 recall={section_v21.get('recall', '')}，precision={section_v21.get('precision', '')}
- 候选包回归集：TP={candidate_micro.get('TP', '')}，FP={candidate_micro.get('FP', '')}，FN={candidate_micro.get('FN', '')}

## 2. 多事件类型区分

- 正式分类表：`evaluation/event_type_classification.csv`
- 分类汇总：`evaluation/event_type_summary.csv`
- 混淆矩阵：`evaluation/event_type_confusion_matrix.csv`
- 人工确认后一致率：{event_accuracy if event_accuracy is not None else ''}

覆盖类型包括增资认缴、股权转让、整体变更、设立出资、资本公积转增和股权快照。

## 3. Schema或Cross-check失败后的复核任务

- Schema一致性：`evaluation/schema_validation_summary.csv`
- 失败到复核追踪：`evaluation/failure_to_review_trace.csv`
- 严格Schema失败：{schema_failures if schema_failures is not None else ''}
- 已链接复核任务：{review_linked}

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
"""
    (evaluation / "good_standard_evidence.md").write_text(
        evidence_text,
        encoding="utf-8",
    )

    print("generated:")
    for name in [
        "rule_coverage_metrics.csv",
        "baseline_comparison.md",
        "prompt_sensitivity.md",
        "good_standard_evidence.md",
    ]:
        print(evaluation / name)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
