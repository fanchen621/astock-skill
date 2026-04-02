"""
知识提炼引擎：从爬取内容学习→策略进化建议
═══════════════════════════════════════════════════════════════════════════
学习产出：
  - param_suggestion: 参数建议
  - pattern_discovery: 热门板块/战法发现
  - risk_warning: 多源风险预警
  - strategy_idea: 策略灵感
"""
from __future__ import annotations

import datetime
from typing import Dict, List

from loguru import logger

from intelligence.knowledge_base import get_param_references, get_recent_knowledge
from config import TRADE_RULES
from models import get_param


def generate_learning_insights() -> List[Dict]:
    """生成知识驱动的进化建议"""
    insights = []

    # 1. 参数对比
    insights.extend(_compare_params())
    # 2. 板块热度发现
    insights.extend(_discover_hot_sectors())
    # 3. 风险预警聚合
    insights.extend(_aggregate_risk_warnings())
    # 4. 策略灵感
    insights.extend(_extract_strategy_ideas())

    logger.info(f"[学习] 生成{len(insights)}条洞察")
    return insights


def _compare_params() -> List[Dict]:
    """将自身参数与知识库中的参考值对比"""
    insights = []
    refs = get_param_references()

    param_map = {
        "stop_loss": ("stop_loss_pct", abs(TRADE_RULES["stop_loss_pct"]) * 100),
        "take_profit": ("take_profit_1_pct", TRADE_RULES["take_profit_1_pct"] * 100),
        "win_rate": (None, None),
        "max_drawdown": (None, None),
    }

    for ref_name, values in refs.items():
        if not values or len(values) < 3:
            continue
        avg = sum(values) / len(values)
        median = sorted(values)[len(values) // 2]
        mapping = param_map.get(ref_name)
        if not mapping:
            continue
        our_key, our_val = mapping
        if our_val is None:
            continue

        diff = abs(our_val - median)
        if diff > median * 0.3:  # 偏差>30%
            direction = "偏高" if our_val > median else "偏低"
            insights.append({
                "type": "param_suggestion",
                "param": ref_name,
                "our_value": our_val,
                "reference_median": round(median, 2),
                "reference_avg": round(avg, 2),
                "sample_count": len(values),
                "suggestion": f"我们的{ref_name}={our_val:.1f}%，"
                              f"知识库中位数{median:.1f}%（{len(values)}个样本），{direction}",
            })

    return insights


def _discover_hot_sectors() -> List[Dict]:
    """从知识库发现当前热门板块"""
    entries = get_recent_knowledge(min_quality=30, limit=100)
    sector_freq: Dict[str, int] = {}
    for e in entries:
        for tag in (e.get("tags") or []):
            if tag not in ("短线", "趋势", "价值", "量化"):
                sector_freq[tag] = sector_freq.get(tag, 0) + 1

    if not sector_freq:
        return []

    top_sectors = sorted(sector_freq.items(), key=lambda x: x[1], reverse=True)[:5]
    return [{
        "type": "pattern_discovery",
        "discovery": "hot_sectors",
        "sectors": [{"name": s, "mentions": c} for s, c in top_sectors],
        "suggestion": f"当前知识库热门板块: {', '.join(s for s, _ in top_sectors[:3])}",
    }]


def _aggregate_risk_warnings() -> List[Dict]:
    """聚合多源风险信号"""
    entries = get_recent_knowledge(min_quality=30, limit=50)
    bear_count = sum(1 for e in entries if e.get("market_view") == "bearish")
    bull_count = sum(1 for e in entries if e.get("market_view") == "bullish")
    total = len(entries)

    insights = []
    if total >= 10:
        bear_ratio = bear_count / total
        if bear_ratio > 0.6:
            insights.append({
                "type": "risk_warning",
                "severity": "high",
                "bear_ratio": round(bear_ratio, 2),
                "suggestion": f"知识库中{bear_ratio*100:.0f}%文章偏空，"
                              f"多源风险信号一致，建议降低仓位",
            })
        elif bear_ratio > 0.4:
            insights.append({
                "type": "risk_warning",
                "severity": "medium",
                "bear_ratio": round(bear_ratio, 2),
                "suggestion": f"知识库中{bear_ratio*100:.0f}%文章偏空，市场分歧较大",
            })

    return insights


def _extract_strategy_ideas() -> List[Dict]:
    """提取策略灵感"""
    entries = get_recent_knowledge(entry_type="strategy", min_quality=60, limit=20)
    ideas = []
    for e in entries:
        params = e.get("params") or {}
        if params and not e.get("applied"):
            ideas.append({
                "type": "strategy_idea",
                "source": e.get("source"),
                "title": e.get("title", "")[:80],
                "params": params,
                "quality": e.get("quality"),
                "suggestion": f"[{e.get('source')}] {e.get('title','')[:60]}",
            })
    return ideas[:5]


def run_nightly_learning():
    """夜间深度学习（凌晨执行）"""
    logger.info("[学习] 开始夜间深度学习...")

    from intelligence.crawler import run_crawl_cycle
    from intelligence.knowledge_base import save_entries, cleanup_expired

    # 深度爬取
    items = run_crawl_cycle(deep=True)
    saved = save_entries(items)

    # 清理过期
    cleanup_expired()

    # 生成洞察
    insights = generate_learning_insights()

    # 推送摘要
    if insights:
        from openclaw_adapter import push_message
        msg = "**夜间学习报告**\n\n"
        msg += f"- 爬取{len(items)}条，入库{saved}条\n"
        for ins in insights[:5]:
            msg += f"- [{ins['type']}] {ins.get('suggestion','')}\n"
        push_message(msg, title="DragonFlow 学习")

    return {"crawled": len(items), "saved": saved, "insights": insights}
