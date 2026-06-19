#!/usr/bin/env python
r"""
Rule-based extractor for IPO share_transfer_flow event packages.

PowerShell example:
    python pipeline/extract_share_transfer_flow_v1.py `
      --packages outputs/candidates/301581_candidate_packages_frozen_v1.jsonl `
      --pdf-path "data/pdfs/301581_黄山谷捷_IPO招股说明书.pdf" `
      --package-id 301581_share_transfer_flow_001 `
      --output-jsonl outputs/structured/301581_share_transfer_flow_v1.jsonl `
      --output-log outputs/logs/301581_share_transfer_flow_v1_log.csv
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

NUMBER = r"(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
DATE_RE = re.compile(
    r"(?P<year>19\d{2}|20\d{2})\s*年\s*"
    r"(?P<month>1[0-2]|0?[1-9])\s*月\s*"
    r"(?P<day>3[01]|[12]\d|0?[1-9])\s*日"
)

TRANSFER_SENTENCE_RE = re.compile(
    rf"(?:同意)?(?P<transferor>[^，。；]{{2,45}}?)将其所持"
    rf"(?P<target>[^，。；%]{{2,35}}?)"
    rf"(?P<ratios>{NUMBER}%(?:、{NUMBER}%)*)的股权"
    rf"以(?P<consideration>零对价|0元|[^，。；]{{1,30}}?)"
    rf"分别转让给(?P<transferees>[^。；]{{2,100}})"
)

SINGLE_TRANSFER_RE = re.compile(
    rf"(?P<transferor>[^，。；]{{2,45}}?)将其(?:持有的|所持)"
    rf"(?P<target>[^，。；]{{2,35}}?)(?:的)?"
    rf"(?P<quantity>{NUMBER})\s*(?P<quantity_unit>万股|股|万元|元注册资本)?"
    rf"(?:股份|股权|注册资本)?(?:以|作价)"
    rf"(?P<consideration>{NUMBER})\s*(?P<consideration_unit>亿元|万元|元)"
    rf"(?:的价格)?转让(?:予|给)(?P<transferee>[^，。；]{{2,45}})"
)

POST_TOTAL_RE = re.compile(
    rf"合\s*计(?P<capital>{NUMBER})(?P<ratio>100(?:\.00)?)"
)

ZERO_PRICE_RE = re.compile(r"(?:转让价格|股权转让价格)(?:为)?\s*0\s*元|零对价")


@dataclass
class Segment:
    page: int
    text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract share_transfer_flow records.")
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
        for line_number, raw_line in enumerate(file, 1):
            if not raw_line.strip():
                continue
            try:
                row = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise SystemExit(
                    f"JSONL第{line_number}行解析失败，第{exc.colno}列：{exc.msg}"
                ) from exc
            if not isinstance(row, dict):
                raise SystemExit(f"JSONL第{line_number}行不是对象。")
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
    for key in ("all_pages", "primary_pages"):
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


def package_segments(package: dict[str, Any]) -> list[Segment]:
    raw = package.get("source_segments")
    if not isinstance(raw, list):
        return []
    result: list[Segment] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            page = int(item.get("page"))
        except (TypeError, ValueError):
            continue
        text = item.get("text")
        if isinstance(text, str) and text.strip():
            result.append(Segment(page=page, text=text))
    return sorted(result, key=lambda item: item.page)


def pdf_segments(pdf_path: Path, pages: list[int]) -> list[Segment]:
    if fitz is None:
        raise RuntimeError("缺少PyMuPDF，请运行：python -m pip install pymupdf")
    if not pdf_path.exists():
        raise RuntimeError(f"PDF不存在：{pdf_path}")
    result: list[Segment] = []
    with fitz.open(pdf_path) as document:
        for page in pages:
            index = page - 1
            if 0 <= index < document.page_count:
                text = document.load_page(index).get_text("text")
                if text.strip():
                    result.append(Segment(page=page, text=text))
    return result


def get_segments(package: dict[str, Any], pdf_path: Optional[Path]) -> list[Segment]:
    segments = package_segments(package)
    if segments:
        return segments
    if pdf_path is None:
        raise RuntimeError("候选包缺少source_segments，必须提供--pdf-path。")
    return pdf_segments(pdf_path, package_pages(package))


def compact(text: str) -> str:
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    return re.sub(r"\s+", "", text)


def parse_number(raw: str) -> float:
    return float(raw.replace(",", ""))


def numeric(raw_number: str, unit: str) -> dict[str, Any]:
    value = parse_number(raw_number)
    factors = {
        "元": (1, "CNY"),
        "万元": (10_000, "CNY"),
        "亿元": (100_000_000, "CNY"),
        "股": (1, "share"),
        "万股": (10_000, "share"),
        "%": (1, "percent"),
        "元/股": (1, "CNY_per_share"),
        "元/注册资本": (1, "CNY_per_registered_capital"),
    }
    factor, normalized_unit = factors.get(unit, (1, "other"))
    normalized = value * factor
    if value.is_integer():
        value = int(value)
    if float(normalized).is_integer():
        normalized = int(normalized)
    return {
        "raw_text": f"{raw_number}{unit}",
        "value": value,
        "unit": unit,
        "normalized_value": normalized,
        "normalized_unit": normalized_unit,
    }


def date_object(match: re.Match[str]) -> dict[str, str]:
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    return {
        "raw_text": match.group(0),
        "iso_date": f"{year:04d}-{month:02d}-{day:02d}",
    }


def date_before_phrase(text: str, phrases: tuple[str, ...]) -> Optional[dict[str, str]]:
    candidates: list[tuple[int, re.Match[str]]] = []
    for date_match in DATE_RE.finditer(text):
        right = text[date_match.end(): min(len(text), date_match.end() + 130)]
        positions = [right.find(phrase) for phrase in phrases if phrase in right]
        if positions:
            candidates.append((min(positions), date_match))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1].start()))
    return date_object(candidates[0][1])


def page_for_text(segments: list[tuple[int, str]], text: str) -> int:
    needle = text[:35]
    for page, segment_text in segments:
        if needle and needle in segment_text:
            return page
    return segments[0][0] if segments else 1


def add_evidence(
    evidence: list[dict[str, Any]],
    segments: list[tuple[int, str]],
    role: str,
    text: str,
) -> None:
    if not text:
        return
    evidence.append(
        {
            "page": page_for_text(segments, text),
            "evidence_role": role,
            "text": text[:500],
        }
    )


def read_alias_map(pdf_path: Optional[Path], max_pages: int = 25) -> dict[str, str]:
    if pdf_path is None or fitz is None or not pdf_path.exists():
        return {}
    aliases: dict[str, str] = {}
    with fitz.open(pdf_path) as document:
        for index in range(min(max_pages, document.page_count)):
            lines = [line.strip() for line in document.load_page(index).get_text("text").splitlines()]
            for pos in range(len(lines) - 2):
                alias = lines[pos]
                marker = lines[pos + 1]
                definition = lines[pos + 2]
                if marker != "指" or not alias or not definition:
                    continue
                alias = re.sub(r"\s+", "", alias)
                definition = re.sub(r"\s+", "", definition)
                full_name = re.split(r"[，,]", definition, maxsplit=1)[0]
                if 1 < len(alias) <= 40 and 2 < len(full_name) <= 80:
                    aliases[alias] = full_name
    return aliases


def expand_alias(name: str, aliases: dict[str, str]) -> str:
    clean = name.strip(" ，。；:：")
    return aliases.get(clean, clean)


def split_names(text: str) -> list[str]:
    text = re.sub(r"(?:以及|并|和|及)", "、", text)
    names = [item.strip(" ，。；:：") for item in text.split("、")]
    return [name for name in names if name]


def detect_target_is_issuer(
    package: dict[str, Any], target_alias: str, aliases: dict[str, str], text: str
) -> tuple[bool, str]:
    if package.get("issuer_bound") is True:
        return True, "chapter_context"
    definition = aliases.get(target_alias, "")
    if "发行人前身" in definition or "发行人" in definition:
        return True, "issuer_alias"
    if "本次股权转让后" in text and target_alias in text:
        return True, "post_transfer_snapshot"
    return False, "manual_confirmation"


def infer_total_capital(text: str) -> Optional[dict[str, Any]]:
    match = POST_TOTAL_RE.search(text)
    if not match:
        return None
    return numeric(match.group("capital"), "万元")


def zero_money() -> dict[str, Any]:
    return {
        "raw_text": "0元",
        "value": 0,
        "unit": "元",
        "normalized_value": 0,
        "normalized_unit": "CNY",
    }


def detect_subtype(text: str) -> str:
    if "同一股权结构下的公司架构调整" in text or "间接持股变更为直接持股" in text:
        return "internal_restructuring"
    if "代持" in text and any(word in text for word in ("解除", "还原", "清理")):
        return "nominee_release"
    if "赠与" in text or "继承" in text:
        return "gift_or_inheritance"
    if "司法" in text or "拍卖" in text:
        return "judicial_transfer"
    if "合伙份额" in text or "财产份额" in text:
        return "employee_platform_internal"
    return "ordinary_transfer"


def extract_multi_transfer(
    match: re.Match[str],
    text: str,
    aliases: dict[str, str],
) -> tuple[list[dict[str, Any]], str, str, Optional[dict[str, Any]], Optional[dict[str, Any]]]:
    transferor_alias = match.group("transferor").strip(" ，。；:：")
    target_alias = match.group("target").strip(" ，。；:：")
    ratios = [parse_number(value) for value in re.findall(NUMBER, match.group("ratios"))]
    transferees = split_names(match.group("transferees"))
    total_capital = infer_total_capital(text)
    zero = bool(ZERO_PRICE_RE.search(match.group(0)) or "零对价" in match.group("consideration"))

    transfers: list[dict[str, Any]] = []
    for index, transferee in enumerate(transferees):
        ratio = ratios[index] if index < len(ratios) else None
        capital = None
        if ratio is not None and total_capital is not None:
            amount = total_capital["normalized_value"] * ratio / 100
            amount_wan = amount / 10_000
            raw = f"{amount_wan:.4f}".rstrip("0").rstrip(".")
            capital = numeric(raw, "万元")

        transfers.append(
            {
                "transferor_name": expand_alias(transferor_alias, aliases),
                "transferee_name": transferee,
                "transferred_registered_capital": capital,
                "transferred_shares": None,
                "transferred_ratio": numeric(str(ratio), "%") if ratio is not None else None,
                "consideration": zero_money() if zero else None,
                "price_per_share": None,
                "price_per_registered_capital": None,
                "payment_method": "zero_consideration" if zero else "cash",
                "notes": None,
            }
        )

    total_ratio = sum(ratios) if ratios else None
    total_ratio_value = numeric(str(total_ratio), "%") if total_ratio is not None else None
    total_consideration = zero_money() if zero else None
    return transfers, transferor_alias, target_alias, total_capital, total_ratio_value


def extract_single_transfers(text: str, aliases: dict[str, str]) -> list[dict[str, Any]]:
    transfers: list[dict[str, Any]] = []
    for match in SINGLE_TRANSFER_RE.finditer(text):
        quantity = match.group("quantity")
        unit = match.group("quantity_unit")
        registered_capital = numeric(quantity, "万元") if unit in ("万元", "元注册资本") else None
        shares = numeric(quantity, unit) if unit in ("万股", "股") else None
        transfers.append(
            {
                "transferor_name": expand_alias(match.group("transferor"), aliases),
                "transferee_name": expand_alias(match.group("transferee"), aliases),
                "transferred_registered_capital": registered_capital,
                "transferred_shares": shares,
                "transferred_ratio": None,
                "consideration": numeric(match.group("consideration"), match.group("consideration_unit")),
                "price_per_share": None,
                "price_per_registered_capital": None,
                "payment_method": "cash",
                "notes": None,
            }
        )
    return transfers


def sum_numeric(items: list[dict[str, Any]], field: str, unit: str) -> Optional[dict[str, Any]]:
    values = [item.get(field) for item in items]
    valid = [value for value in values if isinstance(value, dict) and value.get("normalized_value") is not None]
    if not valid or len(valid) != len(items):
        return None
    normalized = sum(float(value["normalized_value"]) for value in valid)
    if unit == "万元":
        raw_value = normalized / 10_000
    elif unit == "万股":
        raw_value = normalized / 10_000
    elif unit == "%":
        raw_value = normalized
    elif unit == "元":
        raw_value = normalized
    else:
        raw_value = normalized
    raw = f"{raw_value:.6f}".rstrip("0").rstrip(".")
    return numeric(raw, unit)


def extract_record(
    package: dict[str, Any],
    segments: list[Segment],
    aliases: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    compact_segments = [(segment.page, compact(segment.text)) for segment in segments]
    text = "".join(segment_text for _, segment_text in compact_segments)
    review: list[str] = []
    evidence: list[dict[str, Any]] = []

    multi_match = TRANSFER_SENTENCE_RE.search(text)
    target_alias = ""
    transferor_alias = ""
    total_capital = None
    total_ratio = None

    if multi_match:
        transfers, transferor_alias, target_alias, total_capital, total_ratio = extract_multi_transfer(
            multi_match, text, aliases
        )
        add_evidence(evidence, compact_segments, "target_confirmation", multi_match.group(0))
        add_evidence(evidence, compact_segments, "transferor", transferor_alias)
        add_evidence(evidence, compact_segments, "transferee", multi_match.group("transferees"))
        add_evidence(evidence, compact_segments, "ratio", multi_match.group("ratios"))
    else:
        transfers = extract_single_transfers(text, aliases)
        if not transfers:
            review.append("transfer_items_not_found")
        target_alias = str(package.get("company_name") or "")

    decision_date = date_before_phrase(text, ("股东决定", "股东会", "股东大会", "审议通过", "作出决议"))
    agreement_date = date_before_phrase(text, ("签署", "签订", "股权转让协议", "转让协议"))
    if agreement_date is None and decision_date and "同日" in text and "签署" in text:
        agreement_date = dict(decision_date)
    registration_date = date_before_phrase(text, ("换发的营业执照", "换发营业执照", "工商变更登记", "办理完成工商"))

    if decision_date:
        add_evidence(evidence, compact_segments, "decision", decision_date["raw_text"])
    else:
        review.append("decision_date_not_found")
    if agreement_date:
        add_evidence(evidence, compact_segments, "agreement", agreement_date["raw_text"])
    else:
        review.append("agreement_date_not_found")
    if registration_date:
        add_evidence(evidence, compact_segments, "registration", registration_date["raw_text"])

    target_is_issuer, confirmation_method = detect_target_is_issuer(
        package, target_alias, aliases, text
    )
    target_name = expand_alias(target_alias, aliases) if target_alias else str(package.get("company_name") or "")
    if not target_is_issuer:
        review.append("transfer_target_not_confirmed_as_issuer")

    total_registered = total_capital or sum_numeric(transfers, "transferred_registered_capital", "万元")
    total_shares = sum_numeric(transfers, "transferred_shares", "万股")
    total_ratio_value = total_ratio or sum_numeric(transfers, "transferred_ratio", "%")
    total_consideration = sum_numeric(transfers, "consideration", "元")

    pre_pages = [page for page, page_text in compact_segments if "转让前后" in page_text or "转让前" in page_text]
    post_pages = [page for page, page_text in compact_segments if "本次股权转让后" in page_text or "转让后" in page_text]

    if not transfers:
        review.append("transferor_or_transferee_not_found")
    for item in transfers:
        if item["transferred_registered_capital"] is None and item["transferred_shares"] is None and item["transferred_ratio"] is None:
            review.append("transferred_quantity_not_found")
        if item["consideration"] is None:
            review.append("consideration_not_found")

    confirmation_text = multi_match.group(0) if multi_match else text[:300]
    if not evidence:
        add_evidence(evidence, compact_segments, "other", text[:300])

    package_id = str(package.get("package_id") or "")
    record = {
        "schema_version": "1.0",
        "company_code": str(package.get("company_code") or ""),
        "company_name": str(package.get("company_name") or ""),
        "package_id": package_id,
        "event_id": f"{package_id}_evt_001",
        "event_family": "share_transfer_flow",
        "event_title": str(package.get("event_title") or "share_transfer_flow"),
        "transfer_subtype": detect_subtype(text),
        "decision_date": decision_date,
        "agreement_date": agreement_date,
        "payment_date": None,
        "registration_date": registration_date,
        "effective_date": registration_date,
        "target_company_name": target_name,
        "target_security_type": "equity_interest" if "股权" in text else "shares",
        "target_is_issuer": target_is_issuer,
        "target_confirmation": {
            "raw_text": confirmation_text[:500],
            "confirmed_by": confirmation_method,
        },
        "source_pages": package_pages(package),
        "evidence": evidence,
        "transfers": transfers,
        "total_transferred_registered_capital": total_registered,
        "total_transferred_shares": total_shares,
        "total_transferred_ratio": total_ratio_value,
        "total_consideration": total_consideration,
        "pre_event_snapshot_present": bool(pre_pages),
        "pre_event_snapshot_pages": unique_pages(pre_pages),
        "post_event_snapshot_present": bool(post_pages),
        "post_event_snapshot_pages": unique_pages(post_pages),
        "currency": "CNY",
        "notes": "V1规则抽取结果；后续需进行Schema校验、字段级Gold比较和人工复核。",
        "needs_manual_review": bool(review),
        "review_reasons": sorted(set(review)),
    }
    return record, sorted(set(review))


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
        "transfer_count",
        "target_is_issuer",
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
        row
        for row in packages
        if row.get("event_family") == "share_transfer_flow"
        and (not args.package_id or row.get("package_id") == args.package_id)
        and (not args.company_code or str(row.get("company_code")) == args.company_code)
    ]

    aliases = read_alias_map(args.pdf_path)
    output: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []

    for package in selected:
        package_id = str(package.get("package_id") or "")
        try:
            segments = get_segments(package, args.pdf_path)
            record, reasons = extract_record(package, segments, aliases)
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
                    "transfer_count": len(record["transfers"]),
                    "target_is_issuer": record["target_is_issuer"],
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
                    "transfer_count": 0,
                    "target_is_issuer": False,
                    "evidence_count": 0,
                }
            )

    write_jsonl(args.output_jsonl, output)
    write_log(args.output_log, logs)

    errors = sum(row["status"] == "ERROR" for row in logs)
    reviews = sum(bool(row.get("needs_manual_review")) for row in output)
    print(f"packages_input={len(packages)}")
    print(f"share_transfer_packages={len(selected)}")
    print(f"records_output={len(output)}")
    print(f"needs_manual_review={reviews}")
    print(f"errors={errors}")
    print(f"output_jsonl={args.output_jsonl}")
    print(f"output_log={args.output_log}")

    if not selected:
        print("没有找到符合条件的share_transfer_flow事件包。", file=sys.stderr)
        return 3
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
