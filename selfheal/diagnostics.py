"""
系统健康监控 + 异常模式检测
═══════════════════════════════════════════════════════════════════════════
监控维度：数据新鲜度/API错误率/tick延迟/数据库/内存/调度器
异常检测：连续错误模式/格式变化/缓存泄漏
"""
from __future__ import annotations

import datetime
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger

from config import SELFHEAL_CONFIG
from openclaw_adapter import get_data_dir

# ─── 滑动窗口错误追踪 ──────────────────────────────────────────────────────

_error_window: List[Dict] = []  # [{ts, source, error, type}]
_WINDOW_SIZE = 300  # 5分钟窗口

_last_data_ts: Dict[str, float] = {}  # 各数据源最后成功时间
_tick_latencies: List[float] = []     # 最近100个tick延迟


def record_data_success(source: str):
    _last_data_ts[source] = time.time()


def record_data_error(source: str, error: str, error_type: str = "unknown"):
    now = time.time()
    _error_window.append({"ts": now, "source": source, "error": error, "type": error_type})
    # 清理过期
    cutoff = now - _WINDOW_SIZE
    while _error_window and _error_window[0]["ts"] < cutoff:
        _error_window.pop(0)


def record_tick_latency(ms: float):
    _tick_latencies.append(ms)
    if len(_tick_latencies) > 100:
        _tick_latencies.pop(0)


# ════════════════════════════════════════════════════════════════════════════
# 健康检查
# ════════════════════════════════════════════════════════════════════════════

def check_data_freshness() -> Dict:
    """检查数据新鲜度"""
    now = time.time()
    issues = []
    status = "ok"

    for source, last_ts in _last_data_ts.items():
        age = now - last_ts
        if age > SELFHEAL_CONFIG["data_freshness_critical"]:
            issues.append(f"{source}: {age:.0f}秒未更新(严重)")
            status = "critical"
        elif age > SELFHEAL_CONFIG["data_freshness_warn"]:
            issues.append(f"{source}: {age:.0f}秒未更新(警告)")
            if status != "critical":
                status = "warn"

    return {"check": "data_freshness", "status": status,
            "issues": issues, "sources": dict(_last_data_ts)}


def check_api_error_rate() -> Dict:
    """检查各数据源错误率"""
    now = time.time()
    cutoff = now - _WINDOW_SIZE
    recent = [e for e in _error_window if e["ts"] >= cutoff]

    source_errors: Dict[str, int] = {}
    source_total: Dict[str, int] = {}
    for e in recent:
        src = e["source"]
        source_errors[src] = source_errors.get(src, 0) + 1

    # 需要结合成功次数计算真实错误率
    # 简化：只看错误绝对数量
    status = "ok"
    issues = []
    for src, count in source_errors.items():
        rate = count / max(len(recent), 1)
        if rate > SELFHEAL_CONFIG["error_rate_critical"]:
            issues.append(f"{src}: 错误{count}次 严重")
            status = "critical"
        elif rate > SELFHEAL_CONFIG["error_rate_warn"]:
            issues.append(f"{src}: 错误{count}次 警告")
            if status != "critical":
                status = "warn"

    # 检测连续相同错误（系统性问题）
    if len(recent) >= 5:
        last5_types = [e["type"] for e in recent[-5:]]
        if len(set(last5_types)) == 1 and last5_types[0] != "unknown":
            issues.append(f"检测到连续相同错误: {last5_types[0]}")
            status = "critical"

    return {"check": "api_error_rate", "status": status,
            "issues": issues, "error_count": len(recent)}


def check_tick_latency() -> Dict:
    """检查tick延迟"""
    if not _tick_latencies:
        return {"check": "tick_latency", "status": "ok", "issues": [], "avg_ms": 0}

    avg = sum(_tick_latencies) / len(_tick_latencies)
    max_lat = max(_tick_latencies)
    status = "ok"
    issues = []

    if max_lat > SELFHEAL_CONFIG["tick_latency_critical"]:
        status = "critical"
        issues.append(f"最大延迟{max_lat:.0f}ms")
    elif avg > SELFHEAL_CONFIG["tick_latency_warn"]:
        status = "warn"
        issues.append(f"平均延迟{avg:.0f}ms偏高")

    return {"check": "tick_latency", "status": status,
            "issues": issues, "avg_ms": round(avg, 1), "max_ms": round(max_lat, 1)}


def check_db_health() -> Dict:
    """检查数据库健康"""
    db_path = get_data_dir() / "dragonflow.db"
    status = "ok"
    issues = []

    if db_path.exists():
        size_mb = db_path.stat().st_size / 1024 / 1024
        if size_mb > SELFHEAL_CONFIG["db_size_warn_mb"]:
            status = "warn"
            issues.append(f"数据库{size_mb:.1f}MB过大")
    else:
        status = "warn"
        issues.append("数据库文件不存在")

    # 测试连接
    try:
        from models import get_session
        from sqlalchemy import text
        session = get_session()
        session.execute(text("SELECT 1"))
        session.close()
    except Exception as e:
        status = "critical"
        issues.append(f"数据库连接失败: {e}")

    return {"check": "db_health", "status": status, "issues": issues}


def check_memory() -> Dict:
    """检查内存占用"""
    status = "ok"
    issues = []
    try:
        import os
        # 粗略估算进程内存（跨平台）
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            # 简化：只检查缓存大小
            pass
        mem_mb = sys.getsizeof(object) * 0  # placeholder
    except Exception:
        pass

    # 检查缓存大小
    from feeds.resilient import _source_health
    from feeds.sentiment import _sentiment_cache
    cache_items = len(_sentiment_cache) + len(_source_health)
    if cache_items > 1000:
        status = "warn"
        issues.append(f"缓存条目{cache_items}过多")

    return {"check": "memory", "status": status, "issues": issues}


# ════════════════════════════════════════════════════════════════════════════
# 综合健康报告
# ════════════════════════════════════════════════════════════════════════════

def run_health_check() -> Dict:
    """执行完整健康检查"""
    checks = [
        check_data_freshness(),
        check_api_error_rate(),
        check_tick_latency(),
        check_db_health(),
        check_memory(),
    ]

    overall = "ok"
    critical_count = 0
    warn_count = 0

    for c in checks:
        if c["status"] == "critical":
            critical_count += 1
            overall = "critical"
        elif c["status"] == "warn":
            warn_count += 1
            if overall != "critical":
                overall = "warn"

    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "overall": overall,
        "critical_count": critical_count,
        "warn_count": warn_count,
        "checks": checks,
    }

    if overall != "ok":
        logger.warning(f"[自愈] 健康检查: {overall} (严重{critical_count} 警告{warn_count})")

    return report


def save_health_log(report: Dict, repair_action: str = "", repair_result: str = ""):
    """保存健康日志到数据库"""
    from models import get_session, SystemHealthLog
    session = get_session()
    try:
        from feeds.resilient import get_all_source_status
        log = SystemHealthLog(
            check_type="full_check",
            status=report.get("overall", "unknown"),
            detail=json.dumps(report.get("checks", []), ensure_ascii=False, default=str)[:2000],
            repair_action=repair_action,
            repair_result=repair_result,
            data_source_status=get_all_source_status(),
        )
        session.add(log)
        session.commit()
    except Exception as e:
        session.rollback()
        logger.debug(f"[自愈] 健康日志保存失败: {e}")
    finally:
        session.close()


import json
