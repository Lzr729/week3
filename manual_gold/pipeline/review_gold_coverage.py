#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

try:
    import fitz
except ImportError:
    fitz = None


COMPANIES = {
    "603418": "友升股份",
    "001282": "三联锻造",
    "688758": "赛分科技",
    "920100": "三协电机",
    "920116": "星图测控",
    "301581": "黄山谷捷",
    "301563": "云汉芯城",
    "688775": "影石创新",
}

VALID_STATUSES = {
    "GOLD_CREATED",
    "REVIEWED_NOT_SELECTED_FOR_FIELD_GOLD",
    "REVIEWED_NEEDS_FURTHER_WORK",
    "FALSE_POSITIVE",
    "OUT_OF_SCOPE",
    "PENDING_HUMAN_REVIEW",
}

CHOICES = {
    "1": "REVIEWED_NOT_SELECTED_FOR_FIELD_GOLD",
    "2": "REVIEWED_NEEDS_FURTHER_WORK",
    "3": "FALSE_POSITIVE",
    "4": "OUT_OF_SCOPE",
    "5": "GOLD_CREATED",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create and interactively complete manual_gold/gold_coverage.csv."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("manual_gold/gold_coverage.csv"),
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Review pending candidate packages one by one.",
    )
    parser.add_argument(
        "--show-text",
        action="store_true",
        help="Extract a short PDF text snippet during interactive review.",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=1800,
    )
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8-sig") as file:
        for raw in file:
            if not raw.strip():
                continue
            value = json.loads(raw)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "company_code",
        "company_name",
        "package_id",
        "event_family",
        "event_title",
        "pages",
        "human_review_status",
        "field_gold_created",
        "review_result",
        "notes",
        "reviewed_by",
        "reviewed_at",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def read_existing(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {
            row.get("package_id", ""): row
            for row in csv.DictReader(file)
            if row.get("package_id")
        }


def gold_package_ids() -> set[str]:
    ids = set()
    for directory in [
        Path("manual_gold/fields/subscription_flow"),
        Path("manual_gold/fields/share_transfer_flow"),
        Path("manual_gold/fields/equity_snapshot"),
    ]:
        if not directory.exists():
            continue
        for path in directory.glob("*.jsonl"):
            for record in read_jsonl(path):
                package_id = record.get("package_id")
                if package_id:
                    ids.add(str(package_id))
    return ids


def package_pages(record: dict[str, Any]) -> list[int]:
    for key in ("all_pages", "primary_pages", "source_pages"):
        value = record.get(key)
        if isinstance(value, list) and value:
            output = []
            for item in value:
                try:
                    page = int(item)
                except (TypeError, ValueError):
                    continue
                if page not in output:
                    output.append(page)
            if output:
                return output
    return []


def load_candidates() -> list[dict[str, Any]]:
    rows = []
    for code in COMPANIES:
        path = Path(
            f"outputs/candidates/{code}_candidate_packages_frozen_v1.jsonl"
        )
        for record in read_jsonl(path):
            rows.append(record)
    return rows


def initialize_rows(output: Path) -> list[dict[str, Any]]:
    existing = read_existing(output)
    gold_ids = gold_package_ids()
    candidates = load_candidates()

    rows = []
    for record in candidates:
        package_id = str(record.get("package_id") or "")
        previous = existing.get(package_id, {})
        is_gold = package_id in gold_ids

        status = previous.get("human_review_status", "")
        if is_gold:
            status = "GOLD_CREATED"
        elif status not in VALID_STATUSES:
            status = "PENDING_HUMAN_REVIEW"

        pages = package_pages(record)
        rows.append(
            {
                "company_code": str(record.get("company_code") or package_id[:6]),
                "company_name": str(
                    record.get("company_name")
                    or COMPANIES.get(package_id[:6], "")
                ),
                "package_id": package_id,
                "event_family": str(record.get("event_family") or ""),
                "event_title": str(record.get("event_title") or ""),
                "pages": "|".join(str(page) for page in pages),
                "human_review_status": status,
                "field_gold_created": "True" if is_gold else "False",
                "review_result": previous.get(
                    "review_result",
                    "FIELD_GOLD_CONFIRMED" if is_gold else "",
                ),
                "notes": previous.get("notes", ""),
                "reviewed_by": previous.get("reviewed_by", ""),
                "reviewed_at": previous.get("reviewed_at", ""),
            }
        )

    rows.sort(key=lambda row: (row["company_code"], row["package_id"]))
    write_csv(output, rows)
    return rows


def find_pdf(company_code: str) -> Path | None:
    matches = sorted(Path("data/pdfs").glob(f"{company_code}*.pdf"))
    return matches[0] if matches else None


def extract_snippet(company_code: str, pages: str, max_chars: int) -> str:
    if fitz is None:
        return "[PyMuPDF unavailable]"
    pdf_path = find_pdf(company_code)
    if pdf_path is None:
        return "[PDF not found]"

    page_numbers = []
    for token in re.split(r"[|,，;\s]+", pages):
        if not token:
            continue
        try:
            page_numbers.append(int(token))
        except ValueError:
            pass

    parts = []
    with fitz.open(pdf_path) as document:
        for page_number in page_numbers:
            index = page_number - 1
            if 0 <= index < document.page_count:
                text = document.load_page(index).get_text("text")
                text = re.sub(r"\s+", " ", text).strip()
                parts.append(f"[PAGE {page_number}] {text}")

    joined = "\n".join(parts)
    return joined[:max_chars]


def review_interactively(
    output: Path,
    rows: list[dict[str, Any]],
    show_text: bool,
    max_chars: int,
) -> None:
    from datetime import datetime

    pending = [
        row
        for row in rows
        if row["human_review_status"] == "PENDING_HUMAN_REVIEW"
    ]

    print(f"pending_packages={len(pending)}")
    print("输入 q 可随时停止；脚本每完成一条都会立即保存。")

    for index, row in enumerate(pending, start=1):
        print("\n" + "=" * 90)
        print(f"[{index}/{len(pending)}]")
        print("company =", row["company_code"], row["company_name"])
        print("package_id =", row["package_id"])
        print("event_family =", row["event_family"])
        print("event_title =", row["event_title"])
        print("pages =", row["pages"])

        if show_text:
            print("-" * 90)
            print(extract_snippet(
                row["company_code"],
                row["pages"],
                max_chars,
            ))

        print("-" * 90)
        print("1 = REVIEWED_NOT_SELECTED_FOR_FIELD_GOLD")
        print("2 = REVIEWED_NEEDS_FURTHER_WORK")
        print("3 = FALSE_POSITIVE")
        print("4 = OUT_OF_SCOPE")
        print("5 = GOLD_CREATED")
        print("s = skip this record")
        print("q = save and quit")

        while True:
            choice = input("choice: ").strip().lower()
            if choice == "q":
                write_csv(output, rows)
                print(f"saved={output}")
                return
            if choice == "s":
                break
            if choice in CHOICES:
                status = CHOICES[choice]
                notes = input("notes (可留空): ").strip()
                reviewer = input("reviewed_by (建议填写姓名或学号): ").strip()

                row["human_review_status"] = status
                row["field_gold_created"] = (
                    "True" if status == "GOLD_CREATED" else row["field_gold_created"]
                )
                row["review_result"] = status
                row["notes"] = notes
                row["reviewed_by"] = reviewer
                row["reviewed_at"] = datetime.now().isoformat(timespec="seconds")
                write_csv(output, rows)
                print("saved")
                break
            print("无效输入，请重新输入。")

    write_csv(output, rows)
    print(f"review_complete={output}")


def print_summary(rows: list[dict[str, Any]]) -> None:
    counts: dict[str, int] = {}
    for row in rows:
        status = row["human_review_status"]
        counts[status] = counts.get(status, 0) + 1

    print(f"candidate_packages={len(rows)}")
    for status in sorted(counts):
        print(f"{status}={counts[status]}")
    print(
        "human_review_complete=",
        counts.get("PENDING_HUMAN_REVIEW", 0) == 0,
    )


def main() -> int:
    args = parse_args()
    rows = initialize_rows(args.output)
    print_summary(rows)

    if args.interactive:
        review_interactively(
            args.output,
            rows,
            show_text=args.show_text,
            max_chars=args.max_chars,
        )
        rows = initialize_rows(args.output)
        print_summary(rows)

    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
