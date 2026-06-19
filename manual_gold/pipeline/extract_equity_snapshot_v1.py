#!/usr/bin/env python
r"""
Baseline extractor for equity_snapshot candidate packages.

PowerShell example:
    python pipeline/extract_equity_snapshot_v1.py `
      --packages outputs/candidates/603418_candidate_packages_frozen_v1.jsonl `
      --pdf-path "$pdf" `
      --output-jsonl outputs/structured/603418_equity_snapshot_v1.jsonl `
      --output-log outputs/logs/603418_equity_snapshot_v1_log.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    import fitz
except ImportError:
    fitz = None


DATE_RE = re.compile(
    r"(?P<year>19\d{2}|20\d{2})\s*年\s*"
    r"(?P<month>1[0-2]|0?[1-9])\s*月\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])\s*日"
)
NUMBER_RE = re.compile(r"^-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?$")
RATIO_RE = re.compile(r"^-?(?:\d+(?:\.\d+)?)\s*%?$")
INDEX_RE = re.compile(r"^\d{1,3}$")


@dataclass
class Segment:
    page: int
    text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract equity_snapshot records from frozen candidate packages."
    )
    parser.add_argument("--packages", required=True, type=Path)
    parser.add_argument("--pdf-path", type=Path)
    parser.add_argument("--output-jsonl", required=True, type=Path)
    parser.add_argument("--output-log", required=True, type=Path)
    parser.add_argument("--package-id")
    parser.add_argument("--company-code")
    return parser.parse_args()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise SystemExit(f"文件不存在：{path}")
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, raw in enumerate(file, start=1):
            text = raw.strip()
            if not text:
                continue
            try:
                row = json.loads(text)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"{path} 第{line_number}行JSON解析失败，第{exc.colno}列：{exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise SystemExit(f"{path} 第{line_number}行不是JSON对象。")
            rows.append(row)
    return rows


def unique_pages(values: Iterable[Any]) -> list[int]:
    output: list[int] = []
    seen: set[int] = set()
    for value in values:
        try:
            page = int(value)
        except (TypeError, ValueError):
            continue
        if page > 0 and page not in seen:
            seen.add(page)
            output.append(page)
    return output


def package_pages(package: dict[str, Any]) -> list[int]:
    for key in ("all_pages", "primary_pages", "source_pages"):
        value = package.get(key)
        if isinstance(value, list):
            pages = unique_pages(value)
            if pages:
                return pages
        if isinstance(value, str):
            pages = unique_pages(re.split(r"[|,，;\s]+", value))
            if pages:
                return pages
    try:
        start = int(package["start_page"])
        end = int(package["end_page"])
    except (KeyError, TypeError, ValueError):
        return []
    return list(range(start, end + 1))


def segments_from_package(package: dict[str, Any]) -> list[Segment]:
    raw = package.get("source_segments")
    if not isinstance(raw, list):
        return []
    result: list[Segment] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        text = item.get("text")
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        if isinstance(text, str) and text.strip():
            result.append(Segment(page=page, text=text))
    return sorted(result, key=lambda x: x.page)


def segments_from_pdf(pdf_path: Path, pages: list[int]) -> list[Segment]:
    if fitz is None:
        raise RuntimeError("缺少PyMuPDF，请运行：python -m pip install pymupdf")
    if not pdf_path.exists():
        raise RuntimeError(f"PDF不存在：{pdf_path}")
    result: list[Segment] = []
    document = fitz.open(pdf_path)
    try:
        for page in pages:
            index = page - 1
            if 0 <= index < document.page_count:
                text = document.load_page(index).get_text("text")
                if text.strip():
                    result.append(Segment(page=page, text=text))
    finally:
        document.close()
    return result


def get_segments(
    package: dict[str, Any],
    pdf_path: Optional[Path],
) -> list[Segment]:
    segments = segments_from_package(package)
    if segments:
        return segments
    if pdf_path is None:
        raise RuntimeError("候选包缺少source_segments，必须提供--pdf-path。")
    return segments_from_pdf(pdf_path, package_pages(package))


def clean_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.replace("\u3000", " ")).strip()


def parse_number(text: str) -> float:
    return float(text.replace(",", "").replace("%", "").strip())


def numeric_value(raw_text: str, unit: str) -> dict[str, Any]:
    value = parse_number(raw_text)
    if unit == "万元":
        normalized = value * 10_000
        normalized_unit = "CNY"
    elif unit == "元":
        normalized = value
        normalized_unit = "CNY"
    elif unit == "万股":
        normalized = value * 10_000
        normalized_unit = "share"
    elif unit == "股":
        normalized = value
        normalized_unit = "share"
    elif unit == "%":
        normalized = value
        normalized_unit = "percent"
    else:
        normalized = value
        normalized_unit = "other"

    if float(value).is_integer():
        value = int(value)
    if float(normalized).is_integer():
        normalized = int(normalized)

    return {
        "raw_text": f"{raw_text}{unit}" if not raw_text.endswith(unit) else raw_text,
        "value": value,
        "unit": unit,
        "normalized_value": normalized,
        "normalized_unit": normalized_unit,
    }


def date_value(match: re.Match[str]) -> dict[str, str]:
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    return {
        "raw_text": match.group(0),
        "iso_date": f"{year:04d}-{month:02d}-{day:02d}",
    }


def detect_snapshot_date(text: str, title: str) -> Optional[dict[str, str]]:
    preferred = re.search(
        r"(?:截至|截止|于)\s*"
        + DATE_RE.pattern,
        text,
    )
    if preferred:
        date_match = DATE_RE.search(preferred.group(0))
        if date_match:
            return date_value(date_match)

    for source in (title, text):
        match = DATE_RE.search(source)
        if match:
            return date_value(match)
    return None


def detect_snapshot_type(title: str, text: str) -> str:
    merged = title + text
    if "发行前后" in merged or "本次发行前" in merged:
        return "pre_post_ipo_structure"
    if "股权结构图" in merged or "股权结构及组织结构" in merged:
        return "ownership_chart"
    if any(word in merged for word in ("本次变更后", "增资后", "转让后", "设立后")):
        return "post_event_structure"
    if any(word in merged for word in ("截至", "目前", "当前")):
        return "current_shareholding"
    if any(word in merged for word in ("历史沿革", "历次股权结构")):
        return "historical_shareholding"
    return "other"


def table_mode(lines: list[str]) -> tuple[Optional[str], Optional[str]]:
    joined = "|".join(lines)
    amount_mode = None
    share_type = None

    if "持股数量（股）" in joined or "持股数量(股)" in joined:
        amount_mode = "股"
        share_type = "common_share"
    elif "持股数量（万股）" in joined or "持股数量(万股)" in joined:
        amount_mode = "万股"
        share_type = "common_share"
    elif "出资额（万元）" in joined or "出资额(万元)" in joined:
        amount_mode = "万元"
        share_type = "equity_interest"
    elif "出资额" in joined and "出资比例" in joined:
        amount_mode = "万元"
        share_type = "equity_interest"

    return amount_mode, share_type


def valid_holder_name(text: str) -> bool:
    if not text or NUMBER_RE.match(text) or RATIO_RE.match(text):
        return False
    blocked = (
        "序号", "股东姓名", "股东名称", "持股数量", "持股比例",
        "出资额", "出资比例", "合计", "总计", "招股说明书",
        "股权结构", "股份性质"
    )
    return not any(word in text for word in blocked)


def parse_table_rows(
    segments: list[Segment],
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]], Optional[dict[str, Any]], list[int]]:
    holders: list[dict[str, Any]] = []
    total_amount: Optional[dict[str, Any]] = None
    total_ratio: Optional[dict[str, Any]] = None
    table_pages: list[int] = []

    for segment in segments:
        lines = [clean_line(line) for line in segment.text.splitlines()]
        lines = [line for line in lines if line]
        amount_unit, share_type = table_mode(lines)
        if not amount_unit:
            continue

        page_had_row = False
        i = 0
        while i < len(lines):
            line = lines[i]

            if line in ("合计", "总计", "合 计"):
                if i + 2 < len(lines):
                    amount_text = lines[i + 1]
                    ratio_text = lines[i + 2]
                    if NUMBER_RE.match(amount_text) and RATIO_RE.match(ratio_text):
                        total_amount = numeric_value(amount_text, amount_unit)
                        total_ratio = numeric_value(ratio_text.replace("%", ""), "%")
                i += 1
                continue

            if INDEX_RE.match(line) and i + 3 < len(lines):
                name = lines[i + 1]
                amount_text = lines[i + 2]
                ratio_text = lines[i + 3]

                if (
                    valid_holder_name(name)
                    and NUMBER_RE.match(amount_text)
                    and RATIO_RE.match(ratio_text)
                ):
                    amount_value = numeric_value(amount_text, amount_unit)
                    ratio_value = numeric_value(ratio_text.replace("%", ""), "%")

                    holder = {
                        "holder_name": name,
                        "direct_holder": True,
                        "registered_capital": (
                            amount_value if amount_unit in ("元", "万元") else None
                        ),
                        "shares": (
                            amount_value if amount_unit in ("股", "万股") else None
                        ),
                        "ownership_ratio": ratio_value,
                        "share_type": share_type,
                        "notes": None,
                    }
                    holders.append(holder)
                    page_had_row = True
                    i += 4
                    continue
            i += 1

        if page_had_row:
            table_pages.append(segment.page)

    # De-duplicate exact repeated rows caused by repeated table headers/pages.
    unique: list[dict[str, Any]] = []
    seen: set[tuple[str, Any, Any]] = set()
    for holder in holders:
        amount = holder["shares"] or holder["registered_capital"]
        amount_norm = amount.get("normalized_value") if isinstance(amount, dict) else None
        ratio_norm = holder["ownership_ratio"].get("normalized_value")
        key = (holder["holder_name"], amount_norm, ratio_norm)
        if key not in seen:
            seen.add(key)
            unique.append(holder)

    return unique, total_amount, total_ratio, unique_pages(table_pages)


def detect_issuer_confirmation(
    package: dict[str, Any],
    text: str,
) -> tuple[bool, dict[str, str]]:
    company_name = str(package.get("company_name") or "").strip()
    title = str(package.get("event_title") or "").strip()

    if company_name and company_name in text:
        return True, {
            "raw_text": f"正文出现发行人名称：{company_name}",
            "confirmed_by": "explicit_issuer_name",
        }

    if any(
        phrase in text
        for phrase in (
            "公司的股权结构如下",
            "发行人的股权结构如下",
            "公司股本结构如下",
            "本次变更完成后",
        )
    ):
        return True, {
            "raw_text": "正文通过公司/发行人股权结构语句确认主体。",
            "confirmed_by": "chapter_context",
        }

    if title:
        return True, {
            "raw_text": f"候选事件标题：{title}",
            "confirmed_by": "chapter_context",
        }

    return False, {
        "raw_text": "未找到足够的发行人主体确认文字。",
        "confirmed_by": "manual_confirmation",
    }


def extract_one(
    package: dict[str, Any],
    segments: list[Segment],
) -> tuple[dict[str, Any], list[str]]:
    pages = package_pages(package)
    title = str(package.get("event_title") or "equity_snapshot")
    full_text = "\n".join(segment.text for segment in segments)

    holders, total_amount, total_ratio, table_pages = parse_table_rows(segments)
    issuer_confirmed, issuer_confirmation = detect_issuer_confirmation(package, full_text)
    snapshot_type = detect_snapshot_type(title, full_text)
    snapshot_date = detect_snapshot_date(full_text, title)

    review_reasons: list[str] = []
    evidence: list[dict[str, Any]] = []

    if holders:
        evidence.append(
            {
                "page": table_pages[0] if table_pages else pages[0],
                "evidence_role": "holder_table",
                "text": f"识别到股东/出资人结构表，共{len(holders)}行。",
            }
        )
    else:
        review_reasons.append("holder_table_not_parsed")
        evidence.append(
            {
                "page": pages[0],
                "evidence_role": "other",
                "text": full_text[:500] or "候选页面未提取到文本。",
            }
        )

    if snapshot_date:
        evidence.append(
            {
                "page": pages[0],
                "evidence_role": "snapshot_date",
                "text": snapshot_date["raw_text"],
            }
        )
    else:
        review_reasons.append("snapshot_date_not_found")

    evidence.append(
        {
            "page": pages[0],
            "evidence_role": "issuer_confirmation",
            "text": issuer_confirmation["raw_text"],
        }
    )

    total_registered_capital = None
    total_shares = None
    if total_amount:
        if total_amount["normalized_unit"] == "CNY":
            total_registered_capital = total_amount
        elif total_amount["normalized_unit"] == "share":
            total_shares = total_amount

    if total_ratio is None and holders:
        ratio_sum = sum(
            float(holder["ownership_ratio"]["normalized_value"])
            for holder in holders
            if holder.get("ownership_ratio")
        )
        total_ratio = {
            "raw_text": None,
            "value": None,
            "unit": None,
            "normalized_value": ratio_sum,
            "normalized_unit": "percent",
        }
        review_reasons.append("total_ratio_inferred_by_sum")

    if not issuer_confirmed:
        review_reasons.append("issuer_not_confirmed")

    if total_ratio and abs(float(total_ratio["normalized_value"]) - 100.0) > 0.05:
        review_reasons.append("total_ratio_not_100")

    package_id = str(package.get("package_id") or "")
    record = {
        "schema_version": "1.0",
        "company_code": str(package.get("company_code") or ""),
        "company_name": str(package.get("company_name") or ""),
        "package_id": package_id,
        "event_id": f"{package_id}_evt_001",
        "event_family": "equity_snapshot",
        "event_title": title,
        "snapshot_type": snapshot_type,
        "snapshot_date": snapshot_date,
        "source_pages": pages,
        "evidence": evidence,
        "issuer_confirmed": issuer_confirmed,
        "issuer_confirmation": issuer_confirmation,
        "holders": holders,
        "total_registered_capital": total_registered_capital,
        "total_shares": total_shares,
        "total_ratio": total_ratio,
        "currency": "CNY",
        "notes": "V1基础抽取结果；无法稳定解析的表格进入人工复核队列，不静默丢失。",
        "needs_manual_review": bool(review_reasons),
        "review_reasons": sorted(set(review_reasons)),
    }
    return record, record["review_reasons"]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_log(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "company_code",
        "package_id",
        "event_title",
        "status",
        "needs_manual_review",
        "review_reasons",
        "source_pages",
        "holder_count",
        "issuer_confirmed",
        "evidence_count",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    packages = load_jsonl(args.packages)

    selected = [
        package
        for package in packages
        if package.get("event_family") == "equity_snapshot"
        and (not args.package_id or package.get("package_id") == args.package_id)
        and (
            not args.company_code
            or str(package.get("company_code")) == args.company_code
        )
    ]

    output: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []

    for package in selected:
        package_id = str(package.get("package_id") or "")
        try:
            segments = get_segments(package, args.pdf_path)
            record, reasons = extract_one(package, segments)
            output.append(record)
            logs.append(
                {
                    "company_code": record["company_code"],
                    "package_id": package_id,
                    "event_title": record["event_title"],
                    "status": "EXTRACTED",
                    "needs_manual_review": record["needs_manual_review"],
                    "review_reasons": "|".join(reasons),
                    "source_pages": "|".join(map(str, record["source_pages"])),
                    "holder_count": len(record["holders"]),
                    "issuer_confirmed": record["issuer_confirmed"],
                    "evidence_count": len(record["evidence"]),
                }
            )
        except Exception as exc:
            logs.append(
                {
                    "company_code": str(package.get("company_code") or ""),
                    "package_id": package_id,
                    "event_title": str(package.get("event_title") or ""),
                    "status": "ERROR",
                    "needs_manual_review": True,
                    "review_reasons": f"{type(exc).__name__}: {exc}",
                    "source_pages": "|".join(map(str, package_pages(package))),
                    "holder_count": 0,
                    "issuer_confirmed": False,
                    "evidence_count": 0,
                }
            )

    write_jsonl(args.output_jsonl, output)
    write_log(args.output_log, logs)

    errors = sum(row["status"] == "ERROR" for row in logs)
    reviews = sum(bool(row.get("needs_manual_review")) for row in output)

    print(f"packages_input={len(packages)}")
    print(f"equity_snapshot_packages={len(selected)}")
    print(f"records_output={len(output)}")
    print(f"needs_manual_review={reviews}")
    print(f"errors={errors}")
    print(f"output_jsonl={args.output_jsonl}")
    print(f"output_log={args.output_log}")

    if not selected:
        print("没有找到equity_snapshot事件包。", file=sys.stderr)
        return 3
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
