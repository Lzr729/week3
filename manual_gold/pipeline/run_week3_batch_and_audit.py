#!/usr/bin/env python
r"""
Run the three Week3 structured extraction modules for all eight companies,
validate outputs, generate a coverage audit, and build a manual review queue.

Run from the Week3 project root in VS Code PowerShell:

    python pipeline/run_week3_batch_and_audit.py

Outputs:
    evaluation/coverage_audit.csv
    evaluation/coverage_summary.csv
    outputs/review_queue/week3_review_queue.csv
    outputs/logs/week3_batch_run_log.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


COMPANIES = [
    ("603418", "友升股份"),
    ("001282", "三联锻造"),
    ("688758", "赛分科技"),
    ("920100", "三协电机"),
    ("920116", "星图测控"),
    ("301581", "黄山谷捷"),
    ("301563", "云汉芯城"),
    ("688775", "影石创新"),
]

MODULES = {
    "subscription_flow": {
        "script_candidates": [
            Path("pipeline/extract_subscription_flow_frozen_v1.py"),
            Path("pipeline/extract_subscription_flow_v1_5.py"),
        ],
        "schema": Path("schemas/subscription_flow.schema.json"),
        "output_suffix": "subscription_flow_frozen_v1",
    },
    "share_transfer_flow": {
        "script_candidates": [
            Path("pipeline/extract_share_transfer_flow_frozen_v1.py"),
            Path("pipeline/extract_share_transfer_flow_v1.py"),
        ],
        "schema": Path("schemas/share_transfer_flow.schema.json"),
        "output_suffix": "share_transfer_flow_frozen_v1",
    },
    "equity_snapshot": {
        "script_candidates": [
            Path("pipeline/extract_equity_snapshot_frozen_v1.py"),
            Path("pipeline/extract_equity_snapshot_v1.py"),
        ],
        "schema": Path("schemas/equity_snapshot.schema.json"),
        "output_suffix": "equity_snapshot_v1",
    },
}


@dataclass
class RunResult:
    command: str
    returncode: int
    stdout: str
    stderr: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Week3 extraction modules for all eight companies and audit coverage."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("."),
        help="Week3 project root. Default: current directory.",
    )
    parser.add_argument(
        "--regenerate-candidates",
        action="store_true",
        help="Regenerate frozen candidate-package files even when they already exist.",
    )
    return parser.parse_args()


def run_command(args: list[str], cwd: Path) -> RunResult:
    completed = subprocess.run(
        args,
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    command = subprocess.list2cmdline(args)
    return RunResult(
        command=command,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    rows: list[dict[str, Any]] = []
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
            else:
                rows.append(
                    {
                        "__parse_error__": (
                            f"line={line_number}; JSON value is not an object"
                        )
                    }
                )
    return rows


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


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def select_existing_script(root: Path, candidates: list[Path]) -> Path | None:
    for relative in candidates:
        if (root / relative).exists():
            return relative
    return None


def find_pdf(root: Path, company_code: str) -> Path | None:
    matches = sorted((root / "data/pdfs").glob(f"{company_code}*.pdf"))
    return matches[0] if matches else None


def candidate_file(root: Path, company_code: str) -> Path:
    return root / "outputs/candidates" / f"{company_code}_candidate_packages_frozen_v1.jsonl"


def candidate_log_file(root: Path, company_code: str) -> Path:
    return root / "outputs/logs" / f"{company_code}_candidate_package_log_frozen_v1.csv"


def ensure_candidate_packages(
    root: Path,
    company_code: str,
    pdf_path: Path,
    regenerate: bool,
) -> tuple[bool, RunResult | None]:
    output_path = candidate_file(root, company_code)
    log_path = candidate_log_file(root, company_code)

    if output_path.exists() and output_path.stat().st_size > 0 and not regenerate:
        return True, None

    script = root / "pipeline/extract_candidates_frozen_v1.py"
    if not script.exists():
        return False, RunResult(
            command="",
            returncode=98,
            stdout="",
            stderr=f"Missing script: {script}",
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    result = run_command(
        [
            sys.executable,
            str(script.relative_to(root)),
            "--company-code",
            company_code,
            "--pdf-path",
            str(pdf_path),
            "--output-jsonl",
            str(output_path.relative_to(root)),
            "--output-log",
            str(log_path.relative_to(root)),
        ],
        cwd=root,
    )

    success = (
        result.returncode == 0
        and output_path.exists()
        and output_path.stat().st_size > 0
    )
    return success, result


def validation_counts(rows: list[dict[str, str]]) -> tuple[int, int]:
    valid_lines: set[str] = set()
    invalid_lines: set[str] = set()

    for row in rows:
        source_line = row.get("source_line", "")
        status = row.get("status", "")
        if status == "INVALID":
            invalid_lines.add(source_line)
        elif status == "VALID":
            valid_lines.add(source_line)

    valid_lines -= invalid_lines
    return len(valid_lines), len(invalid_lines)


def determine_status(
    candidate_count: int,
    structured_count: int,
    error_count: int,
    review_count: int,
    schema_invalid_count: int,
    missing_package_count: int,
) -> str:
    if candidate_count == 0:
        return "NO_EVENT"
    if missing_package_count > 0:
        return "COVERAGE_GAP"
    if schema_invalid_count > 0 or error_count > 0 or review_count > 0:
        return "REVIEW_REQUIRED"
    if structured_count == candidate_count:
        return "EXTRACTED"
    return "COVERAGE_GAP"


def main() -> int:
    args = parse_args()
    root = args.project_root.resolve()

    required_common = [
        root / "pipeline/extract_candidates_frozen_v1.py",
        root / "pipeline/validate_structured_output.py",
    ]
    missing_common = [str(path) for path in required_common if not path.exists()]
    if missing_common:
        print("缺少必要脚本：")
        for item in missing_common:
            print(item)
        return 2

    selected_scripts: dict[str, Path] = {}
    for family, config in MODULES.items():
        selected = select_existing_script(root, config["script_candidates"])
        schema_path = root / config["schema"]
        if selected is None:
            print(f"缺少 {family} 抽取器。候选路径：")
            for candidate in config["script_candidates"]:
                print(f"  {candidate}")
            return 2
        if not schema_path.exists():
            print(f"缺少Schema：{schema_path}")
            return 2
        selected_scripts[family] = selected

    for relative in [
        "outputs/candidates",
        "outputs/structured",
        "outputs/logs",
        "outputs/validation",
        "outputs/review_queue",
        "evaluation",
    ]:
        (root / relative).mkdir(parents=True, exist_ok=True)

    audit_rows: list[dict[str, Any]] = []
    review_rows: list[dict[str, Any]] = []
    batch_log_rows: list[dict[str, Any]] = []

    for company_code, company_name in COMPANIES:
        print("=" * 80)
        print(f"{company_code} {company_name}")

        pdf_path = find_pdf(root, company_code)
        if pdf_path is None:
            print("PDF_MISSING")
            for family in MODULES:
                audit_rows.append(
                    {
                        "company_code": company_code,
                        "company_name": company_name,
                        "event_family": family,
                        "extractor_script": str(selected_scripts[family]),
                        "pdf_found": False,
                        "candidate_package_count": 0,
                        "structured_record_count": 0,
                        "error_count": 0,
                        "review_required_count": 0,
                        "schema_valid_count": 0,
                        "schema_invalid_count": 0,
                        "missing_package_count": 0,
                        "status": "PDF_MISSING",
                        "missing_package_ids": "",
                    }
                )
                review_rows.append(
                    {
                        "company_code": company_code,
                        "company_name": company_name,
                        "event_family": family,
                        "package_id": "",
                        "reason": "pdf_missing",
                        "priority": "HIGH",
                        "status": "PENDING",
                    }
                )
            continue

        candidates_ok, candidate_run = ensure_candidate_packages(
            root=root,
            company_code=company_code,
            pdf_path=pdf_path,
            regenerate=args.regenerate_candidates,
        )

        if candidate_run is not None:
            batch_log_rows.append(
                {
                    "company_code": company_code,
                    "event_family": "candidate_packages",
                    "stage": "extract",
                    "returncode": candidate_run.returncode,
                    "command": candidate_run.command,
                    "stdout": candidate_run.stdout.strip(),
                    "stderr": candidate_run.stderr.strip(),
                }
            )

        if not candidates_ok:
            print("CANDIDATE_GENERATION_FAILED")
            for family in MODULES:
                audit_rows.append(
                    {
                        "company_code": company_code,
                        "company_name": company_name,
                        "event_family": family,
                        "extractor_script": str(selected_scripts[family]),
                        "pdf_found": True,
                        "candidate_package_count": 0,
                        "structured_record_count": 0,
                        "error_count": 0,
                        "review_required_count": 0,
                        "schema_valid_count": 0,
                        "schema_invalid_count": 0,
                        "missing_package_count": 0,
                        "status": "CANDIDATE_GENERATION_FAILED",
                        "missing_package_ids": "",
                    }
                )
                review_rows.append(
                    {
                        "company_code": company_code,
                        "company_name": company_name,
                        "event_family": family,
                        "package_id": "",
                        "reason": "candidate_generation_failed",
                        "priority": "HIGH",
                        "status": "PENDING",
                    }
                )
            continue

        candidate_records = read_jsonl(candidate_file(root, company_code))

        for family, config in MODULES.items():
            selected_script = selected_scripts[family]
            suffix = config["output_suffix"]

            structured_path = (
                root / "outputs/structured" / f"{company_code}_{suffix}.jsonl"
            )
            log_path = root / "outputs/logs" / f"{company_code}_{suffix}_log.csv"
            validation_path = (
                root / "outputs/validation" / f"{company_code}_{suffix}_validation.csv"
            )

            run_result = run_command(
                [
                    sys.executable,
                    str(selected_script),
                    "--packages",
                    str(candidate_file(root, company_code).relative_to(root)),
                    "--pdf-path",
                    str(pdf_path),
                    "--output-jsonl",
                    str(structured_path.relative_to(root)),
                    "--output-log",
                    str(log_path.relative_to(root)),
                ],
                cwd=root,
            )

            batch_log_rows.append(
                {
                    "company_code": company_code,
                    "event_family": family,
                    "stage": "extract",
                    "returncode": run_result.returncode,
                    "command": run_result.command,
                    "stdout": run_result.stdout.strip(),
                    "stderr": run_result.stderr.strip(),
                }
            )

            # Return code 3 means no matching event packages and is not a failure.
            if run_result.returncode not in {0, 1, 3}:
                print(
                    f"{family}: extractor returncode={run_result.returncode}; "
                    "will be recorded in audit"
                )

            validation_result = run_command(
                [
                    sys.executable,
                    "pipeline/validate_structured_output.py",
                    "--schema",
                    str(config["schema"]),
                    "--input",
                    str(structured_path.relative_to(root)),
                    "--output",
                    str(validation_path.relative_to(root)),
                ],
                cwd=root,
            )

            batch_log_rows.append(
                {
                    "company_code": company_code,
                    "event_family": family,
                    "stage": "validate",
                    "returncode": validation_result.returncode,
                    "command": validation_result.command,
                    "stdout": validation_result.stdout.strip(),
                    "stderr": validation_result.stderr.strip(),
                }
            )

            family_candidates = [
                record
                for record in candidate_records
                if record.get("event_family") == family
            ]
            candidate_ids = {
                str(record.get("package_id") or "")
                for record in family_candidates
                if record.get("package_id")
            }

            structured_records = read_jsonl(structured_path)
            structured_ids = {
                str(record.get("package_id") or "")
                for record in structured_records
                if record.get("package_id") and "__parse_error__" not in record
            }

            log_rows = read_csv(log_path)
            error_rows = [
                row for row in log_rows if row.get("status") == "ERROR"
            ]
            error_ids = {
                row.get("package_id", "")
                for row in error_rows
                if row.get("package_id")
            }

            review_records = [
                record
                for record in structured_records
                if "__parse_error__" not in record
                and bool_value(record.get("needs_manual_review"))
            ]

            validation_rows = read_csv(validation_path)
            schema_valid_count, schema_invalid_count = validation_counts(
                validation_rows
            )

            covered_ids = structured_ids | error_ids
            missing_ids = sorted(candidate_ids - covered_ids)

            status = determine_status(
                candidate_count=len(family_candidates),
                structured_count=len(structured_records),
                error_count=len(error_rows),
                review_count=len(review_records),
                schema_invalid_count=schema_invalid_count,
                missing_package_count=len(missing_ids),
            )

            audit_rows.append(
                {
                    "company_code": company_code,
                    "company_name": company_name,
                    "event_family": family,
                    "extractor_script": str(selected_script),
                    "pdf_found": True,
                    "candidate_package_count": len(family_candidates),
                    "structured_record_count": len(structured_records),
                    "error_count": len(error_rows),
                    "review_required_count": len(review_records),
                    "schema_valid_count": schema_valid_count,
                    "schema_invalid_count": schema_invalid_count,
                    "missing_package_count": len(missing_ids),
                    "status": status,
                    "missing_package_ids": "|".join(missing_ids),
                }
            )

            for record in review_records:
                reasons = record.get("review_reasons")
                if isinstance(reasons, list):
                    reason_text = "|".join(str(item) for item in reasons)
                else:
                    reason_text = str(reasons or "needs_manual_review")
                review_rows.append(
                    {
                        "company_code": company_code,
                        "company_name": company_name,
                        "event_family": family,
                        "package_id": str(record.get("package_id") or ""),
                        "reason": reason_text,
                        "priority": "MEDIUM",
                        "status": "PENDING",
                    }
                )

            for row in error_rows:
                review_rows.append(
                    {
                        "company_code": company_code,
                        "company_name": company_name,
                        "event_family": family,
                        "package_id": row.get("package_id", ""),
                        "reason": row.get("review_reasons", "extractor_error"),
                        "priority": "HIGH",
                        "status": "PENDING",
                    }
                )

            for missing_id in missing_ids:
                review_rows.append(
                    {
                        "company_code": company_code,
                        "company_name": company_name,
                        "event_family": family,
                        "package_id": missing_id,
                        "reason": "candidate_package_has_no_structured_or_error_record",
                        "priority": "HIGH",
                        "status": "PENDING",
                    }
                )

            if schema_invalid_count > 0:
                invalid_record_ids = sorted(
                    {
                        row.get("record_id", "")
                        for row in validation_rows
                        if row.get("status") == "INVALID"
                    }
                )
                for record_id in invalid_record_ids:
                    review_rows.append(
                        {
                            "company_code": company_code,
                            "company_name": company_name,
                            "event_family": family,
                            "package_id": record_id,
                            "reason": "schema_invalid",
                            "priority": "HIGH",
                            "status": "PENDING",
                        }
                    )

            print(
                f"{family}: candidates={len(family_candidates)}, "
                f"structured={len(structured_records)}, "
                f"errors={len(error_rows)}, "
                f"review={len(review_records)}, "
                f"schema_invalid={schema_invalid_count}, "
                f"missing={len(missing_ids)}, "
                f"status={status}"
            )

    audit_fieldnames = [
        "company_code",
        "company_name",
        "event_family",
        "extractor_script",
        "pdf_found",
        "candidate_package_count",
        "structured_record_count",
        "error_count",
        "review_required_count",
        "schema_valid_count",
        "schema_invalid_count",
        "missing_package_count",
        "status",
        "missing_package_ids",
    ]
    write_csv(
        root / "evaluation/coverage_audit.csv",
        audit_rows,
        audit_fieldnames,
    )

    review_fieldnames = [
        "company_code",
        "company_name",
        "event_family",
        "package_id",
        "reason",
        "priority",
        "status",
    ]
    write_csv(
        root / "outputs/review_queue/week3_review_queue.csv",
        review_rows,
        review_fieldnames,
    )

    batch_log_fieldnames = [
        "company_code",
        "event_family",
        "stage",
        "returncode",
        "command",
        "stdout",
        "stderr",
    ]
    write_csv(
        root / "outputs/logs/week3_batch_run_log.csv",
        batch_log_rows,
        batch_log_fieldnames,
    )

    summary_rows: list[dict[str, Any]] = []
    for company_code, company_name in COMPANIES:
        company_rows = [
            row for row in audit_rows if row["company_code"] == company_code
        ]
        summary_rows.append(
            {
                "company_code": company_code,
                "company_name": company_name,
                "module_rows": len(company_rows),
                "candidate_packages_total": sum(
                    int(row["candidate_package_count"]) for row in company_rows
                ),
                "structured_records_total": sum(
                    int(row["structured_record_count"]) for row in company_rows
                ),
                "errors_total": sum(int(row["error_count"]) for row in company_rows),
                "review_required_total": sum(
                    int(row["review_required_count"]) for row in company_rows
                ),
                "schema_invalid_total": sum(
                    int(row["schema_invalid_count"]) for row in company_rows
                ),
                "missing_packages_total": sum(
                    int(row["missing_package_count"]) for row in company_rows
                ),
                "company_coverage_complete": all(
                    row["status"]
                    not in {
                        "PDF_MISSING",
                        "CANDIDATE_GENERATION_FAILED",
                        "COVERAGE_GAP",
                    }
                    for row in company_rows
                )
                and len(company_rows) == len(MODULES),
            }
        )

    summary_fieldnames = [
        "company_code",
        "company_name",
        "module_rows",
        "candidate_packages_total",
        "structured_records_total",
        "errors_total",
        "review_required_total",
        "schema_invalid_total",
        "missing_packages_total",
        "company_coverage_complete",
    ]
    write_csv(
        root / "evaluation/coverage_summary.csv",
        summary_rows,
        summary_fieldnames,
    )

    complete_companies = sum(
        bool_value(row["company_coverage_complete"]) for row in summary_rows
    )
    coverage_gaps = sum(
        row["status"]
        in {
            "PDF_MISSING",
            "CANDIDATE_GENERATION_FAILED",
            "COVERAGE_GAP",
        }
        for row in audit_rows
    )
    missing_package_total = sum(
        int(row["missing_package_count"]) for row in audit_rows
    )

    print("=" * 80)
    print("BATCH COMPLETE")
    print(f"companies_complete={complete_companies}/{len(COMPANIES)}")
    print(f"module_rows={len(audit_rows)}")
    print(f"coverage_gap_rows={coverage_gaps}")
    print(f"missing_package_total={missing_package_total}")
    print(f"review_queue_records={len(review_rows)}")
    print("coverage_audit=evaluation/coverage_audit.csv")
    print("coverage_summary=evaluation/coverage_summary.csv")
    print("review_queue=outputs/review_queue/week3_review_queue.csv")
    print("batch_log=outputs/logs/week3_batch_run_log.csv")

    return 0 if coverage_gaps == 0 and missing_package_total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
