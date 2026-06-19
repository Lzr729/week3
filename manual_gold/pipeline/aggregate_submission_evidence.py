#!/usr/bin/env python
from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any


ROOT = Path(".")
MANUAL = ROOT / "manual_gold"
EVAL = ROOT / "evaluation"


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    with path.open("r", encoding="utf-8-sig") as file:
        for raw in file:
            if raw.strip():
                value = json.loads(raw)
                if isinstance(value, dict):
                    rows.append(value)
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def aggregate_gold(family: str, output_name: str) -> list[dict[str, Any]]:
    source_dir = MANUAL / "fields" / family
    rows: list[dict[str, Any]] = []
    if source_dir.exists():
        for path in sorted(source_dir.glob("*.jsonl")):
            rows.extend(read_jsonl(path))
    write_jsonl(MANUAL / output_name, rows)
    return rows


def build_annotation_index(groups: dict[str, list[dict[str, Any]]]) -> None:
    rows = []
    for family, records in groups.items():
        for record in records:
            evidence = record.get("evidence")
            if not isinstance(evidence, list):
                continue
            for idx, item in enumerate(evidence, start=1):
                if not isinstance(item, dict):
                    continue
                rows.append({
                    "event_family": family,
                    "company_code": record.get("company_code", ""),
                    "company_name": record.get("company_name", ""),
                    "package_id": record.get("package_id", ""),
                    "event_id": record.get("event_id", ""),
                    "evidence_index": idx,
                    "page": item.get("page", ""),
                    "evidence_role": item.get("evidence_role", ""),
                    "evidence_text": item.get("text", ""),
                    "review_status": (
                        "REVIEW"
                        if record.get("needs_manual_review")
                        else "CONFIRMED"
                    ),
                })
    write_csv(
        MANUAL / "annotation_index.csv",
        rows,
        [
            "event_family",
            "company_code",
            "company_name",
            "package_id",
            "event_id",
            "evidence_index",
            "page",
            "evidence_role",
            "evidence_text",
            "review_status",
        ],
    )


def aggregate_row_match() -> list[dict[str, Any]]:
    rows = []
    field_level = EVAL / "field_level"
    if field_level.exists():
        for path in sorted(field_level.glob("*comparison*.csv")):
            for row in read_csv(path):
                row["source_file"] = str(path)
                package_id = row.get("package_id", "")
                row["company_code"] = package_id[:6]
                if "subscription_flow" in path.name:
                    row["event_family"] = "subscription_flow"
                elif "share_transfer_flow" in path.name:
                    row["event_family"] = "share_transfer_flow"
                elif "equity_snapshot" in path.name:
                    row["event_family"] = "equity_snapshot"
                else:
                    row["event_family"] = ""
                rows.append(row)

    fields = [
        "company_code",
        "event_family",
        "package_id",
        "field_path",
        "status",
        "gold_value",
        "auto_value",
        "note",
        "source_file",
    ]
    write_csv(EVAL / "row_match.csv", rows, fields)
    return rows


def build_event_summary(row_match: list[dict[str, Any]]) -> None:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in row_match:
        key = (
            row.get("company_code", ""),
            row.get("event_family", ""),
            row.get("package_id", ""),
        )
        groups.setdefault(key, []).append(row)

    output = []
    for (company_code, family, package_id), rows in sorted(groups.items()):
        compared = len(rows)
        matched = sum(row.get("status") == "MATCH" for row in rows)
        missing = sum(row.get("status") == "MISSING_AUTO" for row in rows)
        extra = sum(row.get("status") == "EXTRA_AUTO" for row in rows)
        mismatch = compared - matched - missing - extra
        output.append({
            "company_code": company_code,
            "event_family": family,
            "package_id": package_id,
            "fields_compared": compared,
            "fields_matched": matched,
            "fields_mismatched": mismatch,
            "fields_missing_auto": missing,
            "fields_extra_auto": extra,
            "field_accuracy": matched / compared if compared else 0,
        })

    write_csv(
        EVAL / "event_summary.csv",
        output,
        [
            "company_code",
            "event_family",
            "package_id",
            "fields_compared",
            "fields_matched",
            "fields_mismatched",
            "fields_missing_auto",
            "fields_extra_auto",
            "field_accuracy",
        ],
    )


def main() -> int:
    subscription = aggregate_gold(
        "subscription_flow",
        "subscription_flow_gold.jsonl",
    )
    share_transfer = aggregate_gold(
        "share_transfer_flow",
        "share_transfer_flow_gold.jsonl",
    )
    equity_nested = aggregate_gold(
        "equity_snapshot",
        "equity_snapshot_gold_nested.jsonl",
    )

    groups = {
        "subscription_flow": subscription,
        "share_transfer_flow": share_transfer,
        "equity_snapshot": equity_nested,
    }
    build_annotation_index(groups)

    numeric = EVAL / "numeric_cross_check.csv"
    if numeric.exists():
        shutil.copyfile(numeric, MANUAL / "cross_check_summary.csv")

    row_match = aggregate_row_match()
    build_event_summary(row_match)

    print(f"subscription_gold_records={len(subscription)}")
    print(f"share_transfer_gold_records={len(share_transfer)}")
    print(f"equity_snapshot_nested_gold_records={len(equity_nested)}")
    print(f"row_match_rows={len(row_match)}")
    print("annotation_index=manual_gold/annotation_index.csv")
    print("cross_check_summary=manual_gold/cross_check_summary.csv")
    print("event_summary=evaluation/event_summary.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
