from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Stock(Base):
    __tablename__ = "stocks"
    code = Column(String(5), primary_key=True)
    name = Column(String)
    sector17 = Column(String)
    sector33 = Column(String)
    market = Column(String)
    scale_category = Column(String)
    margin_type = Column(String)
    product_category = Column(String)  # ProdCat: 011=内国株券, 013=REIT, 014=ETF 等
    edinet_code = Column(String(6))    # EDINET コードリストの証券コードで付ける。無い銘柄は NULL


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    code = Column(String(5), primary_key=True)
    date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    turnover = Column(Float)
    adj_factor = Column(Float, default=1.0)
    adj_open = Column(Float)
    adj_high = Column(Float)
    adj_low = Column(Float)
    adj_close = Column(Float)
    adj_volume = Column(Float)
    market_cap = Column(Float)        # 時価総額(百万円)
    upper_limit = Column(Integer)     # J-Quants UL。その日にストップ高を付けたら 1。取込前の行は NULL
    lower_limit = Column(Integer)     # J-Quants LL。ストップ安
    ex_rt = Column(String)            # J-Quants ExRT。権利落ち日だけ 1=分割（無償割当を含む）/ 2=併合 / 3=ライツイシュー
    # 行を (code, date) 順に置く。rowid 表だと日ごとの追記で 1 銘柄の行が表全体に散り、
    # チャート 1 回で千ページ以上をランダムに読む (KAB-289)。既存 DB は jobs/ops/rebuild_daily_prices.py
    __table_args__ = (Index("ix_prices_date", "date"), {"sqlite_with_rowid": False})


class Topix(Base):
    __tablename__ = "topix"
    date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)


class Statement(Base):
    __tablename__ = "statements"
    disclosure_no = Column(String, primary_key=True)
    code = Column(String(5), index=True)
    disclosed_date = Column(Date, index=True)
    disclosed_time = Column(String)
    type_of_period = Column(String)      # 1Q/2Q/3Q/4Q/5Q/FY
    type_of_document = Column(String)
    period_start = Column(Date)
    period_end = Column(Date)
    fiscal_year_start = Column(Date)
    fiscal_year_end = Column(Date)
    net_sales = Column(Float)
    operating_profit = Column(Float)
    ordinary_profit = Column(Float)
    profit = Column(Float)
    eps = Column(Float)
    f2_net_sales = Column(Float)
    f2_operating_profit = Column(Float)
    f2_ordinary_profit = Column(Float)
    f2_profit = Column(Float)
    f2_eps = Column(Float)
    fy_net_sales = Column(Float)
    fy_operating_profit = Column(Float)
    fy_ordinary_profit = Column(Float)
    fy_profit = Column(Float)
    fy_eps = Column(Float)
    is_revision = Column(Boolean, default=False)
    raw_json = Column(Text)
    __table_args__ = (
        # カレンダーの重複判定と決算一覧の履歴。raw_json 以外を載せて表本体を読まない (KAB-292)
        Index(
            "ix_statements_earnings_cover",
            "code",
            "disclosed_date",
            "disclosure_no",
            "disclosed_time",
            "type_of_period",
            "type_of_document",
            "period_start",
            "period_end",
            "fiscal_year_start",
            "fiscal_year_end",
            "net_sales",
            "operating_profit",
            "ordinary_profit",
            "profit",
            "eps",
            "f2_net_sales",
            "f2_operating_profit",
            "f2_ordinary_profit",
            "f2_profit",
            "f2_eps",
            "fy_net_sales",
            "fy_operating_profit",
            "fy_ordinary_profit",
            "fy_profit",
            "fy_eps",
            "is_revision",
        ),
    )


class Indicator(Base):
    __tablename__ = "indicators"
    code = Column(String(5), primary_key=True)
    date = Column(Date, primary_key=True)
    close = Column(Float)
    ma50 = Column(Float)
    ma150 = Column(Float)
    ma200 = Column(Float)
    ma200_slope_1m = Column(Float)
    high_52w = Column(Float)
    low_52w = Column(Float)
    rs_raw = Column(Float)
    rs_rank = Column(Integer)
    stage = Column(Integer)
    avg_volume_50 = Column(Float)
    __table_args__ = (Index("ix_ind_date_stage", "date", "stage"),)


class EarningsDatePub(Base):
    """発表のたびに 1 行。現在の予定は (code, fq_name) で pub_date が最新の行。

    sch_date だけで引かない。移動前の日が残っている。
    """
    __tablename__ = "earnings_date_pubs"
    code = Column(String(5), primary_key=True)
    fq_name = Column(String(8), primary_key=True)  # 1Q | 2Q | 3Q | FY
    pub_date = Column(Date, primary_key=True)
    sch_date = Column(Date, nullable=True)  # NULL は未定。欠測ではない
    fye = Column(String(4), nullable=False)  # MMDD。0299 は 2月末
    co_name = Column(String, nullable=False)
    __table_args__ = (Index("ix_earnings_date_pubs_sch_date", "sch_date"),)


class EarningsDateFetch(Base):
    """取り終えた発表日。空の応答も n=0 で残す。pubs の写しではない。"""
    __tablename__ = "earnings_date_fetches"
    pub_date = Column(Date, primary_key=True)
    n = Column(Integer, nullable=False)


