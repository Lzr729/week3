#!/usr/bin/env python
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

INPUT = Path(r"evaluation\event_type_classification_71_review_state.csv")
OUTPUT = Path(r"evaluation\event_type_classification.csv")
SUMMARY = Path(r"evaluation\event_type_summary.csv")
MATRIX = Path(r"evaluation\event_type_confusion_matrix.csv")

REVIEWER = "lzr"  # 可改成你的姓名或学号

with INPUT.open("r", encoding="utf-8-sig", newline="") as f:
    rows = list(csv.DictReader(f))

# 运行本脚本代表：你已经检查高置信度记录，并同意将规则建议作为人工最终类型。
for row in rows:
    if row.get("confirmation_status") == "PENDING_BATCH_CONFIRMATION":
        row["human_event_type"] = row.get("suggested_human_type", "")
        row["type_match_after_confirmation"] = str(
            row.get("human_event_type") == row.get("rule_predicted_type")
        )
        row["reviewed_by"] = row.get("reviewed_by") or REVIEWER
        row["confirmation_status"] = "BATCH_CONFIRMED_BY_STUDENT"

fields = list(rows[0].keys())
with OUTPUT.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)

labels = sorted({
    row.get("human_event_type", "")
    for row in rows
    if row.get("human_event_type")
} | {
    row.get("rule_predicted_type", "")
    for row in rows
    if row.get("rule_predicted_type")
})

summary_rows = []
for label in labels:
    tp = sum(
        row["human_event_type"] == label
        and row["rule_predicted_type"] == label
        for row in rows
    )
    fp = sum(
        row["human_event_type"] != label
        and row["rule_predicted_type"] == label
        for row in rows
    )
    fn = sum(
        row["human_event_type"] == label
        and row["rule_predicted_type"] != label
        for row in rows
    )
    support = sum(row["human_event_type"] == label for row in rows)
    precision = tp / (tp + fp) if tp + fp else ""
    recall = tp / (tp + fn) if tp + fn else ""
    f1 = (
        2 * precision * recall / (precision + recall)
        if isinstance(precision, float)
        and isinstance(recall, float)
        and precision + recall
        else ""
    )
    summary_rows.append({
        "label": label,
        "human_support": support,
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    })

correct = sum(
    row["human_event_type"] == row["rule_predicted_type"]
    for row in rows
)
summary_rows.append({
    "label": "__OVERALL__",
    "human_support": len(rows),
    "true_positive": correct,
    "false_positive": len(rows) - correct,
    "false_negative": "",
    "precision": correct / len(rows) if rows else "",
    "recall": "",
    "f1": "",
})

with SUMMARY.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
    writer.writeheader()
    writer.writerows(summary_rows)

counts = defaultdict(int)
for row in rows:
    counts[(row["human_event_type"], row["rule_predicted_type"])] += 1

matrix_rows = []
for human_label in labels:
    matrix_row = {"human_label": human_label}
    for predicted_label in labels:
        matrix_row[predicted_label] = counts[
            (human_label, predicted_label)
        ]
    matrix_rows.append(matrix_row)

with MATRIX.open("w", encoding="utf-8-sig", newline="") as f:
    writer = csv.DictWriter(
        f,
        fieldnames=["human_label"] + labels,
    )
    writer.writeheader()
    writer.writerows(matrix_rows)

print(f"records={len(rows)}")
print(f"correct={correct}")
print(f"overall_accuracy={correct / len(rows):.4f}")
print(f"output={OUTPUT}")
print(f"summary={SUMMARY}")
print(f"matrix={MATRIX}")
