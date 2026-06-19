#!/usr/bin/env python
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError


ROOT = Path(".")
OUTPUT = ROOT / "outputs/validation/pydantic_validation_all.csv"

FILES = {
    "subscription_flow": "outputs/structured/*_subscription_flow_frozen_v1.jsonl",
    "share_transfer_flow": "outputs/structured/*_share_transfer_flow_frozen_v1.jsonl",
    "equity_snapshot": "outputs/structured/*_equity_snapshot_v1.jsonl",
}


class FlexibleModel(BaseModel):
    model_config = ConfigDict(extra="allow", coerce_numbers_to_str=True)


class DateValue(FlexibleModel):
    raw_text: str | None = None
    iso_date: str | None = None


class NumericValue(FlexibleModel):
    raw_text: str | None = None
    value: float | int | None = None
    unit: str | None = None
    normalized_value: float | int | None = None
    normalized_unit: str | None = None


class EvidenceItem(FlexibleModel):
    page: int = Field(ge=1)
    evidence_role: str
    text: str


class Participant(FlexibleModel):
    participant_name: str
    participant_role: str | None = None


class TransferItem(FlexibleModel):
    transferor_name: str
    transferee_name: str


class HolderItem(FlexibleModel):
    holder_name: str
    direct_holder: bool | None = None


class CommonRecord(FlexibleModel):
    schema_version: str
    company_code: str = Field(pattern=r"^\d{6}$")
    company_name: str
    package_id: str
    event_id: str
    event_title: str
    source_pages: list[int]
    evidence: list[EvidenceItem]
    needs_manual_review: bool
    review_reasons: list[str]


class SubscriptionRecord(CommonRecord):
    event_family: Literal["subscription_flow"]
    flow_subtype: str
    participants: list[Participant]


class ShareTransferRecord(CommonRecord):
    event_family: Literal["share_transfer_flow"]
    transfer_subtype: str
    target_company_name: str
    target_is_issuer: bool
    transfers: list[TransferItem] = Field(min_length=1)


class EquitySnapshotRecord(CommonRecord):
    event_family: Literal["equity_snapshot"]
    snapshot_type: str
    issuer_confirmed: bool
    holders: list[HolderItem]


MODELS = {
    "subscription_flow": SubscriptionRecord,
    "share_transfer_flow": ShareTransferRecord,
    "equity_snapshot": EquitySnapshotRecord,
}


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8-sig") as file:
        for line_number, raw in enumerate(file, start=1):
            if raw.strip():
                yield line_number, raw.strip()


def main() -> int:
    rows: list[dict[str, Any]] = []

    for family, pattern in FILES.items():
        model = MODELS[family]
        for path in sorted(ROOT.glob(pattern)):
            for line_number, raw in iter_jsonl(path):
                record_id = ""
                try:
                    value = json.loads(raw)
                    record_id = str(value.get("package_id") or value.get("event_id") or "")
                    model.model_validate(value)
                    rows.append({
                        "source_file": str(path),
                        "source_line": line_number,
                        "event_family": family,
                        "record_id": record_id,
                        "status": "VALID",
                        "error_path": "",
                        "message": "",
                    })
                except json.JSONDecodeError as exc:
                    rows.append({
                        "source_file": str(path),
                        "source_line": line_number,
                        "event_family": family,
                        "record_id": record_id,
                        "status": "INVALID",
                        "error_path": "",
                        "message": f"JSONDecodeError: {exc}",
                    })
                except ValidationError as exc:
                    for error in exc.errors():
                        location = ".".join(str(part) for part in error.get("loc", []))
                        rows.append({
                            "source_file": str(path),
                            "source_line": line_number,
                            "event_family": family,
                            "record_id": record_id,
                            "status": "INVALID",
                            "error_path": location,
                            "message": error.get("msg", ""),
                        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "source_file",
        "source_line",
        "event_family",
        "record_id",
        "status",
        "error_path",
        "message",
    ]
    with OUTPUT.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    valid_records = {
        (row["source_file"], row["source_line"])
        for row in rows
        if row["status"] == "VALID"
    }
    invalid_records = {
        (row["source_file"], row["source_line"])
        for row in rows
        if row["status"] == "INVALID"
    }
    valid_records -= invalid_records

    print(f"records_valid={len(valid_records)}")
    print(f"records_invalid={len(invalid_records)}")
    print(f"output={OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
