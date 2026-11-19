from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    literal_column,
)
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class JstDateTime(DateTime):
    pass


class UtcDateTime(DateTime):
    pass


def _one_of(*values: str | dict[str, str], length: int | None = None) -> Enum:
    meanings = {k: v for d in values if isinstance(d, dict) for k, v in d.items()}
    names = [k for d in values for k in ([d] if isinstance(d, str) else d)]
    out = Enum(*names, native_enum=False, validate_strings=True, create_constraint=False, length=length)
    out.meanings = meanings
    return out


class Stock(Base):
    __tablename__ = "stocks"
    __table_args__ = ({"comment": "銘柄マスタ"},)
    code = Column(String(5), primary_key=True, comment='5桁コード (例 "72030", "262A0")。先頭・末尾は数字、途中は 0-9A-Z')
    name = Column(String)
    sector17 = Column(String)
    sector33 = Column(String)
    market = Column(String)
    scale_category = Column(String)
    margin_type = Column(String)
    product_category = Column(String, comment="J-Quants ProdCat。011=内国株券, 013=REIT, 014=ETF 等")
    edinet_code = Column(
        String(6), info={"ref": "edinet_filers.edinet_code"},
        comment="EDINET コードリストの証券コードで付ける。無い銘柄は NULL",
    )


class Homepage(Base):
    __tablename__ = "homepages"
    __table_args__ = ({"comment": "上場会社のホームページ URL"},)
    code = Column(String(5), primary_key=True, info={"ref": "stocks.code"}, comment="普通株（末尾 0）")
    url = Column(Text, nullable=False, comment="選んだ 1 本（会社ホームページ）")
    host = Column(String(255), nullable=False, comment="小文字。先頭 www. を除く")
    source = Column(_one_of("manual", "xbrl_url", "tanshin_html", length=32), nullable=False)
    origin = Column(Text, nullable=False, comment="manual / xbrl:{doc_id} / tanshin:{disclosure_no}")
    changed_at = Column(JstDateTime, nullable=False, comment="url / source / origin / host が変わった時刻")
    checked_at = Column(JstDateTime, nullable=False, comment="最後に確認した時刻。値が同じでも進む")


class BriefingDocument(Base):
    __tablename__ = "briefing_documents"
    source = Column(_one_of("tdnet", "irbank", "homepage", length=16), primary_key=True)
    document_id = Column(String(80), primary_key=True)
    code = Column(String(5), nullable=False, info={"ref": "stocks.code"}, comment="普通株の 5 桁")
    company_name = Column(String)
    pubdate = Column(String(32), nullable=False, comment="開示日時 YYYY-MM-DD HH:MM:SS")
    title = Column(Text, nullable=False)
    rel_path = Column(Text, nullable=False, comment="data/briefing からの相対パス")
    url = Column(Text)
    sha256 = Column(String(64))
    size = Column(Integer)
    status = Column(String(16), nullable=False, comment="saved")
    storage = Column(_one_of("local", "onedrive", length=16), nullable=False, default="local", server_default="local")
    saved_at = Column(JstDateTime, nullable=False)
    __table_args__ = (
        Index("ix_briefing_documents_code_pubdate", "code", "pubdate"),
        {"comment": "決算説明資料 PDF の索引"},
    )


class BriefingIrbankScan(Base):
    __tablename__ = "briefing_irbank_scans"
    __table_args__ = ({"comment": "IR BANK の開示一覧を読み終えた銘柄"},)
    code = Column(String(5), primary_key=True, info={"ref": "stocks.code"}, comment="代表の 5 桁（末尾 0 を優先）")
    scanned_at = Column(JstDateTime, nullable=False)
    listed = Column(Integer, nullable=False, comment="説明資料と判定した件数")
    saved = Column(Integer, nullable=False)
    skipped = Column(Integer, nullable=False, comment="開示番号が索引済み")
    reindexed = Column(Integer, nullable=False, comment="PDF は既にあり索引だけ足した")
    missing = Column(Integer, nullable=False, comment="PDF が 404")
    failed = Column(Integer, nullable=False)


class BriefingExtract(Base):
    __tablename__ = "briefing_extracts"
    __table_args__ = (
        Index("ix_briefing_extracts_code", "code"), Index("ix_briefing_extracts_batch", "batch_id"),
        {
            "info": {"ref": {"source, document_id": "briefing_documents.source, document_id"}},
            "comment": "決算説明資料の読み取り。資料 1 つに 1 行",
        },
    )
    document_id = Column(String(80), primary_key=True, comment="出所をまたいで同じ開示番号")
    source = Column(_one_of("tdnet", "irbank", "homepage", length=16), nullable=False, comment="どの出所の PDF を読んだか")
    code = Column(String(5), nullable=False, info={"ref": "stocks.code"})
    status = Column(
        _one_of("submitted", {"done": "1 ページでも読めた"}, "failed", length=16), nullable=False,
        comment="結果を受け取ったら done か failed",
    )
    batch_id = Column(String(80))
    request_key = Column(
        String(24), nullable=False,
        comment="Batch の custom_id の頭。document_id は custom_id に使えない文字を含みうる",
    )
    model = Column(String(40), nullable=False)
    prompt_version = Column(String(16), nullable=False)
    pages = Column(Integer, comment="PDF のページ数")
    llm_pages = Column(Integer, comment="Claude に回したページ数")
    error_pages = Column(Integer)
    facts = Column(Integer)
    verified = Column(Integer, comment="文字データにある数字")
    sum_checks = Column(Integer)
    sum_mismatch = Column(Integer)
    input_tokens = Column(Integer, comment="キャッシュの読み書きを含む")
    output_tokens = Column(Integer)
    error = Column(Text)
    submitted_at = Column(JstDateTime, nullable=False)
    extracted_at = Column(JstDateTime)


class BriefingPage(Base):
    __tablename__ = "briefing_pages"
    __table_args__ = ({
        "comment": "読み取った説明資料のページ。表紙・注意事項など Claude に回さないページも kind だけ残す",
    },)
    document_id = Column(String(80), primary_key=True, info={"ref": "briefing_extracts.document_id"})
    page = Column(Integer, primary_key=True)
    kind = Column(_one_of("divider", "disclaimer", "chart_or_table", "table_or_text", "text", length=24), nullable=False)
    title = Column(Text)
    summary = Column(Text)
    tables_json = Column(Text, comment="Claude が返した表（列 × 行、行の of）をそのまま")
    error = Column(Text)
    raw_text = Column(
        Text,
        comment="JSON が読めなかったページだけ、Claude の応答をそのまま残す。Batch の結果は 29 日で消える",
    )


class BriefingFact(Base):
    __tablename__ = "briefing_facts"
    __table_args__ = (
        Index("ix_briefing_facts_doc", "document_id", "page"),
        {
            "info": {"ref": {"document_id, page": "briefing_pages.document_id, page"}},
            "comment": "読み取った数値。briefing_pages の表を 1 セル 1 行に展開したもの",
        },
    )
    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(String(80), nullable=False)
    page = Column(Integer, nullable=False)
    metric = Column(Text, nullable=False)
    segment = Column(Text)
    period = Column(Text)
    period_kind = Column(String(24), comment="actual / forecast / previous_forecast / plan / change")
    value = Column(Text, nullable=False, comment="資料の表記（符号は - にそろえる）")
    value_num = Column(Float)
    unit = Column(String(24))
    part_of = Column(Text, comment="内訳の親（metric|segment）")
    verified = Column(Boolean, nullable=False, comment="文字データにある")
    sum_status = Column(_one_of("ok", "split", "paren", "partial", "mismatch", length=12), comment="親の行だけ")


