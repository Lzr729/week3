from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import fitz

TARGET_GROUPS: dict[str, list[str]] = {
    "company_establishment": [
        "发行人设立", "公司设立", "有限公司设立", "股份公司设立",
        "注册资本已全部到位", "验资报告", "设立时股权结构",
    ],
    "historical_evolution": [
        "历史沿革", "股本形成", "股本演变", "股本变化",
        "股权演变", "股东变化", "注册资本变化",
    ],
    "subscription_flow": [
        "增资至", "增加注册资本", "新增注册资本", "新增股份",
        "认购新增股份", "增资扩股", "认购数量", "认购金额",
        "新增股本的认购情况",
    ],
    "share_transfer_flow": [
        "股权转让", "股份转让", "转让方", "受让方",
        "将其持有的", "转让其所持有的",
    ],
    "equity_snapshot": [
        "股权结构", "股东结构", "股本结构", "持股数量",
        "持股比例", "出资比例", "发行前股本结构",
        "发行前后股本结构", "股东名称",
    ],
    "overall_conversion": [
        "整体变更", "净资产折股", "折合股本", "变更设立为股份有限公司",
    ],
}

ACTION_TERMS = [
    "设立", "出资", "增资", "增加注册资本", "认购", "转让",
    "受让", "整体变更", "折股", "股本增加", "实缴",
]

NUMBER_TERMS = [
    "万元", "万股", "元/股", "%", "注册资本", "总股本",
    "认购数量", "认购金额", "持股比例", "出资额", "股权",
]

HIGH_PRIORITY_SECTION_TERMS = [
    "发行人基本情况",
    "发行人设立情况和报告期内股本、股东变化情况",
    "历次增资", "历次股权转让", "股本形成及变化",
    "发行人股本情况", "新增股东", "入股原因", "入股价格",
]

HARD_NEGATIVE_TERMS = [
    "风险因素", "募集资金用途", "募集资金运用", "战略配售",
    "投资者保护", "限售安排", "减持意向", "稳定股价",
    "现金流量分析", "经营活动产生的现金流量", "投资活动产生的现金流量",
    "公司治理", "内部控制", "独立性", "同业竞争",
    "董事、监事、高级管理人员", "人员简历", "薪酬情况", "对外投资情况",
    "股权激励", "股份支付", "员工持股平台", "合伙份额",
    "控股和参股公司情况", "子公司情况", "全资子公司",
    "声明", "备查文件", "附件",
    "承诺", "约束措施", "关联交易", "股利分配", "利润分配", "现金分红",
    "行政处罚", "违法违规", "规范运作", "独立持续经营",
    "资金占用及担保",
]

NON_TARGET_STRUCTURE_TERMS = [
    "执行事务合伙人", "合伙人出资结构", "合伙人名称",
    "私募基金备案", "认缴出资比例", "基金管理人",
    "合伙企业基本情况", "认缴出资额",
]

RULE_OR_PROMISE_TERMS = [
    "优先受让权", "限制出售权", "反稀释权", "清算权",
    "不得转让", "不转让或者委托他人管理", "减持承诺",
    "特殊股东权利", "锁定期限", "限售期",
]

TABLE_HEADER_TERMS = [
    "序号", "股东名称", "认购人", "认购数量", "认购金额",
    "出资额", "出资比例", "持股数量", "持股比例", "合计",
]


# R1：章节上下文继承。只在明确的发行人资本章节内传播，避免跨入子公司、
# 董监高、财务等后续章节。
SECTION_CONTEXT_RULES: dict[str, list[str]] = {
    "issuer_history": [
        "发行人设立及报告期内股本演变情况",
        "发行人设立及报告期内股本、股东变化情况",
        "发行人设立情况和报告期内股本、股东变化情况",
        "报告期内的股本和股东变化情况",
        "设立以来股本演变情况",
        "股本形成及变化",
        "股本演变情况",
        "公司设立及报告期内的股本和股东变化情况",
    ],
    "issuer_structure": [
        "发行人的股权结构",
        "发行人股本情况",
        "本次发行前后股本情况",
        "发行前后股本结构",
        "发行前股本结构",
        "公司股权关系",
        "公司股本情况",
        "本次发行前后的股本结构",
        "本次发行前后股本结构",
        "本次发行前后公司前十名自然人股东情况",
        "公司股东及持股情况如下",
    ],
}

# V2.3.2补丁1：这些是发行人自身资本章节标题，不得被
# “XX股本演变情况”等主体抽取规则误识别为其他企业。
ISSUER_SECTION_HEADING_PROTECTIONS = [
    "发行人设立及报告期内股本演变情况",
    "发行人设立及报告期内股本、股东变化情况",
    "发行人设立情况和报告期内股本、股东变化情况",
    "发行人的设立及历次股本变化",
    "发行人的设立及股本演变情况",
    "发行人设立以来股本演变情况",
    "发行人设立情况及股本演变情况",
    "发行人设立及股本演变情况",
    "公司设立及报告期内的股本和股东变化情况",
    "公司前身的设立情况",
]

# V2.3.3补丁1：发行人前身设立标题及其验资/工商证据提供者保护。
ISSUER_ESTABLISHMENT_HEADINGS = [
    "公司前身",
    "发行人前身",
    "有限公司的设立情况",
    "有限公司设立情况",
    "公司设立情况",
]

SECTION_STOP_TERMS = [
    "发行人控股子公司",
    "控股子公司、分公司及参股公司情况",
    "控股和参股公司情况",
    "董事、监事、高级管理人员",
    "业务与技术",
    "财务会计信息",
    "募集资金运用",
    "公司控股、参股公司基本情况",
    "公司成立以来重要事件",
]

# 章节上下文最多向后继承的页数，避免标题识别失败后无限传播。
MAX_SECTION_CONTEXT_PAGES = 12

# R2：从一个强事件锚点向前/向后最多吸收的连续页数。
MAX_EVENT_BUNDLE_DISTANCE = 3

CONTINUATION_TERMS = [
    "续上图", "转下图", "续表", "接上表", "承上表",
    "下表续", "（续）", "(续)", "具体如下", "情况如下",
]


# R3：识别页面主要描述的主体，避免把子公司、关联企业和其他主体
# 自身的历史沿革当作发行人资本变化。
LEGAL_ENTITY_PATTERN = re.compile(
    r"[\u4e00-\u9fa5A-Za-z0-9·（）()]{2,35}"
    r"(?:股份有限公司|有限责任公司|有限公司|合伙企业（有限合伙）|"
    r"合伙企业\\(有限合伙\\)|合伙企业|有限合伙)"
)

OTHER_ENTITY_RELATION_TERMS = [
    "股东与公司实际控制人的关系",
    "实际控制人与公司实际控制人的关系",
    "公司及实际控制人均未持有过",
    "公司及实际控制人均未持有",
    "双方在历史沿革方面相互独立",
    "不存在直接或间接持有",
    "关联方名称",
    "关联关系",
]

OTHER_ENTITY_HEADING_TERMS = [
    "公司名称", "法定代表人", "设立时间", "住所",
    "经营范围", "主营业务及主要客户、供应商",
]

# R4：员工持股平台内部结构与发行人自身增资必须区分。
PLATFORM_INTERNAL_TERMS = [
    "员工持股平台", "合伙人", "执行事务合伙人",
    "合伙份额", "认缴出资额", "认缴出资比例",
    "普通合伙人", "有限合伙人",
]

# R5：这些内容可能出现“股权、增资、转让”等字样，但通常不是新的
# 已发生资本事件。
NON_EVENT_NARRATIVE_TERMS = [
    "股份支付费用", "股份支付会计处理", "公允价值",
    "等待期", "可行权条件", "会计准则",
    "滚存利润", "滚存未分配利润",
    "财务报表编制", "合并报表范围", "合并范围",
    "关联方", "关联关系",
    "锁定期满后", "减持方式", "减持价格",
]


# V2.3.1补丁：排除“（一）有限公司”等章节标题被误识别为企业名称。
PSEUDO_ENTITY_PATTERN = re.compile(
    r"^[（(]?[一二三四五六七八九十0-9]+[）)]?"
    r"(?:股份有限公司|有限责任公司|有限公司)$"
)

