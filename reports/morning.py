"""
早盘速递（08:50）
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import datetime
from typing import Dict

from loguru import logger

from feeds.akshare_data import get_financial_news
from models import get_session, WatchList, Position


def generate_morning_report() -> Dict:
    """生成早盘速递"""
    logger.info("[早盘] 生成早盘速递...")

    news = get_financial_news(8)

    # 今日候选
    session = get_session()
    today = datetime.date.today()
    watchlist = session.query(WatchList).filter_by(date=today).all()
    positions = session.query(Position).all()
    session.close()

    msg = f"## 早盘速递 {today.isoformat()}\n\n"

    # 新闻
    high_news = [n for n in news if n.get("importance") == "high"]
    if high_news:
        msg += "### 重要资讯\n"
        for n in high_news[:3]:
            msg += f"- [{n.get('source','')}] {n['title']}\n"
        msg += "\n"

    # 今日候选
    if watchlist:
        msg += f"### 今日候选 ({len(watchlist)}只)\n"
        for w in watchlist:
            msg += (f"- **{w.code} {w.name}** [{w.pick_type or ''}] "
                    f"入场{w.entry_price or 0:.2f} "
                    f"止损{w.stop_price or 0:.2f}\n")
    else:
        msg += "### 今日候选: 无\n"

    # 持仓提醒
    if positions:
        msg += f"\n### 当前持仓 ({len(positions)}只)\n"
        for p in positions:
            msg += (f"- {p.code} {p.name} | "
                    f"持仓{p.hold_days or 0}天 | "
                    f"成本{p.buy_price:.2f}\n")
            if (p.hold_days or 0) >= 2:
                msg += f"  ** 注意：持仓已{p.hold_days}天，今日须清仓！**\n"

    msg += "\n---\n*开盘后 AutoPilot 自动执行交易计划，严格止损-1.8%*"

    return {
        "message": msg,
        "data": {
            "news": news,
            "watchlist_count": len(watchlist),
            "position_count": len(positions),
        },
    }
