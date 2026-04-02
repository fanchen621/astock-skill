"""
午盘分析（11:35）
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import datetime
from typing import Dict

from loguru import logger

from feeds.market_snapshot import build_analysis_snapshot, build_market_env_data
from strategy.zone_classifier import classify_zone, get_zone_cn
from strategy.emotion_cycle import get_emotion_cycle, get_emotion_advice
from strategy.position_manager import get_adjusted_position
from models import get_session, Position


def generate_midday_report() -> Dict:
    """生成午盘分析"""
    logger.info("[午盘] 生成午盘分析...")

    snapshot = build_analysis_snapshot()
    env_data = build_market_env_data()
    zone, zone_notes = classify_zone(env_data)
    zt_count = env_data.get("limit_up_count", 0)
    emotion = get_emotion_cycle(zt_count)
    total_pos, single_pos = get_adjusted_position(zone, emotion)

    indices = snapshot.get("indices", [])
    breadth = snapshot.get("breadth", {})
    sectors = snapshot.get("sector_flow_top5", [])

    # 持仓状态
    session = get_session()
    positions = session.query(Position).all()
    session.close()

    msg = f"## 午盘分析 {datetime.date.today().isoformat()}\n\n"
    msg += f"### 区间: [{get_zone_cn(zone)}] | 情绪: [{emotion}]\n"
    msg += f"{zone_notes[0] if zone_notes else ''}\n"
    msg += f"{get_emotion_advice(emotion)}\n\n"

    # 指数
    msg += "### 指数行情\n"
    for idx in indices:
        emoji = "+" if idx.get("pct", 0) >= 0 else ""
        msg += f"- {idx.get('name','')}: {idx.get('last',0):.2f} ({emoji}{idx.get('pct',0):.2f}%)\n"

    # 市场宽度
    msg += f"\n### 市场宽度\n"
    msg += f"- 涨停 {zt_count} | 炸板率 {env_data.get('broken_rate',0):.0%}\n"
    msg += f"- 涨跌比 {breadth.get('up_down_ratio',0):.1f}\n"
    msg += f"- 主力 {env_data.get('main_flow_100m',0):+.1f}亿\n"

    # 板块
    if sectors:
        msg += "\n### 板块Top5\n"
        for s in sectors:
            msg += f"- {s['name']}: {s.get('change_pct',0):+.1f}%\n"

    # 持仓
    if positions:
        msg += f"\n### 持仓 ({len(positions)}只)\n"
        for p in positions:
            pnl_str = f"+{p.current_pnl:.0f}" if (p.current_pnl or 0) >= 0 else f"{p.current_pnl:.0f}"
            msg += (f"- {p.code} {p.name}: "
                    f"{p.current_price:.2f} ({pnl_str}元, {p.current_pnl_pct or 0:+.1f}%)\n")

    msg += f"\n**下午策略**: 仓位{total_pos*100:.0f}%, 严格执行止损止盈"

    return {
        "message": msg,
        "data": {
            "zone": zone, "emotion": emotion,
            "zt_count": zt_count,
            "emotion_score": 0,
            "indices": indices,
        },
    }
