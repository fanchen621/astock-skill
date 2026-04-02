"""
收盘报告 + 自动选股入池
═══════════════════════════════════════════════════════════════════════════
每日 15:35 执行：
  1. 获取完整市场数据
  2. 判断市场区间 + 情绪周期
  3. 自动选股（双龙破晓）
  4. 生成交易计划写入明日 WatchList
  5. 触发日度统计 + 进化检查
"""
from __future__ import annotations

import datetime
from typing import Dict

from loguru import logger

from feeds.market_snapshot import build_full_snapshot, build_market_env_data
from feeds.akshare_data import get_zt_pool, get_all_stocks_spot, compute_stock_features
from strategy.zone_classifier import classify_zone, get_zone_cn
from strategy.emotion_cycle import get_emotion_cycle, get_emotion_advice
from strategy.stock_picker import pick_stocks
from strategy.trade_rules import build_trade_plan
from strategy.position_manager import get_adjusted_position
from strategy.signal import StockCandidate
from config import ALLOWED_PREFIXES, DOUBLE_DRAGON_FILTER


def generate_closing_report() -> Dict:
    """
    生成收盘报告 + 自动选股
    Returns: {"message": str, "data": dict, "recommended_stocks": list}
    """
    logger.info("[收盘] 开始生成收盘报告...")

    # 1. 完整快照
    snapshot = build_full_snapshot()

    # 2. 市场区间判断
    env_data = build_market_env_data()
    zone, zone_notes = classify_zone(env_data)

    # 3. 情绪周期
    zt_count = env_data.get("limit_up_count", 0)
    emotion = get_emotion_cycle(zt_count)
    total_pos, single_pos = get_adjusted_position(zone, emotion)

    # 4. 自动选股
    candidates = _build_candidates_from_zt_pool()
    picks = pick_stocks(candidates, zone, emotion)

    # 5. 生成交易计划
    plans = []
    recommended = []
    for p in picks:
        plan = build_trade_plan(
            p.stock, zone, emotion,
            p.pick_type, p.score, p.reasons)
        plans.append(plan)
        recommended.append({
            "code": p.stock.code,
            "name": p.stock.name,
            "sector": p.stock.sector,
            "pick_type": p.pick_type,
            "score": p.score,
            "entry_price": plan.entry_price,
            "stop_price": plan.stop_price,
            "tp1_price": plan.tp1_price,
            "tp2_price": plan.tp2_price,
            "volume_ratio": p.stock.volume_ratio,
            "turnover_pct": p.stock.turnover_pct,
            "from_60d_low_pct": p.stock.from_60d_low_pct,
            "reason": " | ".join(p.reasons[-3:]),
        })

    # 6. 组装报告消息
    ladder = snapshot.get("zt_stats", {}).get("consecutive_ladder", {})
    breadth = snapshot.get("breadth", {})
    sectors = snapshot.get("sector_flow", [])[:5]

    msg = f"""## 收盘报告 {datetime.date.today().isoformat()}

### 市场区间: [{get_zone_cn(zone)}] | 情绪: [{emotion}]
{zone_notes[0] if zone_notes else ''}
{get_emotion_advice(emotion)}

**建议仓位**: 总仓{total_pos*100:.0f}% | 单票{single_pos*100:.0f}%

### 涨停统计
- 涨停 {zt_count} 家 | 最高连板 {env_data.get('highest_board',0)} 板
- 炸板率 {env_data.get('broken_rate',0):.0%}
- 涨跌比 {breadth.get('up_down_ratio',0):.1f} (涨{breadth.get('up_count',0)}/跌{breadth.get('down_count',0)})
- 主力净流入 {env_data.get('main_flow_100m',0):.1f} 亿

### 连板梯队
"""
    for days in sorted(ladder.keys(), reverse=True):
        names = [f"{s['name']}" for s in ladder[days][:5]]
        msg += f"- {days}板: {', '.join(names)}\n"

    if sectors:
        msg += "\n### 板块资金Top5\n"
        for s in sectors:
            msg += f"- {s['name']}: {s.get('change_pct',0):+.1f}%\n"

    if recommended:
        msg += f"\n### 明日候选 ({len(recommended)}只)\n"
        for r in recommended:
            msg += (f"- **{r['code']} {r['name']}** [{r['pick_type']}] "
                    f"评分{r['score']:.0f} | "
                    f"入场{r['entry_price']:.2f} 止损{r['stop_price']:.2f} "
                    f"TP1:{r['tp1_price']:.2f} TP2:{r['tp2_price']:.2f}\n")
    else:
        msg += "\n### 明日候选: 无（风险区/无符合条件标的）\n"

    msg += f"\n---\n*{emotion}期 | {get_zone_cn(zone)}区 | 仓位{total_pos*100:.0f}%*"

    logger.info(f"[收盘] 报告完成: {zone}/{emotion}, {len(recommended)}只候选")

    return {
        "message": msg,
        "data": {
            "zone": zone,
            "emotion": emotion,
            "total_position_pct": total_pos,
            "zt_count": zt_count,
            "highest_board": env_data.get("highest_board", 0),
            "broken_rate": env_data.get("broken_rate", 0),
            "up_down_ratio": breadth.get("up_down_ratio", 0),
            "main_flow_100m": env_data.get("main_flow_100m", 0),
            "recommended_stocks": recommended,
        },
        "recommended_stocks": recommended,
    }