GENERIC_SUBJECT_TERMS = {
    "公司", "本公司", "发行人", "本次", "上述公司",
    "该公司", "标的公司", "股份有限公司", "有限公司",
}

# 页面主要内容若是股份支付或会计测算，即使引用历史增资，也不作为新的资本事件。
DOMINANT_NON_EVENT_HEADINGS = [
    "股份支付费用的计算依据",
    "股份支付费用",
    "股份支付会计处理",
    "公允价值的确定",
    "股份支付的会计处理",
    "滚存利润分配",
    "财务报表编制的基础",
    "合并报表范围",
]

DATE_PATTERN = re.compile(r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月)?")
PERCENT_OR_AMOUNT_PATTERN = re.compile(
    r"\d[\d,\.]*\s*(?:万股|万元|万美元|元/股|%|股)"
)


@dataclass
class PageResult:
    company_code: str
    company_name: str
    pdf_filename: str
    page_index: int
    pdf_file_page: int
    matched_categories: str
    matched_keywords: str
    issuer_alias_hits: str
    hard_negative_hits: str
    non_target_keywords: str
    rule_or_promise_keywords: str
    strong_event_hits: str
    score: int
    candidate_status: str
    selection_source: str
    parent_hit_page: str
    expansion_reason: str
    section_context: str
    section_context_source: str
    entity_scope: str
    entity_scope_reason: str
    issuer_bound_event: str
    issuer_bound_event_reason: str
    event_evidence_count: int
    non_event_narrative: str
    text_length: int
    snippet: str


def normalize_line(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip()


def clean_page_text(raw_text: str, company_name: str, aliases: list[str]) -> str:
    specific_names = [name for name in aliases if name not in {"发行人", "本公司", "公司"}]
    if company_name not in specific_names:
        specific_names.append(company_name)

    cleaned_lines: list[str] = []
    for raw_line in raw_text.splitlines():
        line = normalize_line(raw_line)
        if not line:
            continue
        if re.fullmatch(r"\d{1,4}", line):
            continue
        if "招股说明书" in line and any(name and name in line for name in specific_names):
            continue
        cleaned_lines.append(line)
    return " ".join(cleaned_lines).strip()


def unique_hits(text: str, terms: Iterable[str]) -> list[str]:
    return sorted({term for term in terms if term and term in text})


def derive_aliases(company_name: str) -> list[str]:
    aliases = {company_name.strip()}
    for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
        if company_name.endswith(suffix):
            short_name = company_name.removesuffix(suffix).strip()
            if short_name:
                aliases.add(short_name)
    return sorted(aliases)


def load_alias_map(project_root: Path) -> dict[str, list[str]]:
    alias_path = project_root / "data" / "company_aliases.csv"
    alias_map: dict[str, list[str]] = {}
    if not alias_path.exists():
        return alias_map
    with alias_path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            code = row["company_code"].strip().zfill(6)
            alias = row["alias"].strip()
            if alias:
                alias_map.setdefault(code, []).append(alias)
    return alias_map


def match_groups(text: str) -> tuple[list[str], list[str]]:
    categories: list[str] = []
    keywords: list[str] = []
    for category, terms in TARGET_GROUPS.items():
        hits = unique_hits(text, terms)
        if hits:
            categories.append(category)
            keywords.extend(hits)
    return categories, sorted(set(keywords))


def is_toc_page(text: str, page_number: int) -> bool:
    section_hits = len(re.findall(r"第[一二三四五六七八九十]+节", text))
    numbered_item_hits = len(re.findall(r"[一二三四五六七八九十]+、", text))
    page_ref_hits = len(re.findall(r"\.{3,}|…{2,}|\s\d{1,3}\s", text))
    explicit_toc = "目 录" in text or "目录" in text[:80]
    dotted_leaders = text.count("...") >= 3 or text.count("……") >= 2
    early_dense_index = (
        page_number <= 15
        and (
            (section_hits >= 1 and numbered_item_hits >= 4)
            or dotted_leaders
        )
    )
    return explicit_toc or early_dense_index or (section_hits >= 4 and page_ref_hits >= 4)


def strong_event_signals(
    text: str,
    target_keywords: list[str],
    issuer_hits: list[str],
    aliases: list[str],
    section_context: str,
) -> list[str]:
    signals: list[str] = []
    action_hits = unique_hits(text, ACTION_TERMS)
    date_hit = bool(DATE_PATTERN.search(text))
    amount_hit = bool(PERCENT_OR_AMOUNT_PATTERN.search(text))

    if date_hit:
        signals.append("date")
    if action_hits:
        signals.append("action")
    if amount_hit:
        signals.append("amount_or_ratio")
    if issuer_hits:
        signals.append("issuer_alias")
    if target_keywords:
        signals.append("target_keyword")

    direct_transfer = bool(
        amount_hit
        and any(
            transfer_targets_issuer(
                clause,
                aliases,
                section_context,
            )
            for clause in split_clauses(text)
            if "转让" in clause or "受让" in clause
        )
    )
    if direct_transfer:
        signals.append("direct_transfer_signature")

    establishment = (
        ("验资报告" in text or "注册资本已全部到位" in text or "实缴出资" in text)
        and amount_hit
    )
    if establishment:
        signals.append("establishment_signature")

    conversion = (
        "整体变更" in text
        and ("净资产" in text or "折合股本" in text or "折股" in text)
    )
    if conversion:
        signals.append("conversion_signature")

    subscription = (
        ("增资" in text or "认购" in text)
        and amount_hit
        and ("注册资本" in text or "股本" in text or "认购数量" in text)
    )
    if subscription:
        signals.append("subscription_signature")

    capitalization = bool(
        ("资本公积转增" in text or "转增股本" in text)
        and (
            "共计转增股本" in text
            or "股本总数由" in text
            or "总股本为基数" in text
            or "股本为基数" in text
        )
    )
    if capitalization:
        signals.append("capitalization_signature")

    if has_explicit_issuer_establishment_heading(text):
        signals.append("issuer_establishment_heading")

    snapshot = (
        ("股东名称" in text or "股权结构" in text or "股本结构" in text)
        and ("持股比例" in text or "出资比例" in text or "持股数量" in text or "出资额" in text)
        and amount_hit
    )
    if snapshot:
        signals.append("snapshot_signature")

    return sorted(set(signals))


def has_strong_core_signature(signals: list[str]) -> bool:
    signature_terms = {
        "direct_transfer_signature",
        "establishment_signature",
        "conversion_signature",
        "subscription_signature",
        "snapshot_signature",
        "issuer_structure_heading",
        "issuer_establishment_heading",
        "capitalization_signature",
    }
    return bool(signature_terms.intersection(signals))


def has_explicit_issuer_structure_heading(text: str) -> bool:
    """
    识别明确的发行人股权/股本结构标题。

    V2.3.3.1：
    - 发行人专属标题本身即可确认页面属于发行人结构页；
    - 新增的泛化标题仍要求同页存在股东/股本结构证据，避免扩大误选。
    """
    head = text[:1600]

    issuer_specific_headings = [
        "发行人的股权结构",
        "发行前后股本结构",
        "发行前股本结构",
        "公司整体变更设立时的股权结构",
        "增资后的股权结构",
        "增资后股权结构",
        "发行人股本情况",
    ]
    if any(term in head for term in issuer_specific_headings):
        return True

    generic_structure_headings = [
        "公司股权关系",
        "公司股本情况",
        "本次发行前后的股本结构",
        "本次发行前后股本结构",
        "本次发行前后公司前十名自然人股东情况",
        "公司股东及持股情况如下",
    ]
    heading_hit = any(term in head for term in generic_structure_headings)
    structure_evidence = bool(
        (
            ("股东名称" in head or "股东姓名/名称" in head)
            and ("持股比例" in head or "股份数量" in head or "持股数量" in head)
        )
        or (
            ("股本总数" in head or "总股本" in head)
            and ("股" in head or "持股" in head)
        )
    )
    return heading_hit and structure_evidence


def has_explicit_issuer_establishment_heading(text: str) -> bool:
    """发行人或其前身设立章节，且同页存在注册资本/验资证据。"""
    head = text[:1000]
    heading_hit = (
        bool(re.search(
            r"(?:公司|发行人)前身[一-龥A-Za-z0-9·]{0,24}(?:的)?设立情况",
            head,
        ))
        or any(term in head for term in ISSUER_ESTABLISHMENT_HEADINGS)
    )
    evidence_hit = any(
        term in text
        for term in [
            "注册资本", "实收资本", "验资报告",
            "工商登记", "企业法人营业执照", "公司章程",
        ]
    )
    return heading_hit and evidence_hit


def discover_issuer_aliases_from_text(text: str) -> list[str]:
    """从“公司前身XX的设立情况/前身为XX”发现发行人历史名称。"""
    patterns = [
        r"(?:公司|发行人)前身(?:为|系)?\s*([一-龥A-Za-z0-9·]{2,24}?)(?:的设立情况|设立情况|，|。|系)",
        r"([一-龥A-Za-z0-9·]{2,24})(?:，|,)?系(?:公司|发行人)(?:的)?前身",
    ]
    found: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text[:1600]):
            name = match.group(1).strip(" ，。；：:（）()、")
            if name and name not in GENERIC_SUBJECT_TERMS:
                found.add(name)
    return sorted(found)