class EarningsEvent(Base):
    __tablename__ = "earnings_events"
    code = Column(String(5), primary_key=True)
    disclosed_date = Column(Date, primary_key=True)
    base_date = Column(Date)
    base_price = Column(Float)
    return_1d = Column(Float)
    return_5d = Column(Float)
    return_20d = Column(Float)
    return_60d = Column(Float)
    return_to_date = Column(Float)
    alpha_1d = Column(Float)
    alpha_5d = Column(Float)
    alpha_20d = Column(Float)
    alpha_60d = Column(Float)
    alpha_to_date = Column(Float)
    disclosed_time = Column(String)   # その日の決算短信のうち一番早い発表時刻
    is_first = Column(Boolean)        # その期（決算期 × 四半期）の最初の決算短信。訂正・予想修正だけは偽
    next_disclosed_date = Column(Date)  # 同じ銘柄の次の期の最初の決算短信の日。まだ無ければ NULL
    end_date = Column(Date)           # return_to_next の終点（次の決算の前営業日。無ければ株価の最終日）
    return_to_next = Column(Float)    # 次の決算の前日終値まで（%）
    alpha_to_next = Column(Float)


class MonthlyIssuer(Base):
    """発行体ごとの月次属性。industry は月次業種で stocks.sector33 とは別。FK は付けない。"""
    __tablename__ = "monthly_issuers"
    code = Column(String(5), primary_key=True)
    industry = Column(String(50), nullable=False, default="その他", server_default="その他")
    last_ingested_at = Column(DateTime, nullable=True)


class MonthlyTable(Base):
    """銘柄×年度の月次表。code は stocks にあるものだけ書く。FK は付けない。"""
    __tablename__ = "monthly_tables"
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)


class MonthlyElement(Base):
    """月次表の行。element は登録時の正規名、sort_order が登録順。

    yoy の element は前年比語を剥がして末尾 (前年比)。number はトリムのみ。
    正規化後に同一なら同一行。短い保存名は正規名へリネームする。
    保存名は凍結。揃えるのは jobs/monthly/monthly_names.py だけ。
    """
    __tablename__ = "monthly_elements"
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)
    element = Column(String(50), primary_key=True)
    sort_order = Column(Integer, nullable=False)
    unit = Column(String(16), nullable=False, default="number", server_default="number")


class MonthlyElementSource(Base):
    """送られてきたままの行名 → 今の保存名。正規化の差分スナップショットの入力。

    source_label と unit は受け取ったまま（正規化前）。一度入れたら書き換えない。
    element は保存名の付け替え・統合に付いていく。行を消すと一緒に消える。
    via は経路（api / doc / ir_html / ir_json / ir_xls / legacy）。
    """
    __tablename__ = "monthly_element_sources"
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)
    source_label = Column(String(50), primary_key=True)
    unit = Column(String(16), primary_key=True)
    element = Column(String(50), nullable=False, index=True)
    via = Column(String(16), nullable=False)
    first_seen_at = Column(DateTime, nullable=False)
    last_seen_at = Column(DateTime, nullable=False)


class MonthlyValue(Base):
    """月次セル。(code, year, element, month) 一意。value は数値のみ。"""
    __tablename__ = "monthly_values"
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)
    element = Column(String(50), primary_key=True)
    month = Column(Integer, primary_key=True)
    value = Column(Float, nullable=False)


class XbrlFiling(Base):
    __tablename__ = "xbrl_filings"
    id = Column(Integer, primary_key=True)
    code = Column(String(5), nullable=False, index=True)
    source = Column(String(16), nullable=False, default="tdnet")
    doc_id = Column(String(64), nullable=False)
    doc_type = Column(String(32), nullable=False, default="other")
    period_type = Column(String(8))
    fiscal_year_end = Column(Date)
    submitted_at = Column(DateTime, nullable=False)
    title = Column(Text)
    accounting_standard = Column(String(16))
    is_amendment = Column(Boolean, nullable=False, default=False)
    raw_path = Column(Text, nullable=False)
    raw_sha256 = Column(String(64))
    parse_status = Column(String(16), nullable=False, default="pending")
    parse_error = Column(Text)
    parsed_at = Column(DateTime)
    documents_status = Column(String(16))  # 原文（KAB-161）: NULL=まだ / stored / empty（選べる HTML が 0 件）
    created_at = Column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint("source", "doc_id", name="uq_xbrl_filings_source_doc"),
        Index("idx_xbrl_filings_code_submitted", "code", "submitted_at"),
        Index("idx_xbrl_filings_parse_status", "parse_status"),
        # 決算一覧の売上総利益。id 引きのあと source / doc_id / submitted_at / parse_status
        # を表本体から読まない (KAB-295)
        Index(
            "idx_xbrl_filings_id_cover",
            "id", "parse_status", "source", "doc_id", "submitted_at",
        ),
    )



class XbrlDocument(Base):
    """短信 ZIP の表示用 HTML（KAB-161）。書類ごとに表示順で並べる。元の HTML を gzip で持つ。

    part は summary（サマリー）/ qualitative（添付資料まるごと）/ attachment（qualitative.htm が
    無い書類の本表 iXBRL）。script などの除去は表示のとき（app/xbrl/documents.py）。
    """
    __tablename__ = "xbrl_documents"
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), primary_key=True)
    seq = Column(Integer, primary_key=True)
    part = Column(String(16), nullable=False)
    file_name = Column(Text, nullable=False)
    html_gz = Column(LargeBinary, nullable=False)
    bytes = Column(Integer, nullable=False)       # 圧縮前
    sha256 = Column(String(64), nullable=False)   # 圧縮前
    created_at = Column(DateTime, nullable=False)