class TanshinCover(Base):
    __tablename__ = "tanshin_covers"
    __table_args__ = (Index("ix_tanshin_covers_code", "code"), {"comment": "決算短信の表紙。短信ごとに1行"})
    disclosure_no = Column(String(32), primary_key=True, comment="TDnet の書類 ID（PDF/ZIP の stem）")
    code = Column(String(5), nullable=False, info={"ref": "stocks.code"}, comment="普通株の 5 桁")
    disclosed_at = Column(JstDateTime)
    title = Column(Text)
    source = Column(_one_of("xbrl_summary", "tdnet_pdf", length=16), nullable=False)
    has_material = Column(Boolean, comment="決算補足説明資料作成の有無。表紙から読めなければ NULL")
    material_note = Column(String(80))
    has_briefing = Column(Boolean, comment="決算説明会開催の有無。表紙から読めなければ NULL")
    briefing_note = Column(String(80))
    checked_at = Column(JstDateTime, nullable=False)


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    code = Column(String(5), primary_key=True, info={"ref": "stocks.code"})
    date = Column(Date, primary_key=True)
    open = Column(Float, comment="調整前")
    high = Column(Float, comment="調整前")
    low = Column(Float, comment="調整前")
    close = Column(Float, comment="調整前")
    volume = Column(Float, comment="調整前")
    turnover = Column(Float, comment="調整前")
    adj_factor = Column(Float, default=1.0)
    adj_open = Column(Float, comment="調整済")
    adj_high = Column(Float, comment="調整済")
    adj_low = Column(Float, comment="調整済")
    adj_close = Column(Float, comment="調整済")
    adj_volume = Column(Float, comment="調整済")
    market_cap = Column(Float, comment="時価総額(百万円)")
    upper_limit = Column(Integer, comment="J-Quants UL。その日にストップ高を付けたら 1。取込前の行は NULL")
    lower_limit = Column(Integer, comment="J-Quants LL。ストップ安")
    ex_rt = Column(
        String,
        comment="J-Quants ExRT。権利落ち日だけ 1=分割（無償割当を含む）/ 2=併合 / 3=ライツイシュー",
    )
    __table_args__ = (Index("ix_prices_date", "date"), {"sqlite_with_rowid": False, "comment": "日足"})


class Topix(Base):
    __tablename__ = "topix"
    __table_args__ = ({"comment": "TOPIX日足"},)
    date = Column(Date, primary_key=True)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)


class Statement(Base):
    __tablename__ = "statements"
    disclosure_no = Column(String, primary_key=True)
    code = Column(String(5), index=True, info={"ref": "stocks.code"})
    disclosed_date = Column(Date, index=True)
    disclosed_time = Column(String)
    type_of_period = Column(String, comment="1Q/2Q/3Q/4Q/5Q/FY")
    type_of_document = Column(String)
    period_start = Column(Date)
    period_end = Column(Date)
    fiscal_year_start = Column(Date)
    fiscal_year_end = Column(Date)
    net_sales = Column(Float, comment="累計実績")
    operating_profit = Column(Float, comment="累計実績")
    ordinary_profit = Column(Float, comment="累計実績")
    profit = Column(Float, comment="累計実績")
    eps = Column(Float, comment="累計実績")
    f2_net_sales = Column(Float, comment="半期(2Q)会社予想")
    f2_operating_profit = Column(Float, comment="半期(2Q)会社予想")
    f2_ordinary_profit = Column(Float, comment="半期(2Q)会社予想")
    f2_profit = Column(Float, comment="半期(2Q)会社予想")
    f2_eps = Column(Float, comment="半期(2Q)会社予想")
    fy_net_sales = Column(Float, comment="通期会社予想")
    fy_operating_profit = Column(Float, comment="通期会社予想")
    fy_ordinary_profit = Column(Float, comment="通期会社予想")
    fy_profit = Column(Float, comment="通期会社予想")
    fy_eps = Column(Float, comment="通期会社予想")
    is_revision = Column(Boolean, default=False, comment="予想修正開示フラグ")
    raw_json = Column(Text, comment="元データ全項目")
    source = Column(_one_of("jquants", "xbrl", length=16), comment="NULL は J-Quants")
    __table_args__ = (
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
        {"comment": "決算短信 (J-Quants /fins/summary ほぼ生保存。短信 XBRL から作った行も入る)"},
    )


class Indicator(Base):
    __tablename__ = "indicators"
    code = Column(String(5), primary_key=True)
    date = Column(Date, primary_key=True)
    close = Column(Float)
    ma50 = Column(Float)
    ma150 = Column(Float)
    ma200 = Column(Float)
    ma200_slope_1m = Column(Float, comment="200日線の1ヶ月変化率")
    high_52w = Column(Float)
    low_52w = Column(Float)
    rs_raw = Column(Float, comment="RS生値")
    rs_rank = Column(Integer, comment="1-99ランク")
    stage = Column(Integer, comment="1/2/3/4 (判定不能はNULL)")
    avg_volume_50 = Column(Float)
    __table_args__ = (
        Index("ix_ind_date_stage", "date", "stage"),
        {"info": {"ref": {"code, date": "daily_prices.code, date"}}, "comment": "日次算出テクニカル"},
    )


class EarningsDatePub(Base):
    __tablename__ = "earnings_date_pubs"
    code = Column(String(5), primary_key=True, info={"ref": "stocks.code"})
    fq_name = Column(String(8), primary_key=True, comment="1Q | 2Q | 3Q | FY")
    pub_date = Column(Date, primary_key=True, info={"ref": "earnings_date_fetches.pub_date"})
    sch_date = Column(Date, nullable=True, comment="NULL は未定。欠測ではない")
    fye = Column(String(4), nullable=False, comment="MMDD。0299 は 2月末")
    co_name = Column(String, nullable=False)
    __table_args__ = (
        Index("ix_earnings_date_pubs_sch_date", "sch_date"),
        {
            "comment": (
                "決算発表予定の発表履歴。発表のたびに 1 行。現在の予定は (code, fq_name) で pub_date が最新の行。"
                "移動前の日が残っている"
            ),
        },
    )


class EarningsDateFetch(Base):
    __tablename__ = "earnings_date_fetches"
    __table_args__ = ({"comment": "取り終えた発表日。空の応答も n=0 で残す。pubs の写しではない"},)
    pub_date = Column(Date, primary_key=True)
    n = Column(Integer, nullable=False)


class EarningsEvent(Base):
    __tablename__ = "earnings_events"
    __table_args__ = ({
        "info": {"ref": {
            "code, disclosed_date": "statements.code, disclosed_date",
            "code, next_disclosed_date": "statements.code, disclosed_date",
            "code, base_date": "daily_prices.code, date",
            "code, end_date": "daily_prices.code, date",
        }},
        "comment": "決算発表イベントと発表後パフォーマンス",
    },)
    code = Column(String(5), primary_key=True)
    disclosed_date = Column(Date, primary_key=True)
    base_date = Column(Date, comment="決算後に買えた最初の値")
    base_price = Column(Float, comment="決算後に買えた最初の値")
    return_1d = Column(Float)
    return_5d = Column(Float)
    return_20d = Column(Float)
    return_60d = Column(Float)
    return_to_date = Column(Float)
    alpha_1d = Column(Float, comment="対TOPIX超過リターン")
    alpha_5d = Column(Float, comment="対TOPIX超過リターン")
    alpha_20d = Column(Float, comment="対TOPIX超過リターン")
    alpha_60d = Column(Float, comment="対TOPIX超過リターン")
    alpha_to_date = Column(Float, comment="対TOPIX超過リターン")
    disclosed_time = Column(String, comment="その日の決算短信のうち一番早い発表時刻")
    is_first = Column(Boolean, comment="その期（決算期 × 四半期）の最初の決算短信。訂正・予想修正だけは偽")
    next_disclosed_date = Column(Date, comment="同じ銘柄の次の期の最初の決算短信の日。まだ無ければ NULL")
    end_date = Column(Date, comment="return_to_next の終点（次の決算の前営業日。無ければ株価の最終日）")
    return_to_next = Column(Float, comment="次の決算の前日終値まで（%）")
    alpha_to_next = Column(Float, comment="対TOPIX超過リターン")