def starts_with_relation_table(text: str) -> bool:
    """关联关系续表不能冒充发行人股权快照续表。"""
    head = text[:900]
    return bool(
        "关联关系" in head
        and ("股东姓名/名称" in head or "股东名称" in head)
        and "发行前" not in head
        and "发行后" not in head
    )


def has_actual_event_clause(text: str) -> bool:
    patterns = [
        r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月)?[^。；]{0,120}(?:第一次增资|第[一二三四五六七八九十]+次增资|增资扩股|增资至|增加注册资本至|认购新增股份)",
        r"(?:19|20)\d{2}\s*年(?:\s*\d{1,2}\s*月)?[^。；]{0,120}(?:签署.{0,20}股权转让协议|将其持有的.{0,50}股权转让|转让其所持有的.{0,50}股权)",
        r"(?:验资报告|注册资本已全部到位|实缴出资)",
        r"整体变更.{0,80}(?:净资产|折合股本|折股)",
        r"股本由\s*[\d,\.]+\s*万元增加至\s*[\d,\.]+\s*万元",
        r"本次增资作价为\s*[\d,\.]+\s*元/股",
        r"(?:以|按).{0,50}(?:总股本|股本).{0,20}为基数.{0,80}(?:资本公积金?|资本公积).{0,50}(?:转增股本|向.{0,20}转增)",
        r"(?:股本总数|总股本)由\s*[\d,\.]+\s*股(?:变更为|增加至|增至)\s*[\d,\.]+\s*股",
    ]
    return any(re.search(pattern, text) for pattern in patterns)



def specific_aliases(aliases: list[str]) -> list[str]:
    return sorted({
        alias.strip()
        for alias in aliases
        if alias.strip() not in {"发行人", "本公司", "公司"}
        and len(alias.strip()) >= 2
    })


def is_pseudo_entity_name(entity: str) -> bool:
    compact = re.sub(r"\s+", "", entity)
    compact = compact.strip("，。；：:、")
    if PSEUDO_ENTITY_PATTERN.fullmatch(compact):
        return True

    # 章节标题残片，例如“一）有限公司”“二、股份有限公司”。
    if re.fullmatch(
        r"[一二三四五六七八九十0-9]+[）)、.]?"
        r"(?:股份有限公司|有限责任公司|有限公司)",
        compact,
    ):
        return True

    return False


def extract_other_entities(
    text: str,
    aliases: list[str],
) -> list[str]:
    """抽取页面中的其他法人/合伙企业名称，并过滤章节标题残片。"""
    issuer_names = specific_aliases(aliases)
    entities: set[str] = set()

    for match in LEGAL_ENTITY_PATTERN.finditer(text):
        entity = match.group(0).strip(
            " ，。；：:（）()、"
        )
        if not entity or is_pseudo_entity_name(entity):
            continue
        if any(
            issuer in entity or entity in issuer
            for issuer in issuer_names
        ):
            continue
        entities.add(entity)

    return sorted(entities)


def split_clauses(text: str) -> list[str]:
    return [
        clause.strip()
        for clause in re.split(r"[。；;\n]", text)
        if clause.strip()
    ]


def clause_mentions_issuer(
    clause: str,
    aliases: list[str],
) -> bool:
    return any(
        alias in clause
        for alias in specific_aliases(aliases)
    )


def is_protected_issuer_section_heading(text: str) -> bool:
    """识别发行人自身资本章节标题，防止标题残片被当作其他主体。"""
    compact = re.sub(r"\s+", "", text[:500])
    return any(
        re.sub(r"\s+", "", term) in compact
        for term in ISSUER_SECTION_HEADING_PROTECTIONS
    )


def extract_post_event_structure_subject(clause: str) -> str:
    """
    从“本次增资及股权转让后，XX出资结构如下”等结果状态句式中
    提取被描述主体。该主体可能不带完整公司后缀。
    """
    patterns = [
        r"(?:本次|上述)(?:增资|股权转让|股份转让)"
        r"(?:及|和|、)(?:增资|股权转让|股份转让)(?:完成)?后"
        r"[，,:：\s]*([一-龥A-Za-z0-9·]{2,24}?)"
        r"\s*(?:自身)?\s*(?:的)?\s*"
        r"(?:出资结构|股权结构|股东结构|股本结构)(?:如下|情况如下)",
        r"(?:本次|上述)(?:股权|股份)转让(?:完成)?后"
        r"[，,:：\s]*([一-龥A-Za-z0-9·]{2,24}?)"
        r"\s*(?:自身)?\s*(?:的)?\s*"
        r"(?:出资结构|股权结构|股东结构|股本结构)(?:如下|情况如下)",
        r"(?:本次|上述)增资(?:完成)?后"
        r"[，,:：\s]*([一-龥A-Za-z0-9·]{2,24}?)"
        r"\s*(?:自身)?\s*(?:的)?\s*"
        r"(?:股东及出资情况|出资结构|股权结构|股东结构|股本结构)"
        r"(?:如下|情况如下)",
    ]

    for pattern in patterns:
        match = re.search(pattern, clause)
        if match:
            return match.group(1).strip()
    return ""


def has_issuer_transfer_structure_evidence(
    text: str,
    aliases: list[str],
) -> bool:
    """识别转让前后发行人股东或股权结构发生变化的直接证据。"""
    subjects = ["发行人", "本公司"] + specific_aliases(aliases)
    for subject in subjects:
        escaped = re.escape(subject)
        patterns = [
            rf"{escaped}.{{0,12}}股东由.{{0,35}}(?:变更为|变为)",
            rf"(?:本次|上述)?(?:股权|股份)转让(?:完成)?后.{{0,45}}"
            rf"{escaped}.{{0,20}}(?:股权结构|股东结构|股本结构)"
            rf"(?:如下|为|变更为)",
            rf"(?:转让前后|转让完成前后).{{0,45}}{escaped}.{{0,20}}"
            rf"(?:股权结构|股东结构|股本结构)(?:如下|对比如下|为)",
        ]
        if any(re.search(pattern, text) for pattern in patterns):
            return True
    return False