class TdnetPdfDocument(Base):
    """TDnet の開示 PDF（IR BANK の保管分）を HTML に変換したもの（KAB-164）。

    XBRL ZIP の原文（xbrl_documents）が無い過去の回を銘柄タブで読むため。キーは TDnet の
    docID（= statements.disclosure_no = XBRL ZIP 名の後ろ 14 桁）。取込は tools/tdnet_pdf/。
    status: ok（html あり）/ missing（PDF が 404）/ failed（取得・変換の失敗）。
    """
    __tablename__ = "tdnet_pdf_documents"
    disclosure_no = Column(String(14), primary_key=True)
    code = Column(String(5), nullable=False, index=True)
    disclosed_date = Column(Date)
    disclosed_time = Column(String(8))
    title = Column(Text)
    status = Column(String(16), nullable=False)
    error = Column(Text)
    html_gz = Column(LargeBinary)
    bytes = Column(Integer)          # 変換後 HTML（圧縮前）
    sha256 = Column(String(64))      # 変換後 HTML（圧縮前）
    pages = Column(Integer)
    pdf_bytes = Column(Integer)
    pdf_sha256 = Column(String(64))
    converter = Column(String(16))   # 変換器の版（tools/tdnet_pdf/convert.py CONVERTER）
    created_at = Column(DateTime, nullable=False)

class XbrlElement(Base):
    __tablename__ = "xbrl_elements"
    id = Column(Integer, primary_key=True)
    namespace_uri = Column(Text, nullable=False)
    namespace_key = Column(String(64), nullable=False)
    local_name = Column(String(255), nullable=False)
    label_ja = Column(Text)
    label_en = Column(Text)
    item_type = Column(String(16))
    is_custom = Column(Boolean, nullable=False, default=False)
    first_seen_at = Column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint("namespace_uri", "local_name", name="uq_xbrl_elements_ns_local"),
        Index("idx_xbrl_elements_key", "namespace_key", "local_name"),
    )


class XbrlContext(Base):
    __tablename__ = "xbrl_contexts"
    id = Column(Integer, primary_key=True)
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), nullable=False)
    context_ref = Column(String(255), nullable=False)
    period_type = Column(String(16), nullable=False)
    start_date = Column(Date)
    end_date = Column(Date, nullable=False)
    is_consolidated = Column(Integer, nullable=False, default=1)
    relative_period = Column(String(16))
    dimensions_json = Column(Text, nullable=False, default="{}")
    has_dimensions = Column(Boolean, nullable=False, default=False)
    # 次元メンバー → その書類のラベル（日本語）。標準メンバーも会社が名前を付け替えるので書類ごと（KAB-77）
    member_labels_json = Column(Text)
    __table_args__ = (
        UniqueConstraint("filing_id", "context_ref", name="uq_xbrl_contexts_filing_ref"),
        Index("idx_xbrl_contexts_filing", "filing_id"),
    )


class XbrlFact(Base):
    __tablename__ = "xbrl_facts"
    id = Column(Integer, primary_key=True)
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), nullable=False)
    element_id = Column(Integer, ForeignKey("xbrl_elements.id"), nullable=False)
    context_id = Column(Integer, ForeignKey("xbrl_contexts.id"), nullable=False)
    unit = Column(String(64))
    decimals = Column(Integer)
    scale = Column(Integer)
    value_num = Column(Numeric(30, 4))
    value_text = Column(Text)
    is_nil = Column(Boolean, nullable=False, default=False)
    __table_args__ = (
        UniqueConstraint(
            "filing_id", "element_id", "context_id", "unit",
            name="uq_xbrl_facts_key",
        ),
        Index("idx_xbrl_facts_filing", "filing_id"),
        Index("idx_xbrl_facts_element", "element_id"),
    )


class XbrlStdItem(Base):
    __tablename__ = "xbrl_std_items"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), nullable=False, unique=True)
    statement = Column(String(8), nullable=False)
    section = Column(String(64))
    label_ja = Column(String(128), nullable=False)
    display_order = Column(Integer, nullable=False, default=0)
    sign = Column(String(16), nullable=False, default="natural")
    is_required = Column(Boolean, nullable=False, default=False)
    # xbrl_financials.unit に入れる単位。NULL は fact の単位（JPY / USD / JPYPerShares 等）。
    # fact が pure（人数・年数・比率）のときだけ科目側で persons / years / ratio と決める（KAB-78 / KAB-147）
    unit = Column(String(32))


class XbrlElementMapping(Base):
    __tablename__ = "xbrl_element_mappings"
    id = Column(Integer, primary_key=True)
    std_item_id = Column(Integer, ForeignKey("xbrl_std_items.id"), nullable=False)
    namespace_key = Column(String(64), nullable=False)
    local_name = Column(String(255), nullable=False)
    priority = Column(Integer, nullable=False, default=100)
    accounting_standard = Column(String(16))
    note = Column(Text)
    # NULL は値そのもの。'months' は同じ科目・context の値に 月÷12 を足す
    # （有報の「40歳3ヶ月」は年と月が別の要素。KAB-78）
    part = Column(String(16))
    __table_args__ = (
        UniqueConstraint(
            "std_item_id", "namespace_key", "local_name", "accounting_standard",
            name="uq_xbrl_element_mappings",
        ),
        Index("idx_xbrl_mappings_elem", "namespace_key", "local_name"),
    )


