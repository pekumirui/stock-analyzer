from sqlalchemy import (
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
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
    __table_args__ = (Index("ix_prices_date", "date"),)


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
    created_at = Column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint("source", "doc_id", name="uq_xbrl_filings_source_doc"),
        Index("idx_xbrl_filings_code_submitted", "code", "submitted_at"),
        Index("idx_xbrl_filings_parse_status", "parse_status"),
    )


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


class XbrlElementMapping(Base):
    __tablename__ = "xbrl_element_mappings"
    id = Column(Integer, primary_key=True)
    std_item_id = Column(Integer, ForeignKey("xbrl_std_items.id"), nullable=False)
    namespace_key = Column(String(64), nullable=False)
    local_name = Column(String(255), nullable=False)
    priority = Column(Integer, nullable=False, default=100)
    accounting_standard = Column(String(16))
    note = Column(Text)
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
        Index(
            "idx_xbrl_financials_lookup",
            "code", "std_item_id", "is_consolidated", "period_end", "value_kind",
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


class EdinetListDay(Base):
    """書類一覧を取った日ごとの記録。finalized の日は取り直さない。"""
    __tablename__ = "edinet_list_days"
    file_date = Column(Date, primary_key=True)
    count = Column(Integer, nullable=False)          # metadata.resultset.count
    max_seq = Column(Integer)
    process_datetime = Column(DateTime)              # EDINET 側の一覧作成時刻（JST naive）
    finalized = Column(Boolean, nullable=False)      # file_date より後（JST）に取った = その日の一覧は確定
    fetched_at = Column(DateTime, nullable=False)
