from __future__ import annotations

import csv
import re
from pathlib import Path

import fitz


COMPANY_CODE = "603418"

# 第一组：人工Gold核心页
CORE_PAGE_RANGES = [
    (41, 45, "设立、整体变更及历次增资"),
    (73, 80, "发行前股权结构及新增股东"),
]

# 第二组：需要判断是否属于直接发行人股权转让
REVIEW_PAGE_RANGES = [
    (47, 51, "代持、间接股权转让及控制权变化"),
]


def normalize_text(text: str) -> str:
    text = text.replace("\u3000", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def load_manifest_row(
    manifest_path: Path,
    company_code: str,
) -> dict[str, str]:
    with manifest_path.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            code = row["company_code"].strip().zfill(6)

            if code == company_code:
                return row

    raise ValueError(f"未在manifest中找到公司：{company_code}")


def expand_page_ranges(
    ranges: list[tuple[int, int, str]],
) -> list[tuple[int, str]]:
    pages: list[tuple[int, str]] = []

    for start, end, group_name in ranges:
        for page_number in range(start, end + 1):
            pages.append((page_number, group_name))

    return pages


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]

    manifest_path = (
        project_root
        / "data"
        / "pdf_manifest.csv"
    )

    manifest_row = load_manifest_row(
        manifest_path,
        COMPANY_CODE,
    )

    pdf_path = (
        project_root
        / manifest_row["pdf_path"].strip()
    )

    if not pdf_path.exists():
        raise FileNotFoundError(f"找不到PDF：{pdf_path}")

    output_dir = (
        project_root
        / "outputs"
        / "review_packets"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    markdown_path = (
        output_dir
        / f"{COMPANY_CODE}_review_packet.md"
    )

    index_path = (
        output_dir
        / f"{COMPANY_CODE}_review_index.csv"
    )

    page_groups = (
        expand_page_ranges(CORE_PAGE_RANGES)
        + expand_page_ranges(REVIEW_PAGE_RANGES)
    )

    # 防止同一页被重复写入。
    page_group_map: dict[int, list[str]] = {}

    for page_number, group_name in page_groups:
        page_group_map.setdefault(page_number, [])
        page_group_map[page_number].append(group_name)

    index_rows: list[dict[str, str | int]] = []
    markdown_parts: list[str] = [
        "# 603418 友升股份 Week3人工复核材料",
        "",
        "本材料中的页码为PDF文件页序号。",
        "",
    ]

    with fitz.open(pdf_path) as document:
        total_pages = len(document)

        for pdf_file_page in sorted(page_group_map):
            if pdf_file_page < 1 or pdf_file_page > total_pages:
                print(
                    f"[WARNING] 页码超出范围：{pdf_file_page}"
                )
                continue

            page = document[pdf_file_page - 1]
            text = normalize_text(page.get_text("text"))

            group_name = "；".join(
                page_group_map[pdf_file_page]
            )

            markdown_parts.extend(
                [
                    "---",
                    "",
                    f"## PDF文件第 {pdf_file_page} 页",
                    "",
                    f"复核组：{group_name}",
                    "",
                    "```text",
                    text,
                    "```",
                    "",
                    "人工记录：",
                    "",
                    "- 正文印刷页码：",
                    "- 章节标题：",
                    "- 是否属于发行人直接股本变化：",
                    "- 事件类型：",
                    "- 是否需要进入Gold：",
                    "- 备注：",
                    "",
                ]
            )

            index_rows.append(
                {
                    "company_code": COMPANY_CODE,
                    "company_name": manifest_row[
                        "company_name"
                    ],
                    "pdf_file_page": pdf_file_page,
                    "review_group": group_name,
                    "printed_page": "",
                    "section_title": "",
                    "event_type": "",
                    "gold_status": "pending",
                    "review_notes": "",
                }
            )

    markdown_path.write_text(
        "\n".join(markdown_parts),
        encoding="utf-8",
    )

    fieldnames = [
        "company_code",
        "company_name",
        "pdf_file_page",
        "review_group",
        "printed_page",
        "section_title",
        "event_type",
        "gold_status",
        "review_notes",
    ]

    with index_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(index_rows)

    print(f"复核材料已生成：{markdown_path}")
    print(f"复核索引已生成：{index_path}")


if __name__ == "__main__":
    main()