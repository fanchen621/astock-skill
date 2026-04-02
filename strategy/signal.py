"""
信号数据类
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class StockCandidate:
    """选股候选"""
    code: str
    name: str
    sector: str
    close: float
    change_pct: float          # 当日涨跌幅%
    volume_ratio: float        # 量比
    turnover_pct: float        # 换手率%
    breakout_20d: int          # 突破20日新高 0/1
    from_60d_low_pct: float    # 距60日低点涨幅%
    board_height: int          # 连板高度（0=非涨停）
    is_limit_up: int           # 当日是否涨停 0/1
    is_one_wall: int           # 是否一字板 0/1
    theme_rank: int            # 题材排名（1=最强）
    amount_100m: float         # 成交额（亿）
    score: float = 0.0


@dataclass
class PickResult:
    """选股结果"""
    stock: StockCandidate
    score: float
    reasons: List[str]
    pick_type: str             # 双龙破晓 / 换手龙 / 分歧买点


@dataclass
class TradeSignal:
    """交易信号"""
    code: str
    name: str
    direction: str             # BUY / SELL
    price: float
    shares: int
    signal_type: str           # 双龙破晓/换手龙/止损/止盈1/止盈2/T+3清仓/次日不及预期
    signal_reason: str
    timestamp: datetime.datetime
    minute_price: float = 0.0
    volume_ratio: float = 0.0
    zone: str = ""
    emotion: str = ""
    zt_count: int = 0
    pick_type: str = ""
    strategy_ver: str = "v1.0"


@dataclass
class TradePlan:
    """交易计划"""
    code: str
    name: str
    sector: str
    zone: str
    emotion: str
    target_position_pct: float
    entry_price: float
    add_price: float           # 加仓触发价（成本+2%）
    stop_price: float          # 止损价（成本-1.8%）
    tp1_price: float           # 止盈1（+5%）
    tp2_price: float           # 止盈2（+8%）
    hold_days_max: int = 3
    pick_type: str = ""
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
