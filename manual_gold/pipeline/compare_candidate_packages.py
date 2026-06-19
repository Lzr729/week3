from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path


def parse_pages(value: str | None) -> set[int]:
    if value is None:
        return set()
    value = str(value).strip()
    if not value:
        return set()
    return {int(part) for part in value.split("|") if part.strip()}


def page_iou(a: set[int], b: set[int]) -> float:
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def load_gold(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            row["primary_set"] = parse_pages(row.get("primary_pages"))
            row["support_set"] = parse_pages(row.get("supporting_pages"))
            rows.append(row)
    return rows


def load_auto(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            row = json.loads(line)
            row["primary_set"] = set(row.get("primary_pages", []))
            row["support_set"] = set(row.get("supporting_pages", []))
            rows.append(row)
    return rows


def best_matches(gold: list[dict], auto: list[dict]) -> list[dict]:
    unmatched_auto = set(range(len(auto)))
    results: list[dict] = []

    for g in gold:
        candidates: list[tuple[float, int]] = []
        for index in unmatched_auto:
            a = auto[index]
            if a["event_family"] != g["event_family"]:
                continue
            score = page_iou(g["primary_set"], a["primary_set"])
            candidates.append((score, index))

        if not candidates:
            results.append(
                {
                    "gold_package_id": g["package_id"],
                    "auto_package_id": "",
                    "event_family_gold": g["event_family"],
                    "event_family_auto": "",
                    "gold_primary_pages": "|".join(map(str, sorted(g["primary_set"]))),
                    "auto_primary_pages": "",
                    "gold_supporting_pages": "|".join(map(str, sorted(g["support_set"]))),
                    "auto_supporting_pages": "",
                    "primary_page_iou": 0.0,
                    "event_match": False,
                    "type_correct": False,
                    "primary_boundary_exact": False,
                    "supporting_pages_exact": False,
                    "full_page_set_exact": False,
                    "error_type": "FN",
                }
            )
            continue

        score, index = max(candidates, key=lambda item: (item[0], -item[1]))
        a = auto[index]
        matched = score >= 0.5

        if matched:
            unmatched_auto.remove(index)

        primary_exact = g["primary_set"] == a["primary_set"]
        support_exact = g["support_set"] == a["support_set"]
        full_exact = (
            g["primary_set"] | g["support_set"]
        ) == (
            a["primary_set"] | a["support_set"]
        )

        if not matched:
            error_type = "FN_boundary"
        elif not primary_exact:
            error_type = "boundary_error"
        elif not support_exact:
            error_type = "supporting_error"
        else:
            error_type = ""

        results.append(
            {
                "gold_package_id": g["package_id"],
                "auto_package_id": a["package_id"] if matched else "",
                "event_family_gold": g["event_family"],
                "event_family_auto": a["event_family"] if matched else "",
                "gold_primary_pages": "|".join(map(str, sorted(g["primary_set"]))),
                "auto_primary_pages": "|".join(map(str, sorted(a["primary_set"]))) if matched else "",
                "gold_supporting_pages": "|".join(map(str, sorted(g["support_set"]))),
                "auto_supporting_pages": "|".join(map(str, sorted(a["support_set"]))) if matched else "",
                "primary_page_iou": round(score, 4),
                "event_match": matched,
                "type_correct": matched and a["event_family"] == g["event_family"],
                "primary_boundary_exact": matched and primary_exact,
                "supporting_pages_exact": matched and support_exact,
                "full_page_set_exact": matched and full_exact,
                "error_type": error_type,
            }
        )

    for index in sorted(unmatched_auto):
        a = auto[index]
        results.append(
            {
                "gold_package_id": "",
                "auto_package_id": a["package_id"],
                "event_family_gold": "",
                "event_family_auto": a["event_family"],
                "gold_primary_pages": "",
                "auto_primary_pages": "|".join(map(str, sorted(a["primary_set"]))),
                "gold_supporting_pages": "",
                "auto_supporting_pages": "|".join(map(str, sorted(a["support_set"]))),
                "primary_page_iou": 0.0,
                "event_match": False,
                "type_correct": False,
                "primary_boundary_exact": False,
                "supporting_pages_exact": False,
                "full_page_set_exact": False,
                "error_type": "FP",
            }
        )

    return results


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def summarize(results: list[dict], gold_count: int, auto_count: int) -> dict:
    tp = sum(bool(row["event_match"]) for row in results)
    fp = sum(row["error_type"] == "FP" for row in results)
    fn = gold_count - tp

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    matched = [row for row in results if row["event_match"]]
    return {
        "gold_packages": gold_count,
        "auto_packages": auto_count,
        "TP": tp,
        "FP": fp,
        "FN": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
        "type_accuracy": round(
            sum(bool(row["type_correct"]) for row in matched) / len(matched), 4
        ) if matched else 0.0,
        "primary_boundary_accuracy": round(
            sum(bool(row["primary_boundary_exact"]) for row in matched) / len(matched), 4
        ) if matched else 0.0,
        "supporting_pages_accuracy": round(
            sum(bool(row["supporting_pages_exact"]) for row in matched) / len(matched), 4
        ) if matched else 0.0,
        "full_page_set_accuracy": round(
            sum(bool(row["full_page_set_exact"]) for row in matched) / len(matched), 4
        ) if matched else 0.0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold", type=Path, required=True)
    parser.add_argument("--auto", type=Path, required=True)
    parser.add_argument("--detail-output", type=Path, required=True)
    parser.add_argument("--summary-output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    gold = load_gold(args.gold)
    auto = load_auto(args.auto)
    results = best_matches(gold, auto)
    summary = summarize(results, len(gold), len(auto))

    write_csv(args.detail_output, results)
    write_csv(args.summary_output, [summary])

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"DETAIL:  {args.detail_output}")
    print(f"SUMMARY: {args.summary_output}")


if __name__ == "__main__":
    main()
