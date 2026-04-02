"""
复合快照聚合
═══════════════════════════════════════════════════════════════════════════════════
将 腾讯 + 同花顺 数据聚合为统一快照，供策略/报告/Dashboard使用。
"""
from __future__ import annotations

import datetime
import logging
from typing import Dict

from loguru import logger

from feeds.resilient import resilient_fetch_indices
from feeds.tencent_data import (
    get_indices, get_watchlist, get_futures, get_hk_stocks, get_us_stocks,
)
from feeds.akshare_data import get_zt_pool

# 同花顺涨停池
try:
    import requests
    def get_zt_pool_10jqka() -> Dict:
        """同花顺涨停池"""
        try:
            url = ("https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool?"
                   "page=1&limit=100&field=2024,199112,199113,199114"
                   "&order_field=199112&order_type=0&ajax=1")
            resp = requests.get(url, headers={
                "Referer": "https://data.10jqka.com.cn/",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"
            }, timeout=8)
            data = resp.json()
            info = data.get("data", {}).get("info", [])
            if not info:
                return {}
            ladder = {}
            for item in info:
                boards = 1 if item.get("is_again_limit") != 1 else 2
                board_list = ladder.setdefault(boards, [])
                board_list.append({"name": item.get("name", ""), "code": item.get("code", "")})
            return {
                "zt_count": len(info),
                "highest_board": max([1 if item.get("is_again_limit") != 1 else 2 for item in info]),
                "consecutive_ladder": ladder,
            }
        except Exception as e:
            logger.warning(f"[snapshot] 同花顺涨停池失败: {e}")
            return {}
except Exception:
    def get_zt_pool_10jqka():
        return {}


def build_analysis_snapshot() -> Dict:
    """分钟级分析快照（腾讯 + 同花顺）"""
    try:
        tencent_indices = get_indices()
    except Exception as e:
        logger.warning(f"[snapshot] 腾讯指数失败: {e}")
        tencent_indices = {}

    try:
        watchlist = get_watchlist()
    except Exception:
        watchlist = {}

    try:
        futures_data = get_futures()
    except Exception:
        futures_data = {}

    try:
        hk_stocks = get_hk_stocks()
    except Exception:
        hk_stocks = {}

    try:
        us_stocks = get_us_stocks()
    except Exception:
        us_stocks = {}

    # 降级fallback
    try:
        indices = resilient_fetch_indices()
    except Exception:
        indices = []

    # 涨停数据：优先同花顺，备选akshare
    zt_stats = get_zt_pool_10jqka()
    if not zt_stats:
        try:
            df = get_zt_pool()
            if not df.empty:
                board_col = None
                for c in ["连板数", "连续涨停天数", "连板天数", "days"]:
                    if c in df.columns:
                        board_col = c
                        break
                highest_board = int(df[board_col].max()) if board_col and board_col in df.columns else 1
                zt_stats = {
                    "zt_count": len(df),
                    "highest_board": highest_board,
                    "consecutive_ladder": {},
                }
        except Exception as e:
            logger.warning(f"[snapshot] 涨停池失败: {e}")

    return {
        "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source": "tencent",
        "indices": indices,
        "tencent": {
            "indices": tencent_indices,
            "watchlist": watchlist,
            "futures": futures_data,
            "hk": hk_stocks,
            "us": us_stocks,
        },
        "zt_stats": zt_stats,
        "breadth": {"up_count": 0, "down_count": 0, "flat_count": 0},
        "main_flow_100m": 0.0,
        "sector_flow_top5": [],
    }


def build_market_env_data() -> Dict:
    """
    提取策略 zone_classifier 所需的全部市场环境数据
    返回可直接传入 classify_zone() 的参数
    """
    try:
        zt_stats = get_zt_pool_10jqka()
        if not zt_stats:
            df = get_zt_pool()
            if not df.empty:
                zt_stats = {"zt_count": len(df), "highest_board": 1, "consecutive_ladder": {}}
    except Exception:
        zt_stats = {"zt_count": 0, "highest_board": 0, "consecutive_ladder": {}}

    return {
        "highest_board": zt_stats.get("highest_board", 0),
        "limit_up_count": zt_stats.get("zt_count", 0),
        "broken_rate": 0.0,
        "up_down_ratio": 1.0,
        "main_flow_100m": 0.0,
    }
