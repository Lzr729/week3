#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
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

LABELS = [
    "capital_increase",
    "share_transfer",
    "overall_conversion",
    "establishment_contribution",
    "capital_reserve_conversion",
    "equity_snapshot",
    "historical_context",
    "other",
    "out_of_scope",
    "false_positive",
]

CHOICES = {
    "1": "capital_increase",
    "2": "share_transfer",
    "3": "overall_conversion",
    "4": "establishment_contribution",
    "5": "capital_reserve_conversion",
    "6": "equity_snapshot",
    "7": "historical_context",
    "8": "other",
    "9": "out_of_scope",
    "0": "false_positive",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and manually review event-type classification for all candidate packages."
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("evaluation/event_type_classification.csv"),
    )
    parser.add_argument(
        "--summary-output",
        type=Path,
        default=Path("evaluation/event_type_summary.csv"),
    )
    parser.add_argument(
        "--confusion-output",
        type=Path,
        default=Path("evaluation/event_type_confusion_matrix.csv"),
    )
    parser.add_argument("--interactive", action="store_true")
    parser.add_argument("--show-text", action="store_true")
    parser.add_argument("--max-chars", type=int, default=1600)
    return parser.parse_args()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
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


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def package_pages(record: dict[str, Any]) -> list[int]:
    for key in ("all_pages", "primary_pages", "source_pages"):
        value = record.get(key)
        if isinstance(value, list) and value:
            output: list[int] = []
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


def infer_rule_type(raw_family: str, title: str) -> tuple[str, str]:
    raw = (raw_family or "").strip().lower()
    text = (title or "").strip()

    if "资本公积转增" in text or "转增股本" in text:
        return "capital_reserve_conversion", "title_keyword"
    if "整体变更" in text or "整体改制" in text:
        return "overall_conversion", "title_keyword"
    if "设立" in text and any(word in text for word in ("出资", "注册资本", "发起人")):
        return "establishment_contribution", "title_keyword"

    if raw == "subscription_flow":
        return "capital_increase", "raw_event_family"
    if raw == "share_transfer_flow":
        return "share_transfer", "raw_event_family"
    if raw == "overall_conversion":
        return "overall_conversion", "raw_event_family"
    if raw == "equity_snapshot":
        return "equity_snapshot", "raw_event_family"
    if raw == "historical_context":
        return "historical_context", "raw_event_family"
    if raw in {"establishment", "establishment_flow"}:
        return "establishment_contribution", "raw_event_family"
    if raw in {"capital_reserve_conversion", "capital_reserve_to_capital"}:
        return "capital_reserve_conversion", "raw_event_family"

    if "股权转让" in text or "股份转让" in text:
        return "share_transfer", "title_keyword"
    if any(word in text for word in ("增资", "定向发行", "股票发行", "吸收合并")):
        return "capital_increase", "title_keyword"
    if any(word in text for word in ("股权结构", "股本结构", "股权关系图", "股东情况")):
        return "equity_snapshot", "title_keyword"
    if "代持" in text:
        return "historical_context", "title_keyword"
    return "other", "fallback_other"


def load_gold_coverage() -> dict[str, dict[str, str]]:
    path = Path("manual_gold/gold_coverage.csv")
    return {
        row.get("package_id", ""): row
        for row in read_csv(path)
        if row.get("package_id")
    }


def load_existing(path: Path) -> dict[str, dict[str, str]]:
    return {
        row.get("package_id", ""): row
        for row in read_csv(path)
        if row.get("package_id")
    }


def load_candidates() -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for code in COMPANIES:
        path = Path(
            f"outputs/candidates/{code}_candidate_packages_frozen_v1.jsonl"
        )
        output.extend(read_jsonl(path))
    return output


