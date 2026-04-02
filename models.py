"""
SQLAlchemy ORM 数据模型
═══════════════════════════════════════════════════════════════════════════
DragonFlow 全部持久化表：
  - DailyReport       每日报告存档（早盘/午盘/收盘）
  - TradeRecord       交易记录（含B/S点精确时间戳）
  - Position          当前持仓
  - StrategyParam     策略参数（支持在线修改+进化调整）
  - StrategyStats     统计（日/周/月）
  - MarketSnapshot    市场快照缓存
  - EvolutionLog      策略进化迭代记录
  - WatchList         候选股票池
  - RiskEvent         风控事件日志
"""
from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import (
    Column, Integer, Float, String, Text, Boolean,
    DateTime, Date, JSON, Index, create_engine, event
)
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.pool import StaticPool

from openclaw_adapter import get_data_dir

# ─── 数据库连接 ──────────────────────────────────────────────────────────────
_DB_PATH = get_data_dir() / "dragonflow.db"
_ENGINE = None


def get_engine():
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(
            f"sqlite:///{_DB_PATH}",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )

        @event.listens_for(_ENGINE, "connect")
        def set_wal(dbapi_conn, _):
            dbapi_conn.execute("PRAGMA journal_mode=WAL")
            dbapi_conn.execute("PRAGMA synchronous=NORMAL")
            dbapi_conn.execute("PRAGMA cache_size=10000")
    return _ENGINE


def get_session() -> Session:
    return Session(get_engine())


class Base(DeclarativeBase):
    pass


# ════════════════════════════════════════════════════════════════════════════
# 每日报告
# ════════════════════════════════════════════════════════════════════════════

class DailyReport(Base):
    __tablename__ = "daily_reports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    report_type = Column(String(20), nullable=False)   # morning/midday/closing
    created_at = Column(DateTime, default=datetime.datetime.now)
    summary = Column(JSON)
    data_snapshot = Column(JSON)
    zone = Column(String(20))              # 市场区间
    emotion = Column(String(10))           # 情绪周期
    emotion_score = Column(Float)
    zt_count = Column(Integer)
    highest_board = Column(Integer)
    recommended_stocks = Column(JSON)
    html_path = Column(String(256))
    full_text = Column(Text)

    __table_args__ = (
        Index("idx_report_date_type", "date", "report_type"),
    )


# ════════════════════════════════════════════════════════════════════════════
# 交易记录
# ════════════════════════════════════════════════════════════════════════════

class TradeRecord(Base):
    __tablename__ = "trade_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    code = Column(String(10), nullable=False, index=True)
    name = Column(String(20))
    direction = Column(String(4))          # BUY / SELL
    price = Column(Float)
    shares = Column(Integer)
    amount = Column(Float)
    timestamp = Column(DateTime)
    signal_type = Column(String(30))       # 双龙破晓/换手龙/止损/止盈1/止盈2/T+3清仓
    signal_reason = Column(Text)
    minute_price = Column(Float)
    volume_ratio = Column(Float)
    # SELL 时填写
    buy_price = Column(Float)
    pnl = Column(Float)
    pnl_pct = Column(Float)
    hold_minutes = Column(Integer)
    # 市场环境快照
    zone = Column(String(20))
    emotion = Column(String(10))
    zt_count_at = Column(Integer)
    strategy_ver = Column(String(20))

    __table_args__ = (
        Index("idx_trade_code_date", "code", "date"),
    )


# ════════════════════════════════════════════════════════════════════════════
# 当前持仓
# ════════════════════════════════════════════════════════════════════════════

class Position(Base):
    __tablename__ = "positions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(10), unique=True, nullable=False)
    name = Column(String(20))
    buy_date = Column(Date)
    buy_time = Column(DateTime)
    buy_price = Column(Float)
    shares = Column(Integer)
    cost = Column(Float)
    current_price = Column(Float)
    current_pnl = Column(Float)
    current_pnl_pct = Column(Float)
    stop_loss = Column(Float)
    take_profit_1 = Column(Float)
    take_profit_2 = Column(Float)
    add_price = Column(Float)              # 加仓触发价
    strategy = Column(String(30))
    pick_type = Column(String(20))         # 双龙破晓/换手龙/分歧买点
    reason = Column(Text)
    sector = Column(String(30))
    hold_days = Column(Integer, default=0)
    updated_at = Column(DateTime, default=datetime.datetime.now,
                        onupdate=datetime.datetime.now)


# ════════════════════════════════════════════════════════════════════════════
# 策略参数
# ════════════════════════════════════════════════════════════════════════════

class StrategyParam(Base):
    __tablename__ = "strategy_params"

    id = Column(Integer, primary_key=True, autoincrement=True)
    key = Column(String(60), unique=True, nullable=False)
    value = Column(String(256))
    value_type = Column(String(10))        # int/float/str/bool
    description = Column(String(256))
    category = Column(String(30))          # zone/emotion/trade/risk/filter
    updated_at = Column(DateTime, default=datetime.datetime.now,
                        onupdate=datetime.datetime.now)
    updated_by = Column(String(30), default="system")


