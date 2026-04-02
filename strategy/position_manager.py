"""
仓位管理（zone x emotion 双重修正）
═══════════════════════════════════════════════════════════════════════════
1万本金仓位表：
  super_attack: 总仓100% → 10000元, 2票(6000+4000)
  attack:       总仓70%  → 7000元, 2票(4000+3000)
  range:        总仓30%  → 3000元, 仅1票
  risk:         总仓0%   → 空仓
"""
from __future__ import annotations

from typing import Tuple

from config import ZONE_POSITION, TRADE_RULES
from strategy.emotion_cycle import get_emotion_modifier


def get_position_policy(zone: str, emotion: str) -> dict:
    """
    获取修正后的仓位策略

    Returns: {
        "total_pct": 实际总仓位比例,
        "max_single_pct": 单票上限比例,
        "max_holdings": 最多持仓数,
        "total_amount": 总仓位金额,
        "single_amount": 单票金额上限,
    }
    """
    policy = ZONE_POSITION.get(zone, ZONE_POSITION["risk"])
    modifier = get_emotion_modifier(emotion)
    capital = TRADE_RULES["initial_capital"]

    total_pct = round(policy["total_pct"] * modifier, 3)
    single_pct = min(policy["max_single_pct"], total_pct)

    return {
        "total_pct": total_pct,
        "max_single_pct": single_pct,
        "max_holdings": policy["max_holdings"],
        "total_amount": round(capital * total_pct, 0),
        "single_amount": round(capital * single_pct, 0),
    }


def get_adjusted_position(zone: str, emotion: str) -> Tuple[float, float]:
    """
    快捷接口：返回 (总仓位比例, 单票仓位比例)
    """
    p = get_position_policy(zone, emotion)
    return p["total_pct"], p["max_single_pct"]


def can_open_new_position(zone: str, current_count: int) -> bool:
    """是否可以开新仓"""
    policy = ZONE_POSITION.get(zone, ZONE_POSITION["risk"])
    return current_count < policy["max_holdings"]


def can_add_position(zone: str) -> bool:
    """是否允许加仓（仅进攻区）"""
    return zone in ("super_attack", "attack")


def calc_buy_shares(price: float, max_amount: float) -> int:
    """计算买入股数（100股整手）"""
    if price <= 0 or max_amount <= 0:
        return 0
    shares = int(max_amount / price / 100) * 100
    return max(shares, 0)
