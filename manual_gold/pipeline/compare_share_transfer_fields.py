#!/usr/bin/env python
r"""
Compare share_transfer_flow JSONL output with field-level Gold.

PowerShell example:
    python pipeline/compare_share_transfer_fields.py `
      --gold manual_gold/fields/share_transfer_flow/301581_share_transfer_flow_gold.jsonl `
      --auto outputs/structured/301581_share_transfer_flow_v1.jsonl `
      --detail-output evaluation/field_level/301581_share_transfer_flow_comparison_v1.csv `
      --summary-output evaluation/field_level/301581_share_transfer_flow_metrics_v1.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


DATE_FIELDS = [
    "decision_date",
    "agreement_date",
    "payment_date",
    "registration_date",
    "effective_date",
]

SCALAR_FIELDS = [
    "event_family",
    "transfer_subtype",
    "target_company_name",
    "target_security_type",
    "target_is_issuer",
    "currency",
    "pre_event_snapshot_present",
    "post_event_snapshot_present",
]

SET_FIELDS = [
    "source_pages",
    "pre_event_snapshot_pages",
    "post_event_snapshot_pages",
]

NUMERIC_FIELDS = [
    "total_transferred_registered_capital",
    "total_transferred_shares",
    "total_transferred_ratio",
    "total_consideration",
]

TRANSFER_NUMERIC_FIELDS = [
    "transferred_registered_capital",
    "transferred_shares",
    "transferred_ratio",
    "consideration",
    "price_per_share",
    "price_per_registered_capital",
]

TRANSFER_SCALAR_FIELDS = [
    "payment_method",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare share_transfer_flow JSONL against field-level Gold."
    )
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--auto", required=True, type=Path)
    parser.add_argument("--detail-output", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--numeric-tolerance", type=float, default=0.01)
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"文件不存在：{path}")

    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, raw_line in enumerate(file, start=1):
            text = raw_line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"{path} 第{line_number}行JSON解析失败，"
                    f"第{exc.colno}列：{exc.msg}"
                ) from exc
            if not isinstance(record, dict):
                raise SystemExit(f"{path} 第{line_number}行不是JSON对象。")
            records.append(record)
    return records


def record_key(record: dict[str, Any]) -> str:
    package_id = record.get("package_id")
    if package_id:
        return str(package_id)
    event_id = record.get("event_id")
    if event_id:
        return str(event_id)
    raise SystemExit("记录缺少 package_id 和 event_id。")


def index_records(
    records: list[dict[str, Any]],
    source_name: str,
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for record in records:
        key = record_key(record)
        if key in indexed:
            raise SystemExit(f"{source_name}中package_id重复：{key}")
        indexed[key] = record
    return indexed


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def compare_scalar(gold: Any, auto: Any) -> tuple[str, str]:
    if gold == auto:
        return "MATCH", ""
    if auto is None and gold is not None:
        return "MISSING_AUTO", "自动结果为空"
    if gold is None and auto is not None:
        return "EXTRA_AUTO", "Gold为空但自动结果有值"
    return "MISMATCH", "值不一致"


def compare_date(gold: Any, auto: Any) -> tuple[str, str]:
    gold_iso = gold.get("iso_date") if isinstance(gold, dict) else None
    auto_iso = auto.get("iso_date") if isinstance(auto, dict) else None

    if gold_iso == auto_iso:
        return "MATCH", ""
    if auto_iso is None and gold_iso is not None:
        return "MISSING_AUTO", "自动结果缺少iso_date"
    if gold_iso is None and auto_iso is not None:
        return "EXTRA_AUTO", "Gold为空但自动结果有日期"
    return "MISMATCH", f"gold_iso={gold_iso}; auto_iso={auto_iso}"


def as_set(value: Any) -> set[Any]:
    if value is None:
        return set()
    if isinstance(value, list):
        return set(value)
    return {value}


def compare_set(gold: Any, auto: Any) -> tuple[str, str]:
    gold_set = as_set(gold)
    auto_set = as_set(auto)

    if gold_set == auto_set:
        return "MATCH", ""
    if gold_set and not auto_set:
        return "MISSING_AUTO", "自动结果为空集合"
    if auto_set and not gold_set:
        return "EXTRA_AUTO", "Gold为空集合但自动结果有值"

    missing = sorted(gold_set - auto_set)
    extra = sorted(auto_set - gold_set)
    return "MISMATCH", f"missing={missing}; extra={extra}"


def normalized_numeric(value: Any) -> tuple[Any, Any]:
    if not isinstance(value, dict):
        return None, None
    return value.get("normalized_value"), value.get("normalized_unit")


def compare_numeric(
    gold: Any,
    auto: Any,
    tolerance: float,
) -> tuple[str, str]:
    gold_value, gold_unit = normalized_numeric(gold)
    auto_value, auto_unit = normalized_numeric(auto)

    if gold_value is None and auto_value is None:
        return "MATCH", ""

    if gold_value is not None and auto_value is None:
        return "MISSING_AUTO", "自动结果缺少normalized_value"

    if gold_value is None and auto_value is not None:
        return "EXTRA_AUTO", "Gold为空但自动结果有数值"

    if gold_unit != auto_unit:
        return "MISMATCH", f"单位不一致：gold={gold_unit}; auto={auto_unit}"

    try:
        matched = math.isclose(
            float(gold_value),
            float(auto_value),
            rel_tol=0.0,
            abs_tol=tolerance,
        )
    except (TypeError, ValueError):
        return "MISMATCH", "normalized_value不是可比较数字"

    if matched:
        return "MATCH", ""

    return (
        "MISMATCH",
        f"数值不一致：gold={gold_value}; auto={auto_value}; tolerance={tolerance}",
    )


def transfer_key(item: dict[str, Any]) -> str:
    return f"{item.get('transferor_name', '')} -> {item.get('transferee_name', '')}"


def transfer_map(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    transfers = record.get("transfers")
    if not isinstance(transfers, list):
        return {}

    output: dict[str, dict[str, Any]] = {}
    for item in transfers:
        if not isinstance(item, dict):
            continue
        key = transfer_key(item)
        if key.strip(" ->"):
            output[key] = item
    return output


def add_row(
    rows: list[dict[str, Any]],
    package_id: str,
    field_path: str,
    gold_value: Any,
    auto_value: Any,
    status: str,
    note: str,
) -> None:
    rows.append(
        {
            "package_id": package_id,
            "field_path": field_path,
            "status": status,
            "gold_value": json_text(gold_value),
            "auto_value": json_text(auto_value),
            "note": note,
        }
    )


def compare_record(
    package_id: str,
    gold: dict[str, Any],
    auto: dict[str, Any],
    tolerance: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    for field in SCALAR_FIELDS:
        status, note = compare_scalar(gold.get(field), auto.get(field))
        add_row(rows, package_id, field, gold.get(field), auto.get(field), status, note)

    for field in DATE_FIELDS:
        status, note = compare_date(gold.get(field), auto.get(field))
        add_row(rows, package_id, field, gold.get(field), auto.get(field), status, note)

    for field in SET_FIELDS:
        status, note = compare_set(gold.get(field), auto.get(field))
        add_row(rows, package_id, field, gold.get(field), auto.get(field), status, note)

    for field in NUMERIC_FIELDS:
        status, note = compare_numeric(gold.get(field), auto.get(field), tolerance)
        add_row(rows, package_id, field, gold.get(field), auto.get(field), status, note)

    gold_confirmation = gold.get("target_confirmation")
    auto_confirmation = auto.get("target_confirmation")

    status, note = compare_scalar(
        gold_confirmation.get("confirmed_by")
        if isinstance(gold_confirmation, dict)
        else None,
        auto_confirmation.get("confirmed_by")
        if isinstance(auto_confirmation, dict)
        else None,
    )
    add_row(
        rows,
        package_id,
        "target_confirmation.confirmed_by",
        gold_confirmation.get("confirmed_by")
        if isinstance(gold_confirmation, dict)
        else None,
        auto_confirmation.get("confirmed_by")
        if isinstance(auto_confirmation, dict)
        else None,
        status,
        note,
    )

    gold_transfers = transfer_map(gold)
    auto_transfers = transfer_map(auto)

    status, note = compare_set(
        list(gold_transfers.keys()),
        list(auto_transfers.keys()),
    )
    add_row(
        rows,
        package_id,
        "transfers.keys",
        sorted(gold_transfers.keys()),
        sorted(auto_transfers.keys()),
        status,
        note,
    )

    for key in sorted(set(gold_transfers) & set(auto_transfers)):
        gold_item = gold_transfers[key]
        auto_item = auto_transfers[key]

        for field in TRANSFER_SCALAR_FIELDS:
            status, note = compare_scalar(gold_item.get(field), auto_item.get(field))
            add_row(
                rows,
                package_id,
                f"transfers[{key}].{field}",
                gold_item.get(field),
                auto_item.get(field),
                status,
                note,
            )

        for field in TRANSFER_NUMERIC_FIELDS:
            status, note = compare_numeric(
                gold_item.get(field),
                auto_item.get(field),
                tolerance,
            )
            add_row(
                rows,
                package_id,
                f"transfers[{key}].{field}",
                gold_item.get(field),
                auto_item.get(field),
                status,
                note,
            )

    return rows


def write_detail(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "package_id",
        "field_path",
        "status",
        "gold_value",
        "auto_value",
        "note",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_summary(
    path: Path,
    gold_count: int,
    auto_count: int,
    matched_packages: int,
    missing_packages: int,
    extra_packages: int,
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    fields_compared = len(rows)
    fields_matched = sum(row["status"] == "MATCH" for row in rows)
    fields_mismatched = fields_compared - fields_matched
    field_accuracy = (
        fields_matched / fields_compared if fields_compared else 0.0
    )

    status_counts: dict[str, int] = {}
    for row in rows:
        status = row["status"]
        status_counts[status] = status_counts.get(status, 0) + 1

    summary = {
        "gold_records": gold_count,
        "auto_records": auto_count,
        "matched_packages": matched_packages,
        "missing_packages": missing_packages,
        "extra_packages": extra_packages,
        "fields_compared": fields_compared,
        "fields_matched": fields_matched,
        "fields_mismatched": fields_mismatched,
        "field_accuracy": field_accuracy,
        "match_count": status_counts.get("MATCH", 0),
        "mismatch_count": status_counts.get("MISMATCH", 0),
        "missing_auto_count": status_counts.get("MISSING_AUTO", 0),
        "extra_auto_count": status_counts.get("EXTRA_AUTO", 0),
    }

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary.keys()))
        writer.writeheader()
        writer.writerow(summary)


def main() -> int:
    args = parse_args()

    gold_records = load_jsonl(args.gold)
    auto_records = load_jsonl(args.auto)

    gold_index = index_records(gold_records, "Gold")
    auto_index = index_records(auto_records, "Auto")

    gold_keys = set(gold_index)
    auto_keys = set(auto_index)

    matched_keys = sorted(gold_keys & auto_keys)
    missing_keys = sorted(gold_keys - auto_keys)
    extra_keys = sorted(auto_keys - gold_keys)

    detail_rows: list[dict[str, Any]] = []

    for package_id in matched_keys:
        detail_rows.extend(
            compare_record(
                package_id=package_id,
                gold=gold_index[package_id],
                auto=auto_index[package_id],
                tolerance=args.numeric_tolerance,
            )
        )

    for package_id in missing_keys:
        add_row(
            detail_rows,
            package_id,
            "__record__",
            gold_index[package_id],
            None,
            "MISSING_AUTO",
            "Gold记录没有对应自动结果",
        )

    for package_id in extra_keys:
        add_row(
            detail_rows,
            package_id,
            "__record__",
            None,
            auto_index[package_id],
            "EXTRA_AUTO",
            "自动结果没有对应Gold记录",
        )

    write_detail(args.detail_output, detail_rows)
    write_summary(
        path=args.summary_output,
        gold_count=len(gold_records),
        auto_count=len(auto_records),
        matched_packages=len(matched_keys),
        missing_packages=len(missing_keys),
        extra_packages=len(extra_keys),
        rows=detail_rows,
    )

    fields_matched = sum(row["status"] == "MATCH" for row in detail_rows)
    fields_compared = len(detail_rows)
    field_accuracy = (
        fields_matched / fields_compared if fields_compared else 0.0
    )

    print(f"gold_records={len(gold_records)}")
    print(f"auto_records={len(auto_records)}")
    print(f"matched_packages={len(matched_keys)}")
    print(f"missing_packages={len(missing_keys)}")
    print(f"extra_packages={len(extra_keys)}")
    print(f"fields_compared={fields_compared}")
    print(f"fields_matched={fields_matched}")
    print(f"field_accuracy={field_accuracy:.4f}")
    print(f"detail_output={args.detail_output}")
    print(f"summary_output={args.summary_output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
