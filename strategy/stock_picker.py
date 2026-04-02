"""
双龙破晓自动选股（文少核心战法 + akshare自动数据）
═══════════════════════════════════════════════════════════════════════════
选股流程：
  1. 从涨停池提取连板股候选
  2. 硬过滤6条件（题材Top3/量比>=1.8/20日突破/60日低点5-40%/涨幅2-9.5%/非一字板）
  3. 6维度综合评分（题材/量比/底部位置/换手率/连板高度/成交额）
  4. 沸腾期额外过滤换手率<15%
  5. 按评分排序，取top N
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from loguru import logger

from config import DOUBLE_DRAGON_FILTER, ZONE_POSITION
from strategy.signal import StockCandidate, PickResult


# ════════════════════════════════════════════════════════════════════════════
# 硬过滤
# ════════════════════════════════════════════════════════════════════════════

def apply_hard_filter(s: StockCandidate) -> Tuple[bool, List[str]]:
    """
    双龙破晓6条硬过滤
    Returns: (通过, 原因列表)
    """
    f = DOUBLE_DRAGON_FILTER
    reasons = []
    ok = True

    # 1. 题材排名
    if s.theme_rank > f["theme_rank_max"]:
        reasons.append(f"X 题材第{s.theme_rank}，非Top{f['theme_rank_max']}")
        ok = False
    else:
        reasons.append(f"V 主线题材Top{s.theme_rank}")

    # 2. 20日突破
    if s.breakout_20d != f["breakout_20d"]:
        reasons.append(f"X 未突破20日新高")
        ok = False
    else:
        reasons.append(f"V 20日突破确认")

    # 3. 量比
    if s.volume_ratio < f["volume_ratio_min"]:
        reasons.append(f"X 量比{s.volume_ratio:.2f}<{f['volume_ratio_min']}")
        ok = False
    else:
        reasons.append(f"V 量比{s.volume_ratio:.2f}")

    # 4. 非一字板
    if s.is_one_wall == 1:
        reasons.append(f"X 一字板涨停，换手差")
        ok = False

    # 5. 底部位置
    low = s.from_60d_low_pct
    if low < f["from_60d_low_pct_min"]:
        reasons.append(f"X 距低点{low:.1f}%<{f['from_60d_low_pct_min']}%")
        ok = False
    elif low > f["from_60d_low_pct_max"]:
        reasons.append(f"X 距低点{low:.1f}%>{f['from_60d_low_pct_max']}%，追高")
        ok = False
    else:
        reasons.append(f"V 底部{low:.1f}%（主升浪区间）")

    # 6. 涨幅
    chg = s.change_pct
    if chg < f["close_change_min"]:
        reasons.append(f"X 涨幅{chg:.1f}%<{f['close_change_min']}%")
        ok = False
    elif chg > f["close_change_max"]:
        reasons.append(f"X 涨幅{chg:.1f}%>{f['close_change_max']}%")
        ok = False
    else:
        reasons.append(f"V 涨幅{chg:.1f}%")

    # 7. 成交额（补充条件）
    if s.amount_100m < f.get("min_amount_100m", 0):
        reasons.append(f"X 成交额{s.amount_100m:.1f}亿不足")
        ok = False

    return ok, reasons


# ════════════════════════════════════════════════════════════════════════════
# 综合评分（6维度）
# ════════════════════════════════════════════════════════════════════════════

def score_stock(s: StockCandidate) -> Tuple[float, List[str]]:
    """
    文少核心评分逻辑：
    - 题材排名越靠前越好（最大36分）
    - 量比越大越强（最大60分）
    - 底部位置18%附近最优（最大25分）
    - 换手率>10%核心指标（最大36分）
    - 连板高度（最大28分）
    - 成交额/资金容量（最大24分）
    """
    score = 0.0
    reasons = []

    # 1. 题材排名（权重最大）
    pts = (4 - s.theme_rank) * 12
    score += pts
    reasons.append(f"题材Rank{s.theme_rank}: +{pts}")

    # 2. 量比
    pts = min(s.volume_ratio, 4.0) * 15
    score += pts
    reasons.append(f"量比{s.volume_ratio:.2f}: +{pts:.0f}")

    # 3. 底部位置（18%最优）
    pts = max(0.0, 25.0 - abs(s.from_60d_low_pct - 18.0))
    score += pts
    reasons.append(f"距低点{s.from_60d_low_pct:.1f}%: +{pts:.0f}")

    # 4. 换手率（文少核心）
    pts = min(s.turnover_pct, 30.0) * 0.6
    score += pts
    if s.turnover_pct >= 10.0:
        score += 10.0
        reasons.append(f"换手{s.turnover_pct:.1f}%>=10%: +{pts:.0f}+10(核心加分)")
    else:
        reasons.append(f"换手{s.turnover_pct:.1f}%: +{pts:.0f}")
    # 换手龙加分
    if s.turnover_pct >= 15.0:
        score += 8.0
        reasons.append(f"换手龙{s.turnover_pct:.1f}%>=15%: +8")

    # 5. 连板高度
    pts = min(s.board_height, 4) * 7
    score += pts
    reasons.append(f"连板{s.board_height}: +{pts}")

    # 6. 成交额
    pts = min(s.amount_100m, 30.0) * 0.8
    score += pts

    # 底部主升浪确认加分
    if 10.0 <= s.from_60d_low_pct <= 30.0:
        score += 5.0
        reasons.append(f"主升浪确认(10-30%): +5")

    score = round(score, 2)
    reasons.append(f"= 总分{score}")
    return score, reasons


# ════════════════════════════════════════════════════════════════════════════
# 选股主入口
# ════════════════════════════════════════════════════════════════════════════

def pick_stocks(candidates: List[StockCandidate],
                zone: str, emotion: str) -> List[PickResult]:
    """
    选股入口
    Args:
        candidates: 候选股列表（从涨停池/板块龙头构建）
        zone: 当前市场区间
        emotion: 当前情绪周期
    Returns: 按评分排序的选股结果（限制数量）
    """
    if zone == "risk":
        logger.info("[选股] 风险区，不选股")
        return []

    fever_filter = (emotion == "沸腾")
    results: List[PickResult] = []

    for s in candidates:
        # 硬过滤
        passed, filter_reasons = apply_hard_filter(s)
        if not passed:
            continue

        # 沸腾期额外过滤
        if fever_filter and s.turnover_pct < 15.0:
            logger.debug(f"[选股] {s.code} 沸腾期换手{s.turnover_pct:.1f}%<15%，过滤")
            continue

        # 评分
        score, score_reasons = score_stock(s)
        s.score = score
        all_reasons = filter_reasons + score_reasons

        # 判断买点类型
        if s.is_limit_up == 1:
            pick_type = "分歧买点"
        elif s.turnover_pct >= 10.0:
            pick_type = "换手龙"
        else:
            pick_type = "双龙破晓"

        results.append(PickResult(
            stock=s, score=score,
            reasons=all_reasons, pick_type=pick_type))

    # 排序
    results.sort(key=lambda x: x.score, reverse=True)

    # 限制数量
    max_hold = ZONE_POSITION.get(zone, ZONE_POSITION["range"])["max_holdings"]
    selected = results[:max_hold]

    for r in selected:
        logger.info(f"[选股] {r.stock.code} {r.stock.name} "
                    f"[{r.pick_type}] 评分{r.score:.0f}")

    return selected
