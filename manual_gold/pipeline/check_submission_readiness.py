#!/usr/bin/env python
from __future__ import annotations

import csv
from pathlib import Path


REQUIRED = [
    "README.md",
    "requirements.txt",
    "rule_coverage.md",
    "evaluation/error_analysis.md",
    "evaluation/coverage_audit.csv",
    "evaluation/coverage_summary.csv",
    "evaluation/final_week3_metrics.csv",
    "evaluation/numeric_cross_check.csv",
    "evaluation/row_match.csv",
    "evaluation/event_summary.csv",
    "manual_gold/gold_coverage.csv",
    "manual_gold/subscription_flow_gold.jsonl",
    "manual_gold/share_transfer_flow_gold.jsonl",
    "manual_gold/equity_snapshot_gold.jsonl",
    "manual_gold/annotation_index.csv",
    "manual_gold/cross_check_summary.csv",
    "outputs/validation/pydantic_validation_all.csv",
    "outputs/review_queue/week3_review_queue.csv",
    "prompts/system_prompt.md",
    "prompts/user_prompt_template.md",
    "prompts/prompt_variants.md",
    "prompts/ai_usage_statement.md",
    "schemas/subscription_flow.schema.json",
    "schemas/share_transfer_flow.schema.json",
    "schemas/equity_snapshot.schema.json",
    "pipeline/run_week3_batch_and_audit.py",
    "pipeline/finalize_week3_submission.py",
    "pipeline/validate_with_pydantic.py",
]


def csv_rows(path: Path):
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def main() -> int:
    missing = [path for path in REQUIRED if not Path(path).exists()]
    print(f"required_files={len(REQUIRED)}")
    print(f"missing_files={len(missing)}")
    for path in missing:
        print("MISSING", path)

    coverage_path = Path("manual_gold/gold_coverage.csv")
    pending = 0
    coverage_rows = 0
    if coverage_path.exists():
        rows = csv_rows(coverage_path)
        coverage_rows = len(rows)
        pending = sum(
            row.get("human_review_status") == "PENDING_HUMAN_REVIEW"
            for row in rows
        )
    print(f"gold_coverage_rows={coverage_rows}")
    print(f"gold_coverage_pending={pending}")

    manifest = Path("evaluation/submission_manifest.csv")
    manifest_missing = 0
    if manifest.exists():
        manifest_missing = sum(
            row.get("exists") != "True"
            for row in csv_rows(manifest)
        )
    print(f"manifest_missing={manifest_missing}")

    ready = not missing and pending == 0 and manifest_missing == 0
    print(f"submission_ready={ready}")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
