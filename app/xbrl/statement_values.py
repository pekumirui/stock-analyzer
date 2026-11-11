from __future__ import annotations

from app.xbrl.three_statements import (
    DEI_CONSOLIDATED,
    DOC_TYPES,
    EXCLUDED_NS,
    PDF_NS,
    STANDARD_NS,
)

__all__ = ["COLUMNS", "VIEW_NAME", "select_sql", "view_sql"]

VIEW_NAME = "xbrl_statement_values"
XBRL_SOURCES = ("edinet", "tdnet")
CUMULATIVE_DAYS = {"Q2": 120, "Q3": 210}
COLUMNS = (
    "code", "period_end", "period_type", "is_consolidated", "context_type", "period_start",
    "element_key", "value", "unit", "source", "filing_id", "submitted_at", "fact_id", "matched_by",
)
PARTITION = ("code", "period_end", "is_consolidated", "element_key")


def _q(values) -> str:
    return ", ".join(f"'{v}'" for v in sorted(values))


def _fact_ok(f: str, c: str, fl: str) -> str:
    days = " ".join(f"WHEN '{k}' THEN {v}" for k, v in sorted(CUMULATIVE_DAYS.items()))
    return (
        f"{c}.relative_period = 'current' AND {f}.value_num IS NOT NULL "
        f"AND ({f}.unit IS NULL OR {f}.unit IN ('JPY', 'JPYPerShares')) "
        f"AND (CASE WHEN {c}.has_dimensions = 0 THEN 1 "
        f"WHEN NOT json_valid({c}.dimensions_json) THEN 0 "
        f"ELSE NOT EXISTS (SELECT 1 FROM json_each({c}.dimensions_json) j "
        f"WHERE substr(j.value, instr(j.value, ':') + 1) NOT IN ('ConsolidatedMember', 'NonConsolidatedMember')) "
        f"END) "
        f"AND ({c}.period_type = 'instant' OR {c}.start_date IS NULL "
        f"OR julianday({c}.end_date) - julianday({c}.start_date) >= CASE {fl}.period_type {days} ELSE 0 END)"
    )


def _basis(c: str, fl: str) -> str:
    return (
        f"CASE WHEN {c}.is_consolidated = 1 AND NOT EXISTS (SELECT 1 FROM xbrl_elements de "
        f"CROSS JOIN xbrl_facts d ON d.filing_id = {fl}.id AND d.element_id = de.id "
        f"WHERE de.namespace_key = 'jpdei_cor' AND de.local_name = '{DEI_CONSOLIDATED}' "
        f"AND lower(trim(d.value_text)) = 'false') THEN 1 ELSE 0 END"
    )


def _element_ok(custom: bool) -> str:
    if custom:
        return f"e.is_custom = 1 AND e.namespace_key NOT IN ({_q(EXCLUDED_NS + tuple(PDF_NS))})"
    return f"+e.namespace_key IN ({_q(STANDARD_NS)})"


def _xbrl_arm(custom: bool) -> str:
    key = "'ext:' || e.local_name" if custom else "e.local_name"
    return (
        "SELECT fl.code AS code, fl.fiscal_year_end AS period_end, fl.period_type AS period_type, "
        f"{_basis('c', 'fl')} AS is_consolidated, c.period_type AS context_type, c.start_date AS period_start, "
        f"{key} AS element_key, f.value_num AS value, f.unit AS unit, fl.source AS source, "
        "fl.id AS filing_id, fl.submitted_at AS submitted_at, f.id AS fact_id, NULL AS matched_by "
        "FROM xbrl_elements e "
        "JOIN xbrl_facts f ON f.element_id = e.id "
        "JOIN xbrl_contexts c ON c.id = f.context_id "
        "JOIN xbrl_filings fl ON fl.id = f.filing_id "
        f"WHERE {_element_ok(custom)} AND fl.parse_status = 'parsed' "
        f"AND fl.source IN ({_q(XBRL_SOURCES)}) AND fl.doc_type IN ({_q(DOC_TYPES)}) "
        f"AND fl.fiscal_year_end IS NOT NULL AND {_fact_ok('f', 'c', 'fl')}"
    )


def _pdf_arm() -> str:
    return (
        "SELECT fl.code AS code, fl.fiscal_year_end AS period_end, fl.period_type AS period_type, "
        "m.is_consolidated AS is_consolidated, c.period_type AS context_type, c.start_date AS period_start, "
        "m.element_key AS element_key, f.value_num * m.sign AS value, f.unit AS unit, fl.source AS source, "
        "fl.id AS filing_id, fl.submitted_at AS submitted_at, f.id AS fact_id, m.matched_by AS matched_by "
        "FROM xbrl_pdf_matches m "
        "JOIN xbrl_facts f ON f.id = m.fact_id "
        "JOIN xbrl_contexts c ON c.id = f.context_id "
        "JOIN xbrl_filings fl ON fl.id = m.filing_id "
        "WHERE m.element_key IS NOT NULL AND fl.parse_status = 'parsed'"
    )


def select_sql(*, pdf: bool = True) -> str:
    arms = [_xbrl_arm(False), _xbrl_arm(True)]
    if pdf:
        arms.append(_pdf_arm())
    union = "\nUNION ALL\n".join(arms)
    cols = ", ".join(COLUMNS)
    return (
        f"SELECT {cols} FROM (SELECT {cols}, row_number() OVER ("
        f"PARTITION BY {', '.join(PARTITION)} "
        "ORDER BY CASE source WHEN 'edinet' THEN 0 WHEN 'tdnet' THEN 1 ELSE 2 END, "
        "submitted_at DESC, filing_id DESC, fact_id) AS pick "
        f"FROM (\n{union}\n)) WHERE pick = 1"
    )


def view_sql() -> str:
    return f"CREATE VIEW {VIEW_NAME} AS\n{select_sql()}"