class MonthlyIssuer(Base):
    __tablename__ = "monthly_issuers"
    __table_args__ = ({"comment": "発行体ごとの月次属性"},)
    code = Column(String(5), primary_key=True, info={"ref": "stocks.code"})
    industry = Column(
        String(50), nullable=False, default="その他", server_default="その他",
        comment="月次業種。stocks.sector33 とは別",
    )
    last_ingested_at = Column(
        JstDateTime, nullable=True, comment="ingest でセルが変わったときだけ更新。NULL は新着ではない",
    )


class MonthlyTable(Base):
    __tablename__ = "monthly_tables"
    __table_args__ = ({"comment": "銘柄×年度の月次表 (暦月1〜12。会計月ではない)"},)
    code = Column(String(5), primary_key=True, info={"ref": "monthly_issuers.code"})
    year = Column(Integer, primary_key=True, comment="西暦4桁")


class MonthlyElement(Base):
    __tablename__ = "monthly_elements"
    __table_args__ = ({"info": {"ref": {"code, year": "monthly_tables.code, year"}}, "comment": "月次表の行"},)
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)
    element = Column(String(50), primary_key=True, comment="登録時の正規名")
    sort_order = Column(Integer, nullable=False, comment="登録順")
    unit = Column(String(16), nullable=False, default="number", server_default="number", comment="number | yoy_index")


class MonthlyElementSource(Base):
    __tablename__ = "monthly_element_sources"
    __table_args__ = ({
        "info": {"ref": {"code, year, element": "monthly_elements.code, year, element"}},
        "comment": "送られてきたままの行名 → 今の保存名。正規化の差分スナップショットの入力",
    },)
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)
    source_label = Column(String(50), primary_key=True, comment="受け取ったまま（正規化前）。前後の空白だけ落とす")
    unit = Column(String(16), primary_key=True, comment="受け取ったまま（正規化前）。yoy_pct 可")
    element = Column(String(50), nullable=False, index=True, comment="今の保存名")
    via = Column(
        _one_of("api", "doc", "ir_html", "ir_json", "ir_xls", {"legacy": "記録前からある行。保存名そのものを入れる"}, length=16),
        nullable=False, comment="経路",
    )
    first_seen_at = Column(JstDateTime, nullable=False)
    last_seen_at = Column(JstDateTime, nullable=False)


class MonthlyValue(Base):
    __tablename__ = "monthly_values"
    __table_args__ = ({
        "info": {"ref": {"code, year, element": "monthly_elements.code, year, element"}},
        "comment": "月次セル",
    },)
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)
    element = Column(String(50), primary_key=True)
    month = Column(Integer, primary_key=True)
    value = Column(Float, nullable=False, comment="数値のみ (単位・カンマ付き文字列は拒否)")


class DataVersion(Base):
    __tablename__ = "data_versions"
    __table_args__ = ({"comment": "読み取り結果を使い回すための版。対象表のトリガーが version を +1 する"},)
    name = Column(
        String, primary_key=True,
        comment="使い回す結果の名前。monthly_list は月次一覧（/monthly、/api/v1/tables、/sitemap.xml）",
    )
    version = Column(Integer, nullable=False, default=0, server_default="0")


class XbrlFiling(Base):
    __tablename__ = "xbrl_filings"
    id = Column(Integer, primary_key=True)
    code = Column(String(5), nullable=False, index=True, info={"ref": "stocks.code"})
    source = Column(_one_of("tdnet", "edinet", "tdnet_pdf", length=16), nullable=False, default="tdnet")
    doc_id = Column(
        String(64), nullable=False,
        comment="tdnet は TDnet xbrl_id（ZIP 名）、edinet は EDINET の docID、tdnet_pdf は開示番号 14 桁",
    )
    doc_type = Column(
        _one_of("annual", "quarterly", "semiannual", "forecast_revision", "dividend_revision", "other", length=32),
        nullable=False, default="other",
    )
    period_type = Column(_one_of("FY", "Q1", "Q2", "Q3", length=8))
    fiscal_year_end = Column(Date)
    submitted_at = Column(JstDateTime, nullable=False)
    title = Column(Text)
    accounting_standard = Column(_one_of("JPGAAP", "IFRS", "USGAAP", length=16))
    is_amendment = Column(Boolean, nullable=False, default=False)
    raw_path = Column(
        Text, nullable=False,
        comment="tdnet は data/xbrl からの相対パス（manifest.path）。edinet は edinet:<doc_id>、tdnet_pdf は tdnet_pdf:<開示番号>",
    )
    raw_sha256 = Column(String(64), comment="manifest.sha256")
    parse_status = Column(_one_of("pending", "parsed", "failed", "skipped", length=16), nullable=False, default="pending")
    parse_error = Column(Text)
    parsed_at = Column(UtcDateTime)
    documents_status = Column(
        _one_of("stored", {"empty": "選べる HTML が 0 件"}, length=16), comment="原文。NULL=まだ",
    )
    presentation_status = Column(_one_of("ok", "none", "gone", "failed", length=8), comment="表示リンク")
    created_at = Column(UtcDateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint("source", "doc_id", name="uq_xbrl_filings_source_doc"),
        Index("idx_xbrl_filings_code_submitted", "code", "submitted_at"),
        Index("idx_xbrl_filings_parse_status", "parse_status"),
        Index(
            "idx_xbrl_filings_id_cover",
            "id", "parse_status", "source", "doc_id", "submitted_at",
        ),
        {"comment": "開示 1 件（1 ZIP = 1 filing）"},
    )


class XbrlDocument(Base):
    __tablename__ = "xbrl_documents"
    __table_args__ = ({"comment": "短信 ZIP の表示用 HTML。書類ごとに表示順で並べる"},)
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), primary_key=True)
    seq = Column(Integer, primary_key=True, comment="表示順。0 から")
    part = Column(
        _one_of({
            "summary": "サマリー",
            "qualitative": "添付資料まるごと",
            "attachment": "qualitative.htm が無い書類の本表 iXBRL",
        }, length=16),
        nullable=False,
    )
    file_name = Column(Text, nullable=False, comment="ZIP の中のファイル名（パスなし）")
    html_gz = Column(LargeBinary, nullable=False, comment="元の HTML を gzip。script などの除去は表示のとき")
    bytes = Column(Integer, nullable=False, comment="圧縮前")
    sha256 = Column(String(64), nullable=False, comment="圧縮前")
    created_at = Column(UtcDateTime, nullable=False)


