"""
交易规则引擎（1万本金专属）
═══════════════════════════════════════════════════════════════════════════
核心规则来自文少 + Codex PLAYBOOK：
  - 止损：-1.8%（硬性）
  - 止盈：+5%减半，+8%再减半
  - 最多持有3天
  - 次日不及预期离场
  - 禁止追高/抄底/补仓
  - 加仓仅进攻区，成本+2%后加50%

所有信号检查函数返回 TradeSignal 或 None
"""
from __future__ import annotations

import datetime
from typing import Dict, Optional

from loguru import logger

from config import TRADE_RULES, ALLOWED_PREFIXES
from strategy.signal import TradeSignal, TradePlan, StockCandidate
from strategy.position_manager import (
    get_position_policy, calc_buy_shares, can_open_new_position, can_add_position,
)


def is_allowed_code(code: str) -> bool:
    """硬约束：只允许沪深主板"""
    return any(code.startswith(p) for p in ALLOWED_PREFIXES)


# ════════════════════════════════════════════════════════════════════════════
# 买入信号检查
# ════════════════════════════════════════════════════════════════════════════

def check_buy_signal(
    plan: TradePlan,
    realtime: Dict,
    zone: str,
    emotion: str,
    current_positions: int,
) -> Optional[TradeSignal]:
    """
    检查买入信号
    Args:
        plan: 交易计划（由选股生成）
        realtime: 实时行情 {price, change_pct, volume_ratio, ...}
        zone: 当前市场区间
        emotion: 当前情绪周期
        current_positions: 当前持仓数
    """
    code = plan.code
    if not is_allowed_code(code):
        return None

    # 风险区禁止开仓
    if zone == "risk":
        return None

    # 持仓数检查
    if not can_open_new_position(zone, current_positions):
        return None

    price = float(realtime.get("price", 0))
    vol_ratio = float(realtime.get("volume_ratio", 0))
    change_pct = float(realtime.get("change_pct", 0))

    if price <= 0:
        return None

    # 禁止追高（已涨>max_chase_pct不追）
    max_chase = TRADE_RULES["max_chase_pct"]
    if change_pct > max_chase:
        logger.debug(f"[规则] {code} 涨{change_pct:.1f}%>{max_chase}%，禁追高")
        return None

    # 量比门槛
    min_vol = TRADE_RULES["min_buy_volume_ratio"]
    if vol_ratio < min_vol:
        logger.debug(f"[规则] {code} 量比{vol_ratio:.1f}<{min_vol}，跳过")
        return None

    # 开盘价不能高开太多（相对计划价）
    if plan.entry_price > 0 and price > plan.entry_price * 1.03:
        logger.debug(f"[规则] {code} 现价{price}远超计划价{plan.entry_price}，不追")
        return None

    # 计算买入股数
    policy = get_position_policy(zone, emotion)
    shares = calc_buy_shares(price, policy["single_amount"])
    if shares <= 0:
        return None

    reason = (f"[{plan.pick_type}] {plan.sector} | "
              f"量比{vol_ratio:.1f} | 涨{change_pct:.1f}% | "
              f"{zone}/{emotion}")

    return TradeSignal(
        code=code, name=plan.name,
        direction="BUY", price=price, shares=shares,
        signal_type=plan.pick_type,
        signal_reason=reason,
        timestamp=datetime.datetime.now(),
        minute_price=price, volume_ratio=vol_ratio,
        zone=zone, emotion=emotion,
        pick_type=plan.pick_type,
    )


# ════════════════════════════════════════════════════════════════════════════
# 卖出信号检查
# ════════════════════════════════════════════════════════════════════════════

