from __future__ import annotations

import argparse
import csv
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import fitz


KEEP_STATUSES = {"candidate", "review"}


@dataclass(frozen=True)
class StartRule:
    rule_id: str
    event_family: str
    pattern: re.Pattern[str]
    title_template: str
    end_pattern: re.Pattern[str]


@dataclass
class CandidatePackage:
    company_code: str
    company_name: str
    package_id: str
    event_family: str
    event_title: str
    start_page: int
    end_page: int
    primary_pages: list[int]
    supporting_pages: list[int]
    all_pages: list[int]
    start_anchor: str
    end_anchor: str
    source_segments: list[dict]
    source_text: str
    issuer_bound: bool
    is_mixed_page: bool
    is_non_contiguous: bool
    needs_manual_review: bool
    detection_reason: str


def compact_text(text: str) -> str:
    """Normalize PDF text while preserving paragraph-level readability."""
    text = text.replace("\u3000", " ").replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def search_text(text: str) -> str:
    """Whitespace-insensitive text used for anchor matching."""
    return re.sub(r"\s+", "", text)


def page_header_length(text: str) -> int:
    """Length of the repeated company/header block in whitespace-free text."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and re.fullmatch(r"\d+-\d+-\d+", lines[0]):
        return len(search_text(lines[0]))
    if (
        len(lines) >= 2
        and "招股说明书" in lines[0]
        and re.fullmatch(r"(?:\d+|\d+-\d+-\d+)", lines[1])
    ):
        return len(search_text(lines[0] + lines[1]))
    return 0


def boundary_prefix_is_header_only(text: str, boundary: int) -> bool:
    """Return True when text before a boundary is only the repeated PDF header."""
    normalized = search_text(text)
    header_len = page_header_length(text)
    prefix = normalized[header_len:boundary]
    return re.search(r"[\u4e00-\u9fffA-Za-z0-9]", prefix) is None


START_RULES: list[StartRule] = [
    StartRule(
        rule_id="proxy_establishment_context",
        event_family="establishment",
        pattern=re.compile(
            r"1、代持基本情况"
            r"(?=.{0,500}(?:共同出资设立|出资设立)[^。；]{0,120}(?:有限|有限公司))"
        ),
        title_template="有限公司设立及股权代持形成",
        end_pattern=re.compile(r"2、代持解除情况"),
    ),
    StartRule(
        rule_id="proxy_release_transfer",
        event_family="share_transfer_flow",
        pattern=re.compile(
            r"2、代持解除情况"
            r"(?=.{0,900}(?:转让予|转让给).{0,260}(?:股权|注册资本))"
        ),
        title_template="股权代持解除转让",
        end_pattern=re.compile(
            r"3、是否存在纠纷或潜在纠纷"
            r"|六、股权激励等可能导致发行人股权结构变化的事项"
        ),
    ),
    StartRule(
        rule_id="single_directed_issue_section",
        event_family="subscription_flow",
        pattern=re.compile(
            r"（[一二三四五六七八九十]+）报告期内发行融资情况"
            r"(?=.{0,180}共进行过一次(?:股票)?定向发行)"
        ),
        title_template="报告期内一次股票定向发行",
        end_pattern=re.compile(
            r"（[九十]+）报告期内重大资产重组情况"
        ),
    ),
    StartRule(
        rule_id="financial_note_registered_capital_increase",
        event_family="subscription_flow",
        pattern=re.compile(
            r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日，?"
            r"[^。；]{0,180}注册资本由(?P<before>[\d,，.]+)万元"
            r"增(?:加)?至(?P<after>[\d,，.]+)万元"
        ),
        title_template="{year}年{month}月增资至{after}万元",
        end_pattern=re.compile(
            r"\d{4}年\d{1,2}月\d{1,2}日，?公司召开创立大会"
            r"|\d{4}年\d{1,2}月，?公司完成定向发行"
        ),
    ),
    StartRule(
        rule_id="financial_note_overall_conversion",
        event_family="overall_conversion",
        pattern=re.compile(
            r"(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日，?"
            r"公司召开创立大会"
            r"(?=.{0,700}整体变更设立为股份公司)"
        ),
        title_template="{year}年整体变更设立股份公司",
        end_pattern=re.compile(
            r"\d{4}年\d{1,2}月，?公司完成定向发行"
        ),
    ),
    StartRule(
        rule_id="overview_establishment_date",
        event_family="establishment",
        pattern=re.compile(
            r"有限公司成立日期(?P<date>\d{4}年\d{1,2}月\d{1,2}日)"
            r"(?=.{0,120}股份公司成立日期)"
        ),
        title_template="{date}有限公司成立日期概览",
        end_pattern=re.compile(r"股份公司成立日期"),
    ),
    StartRule(
        rule_id="overview_overall_conversion_date",
        event_family="overall_conversion",
        pattern=re.compile(
            r"股份公司成立日期(?P<date>\d{4}年\d{1,2}月\d{1,2}日)"
        ),
        title_template="{date}整体变更设立股份公司日期概览",
        end_pattern=re.compile(r"注册资本"),
    ),
    StartRule(
        rule_id="yearly_stock_issue",
        event_family="subscription_flow",
        pattern=re.compile(
            r"(?P<ordinal>\d+)、(?P<year>\d{4})年股票发行"
        ),
        title_template="{year}年股票发行",
        end_pattern=re.compile(
            r"\d+、\d{4}年股票发行"
            r"|（九）报告期内重大资产重组情况"
        ),
    ),
    StartRule(
        rule_id="report_period_distribution_capitalization",
        event_family="capitalization",
        pattern=re.compile(
            r"（十一）报告期内股利分配情况"
            r"(?=.{0,900}资本公积.{0,180}转增)"
        ),
        title_template="报告期内资本公积转增股本",
        end_pattern=re.compile(r"三、发行人的股权结构"),
    ),
    StartRule(
        rule_id="issuer_proxy_release_context",
        event_family="historical_context",
        pattern=re.compile(r"发行人历史沿革中存在股权代持情况"),
        title_template="历史股权代持解除汇总",
        end_pattern=re.compile(r"六、股权激励等可能导致发行人股权结构变化的事项"),
    ),
    StartRule(
        rule_id="bse_top_shareholders_snapshot",
        event_family="equity_snapshot",
        pattern=re.compile(r"（二）本次发行前公司前十名股东情况"),
        title_template="本次发行前公司前十名股东情况",
        end_pattern=re.compile(r"（三）主要股东间关联关系的具体情况"),
    ),
    StartRule(
        rule_id="overall_conversion_establishment_method",
        event_family="overall_conversion",
        pattern=re.compile(
            r"(?:（[一二三四五六七八九十]+）|\d+、)发行人的设立方式"
            r"(?=.{0,2200}整体变更(?:设立|为)股份有限公司)"
        ),
        title_template="整体变更设立股份有限公司",
        end_pattern=re.compile(
            r"(?:（[一二三四五六七八九十]+）|\d+、)"
            r"[^，。；\n]{0,24}有限(?:公司)?的设立情况"
        ),
    ),
    StartRule(
        rule_id="historical_overview_general",
        event_family="historical_overview",
        pattern=re.compile(
            r"(?:（[一二三四五六七八九十]+）|\d+、)"
            r"[^，。；\n]{0,32}设立以来股本演变情况(?:概图)?"
        ),
        title_template="设立以来股本演变概图",
        end_pattern=re.compile(
            r"[一二三四五六七八九十]+、发行人股份公司设立后的股东变化情况"
            r"|\d+、发行人报告期内(?:的)?股本演变情况"
        ),
    ),
    StartRule(
        rule_id="dated_parenthetical_share_transfer",
        event_family="share_transfer_flow",
        pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）"
            r"(?P<year>\d{4})年(?P<month>\d{1,2})月，?"
            r"(?:股份有限公司|股份公司)?第(?P<ordinal>[一二三四五六七八九十0-9]+)次股权转让"
        ),
        title_template="{year}年{month}月第{ordinal}次股权转让",
        end_pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）\d{4}年\d{1,2}月(?:至\d{1,2}月)?，?"
            r"(?:股份有限公司|股份公司)?(?:增加注册资本暨第[一二三四五六七八九十0-9]+次股权转让|第[一二三四五六七八九十0-9]+次(?:增资|股权转让)|第[一二三四五六七八九十0-9]+次增资和第[一二三四五六七八九十0-9]+次股权转让)"
        ),
    ),
    StartRule(
        rule_id="combined_capital_increase_transfer_subscription",
        event_family="subscription_flow",
        pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）"
            r"(?P<year>\d{4})年(?P<month>\d{1,2})月至(?P<end_month>\d{1,2})月，?"
            r"(?:股份有限公司|股份公司)?增加注册资本暨第(?P<ordinal>[一二三四五六七八九十0-9]+)次股权转让"
        ),
        title_template="{year}年{month}月至{end_month}月增加注册资本",
        end_pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）\d{4}年\d{1,2}月，?"
            r"(?:股份有限公司|股份公司)?第[一二三四五六七八九十0-9]+次(?:增资|股权转让)"
        ),
    ),
    StartRule(
        rule_id="combined_capital_increase_transfer_transfer",
        event_family="share_transfer_flow",
        pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）"
            r"(?P<year>\d{4})年(?P<month>\d{1,2})月至(?P<end_month>\d{1,2})月，?"
            r"(?:股份有限公司|股份公司)?增加注册资本暨第(?P<ordinal>[一二三四五六七八九十0-9]+)次股权转让"
        ),
        title_template="{year}年{month}月至{end_month}月第{ordinal}次股权转让",
        end_pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）\d{4}年\d{1,2}月，?"
            r"(?:股份有限公司|股份公司)?第[一二三四五六七八九十0-9]+次(?:增资|股权转让)"
        ),
    ),
    StartRule(
        rule_id="dated_parenthetical_subscription",
        event_family="subscription_flow",
        pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）"
            r"(?P<year>\d{4})年(?P<month>\d{1,2})月，?"
            r"(?:股份有限公司|股份公司)?第(?P<ordinal>[一二三四五六七八九十0-9]+)次增资"
            r"(?!和第[一二三四五六七八九十0-9]+次股权转让)"
        ),
        title_template="{year}年{month}月第{ordinal}次增资",
        end_pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）\d{4}年\d{1,2}月，?"
            r"(?:股份有限公司|股份公司)?(?:第[一二三四五六七八九十0-9]+次增资和第[一二三四五六七八九十0-9]+次股权转让|第[一二三四五六七八九十0-9]+次(?:增资|股权转让))"
        ),
    ),
    StartRule(
        rule_id="combined_subscription_transfer_subscription",
        event_family="subscription_flow",
        pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）"
            r"(?P<year>\d{4})年(?P<month>\d{1,2})月，?"
            r"(?:股份有限公司|股份公司)?第(?P<sub_ordinal>[一二三四五六七八九十0-9]+)次增资和第(?P<transfer_ordinal>[一二三四五六七八九十0-9]+)次股权转让"
        ),
        title_template="{year}年{month}月第{sub_ordinal}次增资",
        end_pattern=re.compile(r"（[一二三四五六七八九十0-9]+）对赌协议解除相关情况"),
    ),
    StartRule(
        rule_id="combined_subscription_transfer_transfer",
        event_family="share_transfer_flow",
        pattern=re.compile(
            r"（[一二三四五六七八九十0-9]+）"
            r"(?P<year>\d{4})年(?P<month>\d{1,2})月，?"
            r"(?:股份有限公司|股份公司)?第(?P<sub_ordinal>[一二三四五六七八九十0-9]+)次增资和第(?P<transfer_ordinal>[一二三四五六七八九十0-9]+)次股权转让"
        ),
        title_template="{year}年{month}月第{transfer_ordinal}次股权转让",
        end_pattern=re.compile(r"（[一二三四五六七八九十0-9]+）对赌协议解除相关情况"),
    ),
    StartRule(
        rule_id="limited_company_establishment",
        event_family="establishment",
        pattern=re.compile(
            r"(?:（[一二三四五六七八九十]+）|\d+、)"
            r"(?:公司前身[^，。；]{0,20})?"
            r"(?:有限责任公司|有限公司|[^，。；]{1,12}有限)(?:的)?设立情况"
        ),
        title_template="有限公司设立及初始出资",
        end_pattern=re.compile(
            r"(?:（[一二三四五六七八九十]+）|\d+、)"
            r"(?:股份有限公司|股份公司)(?:的)?设立情况"
        ),
    ),
    StartRule(
        rule_id="overall_conversion",
        event_family="overall_conversion",
        pattern=re.compile(
            r"(?:（[一二三四五六七八九十]+）|\d+、)"
            r"(?:股份有限公司|股份公司)(?:的)?设立情况"
        ),
        title_template="整体变更设立股份有限公司",
        end_pattern=re.compile(
            r"（[一二三四五六七八九十]+）"
            r"(?:(?:发行人)?报告期内(?:的)?股本和股东(?:的)?变化情况"
            r"|公司报告期内历次股本变化(?:情况)?)"
        ),
    ),
    StartRule(
        rule_id="opening_equity_snapshot",
        event_family="equity_snapshot",
        pattern=re.compile(
            r"(?:\d+、)?报告期初(?:，)?(?:公司)?"
            r"(?:注册资本[^。；]{0,50})?(?:其)?股权结构(?:情况)?如下"
            r"|(?:\d+、)?报告期初公司的股权结构"
        ),
        title_template="报告期初股权结构",
        end_pattern=re.compile(
            r"(?:\d+、)?\d{4}年\d{1,2}月，?"
            r"(?:报告期内)?第[一二三四五六七八九十0-9]+次股权转让"
            r"|报告期内的股本和股东变化情况如下"
        ),
    ),
    StartRule(
        rule_id="historical_overview",
        event_family="historical_overview",
        pattern=re.compile(r"1、[^，。\n]{0,24}设立以来股本演变情况概图"),
        title_template="设立以来股本演变概图",
        end_pattern=re.compile(r"2、发行人报告期内(?:的)?股本演变情况"),
    ),
    StartRule(
        rule_id="dated_absorption_merger",
        event_family="subscription_flow",
        pattern=re.compile(
            r"(?:\d+、)?(?P<year>\d{4})年(?P<month>\d{1,2})月，?吸收合并"
        ),
        title_template="{year}年{month}月吸收合并并增加注册资本",
        end_pattern=re.compile(
            r"(?:\d+、)?\d{4}年\d{1,2}月，?"
            r"(?:第[一二三四五六七八九十0-9]+次增资|整体变更为股份公司)"
            r"|[一二三四五六七八九十]+、发行人成立以来的重要事件"
        ),
    ),
    StartRule(
        rule_id="dated_issuer_share_transfer_heading",
        event_family="share_transfer_flow",
        pattern=re.compile(
            r"(?:\d+、)?(?P<year>\d{4})年(?P<month>\d{1,2})月，?"
            r"(?:股份有限公司|股份公司)?"
            r"第(?P<ordinal>[一二三四五六七八九十0-9]+)次股权转让"
        ),
        title_template="{year}年{month}月第{ordinal}次股权转让",
        end_pattern=re.compile(
            r"(?:\d+、)?\d{4}年\d{1,2}月，?"
            r"(?:吸收合并|第[一二三四五六七八九十0-9]+次增资|整体变更为股份公司)"
            r"|（[一二三四五六七八九十0-9]+）公司历次股本验资情况"
        ),
    ),
    StartRule(
        rule_id="report_period_subscription",
        event_family="subscription_flow",
        pattern=re.compile(
            r"(?:\d+、)?(?P<year>\d{4})年(?P<month>\d{1,2})月，?"
            r"(?:报告期内)?(?:股份有限公司)?"
            r"第(?P<ordinal>[一二三四五六七八九十0-9]+)次增资"
        ),
        title_template="{year}年{month}月第{ordinal}次增资",
        end_pattern=re.compile(
            r"（\d+）股份支付情况"
            r"|(?:\d+、)?\d{4}年\d{1,2}月，?"
            r"(?:整体变更为股份公司|第[一二三四五六七八九十0-9]+次(?:增资|股权转让)|吸收合并|第[一二三四五六七八九十0-9]+次资本公积(?:金)?转增股本)"
            r"|[一二三四五六七八九十]+、发行人成立以来的重要事件"
        ),
    ),
    StartRule(
        rule_id="report_period_subscription_subheading",
        event_family="subscription_flow",
        pattern=re.compile(
            r"（[0-9一二三四五六七八九十]+）"
            r"报告期内第(?P<ordinal>[一二三四五六七八九十0-9]+)次增资"
        ),
        title_template="报告期内第{ordinal}次增资",
        end_pattern=re.compile(
            r"(?:\d+、)?\d{4}年\d{1,2}月，?"
            r"(?:报告期内)?第[一二三四五六七八九十0-9]+次股权转让"
        ),
    ),
    StartRule(
        rule_id="report_period_transfer_subheading",
        event_family="share_transfer_flow",
        pattern=re.compile(
            r"（[0-9一二三四五六七八九十]+）"
            r"报告期内第(?P<ordinal>[一二三四五六七八九十0-9]+)次股权转让"
            r"(?!及第[一二三四五六七八九十0-9]+次增资)"
        ),
        title_template="报告期内第{ordinal}次股权转让",
        end_pattern=re.compile(
            r"（[0-9一二三四五六七八九十]+）"
            r"报告期内第[一二三四五六七八九十0-9]+次增资"
        ),
    ),
    StartRule(
        rule_id="dated_report_period_transfer",
        event_family="share_transfer_flow",
        pattern=re.compile(
            r"\d+、(?P<year>\d{4})年(?P<month>\d{1,2})月，?"
            r"报告期内第(?P<ordinal>[一二三四五六七八九十0-9]+)次股权转让"
            r"(?!及第[一二三四五六七八九十0-9]+次增资)"
        ),
        title_template="{year}年{month}月报告期内第{ordinal}次股权转让",
        end_pattern=re.compile(
            r"\d+、\d{4}年\d{1,2}月，?"
            r"(?:整体变更为股份公司|报告期内第[一二三四五六七八九十0-9]+次增资)"
        ),
    ),
    StartRule(
        rule_id="capitalization",
        event_family="capitalization",
        pattern=re.compile(
            r"\d+、(?P<year>\d{4})年(?P<month>\d{1,2})月，?"
            r"(?:报告期内)?第(?P<ordinal>[一二三四五六七八九十0-9]+)次"
            r"资本公积(?:金)?转增股本"
        ),
        title_template="{year}年{month}月第{ordinal}次资本公积转增股本",
        end_pattern=re.compile(r"[一二三四五六七八九十]+、公司成立以来重要事件"),
    ),
    StartRule(
        rule_id="dated_subscription",
        event_family="subscription_flow",
        pattern=re.compile(
            r"\d+、(?P<year>\d{4})年(?P<month>\d{1,2})月，?"
            r"增资至(?P<capital>[\d,，.]+)万元"
        ),
        title_template="{year}年{month}月增资至{capital}万元",
        end_pattern=re.compile(
            r"（[一二三四五六七八九十]+）发行人与股东之间的特殊权益安排"
        ),
    ),
    StartRule(
        rule_id="issuer_share_transfer",
        event_family="share_transfer_flow",
        pattern=re.compile(
            r"\d+[）)](?P<date>\d{4}年\d{1,2}月\d{1,2}日)，?"
            r"(?P<transferor>[^，。\n]{1,30})向(?P<transferee>[^，。\n]{1,30})"
            r"转让其所持有的(?P<target>友升有限|友升股份|发行人|本公司)"
            r"(?P<ratio>[\d.]+%)股权"
        ),
        title_template="{date}{transferor}向{transferee}转让{target}{ratio}股权",
        end_pattern=re.compile(r"（\d+）股权代持原因"),
    ),
    StartRule(
        rule_id="issuer_organization_relationship_snapshot",
        event_family="equity_snapshot",
        pattern=re.compile(
            r"[一二三四五六七八九十]+、发行人的组织结构"
            r"（一）公司的股权结构图"
        ),
        title_template="招股说明书签署日发行人股权关系图",
        end_pattern=re.compile(r"（二）公司的组织结构图"),
    ),
    StartRule(
        rule_id="issuer_relationship_snapshot",
        event_family="equity_snapshot",
        pattern=re.compile(
            r"[一二三四五六七八九十]+、(?:发行人|公司)的股权结构"
            r"(?:(?:及|和)组织结构(?:（一）(?:公司(?:的)?|发行人(?:的)?)?股权结构(?:图)?)?)?"
        ),
        title_template="招股说明书签署日发行人股权关系图",
        end_pattern=re.compile(
            r"[一二三四五六七八九十]+、发行人"
            r"(?:控股子公司、分公司及参股公司情况|控股和参股公司情况|股东及实际控制人情况)"
            r"|（二）组织结构(?:图)?"
        ),
    ),
    StartRule(
        rule_id="company_relationship_snapshot",
        event_family="equity_snapshot",
        pattern=re.compile(
            r"[一二三四五六七八九十]+、公司股权关系与内部组织结构"
            r"（一）公司股权关系"
        ),
        title_template="招股说明书签署日发行人完整股东结构",
        end_pattern=re.compile(r"（二）公司内部组织结构"),
    ),
    StartRule(
        rule_id="recent_proxy_release_context",
        event_family="historical_context",
        pattern=re.compile(r"（五）最近一年公司新增股东情况"),
        title_template="申报前十二个月股权代持解除汇总",
        end_pattern=re.compile(r"（六）本次发行前各股东间的关联关系"),
    ),
    StartRule(
        rule_id="pre_post_ipo_snapshot",
        event_family="equity_snapshot",
        pattern=re.compile(r"（一）(?:本次)?发行前后(?:的)?股本(?:情况|结构)"),
        title_template="本次发行前后股本结构",
        end_pattern=re.compile(
            r"（二）(?:本次发行前后公司|本次发行前公司|本次发行前的|发行前本公司)?前十名股东(?:情况)?"
        ),
    ),
    StartRule(
        rule_id="top_natural_shareholders_snapshot",
        event_family="equity_snapshot",
        pattern=re.compile(
            r"（三）本次发行前后公司前十名自然人股东情况"
        ),
        title_template="本次发行前后前十名自然人股东情况",
        end_pattern=re.compile(
            r"（四）有关公司股本中的国有股份或外资股份的说明"
        ),
    ),
    StartRule(
        rule_id="complete_pre_ipo_shareholders",
        event_family="equity_snapshot",
        pattern=re.compile(
            r"（二）(?:本次发行前的)?前十名股东"
            r"(?=.{0,160}(?:本次发行前，?公司共有\d+名股东|公司共有\d+名股东))"
        ),
        title_template="本次发行前完整股东结构",
        end_pattern=re.compile(
            r"（三）(?:本次发行前的)?前十名自然人股东"
            r"|（[三四五六七八九十]+）本次发行前各股东间的关联关系"
        ),
    ),
    StartRule(
        rule_id="complete_pre_ipo_shareholders_total_100",
        event_family="equity_snapshot",
        pattern=re.compile(
            r"（二）本次发行前的前十名股东(?:情况)?"
            r"(?=.{0,900}合计[\d,，.]+100(?:[.]0+)?%)"
        ),
        title_template="本次发行前完整股东结构",
        end_pattern=re.compile(
            r"（三）本次发行前的前十名自然人股东"
            r"|（[三四五六七八九十]+）本次发行前各股东间的关联关系"
        ),
    ),
]


SUPPORT_RULES: list[tuple[str, re.Pattern[str], re.Pattern[str]]] = [
    (
        "establishment",
        re.compile(r"（六）发行人设立时存在部分实缴出资超期的情况"),
        re.compile(r"三、发行人成立以来重要事件"),
    ),
    (
        "subscription_flow",
        re.compile(r"（五）申报前十二个月新增股东的基本情况"),
        re.compile(r"（六）股东私募投资基金备案情况"),
    ),
]


def load_selected_pages(log_path: Path, company_code: str) -> tuple[str, str, list[int]]:
    company_code = company_code.zfill(6)
    pages: list[int] = []
    company_name = ""
    pdf_filename = ""

    with log_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            code = row["company_code"].strip().zfill(6)
            if code != company_code:
                continue
            company_name = row["company_name"].strip()
            pdf_filename = row["pdf_filename"].strip()
            if row["candidate_status"].strip() in KEEP_STATUSES:
                pages.append(int(row["pdf_file_page"]))

    if not pages:
        raise ValueError(f"No candidate/review pages found for company {company_code}")

    return company_name, pdf_filename, sorted(set(pages))


def extract_page_texts(pdf_path: Path, pages: Iterable[int]) -> dict[int, str]:
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    doc = fitz.open(pdf_path)
    result: dict[int, str] = {}
    try:
        for page_no in pages:
            if not 1 <= page_no <= len(doc):
                raise ValueError(f"Invalid page number {page_no}; PDF has {len(doc)} pages")
            result[page_no] = compact_text(doc[page_no - 1].get_text("text"))
    finally:
        doc.close()
    return result


def expand_selected_pages_with_relevant_neighbors(
    pdf_path: Path,
    selected_pages: list[int],
) -> list[int]:
    """
    Recover one-page context that the frozen page locator can miss.

    Expansion is deliberately narrow:
    - previous page containing a report-period stock-issue heading;
    - next page carrying an issuer equity diagram whose heading is at the
      bottom of the selected page;
    - next page containing an issuer-level historical proxy-holding release.
    """
    expanded = set(selected_pages)
    doc = fitz.open(pdf_path)
    try:
        cache: dict[int, str] = {}

        def normalized(page_no: int) -> str:
            if page_no not in cache:
                cache[page_no] = search_text(
                    compact_text(doc[page_no - 1].get_text("text"))
                )
            return cache[page_no]

        # Recover issuer/company equity-relationship headings that may sit on a
        # page dominated by VIE history or other non-event narrative. The page
        # locator can drop such a mixed title page, so scan the PDF for the
        # formal issuer-level heading and retain the following diagram page.
        relationship_heading = re.compile(
            r"[一二三四五六七八九十]+、(?:发行人|公司)的股权结构"
            r"(?:(?:及|和)组织结构(?:（一）(?:公司(?:的)?|发行人(?:的)?)?股权结构(?:图)?)?)?"
        )
        relationship_intro = re.compile(
            r"截至.{0,80}(?:公司|发行人)?股权结构如下(?:图)?"
        )
        for page_no in range(1, len(doc) + 1):
            current = normalized(page_no)
            if relationship_heading.search(current) and relationship_intro.search(current):
                expanded.add(page_no)
                if page_no < len(doc):
                    expanded.add(page_no + 1)

        # Recover short omitted issuer-history bundles between selected pages.
        # This covers a mixed page followed by one or more diagram/table continuation
        # pages, while remaining bounded to small gaps only.
        ordered = sorted(selected_pages)
        for left, right in zip(ordered, ordered[1:]):
            gap = right - left
            if 2 <= gap <= 5:
                intermediate = list(range(left + 1, right))
                texts = [normalized(page_no) for page_no in intermediate]
                has_history_heading = any(
                    re.search(
                        r"(?:（[一二三四五六七八九十0-9]+）|\d+、)"
                        r"(?:[^，。；\n]{0,28}有限(?:公司)?的设立情况|[^，。；\n]{0,32}设立以来股本演变情况(?:概图)?|\d{4}年\d{1,2}月(?:至\d{1,2}月)?，?(?:股份有限公司|股份公司)?(?:第[一二三四五六七八九十0-9]+次(?:股权转让|增资)|增加注册资本暨第[一二三四五六七八九十0-9]+次股权转让))",
                        page_text,
                    )
                    for page_text in texts
                )
                if has_history_heading:
                    expanded.update(intermediate)

        for page_no in list(selected_pages):
            current = normalized(page_no)

            if page_no > 1:
                previous = normalized(page_no - 1)
                if (
                    "报告期内发行融资情况" in previous
                    and re.search(r"\d+、\d{4}年股票发行", previous)
                ):
                    expanded.add(page_no - 1)

            if page_no < len(doc):
                following = normalized(page_no + 1)
                if (
                    current.endswith("三、发行人的股权结构")
                    or re.search(r"股权结构如下图(?:所示)?[：:]?$", current)
                    or current.endswith("（一）股权结构图")
                    or current.endswith("（一）公司的股权结构图")
                ):
                    expanded.add(page_no + 1)
                if (
                    "发行人历史沿革中存在股权代持情况" in following
                    or (
                        "股权代持已解除" in following
                        and "发行人" in following
                    )
                ):
                    expanded.add(page_no + 1)
    finally:
        doc.close()

    return sorted(expanded)


def shift_heading_only_start(
    start: dict,
    selected_pages: list[int],
    page_texts: dict[int, str],
) -> dict:
    """
    If a section heading is the final meaningful content on one page and the
    actual diagram/table is on the next physical page, use that next page as
    the primary package page while retaining the heading as the start anchor.
    """
    if start["rule"].rule_id not in {"issuer_relationship_snapshot", "issuer_organization_relationship_snapshot"}:
        return start

    current = search_text(page_texts[start["page"]])
    suffix = current[start["match_end"]:]
    next_page = start["page"] + 1
    if (
        re.search(r"[\u4e00-\u9fffA-Za-z0-9]", suffix) is None
        and next_page in page_texts
        and next_page in selected_pages
    ):
        shifted = dict(start)
        shifted["heading_page"] = start["page"]
        shifted["page"] = next_page
        shifted["char"] = page_header_length(page_texts[next_page])
        shifted["match_end"] = shifted["char"]
        shifted["force_full_page"] = True
        return shifted

    return start


def find_starts(page_texts: dict[int, str]) -> list[dict]:
    starts: list[dict] = []
    for page_no, text in page_texts.items():
        normalized = search_text(text)
        header_len = page_header_length(text)
        match_text = normalized[header_len:]

        for rule in START_RULES:
            for match in rule.pattern.finditer(match_text):
                absolute_start = match.start() + header_len
                absolute_end = match.end() + header_len
                left_context = normalized[max(0, absolute_start - 160):absolute_start]
                right_context = normalized[absolute_end:absolute_end + 40]
                sentence_tail = re.split(r"[。；]", left_context)[-1]
                if any(
                    term in sentence_tail
                    for term in ("参见", "详见", "具体情况见", "相关内容见")
                ):
                    continue

                # Summary timelines repeat event labels without transaction
                # evidence. Keep the detailed disclosure pages only.
                timeline_pos = normalized.rfind(
                    "股本和股东变化情况", 0, absolute_start
                )
                timeline_text = (
                    normalized[timeline_pos:] if timeline_pos >= 0 else ""
                )
                event_label_count = len(re.findall(
                    r"\d{4}年\d{1,2}月，?"
                    r"(?:第[一二三四五六七八九十0-9]+次股权转让|吸收合并|(?:股份有限公司)?第[一二三四五六七八九十0-9]+次增资|整体变更为股份公司)",
                    timeline_text,
                ))
                if (
                    rule.rule_id in {
                        "report_period_subscription",
                        "report_period_subscription_subheading",
                        "dated_report_period_transfer",
                        "dated_absorption_merger",
                        "dated_issuer_share_transfer_heading",
                    }
                    and "股本和股东变化情况" in normalized
                    and event_label_count >= 2
                    and not re.search(
                        r"召开股东会|签署.{0,30}(?:增资|股权转让)协议|新增注册资本|收到股东缴纳",
                        normalized,
                    )
                ):
                    continue
                groups = {k: v for k, v in match.groupdict().items() if v is not None}
                title = rule.title_template.format(**groups) if groups else rule.title_template
                starts.append(
                    {
                        "page": page_no,
                        "char": absolute_start,
                        "match_end": absolute_end,
                        "anchor": match.group(0),
                        "rule": rule,
                        "title": title,
                    }
                )
    fallback_rule_ids = {
        "proxy_establishment_context",
        "proxy_release_transfer",
        "financial_note_registered_capital_increase",
        "financial_note_overall_conversion",
    }

    # Prefer formal section headings over compact fallback disclosures.
    if any(
        item["rule"].event_family == "establishment"
        and item["rule"].rule_id not in fallback_rule_ids
        for item in starts
    ):
        starts = [
            item for item in starts
            if item["rule"].rule_id != "proxy_establishment_context"
        ]

    if any(
        item["rule"].event_family == "overall_conversion"
        and item["rule"].rule_id not in fallback_rule_ids
        for item in starts
    ):
        starts = [
            item for item in starts
            if item["rule"].rule_id != "financial_note_overall_conversion"
        ]

    # A financial-note capital increase is a fallback only. Suppress it when
    # a formal subscription heading for the same year is on the same or an
    # adjacent page.
    formal_subscriptions = [
        item for item in starts
        if item["rule"].event_family == "subscription_flow"
        and item["rule"].rule_id not in fallback_rule_ids
    ]
    filtered_starts: list[dict] = []
    for item in starts:
        if item["rule"].rule_id == "financial_note_registered_capital_increase":
            year_match = re.search(r"(\d{4})年", item["title"])
            year = year_match.group(1) if year_match else ""
            duplicate = any(
                abs(other["page"] - item["page"]) <= 1
                and year
                and year in other["title"]
                for other in formal_subscriptions
            )
            if duplicate:
                continue
        if item["rule"].rule_id == "proxy_release_transfer":
            duplicate = any(
                other is not item
                and other["rule"].event_family == "share_transfer_flow"
                and other["rule"].rule_id not in fallback_rule_ids
                and other["page"] == item["page"]
                for other in starts
            )
            if duplicate:
                continue
        filtered_starts.append(item)
    starts = filtered_starts

    # The generic dated issuer-transfer rule intentionally accepts optional
    # "股份公司" wording. When a parenthetical formal heading on the same
    # page has already matched, suppress the generic duplicate.
    parenthetical_transfer_pages = {
        item["page"]
        for item in starts
        if item["rule"].rule_id == "dated_parenthetical_share_transfer"
    }
    starts = [
        item for item in starts
        if not (
            item["rule"].rule_id == "dated_issuer_share_transfer_heading"
            and item["page"] in parenthetical_transfer_pages
        )
    ]

    rule_ids = {item["rule"].rule_id for item in starts}
    if "historical_overview_general" in rule_ids:
        starts = [
            item for item in starts
            if item["rule"].rule_id != "historical_overview"
        ]

    if "limited_company_establishment" in rule_ids:
        starts = [
            item for item in starts
            if item["rule"].rule_id != "overview_establishment_date"
        ]
    if "overall_conversion" in rule_ids or "overall_conversion_establishment_method" in rule_ids:
        starts = [
            item for item in starts
            if item["rule"].rule_id != "overview_overall_conversion_date"
        ]

    starts.sort(key=lambda item: (item["page"], item["char"]))
    return starts


def find_boundary(
    page_texts: dict[int, str],
    selected_pages: list[int],
    start: dict,
    next_start: dict | None,
) -> tuple[int, int, str]:
    """
    Find the first applicable end boundary.
    Returns (end_page, end_char_exclusive, end_anchor).
    """
    start_page = start["page"]
    start_rule: StartRule = start["rule"]

    if start.get("force_full_page"):
        normalized = search_text(page_texts[start_page])
        explicit = start_rule.end_pattern.search(normalized)
        anchor = explicit.group(0) if explicit else "image_page_boundary"
        return start_page, len(normalized), anchor

    for page_no in selected_pages:
        if page_no < start_page:
            continue

        normalized = search_text(page_texts[page_no])
        search_from = start["match_end"] if page_no == start_page else 0

        explicit = start_rule.end_pattern.search(normalized, search_from)
        explicit_pos = explicit.start() if explicit else None

        next_pos = None
        if next_start is not None and page_no == next_start["page"]:
            next_pos = next_start["char"]

        candidates = [pos for pos in (explicit_pos, next_pos) if pos is not None]
        if candidates:
            boundary = min(candidates)

            if explicit_pos is not None and boundary == explicit_pos:
                anchor = explicit.group(0)
            else:
                anchor = next_start["anchor"] if next_start else ""

            # If the boundary appears on a later page and everything before it
            # is only the repeated company/page header, close the package on the
            # previous selected page. Otherwise retain the meaningful prefix
            # (for example a continued table or “续上图”) on the boundary page.
            if (
                page_no > start_page
                and boundary_prefix_is_header_only(page_texts[page_no], boundary)
            ):
                # Some PDF layouts place an equity diagram visually on the
                # next page even though that page's extracted text starts with
                # the following subsection heading. Retain the image page.
                start_text = search_text(page_texts[start_page])
                if (
                    start_rule.rule_id in {"issuer_relationship_snapshot", "issuer_organization_relationship_snapshot"}
                    and page_no == start_page + 1
                    and "股权结构如下图所示" in start_text
                ):
                    return page_no, len(normalized), "image_page_boundary"

                current_index = selected_pages.index(page_no)
                previous_page = selected_pages[current_index - 1]
                previous_text = search_text(page_texts[previous_page])
                return previous_page, len(previous_text), anchor

            return page_no, boundary, anchor

        # Stop before a non-contiguous selected-page jump unless the package has
        # already reached an explicit boundary on the current page.
        index = selected_pages.index(page_no)
        if index + 1 < len(selected_pages):
            next_page_no = selected_pages[index + 1]
            if next_page_no > page_no + 1:
                return page_no, len(normalized), "selected_page_gap"

    final_page = selected_pages[-1]
    return final_page, len(search_text(page_texts[final_page])), "end_of_selected_pages"


def slice_primary_segments(
    page_texts: dict[int, str],
    selected_pages: list[int],
    start: dict,
    end_page: int,
    end_char: int,
) -> list[dict]:
    segments: list[dict] = []
    for page_no in selected_pages:
        if page_no < start["page"] or page_no > end_page:
            continue
        normalized = search_text(page_texts[page_no])
        char_start = start["char"] if page_no == start["page"] else 0
        char_end = end_char if page_no == end_page else len(normalized)
        if char_end <= char_start:
            continue
        segments.append(
            {
                "page": page_no,
                "role": "primary",
                "char_start": char_start,
                "char_end": char_end,
                "text": normalized[char_start:char_end],
            }
        )
    return segments


def build_primary_packages(
    company_code: str,
    company_name: str,
    selected_pages: list[int],
    page_texts: dict[int, str],
) -> list[CandidatePackage]:
    starts = find_starts(page_texts)
    if not starts:
        return []

    counters: dict[str, int] = {}
    packages: list[CandidatePackage] = []

    for idx, raw_start in enumerate(starts):
        start = shift_heading_only_start(
            raw_start, selected_pages, page_texts
        )
        next_start = next(
            (
                candidate for candidate in starts[idx + 1:]
                if (candidate["page"], candidate["char"])
                > (raw_start["page"], raw_start["char"])
            ),
            None,
        )
        end_page, end_char, end_anchor = find_boundary(
            page_texts, selected_pages, start, next_start
        )
        segments = slice_primary_segments(
            page_texts, selected_pages, start, end_page, end_char
        )
        if not segments:
            continue

        family = start["rule"].event_family
        counters[family] = counters.get(family, 0) + 1
        package_id = f"{company_code}_{family}_{counters[family]:03d}"
        primary_pages = sorted({segment["page"] for segment in segments})

        source_text = "\n\n".join(
            f"[PAGE {segment['page']}][PRIMARY]\n{segment['text']}"
            for segment in segments
        )

        needs_review = (
            start["rule"].rule_id in {
                "historical_overview",
                "historical_overview_general",
                "overview_establishment_date",
                "overview_overall_conversion_date",
                "issuer_proxy_release_context",
                "bse_top_shareholders_snapshot",
                "dated_absorption_merger",
            }
            or (
                start["rule"].rule_id in {"issuer_relationship_snapshot", "issuer_organization_relationship_snapshot"}
                and "股东名称" not in source_text
            )
        )

        packages.append(
            CandidatePackage(
                company_code=company_code,
                company_name=company_name,
                package_id=package_id,
                event_family=family,
                event_title=start["title"],
                start_page=primary_pages[0],
                end_page=primary_pages[-1],
                primary_pages=primary_pages,
                supporting_pages=[],
                all_pages=primary_pages.copy(),
                start_anchor=start["anchor"],
                end_anchor=end_anchor,
                source_segments=segments,
                source_text=source_text,
                issuer_bound=True,
                is_mixed_page=False,
                is_non_contiguous=False,
                needs_manual_review=needs_review,
                detection_reason=(
                    f"start_rule:{start['rule'].rule_id}"
                    + (
                        f";heading_shifted_from:{start['heading_page']}"
                        if "heading_page" in start
                        else ""
                    )
                ),
            )
        )

    return packages


def add_supporting_segments(
    packages: list[CandidatePackage],
    selected_pages: list[int],
    page_texts: dict[int, str],
) -> None:
    """
    Attach supplementary disclosures without changing the primary event boundary.

    Current generic relationships:
    - delayed establishment contributions -> establishment package;
    - pre-filing new-shareholder / pricing disclosures -> latest subscription.
    """
    for family, start_pattern, end_pattern in SUPPORT_RULES:
        candidates = [p for p in packages if p.event_family == family]
        if not candidates:
            continue
        target = candidates[0] if family == "establishment" else candidates[-1]

        support_start: tuple[int, int] | None = None
        support_end: tuple[int, int, str] | None = None

        for page_no in selected_pages:
            normalized = search_text(page_texts[page_no])
            if support_start is None:
                match = start_pattern.search(normalized)
                if match:
                    support_start = (page_no, match.start())
                    end_match = end_pattern.search(normalized, match.end())
                    if end_match:
                        support_end = (page_no, end_match.start(), end_match.group(0))
                        break
            else:
                end_match = end_pattern.search(normalized)
                if end_match:
                    support_end = (page_no, end_match.start(), end_match.group(0))
                    break

        if support_start is None:
            continue

        if support_end is None:
            support_end = (
                selected_pages[-1],
                len(search_text(page_texts[selected_pages[-1]])),
                "end_of_selected_pages",
            )

        start_page, start_char = support_start
        end_page, end_char, end_anchor = support_end
        new_segments: list[dict] = []

        for page_no in selected_pages:
            if page_no < start_page or page_no > end_page:
                continue
            normalized = search_text(page_texts[page_no])
            char_start = start_char if page_no == start_page else 0
            char_end = end_char if page_no == end_page else len(normalized)
            if char_end <= char_start:
                continue
            new_segments.append(
                {
                    "page": page_no,
                    "role": "supporting",
                    "char_start": char_start,
                    "char_end": char_end,
                    "text": normalized[char_start:char_end],
                }
            )

        # For subscription supplementary evidence, retain only pages that carry
        # the new-shareholder list or the explicit pricing rationale.
        if family == "subscription_flow":
            filtered: list[dict] = []
            for segment in new_segments:
                text = segment["text"]
                if (
                    "申报前十二个月新增股东" in text
                    or "新增股东入股原因、入股价格及定价依据" in text
                    or "本次增资作价" in text
                ):
                    filtered.append(segment)
            new_segments = filtered

        if not new_segments:
            continue

        support_pages = sorted({segment["page"] for segment in new_segments})
        target.supporting_pages = support_pages
        target.all_pages = sorted(set(target.primary_pages) | set(support_pages))
        target.is_non_contiguous = any(
            b > a + 1 for a, b in zip(target.all_pages, target.all_pages[1:])
        )
        target.source_segments.extend(new_segments)
        target.source_text += "\n\n" + "\n\n".join(
            f"[PAGE {segment['page']}][SUPPORTING]\n{segment['text']}"
            for segment in new_segments
        )
        target.detection_reason += f";support_rule:{family};support_end:{end_anchor}"


def append_support_page(
    package: CandidatePackage,
    page_no: int,
    text: str,
    reason: str,
) -> None:
    if page_no in package.primary_pages or page_no in package.supporting_pages:
        return

    normalized = search_text(text)
    package.source_segments.append(
        {
            "page": page_no,
            "role": "supporting",
            "char_start": 0,
            "char_end": len(normalized),
            "text": normalized,
        }
    )
    package.supporting_pages = sorted(set(package.supporting_pages) | {page_no})
    package.all_pages = sorted(set(package.primary_pages) | set(package.supporting_pages))
    package.is_non_contiguous = any(
        b > a + 1 for a, b in zip(package.all_pages, package.all_pages[1:])
    )
    package.source_text += (
        f"\n\n[PAGE {page_no}][SUPPORTING]\n{normalized}"
    )
    package.detection_reason += f";support:{reason}:{page_no}"


def add_v13_context_support(
    packages: list[CandidatePackage],
    pdf_path: Path,
) -> None:
    """
    Attach cross-section evidence for compact BSE prospectus disclosures.
    This step does not create new packages; it only enriches packages created
    by V1.3-specific start rules.
    """
    if not packages:
        return

    doc = fitz.open(pdf_path)
    try:
        page_texts = {
            page_no: compact_text(doc[page_no - 1].get_text("text"))
            for page_no in range(1, len(doc) + 1)
        }
        normalized = {
            page_no: search_text(text)
            for page_no, text in page_texts.items()
        }

        # Establishment overview -> issuer basic-information page.
        for package in packages:
            if "start_rule:overview_establishment_date" in package.detection_reason:
                for page_no, text in normalized.items():
                    if (
                        "第四节发行人基本情况" in text
                        and "发行人基本信息" in text
                        and "成立日期" in text
                    ):
                        append_support_page(
                            package, page_no, page_texts[page_no],
                            "issuer_basic_information"
                        )
                        break

        # Overall-conversion overview -> governance confirmation.
        for package in packages:
            if "start_rule:overview_overall_conversion_date" in package.detection_reason:
                for page_no, text in normalized.items():
                    if "自整体变更为股份有限公司以来" in text:
                        append_support_page(
                            package, page_no, page_texts[page_no],
                            "governance_conversion_confirmation"
                        )
                        break

        yearly_packages = [
            package for package in packages
            if "start_rule:yearly_stock_issue" in package.detection_reason
        ]
        for package in yearly_packages:
            match = re.search(r"(\d{4})年", package.event_title)
            if not match:
                continue
            year = match.group(1)
            for page_no, text in normalized.items():
                if (
                    year in text
                    and "科目具体情况及分析说明" in text
                    and (
                        "本次发行完成后公司注册资本" in text
                        or "本次发行完成后公司股本总额" in text
                    )
                ):
                    append_support_page(
                        package, page_no, page_texts[page_no],
                        f"{year}_financial_note"
                    )

        # New-shareholder and pricing disclosure belongs to the latest yearly issue.
        if yearly_packages:
            latest_package = max(
                yearly_packages,
                key=lambda package: int(
                    re.search(r"(\d{4})年", package.event_title).group(1)
                ),
            )
            start_page = None
            end_page = None
            for page_no, text in normalized.items():
                if (
                    start_page is None
                    and (
                        "申报前12个月新增股东情况" in text
                        or "申报前十二个月新增股东情况" in text
                    )
                ):
                    start_page = page_no
                    continue
                if (
                    start_page is not None
                    and page_no > start_page
                    and (
                        "（5）新增股东间" in text
                        or "2、直接持有发行人股份的私募投资基金" in text
                    )
                ):
                    end_page = page_no
                    break

            if start_page is not None:
                stop = end_page if end_page is not None else start_page + 2
                for page_no in range(start_page, stop):
                    append_support_page(
                        latest_package, page_no, page_texts[page_no],
                        "recent_shareholder_pricing"
                    )

        # Capitalization support from major-matters summary and financial note.
        for package in packages:
            if (
                "start_rule:report_period_distribution_capitalization"
                not in package.detection_reason
            ):
                continue
            for page_no, text in normalized.items():
                if (
                    "资本公积" in text
                    and re.search(r"每10股转增3[.]?8", text)
                    and (
                        "首次申报审计截止日后分红情况" in text
                        or (
                            "科目具体情况及分析说明" in text
                            and (
                                "本次发行完成后公司注册资本" in text
                                or "本次发行完成后公司股本总额" in text
                            )
                        )
                    )
                ):
                    append_support_page(
                        package, page_no, page_texts[page_no],
                        "capitalization_cross_check"
                    )

        # If the equity diagram heading was shifted from the previous page,
        # attach the overview page with direct-control percentages.
        for package in packages:
            if (
                ("start_rule:issuer_relationship_snapshot" in package.detection_reason
                 or "start_rule:issuer_organization_relationship_snapshot" in package.detection_reason)
                and "heading_shifted_from:" in package.detection_reason
            ):
                for page_no, text in normalized.items():
                    if (
                        "第二节概览" in text
                        and "控股股东" in text
                        and "实际控制人" in text
                        and "持股比例" in text
                    ):
                        append_support_page(
                            package, page_no, page_texts[page_no],
                            "overview_control_relationship"
                        )
                        break
    finally:
        doc.close()


def add_v14_context_support(
    packages: list[CandidatePackage],
    pdf_path: Path,
) -> None:
    """Attach non-contiguous evidence for V1.4 compact-disclosure rules."""
    if not packages:
        return

    doc = fitz.open(pdf_path)
    try:
        page_texts = {
            page_no: compact_text(doc[page_no - 1].get_text("text"))
            for page_no in range(1, len(doc) + 1)
        }
        normalized = {
            page_no: search_text(text)
            for page_no, text in page_texts.items()
        }

        for package in packages:
            if "start_rule:proxy_establishment_context" in package.detection_reason:
                for page_no, text in normalized.items():
                    if (
                        "第四节发行人基本情况" in text
                        and "发行人基本信息" in text
                        and "成立日期" in text
                    ):
                        append_support_page(
                            package,
                            page_no,
                            page_texts[page_no],
                            "issuer_basic_information",
                        )
                        break

            if "start_rule:financial_note_overall_conversion" in package.detection_reason:
                for page_no, text in normalized.items():
                    if page_no in package.primary_pages:
                        continue
                    if (
                        "整体变更为股份有限公司" in text
                        and ("盈余公积" in text or "未分配利润" in text)
                        and "所有者权益内部结转" in text
                    ):
                        append_support_page(
                            package,
                            page_no,
                            page_texts[page_no],
                            "conversion_equity_reclassification",
                        )
                        break

            if "start_rule:single_directed_issue_section" in package.detection_reason:
                for page_no, text in normalized.items():
                    if page_no in package.primary_pages:
                        continue
                    if (
                        "科目具体情况及分析说明" in text
                        and "完成定向发行" in text
                        and "计入股本" in text
                    ):
                        append_support_page(
                            package,
                            page_no,
                            page_texts[page_no],
                            "directed_issue_financial_note",
                        )
                    elif (
                        "股票发行基本情况" in text
                        and "股票定向发行" in text
                        and "募集资金总额" in text
                    ):
                        append_support_page(
                            package,
                            page_no,
                            page_texts[page_no],
                            "directed_issue_fundraising_note",
                        )
    finally:
        doc.close()


def finalize_mixed_page_flags(packages: list[CandidatePackage], page_texts: dict[int, str]) -> None:
    page_package_counts: dict[int, int] = {}
    for package in packages:
        for page_no in package.primary_pages:
            page_package_counts[page_no] = page_package_counts.get(page_no, 0) + 1

    for package in packages:
        segment_partial = False
        for segment in package.source_segments:
            page_len = len(search_text(page_texts[segment["page"]]))
            if segment["char_start"] > 0 or segment["char_end"] < page_len:
                segment_partial = True
                break
        package.is_mixed_page = segment_partial or any(
            page_package_counts.get(page_no, 0) > 1 for page_no in package.primary_pages
        )


def write_jsonl(path: Path, packages: list[CandidatePackage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for package in packages:
            f.write(json.dumps(asdict(package), ensure_ascii=False) + "\n")


def write_log(path: Path, packages: list[CandidatePackage]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "company_code",
        "company_name",
        "package_id",
        "event_family",
        "event_title",
        "start_page",
        "end_page",
        "primary_pages",
        "supporting_pages",
        "all_pages",
        "start_anchor",
        "end_anchor",
        "issuer_bound",
        "is_mixed_page",
        "is_non_contiguous",
        "needs_manual_review",
        "detection_reason",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for package in packages:
            row = asdict(package)
            row["primary_pages"] = "|".join(map(str, package.primary_pages))
            row["supporting_pages"] = "|".join(map(str, package.supporting_pages))
            row["all_pages"] = "|".join(map(str, package.all_pages))
            writer.writerow({key: row[key] for key in fields})


def resolve_default_pdf(project_root: Path, pdf_filename: str) -> Path:
    candidates = [
        project_root / "data" / "pdfs" / pdf_filename,
        project_root / "data" / "raw_pdfs" / pdf_filename,
        project_root / pdf_filename,
    ]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate candidate IPO equity-change event packages."
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--company-code", default="603418")
    parser.add_argument("--log-path", type=Path)
    parser.add_argument("--pdf-path", type=Path)
    parser.add_argument("--output-jsonl", type=Path)
    parser.add_argument("--output-log", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = args.project_root.resolve()
    company_code = args.company_code.zfill(6)

    log_path = args.log_path or (
        root / "outputs" / "logs" / "section_location_log_frozen_v1.csv"
    )
    company_name, pdf_filename, selected_pages = load_selected_pages(
        log_path, company_code
    )
    pdf_path = args.pdf_path or resolve_default_pdf(root, pdf_filename)

    output_jsonl = args.output_jsonl or (
        root / "outputs" / "candidates" / f"{company_code}_candidate_packages_v1.jsonl"
    )
    output_log = args.output_log or (
        root / "outputs" / "logs" / f"{company_code}_candidate_package_log_v1.csv"
    )

    selected_pages = expand_selected_pages_with_relevant_neighbors(
        pdf_path, selected_pages
    )
    page_texts = extract_page_texts(pdf_path, selected_pages)
    packages = build_primary_packages(
        company_code, company_name, selected_pages, page_texts
    )
    add_supporting_segments(packages, selected_pages, page_texts)
    add_v13_context_support(packages, pdf_path)
    add_v14_context_support(packages, pdf_path)
    finalize_mixed_page_flags(packages, page_texts)

    write_jsonl(output_jsonl, packages)
    write_log(output_log, packages)

    print(f"[OK] company={company_code} {company_name}")
    print(f"selected_pages={selected_pages}")
    print(f"packages={len(packages)}")
    for package in packages:
        print(
            f"  {package.package_id} | {package.event_family} | "
            f"primary={package.primary_pages} | support={package.supporting_pages}"
        )
    print(f"JSONL: {output_jsonl}")
    print(f"LOG:   {output_log}")


if __name__ == "__main__":
    main()
