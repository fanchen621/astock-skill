"""
APScheduler 定时调度
═══════════════════════════════════════════════════════════════════════════
与 OpenClaw Cron 系统双路兼容
"""
from __future__ import annotations

import asyncio
import datetime
import traceback
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler as AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
import pytz

from loguru import logger
from openclaw_adapter import (
    is_trading_day, is_trading_hours, push_message,
    broadcast_to_dashboard, update_state,
)
from models import get_session, DailyReport, MarketSnapshot, WatchList

TZ = pytz.timezone("Asia/Shanghai")
_scheduler: Optional[AsyncIOScheduler] = None  # type: ignore


# ════════════════════════════════════════════════════════════════════════════
# 重试包装
# ════════════════════════════════════════════════════════════════════════════

async def _with_retry(name: str, coro_fn, max_attempts: int = 3, delay: int = 30):
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"[调度] {name} 开始（第{attempt}/{max_attempts}次）")
            await coro_fn()
            logger.info(f"[调度] {name} 完成")
            return True
        except Exception as e:
            logger.error(f"[调度] {name} 第{attempt}次失败: {e}")
            if attempt < max_attempts:
                await asyncio.sleep(delay)
            else:
                push_message(
                    f"**定时任务失败** {name}\n- 错误: `{str(e)[:200]}`\n- 已重试{attempt}次",
                    title="DragonFlow 告警", urgent=True)
    return False


# ════════════════════════════════════════════════════════════════════════════
# 任务函数
# ════════════════════════════════════════════════════════════════════════════

async def run_morning_briefing():
    if not is_trading_day():
        return

    async def _do():
        from reports.morning import generate_morning_report
        from engine.monitor import run_daily_open
        loop = asyncio.get_event_loop()

        # 开盘前初始化
        await loop.run_in_executor(None, run_daily_open)

        report = await loop.run_in_executor(None, generate_morning_report)
        push_message(report["message"], title="早盘速递")
        await broadcast_to_dashboard("morning_update", report["data"])
        _save_report("morning", report)
        update_state(last_morning=datetime.datetime.now().strftime("%H:%M"))

    await _with_retry("早盘速递", _do)


async def run_midday_analysis():
    if not is_trading_day():
        return

    async def _do():
        from reports.midday import generate_midday_report
        loop = asyncio.get_event_loop()
        report = await loop.run_in_executor(None, generate_midday_report)
        push_message(report["message"], title="午盘分析")
        await broadcast_to_dashboard("midday_update", report["data"])
        _save_report("midday", report)
        update_state(last_midday=datetime.datetime.now().strftime("%H:%M"))

    await _with_retry("午盘分析", _do)


async def run_closing_report():
    if not is_trading_day():
        return

    async def _do():
        from reports.closing import generate_closing_report
        from evolution.statistics import compute_daily_stats, compute_weekly_stats
        from evolution.optimizer import run_evolution_check
        loop = asyncio.get_event_loop()

        report = await loop.run_in_executor(None, generate_closing_report)
        push_message(report["message"], title="收盘报告")
        await broadcast_to_dashboard("closing_update", report["data"])
        _save_report("closing", report)

        # 推荐股票入池
        if report.get("recommended_stocks"):
            _save_watchlist(report["recommended_stocks"])

        # 统计 + 进化
        await loop.run_in_executor(None, compute_daily_stats)
        await loop.run_in_executor(None, compute_weekly_stats)
        await loop.run_in_executor(None, run_evolution_check)

        update_state(last_closing=datetime.datetime.now().strftime("%H:%M"))

    await _with_retry("收盘报告", _do)


# ════════════════════════════════════════════════════════════════════════════
# 数据持久化
# ════════════════════════════════════════════════════════════════════════════

def _save_report(rtype: str, report: dict):
    session = get_session()
    try:
        data = report.get("data", {})
        rec = DailyReport(
            date=datetime.date.today(),
            report_type=rtype,
            summary=data,
            data_snapshot=data,
            zone=data.get("zone"),
            emotion=data.get("emotion"),
            zt_count=data.get("zt_count"),
            highest_board=data.get("highest_board"),
            recommended_stocks=data.get("recommended_stocks"),
            full_text=report.get("message", ""),
        )
        session.add(rec)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error(f"[DB] 报告存档失败: {e}")
    finally:
        session.close()


