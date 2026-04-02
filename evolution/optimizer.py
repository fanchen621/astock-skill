"""
策略进化引擎
═══════════════════════════════════════════════════════════════════════════
融合 astock_skill 进化框架 + Codex PLAYBOOK 防过拟合规则：
  - 每日收盘后自动执行
  - 正向进化（表现好） → 适度放宽
  - 负向进化（表现差） → 收紧防守
  - 每周最多调1个核心参数
  - 连续2周验证后才固化
  - 月回撤>8% → 暂停实盘
  - 总回撤>15% → 强制停手
"""
from __future__ import annotations

import datetime
from typing import Optional

from loguru import logger

from config import EVOLUTION_THRESHOLDS, EVOLUTION_BOUNDS, RISK_CONTROL
from models import (
    get_session, StrategyStats, EvolutionLog, RiskEvent,
    get_param, set_param,
)


def run_evolution_check(auto_adjust: bool = False):
    """
    策略进化检查
    Args:
        auto_adjust: True时自动写入参数，False只记录建议
    """
    logger.info("[进化] 开始进化检查...")
    session = get_session()
    try:
        # 获取近N天统计
        lookback = EVOLUTION_THRESHOLDS["lookback_days"]
        since = datetime.date.today() - datetime.timedelta(days=lookback * 2)

        day_stats = session.query(StrategyStats).filter(
            StrategyStats.period_type == "day",
            StrategyStats.period_start >= since,
        ).order_by(StrategyStats.period_start.desc()).limit(lookback).all()

        if len(day_stats) < 3:
            logger.info(f"[进化] 数据不足({len(day_stats)}天)，跳过")
            return

        # 计算近期指标
        win_rates = [s.win_rate or 0 for s in day_stats]
        pnl_ratios = [s.profit_ratio or 0 for s in day_stats]
        pnls = [s.total_pnl or 0 for s in day_stats]
        dds = [s.max_drawdown or 0 for s in day_stats]

        avg_wr = sum(win_rates) / len(win_rates)
        avg_pr = sum(pnl_ratios) / len(pnl_ratios)
        total_pnl = sum(pnls)
        max_dd = max(dds) if dds else 0

        # 连续亏损天数
        consec_loss = 0
        for s in day_stats:
            if (s.total_pnl or 0) < 0:
                consec_loss += 1
            else:
                break

        logger.info(f"[进化] 近{len(day_stats)}日: 胜率{avg_wr*100:.0f}% "
                    f"盈亏比{avg_pr:.1f} PnL={total_pnl:+.0f} "
                    f"最大回撤{max_dd:.1f}% 连亏{consec_loss}天")

        # ── 进化判断 ────────────────────────────────────────────────
        actions = []
        insights = []
        T = EVOLUTION_THRESHOLDS

        # 1. 风控铁律检查（不可被进化覆盖）
        if max_dd >= RISK_CONTROL["total_max_drawdown_pct"]:
            insights.append(f"!! 总回撤{max_dd:.1f}%>={RISK_CONTROL['total_max_drawdown_pct']}%，"
                            f"系统强制停手！重新回测后再开仓")
            _log_risk_event(session, "force_stop",
                            f"总回撤{max_dd:.1f}%触发强制停手",
                            "系统停止交易，需手动重置")

        elif max_dd >= RISK_CONTROL["month_max_drawdown_pct"]:
            insights.append(f"! 月回撤{max_dd:.1f}%>={RISK_CONTROL['month_max_drawdown_pct']}%，"
                            f"暂停实盘，仅模拟运行")
            _log_risk_event(session, "drawdown_warn",
                            f"月回撤{max_dd:.1f}%触发暂停",
                            "切换为模拟模式")

        # 2. 胜率评估
        if avg_wr >= T["win_rate_excellent"]:
            insights.append(f"V 胜率{avg_wr*100:.0f}%优秀(>={T['win_rate_excellent']*100:.0f}%)")
        elif avg_wr <= T["win_rate_poor"]:
            insights.append(f"X 胜率{avg_wr*100:.0f}%差(<={T['win_rate_poor']*100:.0f}%)")
            actions.append({
                "param": "min_buy_volume_ratio", "direction": "+", "delta": 0.2,
                "reason": f"胜率差，提高量比门槛"})

        # 3. 盈亏比评估
        if avg_pr >= T["profit_ratio_excellent"]:
            insights.append(f"V 盈亏比{avg_pr:.1f}优秀(>={T['profit_ratio_excellent']})")
        elif avg_pr <= T["profit_ratio_poor"]:
            insights.append(f"X 盈亏比{avg_pr:.1f}差(<={T['profit_ratio_poor']})")
            actions.append({
                "param": "take_profit_1_pct", "direction": "+", "delta": 0.005,
                "reason": "盈亏比差，扩大止盈1空间"})

        # 4. 连续亏损
        if consec_loss >= 3:
            insights.append(f"X 连续亏损{consec_loss}天，进入防御模式")
            actions.append({
                "param": "max_chase_pct", "direction": "-", "delta": 0.5,
                "reason": f"连亏{consec_loss}天，收紧追高上限"})

        # 5. 回撤预警
        if max_dd >= T["max_dd_warn"] and max_dd < T["max_dd_danger"]:
            insights.append(f"! 回撤{max_dd:.1f}%接近警戒线")

        if not insights:
            insights.append("策略运行平稳，继续观察")

        # ── 执行调参（受防过拟合限制） ────────────────────────────────
        applied = []
        if auto_adjust and actions:
            # 每周最多调1个参数
            week_changes = _count_week_changes(session)
            max_changes = T.get("max_param_changes_per_week", 1)

            for act in actions[:max(0, max_changes - week_changes)]:
                try:
                    current = float(get_param(act["param"]) or 0)
                    if act["direction"] == "+":
                        new_val = current + act["delta"]
                    else:
                        new_val = current - act["delta"]

                    if act["param"] in EVOLUTION_BOUNDS:
                        lo, hi = EVOLUTION_BOUNDS[act["param"]]
                        new_val = max(lo, min(hi, new_val))

                    new_val = round(new_val, 4)
                    if abs(new_val - current) > 1e-6:
                        set_param(act["param"], str(new_val), updated_by="evolution")
                        applied.append({
                            "param": act["param"],
                            "old": current, "new": new_val,
                            "reason": act["reason"]})
                        logger.info(f"[进化] {act['param']}: {current} -> {new_val}")
                except Exception as e:
                    logger.error(f"[进化] 调参失败 {act['param']}: {e}")

        # ── 记录进化日志 ────────────────────────────────────────────
        log = EvolutionLog(
            trigger_reason=f"daily | 连亏{consec_loss}" if consec_loss else "daily",
            analysis="\n".join(insights),
            old_params={a["param"]: a["old"] for a in applied},
            new_params={a["param"]: a["new"] for a in applied},
            performance_before={
                "avg_win_rate": avg_wr, "avg_profit_ratio": avg_pr,
                "total_pnl": total_pnl, "max_drawdown": max_dd,
                "consecutive_loss_days": consec_loss,
            },
            approved=bool(applied),
        )
        session.add(log)
        session.commit()

        # ── 知识驱动进化（补充统计驱动） ──────────────────────────
        try:
            from intelligence.learner import generate_learning_insights
            knowledge_insights = generate_learning_insights()
            for ki in knowledge_insights:
                insights.append(f"[知识] {ki.get('suggestion', '')}")
        except Exception as e:
            logger.debug(f"[进化] 知识驱动跳过: {e}")

        logger.info(f"[进化] 完成: {len(insights)}条洞察, "
                    f"{'调整'+str(len(applied))+'参数' if applied else '仅建议'}")

        return {"insights": insights, "applied": applied}

    except Exception as e:
        session.rollback()
        logger.error(f"[进化] 检查失败: {e}")
    finally:
        session.close()


def _count_week_changes(session) -> int:
    """统计本周已有多少次自动调参"""
    today = datetime.date.today()
    week_start = today - datetime.timedelta(days=today.weekday())
    logs = session.query(EvolutionLog).filter(
        EvolutionLog.created_at >= datetime.datetime.combine(week_start, datetime.time.min),
        EvolutionLog.approved == True,
    ).all()
    return sum(1 for l in logs if l.new_params)


def _log_risk_event(session, event_type: str, desc: str, penalty: str):
    """记录风控事件"""
    session.add(RiskEvent(
        date=datetime.date.today(),
        event_type=event_type,
        description=desc,
        penalty=penalty,
    ))


def get_evolution_history(limit: int = 30) -> list:
    session = get_session()
    try:
        logs = session.query(EvolutionLog).order_by(
            EvolutionLog.created_at.desc()).limit(limit).all()
        return [{
            "date": str(l.created_at.date()) if l.created_at else "",
            "trigger": l.trigger_reason or "",
            "insights": l.analysis or "",
            "changes": l.new_params or {},
            "performance": l.performance_before or {},
            "auto_adjusted": l.approved,
        } for l in logs]
    finally:
        session.close()
