
from __future__ import annotations

from pathlib import Path

import pandas as pd


COMPANY_CODE = "603418"


def normalize_company_code(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )


def load_original_page_gold(path: Path) -> pd.DataFrame:
    """
    读取原V1候选页人工标注，以及V1遗漏页（第74页）。
    """
    main = pd.read_excel(
        path,
        sheet_name="603418_V1候选页",
        dtype={"company_code": "string"},
    )

    missing = pd.read_excel(
        path,
        sheet_name="V1遗漏页",
        dtype={"company_code": "string"},
    )

    keep_columns = [
        "company_code",
        "company_name",
        "pdf_file_page",
        "人工页面标签",
        "事件类型",
        "最终是否保留",
        "人工备注",
    ]

    main = main[keep_columns].copy()
    missing = missing[keep_columns].copy()

    return pd.concat([main, missing], ignore_index=True)


def load_new_page_reviews(path: Path) -> pd.DataFrame:
    """
    读取V2新增33页的人工复核结果。

    由于原文件同时包含自动列和人工列，人工结论位于：
    manual_label.1、manual_event_type、manual_decision、manual_notes.1。
    """
    reviewed = pd.read_excel(
        path,
        sheet_name="新增页面复核结果",
        dtype={"company_code": "string"},
    )

    required = [
        "company_code",
        "pdf_file_page",
        "manual_label.1",
        "manual_event_type",
        "manual_decision",
        "manual_notes.1",
    ]

    missing_columns = [
        column
        for column in required
        if column not in reviewed.columns
    ]

    if missing_columns:
        raise ValueError(
            "新增页面复核表缺少列："
            + ", ".join(missing_columns)
        )

    return pd.DataFrame(
        {
            "company_code": reviewed["company_code"],
            "company_name": "友升股份",
            "pdf_file_page": reviewed["pdf_file_page"],
            "人工页面标签": reviewed["manual_label.1"],
            "事件类型": reviewed["manual_event_type"],
            "最终是否保留": reviewed["manual_decision"],
            "人工备注": reviewed["manual_notes.1"],
        }
    )


def build_page_gold(
    original_gold: pd.DataFrame,
    new_reviews: pd.DataFrame,
) -> pd.DataFrame:
    page_gold = pd.concat(
        [original_gold, new_reviews],
        ignore_index=True,
    )

    page_gold["company_code"] = normalize_company_code(
        page_gold["company_code"]
    )
    page_gold["pdf_file_page"] = pd.to_numeric(
        page_gold["pdf_file_page"],
        errors="raise",
    ).astype(int)

    page_gold = page_gold[
        page_gold["company_code"] == COMPANY_CODE
    ].copy()

    valid_decisions = {"keep", "drop"}
    invalid = page_gold[
        ~page_gold["最终是否保留"].isin(valid_decisions)
    ]

    if not invalid.empty:
        raise ValueError(
            "页面Gold中仍有非keep/drop记录，请先人工处理：\n"
            + invalid[
                [
                    "pdf_file_page",
                    "最终是否保留",
                    "人工备注",
                ]
            ].to_string(index=False)
        )

    # 后加入的人工复核结果优先。
    page_gold = (
        page_gold.drop_duplicates(
            subset=["company_code", "pdf_file_page"],
            keep="last",
        )
        .sort_values("pdf_file_page")
        .reset_index(drop=True)
    )

    page_gold["gold_keep"] = (
        page_gold["最终是否保留"] == "keep"
    )

    return page_gold


def load_locator_results(path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        dtype={"company_code": "string"},
        encoding="utf-8-sig",
    )

    df["company_code"] = normalize_company_code(
        df["company_code"]
    )
    df["pdf_file_page"] = pd.to_numeric(
        df["pdf_file_page"],
        errors="raise",
    ).astype(int)

    df = df[df["company_code"] == COMPANY_CODE].copy()

    if "candidate_status" not in df.columns:
        raise ValueError(
            f"{path.name}缺少candidate_status列"
        )

    return df[
        [
            "pdf_file_page",
            "candidate_status",
        ]
    ].drop_duplicates(
        subset=["pdf_file_page"],
        keep="last",
    )


def is_selected(status: str) -> bool:
    return status in {"candidate", "review"}