class TdnetPdfDocument(Base):
    __tablename__ = "tdnet_pdf_documents"
    __table_args__ = ({"comment": "TDnet の開示 PDF（IR BANK の保管分）を HTML に変換したもの"},)
    disclosure_no = Column(
        String(14), primary_key=True, info={"ref": "statements.disclosure_no"},
        comment="TDnet の docID（= XBRL ZIP 名の後ろ 14 桁）",
    )
    code = Column(String(5), nullable=False, index=True, info={"ref": "stocks.code"}, comment="statements から")
    disclosed_date = Column(Date, comment="statements から")
    disclosed_time = Column(String(8), comment="statements から")
    title = Column(Text, comment="1 ページ目の題名。取れなければ statements.type_of_document")
    status = Column(
        _one_of({"ok": "html あり", "missing": "PDF が 404", "failed": "取得・変換の失敗"}, length=16), nullable=False,
    )
    error = Column(Text, comment="status が failed のときの理由")
    html_gz = Column(LargeBinary, comment="変換後の HTML を gzip")
    bytes = Column(Integer, comment="変換後 HTML（圧縮前）")
    sha256 = Column(String(64), comment="変換後 HTML（圧縮前）")
    pages = Column(Integer, comment="元の PDF")
    pdf_bytes = Column(Integer, comment="元の PDF")
    pdf_sha256 = Column(String(64), comment="元の PDF")
    converter = Column(String(16), comment="変換器の版（tools/tdnet_pdf/convert.py CONVERTER）")
    created_at = Column(UtcDateTime, nullable=False)


class XbrlElement(Base):
    __tablename__ = "xbrl_elements"
    id = Column(Integer, primary_key=True)
    namespace_uri = Column(Text, nullable=False)
    namespace_key = Column(String(64), nullable=False, comment="'jppfs_cor' / 'tse-ed-t' / 'jpigp_cor' / 'custom' 等")
    local_name = Column(String(255), nullable=False)
    label_ja = Column(Text)
    label_en = Column(Text)
    item_type = Column(String(16))
    is_custom = Column(Boolean, nullable=False, default=False)
    first_seen_at = Column(UtcDateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint("namespace_uri", "local_name", name="uq_xbrl_elements_ns_local"),
        Index("idx_xbrl_elements_key", "namespace_key", "local_name"),
        Index("idx_xbrl_elements_local", "local_name"),
        {"comment": "XBRL の要素。名前空間 × 名前で 1 行"},
    )


Index(
    "idx_xbrl_elements_ext_key",
    literal_column("'ext:'").concat(XbrlElement.__table__.c.local_name),
    _table=XbrlElement.__table__,
)


class XbrlContext(Base):
    __tablename__ = "xbrl_contexts"
    id = Column(Integer, primary_key=True)
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), nullable=False)
    context_ref = Column(String(255), nullable=False)
    period_type = Column(_one_of("instant", "duration", length=16), nullable=False)
    start_date = Column(Date)
    end_date = Column(Date, nullable=False)
    is_consolidated = Column(Integer, nullable=False, default=1, comment="1=連結 / 0=単体。判定不能は 1")
    relative_period = Column(_one_of("current", "prior1", "prior2", length=16))
    dimensions_json = Column(Text, nullable=False, default="{}")
    has_dimensions = Column(Boolean, nullable=False, default=False)
    member_labels_json = Column(
        Text,
        comment="次元メンバー → その書類のラベル（日本語）。標準メンバーも会社が名前を付け替える",
    )
    __table_args__ = (
        UniqueConstraint("filing_id", "context_ref", name="uq_xbrl_contexts_filing_ref"),
        Index("idx_xbrl_contexts_filing", "filing_id"),
        {"comment": "開示ごとのコンテキスト（期間・連結か個別か・次元）"},
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
    value_num = Column(Numeric(30, 4), comment="scale/sign 適用後")
    value_text = Column(Text)
    is_nil = Column(Boolean, nullable=False, default=False)
    __table_args__ = (
        UniqueConstraint(
            "filing_id", "element_id", "context_id", "unit",
            name="uq_xbrl_facts_key",
        ),
        Index("idx_xbrl_facts_filing", "filing_id"),
        Index("idx_xbrl_facts_element", "element_id"),
        {"comment": "開示の値（生データ層）。開示 × 要素 × コンテキスト"},
    )


class XbrlPresentationRole(Base):
    __tablename__ = "xbrl_presentation_roles"
    __table_args__ = ({"comment": "書類の表示リンク（*pre.xml）のうち財務3表の区分"},)
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), primary_key=True)
    role = Column(String(255), primary_key=True, comment="区分名（role URI の最後。'rol_ConsolidatedBalanceSheet'）")
    seq = Column(Integer, nullable=False, comment="書類の中で保存した区分の順（0 から）")
    statement = Column(_one_of("BS", "PL", "CF", length=8), nullable=False)
    is_consolidated = Column(Boolean, nullable=False)
    is_ifrs = Column(Boolean, nullable=False)
    items_json = Column(
        Text, nullable=False,
        comment=(
            "区分の要素を表示の順に並べた JSON の配列。1 要素は [namespace_key, local_name, is_custom(0/1), "
            "preferredLabel の種類 or null, 深さ, 見出しの表示名 or null]。"
            "深さは区分の根からの段（0 から。落とした構造の要素は数えない）。"
            "表示名は見出しの要素（名前に Abstract）だけ。深さと表示名の無い古い行は 4 つ"
        ),
    )
    generated_at = Column(UtcDateTime, nullable=False)


class XbrlStdItem(Base):
    __tablename__ = "xbrl_std_items"
    id = Column(Integer, primary_key=True)
    code = Column(String(64), nullable=False, unique=True, comment="'net_sales'")
    statement = Column(_one_of("BS", "PL", "CF", "OTHER", "SEG", length=8), nullable=False)
    section = Column(String(64))
    label_ja = Column(String(128), nullable=False)
    display_order = Column(Integer, nullable=False, default=0)
    sign = Column(_one_of("natural", "invert", length=16), nullable=False, default="natural")
    is_required = Column(Boolean, nullable=False, default=False)
    unit = Column(
        String(32),
        comment=(
            "xbrl_financials.unit に入れる単位。NULL は fact の単位（JPY / USD / JPYPerShares 等）。"
            "fact が pure（人数・年数・比率）のときだけ科目側で persons / years / ratio と決める"
        ),
    )
    __table_args__ = ({"comment": "標準科目"},)


class XbrlElementMapping(Base):
    __tablename__ = "xbrl_element_mappings"
    id = Column(Integer, primary_key=True)
    std_item_id = Column(Integer, ForeignKey("xbrl_std_items.id"), nullable=False)
    namespace_key = Column(String(64), nullable=False)
    local_name = Column(String(255), nullable=False)
    priority = Column(Integer, nullable=False, default=100)
    accounting_standard = Column(String(16), comment="NULL=全共通")
    note = Column(Text)
    part = Column(
        _one_of({"months": "同じ科目・context の値に 月÷12 を足す（有報の「40歳3ヶ月」は年と月が別の要素）"}, length=16),
        comment="NULL は値そのもの",
    )
    __table_args__ = (
        UniqueConstraint(
            "std_item_id", "namespace_key", "local_name", "accounting_standard",
            name="uq_xbrl_element_mappings",
        ),
        Index("idx_xbrl_mappings_elem", "namespace_key", "local_name"),
        {"comment": "XBRL の要素 → 標準科目の対応"},
    )