def transfer_targets_issuer(
    clause: str,
    aliases: list[str],
    section_context: str,
) -> bool:
    """
    股权转让只有在转让标的明确是发行人股份/股权时才视为发行人直接事件。
    """
    if "转让" not in clause and "受让" not in clause:
        return False

    object_terms = r"(?:股权|股份|出资额|出资份额|注册资本)"
    issuer_names = specific_aliases(aliases)

    # 公司简称必须与“股权/股份/出资额”等转让标的紧密相邻。
    for issuer in issuer_names:
        escaped = re.escape(issuer)
        patterns = [
            rf"(?:将|拟将)?(?:其)?(?:持有的|所持有的){escaped}.{{0,18}}"
            rf"{object_terms}.{{0,25}}(?:转让|让与)",
            rf"(?:转让|受让)(?:其)?(?:持有的|所持有的)?"
            rf"{escaped}.{{0,18}}{object_terms}",
            rf"{escaped}.{{0,18}}{object_terms}.{{0,25}}(?:转让给|受让自)",
            rf"{escaped}.{{0,12}}股东由.{{0,35}}(?:变更为|变为)",
        ]
        if any(re.search(pattern, clause) for pattern in patterns):
            return True

    # “发行人/本公司”属于明确指代，可直接作为转让标的主体。
    explicit_generic_patterns = [
        rf"(?:将|拟将)?(?:其)?(?:持有的|所持有的)(?:发行人|本公司)"
        rf".{{0,18}}{object_terms}.{{0,25}}(?:转让|让与)",
        rf"(?:转让|受让)(?:其)?(?:持有的|所持有的)?"
        rf"(?:发行人|本公司).{{0,18}}{object_terms}",
        rf"(?:发行人|本公司).{{0,18}}{object_terms}.{{0,25}}"
        rf"(?:转让给|受让自)",
        r"(?:发行人|本公司).{0,12}股东由.{0,35}(?:变更为|变为)",
    ]
    if any(re.search(pattern, clause) for pattern in explicit_generic_patterns):
        return True

    # “公司”是弱指代：仅在发行人历史沿革章节、且句中没有其他法人名称时接受。
    if (
        section_context == "issuer_history"
        and not extract_other_entities(clause, aliases)
    ):
        bare_company_patterns = [
            rf"(?:将|拟将)?(?:其)?(?:持有的|所持有的)公司.{{0,18}}"
            rf"{object_terms}.{{0,25}}(?:转让|让与)",
            rf"公司.{{0,18}}{object_terms}.{{0,25}}(?:转让给|受让自)",
            r"公司.{0,12}股东由.{0,35}(?:变更为|变为)",
        ]
        if any(re.search(pattern, clause) for pattern in bare_company_patterns):
            return True

    return False


def extract_named_event_subject(
    clause: str,
) -> str:
    """
    从“温州三联注册资本由……”“黄山联鑫股本演变情况”等句式中提取主体。
    """
    patterns = [
        r"([一-龥A-Za-z0-9·]{2,18})注册资本由",
        r"([一-龥A-Za-z0-9·]{2,18})第[一二三四五六七八九十0-9]+次增资",
        r"([一-龥A-Za-z0-9·]{2,18})股本演变情况",
        r"([一-龥A-Za-z0-9·]{2,18})设立时(?:股权|出资)结构",
        r"([一-龥A-Za-z0-9·]{2,18})整体变更为",
    ]

    for pattern in patterns:
        match = re.search(pattern, clause)
        if not match:
            continue

        matched_phrase = re.sub(r"\s+", "", match.group(0))
        if any(
            matched_phrase in re.sub(r"\s+", "", term)
            or re.sub(r"\s+", "", term) in matched_phrase
            for term in ISSUER_SECTION_HEADING_PROTECTIONS
        ):
            continue

        subject = match.group(1)
        # 只保留最近的语义主体，去掉日期、序号和关系前缀。
        subject = re.split(
            r"[，,。；;：:\s]",
            subject,
        )[-1]
        subject = re.sub(
            r"^(?:本次|上述|原股东|新股东|子公司)",
            "",
            subject,
        )
        return subject.strip()

    return ""


def has_issuer_bound_event_clause(
    text: str,
    aliases: list[str],
    section_context: str,
) -> tuple[bool, str]:
    """
    判断资本动作是否真正绑定发行人。
    对股权转让额外校验被转让标的，避免把上层公司股权转让误判为发行人变化。
    """
    issuer_names = specific_aliases(aliases)

    if has_explicit_issuer_establishment_heading(text):
        return True, "issuer_establishment_heading"

    if has_issuer_transfer_structure_evidence(text, aliases):
        return True, "issuer_transfer_structure_change"

    strong_patterns = [
        r"(?:公司|本公司|发行人)的?(?:注册资本|股本)由\s*[\d,\.]+\s*万元"
        r"(?:增加至|增至)\s*[\d,\.]+\s*万元",
        r"(?:公司|本公司|发行人).{0,30}(?:召开|审议通过).{0,60}增资",
        r"新增注册资本由.{0,80}(?:认购|出资)",
        r"(?:公司|本公司|发行人).{0,50}整体变更为股份有限公司",
        r"(?:公司|本公司|发行人).{0,50}收到全体股东.{0,60}注册资本",
        r"(?:公司|本公司|发行人)发行前总股本",
        r"发行人股本由\s*[\d,\.]+\s*万元增加至\s*[\d,\.]+\s*万元",
        r"本次增资作价(?:为)?\s*[\d,\.]+\s*元/股",
        r"新增股东.{0,30}(?:入股原因|定价依据|入股价格)",
        r"(?:公司|本公司|发行人|[一-龥A-Za-z0-9·]{2,24}).{0,45}(?:召开|股东大会|决议同意).{0,80}(?:资本公积金?|资本公积).{0,40}(?:转增股本|向.{0,20}转增)",
        r"(?:以|按).{0,50}(?:总股本|股本).{0,20}为基数.{0,80}(?:资本公积金?|资本公积).{0,50}(?:转增股本|向.{0,20}转增)",
        r"(?:股本总数|总股本)由\s*[\d,\.]+\s*股(?:变更为|增加至|增至)\s*[\d,\.]+\s*股",
    ]

    for pattern in strong_patterns:
        if re.search(pattern, text):
            return True, "strong_issuer_capital_pattern"

    capital_actions = [
        "增资", "增加注册资本", "新增注册资本", "认购",
        "整体变更", "净资产折股", "折合股本",
        "实缴出资", "验资报告",
    ]

    for clause in split_clauses(text):
        # 转让必须单独核对转让标的。
        if "转让" in clause or "受让" in clause:
            if transfer_targets_issuer(
                clause,
                aliases,
                section_context,
            ):
                return True, "transfer_object_is_issuer"
            continue

        if not any(action in clause for action in capital_actions):
            continue

        has_amount = bool(
            PERCENT_OR_AMOUNT_PATTERN.search(clause)
        )
        has_date = bool(DATE_PATTERN.search(clause))
        specific_issuer = any(
            issuer in clause for issuer in issuer_names
        )
        generic_issuer = bool(
            re.search(
                r"(?:^|[，,：:])(?:公司|本公司|发行人)",
                clause,
            )
            and section_context
            in {"issuer_history", "issuer_structure"}
        )

        if specific_issuer and (has_amount or has_date):
            return True, "specific_alias_in_event_clause"

        if generic_issuer and has_amount:
            return True, "generic_issuer_in_capital_section"

    return False, ""