def calculate_metrics(
    detail: pd.DataFrame,
    selected_column: str,
) -> dict[str, int | float]:
    tp = int(
        (
            detail["gold_keep"]
            & detail[selected_column]
        ).sum()
    )
    fp = int(
        (
            ~detail["gold_keep"]
            & detail[selected_column]
        ).sum()
    )
    fn = int(
        (
            detail["gold_keep"]
            & ~detail[selected_column]
        ).sum()
    )
    tn = int(
        (
            ~detail["gold_keep"]
            & ~detail[selected_column]
        ).sum()
    )

    precision = (
        tp / (tp + fp)
        if tp + fp
        else 0.0
    )
    recall = (
        tp / (tp + fn)
        if tp + fn
        else 0.0
    )
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

    original_gold_path = (
        project_root
        / "evaluation"
        / "603418_友升股份_V1页面标注表_已完成.xlsx"
    )
    new_reviews_path = (
        project_root
        / "evaluation"
        / "section_locator_v2_new_pages_reviewed.xlsx"
    )
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
        / "section_location_log_v2_1.csv"
    )

    required_paths = [
        original_gold_path,
        new_reviews_path,
        v1_path,
        v2_path,
    ]

    for path in required_paths:
        if not path.exists():
            raise FileNotFoundError(
                f"缺少输入文件：{path}"
            )

    original_gold = load_original_page_gold(
        original_gold_path
    )
    new_reviews = load_new_page_reviews(
        new_reviews_path
    )

    page_gold = build_page_gold(
        original_gold=original_gold,
        new_reviews=new_reviews,
    )

    v1 = load_locator_results(v1_path).rename(
        columns={
            "candidate_status": "v1_status",
        }
    )
    v2 = load_locator_results(v2_path).rename(
        columns={
            "candidate_status": "v2_status",
        }
    )

    detail = (
        page_gold.merge(
            v1,
            on="pdf_file_page",
            how="left",
        )
        .merge(
            v2,
            on="pdf_file_page",
            how="left",
        )
    )

    detail["v1_status"] = detail[
        "v1_status"
    ].fillna("")
    detail["v2_status"] = detail[
        "v2_status"
    ].fillna("")

    detail["v1_selected"] = detail[
        "v1_status"
    ].map(is_selected)
    detail["v2_selected"] = detail[
        "v2_status"
    ].map(is_selected)

    detail["v1_result"] = "TN"
    detail.loc[
        detail["gold_keep"] & detail["v1_selected"],
        "v1_result",
    ] = "TP"
    detail.loc[
        ~detail["gold_keep"] & detail["v1_selected"],
        "v1_result",
    ] = "FP"
    detail.loc[
        detail["gold_keep"] & ~detail["v1_selected"],
        "v1_result",
    ] = "FN"

    detail["v2_result"] = "TN"
    detail.loc[
        detail["gold_keep"] & detail["v2_selected"],
        "v2_result",
    ] = "TP"
    detail.loc[
        ~detail["gold_keep"] & detail["v2_selected"],
        "v2_result",
    ] = "FP"
    detail.loc[
        detail["gold_keep"] & ~detail["v2_selected"],
        "v2_result",
    ] = "FN"

    v1_metrics = calculate_metrics(
        detail,
        "v1_selected",
    )
    v2_metrics = calculate_metrics(
        detail,
        "v2_selected",
    )

    summary = pd.DataFrame(
        [
            {"version": "V1", **v1_metrics},
            {"version": "V2.1", **v2_metrics},
        ]
    )

    false_positives = detail[
        detail["v2_result"] == "FP"
    ].copy()

    false_negatives = detail[
        detail["v2_result"] == "FN"
    ].copy()

    output_dir = project_root / "evaluation"
    output_dir.mkdir(parents=True, exist_ok=True)

    page_gold_path = (
        output_dir
        / "603418_page_gold.csv"
    )
    detail_path = (
        output_dir
        / "section_locator_v1_v2_1_final_detail.csv"
    )
    summary_path = (
        output_dir
        / "section_locator_v1_v2_1_final_summary.csv"
    )
    fp_path = (
        output_dir
        / "section_locator_v2_1_false_positives.csv"
    )
    fn_path = (
        output_dir
        / "section_locator_v2_1_false_negatives.csv"
    )

    page_gold.to_csv(
        page_gold_path,
        index=False,
        encoding="utf-8-sig",
    )
    detail.to_csv(
        detail_path,
        index=False,
        encoding="utf-8-sig",
    )
    summary.to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )
    false_positives.to_csv(
        fp_path,
        index=False,
        encoding="utf-8-sig",
    )
    false_negatives.to_csv(
        fn_path,
        index=False,
        encoding="utf-8-sig",
    )

    print("页面Gold数量：", len(page_gold))
    print(
        "Gold keep/drop：",
        page_gold["最终是否保留"]
        .value_counts()
        .to_dict(),
    )
    print("V1指标：", v1_metrics)
    print("V2.1指标：", v2_metrics)
    print(
        "V2.1误选页数：",
        len(false_positives),
    )
    print(
        "V2.1漏选页数：",
        len(false_negatives),
    )
    print("\n输出文件：")
    for path in [
        page_gold_path,
        detail_path,
        summary_path,
        fp_path,
        fn_path,
    ]:
        print(path)


if __name__ == "__main__":
    main()
