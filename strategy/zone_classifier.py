"""
4区间市场判断
═══════════════════════════════════════════════════════════════════════════
来自文少体系 + Codex TRADING_PLAYBOOK，量化判断当前市场处于哪个区间：
  - super_attack: 超级进攻（满仓）
  - attack:       进攻（7成仓）
  - range:        震荡（3成仓）
  - risk:         风险（空仓）

所有条件必须同时满足（AND逻辑），不满足任一区间 → risk
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from config import ZONE_THRESHOLDS


def classify_zone(market_data: Dict) -> Tuple[str, List[str]]:
    """
    判断市场区间

    Args:
        market_data: {
            "highest_board": int,    # 最高连板数
            "limit_up_count": int,   # 涨停家数
            "broken_rate": float,    # 炸板率（0-1）
            "up_down_ratio": float,  # 涨跌家数比
            "main_flow_100m": float, # 主力净流入（亿）
        }

    Returns: (zone_name, reason_list)
    """
    hb = market_data.get("highest_board", 0)
    luc = market_data.get("limit_up_count", 0)
    br = market_data.get("broken_rate", 1.0)
    udr = market_data.get("up_down_ratio", 0.0)
    mf = market_data.get("main_flow_100m", -999)

    # 按优先级从高到低检查
    for zone_name in ("super_attack", "attack", "range"):
        t = ZONE_THRESHOLDS[zone_name]
        conditions = [
            (hb >= t["highest_board"], f"连板{hb}>={t['highest_board']}"),
            (luc >= t["limit_up_count"], f"涨停{luc}>={t['limit_up_count']}家"),
            (br <= t["broken_rate"], f"炸板率{br:.0%}<={t['broken_rate']:.0%}"),
            (udr >= t["up_down_ratio"], f"涨跌比{udr:.1f}>={t['up_down_ratio']}"),
            (mf >= t["main_flow_100m"], f"主力{mf:.1f}亿>={t['main_flow_100m']}亿"),
        ]

        all_pass = all(c[0] for c in conditions)
        if all_pass:
            notes = [c[1] for c in conditions]
            zone_cn = {"super_attack": "超级进攻", "attack": "进攻",
                       "range": "震荡"}[zone_name]
            return zone_name, [f"[{zone_cn}区] " + "；".join(notes)]

    # 不满足任何区间 → risk
    fail_notes = []
    t = ZONE_THRESHOLDS["range"]  # 连最低的都不满足
    if hb < t["highest_board"]:
        fail_notes.append(f"连板{hb}<{t['highest_board']}")
    if luc < t["limit_up_count"]:
        fail_notes.append(f"涨停{luc}<{t['limit_up_count']}家")
    if br > t["broken_rate"]:
        fail_notes.append(f"炸板率{br:.0%}>{t['broken_rate']:.0%}")
    if udr < t["up_down_ratio"]:
        fail_notes.append(f"涨跌比{udr:.1f}<{t['up_down_ratio']}")
    if mf < t["main_flow_100m"]:
        fail_notes.append(f"主力{mf:.1f}亿<{t['main_flow_100m']}亿")

    return "risk", [f"[风险区] " + "；".join(fail_notes)]


def get_zone_cn(zone: str) -> str:
    return {"super_attack": "超级进攻", "attack": "进攻",
            "range": "震荡", "risk": "风险"}.get(zone, "未知")