def detect_entity_scope(
    text: str,
    aliases: list[str],
    section_context: str,
    issuer_bound_event: bool,
    explicit_issuer_structure: bool,
    explicit_issuer_establishment: bool,
) -> tuple[str, str]:
    """
    返回：
    issuer / platform_internal / other_entity / unclear
    """
    issuer_names = specific_aliases(aliases)

    # V2.3.2补丁3：结果状态句中的明确主体优先于宽泛的
    # issuer_bound_event信号，避免“新增注册资本由……”等通用句式抢先绑定发行人。
    for clause in split_clauses(text):
        structure_subject = extract_post_event_structure_subject(
            clause
        )
        if not structure_subject:
            continue
        subject_is_issuer = (
            structure_subject in GENERIC_SUBJECT_TERMS
            or any(
                structure_subject in alias
                or alias in structure_subject
                for alias in issuer_names
            )
        )
        if not subject_is_issuer:
            return (
                "other_entity",
                "post_event_structure_subject:"
                f"{structure_subject}",
            )

    if explicit_issuer_structure:
        return "issuer", "explicit_issuer_structure_heading"

    if explicit_issuer_establishment:
        return "issuer", "explicit_issuer_establishment_heading"

    if issuer_bound_event:
        return "issuer", "issuer_bound_event_clause"

    if (
        section_context == "issuer_history"
        and is_protected_issuer_section_heading(text)
    ):
        return "issuer", "protected_issuer_section_heading"

    if any(term in text for term in OTHER_ENTITY_RELATION_TERMS):
        return (
            "other_entity",
            "relationship_or_independence_statement",
        )

    platform_hits = unique_hits(
        text,
        PLATFORM_INTERNAL_TERMS,
    )
    if (
        "员工持股平台" in text
        or "合伙份额" in text
        or len(platform_hits) >= 3
    ):
        return "platform_internal", "|".join(platform_hits)

    head = text[:900]
    other_entities = extract_other_entities(
        head,
        aliases,
    )
    heading_hits = unique_hits(
        head,
        OTHER_ENTITY_HEADING_TERMS,
    )

    if other_entities and len(heading_hits) >= 2:
        return (
            "other_entity",
            "entity_profile_heading:"
            + "|".join(other_entities[:3]),
        )

    for clause in split_clauses(text):
        subject = extract_named_event_subject(clause)
        if subject and subject not in GENERIC_SUBJECT_TERMS:
            subject_is_issuer = any(
                subject in alias or alias in subject
                for alias in issuer_names
            )
            if not subject_is_issuer:
                return (
                    "other_entity",
                    f"named_event_subject:{subject}",
                )

        if not any(
            action in clause
            for action in [
                "增资", "股权转让", "股份转让",
                "设立", "整体变更", "注册资本",
            ]
        ):
            continue

        clause_other_entities = extract_other_entities(
            clause,
            aliases,
        )
        has_issuer = clause_mentions_issuer(
            clause,
            aliases,
        )

        if clause_other_entities and not has_issuer:
            return (
                "other_entity",
                "other_entity_event_clause:"
                + "|".join(clause_other_entities[:3]),
            )

    return "unclear", ""


def has_non_event_narrative(
    text: str,
    issuer_bound_event: bool,
    explicit_issuer_structure: bool,
) -> tuple[bool, str]:
    head = text[:1200]
    dominant_hits = unique_hits(
        head,
        DOMINANT_NON_EVENT_HEADINGS,
    )

    # 主标题/页面前部以股份支付或会计测算为核心，即使引用历史增资也排除。
    if dominant_hits and not explicit_issuer_structure:
        return (
            True,
            "dominant_non_event:"
            + "|".join(dominant_hits),
        )

    hits = unique_hits(text, NON_EVENT_NARRATIVE_TERMS)
    if hits and not issuer_bound_event and not explicit_issuer_structure:
        return True, "|".join(hits)

    return False, ""


def event_evidence_count(
    text: str,
    issuer_bound_event: bool,
    explicit_issuer_structure: bool,
) -> int:
    evidence = 0
    if DATE_PATTERN.search(text):
        evidence += 1
    if unique_hits(text, ACTION_TERMS):
        evidence += 1
    if PERCENT_OR_AMOUNT_PATTERN.search(text):
        evidence += 1
    if issuer_bound_event:
        evidence += 2
    if explicit_issuer_structure:
        evidence += 2
    return evidence


def should_refresh_section_context(text: str, active_context: str) -> bool:
    """
    V2.3.3：资本章节按连续事件/表格动态续期，固定页数只作为兜底。
    """
    if not active_context:
        return False

    head = text[:1400]
    if active_context == "issuer_history":
        event_terms = [
            "第一次增资", "第二次增资", "第三次增资",
            "第一次股权转让", "第二次股权转让",
            "报告期内第一次", "报告期内第二次", "报告期内第三次",
            "本次增资完成后", "本次股权转让后",
            "整体变更为股份", "资本公积转增股本", "转增股本",
            "股本总数由", "注册资本增加",
        ]
        table_terms = [
            "股东姓名/名称", "持股数量", "持股比例",
            "认缴出资额", "实缴出资额", "认购股份",
        ]
        return bool(
            any(term in head for term in event_terms)
            or (len(unique_hits(head, table_terms)) >= 2 and any(
                term in text for term in ["合计", "本次增资", "股权结构如下"]
            ))
        )

    if active_context == "issuer_structure":
        return bool(
            has_explicit_issuer_structure_heading(text)
            or (
                not starts_with_relation_table(text)
                and len(unique_hits(
                    head,
                    ["股东名称", "股东姓名/名称", "持股数量", "持股比例", "发行前", "发行后"],
                )) >= 2
            )
        )

    return False


def detect_section_anchor(
    text: str,
    aliases: list[str],
) -> tuple[str, str]:
    """
    识别明确的发行人资本章节标题。

    “股本演变情况”等泛化标题必须在同一页开头出现发行人或发行人别名，
    避免把子公司、关联企业的股本演变误当作发行人章节。
    """
    head = text[:700]
    issuer_markers = ["发行人", "本公司"] + specific_aliases(aliases)
    has_issuer_marker = any(
        marker and marker in head
        for marker in issuer_markers
    )

    generic_terms = {
        "报告期内的股本和股东变化情况",
        "设立以来股本演变情况",
        "股本形成及变化",
        "股本演变情况",
    }

    for context_name, terms in SECTION_CONTEXT_RULES.items():
        for term in terms:
            if term not in head:
                continue
            if term in generic_terms and not has_issuer_marker:
                continue
            return context_name, term

    return "", ""


def major_heading_text(text: str) -> str:
    """提取页面开头附近的中文一级标题，用于终止旧章节上下文。"""
    head = text[:220]
    match = re.search(
        r"(?:^|\s)([一二三四五六七八九十]{1,3})、([^。；]{2,45})",
        head,
    )
    return match.group(0).strip() if match else ""


def should_stop_section_context(text: str, active_context: str) -> bool:
    if not active_context:
        return False

    head = text[:350]
    if any(term in head for term in SECTION_STOP_TERMS):
        return True

    heading = major_heading_text(text)
    if not heading:
        return False

    # 若本页本身出现新的目标章节锚点，由detect_section_anchor负责切换，
    # 此处只负责终止明显无关的新一级章节。
    target_heading_terms = [
        "发行人设立", "股本演变", "股本和股东变化",
        "发行人的股权结构", "发行人股本情况",
        "本次发行前后股本情况",
        "公司股权关系", "公司股本情况",
        "本次发行前后的股本结构",
    ]
    return not any(term in heading for term in target_heading_terms)


def continuation_signals(text: str) -> list[str]:
    """识别跨页图表、续表和表格行延续信号。"""
    signals = unique_hits(text, CONTINUATION_TERMS)
    header_hits = unique_hits(text, TABLE_HEADER_TERMS)

    if len(header_hits) >= 2:
        signals.append("table_like")

    incomplete, reason = incomplete_table_signal(text)
    if incomplete:
        signals.append(reason)

    # 页面开头直接出现“序号/姓名/股东/合伙人”等，通常是上一页表格续页。
    if re.match(
        r"^(?:序号|姓名|股东名称|股东姓名|合伙人|认购人|出资额|持股数量)",
        text,
    ):
        signals.append("starts_with_table_row")

    return sorted(set(signals))


def event_family(categories: list[str], signals: list[str], text: str) -> str:
    """给事件锚点分配粗粒度事件族，供跨页扩展使用。"""
    if "subscription_signature" in signals or "subscription_flow" in categories:
        return "subscription_flow"
    if "conversion_signature" in signals or "overall_conversion" in categories:
        return "overall_conversion"
    if "direct_transfer_signature" in signals or "share_transfer_flow" in categories:
        return "share_transfer_flow"
    if "establishment_signature" in signals or "company_establishment" in categories:
        return "company_establishment"
    if (
        "snapshot_signature" in signals
        or "issuer_structure_heading" in signals
        or "equity_snapshot" in categories
    ):
        return "equity_snapshot"
    if "股本演变" in text or "股东变化" in text:
        return "historical_evolution"
    return ""


def add_source(existing: str, new_source: str) -> str:
    parts = [part for part in existing.split("+") if part]
    if new_source not in parts:
        parts.append(new_source)
    return "+".join(parts)