class XbrlFinancial(Base):
    __tablename__ = "xbrl_financials"
    id = Column(Integer, primary_key=True)
    code = Column(String(5), nullable=False)
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), nullable=False)
    std_item_id = Column(Integer, ForeignKey("xbrl_std_items.id"), nullable=False)
    is_consolidated = Column(Boolean, nullable=False)
    period_type = Column(String(8), nullable=False)
    period_start = Column(Date)
    period_end = Column(Date, nullable=False)
    value = Column(Numeric(30, 4), nullable=False)
    unit = Column(String(32), nullable=False, default="JPY")
    # actual | forecast | forecast_previous（前回予想。EarnForecastRevision の PreviousMember）
    value_kind = Column(String(24), nullable=False, default="actual", server_default="actual")
    # point | lower | upper（レンジ予想。点予想は point）
    range_side = Column(String(16), nullable=False, default="point", server_default="point")
    # 数値が指す期間: FY / H1 / Q1 / Q2 / Q3 / year_end / annual
    target_period = Column(String(16), nullable=False, default="FY", server_default="FY")
    source_fact_id = Column(Integer, ForeignKey("xbrl_facts.id"))
    source_element_id = Column(Integer, ForeignKey("xbrl_elements.id"))
    # primary=本表 / summary=短信サマリー(tse-ed-t)。旧行は NULL（migrate で要素から埋める）
    source_layer = Column(String(16))
    generated_at = Column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "filing_id", "std_item_id", "is_consolidated", "period_end",
            "value_kind", "range_side", "target_period",
            name="uq_xbrl_financials_key",
        ),
        # 決算一覧の売上総利益。code / std_item_id / value_kind で引き、表本体を読まない (KAB-295)
        # 旧 idx_xbrl_financials_lookup (code, std_item_id, is_consolidated, period_end, value_kind)
        # は先頭 2 列が同じなので ensure_indexes が外す
        Index(
            "idx_xbrl_financials_gp_cover",
            "code", "std_item_id", "value_kind", "is_consolidated",
            "period_start", "period_end", "value", "filing_id",
        ),
    )


class XbrlSegment(Base):
    """セグメント注記の正規化層（KAB-77。docs/DESIGN_xbrl.md §14）。xbrl_financials と同じく再生成できる。"""
    __tablename__ = "xbrl_segments"
    id = Column(Integer, primary_key=True)
    code = Column(String(5), nullable=False)
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), nullable=False)
    std_item_id = Column(Integer, ForeignKey("xbrl_std_items.id"), nullable=False)
    axis = Column(String(255), nullable=False)       # 'jpcrp_cor:OperatingSegmentsAxis'
    member = Column(String(255), nullable=False)     # 'prefix:localName'（会社独自は jpcrp030000-asr_E...）
    member_label_ja = Column(String(255))            # その書類のラベル
    # segment=各セグメント / subtotal=報告セグメント計 / other=その他 / total=合計 / reconciling=調整額
    member_kind = Column(String(16), nullable=False)
    is_consolidated = Column(Boolean, nullable=False)
    period_type = Column(String(8), nullable=False)
    period_start = Column(Date)
    period_end = Column(Date, nullable=False)
    value = Column(Numeric(30, 4), nullable=False)
    unit = Column(String(32), nullable=False, default="JPY")
    source_fact_id = Column(Integer, ForeignKey("xbrl_facts.id"))
    source_element_id = Column(Integer, ForeignKey("xbrl_elements.id"))
    generated_at = Column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "filing_id", "std_item_id", "axis", "member", "is_consolidated", "period_end",
            name="uq_xbrl_segments_key",
        ),
        Index("idx_xbrl_segments_lookup", "code", "std_item_id", "period_end"),
        Index("idx_xbrl_segments_filing", "filing_id"),
    )


class XbrlShareholderFiling(Base):
    """有報の大株主・所有者別の 1 書類（KAB-266）。xbrl_facts から正規化。API は別 issue。

    訂正は同じ code / fiscal_year_end / doc_type のうち提出が新しい 1 件が is_latest。
    """
    __tablename__ = "xbrl_shareholder_filings"
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), primary_key=True)
    code = Column(String(5), nullable=False)
    doc_id = Column(String(64), nullable=False)
    as_of = Column(Date)                            # 大株主・所有者別の instant（期末）
    basis = Column(String(16), nullable=False)      # shares / voting_rights / both
    shares_per_unit = Column(Integer)               # 単元株式数（普通株式。無ければ NULL）
    is_amendment = Column(Boolean, nullable=False, default=False)
    amends_doc_id = Column(String(8))
    original_doc_id = Column(String(8), nullable=False)
    is_latest = Column(Boolean, nullable=False, default=False)
    withdrawn = Column(Boolean, nullable=False, default=False)
    generated_at = Column(DateTime, nullable=False)
    __table_args__ = (
        Index("idx_xbrl_shareholder_filings_code", "code", "as_of"),
        Index("idx_xbrl_shareholder_filings_latest", "code", "is_latest"),
    )


