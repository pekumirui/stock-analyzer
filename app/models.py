from sqlalchemy import Column, String, Float, Date, Integer, Boolean, Text, Index
from sqlalchemy.orm import declarative_base

Base = declarative_base()


class Stock(Base):
    __tablename__ = "stocks"
    code = Column(String(5), primary_key=True)
    name = Column(String)
    sector17 = Column(String)
    sector33 = Column(String)
    market = Column(String)
    scale_category = Column(String)   # TOPIX Large70 等
    margin_type = Column(String)      # 貸借信用区分名
    product_category = Column(String)  # ProdCat: 011=内国株券, 013=REIT, 014=ETF 等


class DailyPrice(Base):
    __tablename__ = "daily_prices"
    code = Column(String(5), primary_key=True)
    date = Column(Date, primary_key=True)
    # 調整前 (報告時生値)
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float)
    volume = Column(Float)
    turnover = Column(Float)          # 売買代金
    adj_factor = Column(Float, default=1.0)
    # 調整済 (分割・併合・ライツイシュー遡及調整済。分析は必ずこちらを使う)
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
    period_start = Column(Date)          # 当会計期間開始日
    period_end = Column(Date)            # 当会計期間終了日
    fiscal_year_start = Column(Date)     # 当事業年度開始日
    fiscal_year_end = Column(Date)       # 当事業年度終了日
    # 累計実績
    net_sales = Column(Float)
    operating_profit = Column(Float)
    ordinary_profit = Column(Float)
    profit = Column(Float)
    eps = Column(Float)
    # 半期(2Q)会社予想
    f2_net_sales = Column(Float)
    f2_operating_profit = Column(Float)
    f2_ordinary_profit = Column(Float)
    f2_profit = Column(Float)
    f2_eps = Column(Float)
    # 通期会社予想
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


class MonthlyTable(Base):
    """銘柄×年度の月次表。セルは POST elements で表を作ってから入れる。"""
    __tablename__ = "monthly_tables"
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)


class MonthlyElement(Base):
    """月次表の行。element はトリムした原文を保存し、sort_order が登録順。"""
    __tablename__ = "monthly_elements"
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)
    element = Column(String(50), primary_key=True)
    sort_order = Column(Integer, nullable=False)


class MonthlyValue(Base):
    """月次セル。(code, year, element, month) 一意。value は数値のみ。"""
    __tablename__ = "monthly_values"
    code = Column(String(5), primary_key=True)
    year = Column(Integer, primary_key=True)
    element = Column(String(50), primary_key=True)
    month = Column(Integer, primary_key=True)
    value = Column(Float, nullable=False)