def initialize_rows(output_path: Path) -> list[dict[str, Any]]:
    existing = load_existing(output_path)
    gold_coverage = load_gold_coverage()
    candidates = load_candidates()
    rows: list[dict[str, Any]] = []

    for record in candidates:
        package_id = str(record.get("package_id") or "")
        code = str(record.get("company_code") or package_id[:6])
        title = str(record.get("event_title") or "")
        raw_family = str(record.get("event_family") or "")
        predicted, basis = infer_rule_type(raw_family, title)
        previous = existing.get(package_id, {})
        gold_row = gold_coverage.get(package_id, {})

        human_type = previous.get("human_event_type", "")
        human_status = gold_row.get("human_review_status", "")

        # For explicit out-of-scope / false-positive decisions, initialize the human label.
        if not human_type and human_status == "OUT_OF_SCOPE":
            human_type = "out_of_scope"
        elif not human_type and human_status == "FALSE_POSITIVE":
            human_type = "false_positive"

        rows.append(
            {
                "company_code": code,
                "company_name": str(
                    record.get("company_name")
                    or COMPANIES.get(code, "")
                ),
                "package_id": package_id,
                "raw_event_family": raw_family,
                "event_title": title,
                "pages": "|".join(map(str, package_pages(record))),
                "rule_predicted_type": predicted,
                "rule_basis": basis,
                "human_event_type": human_type,
                "type_match": (
                    str(predicted == human_type)
                    if human_type
                    else ""
                ),
                "human_review_status": human_status,
                "classification_notes": previous.get(
                    "classification_notes", ""
                ),
                "reviewed_by": previous.get("reviewed_by", ""),
                "reviewed_at": previous.get("reviewed_at", ""),
            }
        )

    rows.sort(key=lambda row: (row["company_code"], row["package_id"]))
    return rows


def find_pdf(code: str) -> Path | None:
    matches = sorted(Path("data/pdfs").glob(f"{code}*.pdf"))
    return matches[0] if matches else None


def extract_snippet(code: str, pages: str, max_chars: int) -> str:
    if fitz is None:
        return "[PyMuPDF unavailable]"
    pdf_path = find_pdf(code)
    if pdf_path is None:
        return "[PDF not found]"

    page_numbers: list[int] = []
    for token in re.split(r"[|,，;\s]+", pages):
        if not token:
            continue
        try:
            page_numbers.append(int(token))
        except ValueError:
            pass

    parts: list[str] = []
    with fitz.open(pdf_path) as document:
        for page_number in page_numbers:
            index = page_number - 1
            if 0 <= index < document.page_count:
                text = document.load_page(index).get_text("text")
                text = re.sub(r"\s+", " ", text).strip()
                parts.append(f"[PAGE {page_number}] {text}")

    return "\n".join(parts)[:max_chars]


def review_interactively(
    output_path: Path,
    rows: list[dict[str, Any]],
    show_text: bool,
    max_chars: int,
) -> None:
    pending = [row for row in rows if not row["human_event_type"]]
    print(f"pending_classifications={len(pending)}")
    print("Enter=确认规则预测；q=保存退出；s=跳过本条。")

    fields = list(rows[0].keys())

    for index, row in enumerate(pending, start=1):
        print("\n" + "=" * 96)
        print(f"[{index}/{len(pending)}]")
        print("company =", row["company_code"], row["company_name"])
        print("package_id =", row["package_id"])
        print("raw_event_family =", row["raw_event_family"])
        print("event_title =", row["event_title"])
        print("pages =", row["pages"])
        print("rule_predicted_type =", row["rule_predicted_type"])
        print("human_review_status =", row["human_review_status"])

        if show_text:
            print("-" * 96)
            print(extract_snippet(
                row["company_code"],
                row["pages"],
                max_chars,
            ))

        print("-" * 96)
        print("Enter = 确认规则预测")
        print("1 capital_increase")
        print("2 share_transfer")
        print("3 overall_conversion")
        print("4 establishment_contribution")
        print("5 capital_reserve_conversion")
        print("6 equity_snapshot")
        print("7 historical_context")
        print("8 other")
        print("9 out_of_scope")
        print("0 false_positive")
        print("s skip")
        print("q save and quit")

        while True:
            choice = input("choice: ").strip().lower()
            if choice == "q":
                write_csv(output_path, rows, fields)
                print(f"saved={output_path}")
                return
            if choice == "s":
                break
            if choice == "":
                human_type = row["rule_predicted_type"]
            elif choice in CHOICES:
                human_type = CHOICES[choice]
            else:
                print("无效输入，请重新输入。")
                continue

            notes = input("classification_notes (可留空): ").strip()
            reviewer = input("reviewed_by: ").strip()

            row["human_event_type"] = human_type
            row["type_match"] = str(
                human_type == row["rule_predicted_type"]
            )
            row["classification_notes"] = notes
            row["reviewed_by"] = reviewer
            row["reviewed_at"] = datetime.now().isoformat(
                timespec="seconds"
            )
            write_csv(output_path, rows, fields)
            print("saved")
            break