class XbrlMajorShareholder(Base):
    """大株主の状況 1 行（順位 × 株式ベース / 議決権ベース）。"""
    __tablename__ = "xbrl_major_shareholders"
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), primary_key=True)
    rank = Column(Integer, primary_key=True)        # NoNMajorShareholdersMember の N
    basis = Column(String(16), primary_key=True)    # shares / voting_rights
    name = Column(Text)
    address = Column(Text)
    shares_held = Column(Numeric(30, 4))            # 所有株式数。議決権ベースの表は NULL
    shareholding_ratio = Column(Numeric(18, 6))     # 0.1650 = 16.50%
    voting_rights = Column(Numeric(30, 4))          # 所有議決権数（個）
    voting_rights_ratio = Column(Numeric(18, 6))
    __table_args__ = (
        Index("idx_xbrl_major_shareholders_filing", "filing_id"),
    )


class XbrlShareholdingByCategory(Base):
    """所有者別状況 1 行（株式の種類 × 区分）。"""
    __tablename__ = "xbrl_shareholding_by_category"
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), primary_key=True)
    share_class = Column(String(255), primary_key=True)  # OrdinaryShareMember 等。軸が無ければ ''
    category = Column(String(32), primary_key=True)
    shareholder_count = Column(Integer)
    units_held = Column(BigInteger)                 # 所有株式数（単元）
    shareholding_ratio = Column(Numeric(18, 6))     # 0.2780 = 27.80%。計の行は無いので NULL
    shares_less_than_one_unit = Column(Numeric(30, 4))  # 単元未満株式。区分の計の行に載せる
    __table_args__ = (
        Index("idx_xbrl_shareholding_by_category_filing", "filing_id"),
    )


class XbrlJobRun(Base):
    __tablename__ = "xbrl_job_runs"
    id = Column(Integer, primary_key=True)
    job_name = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False)
    processed_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    message = Column(Text)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime)


class FxRate(Base):
    """ECB 対EUR 日次レート。1 EUR = rate 単位の quote。EUR 自身は持たない。

    任意ペアは EUR クロスで算出する（app.fx.cross_rate）。
    """
    __tablename__ = "fx_rates"
    date = Column(Date, primary_key=True)
    quote = Column(String(3), primary_key=True)
    rate = Column(Float, nullable=False)
    __table_args__ = (Index("ix_fx_rates_quote_date", "quote", "date"),)


class MaterialMonthly(Base):
    """サプライタブの素材ごとの月次量。素材は config/supply/supply_chain.json の id（FK なし）。

    統計の品目との対応は config/supply/material_stats.json。取込は DESIGN.md §3「素材の月次量」。
    """
    __tablename__ = "material_monthly"
    material_id = Column(String, primary_key=True)
    month = Column(String(7), primary_key=True)
    metric = Column(String, primary_key=True)     # production / import / export / inventory / shipment
    source = Column(String, primary_key=True)     # seidou:<報告番号> / trade
    value = Column(Float, nullable=False)
    unit = Column(String, nullable=False)          # t / kl / 本 など。換算しない
    revision = Column(Integer, nullable=False)     # 1 速報 / 2 確報 / 3 年報（年間補正後）。下がる上書きはしない
    origin = Column(String)
    is_estimate = Column(Boolean, nullable=False, default=False)
    updated_at = Column(DateTime)


class MaterialTradeCountry(Base):
    """素材の輸出入の相手国別の月次量（普通貿易統計の品別国別表）。material_monthly の trade の国別内訳。

    国合計を material_monthly に書かない素材（原油・LPG は石油統計が正）も国別だけはここに持つ。
    """
    __tablename__ = "material_trade_country"
    material_id = Column(String, primary_key=True)
    month = Column(String(7), primary_key=True)
    flow = Column(String, primary_key=True)        # import / export
    country = Column(String(3), primary_key=True)  # 貿易統計の国コード（137 = サウジアラビア）
    country_name = Column(String, nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String, nullable=False)
    revision = Column(Integer, nullable=False)     # 1 速報 / 2 確報。下がる上書きはしない
    origin = Column(String)
    updated_at = Column(DateTime)


class PtsSnapshot(Base):
    """株探の PTS ナイトタイムセッションのランキング。取得 1 回ごとにランキング全行を残す。"""
    __tablename__ = "pts_snapshots"
    fetched_at = Column(DateTime, primary_key=True)  # 取得時刻 JST
    ranking = Column(String, primary_key=True)       # night_up / night_down / night_volume
    code = Column(String(5), primary_key=True)       # stocks.code と同じ 5 桁。FK は付けない
    session_date = Column(Date, nullable=False)      # 比べている通常取引の終値の日
    as_of = Column(String(5))                        # 株探の「HH:MM現在」（15 分ディレイ）
    rank = Column(Integer, nullable=False)
    name = Column(String)
    market = Column(String)
    base_close = Column(Float)
    pts_price = Column(Float)
    pts_chg_pct = Column(Float)
    volume = Column(Integer)
    __table_args__ = (Index("ix_pts_session_ranking", "session_date", "ranking"),)