# ════════════════════════════════════════════════════════════════════════════
# 策略统计
# ════════════════════════════════════════════════════════════════════════════

class StrategyStats(Base):
    __tablename__ = "strategy_stats"

    id = Column(Integer, primary_key=True, autoincrement=True)
    period_type = Column(String(10))       # day/week/month
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    total_trades = Column(Integer, default=0)
    win_trades = Column(Integer, default=0)
    lose_trades = Column(Integer, default=0)
    win_rate = Column(Float, default=0.0)
    avg_win_pct = Column(Float, default=0.0)
    avg_lose_pct = Column(Float, default=0.0)
    profit_ratio = Column(Float, default=0.0)
    total_pnl = Column(Float, default=0.0)
    max_drawdown = Column(Float, default=0.0)
    capital_end = Column(Float)            # 期末资金
    zone_distribution = Column(JSON)       # {zone: trade_count}
    loss_reasons = Column(JSON)
    win_patterns = Column(JSON)
    evolution_advice = Column(Text)
    created_at = Column(DateTime, default=datetime.datetime.now)

    __table_args__ = (
        Index("idx_stats_period", "period_type", "period_start"),
    )


# ════════════════════════════════════════════════════════════════════════════
# 市场快照缓存
# ════════════════════════════════════════════════════════════════════════════

class MarketSnapshot(Base):
    __tablename__ = "market_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    snapshot_type = Column(String(20))     # realtime/morning/midday/closing
    timestamp = Column(DateTime, nullable=False, index=True)
    date = Column(Date, index=True)
    zone = Column(String(20))
    emotion = Column(String(10))
    data = Column(JSON)

    __table_args__ = (
        Index("idx_snap_type_ts", "snapshot_type", "timestamp"),
    )


# ════════════════════════════════════════════════════════════════════════════
# 策略进化日志
# ════════════════════════════════════════════════════════════════════════════

class EvolutionLog(Base):
    __tablename__ = "evolution_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    version = Column(String(20))
    created_at = Column(DateTime, default=datetime.datetime.now)
    trigger_reason = Column(String(100))
    old_params = Column(JSON)
    new_params = Column(JSON)
    analysis = Column(Text)
    performance_before = Column(JSON)
    performance_after = Column(JSON)
    approved = Column(Boolean, default=False)


# ════════════════════════════════════════════════════════════════════════════
# 候选股票池
# ════════════════════════════════════════════════════════════════════════════

class WatchList(Base):
    __tablename__ = "watchlist"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, index=True)
    code = Column(String(10), nullable=False)
    name = Column(String(20))
    source = Column(String(30))            # closing_report/manual/auto_pick
    priority = Column(Integer, default=0)
    sector = Column(String(30))
    pick_type = Column(String(20))         # 双龙破晓/换手龙/分歧买点
    score = Column(Float)
    reason = Column(Text)
    entry_price = Column(Float)
    stop_price = Column(Float)
    tp1_price = Column(Float)
    tp2_price = Column(Float)
    volume_ratio = Column(Float)
    turnover_pct = Column(Float)
    from_60d_low_pct = Column(Float)
    status = Column(String(15), default="pending")  # pending/watching/traded/expired
    created_at = Column(DateTime, default=datetime.datetime.now)

    __table_args__ = (
        Index("idx_watchlist_date_code", "date", "code"),
    )


# ════════════════════════════════════════════════════════════════════════════
# 风控事件日志
# ════════════════════════════════════════════════════════════════════════════

class RiskEvent(Base):
    __tablename__ = "risk_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    date = Column(Date, nullable=False, index=True)
    event_type = Column(String(30))        # stop_loss_miss/wrong_add/drawdown_warn/force_stop
    description = Column(Text)
    penalty = Column(String(100))          # 惩罚措施
    resolved = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.datetime.now)


# ════════════════════════════════════════════════════════════════════════════
# 舆情快照
# ════════════════════════════════════════════════════════════════════════════

class SentimentSnapshot(Base):
    __tablename__ = "sentiment_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, nullable=False, index=True)
    date = Column(Date, index=True)
    source = Column(String(20))            # guba/xueqiu/ths/aggregate
    # 市场整体舆情
    rsi = Column(Float)                    # 散户情绪指数 0-1
    bullish_score = Column(Float)
    bearish_score = Column(Float)
    sentiment_label = Column(String(20))   # 极度乐观/偏乐观/中性/偏悲观/极度恐慌
    position_modifier = Column(Float)      # 仓位修正系数
    sample_count = Column(Integer)         # 分析样本数
    hot_topics = Column(JSON)              # 热门讨论话题
    # 个股舆情
    stock_sentiments = Column(JSON)        # {code: {rsi, label, sample}}
    raw_data = Column(JSON)                # 原始采集数据

    __table_args__ = (
        Index("idx_sentiment_ts", "timestamp"),
    )