def build_metrics(
    rows: list[dict[str, Any]],
    summary_path: Path,
    confusion_path: Path,
) -> None:
    labeled = [row for row in rows if row["human_event_type"]]
    total = len(rows)
    correct = sum(row["type_match"] == "True" for row in labeled)

    summary_rows: list[dict[str, Any]] = []
    all_labels = sorted(set(LABELS) | {
        row["rule_predicted_type"] for row in labeled
    } | {
        row["human_event_type"] for row in labeled
    })

    for label in all_labels:
        tp = sum(
            row["rule_predicted_type"] == label
            and row["human_event_type"] == label
            for row in labeled
        )
        fp = sum(
            row["rule_predicted_type"] == label
            and row["human_event_type"] != label
            for row in labeled
        )
        fn = sum(
            row["rule_predicted_type"] != label
            and row["human_event_type"] == label
            for row in labeled
        )
        support = sum(
            row["human_event_type"] == label
            for row in labeled
        )
        precision = tp / (tp + fp) if tp + fp else ""
        recall = tp / (tp + fn) if tp + fn else ""
        f1 = (
            2 * precision * recall / (precision + recall)
            if isinstance(precision, float)
            and isinstance(recall, float)
            and precision + recall
            else ""
        )
        summary_rows.append(
            {
                "label": label,
                "human_support": support,
                "true_positive": tp,
                "false_positive": fp,
                "false_negative": fn,
                "precision": precision,
                "recall": recall,
                "f1": f1,
            }
        )

    summary_rows.append(
        {
            "label": "__OVERALL__",
            "human_support": len(labeled),
            "true_positive": correct,
            "false_positive": "",
            "false_negative": "",
            "precision": correct / len(labeled) if labeled else "",
            "recall": "",
            "f1": "",
        }
    )
    summary_rows.append(
        {
            "label": "__COVERAGE__",
            "human_support": total,
            "true_positive": len(labeled),
            "false_positive": total - len(labeled),
            "false_negative": "",
            "precision": len(labeled) / total if total else "",
            "recall": "",
            "f1": "",
        }
    )

    write_csv(
        summary_path,
        summary_rows,
        [
            "label",
            "human_support",
            "true_positive",
            "false_positive",
            "false_negative",
            "precision",
            "recall",
            "f1",
        ],
    )

    confusion: dict[tuple[str, str], int] = defaultdict(int)
    for row in labeled:
        confusion[
            (row["human_event_type"], row["rule_predicted_type"])
        ] += 1

    confusion_rows: list[dict[str, Any]] = []
    for human_label in all_labels:
        output_row: dict[str, Any] = {
            "human_label": human_label
        }
        for predicted_label in all_labels:
            output_row[predicted_label] = confusion[
                (human_label, predicted_label)
            ]
        confusion_rows.append(output_row)

    write_csv(
        confusion_path,
        confusion_rows,
        ["human_label"] + all_labels,
    )

    print(f"candidate_packages={total}")
    print(f"manually_labeled={len(labeled)}")
    print(f"pending={total-len(labeled)}")
    print(f"correct={correct}")
    print(
        "overall_accuracy=",
        correct / len(labeled) if labeled else 0,
    )
    print(f"summary={summary_path}")
    print(f"confusion_matrix={confusion_path}")


def main() -> int:
    args = parse_args()
    rows = initialize_rows(args.output)
    fields = list(rows[0].keys())
    write_csv(args.output, rows, fields)

    if args.interactive:
        review_interactively(
            args.output,
            rows,
            show_text=args.show_text,
            max_chars=args.max_chars,
        )
        rows = initialize_rows(args.output)
        write_csv(args.output, rows, fields)

    build_metrics(
        rows,
        args.summary_output,
        args.confusion_output,
    )
    print(f"classification={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
