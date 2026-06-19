from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


ValueSource = Literal[
    "disclosed",
    "calculated",
    "inferred",
    "unknown",
]

ReviewStatus = Literal[
    "confirmed",
    "pending",
    "manual_review",
]


class EvidenceBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company_code: str
    company_name: str
    pdf_page: int = Field(gt=0)
    section_title: str
    evidence_text: str = Field(min_length=2)
    value_source: ValueSource = "disclosed"
    review_status: ReviewStatus = "pending"

    @field_validator("company_code")
    @classmethod
    def validate_company_code(cls, value: str) -> str:
        if len(value) != 6 or not value.isdigit():
            raise ValueError("company_code必须是6位数字")
        return value

    @field_validator("evidence_text")
    @classmethod
    def validate_evidence(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("evidence_text不能为空")
        return cleaned


class SubscriptionFlow(EvidenceBase):
    record_type: Literal["subscription_flow"]
    event_id: str
    event_date: str | None = None
    subscriber_name: str

    subscription_quantity_wan_shares: Decimal | None = Field(
        default=None,
        ge=0,
    )
    subscription_amount_wan_yuan: Decimal | None = Field(
        default=None,
        ge=0,
    )
    subscription_price_yuan_per_share: Decimal | None = Field(
        default=None,
        ge=0,
    )
    capital_increase_wan_yuan: Decimal | None = Field(
        default=None,
        ge=0,
    )


class ShareTransferFlow(EvidenceBase):
    record_type: Literal["share_transfer_flow"]
    event_id: str
    transfer_date: str | None = None
    transferor_name: str
    transferee_name: str

    transfer_quantity_wan_shares: Decimal | None = Field(
        default=None,
        ge=0,
    )
    transfer_ratio_percent: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
    )
    transfer_amount_wan_yuan: Decimal | None = Field(
        default=None,
        ge=0,
    )
    transfer_price_yuan_per_share: Decimal | None = Field(
        default=None,
        ge=0,
    )


class EquitySnapshot(EvidenceBase):
    record_type: Literal["equity_snapshot"]
    snapshot_id: str
    snapshot_date: str | None = None
    snapshot_label: str
    shareholder_name: str

    holding_quantity_wan_shares: Decimal | None = Field(
        default=None,
        ge=0,
    )
    capital_contribution_wan_yuan: Decimal | None = Field(
        default=None,
        ge=0,
    )
    holding_ratio_percent: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
    )
    total_share_capital_wan_shares: Decimal | None = Field(
        default=None,
        ge=0,
    )
    total_registered_capital_wan_yuan: Decimal | None = Field(
        default=None,
        ge=0,
    )