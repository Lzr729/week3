#!/usr/bin/env python
r"""
Finalize the Week3 submission after the eight-company batch run.

Run from the Week3 project root:

    python pipeline/finalize_week3_submission.py

Creates:
    evaluation/numeric_cross_check.csv
    evaluation/final_week3_metrics.csv
    evaluation/submission_manifest.csv
    WEEK3_SUBMISSION_README.md
"""

from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(".")
EVALUATION = ROOT / "evaluation"
STRUCTURED = ROOT / "outputs" / "structured"
REVIEW_QUEUE = ROOT / "outputs" / "review_queue" / "week3_review_queue.csv"

COMPANY_ORDER = [
    "603418",
    "001282",
    "688758",
    "920100",
    "920116",
    "301581",
    "301563",
    "688775",
]

STRUCTURED_PATTERNS = {
    "subscription_flow": "*_subscription_flow_frozen_v1.jsonl",
    "share_transfer_flow": "*_share_transfer_flow_frozen_v1.jsonl",
    "equity_snapshot": "*_equity_snapshot_v1.jsonl",
}


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, raw in enumerate(file, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                rows.append(
                    {
                        "__parse_error__": (
                            f"line={line_number}; col={exc.colno}; {exc.msg}"
                        )
                    }
                )
                continue
            if isinstance(value, dict):
                rows.append(value)
    return rows


def number(value: Any) -> float | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("normalized_value")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def unit(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("normalized_unit")
    return str(raw) if raw is not None else None


def sum_values(values: Iterable[Any], expected_unit: str) -> float | None:
    total = 0.0
    found = False
    for value in values:
        numeric = number(value)
        value_unit = unit(value)
        if numeric is None:
            continue
        if value_unit != expected_unit:
            continue
        total += numeric
        found = True
    return total if found else None


def close(a: float, b: float, tolerance: float = 0.05) -> bool:
    return math.isclose(a, b, rel_tol=0.0, abs_tol=tolerance)


def add_check(
    rows: list[dict[str, Any]],
    record: dict[str, Any],
    family: str,
    check_name: str,
    status: str,
    expected: float | None,
    actual: float | None,
    normalized_unit: str | None,
    note: str,
) -> None:
    difference = None
    if expected is not None and actual is not None:
        difference = actual - expected

    rows.append(
        {
            "company_code": record.get("company_code", ""),
            "company_name": record.get("company_name", ""),
            "event_family": family,
            "package_id": record.get("package_id", ""),
            "check_name": check_name,
            "status": status,
            "expected": "" if expected is None else expected,
            "actual": "" if actual is None else actual,
            "difference": "" if difference is None else difference,
            "normalized_unit": normalized_unit or "",
            "note": note,
        }
    )


def compare_check(
    rows: list[dict[str, Any]],
    record: dict[str, Any],
    family: str,
    check_name: str,
    expected: float | None,
    actual: float | None,
    normalized_unit: str,
    note: str,
    tolerance: float = 0.05,
) -> None:
    if expected is None or actual is None:
        add_check(
            rows,
            record,
            family,
            check_name,
            "SKIP",
            expected,
            actual,
            normalized_unit,
            f"{note}; required values unavailable",
        )
        return

    add_check(
        rows,
        record,
        family,
        check_name,
        "PASS" if close(expected, actual, tolerance) else "FAIL",
        expected,
        actual,
        normalized_unit,
        note,
    )


def check_subscription(record: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    family = "subscription_flow"

    capital_before = number(record.get("registration_capital_before"))
    capital_after = number(record.get("registration_capital_after"))
    new_capital = number(record.get("new_registered_capital"))
    compare_check(
        rows,
        record,
        family,
        "registration_capital_change",
        capital_after,
        None if capital_before is None or new_capital is None else capital_before + new_capital,
        "CNY",
        "registration_capital_before + new_registered_capital = registration_capital_after",
    )

    shares_before = number(record.get("total_shares_before"))
    shares_after = number(record.get("total_shares_after"))
    new_shares = number(record.get("new_shares"))
    compare_check(
        rows,
        record,
        family,
        "share_count_change",
        shares_after,
        None if shares_before is None or new_shares is None else shares_before + new_shares,
        "share",
        "total_shares_before + new_shares = total_shares_after",
    )

    total_consideration = number(record.get("total_consideration"))
    capital_premium = number(record.get("capital_premium"))
    compare_check(
        rows,
        record,
        family,
        "consideration_allocation",
        total_consideration,
        None if new_capital is None or capital_premium is None else new_capital + capital_premium,
        "CNY",
        "new_registered_capital + capital_premium = total_consideration",
    )

    participants = record.get("participants")
    if not isinstance(participants, list):
        participants = []

    participant_capital = sum_values(
        (
            item.get("subscribed_registered_capital")
            for item in participants
            if isinstance(item, dict)
        ),
        "CNY",
    )
    compare_check(
        rows,
        record,
        family,
        "participant_registered_capital_sum",
        new_capital,
        participant_capital,
        "CNY",
        "sum(participant subscribed_registered_capital) = new_registered_capital",
    )

    participant_contribution = sum_values(
        (
            item.get("contribution_amount")
            for item in participants
            if isinstance(item, dict)
        ),
        "CNY",
    )
    compare_check(
        rows,
        record,
        family,
        "participant_contribution_sum",
        total_consideration,
        participant_contribution,
        "CNY",
        "sum(participant contribution_amount) = total_consideration",
    )


def check_share_transfer(record: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    family = "share_transfer_flow"
    transfers = record.get("transfers")
    if not isinstance(transfers, list):
        transfers = []

    total_capital = number(record.get("total_transferred_registered_capital"))
    transfer_capital = sum_values(
        (
            item.get("transferred_registered_capital")
            for item in transfers
            if isinstance(item, dict)
        ),
        "CNY",
    )
    compare_check(
        rows,
        record,
        family,
        "transfer_registered_capital_sum",
        total_capital,
        transfer_capital,
        "CNY",
        "sum(transferred_registered_capital) = total_transferred_registered_capital",
    )

    total_shares = number(record.get("total_transferred_shares"))
    transfer_shares = sum_values(
        (
            item.get("transferred_shares")
            for item in transfers
            if isinstance(item, dict)
        ),
        "share",
    )
    compare_check(
        rows,
        record,
        family,
        "transfer_share_sum",
        total_shares,
        transfer_shares,
        "share",
        "sum(transferred_shares) = total_transferred_shares",
    )

    total_ratio = number(record.get("total_transferred_ratio"))
    transfer_ratio = sum_values(
        (
            item.get("transferred_ratio")
            for item in transfers
            if isinstance(item, dict)
        ),
        "percent",
    )
    compare_check(
        rows,
        record,
        family,
        "transfer_ratio_sum",
        total_ratio,
        transfer_ratio,
        "percent",
        "sum(transferred_ratio) = total_transferred_ratio",
    )

    total_consideration = number(record.get("total_consideration"))
    transfer_consideration = sum_values(
        (
            item.get("consideration")
            for item in transfers
            if isinstance(item, dict)
        ),
        "CNY",
    )
    compare_check(
        rows,
        record,
        family,
        "transfer_consideration_sum",
        total_consideration,
        transfer_consideration,
        "CNY",
        "sum(consideration) = total_consideration",
    )


def check_snapshot(record: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    family = "equity_snapshot"
    holders = record.get("holders")
    if not isinstance(holders, list):
        holders = []

    total_capital = number(record.get("total_registered_capital"))
    holder_capital = sum_values(
        (
            item.get("registered_capital")
            for item in holders
            if isinstance(item, dict)
        ),
        "CNY",
    )
    compare_check(
        rows,
        record,
        family,
        "holder_registered_capital_sum",
        total_capital,
        holder_capital,
        "CNY",
        "sum(holder registered_capital) = total_registered_capital",
    )

    total_shares = number(record.get("total_shares"))
    holder_shares = sum_values(
        (
            item.get("shares")
            for item in holders
            if isinstance(item, dict)
        ),
        "share",
    )
    compare_check(
        rows,
        record,
        family,
        "holder_share_sum",
        total_shares,
        holder_shares,
        "share",
        "sum(holder shares) = total_shares",
    )

    total_ratio = number(record.get("total_ratio"))
    holder_ratio = sum_values(
        (
            item.get("ownership_ratio")
            for item in holders
            if isinstance(item, dict)
        ),
        "percent",
    )
    compare_check(
        rows,
        record,
        family,
        "holder_ratio_sum",
        total_ratio,
        holder_ratio,
        "percent",
        "sum(holder ownership_ratio) = total_ratio",
    )

    if total_ratio is not None:
        compare_check(
            rows,
            record,
            family,
            "snapshot_total_ratio_100",
            100.0,
            total_ratio,
            "percent",
            "total_ratio should equal 100%",
        )


def run_numeric_checks() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for family, pattern in STRUCTURED_PATTERNS.items():
        for path in sorted(STRUCTURED.glob(pattern)):
            for record in read_jsonl(path):
                if "__parse_error__" in record:
                    rows.append(
                        {
                            "company_code": path.name[:6],
                            "company_name": "",
                            "event_family": family,
                            "package_id": "",
                            "check_name": "json_parse",
                            "status": "FAIL",
                            "expected": "",
                            "actual": "",
                            "difference": "",
                            "normalized_unit": "",
                            "note": record["__parse_error__"],
                        }
                    )
                    continue

                if family == "subscription_flow":
                    check_subscription(record, rows)
                elif family == "share_transfer_flow":
                    check_share_transfer(record, rows)
                elif family == "equity_snapshot":
                    check_snapshot(record, rows)

    return rows


def file_manifest() -> list[dict[str, Any]]:
    paths = [
        Path("evaluation/coverage_audit.csv"),
        Path("evaluation/coverage_summary.csv"),
        Path("evaluation/numeric_cross_check.csv"),
        Path("evaluation/final_week3_metrics.csv"),
        Path("outputs/review_queue/week3_review_queue.csv"),
        Path("outputs/logs/week3_batch_run_log.csv"),
        Path("schemas/subscription_flow.schema.json"),
        Path("schemas/share_transfer_flow.schema.json"),
        Path("schemas/equity_snapshot.schema.json"),
        Path("pipeline/extract_subscription_flow_frozen_v1.py"),
        Path("pipeline/extract_share_transfer_flow_frozen_v1.py"),
        Path("pipeline/extract_equity_snapshot_v1.py"),
        Path("pipeline/run_week3_batch_and_audit.py"),
        Path("pipeline/finalize_week3_submission.py"),
        Path("WEEK3_SUBMISSION_README.md"),
    ]

    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.append(
            {
                "path": str(path),
                "exists": path.exists(),
                "size_bytes": path.stat().st_size if path.exists() else 0,
                "category": path.parts[0] if path.parts else "",
            }
        )
    return rows


def main() -> int:
    coverage_audit = read_csv(EVALUATION / "coverage_audit.csv")
    coverage_summary = read_csv(EVALUATION / "coverage_summary.csv")
    review_queue = read_csv(REVIEW_QUEUE)

    if not coverage_audit or not coverage_summary:
        print("缺少coverage_audit.csv或coverage_summary.csv，请先运行批量审计脚本。")
        return 2

    numeric_rows = run_numeric_checks()
    numeric_fields = [
        "company_code",
        "company_name",
        "event_family",
        "package_id",
        "check_name",
        "status",
        "expected",
        "actual",
        "difference",
        "normalized_unit",
        "note",
    ]
    write_csv(
        EVALUATION / "numeric_cross_check.csv",
        numeric_rows,
        numeric_fields,
    )

    candidate_total = sum(
        int(row.get("candidate_package_count", "0") or 0)
        for row in coverage_audit
    )
    structured_total = sum(
        int(row.get("structured_record_count", "0") or 0)
        for row in coverage_audit
    )
    errors_total = sum(
        int(row.get("error_count", "0") or 0)
        for row in coverage_audit
    )
    reviews_total = sum(
        int(row.get("review_required_count", "0") or 0)
        for row in coverage_audit
    )
    schema_invalid_total = sum(
        int(row.get("schema_invalid_count", "0") or 0)
        for row in coverage_audit
    )
    missing_total = sum(
        int(row.get("missing_package_count", "0") or 0)
        for row in coverage_audit
    )
    complete_companies = sum(
        str(row.get("company_coverage_complete", "")).lower() == "true"
        for row in coverage_summary
    )

    pass_checks = sum(row["status"] == "PASS" for row in numeric_rows)
    fail_checks = sum(row["status"] == "FAIL" for row in numeric_rows)
    skip_checks = sum(row["status"] == "SKIP" for row in numeric_rows)

    final_metrics = [
        {
            "companies_total": len(coverage_summary),
            "companies_coverage_complete": complete_companies,
            "module_rows": len(coverage_audit),
            "candidate_packages_total": candidate_total,
            "structured_records_total": structured_total,
            "extractor_errors_total": errors_total,
            "review_required_records_total": reviews_total,
            "review_queue_rows": len(review_queue),
            "schema_invalid_records_total": schema_invalid_total,
            "missing_packages_total": missing_total,
            "numeric_checks_total": len(numeric_rows),
            "numeric_checks_pass": pass_checks,
            "numeric_checks_fail": fail_checks,
            "numeric_checks_skip": skip_checks,
            "coverage_complete": (
                complete_companies == len(coverage_summary)
                and missing_total == 0
            ),
        }
    ]
    metrics_fields = list(final_metrics[0].keys())
    write_csv(
        EVALUATION / "final_week3_metrics.csv",
        final_metrics,
        metrics_fields,
    )

    readme = f"""# Week3 IPO招股说明书股本变化抽取项目

## 1. 项目范围

本项目对8家公司招股说明书执行统一的可复现流程：

PDF解析 → 页面定位 → 候选事件包切分 → 三类结构化抽取 → Schema校验 → 数值Cross-check → Gold比较 → 人工复核队列。

三类结构化事件为：

- `subscription_flow`
- `share_transfer_flow`
- `equity_snapshot`

## 2. 八家公司覆盖结果

- 公司覆盖：{complete_companies}/{len(coverage_summary)}
- 公司×模块审计单元：{len(coverage_audit)}
- 候选事件包总数：{candidate_total}
- 结构化记录总数：{structured_total}
- 抽取器错误数：{errors_total}
- 未覆盖候选事件包：{missing_total}
- 需要人工复核的结构化记录：{reviews_total}
- Schema无效记录：{schema_invalid_total}
- 人工复核队列行数：{len(review_queue)}

覆盖结论：{"8家公司均已进入流程，且没有候选事件包被静默遗漏。" if complete_companies == len(coverage_summary) and missing_total == 0 else "仍存在覆盖缺口，需要查看 coverage_audit.csv。"}

## 3. 数值Cross-check

- 检查总数：{len(numeric_rows)}
- PASS：{pass_checks}
- FAIL：{fail_checks}
- SKIP：{skip_checks}

`SKIP`表示原始结构化记录缺少完成该等式所需的字段，不等同于数值错误。`FAIL`记录应进入人工复核。

## 4. 主要质量说明

- `subscription_flow` 已完成开发、修复后验证及最终盲测。最终盲测显示复杂的多阶段增资、多参与方汇总及同节股份转让仍是主要失败类型。
- `share_transfer_flow` 已建立Schema、字段级Gold和基础抽取器。首条代表样本核心转让字段正确，少量差异来自公司名称描述和主体确认证据分类。
- `equity_snapshot` 为提交用基础版本。无法稳定解析的复杂表格不会被静默删除，而是生成记录并进入人工复核队列。
- `company_coverage_complete=True` 表示公司已完整进入流程，不表示所有字段均自动正确。
- 所有低置信度、Schema无效、数值Cross-check失败或抽取错误记录均应通过 `outputs/review_queue/week3_review_queue.csv` 复核。

## 5. 核心提交文件

- `evaluation/coverage_audit.csv`
- `evaluation/coverage_summary.csv`
- `evaluation/final_week3_metrics.csv`
- `evaluation/numeric_cross_check.csv`
- `outputs/review_queue/week3_review_queue.csv`
- `outputs/logs/week3_batch_run_log.csv`
- `schemas/*.schema.json`
- `pipeline/extract_*_frozen_v1.py`
- `manual_gold/`

## 6. 结果解读原则

本项目的目标不是将所有样本调参至100%，而是建立可复现、可扩展、能够暴露失败并触发人工复核的抽取流程。最终结果应同时报告自动成功记录、Schema无效记录、数值Cross-check失败记录和人工复核队列。
"""
    Path("WEEK3_SUBMISSION_README.md").write_text(readme, encoding="utf-8")

    manifest_rows = file_manifest()
    write_csv(
        EVALUATION / "submission_manifest.csv",
        manifest_rows,
        ["path", "exists", "size_bytes", "category"],
    )

    print("FINALIZATION COMPLETE")
    print(f"companies_coverage_complete={complete_companies}/{len(coverage_summary)}")
    print(f"candidate_packages_total={candidate_total}")
    print(f"structured_records_total={structured_total}")
    print(f"missing_packages_total={missing_total}")
    print(f"schema_invalid_records_total={schema_invalid_total}")
    print(f"review_required_records_total={reviews_total}")
    print(f"review_queue_rows={len(review_queue)}")
    print(f"numeric_checks_pass={pass_checks}")
    print(f"numeric_checks_fail={fail_checks}")
    print(f"numeric_checks_skip={skip_checks}")
    print("readme=WEEK3_SUBMISSION_README.md")
    print("metrics=evaluation/final_week3_metrics.csv")
    print("numeric_cross_check=evaluation/numeric_cross_check.csv")
    print("manifest=evaluation/submission_manifest.csv")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