class XbrlFinancial(Base):
    __tablename__ = "xbrl_financials"
    id = Column(Integer, primary_key=True)
    code = Column(String(5), nullable=False, info={"ref": "stocks.code"})
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), nullable=False)
    std_item_id = Column(Integer, ForeignKey("xbrl_std_items.id"), nullable=False)
    is_consolidated = Column(Boolean, nullable=False)
    period_type = Column(_one_of("FY", "Q1", "Q2", "Q3", length=8), nullable=False)
    period_start = Column(Date)
    period_end = Column(Date, nullable=False)
    value = Column(Numeric(30, 4), nullable=False)
    unit = Column(String(32), nullable=False, default="JPY")
    value_kind = Column(
        _one_of("actual", "forecast", {"forecast_previous": "前回予想（EarnForecastRevision の PreviousMember）"}, length=24),
        nullable=False, default="actual", server_default="actual",
    )
    range_side = Column(
        _one_of({"point": "点予想"}, "lower", "upper", length=16), nullable=False, default="point", server_default="point",
        comment="レンジ予想",
    )
    target_period = Column(
        _one_of("FY", "H1", "Q1", "Q2", "Q3", "year_end", "annual", length=16),
        nullable=False, default="FY", server_default="FY", comment="数値が指す期間",
    )
    source_fact_id = Column(Integer, ForeignKey("xbrl_facts.id"))
    source_element_id = Column(Integer, ForeignKey("xbrl_elements.id"))
    source_layer = Column(_one_of({"primary": "本表", "summary": "短信サマリー（tse-ed-t / tse-re-t / pdf-sum）"}, length=16))
    generated_at = Column(UtcDateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "filing_id", "std_item_id", "is_consolidated", "period_end",
            "value_kind", "range_side", "target_period",
            name="uq_xbrl_financials_key",
        ),
        Index(
            "idx_xbrl_financials_gp_cover",
            "code", "std_item_id", "value_kind", "is_consolidated",
            "period_start", "period_end", "value", "filing_id",
        ),
        {"comment": "標準科目ごとの値（正規化層）。xbrl_facts から再生成できる"},
    )


class XbrlPdfMatch(Base):
    __tablename__ = "xbrl_pdf_matches"
    fact_id = Column(Integer, ForeignKey("xbrl_facts.id"), primary_key=True)
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), nullable=False)
    code = Column(String(5), nullable=False, info={"ref": "stocks.code"})
    is_consolidated = Column(Boolean, nullable=False)
    statement = Column(_one_of("BS", "PL", "CF", length=8), nullable=False)
    element_key = Column(String(300), comment="行のキー（'AdvancesReceived' / 'ext:<local_name>'）。付かなければ NULL")
    sign = Column(Integer, nullable=False, default=1, comment="PDF の値に掛けると XBRL の向き")
    matched_by = Column(_one_of("value", "name", length=8), comment="付かなければ NULL")
    generated_at = Column(UtcDateTime, nullable=False)
    __table_args__ = (
        Index("idx_xbrl_pdf_matches_filing", "filing_id"),
        Index("idx_xbrl_pdf_matches_code", "code"),
        Index("idx_xbrl_pdf_matches_key", "element_key", "code"),
        {"comment": "短信 PDF の行 → XBRL の要素。PDF だけの期の本表の当期 fact 1 つに 1 行。値は持たない"},
    )


class XbrlSegment(Base):
    __tablename__ = "xbrl_segments"
    id = Column(Integer, primary_key=True)
    code = Column(String(5), nullable=False, info={"ref": "stocks.code"})
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), nullable=False)
    std_item_id = Column(Integer, ForeignKey("xbrl_std_items.id"), nullable=False, comment="statement='SEG'")
    axis = Column(String(255), nullable=False, comment="'jpcrp_cor:OperatingSegmentsAxis'")
    member = Column(String(255), nullable=False, comment="'prefix:localName'（会社独自は jpcrp030000-asr_E...）")
    member_label_ja = Column(String(255), comment="その書類のラベル")
    member_kind = Column(
        _one_of({
            "segment": "各セグメント",
            "subtotal": "報告セグメント計",
            "other": "その他",
            "total": "合計",
            "reconciling": "調整額",
            "unknown": "どれにも当たらない",
        }, length=16),
        nullable=False,
    )
    is_consolidated = Column(Boolean, nullable=False)
    period_type = Column(_one_of("FY", "Q1", "Q2", "Q3", length=8), nullable=False, comment="filing の period_type")
    period_start = Column(Date)
    period_end = Column(Date, nullable=False)
    value = Column(Numeric(30, 4), nullable=False)
    unit = Column(String(32), nullable=False, default="JPY")
    source_fact_id = Column(Integer, ForeignKey("xbrl_facts.id"))
    source_element_id = Column(Integer, ForeignKey("xbrl_elements.id"), comment="どの物差しを採ったか")
    generated_at = Column(UtcDateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "filing_id", "std_item_id", "axis", "member", "is_consolidated", "period_end",
            name="uq_xbrl_segments_key",
        ),
        Index("idx_xbrl_segments_lookup", "code", "std_item_id", "period_end"),
        Index("idx_xbrl_segments_filing", "filing_id"),
        {"comment": "セグメント注記の正規化層。xbrl_financials と同じく再生成できる"},
    )


class XbrlShareholderFiling(Base):
    __tablename__ = "xbrl_shareholder_filings"
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), primary_key=True)
    code = Column(String(5), nullable=False, info={"ref": "stocks.code"})
    doc_id = Column(String(64), nullable=False)
    as_of = Column(Date, comment="大株主・所有者別の instant（期末）")
    basis = Column(_one_of("shares", "voting_rights", "both", length=16), nullable=False)
    shares_per_unit = Column(Integer, comment="単元株式数（普通株式。無ければ NULL）")
    is_amendment = Column(Boolean, nullable=False, default=False)
    amends_doc_id = Column(String(8), info={"ref": "xbrl_shareholder_filings.doc_id"})
    original_doc_id = Column(String(8), nullable=False, info={"ref": "xbrl_shareholder_filings.doc_id"})
    is_latest = Column(Boolean, nullable=False, default=False, comment="同じ code / fiscal_year_end / doc_type のうち提出が新しい 1 件")
    withdrawn = Column(Boolean, nullable=False, default=False)
    generated_at = Column(UtcDateTime, nullable=False)
    __table_args__ = (
        Index("idx_xbrl_shareholder_filings_code", "code", "as_of"),
        Index("idx_xbrl_shareholder_filings_latest", "code", "is_latest"),
        {"comment": "有報の大株主・所有者別の 1 書類。xbrl_facts から正規化"},
    )


class XbrlMajorShareholder(Base):
    __tablename__ = "xbrl_major_shareholders"
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), primary_key=True)
    rank = Column(Integer, primary_key=True, comment="NoNMajorShareholdersMember の N")
    basis = Column(_one_of("shares", "voting_rights", length=16), primary_key=True)
    name = Column(Text)
    address = Column(Text)
    shares_held = Column(Numeric(30, 4), comment="所有株式数。議決権ベースの表は NULL")
    shareholding_ratio = Column(Numeric(18, 6), comment="0.1650 = 16.50%")
    voting_rights = Column(Numeric(30, 4), comment="所有議決権数（個）")
    voting_rights_ratio = Column(Numeric(18, 6))
    __table_args__ = (
        Index("idx_xbrl_major_shareholders_filing", "filing_id"),
        {"comment": "大株主の状況 1 行（順位 × 株式ベース / 議決権ベース）"},
    )