def should_expand_event_bundle(
    anchor: dict[str, object],
    neighbor: dict[str, object],
    bridge: dict[str, object],
    direction: str,
    distance: int,
) -> tuple[bool, str]:
    """
    R2+R3+R4+R5：
    保留连续事件包，但遇到其他主体、员工平台内部结构或非事件性叙述时停止。
    """
    if distance > MAX_EVENT_BUNDLE_DISTANCE:
        return False, ""

    if neighbor["toc"]:
        return False, ""

    # V2.3.2补丁2：dominant_non_event是事件包硬边界。
    # 当前页不得被吸收，且调用方会在False后终止继续向后/向前扩展。
    if neighbor.get("dominant_non_event"):
        return False, "dominant_non_event_boundary"

    # 向前追溯主要用于“股本演变/增资”章节。
    # 股权结构章节不向前吸收基金或持股平台表格。
    if (
        direction == "backward"
        and anchor["section_context"] != "issuer_history"
    ):
        return False, ""

    same_context = bool(
        anchor["section_context"]
        and anchor["section_context"]
        == neighbor["section_context"]
    )
    neighbor_continuity = neighbor["continuity_signals"]
    bridge_continuity = bridge["continuity_signals"]

    if (
        neighbor["section_context"]
        != anchor["section_context"]
        and should_stop_section_context(
            str(neighbor["text"]),
            str(anchor["section_context"]),
        )
    ):
        return False, ""

    entity_scope = str(neighbor["entity_scope"])

    # 其他主体和员工持股平台页面原则上是边界。
    # 唯一例外：发行人增资章节中的纯续表页，可由紧邻的发行人事件页带入。
    if entity_scope in {"other_entity", "platform_internal"}:
        continuation_exception = bool(
            direction == "backward"
            and same_context
            and anchor["section_context"] == "issuer_history"
            and neighbor_continuity
            and bridge["issuer_bound_event"]
            and not neighbor["non_event_narrative"]
        )
        if not continuation_exception:
            return False, ""

    if (
        neighbor["non_event_narrative"]
        and not neighbor["issuer_bound_event"]
    ):
        return False, ""

    has_negative = bool(
        neighbor["hard_negative_hits"]
        or neighbor["promise_hits"]
        or neighbor["non_target_hits"]
    )

    if has_negative and not (
        neighbor["issuer_bound_event"]
        or (
            same_context
            and neighbor_continuity
            and bridge["issuer_bound_event"]
        )
    ):
        return False, ""

    reasons: list[str] = []

    if same_context:
        reasons.append("same_section_context")
    if neighbor_continuity:
        reasons.extend(
            str(item) for item in neighbor_continuity
        )
    if bridge_continuity:
        reasons.append("bridge_page_continuity")
    if neighbor["issuer_bound_event"]:
        reasons.append("issuer_bound_event")
    if neighbor["strong_core"]:
        reasons.append("strong_core_signature")

    neighbor_text = str(neighbor["text"])
    event_heading_terms = [
        "第一次增资", "第二次增资", "第三次增资",
        "增资情况", "报告期内的股本和股东变化情况",
        "设立以来股本演变情况", "整体变更",
    ]
    if any(
        term in neighbor_text[:500]
        for term in event_heading_terms
    ):
        reasons.append("event_heading")

    if direction == "backward":
        allowed = bool(
            same_context
            and (
                neighbor_continuity
                or neighbor["issuer_bound_event"]
                or (
                    neighbor["strong_core"]
                    and entity_scope == "issuer"
                )
                or "event_heading" in reasons
            )
        )
        return allowed, "|".join(sorted(set(reasons)))

    # 向后只在同一发行人上下文中扩展，并要求续表或发行人事件证据。
    allowed = bool(
        same_context
        and (
            neighbor["issuer_bound_event"]
            or neighbor_continuity
            or (
                bridge_continuity
                and entity_scope in {"issuer", "unclear"}
            )
        )
    )
    return allowed, "|".join(sorted(set(reasons)))


def incomplete_table_signal(text: str) -> tuple[bool, str]:
    header_hits = unique_hits(text, TABLE_HEADER_TERMS)
    has_header = len(header_hits) >= 3
    has_total = "合计" in text or "总计" in text
    ends_with_table_like_value = bool(re.search(r"(?:%|万元|万股|股)\s*$", text))
    if has_header and not has_total:
        return True, "table_header_without_total"
    if has_header and ends_with_table_like_value:
        return True, "table_likely_continues"
    return False, ""


def score_page(
    text: str,
    target_keywords: list[str],
    issuer_hits: list[str],
    hard_negative_hits: list[str],
    non_target_hits: list[str],
    promise_hits: list[str],
    signals: list[str],
    page_number: int,
    section_context: str = "",
    entity_scope: str = "unclear",
    issuer_bound_event: bool = False,
    evidence_count: int = 0,
    non_event_narrative: bool = False,
) -> int:
    score = 0
    action_hits = unique_hits(text, ACTION_TERMS)
    number_hits = unique_hits(text, NUMBER_TERMS)
    priority_hits = unique_hits(text, HIGH_PRIORITY_SECTION_TERMS)

    if target_keywords:
        score += 3
    if issuer_hits:
        score += 2
    if action_hits:
        score += 2
    if number_hits:
        score += 1
    if priority_hits:
        score += 3
    if has_strong_core_signature(signals):
        score += 4
    if section_context:
        score += 3
    if issuer_bound_event:
        score += 4
    score += min(evidence_count, 3)

    if entity_scope in {"other_entity", "platform_internal"}:
        score -= 8
    if non_event_narrative:
        score -= 6

    if hard_negative_hits:
        score -= 5
    if non_target_hits:
        score -= 4
    if promise_hits:
        score -= 4
    if is_toc_page(text, page_number):
        score -= 10

    # 只有已绑定发行人的强核心证据可以覆盖混合页面负面词。
    if (
        has_strong_core_signature(signals)
        and issuer_bound_event
        and (hard_negative_hits or non_target_hits)
    ):
        score += 4

    return score


def status_from_score(
    score: int,
    strong_core: bool,
    toc: bool,
    hard_negative_hits: list[str],
    non_target_hits: list[str],
    promise_hits: list[str],
    actual_event_clause: bool,
    explicit_issuer_structure: bool,
    entity_scope: str,
    issuer_bound_event: bool,
    evidence_count: int,
    non_event_narrative: bool,
    dominant_non_event: bool,
) -> str:
    if toc:
        return "drop"

    # V2.3.2补丁2：主导内容是股份支付、公允价值或会计处理时，
    # 不允许历史增资引用或发行人绑定证据把页面重新拉回。
    if dominant_non_event:
        return "drop"

    if (
        entity_scope in {"other_entity", "platform_internal"}
        and not issuer_bound_event
        and not explicit_issuer_structure
    ):
        return "drop"

    if (
        non_event_narrative
        and not issuer_bound_event
        and not explicit_issuer_structure
    ):
        return "drop"

    if (
        hard_negative_hits
        or promise_hits
        or non_target_hits
    ) and not (
        issuer_bound_event
        or explicit_issuer_structure
    ):
        return "drop"

    if evidence_count < 2 and not explicit_issuer_structure:
        return "drop"

    if strong_core and score >= 7:
        return "candidate"

    if issuer_bound_event and score >= 5:
        return "review"

    if score >= 8:
        return "candidate"
    if score >= 5:
        return "review"
    return "drop"


def make_snippet(text: str, keywords: list[str], radius: int = 220) -> str:
    positions = [text.find(keyword) for keyword in keywords if text.find(keyword) >= 0]
    if not positions:
        return text[: radius * 2]
    position = min(positions)
    return text[max(0, position - radius): min(len(text), position + radius)]


def load_manifest(manifest_path: Path) -> list[dict[str, str]]:
    with manifest_path.open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    if not rows:
        raise ValueError("pdf_manifest.csv为空")
    return rows


