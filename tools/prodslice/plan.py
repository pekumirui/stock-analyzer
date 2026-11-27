from __future__ import annotations

import re
from dataclasses import dataclass

CODE_RE = re.compile(r"^[0-9][0-9A-Z]{3}[0-9]?$")
MAX_CODES = 50
PROD_USER_VERSION = 2


@dataclass(frozen=True)
class CopyAll:
    pass


@dataclass(frozen=True)
class ByCode:
    columns: tuple[str, ...] = ("code",)


@dataclass(frozen=True)
class ChildOf:
    column: str
    parent: str
    parent_column: str


@dataclass(frozen=True)
class UsedBy:
    column: str
    sources: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Skip:
    pass


Rule = CopyAll | ByCode | ChildOf | UsedBy | Skip

FILINGS = ("xbrl_filings", "id")

PLAN: tuple[tuple[str, Rule], ...] = (
    ("stocks", CopyAll()),
    ("data_versions", CopyAll()),
    ("topix", CopyAll()),
    ("fx_rates", CopyAll()),
    ("material_monthly", CopyAll()),
    ("material_trade_country", CopyAll()),
    ("earnings_date_fetches", CopyAll()),
    ("edinet_list_days", CopyAll()),
    ("xbrl_std_items", CopyAll()),
    ("xbrl_element_mappings", CopyAll()),
    ("daily_prices", ByCode()),
    ("indicators", ByCode()),
    ("statements", ByCode()),
    ("earnings_events", ByCode()),
    ("earnings_date_pubs", ByCode()),
    ("pts_snapshots", ByCode()),
    ("homepages", ByCode()),
    ("tanshin_covers", ByCode()),
    ("tdnet_pdf_documents", ByCode()),
    ("monthly_issuers", ByCode()),
    ("monthly_tables", ByCode()),
    ("monthly_elements", ByCode()),
    ("monthly_values", ByCode()),
    ("monthly_element_sources", ByCode()),
    ("monthly_publications", ByCode()),
    ("briefing_documents", ByCode()),
    ("briefing_extracts", ByCode()),
    ("briefing_irbank_scans", ByCode()),
    ("briefing_pages", ChildOf("document_id", "briefing_extracts", "document_id")),
    ("briefing_facts", ChildOf("document_id", "briefing_extracts", "document_id")),
    ("edinet_documents", ByCode()),
    ("edinet_archives", ChildOf("doc_id", "edinet_documents", "doc_id")),
    ("edinet_buybacks", ByCode()),
    ("edinet_buyback_resolutions", ChildOf("doc_id", "edinet_buybacks", "doc_id")),
    ("edinet_tender_offers", ByCode(("offeror_code", "target_code"))),
    ("edinet_tender_offer_docs", ChildOf("tender_offer_id", "edinet_tender_offers", "tender_offer_id")),
    ("large_holding_reports", ByCode()),
    ("large_holdings", ChildOf("doc_id", "large_holding_reports", "doc_id")),
    ("xbrl_filings", ByCode()),
    ("xbrl_contexts", ChildOf("filing_id", *FILINGS)),
    ("xbrl_documents", ChildOf("filing_id", *FILINGS)),
    ("xbrl_presentation_roles", ChildOf("filing_id", *FILINGS)),
    ("xbrl_major_shareholders", ChildOf("filing_id", *FILINGS)),
    ("xbrl_shareholding_by_category", ChildOf("filing_id", *FILINGS)),
    ("xbrl_facts", ChildOf("filing_id", *FILINGS)),
    ("xbrl_shareholder_filings", ByCode()),
    ("xbrl_financials", ByCode()),
    ("xbrl_segments", ByCode()),
    ("xbrl_pdf_matches", ByCode()),
    ("xbrl_elements", UsedBy("id", (
        ("xbrl_facts", "element_id"), ("xbrl_financials", "source_element_id"), ("xbrl_segments", "source_element_id"),
    ))),
    ("edinet_filers", UsedBy("edinet_code", (
        ("stocks", "edinet_code"), ("edinet_documents", "edinet_code"), ("edinet_documents", "issuer_edinet_code"),
        ("edinet_documents", "subject_edinet_code"), ("edinet_buybacks", "edinet_code"),
        ("large_holding_reports", "filer_edinet_code"), ("large_holding_reports", "issuer_edinet_code"),
        ("large_holdings", "edinet_code"), ("edinet_tender_offers", "offeror_edinet_code"),
        ("edinet_tender_offers", "target_edinet_code"), ("edinet_tender_offer_docs", "filer_edinet_code"),
        ("edinet_tender_offer_docs", "subject_edinet_code"),
    ))),
    ("xbrl_job_runs", Skip()),
)


def parse_codes(raw: str) -> list[str]:
    codes = [c.strip().upper() for c in raw.replace(" ", ",").split(",") if c.strip()]
    if not codes or len(codes) > MAX_CODES:
        raise ValueError(f"銘柄は 1〜{MAX_CODES} 個: {raw!r}")
    bad = [c for c in codes if not CODE_RE.match(c)]
    if bad:
        raise ValueError(f"銘柄コードの形が違う: {', '.join(bad)}")
    return list(dict.fromkeys(c if len(c) == 5 else f"{c}0" for c in codes))