class XbrlShareholdingByCategory(Base):
    __tablename__ = "xbrl_shareholding_by_category"
    filing_id = Column(Integer, ForeignKey("xbrl_filings.id"), primary_key=True)
    share_class = Column(String(255), primary_key=True, comment="OrdinaryShareMember 等。軸が無ければ ''")
    category = Column(String(32), primary_key=True)
    shareholder_count = Column(Integer)
    units_held = Column(BigInteger, comment="所有株式数（単元）")
    shareholding_ratio = Column(Numeric(18, 6), comment="0.2780 = 27.80%。計の行は無いので NULL")
    shares_less_than_one_unit = Column(Numeric(30, 4), comment="単元未満株式。区分の計の行に載せる")
    __table_args__ = (
        Index("idx_xbrl_shareholding_by_category_filing", "filing_id"),
        {"comment": "所有者別状況 1 行（株式の種類 × 区分）"},
    )


class XbrlJobRun(Base):
    __tablename__ = "xbrl_job_runs"
    id = Column(Integer, primary_key=True)
    job_name = Column(_one_of("parse", "normalize", "edinet_xbrl", length=32), nullable=False)
    status = Column(_one_of("running", "success", "failed", "skipped", length=16), nullable=False)
    processed_count = Column(Integer, default=0)
    error_count = Column(Integer, default=0)
    message = Column(Text)
    started_at = Column(UtcDateTime, nullable=False)
    finished_at = Column(UtcDateTime)
    __table_args__ = ({"comment": "XBRL のバッチの実行記録"},)


class FxRate(Base):
    __tablename__ = "fx_rates"
    date = Column(Date, primary_key=True)
    quote = Column(String(3), primary_key=True, comment="ISO 4217（USD / JPY 等）")
    rate = Column(Float, nullable=False, comment="1 EUR = rate 単位の quote")
    __table_args__ = (
        Index("ix_fx_rates_quote_date", "quote", "date"),
        {"comment": "ECB 対EUR 日次レート。EUR 自身は持たない"},
    )


class MaterialMonthly(Base):
    __tablename__ = "material_monthly"
    __table_args__ = ({"comment": "サプライタブの素材ごとの月次量"},)
    material_id = Column(String, primary_key=True, comment="config/supply/supply_chain.json の id（FK なし）")
    month = Column(String(7), primary_key=True, comment="YYYY-MM")
    metric = Column(
        _one_of(
            "production", "shipment", "inventory", "import", "export",
            {"processing": "原油処理", "domestic_sales": "国内向販売"},
        ),
        primary_key=True,
    )
    source = Column(
        String, primary_key=True, comment="seidou:<報告番号>（生産動態）/ sekiyu（石油統計）/ trade（貿易）",
    )
    value = Column(Float, nullable=False)
    unit = Column(String, nullable=False, comment="t / kl / 本 など。換算しない")
    revision = Column(Integer, nullable=False, comment="1 速報 / 2 確報 / 3 年報（年間補正後）")
    origin = Column(String, comment="取り込んだ e-Stat の表 ID か Excel のファイル名")
    is_estimate = Column(Boolean, nullable=False, default=False, comment="按分の推計値")
    updated_at = Column(UtcDateTime)


class MaterialTradeCountry(Base):
    __tablename__ = "material_trade_country"
    __table_args__ = ({
        "comment": (
            "素材の輸出入の相手国別の月次量（普通貿易統計の品別国別表）。material_monthly の trade の国別内訳。"
            "国合計を material_monthly に書かない素材も国別だけはここに持つ"
        ),
    },)
    material_id = Column(String, primary_key=True)
    month = Column(String(7), primary_key=True)
    flow = Column(_one_of("import", "export"), primary_key=True)
    country = Column(String(3), primary_key=True, comment="貿易統計の国コード（137 = サウジアラビア）")
    country_name = Column(String, nullable=False)
    value = Column(Float, nullable=False)
    unit = Column(String, nullable=False)
    revision = Column(Integer, nullable=False, comment="1 速報 / 2 確報")
    origin = Column(String)
    updated_at = Column(UtcDateTime)


class PtsSnapshot(Base):
    __tablename__ = "pts_snapshots"
    fetched_at = Column(JstDateTime, primary_key=True, comment="取得時刻")
    ranking = Column(_one_of("night_up", "night_down", "night_volume"), primary_key=True)
    code = Column(String(5), primary_key=True, info={"ref": "stocks.code"})
    session_date = Column(
        Date, nullable=False, comment="比べている通常取引の終値の日（株探の見出し「D日終値」から。取得日ではない）",
    )
    as_of = Column(String(5), comment="株探の「HH:MM現在」（15 分ディレイ）")
    rank = Column(Integer, nullable=False)
    name = Column(String)
    market = Column(String)
    base_close = Column(Float)
    pts_price = Column(Float)
    pts_chg_pct = Column(Float)
    volume = Column(Integer)
    __table_args__ = (
        Index("ix_pts_session_ranking", "session_date", "ranking"),
        {"comment": "株探の PTS ナイトタイムセッションのランキング。取得 1 回ごとにランキング全行を残す"},
    )


class EdinetFiler(Base):
    __tablename__ = "edinet_filers"
    __table_args__ = ({
        "comment": (
            "EDINET コードリスト（提出者）の写し。上場していない提出者（大量保有の保有者など）も持つ。"
            "リストから消えた提出者も残す"
        ),
    },)
    edinet_code = Column(String(6), primary_key=True)
    sec_code = Column(String(5), index=True, info={"ref": "stocks.code"}, comment="証券コード 5 桁")
    filer_name = Column(String)
    filer_name_en = Column(String)
    filer_type = Column(String, comment="提出者種別（内国法人・組合 / 外国法人・組合 / 個人 など）")
    listed = Column(String, comment="上場区分（上場 / 非上場）")
    is_consolidated = Column(String, comment="連結の有無（有 / 無）")
    capital = Column(String, comment="資本金（百万円）。リストの文字列のまま")
    fiscal_year_end = Column(String, comment="決算日（例 3月31日）")
    industry = Column(String, comment="提出者業種")
    jcn = Column(String(13), comment="提出者法人番号")
    list_date = Column(Date, comment="リストのダウンロード実行日")
    updated_at = Column(JstDateTime, nullable=False)


class EdinetDocument(Base):
    __tablename__ = "edinet_documents"
    file_date = Column(Date, primary_key=True, info={"ref": "edinet_list_days.file_date"})
    seq_number = Column(Integer, primary_key=True)
    doc_id = Column(String(8), nullable=False, index=True)
    code = Column(String(5), index=True, info={"ref": "stocks.code"}, comment="secCode か edinet_filers から引く")
    edinet_code = Column(String(6), index=True, info={"ref": "edinet_filers.edinet_code"})
    sec_code = Column(String(5), info={"ref": "stocks.code"})
    jcn = Column(String(13))
    filer_name = Column(String)
    fund_code = Column(String(6))
    ordinance_code = Column(String(3))
    form_code = Column(String(6))
    doc_type_code = Column(String(3), index=True)
    period_start = Column(Date)
    period_end = Column(Date)
    submit_datetime = Column(JstDateTime)
    doc_description = Column(Text)
    issuer_edinet_code = Column(String(6), index=True, info={"ref": "edinet_filers.edinet_code"})
    subject_edinet_code = Column(String(6), index=True, info={"ref": "edinet_filers.edinet_code"})
    subsidiary_edinet_codes = Column(Text, comment="カンマ区切り")
    current_report_reasons = Column(Text, comment="カンマ区切り")
    parent_doc_id = Column(String(8), info={"ref": "edinet_documents.doc_id"})
    ope_datetime = Column(JstDateTime)
    withdrawal_status = Column(String(1), nullable=False)
    doc_info_edit_status = Column(String(1), nullable=False)
    disclosure_status = Column(String(1), nullable=False)
    xbrl_flag = Column(Boolean, nullable=False)
    pdf_flag = Column(Boolean, nullable=False)
    attach_doc_flag = Column(Boolean, nullable=False)
    english_doc_flag = Column(Boolean, nullable=False)
    csv_flag = Column(Boolean, nullable=False)
    legal_status = Column(String(1), nullable=False)
    fetched_at = Column(JstDateTime, nullable=False)
    __table_args__ = (
        Index("ix_edinet_documents_code_type", "code", "doc_type_code", "submit_datetime"),
        {
            "comment": (
                "EDINET 書類一覧 API（type=2）の 1 行。docID は一意でない（修正・不開示・取下げが別レコードで出る）"
            ),
        },
    )