class EdinetFiler(Base):
    """EDINET コードリスト（提出者）の写し。上場していない提出者（大量保有の保有者など）も持つ。

    取込は jobs/edinet/edinet_codes.py。全件置き換えでなく UPSERT（リストから消えた提出者も残す）。
    """
    __tablename__ = "edinet_filers"
    edinet_code = Column(String(6), primary_key=True)
    sec_code = Column(String(5), index=True)        # 証券コード 5 桁。stocks.code と同じ形
    filer_name = Column(String)
    filer_name_en = Column(String)
    filer_type = Column(String)                     # 提出者種別（内国法人・組合 / 外国法人・組合 / 個人 など）
    listed = Column(String)                         # 上場区分（上場 / 非上場）
    is_consolidated = Column(String)                # 連結の有無（有 / 無）
    capital = Column(String)                        # 資本金（百万円）。リストの文字列のまま
    fiscal_year_end = Column(String)                # 決算日（例 3月31日）
    industry = Column(String)                       # 提出者業種
    jcn = Column(String(13))                        # 提出者法人番号
    list_date = Column(Date)                        # リストのダウンロード実行日
    updated_at = Column(DateTime, nullable=False)


class EdinetDocument(Base):
    """EDINET 書類一覧 API（type=2）の 1 行。docID は一意でない（修正・不開示・取下げが別レコードで出る）。

    元の行の状態は後の日のイベント行で書き換える（app/edinet/documents.py apply_events）。
    """
    __tablename__ = "edinet_documents"
    file_date = Column(Date, primary_key=True)
    seq_number = Column(Integer, primary_key=True)
    doc_id = Column(String(8), nullable=False, index=True)
    code = Column(String(5), index=True)            # stocks.code。secCode か edinet_filers から引く。FK は付けない
    edinet_code = Column(String(6), index=True)
    sec_code = Column(String(5))
    jcn = Column(String(13))
    filer_name = Column(String)
    fund_code = Column(String(6))
    ordinance_code = Column(String(3))
    form_code = Column(String(6))
    doc_type_code = Column(String(3), index=True)
    period_start = Column(Date)
    period_end = Column(Date)
    submit_datetime = Column(DateTime)              # JST naive
    doc_description = Column(Text)
    issuer_edinet_code = Column(String(6), index=True)
    subject_edinet_code = Column(String(6), index=True)
    subsidiary_edinet_codes = Column(Text)          # カンマ区切り
    current_report_reasons = Column(Text)           # カンマ区切り
    parent_doc_id = Column(String(8))
    ope_datetime = Column(DateTime)
    withdrawal_status = Column(String(1), nullable=False)
    doc_info_edit_status = Column(String(1), nullable=False)
    disclosure_status = Column(String(1), nullable=False)
    xbrl_flag = Column(Boolean, nullable=False)
    pdf_flag = Column(Boolean, nullable=False)
    attach_doc_flag = Column(Boolean, nullable=False)
    english_doc_flag = Column(Boolean, nullable=False)
    csv_flag = Column(Boolean, nullable=False)
    legal_status = Column(String(1), nullable=False)
    fetched_at = Column(DateTime, nullable=False)
    __table_args__ = (
        Index("ix_edinet_documents_code_type", "code", "doc_type_code", "submit_datetime"),
    )


class EdinetArchive(Base):
    """EDINET の書類取得 API の ZIP を残した記録（KAB-281）。中身は data/edinet → OneDrive。

    status: saved（data/edinet に書いた。翌朝 backup_data が OneDrive へ move する）/
    gone（取り直しのとき EDINET が 404。残っていない）。
    """
    __tablename__ = "edinet_archives"
    doc_id = Column(String(8), primary_key=True)
    file_type = Column(Integer, primary_key=True)    # 書類取得 API の type（1 / 5）
    doc_type_code = Column(String(3))
    rel_path = Column(Text, nullable=False)           # data/edinet からの相対パス
    status = Column(String(8), nullable=False)
    bytes = Column(Integer)
    sha256 = Column(String(64))
    saved_at = Column(DateTime, nullable=False)       # JST naive


class EdinetListDay(Base):
    """書類一覧を取った日ごとの記録。finalized の日は取り直さない。"""
    __tablename__ = "edinet_list_days"
    file_date = Column(Date, primary_key=True)
    count = Column(Integer, nullable=False)          # metadata.resultset.count
    max_seq = Column(Integer)
    process_datetime = Column(DateTime)              # EDINET 側の一覧作成時刻（JST naive）
    finalized = Column(Boolean, nullable=False)      # file_date より後（JST）に取った = その日の一覧は確定
    fetched_at = Column(DateTime, nullable=False)


