"""
自动修复执行器
═══════════════════════════════════════════════════════════════════════════
根据diagnostics健康报告自动执行修复动作：
  - 数据源切换
  - 缓存清理
  - 限流退避
  - 调度器重启
  - 数据库清理
"""
from __future__ import annotations

import datetime
import time
from typing import Dict, List

from loguru import logger

from config import SELFHEAL_CONFIG
from selfheal.diagnostics import run_health_check, save_health_log
from openclaw_adapter import push_message

_repair_count_window: List[float] = []  # 修复时间戳（防循环）


def _can_repair() -> bool:
    now = time.time()
    cutoff = now - 3600
    _repair_count_window[:] = [t for t in _repair_count_window if t > cutoff]
    return len(_repair_count_window) < SELFHEAL_CONFIG["max_repairs_per_hour"]


def _record_repair():
    _repair_count_window.append(time.time())


# ════════════════════════════════════════════════════════════════════════════
# 修复动作
# ════════════════════════════════════════════════════════════════════════════

def repair_data_source_switch() -> str:
    """切换数据源"""
    try:
        from feeds.resilient import reset_all_sources
        reset_all_sources()
        logger.info("[自愈] 已重置所有数据源健康状态")
        return "success"
    except Exception as e:
        logger.error(f"[自愈] 数据源重置失败: {e}")
        return "failed"


def repair_cache_clear() -> str:
    """清理过期缓存"""
    try:
        from feeds.akshare_data import clear_cache as ak_clear
        from feeds.sentiment import _sentiment_cache
        ak_clear()
        _sentiment_cache.clear()
        logger.info("[自愈] 缓存已清理")
        return "success"
    except Exception as e:
        logger.error(f"[自愈] 缓存清理失败: {e}")
        return "failed"


def repair_db_vacuum() -> str:
    """数据库清理+压缩"""
    try:
        from models import get_engine
        from sqlalchemy import text
        engine = get_engine()
        with engine.connect() as conn:
            # 清理90天前的快照
            conn.execute(text(
                "DELETE FROM market_snapshots WHERE date < date('now', '-90 days')"))
            conn.execute(text(
                "DELETE FROM sentiment_snapshots WHERE date < date('now', '-60 days')"))
            conn.execute(text(
                "DELETE FROM system_health_logs WHERE timestamp < datetime('now', '-30 days')"))
            conn.commit()
            conn.execute(text("VACUUM"))
        logger.info("[自愈] 数据库已清理压缩")
        return "success"
    except Exception as e:
        logger.error(f"[自愈] 数据库清理失败: {e}")
        return "failed"


def repair_scheduler_restart() -> str:
    """重启调度器"""
    try:
        from scheduler import stop_scheduler, start_scheduler
        stop_scheduler()
        time.sleep(1)
        start_scheduler()
        logger.info("[自愈] 调度器已重启")
        return "success"
    except Exception as e:
        logger.error(f"[自愈] 调度器重启失败: {e}")
        return "failed"


# ════════════════════════════════════════════════════════════════════════════
# 自动诊断+修复主入口
# ════════════════════════════════════════════════════════════════════════════

def auto_heal() -> Dict:
    """
    执行自动诊断和修复
    Returns: {report, repairs: [{action, result}]}
    """
    if not SELFHEAL_CONFIG["auto_repair"]:
        return {"report": run_health_check(), "repairs": []}

    report = run_health_check()
    repairs = []

    if report["overall"] == "ok":
        return {"report": report, "repairs": []}

    if not _can_repair():
        logger.warning("[自愈] 本小时修复次数已达上限，跳过")
        return {"report": report, "repairs": [{"action": "skipped", "result": "rate_limited"}]}

    # 根据检查结果决定修复动作
    for check in report["checks"]:
        if check["status"] != "critical":
            continue

        name = check["check"]
        action = ""
        result = ""

        if name == "data_freshness" or name == "api_error_rate":
            action = "data_source_switch"
            result = repair_data_source_switch()
            # 同时清缓存
            repair_cache_clear()

        elif name == "tick_latency":
            action = "cache_clear"
            result = repair_cache_clear()

        elif name == "db_health":
            action = "db_vacuum"
            result = repair_db_vacuum()

        elif name == "memory":
            action = "cache_clear"
            result = repair_cache_clear()

        if action:
            _record_repair()
            repairs.append({"action": action, "result": result, "check": name})
            save_health_log(report, action, result)

    # 通知用户
    if repairs:
        repair_msg = "\n".join(
            f"- {r['check']}: {r['action']} -> {r['result']}" for r in repairs)
        push_message(
            f"**系统自愈执行**\n\n状态: {report['overall']}\n{repair_msg}",
            title="DragonFlow 自愈", urgent=(report["overall"] == "critical"))

    return {"report": report, "repairs": repairs}