def _save_watchlist(stocks: list):
    session = get_session()
    try:
        from openclaw_adapter import next_trading_day
        tomorrow = next_trading_day()
        for s in stocks:
            session.add(WatchList(
                date=tomorrow,
                code=s.get("code", ""),
                name=s.get("name", ""),
                source="closing_report",
                sector=s.get("sector", ""),
                pick_type=s.get("pick_type", ""),
                score=s.get("score"),
                reason=s.get("reason", ""),
                entry_price=s.get("entry_price"),
                stop_price=s.get("stop_price"),
                tp1_price=s.get("tp1_price"),
                tp2_price=s.get("tp2_price"),
                volume_ratio=s.get("volume_ratio"),
                turnover_pct=s.get("turnover_pct"),
                from_60d_low_pct=s.get("from_60d_low_pct"),
            ))
        session.commit()
        logger.info(f"[候选池] 写入 {len(stocks)} 只 → {tomorrow}")
    except Exception as e:
        session.rollback()
        logger.error(f"[候选池] 写入失败: {e}")
    finally:
        session.close()


# ════════════════════════════════════════════════════════════════════════════
# 调度器生命周期
# ════════════════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════════════════
# 24h 知识爬虫 / 舆情 / 自愈 任务
# ════════════════════════════════════════════════════════════════════════════

async def run_knowledge_crawl():
    """每2小时知识爬取（24h不间断）"""
    async def _do():
        from intelligence.crawler import run_crawl_cycle
        from intelligence.knowledge_base import save_entries
        loop = asyncio.get_event_loop()
        is_night = datetime.datetime.now().hour < 8 or datetime.datetime.now().hour >= 18
        items = await loop.run_in_executor(None, run_crawl_cycle, is_night)
        await loop.run_in_executor(None, save_entries, items)
    await _with_retry("知识爬取", _do, max_attempts=2, delay=15)


async def run_nightly_learning():
    """凌晨2点深度学习"""
    async def _do():
        from intelligence.learner import run_nightly_learning
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, run_nightly_learning)
    await _with_retry("深度学习", _do, max_attempts=2, delay=30)


async def run_health_full_check():
    """每6小时全面健康检查"""
    try:
        from selfheal.doctor import auto_heal
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, auto_heal)
    except Exception as e:
        logger.warning(f"[自愈] 全面检查异常: {e}")


# ════════════════════════════════════════════════════════════════════════════
# 调度器生命周期
# ════════════════════════════════════════════════════════════════════════════

def create_scheduler():
    global _scheduler
    _scheduler = AsyncIOScheduler(timezone=TZ)

    # ── 交易日定时任务 ──
    _scheduler.add_job(
        run_morning_briefing,
        CronTrigger(hour=8, minute=50, day_of_week="mon-fri", timezone=TZ),
        id="morning", name="早盘速递",
        max_instances=1, coalesce=True, misfire_grace_time=300)

    _scheduler.add_job(
        run_midday_analysis,
        CronTrigger(hour=11, minute=35, day_of_week="mon-fri", timezone=TZ),
        id="midday", name="午盘分析",
        max_instances=1, coalesce=True, misfire_grace_time=300)

    _scheduler.add_job(
        run_closing_report,
        CronTrigger(hour=15, minute=35, day_of_week="mon-fri", timezone=TZ),
        id="closing", name="收盘报告",
        max_instances=1, coalesce=True, misfire_grace_time=300)

    # ── 24h不间断任务 ──
    _scheduler.add_job(
        run_knowledge_crawl,
        CronTrigger(minute=17, hour="*/2", timezone=TZ),  # 每2小时
        id="knowledge", name="知识爬取",
        max_instances=1, coalesce=True, misfire_grace_time=600)

    _scheduler.add_job(
        run_nightly_learning,
        CronTrigger(hour=2, minute=0, timezone=TZ),  # 凌晨2点
        id="nightly_learn", name="深度学习",
        max_instances=1, coalesce=True, misfire_grace_time=3600)

    _scheduler.add_job(
        run_health_full_check,
        CronTrigger(minute=0, hour="*/6", timezone=TZ),  # 每6小时
        id="health_check", name="系统健康全检",
        max_instances=1, coalesce=True, misfire_grace_time=600)

    return _scheduler


def start_scheduler():
    global _scheduler
    if _scheduler is None:
        _scheduler = create_scheduler()
    if not _scheduler.running:
        _scheduler.start()
        update_state(scheduler_running=True)
        logger.info("[调度] APScheduler 已启动")
        logger.info("[调度] 08:50早盘 | 11:35午盘 | 15:35收盘 | 2h知识 | 02:00深度学习 | 6h健康")
    return _scheduler


def stop_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        update_state(scheduler_running=False)
        logger.info("[调度] APScheduler 已停止")