class LargeHoldingReport(Base):
    """大量保有報告書・変更報告書・訂正（EDINET 書類種別 350 / 360）の 1 書類（KAB-74）。

    中身は書類取得 API type=5（XBRL→CSV）。取込は app/edinet/large_holdings.py。
    訂正は大元（original_doc_id）に束ね、取下げでない最新の 1 件に is_latest を立てる。
    """
    __tablename__ = "large_holding_reports"
    doc_id = Column(String(8), primary_key=True)
    file_date = Column(Date, nullable=False)
    submit_datetime = Column(DateTime)              # JST naive
    doc_type_code = Column(String(3), nullable=False)  # 350 大量保有・変更報告書 / 360 訂正
    form_code = Column(String(6))                   # 010000 大量保有 / 010002 変更 / 020002 短期大量譲渡 / 03xxxx 特例 など
    title = Column(Text)                            # 表紙の書類名（変更報告書ＮＯ．3 など）
    filer_edinet_code = Column(String(6), index=True)
    filer_name = Column(String)
    issuer_edinet_code = Column(String(6))
    issuer_sec_code = Column(String(5))             # 発行者の証券コード（CSV の 4 桁に 0 を付けた 5 桁）
    issuer_name = Column(String)
    code = Column(String(5))                        # stocks.code。上場していなければ NULL。FK は付けない
    obligation_date = Column(Date)                  # 報告義務発生日
    filing_date = Column(Date)                      # 表紙の提出日
    reason = Column(Text)                           # 提出事由（変更報告書だけ）
    arrangement = Column(String)                    # 提出形態（連名 / その他）
    holder_count = Column(Integer)                  # 提出者及び共同保有者の総数
    shares_held = Column(BigInteger)                # 保有株券等の数（合計）
    outstanding_shares = Column(BigInteger)         # 発行済株式等総数
    holding_ratio = Column(Float)                   # 株券等保有割合（0.0507 = 5.07%）
    holding_ratio_prev = Column(Float)              # 直前の報告書の保有割合。大量保有報告書は NULL
    joint_holders_text = Column(Text)               # 第3 共同保有者に関する事項（該当なしは NULL）
    is_amendment = Column(Boolean, nullable=False, default=False)
    amends_doc_id = Column(String(8))               # 訂正の対象（DEI の IdentificationOfDocumentSubjectToAmendment）
    original_doc_id = Column(String(8), nullable=False, index=True)  # 訂正をたどった大元。訂正でなければ自分
    is_latest = Column(Boolean, nullable=False, default=False)  # 大元ごとに取下げでない最新の 1 件
    withdrawn = Column(Boolean, nullable=False, default=False)  # 取下げ・不開示（edinet_documents から）
    parse_status = Column(String, nullable=False)   # parsed / failed
    parse_error = Column(Text)
    fetched_at = Column(DateTime, nullable=False)
    __table_args__ = (
        Index("ix_large_holding_reports_code", "code", "obligation_date"),
        Index("ix_large_holding_reports_file_date", "file_date"),
        Index("ix_large_holding_reports_latest_file", "is_latest", "file_date"),
    )


class LargeHolding(Base):
    """大量保有の 1 書類 × 提出者・共同保有者 1 人（CSV の FilerLargeVolumeHolder<N>Member）。"""
    __tablename__ = "large_holdings"
    doc_id = Column(String(8), primary_key=True)
    holder_no = Column(Integer, primary_key=True)   # 書類の中の番号（1 始まり）
    edinet_code = Column(String(6), index=True)
    name = Column(String)
    holder_type = Column(String)                    # 個人・法人の別（法人（株式会社）など）
    shares_held = Column(BigInteger)
    outstanding_shares = Column(BigInteger)
    holding_ratio = Column(Float)
    holding_ratio_prev = Column(Float)
    base_date = Column(Date)                        # 保有の基準日
    purpose = Column(Text)                          # 保有目的
    important_proposal = Column(Text)               # 重要提案行為等（該当なしは NULL）
    own_funds = Column(BigInteger)                  # 取得資金: 自己資金額（円）
    borrowings = Column(BigInteger)                 # 借入金額計
    other_funds = Column(BigInteger)                # その他金額計
    total_funds = Column(BigInteger)                # 取得資金合計


class EdinetBuyback(Base):
    """自己株券買付状況報告書・訂正（EDINET 書類種別 220 / 230）の 1 書類（KAB-79）。

    中身は書類取得 API type=1（XBRL）のテキストブロックの表。取込は app/edinet/buybacks.py。
    決議ごとの枠・買付・累計は edinet_buyback_resolutions。訂正は大元に束ね、最新に is_latest。
    """
    __tablename__ = "edinet_buybacks"
    doc_id = Column(String(8), primary_key=True)
    file_date = Column(Date, nullable=False)
    submit_datetime = Column(DateTime)              # JST naive
    doc_type_code = Column(String(3), nullable=False)  # 220 報告書 / 230 訂正
    form_code = Column(String(6))                   # 170000 株式会社 / 253000 投資法人
    code = Column(String(5))                        # stocks.code。上場していなければ NULL。FK は付けない
    edinet_code = Column(String(6), index=True)
    filer_name = Column(String)
    report_month = Column(String(7))                # 報告月 YYYY-MM（報告期間の末日の月）
    period_start = Column(Date)                     # 報告期間
    period_end = Column(Date)
    issued_shares = Column(BigInteger)              # 報告月末の発行済株式総数（投資法人は投資口）
    treasury_shares = Column(BigInteger)            # 報告月末の保有自己株式数
    resolution_count = Column(Integer)              # 決議の数（edinet_buyback_resolutions の行数）
    month_shares = Column(BigInteger)               # 報告月に取得した株数（決議の合計）
    month_amount = Column(BigInteger)               # 報告月に取得した価額の総額（円）
    is_amendment = Column(Boolean, nullable=False, default=False)
    amends_doc_id = Column(String(8))
    original_doc_id = Column(String(8), nullable=False, index=True)
    is_latest = Column(Boolean, nullable=False, default=False)
    withdrawn = Column(Boolean, nullable=False, default=False)
    parse_status = Column(String, nullable=False)   # parsed / meta（取得状況が対象外の訂正）/ failed
    parse_error = Column(Text)
    fetched_at = Column(DateTime, nullable=False)
    __table_args__ = (
        Index("ix_edinet_buybacks_code", "code", "report_month"),
    )