def _build_candidates_from_zt_pool() -> list:
    """从涨停池构建选股候选列表"""
    zt_df = get_zt_pool()
    if zt_df.empty:
        return []

    spot_df = get_all_stocks_spot()
    candidates = []

    # 确定列名
    code_col = "代码" if "代码" in zt_df.columns else "code"
    name_col = "名称" if "名称" in zt_df.columns else "name"
    board_col = None
    for c in ["连板数", "连续涨停天数", "连板天数"]:
        if c in zt_df.columns:
            board_col = c
            break

    import time as _time

    # 先过滤出主板股票
    filtered_rows = []
    for _, row in zt_df.iterrows():
        code = str(row.get(code_col, ""))
        if any(code.startswith(p) for p in ALLOWED_PREFIXES):
            filtered_rows.append(row)

    # 批量获取历史特征（加间隔防断连，最多处理前20只）
    feature_cache = {}
    for i, row in enumerate(filtered_rows[:20]):
        code = str(row.get(code_col, ""))
        if i > 0 and i % 5 == 0:
            _time.sleep(2)  # 每5只休息2秒，防止连续请求被断开
        features = compute_stock_features(code)
        if features:
            feature_cache[code] = features

    logger.info(f"[选股] 历史特征获取: {len(feature_cache)}/{len(filtered_rows[:20])} 成功")

    for row in filtered_rows:
        code = str(row.get(code_col, ""))
        name = str(row.get(name_col, ""))
        board_height = int(row.get(board_col, 1) or 1) if board_col else 1

        # 从全市场行情获取补充数据
        spot_row = None
        if not spot_df.empty:
            match = spot_df[spot_df["code"] == code]
            if not match.empty:
                spot_row = match.iloc[0]

        close = float(spot_row["price"]) if spot_row is not None else 0
        change_pct = float(spot_row["change_pct"]) if spot_row is not None else 0
        vol_ratio = float(spot_row.get("volume_ratio", 0) or 0) if spot_row is not None else 0
        turnover = float(spot_row.get("turnover_pct", 0) or 0) if spot_row is not None else 0
        amount = float(spot_row.get("amount", 0) or 0) if spot_row is not None else 0
        amount_100m = amount / 1e8 if amount > 1e6 else amount

        # 历史特征（从缓存取，无则用 spot 数据估算 fallback）
        features = feature_cache.get(code)
        if features:
            breakout_20d = features.get("breakout_20d", 0)
            from_60d_low = features.get("from_60d_low_pct", 0)
            if vol_ratio == 0:
                vol_ratio = features.get("volume_ratio_5d", 0)
        else:
            # fallback：连板股本身就是突破，用涨幅估算底部距离
            breakout_20d = 1 if board_height >= 2 else 0
            from_60d_low = max(change_pct * board_height, 8.0) if board_height >= 1 else 0
            if vol_ratio == 0:
                vol_ratio = 2.0  # 涨停默认量比>=2

        # 判断一字板
        is_one_wall = 1 if (change_pct >= 9.8 and turnover < 3.0) else 0

        candidates.append(StockCandidate(
            code=code, name=name,
            sector="",
            close=close,
            change_pct=change_pct,
            volume_ratio=vol_ratio,
            turnover_pct=turnover,
            breakout_20d=breakout_20d,
            from_60d_low_pct=from_60d_low,
            board_height=board_height,
            is_limit_up=1,
            is_one_wall=is_one_wall,
            theme_rank=min(board_height, 3) if board_height >= 2 else 3,
            amount_100m=amount_100m,
        ))

    logger.info(f"[选股] 从涨停池构建 {len(candidates)} 个候选 (特征命中{len(feature_cache)})")
    return candidates