# ════════════════════════════════════════════════════════════════════════════
# 知识库
# ════════════════════════════════════════════════════════════════════════════

class KnowledgeEntry(Base):
    __tablename__ = "knowledge_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    created_at = Column(DateTime, default=datetime.datetime.now, index=True)
    source = Column(String(50))            # eastmoney_research/xueqiu/zhihu/joinquant
    source_url = Column(String(500))
    entry_type = Column(String(30))        # strategy/research/opinion/param_ref
    title = Column(String(256))
    content_hash = Column(String(64), unique=True)  # 去重
    summary = Column(Text)                 # 摘要
    tags = Column(JSON)                    # ["龙空龙","短线","止损"]
    # 提取的量化参数
    extracted_params = Column(JSON)        # {stop_loss: 2%, win_rate: 60%, ...}
    # 市场观点
    market_view = Column(String(20))       # bullish/bearish/neutral
    sector_focus = Column(JSON)            # ["AI","机器人"]
    # 质量评估
    quality_score = Column(Float)          # 0-100
    relevance_score = Column(Float)        # 与自身策略相关度
    applied = Column(Boolean, default=False)  # 是否已应用于进化

    __table_args__ = (
        Index("idx_knowledge_type", "entry_type"),
    )


# ════════════════════════════════════════════════════════════════════════════
# 系统健康日志
# ════════════════════════════════════════════════════════════════════════════

class SystemHealthLog(Base):
    __tablename__ = "system_health_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime, default=datetime.datetime.now, index=True)
    check_type = Column(String(30))        # data_freshness/api_error/tick_latency/db/memory
    status = Column(String(10))            # ok/warn/critical
    detail = Column(Text)
    # 修复动作
    repair_action = Column(String(50))     # data_source_switch/cache_clear/rate_limit_backoff/...
    repair_result = Column(String(20))     # success/failed/skipped
    data_source_status = Column(JSON)      # 各数据源健康状态快照


# ════════════════════════════════════════════════════════════════════════════
# 初始化
# ════════════════════════════════════════════════════════════════════════════

def init_db():
    Base.metadata.create_all(get_engine())
    _seed_default_params()


def _seed_default_params():
    from config import TRADE_RULES
    session = get_session()
    try:
        from sqlalchemy import text
        count = session.execute(text("SELECT COUNT(*) FROM strategy_params")).scalar()
        if count and count > 0:
            return

        defaults = [
            # 交易规则参数
            ("stop_loss_pct", str(TRADE_RULES["stop_loss_pct"]), "float", "止损比例(-1.8%)", "trade"),
            ("take_profit_1_pct", str(TRADE_RULES["take_profit_1_pct"]), "float", "止盈1(+5%)", "trade"),
            ("take_profit_2_pct", str(TRADE_RULES["take_profit_2_pct"]), "float", "止盈2(+8%)", "trade"),
            ("max_hold_days", str(TRADE_RULES["max_hold_days"]), "int", "最长持有天数", "trade"),
            ("add_trigger_pct", str(TRADE_RULES["add_trigger_pct"]), "float", "加仓触发涨幅", "trade"),
            ("max_chase_pct", str(TRADE_RULES["max_chase_pct"]), "float", "追高上限%", "filter"),
            ("min_buy_volume_ratio", str(TRADE_RULES["min_buy_volume_ratio"]), "float", "买入量比门槛", "filter"),
            # 资金参数
            ("initial_capital", str(TRADE_RULES["initial_capital"]), "float", "初始资金", "risk"),
            # 选股参数
            ("theme_rank_max", "3", "int", "题材排名上限", "filter"),
            ("volume_ratio_min", "1.8", "float", "选股量比下限", "filter"),
            ("from_60d_low_pct_min", "5.0", "float", "距60日低点下限%", "filter"),
            ("from_60d_low_pct_max", "40.0", "float", "距60日低点上限%", "filter"),
        ]
        for key, val, vtype, desc, cat in defaults:
            session.add(StrategyParam(
                key=key, value=val, value_type=vtype,
                description=desc, category=cat))
        session.commit()
    except Exception:
        session.rollback()
    finally:
        session.close()


def get_param(key: str, default=None):
    session = get_session()
    try:
        row = session.query(StrategyParam).filter_by(key=key).first()
        if row is None:
            return default
        val = row.value
        if row.value_type == "int":
            return int(val)
        elif row.value_type == "float":
            return float(val)
        elif row.value_type == "bool":
            return val.lower() in ("true", "1", "yes")
        return val
    finally:
        session.close()


def set_param(key: str, value, updated_by: str = "system"):
    session = get_session()
    try:
        row = session.query(StrategyParam).filter_by(key=key).first()
        if row:
            row.value = str(value)
            row.updated_by = updated_by
            row.updated_at = datetime.datetime.now()
            session.commit()
            return True
        return False
    except Exception:
        session.rollback()
        return False
    finally:
        session.close()