class EdinetBuybackResolution(Base):
    """自己株券買付状況報告書の 1 書類 × 決議 1 件（取締役会・株主総会・投資法人の役員会）。"""
    __tablename__ = "edinet_buyback_resolutions"
    doc_id = Column(String(8), primary_key=True)
    resolution_no = Column(Integer, primary_key=True)  # 書類の中の順番（1 始まり）
    kind = Column(String, nullable=False)           # board / shareholders
    body = Column(String)                           # 決議した機関（取締役会・定時株主総会・役員会）
    resolution_date = Column(Date)                  # 決議日
    acquire_from = Column(Date)                     # 取得期間
    acquire_to = Column(Date)
    limit_shares = Column(BigInteger)               # 決議の枠（株数）。無ければ NULL
    limit_amount = Column(BigInteger)               # 決議の枠（価額の総額・円）
    month_shares = Column(BigInteger)               # 報告月の取得（「－」は 0）
    month_amount = Column(BigInteger)
    cumulative_shares = Column(BigInteger)          # 報告月末までの累計
    cumulative_amount = Column(BigInteger)
    progress_shares = Column(Float)                 # 進捗（書類の値。0.5297 = 52.97%）
    progress_amount = Column(Float)
    label = Column(Text)                            # 決議状況の行の見出し（そのまま）


class EdinetTenderOffer(Base):
    """公開買付け 1 件（KAB-79）。届出書（240）を大元に、訂正・撤回・意見表明・報告書から決め直す。

    書類ごとの値は edinet_tender_offer_docs。取込は app/edinet/tender_offers.py。
    """
    __tablename__ = "edinet_tender_offers"
    tender_offer_id = Column(String(8), primary_key=True)  # 大元の docID（ふつうは公開買付届出書）
    is_issuer = Column(Boolean, nullable=False, default=False)  # 発行者による公開買付け（自社株 TOB）
    offeror_edinet_code = Column(String(6), index=True)
    offeror_name = Column(String)
    offeror_code = Column(String(5))                # 買付者が上場していれば stocks.code
    target_edinet_code = Column(String(6), index=True)
    target_name = Column(String)
    target_code = Column(String(5), index=True)     # 対象者の stocks.code
    announced_date = Column(Date)                   # 届出書の提出日
    period_start = Column(Date)                     # 買付け等の期間（訂正で延長したら延長後）
    period_end = Column(Date)
    price = Column(Float)                           # 普通株式 1 株の買付価格（円。訂正で引き上げたら後の値）
    price_initial = Column(Float)                   # 届出書の当初の価格
    planned_shares = Column(BigInteger)             # 買付予定数
    min_shares = Column(BigInteger)                 # 買付予定数の下限（無ければ NULL）
    max_shares = Column(BigInteger)                 # 買付予定数の上限（無ければ NULL）
    holding_ratio_after = Column(Float)             # 買付け後の株券等所有割合（届出書は予定、報告書は結果）
    opinion = Column(String)                        # 対象者の意見: support / oppose / neutral / reserved
    recommendation = Column(String)                 # 応募: recommend / leave / not_recommend
    status = Column(String, nullable=False)         # open / succeeded / failed / withdrawn
    tendered_shares = Column(BigInteger)            # 応募数（株式換算）
    purchased_shares = Column(BigInteger)           # 買付数
    result_date = Column(Date)                      # 公開買付報告書の提出日
    latest_doc_id = Column(String(8))               # いちばん新しい書類
    doc_count = Column(Integer)
    updated_at = Column(DateTime, nullable=False)


class EdinetTenderOfferDoc(Base):
    """公開買付けの書類 1 件（240〜340）。届出・訂正・撤回・報告書・意見表明・対質問回答など。"""
    __tablename__ = "edinet_tender_offer_docs"
    doc_id = Column(String(8), primary_key=True)
    tender_offer_id = Column(String(8), nullable=False, index=True)  # 大元（parent_doc_id をたどる）
    file_date = Column(Date, nullable=False)
    submit_datetime = Column(DateTime)
    doc_type_code = Column(String(3), nullable=False)
    ordinance_code = Column(String(3))              # 040 他社株 / 050 発行者（自社株）
    form_code = Column(String(6))
    title = Column(Text)
    filer_edinet_code = Column(String(6))
    filer_name = Column(String)
    subject_edinet_code = Column(String(6))         # 届出書・報告書は対象者、意見表明は買付者
    parent_doc_id = Column(String(8))
    withdrawn = Column(Boolean, nullable=False, default=False)  # 取下げ・不開示
    period_start = Column(Date)
    period_end = Column(Date)
    price = Column(Float)
    planned_shares = Column(BigInteger)
    min_shares = Column(BigInteger)
    max_shares = Column(BigInteger)
    holding_ratio_after = Column(Float)
    target_name = Column(String)
    opinion = Column(String)
    recommendation = Column(String)
    opinion_text = Column(Text)                     # 意見の内容（最初の段落）
    success = Column(Boolean)                       # 公開買付けの成否（報告書）
    tendered_shares = Column(BigInteger)
    purchased_shares = Column(BigInteger)
    parse_status = Column(String, nullable=False)   # parsed / meta（中身を読まない書類）/ failed
    parse_error = Column(Text)
    fetched_at = Column(DateTime, nullable=False)