def inspect_pdf(
    project_root: Path,
    manifest_row: dict[str, str],
    alias_map: dict[str, list[str]],
) -> tuple[list[PageResult], list[str]]:
    company_code = manifest_row["company_code"].strip().zfill(6)
    company_name = manifest_row["company_name"].strip()
    pdf_path = project_root / manifest_row["pdf_path"].strip()

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF不存在：{pdf_path}")

    aliases = derive_aliases(company_name)
    aliases.extend(alias_map.get(company_code, []))
    aliases = sorted(set(aliases))

    page_infos: list[dict[str, object]] = []
    active_context = ""
    active_context_source = ""
    context_pages_left = 0

    with fitz.open(pdf_path) as document:
        for page_index, page in enumerate(document):
            page_number = page_index + 1
            text = clean_page_text(
                page.get_text("text"),
                company_name,
                aliases,
            )

            discovered_aliases = discover_issuer_aliases_from_text(text)
            if discovered_aliases:
                aliases = sorted(set(aliases + discovered_aliases))

            anchor_context, anchor_source = (
                detect_section_anchor(text, aliases)
            )

            if anchor_context:
                active_context = anchor_context
                active_context_source = anchor_source
                context_pages_left = MAX_SECTION_CONTEXT_PAGES
            elif should_stop_section_context(
                text,
                active_context,
            ):
                active_context = ""
                active_context_source = ""
                context_pages_left = 0
            elif active_context:
                if should_refresh_section_context(text, active_context):
                    context_pages_left = MAX_SECTION_CONTEXT_PAGES
                else:
                    context_pages_left -= 1
                    if context_pages_left <= 0:
                        active_context = ""
                        active_context_source = ""

            section_context = active_context
            section_context_source = active_context_source

            if not text:
                page_infos.append({
                    "page_index": page_index,
                    "page_number": page_number,
                    "text": "",
                    "categories": [],
                    "target_keywords": [],
                    "issuer_hits": [],
                    "hard_negative_hits": [],
                    "non_target_hits": [],
                    "promise_hits": [],
                    "signals": [],
                    "strong_core": False,
                    "actual_event_clause": False,
                    "explicit_issuer_structure": False,
                    "toc": False,
                    "score": -999,
                    "status": "drop",
                    "eligible": False,
                    "section_context": section_context,
                    "section_context_source": section_context_source,
                    "continuity_signals": [],
                    "event_family": "",
                    "entity_scope": "unclear",
                    "entity_scope_reason": "",
                    "issuer_bound_event": False,
                    "issuer_bound_event_reason": "",
                    "event_evidence_count": 0,
                    "non_event_narrative": False,
                    "non_event_narrative_reason": "",
                    "dominant_non_event": False,
                })
                continue

            categories, target_keywords = match_groups(text)
            issuer_hits = unique_hits(text, aliases)
            hard_negative_hits = unique_hits(
                text,
                HARD_NEGATIVE_TERMS,
            )
            non_target_hits = unique_hits(
                text,
                NON_TARGET_STRUCTURE_TERMS,
            )
            promise_hits = unique_hits(
                text,
                RULE_OR_PROMISE_TERMS,
            )

            explicit_issuer_structure = (
                has_explicit_issuer_structure_heading(text)
            )
            explicit_issuer_establishment = (
                has_explicit_issuer_establishment_heading(text)
            )
            actual_event_clause = has_actual_event_clause(text)

            issuer_bound_event, issuer_bound_reason = (
                has_issuer_bound_event_clause(
                    text,
                    aliases,
                    section_context,
                )
            )

            entity_scope, entity_scope_reason = (
                detect_entity_scope(
                    text=text,
                    aliases=aliases,
                    section_context=section_context,
                    issuer_bound_event=issuer_bound_event,
                    explicit_issuer_structure=(
                        explicit_issuer_structure
                    ),
                    explicit_issuer_establishment=(
                        explicit_issuer_establishment
                    ),
                )
            )

            non_event_narrative, non_event_reason = (
                has_non_event_narrative(
                    text=text,
                    issuer_bound_event=issuer_bound_event,
                    explicit_issuer_structure=(
                        explicit_issuer_structure
                    ),
                )
            )
            dominant_non_event = non_event_reason.startswith(
                "dominant_non_event:"
            )

            continuity = continuation_signals(text)
            snapshot_continuation = bool(
                section_context == "issuer_structure"
                and not starts_with_relation_table(text)
                and not non_target_hits
                and (
                    "table_like" in continuity
                    or "starts_with_table_row" in continuity
                    or "持股数量" in text[:900]
                    or "持股比例" in text[:900]
                )
                and (
                    "股东名称" in text[:900]
                    or "持股数量" in text[:900]
                    or "持股比例" in text[:900]
                )
            )

            # 发行人股权结构续表优先于页面后半部分的“关联关系”说明。
            if snapshot_continuation and not dominant_non_event:
                explicit_issuer_structure = True
                entity_scope = "issuer"
                entity_scope_reason = (
                    "issuer_structure_table_continuation"
                )
                non_event_narrative = False
                non_event_reason = ""

            evidence_count = event_evidence_count(
                text=text,
                issuer_bound_event=issuer_bound_event,
                explicit_issuer_structure=(
                    explicit_issuer_structure
                ),
            )

            signals = strong_event_signals(
                text,
                target_keywords,
                issuer_hits,
                aliases,
                section_context,
            )

            if (
                entity_scope
                in {"other_entity", "platform_internal"}
                and not issuer_bound_event
            ):
                signals = [
                    signal
                    for signal in signals
                    if signal not in {
                        "snapshot_signature",
                        "subscription_signature",
                        "direct_transfer_signature",
                        "establishment_signature",
                        "conversion_signature",
                    }
                ]

            if non_event_narrative and not issuer_bound_event:
                signals = [
                    signal
                    for signal in signals
                    if signal not in {
                        "subscription_signature",
                        "direct_transfer_signature",
                    }
                ]

            if dominant_non_event:
                signals = [
                    signal
                    for signal in signals
                    if signal not in {
                        "subscription_signature",
                        "direct_transfer_signature",
                        "establishment_signature",
                        "conversion_signature",
                        "snapshot_signature",
                        "capitalization_signature",
                        "issuer_establishment_heading",
                    }
                ]

            if explicit_issuer_structure:
                signals.append("issuer_structure_heading")

            priority_hits = unique_hits(
                text,
                HIGH_PRIORITY_SECTION_TERMS,
            )
            action_hits = unique_hits(text, ACTION_TERMS)
            number_hits = unique_hits(text, NUMBER_TERMS)

            eligible = bool(target_keywords) or bool(
                priority_hits
                and action_hits
                and number_hits
            )

            toc = is_toc_page(text, page_number)

            score = score_page(
                text=text,
                target_keywords=target_keywords,
                issuer_hits=issuer_hits,
                hard_negative_hits=hard_negative_hits,
                non_target_hits=non_target_hits,
                promise_hits=promise_hits,
                signals=signals,
                page_number=page_number,
                section_context=section_context,
                entity_scope=entity_scope,
                issuer_bound_event=issuer_bound_event,
                evidence_count=evidence_count,
                non_event_narrative=non_event_narrative,
            )

            status = status_from_score(
                score=score,
                strong_core=has_strong_core_signature(signals),
                toc=toc,
                hard_negative_hits=hard_negative_hits,
                non_target_hits=non_target_hits,
                promise_hits=promise_hits,
                actual_event_clause=actual_event_clause,
                explicit_issuer_structure=(
                    explicit_issuer_structure
                ),
                entity_scope=entity_scope,
                issuer_bound_event=issuer_bound_event,
                evidence_count=evidence_count,
                non_event_narrative=non_event_narrative,
                dominant_non_event=dominant_non_event,
            )

            # R1：明确发行人资本事件在股本演变章节中至少进入review。
            if (
                section_context == "issuer_history"
                and issuer_bound_event
                and status == "drop"
                and not toc
                and not dominant_non_event
            ):
                status = "review"

            page_infos.append({
                "page_index": page_index,
                "page_number": page_number,
                "text": text,
                "categories": categories,
                "target_keywords": target_keywords,
                "issuer_hits": issuer_hits,
                "hard_negative_hits": hard_negative_hits,
                "non_target_hits": non_target_hits,
                "promise_hits": promise_hits,
                "signals": sorted(set(signals)),
                "strong_core": has_strong_core_signature(
                    signals
                ),
                "actual_event_clause": actual_event_clause,
                "explicit_issuer_structure": (
                    explicit_issuer_structure
                ),
                "toc": toc,
                "score": score,
                "status": status,
                "eligible": eligible,
                "section_context": section_context,
                "section_context_source": (
                    section_context_source
                ),
                "continuity_signals": continuity,
                "event_family": event_family(
                    categories,
                    signals,
                    text,
                ),
                "entity_scope": entity_scope,
                "entity_scope_reason": entity_scope_reason,
                "issuer_bound_event": issuer_bound_event,
                "issuer_bound_event_reason": (
                    issuer_bound_reason
                ),
                "event_evidence_count": evidence_count,
                "non_event_narrative": non_event_narrative,
                "non_event_narrative_reason": non_event_reason,
                "dominant_non_event": dominant_non_event,
            })

    def result_from_info(
        info: dict[str, object],
        status: str,
        selection_source: str,
        parent_hit_page: str = "",
        expansion_reason: str = "",
    ) -> PageResult:
        return PageResult(
            company_code=company_code,
            company_name=company_name,
            pdf_filename=pdf_path.name,
            page_index=int(info["page_index"]),
            pdf_file_page=int(info["page_number"]),
            matched_categories="|".join(info["categories"]),
            matched_keywords="|".join(
                info["target_keywords"]
            ),
            issuer_alias_hits="|".join(
                info["issuer_hits"]
            ),
            hard_negative_hits="|".join(
                info["hard_negative_hits"]
            ),
            non_target_keywords="|".join(
                info["non_target_hits"]
            ),
            rule_or_promise_keywords="|".join(
                info["promise_hits"]
            ),
            strong_event_hits="|".join(info["signals"]),
            score=int(info["score"]),
            candidate_status=status,
            selection_source=selection_source,
            parent_hit_page=parent_hit_page,
            expansion_reason=expansion_reason,
            section_context=str(info["section_context"]),
            section_context_source=str(
                info["section_context_source"]
            ),
            entity_scope=str(info["entity_scope"]),
            entity_scope_reason=str(
                info["entity_scope_reason"]
            ),
            issuer_bound_event=(
                "yes"
                if info["issuer_bound_event"]
                else "no"
            ),
            issuer_bound_event_reason=str(
                info["issuer_bound_event_reason"]
            ),
            event_evidence_count=int(
                info["event_evidence_count"]
            ),
            non_event_narrative=(
                str(info["non_event_narrative_reason"])
                if info["non_event_narrative"]
                else ""
            ),
            text_length=len(str(info["text"])),
            snippet=make_snippet(
                str(info["text"]),
                list(info["target_keywords"])
                + list(info["issuer_hits"]),
            ),
        )

    direct_results: dict[int, PageResult] = {}

    for info in page_infos:
        if not info["eligible"]:
            continue

        page_number = int(info["page_number"])
        direct_results[page_number] = result_from_info(
            info=info,
            status=str(info["status"]),
            selection_source="direct_hit",
        )

    expanded_results = dict(direct_results)

    # 只有绑定发行人的真实事件或明确发行人结构页可作为扩展锚点。
    anchor_infos = [
        info
        for info in page_infos
        if (
            info["status"] in {"candidate", "review"}
            and (
                info["issuer_bound_event"]
                or info["explicit_issuer_structure"]
            )
            and info["entity_scope"] == "issuer"
            and not info["non_event_narrative"]
        )
    ]

    for anchor in anchor_infos:
        anchor_page = int(anchor["page_number"])

        for direction, step in (
            ("backward", -1),
            ("forward", 1),
        ):
            bridge = anchor

            for distance in range(
                1,
                MAX_EVENT_BUNDLE_DISTANCE + 1,
            ):
                neighbor_page = (
                    anchor_page + step * distance
                )
                if (
                    neighbor_page < 1
                    or neighbor_page > len(page_infos)
                ):
                    break

                neighbor = page_infos[
                    neighbor_page - 1
                ]
                should_expand, reason = (
                    should_expand_event_bundle(
                        anchor=anchor,
                        neighbor=neighbor,
                        bridge=bridge,
                        direction=direction,
                        distance=distance,
                    )
                )

                if not should_expand:
                    break

                existing = expanded_results.get(
                    neighbor_page
                )

                if existing:
                    if existing.candidate_status == "drop":
                        existing.candidate_status = "review"
                    existing.selection_source = add_source(
                        existing.selection_source,
                        "event_bundle_context",
                    )
                    if not existing.parent_hit_page:
                        existing.parent_hit_page = str(
                            anchor_page
                        )
                    existing.expansion_reason = reason
                else:
                    expanded_results[
                        neighbor_page
                    ] = result_from_info(
                        info=neighbor,
                        status="review",
                        selection_source=(
                            "event_bundle_context"
                        ),
                        parent_hit_page=str(anchor_page),
                        expansion_reason=reason,
                    )

                bridge = neighbor

    # 单向表格续页逻辑也必须遵守主体边界和非事件边界。
    for page_number, result in list(
        expanded_results.items()
    ):
        if result.candidate_status != "candidate":
            continue

        current_info = page_infos[page_number - 1]
        incomplete, reason = incomplete_table_signal(
            str(current_info["text"])
        )
        if not incomplete:
            continue

        neighbor = page_number + 1
        if neighbor > len(page_infos):
            continue

        neighbor_info = page_infos[neighbor - 1]
        if not neighbor_info["text"]:
            continue

        if neighbor_info.get("dominant_non_event"):
            continue

        if (
            neighbor_info["entity_scope"]
            in {"other_entity", "platform_internal"}
            and not neighbor_info["issuer_bound_event"]
        ):
            continue

        if (
            neighbor_info["non_event_narrative"]
            and not neighbor_info["issuer_bound_event"]
        ):
            continue

        existing = expanded_results.get(neighbor)

        if existing:
            if existing.candidate_status == "drop":
                existing.candidate_status = "review"
            existing.selection_source = add_source(
                existing.selection_source,
                "table_continuation",
            )
            if not existing.parent_hit_page:
                existing.parent_hit_page = str(page_number)
            if not existing.expansion_reason:
                existing.expansion_reason = reason
        else:
            expanded_results[neighbor] = result_from_info(
                info=neighbor_info,
                status="review",
                selection_source="table_continuation",
                parent_hit_page=str(page_number),
                expansion_reason=reason,
            )

    return [
        expanded_results[page_number]
        for page_number in sorted(expanded_results)
    ], aliases


