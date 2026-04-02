"""
盘中监控调度（与AutoPilot协作）
═══════════════════════════════════════════════════════════════════════════
由 scheduler 每分钟调用，负责：
  1. 更新持仓天数
  2. 加载/刷新交易计划到 AutoPilot
  3. 汇总监控数据供 Dashboard
"""
from __future__ import annotations

import datetime
from typing import Dict

from loguru import logger

from models import get_session, Position, WatchList
from engine.autopilot import get_runtime
from engine.simulator import TradeSimulator
from openclaw_adapter import is_trading_hours, is_trading_day

_simulator = TradeSimulator()


def run_daily_open():
    """
    每日开盘前执行（09:25调用）：
      - 更新持仓天数
      - 加载交易计划到 AutoPilot
    """
    if not is_trading_day():
        return

    logger.info("[监控] 执行开盘前初始化")

    # 更新持仓天数
    _simulator.update_hold_days()

    # 加载今日交易计划
    _load_trade_plans()


def _load_trade_plans():
    """从WatchList加载今日交易计划到AutoPilot"""
    from strategy.signal import TradePlan
    from config import TRADE_RULES

    session = get_session()
    try:
        today = datetime.date.today()
        entries = session.query(WatchList).filter_by(date=today).filter(
            WatchList.status.in_(["pending", "watching"])).all()

        plans = []
        for e in entries:
            plan = TradePlan(
                code=e.code, name=e.name or "",
                sector=e.sector or "",
                zone="",  # 运行时动态获取
                emotion="",
                target_position_pct=0,
                entry_price=e.entry_price or 0,
                add_price=round((e.entry_price or 0) * (1 + TRADE_RULES["add_trigger_pct"]), 3),
                stop_price=e.stop_price or round((e.entry_price or 0) * (1 + TRADE_RULES["stop_loss_pct"]), 3),
                tp1_price=e.tp1_price or round((e.entry_price or 0) * (1 + TRADE_RULES["take_profit_1_pct"]), 3),
                tp2_price=e.tp2_price or round((e.entry_price or 0) * (1 + TRADE_RULES["take_profit_2_pct"]), 3),
                hold_days_max=TRADE_RULES["max_hold_days"],
                pick_type=e.pick_type or "",
                score=e.score or 0,
                reasons=[e.reason or ""],
            )
            plans.append(plan)
            e.status = "watching"

        session.commit()
        session.close()

        if plans:
            runtime = get_runtime()
            runtime.set_trade_plans(plans)
            logger.info(f"[监控] 加载 {len(plans)} 个交易计划到 AutoPilot")
    except Exception as e:
        logger.error(f"[监控] 加载交易计划失败: {e}")


def get_monitor_summary() -> Dict:
    """获取监控汇总（供Dashboard API）"""
    session = get_session()
    try:
        positions = session.query(Position).all()
        runtime = get_runtime()

        return {
            "zone": runtime.current_zone,
            "emotion": runtime.current_emotion,
            "positions": [{
                "code": p.code, "name": p.name,
                "buy_price": p.buy_price, "current_price": p.current_price,
                "shares": p.shares, "pnl": p.current_pnl,
                "pnl_pct": p.current_pnl_pct,
                "hold_days": p.hold_days, "strategy": p.strategy,
            } for p in positions],
            "autopilot_running": runtime.is_running,
            "tick_count": runtime.tick_count,
        }
    finally:
        session.close()
