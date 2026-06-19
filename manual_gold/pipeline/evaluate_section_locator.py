
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd


COMPANY_CODE = "603418"


def read_locator_pages(
    path: Path,
    company_code: str,
) -> dict[int, str]:
    df = pd.read_csv(
        path,
        dtype={"company_code": "string"},
        encoding="utf-8-sig",
    )
    df["company_code"] = df["company_code"].str.zfill(6)
    df = df[df["company_code"] == company_code].copy()

    status_column = (
        "candidate_status"
        if "candidate_status" in df.columns
        else "V1_status"
    )

    return {
        int(row["pdf_file_page"]): str(row[status_column])
        for _, row in df.iterrows()
    }


def load_manual_labels(excel_path: Path) -> pd.DataFrame:
    main_df = pd.read_excel(
        excel_path,
        sheet_name="603418_V1候选页",
        dtype={"company_code": "string"},
    )
    main_df["company_code"] = main_df["company_code"].str.zfill(6)
    main_df = main_df[main_df["company_code"] == COMPANY_CODE].copy()

    columns = [
        "company_code",
        "company_name",
        "pdf_file_page",
        "人工页面标签",
        "事件类型",
        "最终是否保留",
        "人工备注",
    ]
    main_df = main_df[columns]

    missing_df = pd.read_excel(
        excel_path,
        sheet_name="V1遗漏页",
        dtype={"company_code": "string"},
    )
    missing_df["company_code"] = missing_df["company_code"].str.zfill(6)
    missing_df = missing_df[missing_df["company_code"] == COMPANY_CODE].copy()
    missing_df = missing_df[columns]

    combined = pd.concat(
        [main_df, missing_df],
        ignore_index=True,
    )
    combined["pdf_file_page"] = combined["pdf_file_page"].astype(int)
    return combined


def selected(status: str | None) -> bool:
    return status in {"candidate", "review"}


def calculate_metrics(df: pd.DataFrame, prediction_col: str) -> dict[str, float]:
    labeled = df[df["gold_keep"].notna()].copy()

    tp = int(((labeled["gold_keep"] == True) & (labeled[prediction_col] == True)).sum())
    fp = int(((labeled["gold_keep"] == False) & (labeled[prediction_col] == True)).sum())
    fn = int(((labeled["gold_keep"] == True) & (labeled[prediction_col] == False)).sum())
    tn = int(((labeled["gold_keep"] == False) & (labeled[prediction_col] == False)).sum())

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]

    v1_path = (
        project_root
        / "outputs"
        / "logs"
        / "section_location_log_v1.csv"
    )
    v2_path = (
        project_root
        / "outputs"
        / "logs"
        / "section_location_log_v2.csv"
    )
    gold_path = (
        project_root
        / "evaluation"
        / "603418_友升股份_V1页面标注表_已完成.xlsx"
    )

    output_detail = (
        project_root
        / "evaluation"
        / "section_locator_v1_v2_detail.csv"
    )
    output_summary = (
        project_root
        / "evaluation"
        / "section_locator_v1_v2_summary.csv"
    )
    output_new_pages = (
        project_root
        / "evaluation"
        / "section_locator_v2_new_pages_to_review.csv"
    )

    for path in (v1_path, v2_path, gold_path):
        if not path.exists():
            raise FileNotFoundError(f"缺少文件：{path}")

    v1_pages = read_locator_pages(v1_path, COMPANY_CODE)
    v2_pages = read_locator_pages(v2_path, COMPANY_CODE)
    manual = load_manual_labels(gold_path)

    manual["gold_keep"] = manual["最终是否保留"].map(
        {"keep": True, "drop": False}
    )

    all_pages = sorted(
        set(manual["pdf_file_page"])
        | set(v1_pages)
        | set(v2_pages)
    )

    manual_map = manual.set_index("pdf_file_page").to_dict("index")
    detail_rows = []

    for page in all_pages:
        gold = manual_map.get(page, {})
        v1_status = v1_pages.get(page)
        v2_status = v2_pages.get(page)

        detail_rows.append(
            {
                "company_code": COMPANY_CODE,
                "pdf_file_page": page,
                "manual_label": gold.get("人工页面标签", ""),
                "event_type": gold.get("事件类型", ""),
                "gold_keep": gold.get("gold_keep"),
                "manual_notes": gold.get("人工备注", ""),
                "v1_status": v1_status or "",
                "v1_selected": selected(v1_status),
                "v2_status": v2_status or "",
                "v2_selected": selected(v2_status),
                "comparison_status": (
                    "needs_manual_review"
                    if page not in manual_map
                    else "labeled"
                ),
            }
        )

    detail_df = pd.DataFrame(detail_rows)
    output_detail.parent.mkdir(parents=True, exist_ok=True)
    detail_df.to_csv(
        output_detail,
        index=False,
        encoding="utf-8-sig",
    )

    v1_metrics = calculate_metrics(detail_df, "v1_selected")
    v2_metrics = calculate_metrics(detail_df, "v2_selected")

    summary_df = pd.DataFrame(
        [
            {"version": "V1", **v1_metrics},
            {"version": "V2", **v2_metrics},
        ]
    )
    summary_df.to_csv(
        output_summary,
        index=False,
        encoding="utf-8-sig",
    )

    new_pages_df = detail_df[
        detail_df["comparison_status"] == "needs_manual_review"
    ].copy()
    new_pages_df.to_csv(
        output_new_pages,
        index=False,
        encoding="utf-8-sig",
    )

    print("V1指标：", v1_metrics)
    print("V2指标：", v2_metrics)
    print(f"逐页对比：{output_detail}")
    print(f"汇总指标：{output_summary}")
    print(f"V2新增待复核页：{output_new_pages}")


if __name__ == "__main__":
    main()
