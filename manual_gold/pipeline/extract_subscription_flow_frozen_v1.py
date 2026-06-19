#!/usr/bin/env python
r"""
Extract subscription_flow fields from frozen candidate packages.

PowerShell example:
    python pipeline/extract_subscription_flow_v1_5.py `
      --packages outputs/candidates/001282_candidate_packages_frozen_v1.jsonl `
      --pdf-path "data/pdfs/001282_三联锻造_IPO招股说明书.pdf" `
      --package-id 001282_subscription_flow_001 `
      --output-jsonl outputs/structured/001282_subscription_flow_v1_3.jsonl `
      --output-log outputs/logs/001282_subscription_flow_v1_3_log.csv
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

CAPITAL_CHANGE_PATTERNS = [
    re.compile(
        rf"注册资本(?:由|从)\s*(?P<before>{NUMBER})\s*(?P<before_unit>亿元|万元|元)"
        rf"\s*(?:增加至|增至|增加到|变更为)\s*(?P<after>{NUMBER})\s*(?P<after_unit>亿元|万元|元)"
    ),
    re.compile(
        rf"注册资本\s*(?P<before>{NUMBER})\s*(?P<before_unit>亿元|万元|元)"
        rf"\s*(?:增加至|增至|变更为)\s*(?P<after>{NUMBER})\s*(?P<after_unit>亿元|万元|元)"
    ),
]

SHARE_CHANGE_PATTERNS = [
    re.compile(
        rf"(?:总股本|股本)(?:由|从)\s*(?P<before>{NUMBER})\s*(?P<before_unit>亿股|万股|股)"
        rf"\s*(?:增加至|增至|增加到|变更为)\s*(?P<after>{NUMBER})\s*(?P<after_unit>亿股|万股|股)"
    ),
    re.compile(
        rf"(?:增资前|发行前).{{0,30}}?(?P<before>{NUMBER})\s*(?P<before_unit>亿股|万股|股)"
        rf".{{0,160}}?(?:增资后|发行后).{{0,30}}?(?P<after>{NUMBER})\s*(?P<after_unit>亿股|万股|股)"
    ),
]

NEW_CAPITAL_PATTERNS = [
    re.compile(rf"新增注册资本(?:为|合计|共计)?\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)"),
    re.compile(rf"增加注册资本(?:为|合计|共计)?\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)"),
]

NEW_SHARE_PATTERNS = [
    re.compile(
        rf"(?:共)?发行(?:普通股|股份|股票|新股)?(?:数量|股数)?(?:为|共计|合计)?"
        rf"\s*(?P<value>{NUMBER})\s*(?P<unit>亿股|万股|股)"
    ),
    re.compile(
        rf"(?:增发|新增)(?:股份|股票|股本)?(?:数量为|为|合计|共计)?"
        rf"\s*(?P<value>{NUMBER})\s*(?P<unit>亿股|万股|股)"
    ),
    re.compile(rf"认购新增(?:股份|股票|股本)?\s*(?P<value>{NUMBER})\s*(?P<unit>亿股|万股|股)"),
]

CONSIDERATION_PATTERNS = [
    re.compile(
        rf"(?:募集资金(?:总额)?|实际募集资金总额)(?:为)?"
        rf"\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)"
    ),
    re.compile(rf"(?:出资|认购金额|认购价款|投资款)(?:合计|总额|共计|为)?\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)"),
    re.compile(rf"以(?:人民币)?\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)\s*(?:认购|出资|增资)"),
    re.compile(
        rf"以(?:人民币)?(?:货币|现金)(?:形式)?\s*"
        rf"(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)\s*(?:认缴|认购|出资)"
    ),
]

CAPITAL_INCREASE_TO_PATTERNS = [
    re.compile(
        rf"注册资本(?:增加|新增)\s*(?P<new>{NUMBER})\s*(?P<new_unit>亿元|万元|元)"
        rf"\s*(?:至|到)\s*(?P<after>{NUMBER})\s*(?P<after_unit>亿元|万元|元)"
    ),
]

AFTER_CAPITAL_PATTERNS = [
    re.compile(
        rf"(?:本次)?(?:股票)?发行完成后(?:公司)?注册资本(?:为|增至|增加至)"
        rf"\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)"
    ),
    re.compile(
        rf"(?:本次)?增资完成后(?:公司)?注册资本(?:为|增至|增加至)"
        rf"\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)"
    ),
]

NEW_CAPITAL_CREDIT_PATTERNS = [
    re.compile(
        rf"其中\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)"
        rf"\s*计入(?:注册资本|股本)"
    ),
    re.compile(
        rf"(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)"
        rf"\s*计入(?:注册资本|股本)"
    ),
]

PREMIUM_PATTERNS = [
    re.compile(rf"其中\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)\s*(?:计入|增加)资本公积"),
    re.compile(rf"(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)\s*(?:计入|增加)资本公积"),
]

PRICE_SHARE_PATTERNS = [
    re.compile(rf"每股\s*(?P<value>{NUMBER})\s*元(?:\s*/\s*股)?"),
    re.compile(rf"(?:发行价格为|认购价格为)\s*(?P<value>{NUMBER})\s*元(?:\s*/\s*股)?"),
    re.compile(rf"以\s*(?P<value>{NUMBER})\s*元\s*/\s*股(?:的价格)?"),
]

PRICE_CAPITAL_PATTERNS = [
    re.compile(rf"(?:每|以)\s*(?P<value>{NUMBER})\s*元\s*/\s*(?:1元)?注册资本"),
    re.compile(rf"价格(?:为)?\s*(?P<value>{NUMBER})\s*元\s*/\s*注册资本"),
]

PARTICIPANT_PATTERNS = [
    re.compile(
        rf"新增注册资本(?:为)?\s*{NUMBER}\s*(?:亿元|万元|元)"
        r"(?:全部)?由(?:新股东)?(?P<name>[^，。；]{2,80}?)"
        r"以(?:货币|现金)(?:形式)?认缴"
    ),
    re.compile(r"新增注册资本由(?:新股东)?(?P<name>[^，。；]{2,80}?)认购"),
    re.compile(r"由(?:新股东)?(?P<name>[^，。；]{2,80}?)以[^，。；]{0,80}?认购"),
    re.compile(r"(?P<name>[^，。；]{2,80}?)以每股[^，。；]{0,30}?元(?:/股)?(?:的价格)?认购"),
    re.compile(r"(?P<name>[^，。；]{2,80}?)认缴新增注册资本"),
]

DECISION_KEYWORDS = ("股东大会", "股东会", "董事会", "决议", "审议通过")
PAYMENT_KEYWORDS = ("截至", "收到", "缴纳", "出资到账", "实缴")
VERIFICATION_KEYWORDS = ("验资", "验资报告")
REGISTRATION_KEYWORDS = ("工商", "市场监督管理", "变更登记", "营业执照", "备案登记")
AGREEMENT_KEYWORDS = ("增资协议", "认购协议", "投资协议")

LEGAL_SUFFIX_RE = re.compile(
    r"(?:有限责任公司|股份有限公司|有限公司|合伙企业（有限合伙）|合伙企业\(有限合伙\)|中心（有限合伙）|中心\(有限合伙\)|企业（有限合伙）|企业\(有限合伙\))"
)


@dataclass
class Segment:
    page: int
    text: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extract subscription_flow records.")
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


def get_pages(package: dict[str, Any]) -> list[int]:
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


def segments_from_package(package: dict[str, Any]) -> list[Segment]:
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
            result.append(Segment(page, text))
    return result


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
                    result.append(Segment(page, text))
    finally:
        document.close()
    return result


def get_segments(package: dict[str, Any], pdf_path: Optional[Path]) -> list[Segment]:
    # PDF文本保留表格行结构，优先用于字段抽取；候选包文本作为无PDF时的回退。
    if pdf_path is not None:
        return segments_from_pdf(pdf_path, get_pages(package))
    result = segments_from_package(package)
    if result:
        return sorted(result, key=lambda item: item.page)
    raise RuntimeError("候选包缺少source_segments，且未提供--pdf-path。")


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
        "亿股": (100_000_000, "share"),
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


def inferred_numeric(normalized_value: float, normalized_unit: str) -> dict[str, Any]:
    if float(normalized_value).is_integer():
        normalized_value = int(normalized_value)
    return {
        "raw_text": None,
        "value": None,
        "unit": None,
        "normalized_value": normalized_value,
        "normalized_unit": normalized_unit,
    }


def find_change(
    text: str,
    patterns: list[re.Pattern[str]],
) -> tuple[Optional[dict[str, Any]], Optional[dict[str, Any]], Optional[re.Match[str]]]:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return (
                numeric(match.group("before"), match.group("before_unit")),
                numeric(match.group("after"), match.group("after_unit")),
                match,
            )
    return None, None, None


def find_numeric(
    text: str,
    patterns: list[re.Pattern[str]],
    forced_unit: Optional[str] = None,
) -> tuple[Optional[dict[str, Any]], Optional[re.Match[str]]]:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            unit = forced_unit or match.group("unit")
            return numeric(match.group("value"), unit), match
    return None, None


def date_object(match: re.Match[str]) -> dict[str, str]:
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    return {
        "raw_text": match.group(0),
        "iso_date": f"{year:04d}-{month:02d}-{day:02d}",
    }


def find_date(
    text: str,
    keywords: tuple[str, ...],
    prefer_before_keyword: bool = False,
) -> Optional[dict[str, str]]:
    candidates: list[tuple[int, int, int, re.Match[str]]] = []

    for keyword in keywords:
        for keyword_match in re.finditer(re.escape(keyword), text):
            left = max(0, keyword_match.start() - 180)
            right = min(len(text), keyword_match.end() + 180)
            for date_match in DATE_RE.finditer(text, left, right):
                if date_match.end() <= keyword_match.start():
                    direction_rank = 0 if prefer_before_keyword else 1
                    distance = keyword_match.start() - date_match.end()
                else:
                    direction_rank = 1 if prefer_before_keyword else 0
                    distance = date_match.start() - keyword_match.end()
                candidates.append(
                    (direction_rank, abs(distance), date_match.start(), date_match)
                )

    if not candidates:
        return None

    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    return date_object(candidates[0][3])


def excerpt(text: str, match: Optional[re.Match[str]], radius: int = 80) -> str:
    if match is None:
        return text[:300]
    return text[max(0, match.start() - radius): min(len(text), match.end() + radius)]


def page_for_text(segments: list[tuple[int, str]], text: str) -> int:
    needle = text[:35]
    for page, segment_text in segments:
        if needle and needle in segment_text:
            return page
    return segments[0][0]


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


def clean_name(name: str) -> str:
    name = re.sub(r"^(?:新股东|股东|投资者)", "", name)
    name = re.split(r"(?:以每股|以每|以人民币|认购价格|的价格)", name, maxsplit=1)[0]
    return name.strip(" ，。；:：")


def extract_alias_data_from_pdf(
    pdf_path: Optional[Path],
    max_pages: int = 20,
) -> tuple[dict[str, str], dict[str, str]]:
    if pdf_path is None or fitz is None or not pdf_path.exists():
        return {}, {}

    aliases: dict[str, str] = {}
    role_hints: dict[str, str] = {}
    document = fitz.open(pdf_path)
    try:
        for page_index in range(min(max_pages, document.page_count)):
            lines = [
                line.strip()
                for line in document.load_page(page_index).get_text("text").splitlines()
                if line.strip()
            ]
            for index, line in enumerate(lines):
                if line != "指" or index == 0 or index + 1 >= len(lines):
                    continue
                alias = lines[index - 1].strip(" ，。；:：")
                description = ""
                suffix_match = None
                for next_index in range(index + 1, min(index + 4, len(lines))):
                    description += lines[next_index]
                    suffix_match = LEGAL_SUFFIX_RE.search(description)
                    if suffix_match is not None:
                        break
                if not alias or suffix_match is None:
                    continue
                full_name = description[:suffix_match.end()].strip(" ，。；:：")
                if 1 < len(alias) <= 30 and len(full_name) >= len(alias):
                    aliases[alias] = full_name
                    if "员工持股平台" in description or "持股平台" in description:
                        role_hints[full_name] = "employee_platform"
                    elif "控股股东" in description or "发行人股东" in description:
                        role_hints[full_name] = "existing_shareholder"
                    elif any(
                        word in full_name
                        for word in ("投资基金", "创业投资基金", "股权投资中心", "股权投资基金")
                    ):
                        role_hints[full_name] = "institutional_investor"
    finally:
        document.close()
    return aliases, role_hints


def participant_names(text: str, alias_map: dict[str, str]) -> list[str]:
    output: list[str] = []
    for pattern in PARTICIPANT_PATTERNS:
        for match in pattern.finditer(text):
            name = clean_name(match.group("name"))
            if not name or len(name) > 80:
                continue
            if any(
                phrase in name
                for phrase in (
                    "公司召开",
                    "股东大会",
                    "注册资本",
                    "审议通过",
                    "验资报告",
                    "每股",
                    "的价格",
                )
            ):
                continue
            name = alias_map.get(name, name)
            if name not in output:
                output.append(name)
    return output[:20]


def find_date_followed_by(
    text: str,
    keywords: tuple[str, ...],
    max_distance: int = 140,
) -> Optional[dict[str, str]]:
    candidates: list[tuple[int, int, re.Match[str]]] = []
    for date_match in DATE_RE.finditer(text):
        window = text[date_match.end(): date_match.end() + max_distance]
        positions = [window.find(keyword) for keyword in keywords if keyword in window]
        if positions:
            candidates.append((min(positions), date_match.start(), date_match))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return date_object(candidates[0][2])



def package_page_sets(package: dict[str, Any]) -> tuple[set[int], set[int]]:
    primary = package.get("primary_pages") or []
    supporting = package.get("supporting_pages") or []
    if isinstance(primary, str):
        primary = re.split(r"[|,，;\s]+", primary)
    if isinstance(supporting, str):
        supporting = re.split(r"[|,，;\s]+", supporting)
    return set(unique_pages(primary)), set(unique_pages(supporting))


def crop_primary_text(package: dict[str, Any], segments: list[Segment]) -> str:
    primary_pages, supporting_pages = package_page_sets(package)
    selected = [
        compact(item.text)
        for item in segments
        if not primary_pages or item.page in primary_pages or item.page not in supporting_pages
    ]
    text = "".join(selected)
    start_anchor = compact(str(package.get("start_anchor") or ""))
    end_anchor = compact(str(package.get("end_anchor") or ""))
    if start_anchor:
        start_index = text.find(start_anchor)
        if start_index >= 0:
            text = text[start_index:]
    if end_anchor:
        end_index = text.find(end_anchor)
        if end_index > 0:
            text = text[:end_index]
    return text


def crop_supporting_text(package: dict[str, Any], segments: list[Segment]) -> str:
    _, supporting_pages = package_page_sets(package)
    if not supporting_pages:
        return ""

    title = str(package.get("event_title") or "")
    year_match = re.search(r"(19\d{2}|20\d{2})年", title)
    event_year = int(year_match.group(1)) if year_match else None
    output: list[str] = []

    for item in segments:
        if item.page not in supporting_pages:
            continue
        text = compact(item.text)

        start = 0
        if event_year is not None:
            candidates: list[int] = []
            for match in re.finditer(fr"{event_year}年", text):
                window = text[match.start(): match.start() + 240]
                if any(
                    keyword in window
                    for keyword in ("股票发行", "定向发行", "增资", "募集资金", "发行股数")
                ):
                    candidates.append(match.start())
            if candidates:
                start = min(candidates)

        cropped = text[start:]

        # Stop before a later capital event on the same supporting page.
        stop_candidates: list[int] = []
        if event_year is not None:
            for match in re.finditer(r"(19\d{2}|20\d{2})年", cropped):
                year = int(match.group(1))
                if match.start() == 0:
                    continue
                window = cropped[match.start(): match.start() + 220]
                if (
                    year > event_year
                    and any(
                        keyword in window
                        for keyword in ("股票发行", "定向发行", "增资", "权益分派", "转增")
                    )
                ):
                    stop_candidates.append(match.start())
                elif (
                    year == event_year
                    and any(keyword in window for keyword in ("权益分派", "资本公积转增"))
                ):
                    stop_candidates.append(match.start())
        if stop_candidates:
            cropped = cropped[:min(stop_candidates)]

        output.append(cropped)

    return "".join(output)


def extraction_text(package: dict[str, Any], segments: list[Segment]) -> str:
    return crop_primary_text(package, segments) + crop_supporting_text(package, segments)


def find_decision_date(text: str) -> Optional[dict[str, str]]:
    priority_groups = (
        ("召开股东大会", "召开临时股东大会"),
        ("股东大会审议通过", "股东会审议通过", "股东会并作出决议"),
        ("召开股东会", "作出决议"),
        ("召开董事会", "董事会审议通过"),
        ("审议通过",),
    )
    for group in priority_groups:
        value = find_date_followed_by(text, group, max_distance=180)
        if value is not None:
            return value
    return find_date(text, DECISION_KEYWORDS)


def find_effective_date(text: str) -> Optional[dict[str, str]]:
    return find_date_followed_by(
        text,
        ("在股转系统挂牌并公开转让", "挂牌并公开转让", "挂牌转让", "新增股份于"),
        max_distance=180,
    ) or find_date(
        text,
        ("挂牌并公开转让", "挂牌转让", "新增股份"),
        prefer_before_keyword=True,
    )


def find_date_before_regex(
    text: str,
    following_pattern: str,
    max_distance: int = 220,
) -> Optional[dict[str, str]]:
    """Return the nearest date immediately before a regex-described action."""
    candidates: list[tuple[int, int, re.Match[str]]] = []
    action_re = re.compile(following_pattern)
    for date_match in DATE_RE.finditer(text):
        window = text[date_match.end(): date_match.end() + max_distance]
        action_match = action_re.search(window)
        if action_match is not None:
            candidates.append((action_match.start(), date_match.start(), date_match))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return date_object(candidates[0][2])


def find_merger_agreement_date(text: str) -> Optional[dict[str, str]]:
    return (
        find_date_before_regex(
            text,
            r"(?:同日，?)?[^。；]{0,90}?再次签订《(?:吸收)?合并协议》",
            max_distance=260,
        )
        or find_date_before_regex(
            text,
            r"(?:同日，?)?[^。；]{0,90}?签订《吸收合并协议》",
            max_distance=220,
        )
        or find_date_before_regex(
            text,
            r"(?:同日，?)?[^。；]{0,90}?签订《合并协议》",
            max_distance=220,
        )
    )


def find_merger_registration_date(text: str) -> Optional[dict[str, str]]:
    return find_date_before_regex(
        text,
        r"[^。；]{0,120}?(?:取得|领取)[^。；]{0,100}?(?:换发的)?(?:《)?营业执照",
        max_distance=260,
    )


def find_merger_capital_values(
    text: str,
) -> tuple[
    Optional[dict[str, Any]],
    Optional[dict[str, Any]],
    Optional[dict[str, Any]],
    Optional[re.Match[str]],
]:
    after_patterns = (
        re.compile(
            rf"吸收合并后[^。；]{{0,100}}?注册资本(?:变更为|为|增至)"
            rf"\s*(?P<after>{NUMBER})\s*(?P<after_unit>亿元|万元|元)"
        ),
        re.compile(
            rf"合并后[^。；]{{0,100}}?注册资本(?:变更为|为|增至)"
            rf"\s*(?P<after>{NUMBER})\s*(?P<after_unit>亿元|万元|元)"
        ),
    )
    new_patterns = (
        re.compile(
            rf"新增实收资本(?:为)?\s*(?P<new>{NUMBER})\s*(?P<new_unit>亿元|万元|元)"
        ),
        re.compile(
            rf"新增注册资本(?:为)?\s*(?P<new>{NUMBER})\s*(?P<new_unit>亿元|万元|元)"
        ),
    )

    after_match = next((pattern.search(text) for pattern in after_patterns if pattern.search(text)), None)
    new_match = next((pattern.search(text) for pattern in new_patterns if pattern.search(text)), None)

    after = (
        numeric(after_match.group("after"), after_match.group("after_unit"))
        if after_match is not None
        else None
    )
    new = (
        numeric(new_match.group("new"), new_match.group("new_unit"))
        if new_match is not None
        else None
    )
    before = None
    if after is not None and new is not None:
        before = inferred_numeric(
            after["normalized_value"] - new["normalized_value"],
            "CNY",
        )
    return before, after, new, after_match or new_match


def find_merged_entity_name(
    text: str,
    alias_map: dict[str, str],
) -> Optional[str]:
    patterns = (
        re.compile(r"吸收合并(?P<name>[^，。；]{2,50}?)，合并后"),
        re.compile(r"被合并方(?P<name>[^，。；的]{2,50}?)的账面净资产"),
        re.compile(r"与(?P<name>[^，。；]{2,50}?)再次签订《吸收合并协议》"),
    )
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        name = clean_name(match.group("name"))
        if name:
            return alias_map.get(name, name)
    return None


def find_merged_entity_net_assets(
    text: str,
    merged_entity_name: Optional[str],
    alias_map: dict[str, str],
) -> Optional[dict[str, Any]]:
    candidate_names: list[str] = []
    if merged_entity_name:
        candidate_names.append(merged_entity_name)
        candidate_names.extend(
            alias for alias, full_name in alias_map.items()
            if full_name == merged_entity_name
        )
    candidate_names = sorted(set(candidate_names), key=len, reverse=True)

    for name in candidate_names:
        match = re.search(
            rf"{re.escape(name)}净资产账面价值为"
            rf"\s*(?P<value>{NUMBER})\s*(?P<unit>亿元|万元|元)",
            text,
        )
        if match is not None:
            return numeric(match.group("value"), match.group("unit"))
    return None


def merger_snapshot_pages(segments: list[tuple[int, str]]) -> list[int]:
    for page, page_text in segments:
        if (
            "吸收合并前" in page_text
            and "吸收合并后" in page_text
            and ("股东名称" in page_text or "合计" in page_text)
        ):
            return [page]
    return []


def payment_participant_names(
    text: str,
    alias_map: dict[str, str],
) -> list[str]:
    patterns = (
        re.compile(r"收到(?P<names>[^。；]{2,160}?)缴纳的出资款"),
        re.compile(r"由(?P<names>[^。；]{2,160}?)缴纳(?:的)?出资款"),
        re.compile(r"向(?P<names>[^。；]{2,160}?)定向发行股票"),
    )
    output: list[str] = []
    for pattern in patterns:
        match = pattern.search(text)
        if match is None:
            continue
        raw_names = match.group("names")
        raw_names = re.sub(r"^(?:公司已|公司|发行人已|发行人)", "", raw_names)
        for name in re.split(r"[、，,和及与]+", raw_names):
            cleaned = clean_name(name)
            if not cleaned or len(cleaned) > 80:
                continue
            full_name = alias_map.get(cleaned, cleaned)
            if full_name not in output:
                output.append(full_name)
        if output:
            break
    return output


def raw_lines(segments: list[Segment]) -> list[tuple[int, str]]:
    output: list[tuple[int, str]] = []
    for segment in segments:
        for line in segment.text.splitlines():
            cleaned = line.strip()
            if cleaned:
                output.append((segment.page, cleaned))
    return output


def is_number_line(text: str) -> bool:
    return re.fullmatch(NUMBER, text.replace(" ", "")) is not None


def parse_subscription_table(
    segments: list[Segment],
    alias_map: dict[str, str],
    role_hints: dict[str, str],
) -> tuple[list[dict[str, Any]], Optional[dict[str, Any]]]:
    lines = raw_lines(segments)
    marker_index = next(
        (index for index, (_, line) in enumerate(lines) if "本次增资具体情况如下" in line),
        None,
    )
    if marker_index is None:
        return [], None

    header_end = next(
        (
            index
            for index in range(marker_index + 1, min(marker_index + 30, len(lines)))
            if "出资方式" in lines[index][1]
        ),
        None,
    )
    if header_end is None:
        return [], None

    prefix_text = "".join(line for _, line in lines[:marker_index])
    rows: list[dict[str, Any]] = []
    index = header_end + 1
    common_price: Optional[dict[str, Any]] = None

    while index < len(lines):
        page, line = lines[index]
        if line.startswith("注：") or line.startswith("注:"):
            break
        if not re.fullmatch(r"\d{1,3}", line):
            index += 1
            continue
        if index + 3 >= len(lines):
            break

        name = lines[index + 1][1].strip(" ，。；:：")
        capital_text = lines[index + 2][1].replace(" ", "")
        if not is_number_line(capital_text):
            index += 1
            continue

        cursor = index + 3
        number_values: list[str] = []
        method = None
        while cursor < len(lines):
            value = lines[cursor][1].replace(" ", "")
            if value.startswith("注：") or value.startswith("注:"):
                break
            if value in ("货币", "现金", "实物", "净资产", "债权", "其他"):
                method = value
                cursor += 1
                break
            if re.fullmatch(r"\d{1,3}", value) and number_values:
                break
            if is_number_line(value):
                number_values.append(value)
                cursor += 1
                continue
            break

        if method is None or not number_values:
            index += 1
            continue

        if len(number_values) >= 2:
            row_price = numeric(number_values[0], "元/注册资本")
            amount_text = number_values[-1]
            if common_price is None:
                common_price = row_price
        else:
            row_price = common_price
            amount_text = number_values[0]

        full_name = alias_map.get(name, name)
        if full_name in role_hints:
            role = role_hints[full_name]
        elif any(word in full_name for word in ("投资", "基金", "创投", "资本", "股权投资中心")):
            role = "institutional_investor"
        elif name in prefix_text:
            role = "existing_shareholder"
        else:
            role = "new_shareholder"

        rows.append(
            {
                "participant_name": full_name,
                "participant_role": role,
                "subscribed_registered_capital": numeric(capital_text, "万元"),
                "subscribed_shares": None,
                "contribution_amount": numeric(amount_text, "万元"),
                "price_per_share": None,
                "price_per_registered_capital": row_price,
                "contribution_method": "cash" if method in ("货币", "现金") else "other",
                "notes": f"简称：{name}。" if full_name != name else None,
            }
        )
        index = cursor

    return rows, common_price


def participant_role(
    name: str,
    text: str,
    role_hints: dict[str, str],
) -> str:
    if name in role_hints:
        return role_hints[name]
    if "持股平台" in text or ("合伙企业" in name and "员工" in text):
        return "employee_platform"
    if any(word in name for word in ("投资", "基金", "创投", "资本")):
        return "institutional_investor"
    return "new_shareholder"


def subtype(text: str, title: str) -> str:
    merged = title + text
    if "定向发行" in merged or "股票发行" in merged:
        return "private_placement"
    if "吸收合并" in merged:
        return "merger_increase"
    if "资本公积转增" in merged or "转增股本" in merged:
        return "capitalization"
    if "设立" in title and "注册资本" in merged:
        return "establishment_subscription"
    if "债转股" in merged:
        return "debt_to_equity"

    strong_cash = any(
        phrase in merged
        for phrase in (
            "以货币方式缴纳出资",
            "货币出资",
            "现金认购",
            "认购款",
            "以现金方式",
        )
    )
    strong_in_kind = any(
        phrase in merged
        for phrase in (
            "以净资产出资",
            "以实物出资",
            "以知识产权出资",
            "以土地使用权出资",
        )
    )

    if strong_cash and strong_in_kind:
        return "mixed_increase"
    if strong_cash:
        return "cash_increase"
    if strong_in_kind:
        return "in_kind_increase"
    if any(word in merged for word in ("货币", "现金", "认购", "增资")):
        return "cash_increase"
    return "other"


def extract_record(
    package: dict[str, Any],
    segments: list[Segment],
    alias_map: dict[str, str],
    role_hints: dict[str, str],
) -> tuple[dict[str, Any], list[str]]:
    compact_segments = [(item.page, compact(item.text)) for item in segments]
    text = extraction_text(package, segments)
    title = str(package.get("event_title") or "subscription_flow")
    flow_type = subtype(text, title)

    review: list[str] = []
    evidence: list[dict[str, Any]] = []

    capital_before, capital_after, capital_match = find_change(
        text, CAPITAL_CHANGE_PATTERNS
    )
    capital_increase_match = None
    direct_new_capital = None
    if capital_match:
        add_evidence(
            evidence, compact_segments, "capital_change", excerpt(text, capital_match)
        )
    else:
        for pattern in CAPITAL_INCREASE_TO_PATTERNS:
            capital_increase_match = pattern.search(text)
            if capital_increase_match:
                direct_new_capital = numeric(
                    capital_increase_match.group("new"),
                    capital_increase_match.group("new_unit"),
                )
                capital_after = numeric(
                    capital_increase_match.group("after"),
                    capital_increase_match.group("after_unit"),
                )
                capital_before = inferred_numeric(
                    capital_after["normalized_value"]
                    - direct_new_capital["normalized_value"],
                    "CNY",
                )
                add_evidence(
                    evidence,
                    compact_segments,
                    "capital_change",
                    excerpt(text, capital_increase_match),
                )
                review.append("registration_capital_before_inferred_by_difference")
                break
        if capital_increase_match is None:
            after_capital, after_capital_match = find_numeric(
                text, AFTER_CAPITAL_PATTERNS
            )
            credited_capital, credited_capital_match = find_numeric(
                text, NEW_CAPITAL_CREDIT_PATTERNS
            )
            if after_capital is not None and credited_capital is not None:
                capital_after = after_capital
                direct_new_capital = credited_capital
                capital_before = inferred_numeric(
                    capital_after["normalized_value"]
                    - direct_new_capital["normalized_value"],
                    "CNY",
                )
                capital_increase_match = after_capital_match or credited_capital_match
                add_evidence(
                    evidence,
                    compact_segments,
                    "capital_change",
                    excerpt(text, capital_increase_match),
                )
                review.append("registration_capital_before_inferred_by_difference")
            else:
                review.append("registration_capital_change_not_found")

    shares_before, shares_after, shares_match = find_change(
        text, SHARE_CHANGE_PATTERNS
    )
    if shares_match:
        add_evidence(
            evidence, compact_segments, "capital_change", excerpt(text, shares_match)
        )
    elif (
        capital_before
        and capital_after
        and (
            "总股本" in text
            or "股份总数" in text
            or re.search(rf"每股\s*{NUMBER}\s*元", text) is not None
            or "股票发行" in text
            or "定向发行" in text
        )
    ):
        shares_before = inferred_numeric(
            capital_before["normalized_value"], "share"
        )
        shares_after = inferred_numeric(
            capital_after["normalized_value"], "share"
        )
        review.append("total_shares_inferred_from_registered_capital")

    new_capital = direct_new_capital
    new_capital_match = capital_increase_match
    if new_capital_match is None:
        new_capital, new_capital_match = find_numeric(text, NEW_CAPITAL_PATTERNS)
    if new_capital_match:
        add_evidence(
            evidence, compact_segments, "capital_change", excerpt(text, new_capital_match)
        )
    elif capital_before and capital_after:
        new_capital = inferred_numeric(
            capital_after["normalized_value"] - capital_before["normalized_value"],
            "CNY",
        )
        review.append("new_registered_capital_inferred_by_difference")
    else:
        review.append("new_registered_capital_not_found")

    if flow_type == "merger_increase":
        merger_before, merger_after, merger_new, merger_match = (
            find_merger_capital_values(text)
        )
        if merger_after is not None:
            capital_after = merger_after
        if merger_new is not None:
            new_capital = merger_new
        if merger_before is not None:
            capital_before = merger_before
        if merger_match is not None:
            add_evidence(
                evidence, compact_segments, "capital_change", excerpt(text, merger_match)
            )
        review = [
            reason
            for reason in review
            if reason
            not in {
                "registration_capital_change_not_found",
                "new_registered_capital_not_found",
                "new_registered_capital_inferred_by_difference",
                "registration_capital_before_inferred_by_difference",
            }
        ]
        if merger_before is not None and merger_after is not None:
            review.append("registration_capital_before_inferred_by_difference")

    new_shares, new_shares_match = find_numeric(text, NEW_SHARE_PATTERNS)
    if new_shares_match:
        add_evidence(
            evidence, compact_segments, "capital_change", excerpt(text, new_shares_match)
        )
    elif shares_before and shares_after:
        new_shares = inferred_numeric(
            shares_after["normalized_value"] - shares_before["normalized_value"],
            "share",
        )
        review.append("new_shares_inferred_by_difference")
    else:
        review.append("new_shares_not_found")

    if flow_type == "merger_increase":
        new_shares = None
        review = [reason for reason in review if reason != "new_shares_not_found"]

    consideration, consideration_match = find_numeric(text, CONSIDERATION_PATTERNS)
    if consideration_match:
        add_evidence(
            evidence, compact_segments, "payment", excerpt(text, consideration_match)
        )
    else:
        review.append("total_consideration_not_found")

    if flow_type == "merger_increase":
        consideration = None
        consideration_match = None
        review = [
            reason for reason in review
            if reason != "total_consideration_not_found"
        ]

    premium, premium_match = find_numeric(text, PREMIUM_PATTERNS)
    if premium_match:
        add_evidence(
            evidence, compact_segments, "capital_change", excerpt(text, premium_match)
        )

    price_share, price_share_match = find_numeric(
        text, PRICE_SHARE_PATTERNS, forced_unit="元/股"
    )
    if price_share_match:
        add_evidence(
            evidence, compact_segments, "price", excerpt(text, price_share_match)
        )

    price_capital, price_capital_match = find_numeric(
        text, PRICE_CAPITAL_PATTERNS, forced_unit="元/注册资本"
    )
    if price_capital_match:
        add_evidence(
            evidence, compact_segments, "price", excerpt(text, price_capital_match)
        )

    decision_date = find_decision_date(text)
    agreement_date = find_date_followed_by(
        text, ("签署", "签订")
    ) or find_date(text, AGREEMENT_KEYWORDS)
    payment_date = find_date_followed_by(
        text,
        ("支付完毕全部增资款", "支付全部增资款", "缴纳完毕", "缴纳出资", "出资到账"),
    ) or find_date(text, PAYMENT_KEYWORDS)
    verification_date = find_date(
        text, VERIFICATION_KEYWORDS, prefer_before_keyword=True
    )
    registration_date = find_date_followed_by(
        text,
        ("办理完毕工商变更登记", "工商变更登记", "换发的《营业执照》", "取得了营业执照"),
    ) or find_date(text, REGISTRATION_KEYWORDS)

    if flow_type == "merger_increase":
        decision_date = (
            find_date_before_regex(
                text,
                r"[^。；]{0,100}?召开股东会[^。；]{0,160}?注册资本(?:变更为|为|增至)",
                max_distance=300,
            )
            or decision_date
        )
        agreement_date = find_merger_agreement_date(text) or agreement_date
        registration_date = (
            find_merger_registration_date(text) or registration_date
        )

    if decision_date:
        add_evidence(
            evidence, compact_segments, "decision", decision_date["raw_text"]
        )
    else:
        review.append("decision_date_not_found")

    if verification_date:
        add_evidence(
            evidence, compact_segments, "verification", verification_date["raw_text"]
        )
    if registration_date:
        add_evidence(
            evidence, compact_segments, "registration", registration_date["raw_text"]
        )

    table_participants, table_price_capital = parse_subscription_table(
        segments, alias_map, role_hints
    )
    if price_capital is None and table_price_capital is not None:
        price_capital = table_price_capital

    participants: list[dict[str, Any]] = table_participants
    if not participants:
        names = payment_participant_names(text, alias_map)
        if not names:
            names = participant_names(text, alias_map)
        if not names:
            review.append("participant_not_found")
        for name in names:
            single = len(names) == 1
            participants.append(
                {
                    "participant_name": name,
                    "participant_role": participant_role(name, text, role_hints),
                    "subscribed_registered_capital": new_capital if single else None,
                    "subscribed_shares": new_shares if single else None,
                    "contribution_amount": consideration if single else None,
                    "price_per_share": price_share,
                    "price_per_registered_capital": price_capital,
                    "contribution_method": (
                        "cash"
                        if any(word in text for word in ("货币", "现金", "认购款"))
                        else "other"
                    ),
                    "notes": None if single else "多个参与方存在，V1不自动分摊总额。",
                }
            )

    if flow_type == "merger_increase":
        merged_entity = find_merged_entity_name(text, alias_map)
        net_assets = find_merged_entity_net_assets(
            text, merged_entity, alias_map
        )
        if merged_entity is not None:
            participants = [
                {
                    "participant_name": merged_entity,
                    "participant_role": "merged_entity",
                    "subscribed_registered_capital": new_capital,
                    "subscribed_shares": None,
                    "contribution_amount": net_assets,
                    "price_per_share": None,
                    "price_per_registered_capital": None,
                    "contribution_method": "merged_entity_net_assets",
                    "notes": (
                        "被合并方净资产账面价值，不是现金认购对价。"
                        if net_assets is not None
                        else None
                    ),
                }
            ]
            review = [
                reason for reason in review
                if reason != "participant_not_found"
            ]
        else:
            participants = []
            if "participant_not_found" not in review:
                review.append("participant_not_found")

    snapshot_phrases = (
        "增资后股权结构",
        "本次增资后",
        "增资完成后",
        "变更后股权结构",
        "本次增资和股权转让完成后",
        "本次股权转让和增资完成后",
    )
    snapshot_start_page = next(
        (
            page
            for page, page_text in compact_segments
            if any(phrase in page_text for phrase in snapshot_phrases)
        ),
        None,
    )
    post_pages = (
        [page for page, _ in compact_segments if page >= snapshot_start_page]
        if snapshot_start_page is not None
        else []
    )
    if flow_type == "merger_increase":
        post_pages = merger_snapshot_pages(compact_segments)

    if not evidence:
        add_evidence(evidence, compact_segments, "other", text[:300])
        review.append("only_generic_evidence_available")

    package_id = str(package.get("package_id") or "")

    record = {
        "schema_version": "1.0",
        "company_code": str(package.get("company_code") or ""),
        "company_name": str(package.get("company_name") or ""),
        "package_id": package_id,
        "event_id": f"{package_id}_evt_001",
        "event_family": "subscription_flow",
        "event_title": title,
        "flow_subtype": flow_type,
        "decision_date": decision_date,
        "agreement_date": agreement_date,
        "payment_date": payment_date,
        "verification_date": verification_date,
        "registration_date": registration_date,
        "effective_date": find_effective_date(text) or registration_date,
        "source_pages": get_pages(package),
        "evidence": evidence,
        "registration_capital_before": capital_before,
        "registration_capital_after": capital_after,
        "total_shares_before": shares_before,
        "total_shares_after": shares_after,
        "new_registered_capital": new_capital,
        "new_shares": new_shares,
        "total_consideration": consideration,
        "capital_premium": premium,
        "participants": participants,
        "post_event_snapshot_present": bool(post_pages),
        "post_event_snapshot_pages": unique_pages(post_pages),
        "currency": "CNY",
        "notes": "V1.5规则抽取结果；后续需进行Schema校验、数值Cross-check和Gold比较。",
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
        "participant_count",
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
        if row.get("event_family") == "subscription_flow"
        and (not args.package_id or row.get("package_id") == args.package_id)
        and (
            not args.company_code
            or str(row.get("company_code")) == args.company_code
        )
    ]

    output: list[dict[str, Any]] = []
    logs: list[dict[str, Any]] = []
    alias_map, role_hints = extract_alias_data_from_pdf(args.pdf_path)

    for package in selected:
        package_id = str(package.get("package_id") or "")
        try:
            segments = get_segments(package, args.pdf_path)
            record, reasons = extract_record(
                package, segments, alias_map, role_hints
            )
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
                    "participant_count": len(record["participants"]),
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
                    "source_pages": "|".join(map(str, get_pages(package))),
                    "participant_count": 0,
                    "evidence_count": 0,
                }
            )

    write_jsonl(args.output_jsonl, output)
    write_log(args.output_log, logs)

    errors = sum(row["status"] == "ERROR" for row in logs)
    reviews = sum(bool(row.get("needs_manual_review")) for row in output)

    print(f"packages_input={len(packages)}")
    print(f"subscription_packages={len(selected)}")
    print(f"records_output={len(output)}")
    print(f"needs_manual_review={reviews}")
    print(f"errors={errors}")
    print(f"output_jsonl={args.output_jsonl}")
    print(f"output_log={args.output_log}")

    if not selected:
        print("没有找到符合条件的subscription_flow事件包。", file=sys.stderr)
        return 3
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
