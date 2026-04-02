"""
统计引擎
═══════════════════════════════════════════════════════════════════════════
每日收盘后计算交易统计（日/周/月维度）
"""
from __future__ import annotations

import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import func

from models import get_session, TradeRecord, StrategyStats, Position
from config import TRADE_RULES


def compute_daily_stats(date: Optional[datetime.date] = None):
    """计算指定日期的交易统计"""
    if date is None:
        date = datetime.date.today()

    session = get_session()
    try:
        # 当日卖出记录
        sells = session.query(TradeRecord).filter(
            TradeRecord.date == date,
            TradeRecord.direction == "SELL",
        ).all()

        if not sells:
            logger.info(f"[统计] {date} 无卖出记录，跳过")
            return

        total = len(sells)
        wins = [s for s in sells if (s.pnl or 0) > 0]
        losses = [s for s in sells if (s.pnl or 0) <= 0]
        win_count = len(wins)
        lose_count = len(losses)
        win_rate = win_count / total if total > 0 else 0

        avg_win = sum(s.pnl_pct or 0 for s in wins) / win_count if win_count else 0
        avg_lose = sum(s.pnl_pct or 0 for s in losses) / lose_count if lose_count else 0
        profit_ratio = abs(avg_win / avg_lose) if avg_lose != 0 else 0
        total_pnl = sum(s.pnl or 0 for s in sells)

        # 计算当日最大回撤（基于持仓浮亏）
        max_dd = 0.0
        capital = TRADE_RULES["initial_capital"]
        for s in sells:
            if s.pnl_pct and s.pnl_pct < max_dd:
                max_dd = s.pnl_pct

        # 期末资金估算
        realized = session.query(func.sum(TradeRecord.pnl)).filter(
            TradeRecord.direction == "SELL").scalar() or 0
        pos_cost = session.query(func.sum(Position.cost)).scalar() or 0
        capital_end = capital + realized - pos_cost

        # 区间分布
        zone_dist = {}
        for s in sells:
            z = s.zone or "unknown"
            zone_dist[z] = zone_dist.get(z, 0) + 1

        # 亏损原因
        loss_reasons = {}
        for s in losses:
            reason = s.signal_type or "unknown"
            loss_reasons[reason] = loss_reasons.get(reason, 0) + 1

        stat = StrategyStats(
            period_type="day",
            period_start=date,
            period_end=date,
            total_trades=total,
            win_trades=win_count,
            lose_trades=lose_count,
            win_rate=round(win_rate, 4),
            avg_win_pct=round(avg_win, 2),
            avg_lose_pct=round(avg_lose, 2),
            profit_ratio=round(profit_ratio, 2),
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(abs(max_dd), 2),
            capital_end=round(capital_end, 2),
            zone_distribution=zone_dist,
            loss_reasons=[{"reason": k, "count": v} for k, v in loss_reasons.items()],
        )
        session.add(stat)
        session.commit()

        logger.info(f"[统计] {date} 完成：{total}笔 胜率{win_rate*100:.0f}% "
                    f"盈亏比{profit_ratio:.1f} PnL={total_pnl:+.0f}")

    except Exception as e:
        session.rollback()
        logger.error(f"[统计] 日度统计失败: {e}")
    finally:
        session.close()


def compute_weekly_stats():
    """计算本周统计"""
    today = datetime.date.today()
    week_start = today - datetime.timedelta(days=today.weekday())
    _compute_period_stats("week", week_start, today)


def compute_monthly_stats():
    """计算本月统计"""
    today = datetime.date.today()
    month_start = today.replace(day=1)
    _compute_period_stats("month", month_start, today)


def _compute_period_stats(period_type: str,
                          start: datetime.date, end: datetime.date):
    session = get_session()
    try:
        sells = session.query(TradeRecord).filter(
            TradeRecord.date >= start,
            TradeRecord.date <= end,
            TradeRecord.direction == "SELL",
        ).all()

        if not sells:
            return

        total = len(sells)
        wins = [s for s in sells if (s.pnl or 0) > 0]
        losses = [s for s in sells if (s.pnl or 0) <= 0]
        win_count = len(wins)
        lose_count = len(losses)
        win_rate = win_count / total if total > 0 else 0
        avg_win = sum(s.pnl_pct or 0 for s in wins) / win_count if win_count else 0
        avg_lose = sum(s.pnl_pct or 0 for s in losses) / lose_count if lose_count else 0
        profit_ratio = abs(avg_win / avg_lose) if avg_lose != 0 else 0
        total_pnl = sum(s.pnl or 0 for s in sells)
        max_dd = min((s.pnl_pct or 0) for s in sells) if sells else 0

        # 更新或创建
        existing = session.query(StrategyStats).filter_by(
            period_type=period_type, period_start=start).first()
        if existing:
            stat = existing
        else:
            stat = StrategyStats(period_type=period_type,
                                 period_start=start, period_end=end)
            session.add(stat)

        stat.period_end = end
        stat.total_trades = total
        stat.win_trades = win_count
        stat.lose_trades = lose_count
        stat.win_rate = round(win_rate, 4)
        stat.avg_win_pct = round(avg_win, 2)
        stat.avg_lose_pct = round(avg_lose, 2)
        stat.profit_ratio = round(profit_ratio, 2)
        stat.total_pnl = round(total_pnl, 2)
        stat.max_drawdown = round(abs(max_dd), 2)
        session.commit()

        logger.info(f"[统计] {period_type} {start}~{end}：{total}笔 "
                    f"胜率{win_rate*100:.0f}% PnL={total_pnl:+.0f}")
    except Exception as e:
        session.rollback()
        logger.error(f"[统计] {period_type}统计失败: {e}")
    finally:
        session.close()


def get_recent_stats(days: int = 10) -> list:
    """获取最近N天日度统计"""
    session = get_session()
    try:
        since = datetime.date.today() - datetime.timedelta(days=days * 2)
        stats = session.query(StrategyStats).filter(
            StrategyStats.period_type == "day",
            StrategyStats.period_start >= since,
        ).order_by(StrategyStats.period_start.desc()).limit(days).all()

        return [{
            "date": str(s.period_start),
            "trades": s.total_trades,
            "win_rate": s.win_rate,
            "profit_ratio": s.profit_ratio,
            "pnl": s.total_pnl,
            "max_dd": s.max_drawdown,
        } for s in stats]
    finally:
        session.close()