def write_results(output_path: Path, rows: list[PageResult]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(PageResult.__dataclass_fields__.keys())
    with output_path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row.__dict__)


def main() -> None:
    project_root = Path(__file__).resolve().parents[1]
    manifest_path = project_root / "data" / "pdf_manifest.csv"
    output_path = (
        project_root
        / "outputs"
        / "logs"
        / "section_location_log_frozen_v1.csv"
    )
    manifest_rows = load_manifest(manifest_path)
    alias_map = load_alias_map(project_root)
    all_rows: list[PageResult] = []

    for row in manifest_rows:
        code = row["company_code"].strip().zfill(6)
        name = row["company_name"].strip()
        try:
            results, aliases = inspect_pdf(project_root, row, alias_map)
            all_rows.extend(results)
            counts = {
                status: sum(item.candidate_status == status for item in results)
                for status in ("candidate", "review", "drop")
            }
            print(
                f"[OK] {code} {name}: candidate={counts['candidate']}, "
                f"review={counts['review']}, drop={counts['drop']}, total={len(results)}"
            )
        except Exception as exc:
            print(f"[ERROR] {code} {name}: {exc}")

    write_results(output_path, all_rows)
    print(f"\nFrozen V1结果已保存：{output_path}")
    print(f"总记录数：{len(all_rows)}")


if __name__ == "__main__":
    main()