class EdinetArchive(Base):
    __tablename__ = "edinet_archives"
    __table_args__ = ({"comment": "EDINET の書類取得 API の ZIP を残した記録。中身は data/edinet → OneDrive"},)
    doc_id = Column(String(8), primary_key=True, info={"ref": "edinet_documents.doc_id"})
    file_type = Column(Integer, primary_key=True, comment="書類取得 API の type（1 / 5）")
    doc_type_code = Column(String(3))
    rel_path = Column(Text, nullable=False, comment="data/edinet からの相対パス")
    status = Column(
        _one_of({
            "saved": "data/edinet に書いた。翌朝 backup_data が OneDrive へ move する",
            "gone": "取り直しのとき EDINET が 404。残っていない",
        }, length=8),
        nullable=False,
    )
    bytes = Column(Integer)
    sha256 = Column(String(64))
    saved_at = Column(JstDateTime, nullable=False)


class EdinetListDay(Base):
    __tablename__ = "edinet_list_days"
    __table_args__ = ({"comment": "書類一覧を取った日ごとの記録"},)
    file_date = Column(Date, primary_key=True)
    count = Column(Integer, nullable=False, comment="metadata.resultset.count")
    max_seq = Column(Integer)
    process_datetime = Column(JstDateTime, comment="EDINET 側の一覧作成時刻")
    finalized = Column(Boolean, nullable=False, comment="file_date より後に取った = その日の一覧は確定")
    fetched_at = Column(JstDateTime, nullable=False)


class LargeHoldingReport(Base):
    __tablename__ = "large_holding_reports"
    doc_id = Column(String(8), primary_key=True, info={"ref": "edinet_documents.doc_id"})
    file_date = Column(Date, nullable=False)
    submit_datetime = Column(JstDateTime)
    doc_type_code = Column(String(3), nullable=False, comment="350 大量保有・変更報告書 / 360 訂正")
    form_code = Column(String(6), comment="010000 大量保有 / 010002 変更 / 020002 短期大量譲渡 / 03xxxx 特例 など")
    title = Column(Text, comment="表紙の書類名（変更報告書ＮＯ．3 など）")
    filer_edinet_code = Column(String(6), index=True, info={"ref": "edinet_filers.edinet_code"})
    filer_name = Column(String)
    issuer_edinet_code = Column(String(6), info={"ref": "edinet_filers.edinet_code"})
    issuer_sec_code = Column(
        String(5), info={"ref": "stocks.code"}, comment="発行者の証券コード（CSV の 4 桁に 0 を付けた 5 桁）",
    )
    issuer_name = Column(String)
    code = Column(String(5), info={"ref": "stocks.code"}, comment="上場していなければ NULL")
    obligation_date = Column(Date, comment="報告義務発生日")
    filing_date = Column(Date, comment="表紙の提出日")
    reason = Column(Text, comment="提出事由（変更報告書だけ）")
    arrangement = Column(String, comment="提出形態（連名 / その他）")
    holder_count = Column(Integer, comment="提出者及び共同保有者の総数")
    shares_held = Column(BigInteger, comment="保有株券等の数（合計）")
    outstanding_shares = Column(BigInteger, comment="発行済株式等総数")
    holding_ratio = Column(Float, comment="株券等保有割合（0.0507 = 5.07%）")
    holding_ratio_prev = Column(Float, comment="直前の報告書の保有割合。大量保有報告書は NULL")
    joint_holders_text = Column(Text, comment="第3 共同保有者に関する事項（該当なしは NULL）")
    is_amendment = Column(Boolean, nullable=False, default=False)
    amends_doc_id = Column(
        String(8), info={"ref": "large_holding_reports.doc_id"},
        comment="訂正の対象（DEI の IdentificationOfDocumentSubjectToAmendment）",
    )
    original_doc_id = Column(
        String(8), nullable=False, index=True, info={"ref": "large_holding_reports.doc_id"},
        comment="訂正をたどった大元。訂正でなければ自分",
    )
    is_latest = Column(Boolean, nullable=False, default=False, comment="大元ごとに取下げでない最新の 1 件")
    withdrawn = Column(Boolean, nullable=False, default=False, comment="取下げ・不開示（edinet_documents から）")
    parse_status = Column(_one_of("parsed", "failed"), nullable=False)
    parse_error = Column(Text)
    fetched_at = Column(JstDateTime, nullable=False)
    __table_args__ = (
        Index("ix_large_holding_reports_code", "code", "obligation_date"),
        Index("ix_large_holding_reports_file_date", "file_date"),
        Index("ix_large_holding_reports_latest_file", "is_latest", "file_date"),
        {
            "comment": (
                "大量保有報告書・変更報告書・訂正（EDINET 書類種別 350 / 360）の 1 書類。"
                "中身は書類取得 API type=5（XBRL→CSV）"
            ),
        },
    )


class LargeHolding(Base):
    __tablename__ = "large_holdings"
    __table_args__ = ({
        "comment": "大量保有の 1 書類 × 提出者・共同保有者 1 人（CSV の FilerLargeVolumeHolder<N>Member）",
    },)
    doc_id = Column(String(8), primary_key=True, info={"ref": "large_holding_reports.doc_id"})
    holder_no = Column(Integer, primary_key=True, comment="書類の中の番号（1 始まり）")
    edinet_code = Column(String(6), index=True, info={"ref": "edinet_filers.edinet_code"})
    name = Column(String)
    holder_type = Column(String, comment="個人・法人の別（法人（株式会社）など）")
    shares_held = Column(BigInteger)
    outstanding_shares = Column(BigInteger)
    holding_ratio = Column(Float)
    holding_ratio_prev = Column(Float)
    base_date = Column(Date, comment="保有の基準日")
    purpose = Column(Text, comment="保有目的")
    important_proposal = Column(Text, comment="重要提案行為等（該当なしは NULL）")
    own_funds = Column(BigInteger, comment="取得資金: 自己資金額（円）")
    borrowings = Column(BigInteger, comment="借入金額計")
    other_funds = Column(BigInteger, comment="その他金額計")
    total_funds = Column(BigInteger, comment="取得資金合計")