def check_sell_signal(
    position: Dict,
    realtime: Dict,
    zone: str,
    emotion: str,
    force_close: bool = False,
) -> Optional[TradeSignal]:
    """
    检查卖出信号
    Args:
        position: 持仓数据 {code, name, buy_price, shares, buy_date, hold_days, ...}
        realtime: 实时行情 {price, change_pct, volume_ratio, ...}
        force_close: 收盘前强制平仓
    """
    code = position.get("code", "")
    price = float(realtime.get("price", 0))
    buy_price = float(position.get("buy_price", price))
    shares = int(position.get("shares", 0))
    hold_days = int(position.get("hold_days", 0))

    if price <= 0 or shares <= 0:
        return None

    pnl_pct = (price - buy_price) / buy_price
    vol_ratio = float(realtime.get("volume_ratio", 0))

    signal_type = None
    reason = ""
    sell_shares = shares

    # 1. 硬止损 -1.8%（最高优先级，不可延迟）
    stop_loss = TRADE_RULES["stop_loss_pct"]
    if pnl_pct <= stop_loss:
        signal_type = "止损"
        reason = f"跌{pnl_pct*100:.1f}%触发硬止损{stop_loss*100:.1f}%"

    # 2. 止盈2 +8%（全清）
    elif pnl_pct >= TRADE_RULES["take_profit_2_pct"]:
        signal_type = "止盈2"
        reason = f"涨{pnl_pct*100:.1f}%达止盈2(+{TRADE_RULES['take_profit_2_pct']*100:.0f}%)，清仓"

    # 3. 止盈1 +5%（减半）
    elif pnl_pct >= TRADE_RULES["take_profit_1_pct"]:
        signal_type = "止盈1"
        sell_shares = max(shares // 2, 100)  # 至少卖100股
        reason = f"涨{pnl_pct*100:.1f}%达止盈1(+{TRADE_RULES['take_profit_1_pct']*100:.0f}%)，减半"

    # 4. T+3 强制清仓
    elif hold_days >= TRADE_RULES["max_hold_days"]:
        signal_type = "T+3清仓"
        reason = f"持仓{hold_days}天达上限{TRADE_RULES['max_hold_days']}天，无条件清仓"

    # 5. 次日不及预期（hold_days >= 1 时检查）
    elif hold_days >= 1:
        now = datetime.datetime.now()
        # 10:00前跌破成本-1%
        if now.hour < 10 or (now.hour == 10 and now.minute == 0):
            fail_pct = TRADE_RULES["next_day_fail_pct"]
            if pnl_pct <= fail_pct:
                signal_type = "次日不及预期"
                reason = f"次日10:00前跌{pnl_pct*100:.1f}%破成本{fail_pct*100:.0f}%"

        # 开盘30分钟量比<1.0
        if not signal_type and now.hour == 9 and now.minute >= 30 and now.minute < 60:
            min_vol = TRADE_RULES["next_day_low_vol_ratio"]
            if vol_ratio > 0 and vol_ratio < min_vol:
                signal_type = "次日不及预期"
                reason = f"开盘30分钟量比{vol_ratio:.1f}<{min_vol}，缩量"

    # 6. 收盘前强制平仓
    elif force_close:
        signal_type = "收盘前平仓"
        reason = "收盘前强制清仓"

    if not signal_type:
        return None

    return TradeSignal(
        code=code, name=position.get("name", ""),
        direction="SELL", price=price, shares=sell_shares,
        signal_type=signal_type, signal_reason=reason,
        timestamp=datetime.datetime.now(),
        minute_price=price, volume_ratio=vol_ratio,
        zone=zone, emotion=emotion,
    )


# ════════════════════════════════════════════════════════════════════════════
# 加仓信号检查
# ════════════════════════════════════════════════════════════════════════════

def check_add_signal(
    position: Dict,
    realtime: Dict,
    zone: str,
    emotion: str,
) -> Optional[TradeSignal]:
    """
    检查加仓信号
    条件：仅 super_attack/attack，且涨超成本+2%
    加仓量：原仓位50%
    """
    if not can_add_position(zone):
        return None

    code = position.get("code", "")
    price = float(realtime.get("price", 0))
    buy_price = float(position.get("buy_price", 0))
    shares = int(position.get("shares", 0))

    if price <= 0 or buy_price <= 0:
        return None

    trigger = TRADE_RULES["add_trigger_pct"]
    if (price - buy_price) / buy_price < trigger:
        return None

    add_shares = int(shares * TRADE_RULES["add_ratio"] / 100) * 100
    if add_shares < 100:
        return None

    return TradeSignal(
        code=code, name=position.get("name", ""),
        direction="BUY", price=price, shares=add_shares,
        signal_type="加仓",
        signal_reason=f"成本+{trigger*100:.0f}%触发加仓，加{add_shares}股（原仓50%）",
        timestamp=datetime.datetime.now(),
        minute_price=price,
        volume_ratio=float(realtime.get("volume_ratio", 0)),
        zone=zone, emotion=emotion,
    )


# ════════════════════════════════════════════════════════════════════════════
# 交易计划生成
# ════════════════════════════════════════════════════════════════════════════

def build_trade_plan(
    stock: StockCandidate,
    zone: str,
    emotion: str,
    pick_type: str,
    score: float,
    reasons: list,
) -> TradePlan:
    """从选股结果生成交易计划"""
    _, single_pct = __import__("strategy.position_manager",
                                fromlist=["get_adjusted_position"]).get_adjusted_position(zone, emotion)

    entry = stock.close
    return TradePlan(
        code=stock.code,
        name=stock.name,
        sector=stock.sector,
        zone=zone,
        emotion=emotion,
        target_position_pct=single_pct,
        entry_price=entry,
        add_price=round(entry * (1 + TRADE_RULES["add_trigger_pct"]), 3),
        stop_price=round(entry * (1 + TRADE_RULES["stop_loss_pct"]), 3),
        tp1_price=round(entry * (1 + TRADE_RULES["take_profit_1_pct"]), 3),
        tp2_price=round(entry * (1 + TRADE_RULES["take_profit_2_pct"]), 3),
        hold_days_max=TRADE_RULES["max_hold_days"],
        pick_type=pick_type,
        score=score,
        reasons=reasons,
    )
