#!/usr/bin/env python
"""
Validate JSON or JSONL structured extraction records against a JSON Schema.

Example:
    python pipeline\validate_structured_output.py ^
        --schema schemas\subscription_flow.schema.json ^
        --input manual_gold\fields\subscription_flow\001282_subscription_flow_gold.jsonl ^
        --output outputs\validation\001282_subscription_flow_gold_validation.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Iterable

try:
    from jsonschema import Draft202012Validator
except ImportError as exc:
    raise SystemExit(
        "缺少依赖 jsonschema。请先运行：python -m pip install jsonschema"
    ) from exc


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate JSON/JSONL records against a Draft 2020-12 JSON Schema."
    )
    parser.add_argument(
        "--schema",
        required=True,
        type=Path,
        help="JSON Schema 文件路径。",
    )
    parser.add_argument(
        "--input",
        required=True,
        type=Path,
        help="待校验的 .json 或 .jsonl 文件路径。",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="校验明细 CSV 输出路径。",
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8-sig") as file:
            return json.load(file)
    except FileNotFoundError as exc:
        raise SystemExit(f"文件不存在：{path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"JSON 解析失败：{path}，第 {exc.lineno} 行，第 {exc.colno} 列：{exc.msg}"
        ) from exc


def iter_records(path: Path) -> Iterable[tuple[int, Any]]:
    """
    Yield (source_line, record).

    .jsonl: one JSON object per non-empty line.
    .json: one object, or an array of objects.
    """
    if not path.exists():
        raise SystemExit(f"文件不存在：{path}")

    suffix = path.suffix.lower()

    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8-sig") as file:
            for line_number, raw_line in enumerate(file, start=1):
                text = raw_line.strip()
                if not text:
                    continue
                try:
                    yield line_number, json.loads(text)
                except json.JSONDecodeError as exc:
                    yield line_number, {
                        "__parse_error__": (
                            f"第 {line_number} 行 JSON 解析失败，"
                            f"第 {exc.colno} 列：{exc.msg}"
                        )
                    }
        return

    if suffix == ".json":
        data = load_json(path)
        if isinstance(data, list):
            for index, record in enumerate(data, start=1):
                yield index, record
        else:
            yield 1, data
        return

    raise SystemExit("输入文件必须是 .json 或 .jsonl。")


def format_json_path(path_parts: Iterable[Any]) -> str:
    parts = list(path_parts)
    if not parts:
        return "$"

    result = "$"
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        else:
            result += f".{part}"
    return result


def record_identifier(record: Any) -> str:
    if not isinstance(record, dict):
        return ""
    return str(
        record.get("event_id")
        or record.get("package_id")
        or record.get("company_code")
        or ""
    )


def validate_records(
    validator: Draft202012Validator,
    input_path: Path,
) -> tuple[list[dict[str, Any]], int, int]:
    rows: list[dict[str, Any]] = []
    total_records = 0
    invalid_records = 0

    for source_line, record in iter_records(input_path):
        total_records += 1
        identifier = record_identifier(record)

        if isinstance(record, dict) and "__parse_error__" in record:
            invalid_records += 1
            rows.append(
                {
                    "source_line": source_line,
                    "record_id": identifier,
                    "status": "INVALID",
                    "json_path": "$",
                    "validator": "json_parse",
                    "message": record["__parse_error__"],
                }
            )
            continue

        errors = sorted(
            validator.iter_errors(record),
            key=lambda error: list(error.absolute_path),
        )

        if not errors:
            rows.append(
                {
                    "source_line": source_line,
                    "record_id": identifier,
                    "status": "VALID",
                    "json_path": "",
                    "validator": "",
                    "message": "",
                }
            )
            continue

        invalid_records += 1
        for error in errors:
            rows.append(
                {
                    "source_line": source_line,
                    "record_id": identifier,
                    "status": "INVALID",
                    "json_path": format_json_path(error.absolute_path),
                    "validator": error.validator or "",
                    "message": error.message,
                }
            )

    return rows, total_records, invalid_records


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "source_line",
        "record_id",
        "status",
        "json_path",
        "validator",
        "message",
    ]

    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()

    schema = load_json(args.schema)

    try:
        Draft202012Validator.check_schema(schema)
    except Exception as exc:
        print(f"Schema 本身不合法：{exc}", file=sys.stderr)
        return 2

    validator = Draft202012Validator(schema)
    rows, total_records, invalid_records = validate_records(
        validator=validator,
        input_path=args.input,
    )
    write_csv(args.output, rows)

    valid_records = total_records - invalid_records

    print(f"schema={args.schema}")
    print(f"input={args.input}")
    print(f"output={args.output}")
    print(f"records={total_records}")
    print(f"valid={valid_records}")
    print(f"invalid={invalid_records}")

    if total_records == 0:
        print("警告：输入文件中没有可校验记录。", file=sys.stderr)
        return 3

    return 0 if invalid_records == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