class EdinetBuyback(Base):
    __tablename__ = "edinet_buybacks"
    doc_id = Column(String(8), primary_key=True, info={"ref": "edinet_documents.doc_id"})
    file_date = Column(Date, nullable=False)
    submit_datetime = Column(JstDateTime)
    doc_type_code = Column(String(3), nullable=False, comment="220 報告書 / 230 訂正")
    form_code = Column(String(6), comment="170000 株式会社 / 253000 投資法人")
    code = Column(String(5), info={"ref": "stocks.code"}, comment="上場していなければ NULL")
    edinet_code = Column(String(6), index=True, info={"ref": "edinet_filers.edinet_code"})
    filer_name = Column(String)
    report_month = Column(String(7), comment="報告月 YYYY-MM（報告期間の末日の月）")
    period_start = Column(Date, comment="報告期間")
    period_end = Column(Date)
    issued_shares = Column(BigInteger, comment="報告月末の発行済株式総数（投資法人は投資口）")
    treasury_shares = Column(BigInteger, comment="報告月末の保有自己株式数")
    resolution_count = Column(Integer, comment="決議の数（edinet_buyback_resolutions の行数）")
    month_shares = Column(BigInteger, comment="報告月に取得した株数（決議の合計）")
    month_amount = Column(BigInteger, comment="報告月に取得した価額の総額（円）")
    is_amendment = Column(Boolean, nullable=False, default=False)
    amends_doc_id = Column(String(8), info={"ref": "edinet_buybacks.doc_id"})
    original_doc_id = Column(String(8), nullable=False, index=True, info={"ref": "edinet_buybacks.doc_id"})
    is_latest = Column(Boolean, nullable=False, default=False, comment="大元ごとの最新の 1 件")
    withdrawn = Column(Boolean, nullable=False, default=False)
    parse_status = Column(_one_of("parsed", {"meta": "取得状況が対象外の訂正"}, "failed"), nullable=False)
    parse_error = Column(Text)
    fetched_at = Column(JstDateTime, nullable=False)
    __table_args__ = (
        Index("ix_edinet_buybacks_code", "code", "report_month"),
        {
            "comment": (
                "自己株券買付状況報告書・訂正（EDINET 書類種別 220 / 230）の 1 書類。"
                "中身は書類取得 API type=1（XBRL）のテキストブロックの表"
            ),
        },
    )


class EdinetBuybackResolution(Base):
    __tablename__ = "edinet_buyback_resolutions"
    __table_args__ = ({
        "comment": "自己株券買付状況報告書の 1 書類 × 決議 1 件（取締役会・株主総会・投資法人の役員会）",
    },)
    doc_id = Column(String(8), primary_key=True, info={"ref": "edinet_buybacks.doc_id"})
    resolution_no = Column(Integer, primary_key=True, comment="書類の中の順番（1 始まり）")
    kind = Column(_one_of("board", "shareholders"), nullable=False)
    body = Column(String, comment="決議した機関（取締役会・定時株主総会・役員会）")
    resolution_date = Column(Date, comment="決議日")
    acquire_from = Column(Date, comment="取得期間")
    acquire_to = Column(Date)
    limit_shares = Column(BigInteger, comment="決議の枠（株数）。無ければ NULL")
    limit_amount = Column(BigInteger, comment="決議の枠（価額の総額・円）")
    month_shares = Column(BigInteger, comment="報告月の取得（「－」は 0）")
    month_amount = Column(BigInteger)
    cumulative_shares = Column(BigInteger, comment="報告月末までの累計")
    cumulative_amount = Column(BigInteger)
    progress_shares = Column(Float, comment="進捗（書類の値。0.5297 = 52.97%）")
    progress_amount = Column(Float)
    label = Column(Text, comment="決議状況の行の見出し（そのまま）")


class EdinetTenderOffer(Base):
    __tablename__ = "edinet_tender_offers"
    __table_args__ = ({
        "comment": "公開買付け 1 件。届出書（240）を大元に、訂正・撤回・意見表明・報告書から決め直す",
    },)
    tender_offer_id = Column(
        String(8), primary_key=True, info={"ref": "edinet_tender_offer_docs.doc_id"},
        comment="大元の docID（ふつうは公開買付届出書）",
    )
    is_issuer = Column(Boolean, nullable=False, default=False, comment="発行者による公開買付け（自社株 TOB）")
    offeror_edinet_code = Column(String(6), index=True, info={"ref": "edinet_filers.edinet_code"})
    offeror_name = Column(String)
    offeror_code = Column(String(5), info={"ref": "stocks.code"}, comment="買付者が上場していれば")
    target_edinet_code = Column(String(6), index=True, info={"ref": "edinet_filers.edinet_code"})
    target_name = Column(String)
    target_code = Column(String(5), index=True, info={"ref": "stocks.code"}, comment="対象者")
    announced_date = Column(Date, comment="届出書の提出日")
    period_start = Column(Date, comment="買付け等の期間")
    period_end = Column(Date)
    price = Column(Float, comment="普通株式 1 株の買付価格（円）")
    price_initial = Column(Float, comment="届出書の当初の価格")
    planned_shares = Column(BigInteger, comment="買付予定数")
    min_shares = Column(BigInteger, comment="買付予定数の下限（無ければ NULL）")
    max_shares = Column(BigInteger, comment="買付予定数の上限（無ければ NULL）")
    holding_ratio_after = Column(Float, comment="買付け後の株券等所有割合（届出書は予定、報告書は結果）")
    opinion = Column(_one_of("support", "oppose", "neutral", "reserved"), comment="対象者の意見")
    recommendation = Column(_one_of("recommend", "leave", "not_recommend"), comment="応募")
    status = Column(_one_of("open", "succeeded", "failed", "withdrawn"), nullable=False)
    tendered_shares = Column(BigInteger, comment="応募数（株式換算）")
    purchased_shares = Column(BigInteger, comment="買付数")
    result_date = Column(Date, comment="公開買付報告書の提出日")
    latest_doc_id = Column(
        String(8), info={"ref": "edinet_tender_offer_docs.doc_id"}, comment="いちばん新しい書類",
    )
    doc_count = Column(Integer)
    updated_at = Column(JstDateTime, nullable=False)


class EdinetTenderOfferDoc(Base):
    __tablename__ = "edinet_tender_offer_docs"
    __table_args__ = ({
        "comment": "公開買付けの書類 1 件（240〜340）。届出・訂正・撤回・報告書・意見表明・対質問回答など",
    },)
    doc_id = Column(String(8), primary_key=True, info={"ref": "edinet_documents.doc_id"})
    tender_offer_id = Column(
        String(8), nullable=False, index=True, info={"ref": "edinet_tender_offers.tender_offer_id"},
        comment="大元（parent_doc_id をたどる）",
    )
    file_date = Column(Date, nullable=False)
    submit_datetime = Column(JstDateTime)
    doc_type_code = Column(String(3), nullable=False)
    ordinance_code = Column(String(3), comment="040 他社株 / 050 発行者（自社株）")
    form_code = Column(String(6))
    title = Column(Text)
    filer_edinet_code = Column(String(6), info={"ref": "edinet_filers.edinet_code"})
    filer_name = Column(String)
    subject_edinet_code = Column(
        String(6), info={"ref": "edinet_filers.edinet_code"}, comment="届出書・報告書は対象者、意見表明は買付者",
    )
    parent_doc_id = Column(String(8), info={"ref": "edinet_tender_offer_docs.doc_id"})
    withdrawn = Column(Boolean, nullable=False, default=False, comment="取下げ・不開示")
    period_start = Column(Date)
    period_end = Column(Date)
    price = Column(Float)
    planned_shares = Column(BigInteger)
    min_shares = Column(BigInteger)
    max_shares = Column(BigInteger)
    holding_ratio_after = Column(Float)
    target_name = Column(String)
    opinion = Column(_one_of("support", "oppose", "neutral", "reserved"))
    recommendation = Column(_one_of("recommend", "leave", "not_recommend"))
    opinion_text = Column(Text, comment="意見の内容（最初の段落）")
    success = Column(Boolean, comment="公開買付けの成否（報告書）")
    tendered_shares = Column(BigInteger)
    purchased_shares = Column(BigInteger)
    parse_status = Column(_one_of("parsed", {"meta": "中身を読まない書類"}, "failed"), nullable=False)
    parse_error = Column(Text)
    fetched_at = Column(JstDateTime, nullable=False)
