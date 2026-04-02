"""
OpenClaw Skill 入口
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from models import init_db
from openclaw_adapter import push_message, format_status_message, open_webview


def on_install():
    """Skill 安装时初始化数据库"""
    init_db()
    push_message("DragonFlow 安装完成，数据库已初始化。\n\n"
                 "使用 `/dragonflow` 启动系统",
                 title="DragonFlow")


def on_command(command: str, args: str = ""):
    """处理 Skill 命令"""
    if command == "dragonflow":
        from main import _startup
        _startup(enable_web=True)

    elif command == "dragonflow_status":
        push_message(format_status_message(), title="DragonFlow 状态")

    elif command == "dragonflow_dashboard":
        from openclaw_adapter import get_dashboard_url
        open_webview(get_dashboard_url())

    elif command == "dragonflow_report":
        report_type = args.strip() or "closing"
        from main import _run_report
        _run_report(report_type)

    elif command == "dragonflow_backtest":
        from main import _run_backtest
        _run_backtest()


def on_schedule(command: str):
    """处理定时任务（由 OpenClaw Cron 触发）"""
    import asyncio
    loop = asyncio.new_event_loop()

    if command == "morning_briefing":
        from scheduler import run_morning_briefing
        loop.run_until_complete(run_morning_briefing())

    elif command == "midday_analysis":
        from scheduler import run_midday_analysis
        loop.run_until_complete(run_midday_analysis())

    elif command == "closing_report":
        from scheduler import run_closing_report
        loop.run_until_complete(run_closing_report())

    elif command == "knowledge_crawl":
        from scheduler import run_knowledge_crawl
        loop.run_until_complete(run_knowledge_crawl())

    elif command == "nightly_learning":
        from scheduler import run_nightly_learning
        loop.run_until_complete(run_nightly_learning())

    elif command == "health_check":
        from scheduler import run_health_full_check
        loop.run_until_complete(run_health_full_check())

    loop.close()
