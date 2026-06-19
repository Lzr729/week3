#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare equity_snapshot nested Gold with automatic output."
    )
    parser.add_argument("--gold", required=True, type=Path)
    parser.add_argument("--auto", required=True, type=Path)
    parser.add_argument("--detail-output", required=True, type=Path)
    parser.add_argument("--summary-output", required=True, type=Path)
    parser.add_argument("--numeric-tolerance", type=float, default=0.01)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, raw in enumerate(file, start=1):
            if not raw.strip():
                continue
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise SystemExit(f"{path} line {line_number}: not an object")
            rows.append(value)
    return rows


def index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {
        str(row.get("package_id")): row
        for row in rows
        if row.get("package_id")
    }


def text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def normalized(value: Any) -> tuple[float | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    try:
        number = float(value.get("normalized_value"))
    except (TypeError, ValueError):
        number = None
    unit = value.get("normalized_unit")
    return number, str(unit) if unit is not None else None


def compare_scalar(gold: Any, auto: Any) -> tuple[str, str]:
    if gold == auto:
        return "MATCH", ""
    if auto is None and gold is not None:
        return "MISSING_AUTO", "自动结果为空"
    if gold is None and auto is not None:
        return "EXTRA_AUTO", "Gold为空但自动结果有值"
    return "MISMATCH", "值不一致"


def compare_numeric(
    gold: Any,
    auto: Any,
    tolerance: float,
) -> tuple[str, str]:
    gv, gu = normalized(gold)
    av, au = normalized(auto)

    if gv is None and av is None:
        return "MATCH", ""
    if gv is not None and av is None:
        return "MISSING_AUTO", "自动结果缺少normalized_value"
    if gv is None and av is not None:
        return "EXTRA_AUTO", "Gold为空但自动结果有数值"
    if gu != au:
        return "MISMATCH", f"单位不一致：gold={gu}; auto={au}"
    if math.isclose(gv, av, rel_tol=0.0, abs_tol=tolerance):
        return "MATCH", ""
    return "MISMATCH", f"数值不一致：gold={gv}; auto={av}"


def compare_set(gold: Any, auto: Any) -> tuple[str, str]:
    gs = set(gold or [])
    aset = set(auto or [])
    if gs == aset:
        return "MATCH", ""
    if gs and not aset:
        return "MISSING_AUTO", "自动结果为空集合"
    if aset and not gs:
        return "EXTRA_AUTO", "Gold为空集合但自动结果有值"
    return (
        "MISMATCH",
        f"missing={sorted(gs-aset)}; extra={sorted(aset-gs)}",
    )


def holder_map(record: dict[str, Any]) -> dict[str, dict[str, Any]]:
    output = {}
    for item in record.get("holders") or []:
        if isinstance(item, dict) and item.get("holder_name"):
            output[str(item["holder_name"])] = item
    return output


def add(
    rows: list[dict[str, Any]],
    package_id: str,
    field_path: str,
    status: str,
    gold_value: Any,
    auto_value: Any,
    note: str,
) -> None:
    rows.append({
        "package_id": package_id,
        "field_path": field_path,
        "status": status,
        "gold_value": text(gold_value),
        "auto_value": text(auto_value),
        "note": note,
    })


def main() -> int:
    args = parse_args()
    gold_index = index(read_jsonl(args.gold))
    auto_index = index(read_jsonl(args.auto))

    rows: list[dict[str, Any]] = []
    matched = sorted(set(gold_index) & set(auto_index))
    missing = sorted(set(gold_index) - set(auto_index))
    extra = sorted(set(auto_index) - set(gold_index))

    for package_id in matched:
        gold = gold_index[package_id]
        auto = auto_index[package_id]

        for field in [
            "event_family",
            "snapshot_type",
            "issuer_confirmed",
            "currency",
        ]:
            status, note = compare_scalar(gold.get(field), auto.get(field))
            add(rows, package_id, field, status, gold.get(field), auto.get(field), note)

        status, note = compare_scalar(
            (gold.get("snapshot_date") or {}).get("iso_date")
            if isinstance(gold.get("snapshot_date"), dict)
            else None,
            (auto.get("snapshot_date") or {}).get("iso_date")
            if isinstance(auto.get("snapshot_date"), dict)
            else None,
        )
        add(
            rows,
            package_id,
            "snapshot_date",
            status,
            gold.get("snapshot_date"),
            auto.get("snapshot_date"),
            note,
        )

        status, note = compare_set(
            gold.get("source_pages"),
            auto.get("source_pages"),
        )
        add(
            rows,
            package_id,
            "source_pages",
            status,
            gold.get("source_pages"),
            auto.get("source_pages"),
            note,
        )

        for field in [
            "total_registered_capital",
            "total_shares",
            "total_ratio",
        ]:
            status, note = compare_numeric(
                gold.get(field),
                auto.get(field),
                args.numeric_tolerance,
            )
            add(rows, package_id, field, status, gold.get(field), auto.get(field), note)

        gold_holders = holder_map(gold)
        auto_holders = holder_map(auto)

        status, note = compare_set(
            list(gold_holders),
            list(auto_holders),
        )
        add(
            rows,
            package_id,
            "holders.names",
            status,
            sorted(gold_holders),
            sorted(auto_holders),
            note,
        )

        for name in sorted(set(gold_holders) & set(auto_holders)):
            for field in [
                "registered_capital",
                "shares",
                "ownership_ratio",
            ]:
                status, note = compare_numeric(
                    gold_holders[name].get(field),
                    auto_holders[name].get(field),
                    args.numeric_tolerance,
                )
                add(
                    rows,
                    package_id,
                    f"holders[{name}].{field}",
                    status,
                    gold_holders[name].get(field),
                    auto_holders[name].get(field),
                    note,
                )

    for package_id in missing:
        add(
            rows,
            package_id,
            "__record__",
            "MISSING_AUTO",
            gold_index[package_id],
            None,
            "Gold记录没有对应自动结果",
        )

    # Only mark extras belonging to the same company as Gold records.
    gold_codes = {
        str(row.get("company_code"))
        for row in gold_index.values()
        if row.get("company_code")
    }
    for package_id in extra:
        auto = auto_index[package_id]
        if str(auto.get("company_code")) in gold_codes:
            add(
                rows,
                package_id,
                "__record__",
                "EXTRA_AUTO",
                None,
                auto,
                "同一公司自动结果没有对应Gold记录；不计入该Gold package字段准确率",
            )

    args.detail_output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "package_id",
        "field_path",
        "status",
        "gold_value",
        "auto_value",
        "note",
    ]
    with args.detail_output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    package_rows = [
        row
        for row in rows
        if row["package_id"] in set(gold_index)
    ]
    compared = len(package_rows)
    matched_count = sum(row["status"] == "MATCH" for row in package_rows)
    mismatch_count = compared - matched_count

    summary = {
        "gold_records": len(gold_index),
        "auto_records": len(auto_index),
        "matched_packages": len(matched),
        "missing_packages": len(missing),
        "fields_compared": compared,
        "fields_matched": matched_count,
        "fields_mismatched": mismatch_count,
        "field_accuracy": matched_count / compared if compared else 0,
        "match_count": matched_count,
        "mismatch_count": sum(
            row["status"] == "MISMATCH" for row in package_rows
        ),
        "missing_auto_count": sum(
            row["status"] == "MISSING_AUTO" for row in package_rows
        ),
        "extra_auto_count": sum(
            row["status"] == "EXTRA_AUTO" for row in package_rows
        ),
    }

    args.summary_output.parent.mkdir(parents=True, exist_ok=True)
    with args.summary_output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(summary))
        writer.writeheader()
        writer.writerow(summary)

    for key, value in summary.items():
        print(f"{key}={value}")
    print(f"detail_output={args.detail_output}")
    print(f"summary_output={args.summary_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
