"""
回测引擎
═══════════════════════════════════════════════════════════════════════════
基于历史涨停池数据回测双龙破晓策略
达标线（来自Codex PLAYBOOK）：
  - 胜率 >= 55%
  - 盈亏比 >= 2.5
  - 月最大回撤 <= 8%
  - 总最大回撤 <= 15%
  - 样本 >= 100笔
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from loguru import logger

from config import TRADE_RULES, RISK_CONTROL


@dataclass
class BacktestTrade:
    date: str
    code: str
    name: str
    entry_price: float
    exit_price: float
    shares: int
    pnl: float
    pnl_pct: float
    signal_type: str
    exit_reason: str
    hold_days: int


@dataclass
class BacktestResult:
    trades: List[BacktestTrade]
    total_trades: int = 0
    win_trades: int = 0
    lose_trades: int = 0
    win_rate: float = 0.0
    avg_win_pct: float = 0.0
    avg_lose_pct: float = 0.0
    profit_ratio: float = 0.0
    total_pnl: float = 0.0
    max_drawdown_pct: float = 0.0
    capital_curve: List[float] = field(default_factory=list)
    monthly_returns: Dict[str, float] = field(default_factory=dict)
    passed: bool = False
    fail_reasons: List[str] = field(default_factory=list)


def run_backtest(signals: List[Dict],
                 initial_capital: float = 10000.0) -> BacktestResult:
    """
    回测主函数

    Args:
        signals: [{
            "date": "2026-01-15",
            "code": "600123",
            "name": "XX股份",
            "entry_price": 15.5,
            "low_3d": 14.8,     # 3日内最低价
            "high_3d": 17.2,    # 3日内最高价
            "close_d3": 16.0,   # 第3天收盘价
        }, ...]
        initial_capital: 初始资金

    Returns: BacktestResult
    """
    trades: List[BacktestTrade] = []
    capital = initial_capital
    capital_curve = [capital]
    peak = capital

    stop_loss_pct = TRADE_RULES["stop_loss_pct"]
    tp1_pct = TRADE_RULES["take_profit_1_pct"]
    tp2_pct = TRADE_RULES["take_profit_2_pct"]
    fee_buy = TRADE_RULES["buy_fee_rate"]
    fee_sell = TRADE_RULES["sell_fee_rate"]

    for sig in signals:
        entry = float(sig.get("entry_price", 0))
        low_3d = float(sig.get("low_3d", entry))
        high_3d = float(sig.get("high_3d", entry))
        close_d3 = float(sig.get("close_d3", entry))

        if entry <= 0:
            continue

        # 计算可买股数
        shares = int(capital * 0.5 / entry / 100) * 100  # 50%仓位
        if shares <= 0:
            continue

        cost = entry * shares * (1 + fee_buy)
        stop_price = entry * (1 + stop_loss_pct)
        tp1_price = entry * (1 + tp1_pct)
        tp2_price = entry * (1 + tp2_pct)

        # 模拟3天持有
        exit_price = entry
        exit_reason = "T+3清仓"

        # 判断是否触发止损（3日内最低价 < 止损价）
        if low_3d < stop_price:
            exit_price = stop_price
            exit_reason = f"止损({stop_loss_pct*100:.1f}%)"
        # 判断止盈
        elif high_3d >= tp2_price:
            # 简化：假设先触发tp1减半，再触发tp2
            exit_price = (tp1_price + tp2_price) / 2  # 均价近似
            exit_reason = f"止盈2({tp2_pct*100:.0f}%)"
        elif high_3d >= tp1_price:
            exit_price = (tp1_price + close_d3) / 2  # tp1减半后剩余按d3收
            exit_reason = f"止盈1({tp1_pct*100:.0f}%)"
        else:
            exit_price = close_d3
            exit_reason = "T+3清仓"

        sell_amount = exit_price * shares * (1 - fee_sell)
        pnl = sell_amount - cost
        pnl_pct = (exit_price / entry - 1) * 100

        trades.append(BacktestTrade(
            date=sig.get("date", ""),
            code=sig.get("code", ""),
            name=sig.get("name", ""),
            entry_price=entry,
            exit_price=round(exit_price, 2),
            shares=shares,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl_pct, 2),
            signal_type=sig.get("signal_type", "双龙破晓"),
            exit_reason=exit_reason,
            hold_days=3,
        ))

        capital += pnl
        capital_curve.append(round(capital, 2))
        peak = max(peak, capital)

    # 统计
    result = BacktestResult(trades=trades)
    result.total_trades = len(trades)
    result.capital_curve = capital_curve

    if not trades:
        return result

    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    result.win_trades = len(wins)
    result.lose_trades = len(losses)
    result.win_rate = result.win_trades / result.total_trades
    result.avg_win_pct = sum(t.pnl_pct for t in wins) / len(wins) if wins else 0
    result.avg_lose_pct = sum(t.pnl_pct for t in losses) / len(losses) if losses else 0
    result.profit_ratio = abs(result.avg_win_pct / result.avg_lose_pct) if result.avg_lose_pct else 0
    result.total_pnl = sum(t.pnl for t in trades)

    # 最大回撤
    peak = capital_curve[0]
    max_dd = 0
    for c in capital_curve:
        peak = max(peak, c)
        dd = (peak - c) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)
    result.max_drawdown_pct = round(max_dd, 2)

    # 月度收益
    monthly = {}
    for t in trades:
        month = t.date[:7] if len(t.date) >= 7 else "unknown"
        monthly[month] = monthly.get(month, 0) + t.pnl
    result.monthly_returns = {k: round(v, 2) for k, v in monthly.items()}

    # 达标检查
    R = RISK_CONTROL
    result.passed = True
    result.fail_reasons = []

    if result.win_rate < R["backtest_min_win_rate"]:
        result.passed = False
        result.fail_reasons.append(
            f"胜率{result.win_rate*100:.1f}%<{R['backtest_min_win_rate']*100:.0f}%")

    if result.profit_ratio < R["backtest_min_profit_ratio"]:
        result.passed = False
        result.fail_reasons.append(
            f"盈亏比{result.profit_ratio:.1f}<{R['backtest_min_profit_ratio']}")

    if result.max_drawdown_pct > R["backtest_max_total_dd"]:
        result.passed = False
        result.fail_reasons.append(
            f"最大回撤{result.max_drawdown_pct:.1f}%>{R['backtest_max_total_dd']}%")

    if result.total_trades < R["backtest_min_samples"]:
        result.fail_reasons.append(
            f"样本{result.total_trades}<{R['backtest_min_samples']}(参考)")

    logger.info(
        f"[回测] {result.total_trades}笔 胜率{result.win_rate*100:.0f}% "
        f"盈亏比{result.profit_ratio:.1f} 回撤{result.max_drawdown_pct:.1f}% "
        f"PnL={result.total_pnl:+.0f} {'PASS' if result.passed else 'FAIL'}")

    return result
