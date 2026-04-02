"""
DragonFlow 主入口
═══════════════════════════════════════════════════════════════════════════
龙空龙全自动量化交易系统

启动命令：
  python main.py              # 完整启动（调度+AutoPilot+Web）
  python main.py --web-only   # 仅启动Web Dashboard
  python main.py --no-web     # 无Web，仅调度+AutoPilot
"""
from __future__ import annotations

import sys
import asyncio
import datetime
from pathlib import Path

# 确保项目根目录在 sys.path
ROOT = Path(__file__).parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from loguru import logger


def main():
    import argparse
    parser = argparse.ArgumentParser(description="DragonFlow - 龙空龙全自动量化交易系统")
    parser.add_argument("--web-only", action="store_true", help="仅启动Web")
    parser.add_argument("--no-web", action="store_true", help="不启动Web")
    parser.add_argument("--port", type=int, default=0, help="指定Web端口")
    parser.add_argument("--backtest", action="store_true", help="运行回测")
    parser.add_argument("--report", choices=["morning", "midday", "closing"], help="手动触发报告")
    parser.add_argument("--build", action="store_true", help="打包Skill zip")
    parser.add_argument("--health", action="store_true", help="运行健康检查")
    args = parser.parse_args()

    if args.build:
        from build_skill import build
        build()
        return

    # 初始化数据库
    from models import init_db
    init_db()
    logger.info("[DragonFlow] 数据库初始化完成")

    if args.health:
        from selfheal.diagnostics import run_health_check
        import json
        report = run_health_check()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    if args.backtest:
        _run_backtest()
        return

    if args.report:
        _run_report(args.report)
        return

    # 正常启动
    _startup(
        enable_web=not args.no_web,
        web_only=args.web_only,
        port=args.port,
    )


def _startup(enable_web: bool = True, web_only: bool = False, port: int = 0):
    """完整启动"""
    from openclaw_adapter import (
        find_available_port, set_active_port, open_webview,
        update_state, format_status_message, push_message,
    )

    update_state(started_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    if enable_web:
        if port <= 0:
            port = find_available_port()
        set_active_port(port)
        update_state(web_running=True)

    if not web_only:
        # 启动调度器
        from scheduler import start_scheduler
        start_scheduler()

        # 启动 AutoPilot
        from engine.autopilot import get_runtime
        runtime = get_runtime(ROOT)
        runtime.start()

    # 启动消息
    push_message(format_status_message(), title="DragonFlow 已启动")

    if enable_web:
        # 启动 FastAPI
        import uvicorn
        from api.server import app

        logger.info(f"[Web] Dashboard: http://127.0.0.1:{port}")
        open_webview(f"http://127.0.0.1:{port}")

        uvicorn.run(app, host="0.0.0.0", port=port,
                    log_level="warning", access_log=False)
    else:
        # 无Web模式，保持运行
        logger.info("[DragonFlow] 无Web模式运行中（Ctrl+C 退出）")
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_forever()
        except KeyboardInterrupt:
            logger.info("[DragonFlow] 收到退出信号")
        finally:
            from scheduler import stop_scheduler
            from engine.autopilot import get_runtime
            stop_scheduler()
            get_runtime().stop()


def _run_backtest():
    """运行回测"""
    from evolution.backtest import run_backtest
    logger.info("[回测] 从 data/manual/backtest_signals.csv 加载信号...")

    import csv
    csv_path = ROOT / "data" / "manual" / "backtest_signals.csv"
    if not csv_path.exists():
        logger.error(f"[回测] 文件不存在: {csv_path}")
        logger.info("[回测] 请创建 CSV 文件，列: date,code,name,entry_price,low_3d,high_3d,close_d3")
        return

    signals = []
    with csv_path.open("r", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            signals.append(row)

    result = run_backtest(signals)

    print(f"\n{'='*60}")
    print(f"  DragonFlow 回测结果")
    print(f"{'='*60}")
    print(f"  总交易:   {result.total_trades} 笔")
    print(f"  胜率:     {result.win_rate*100:.1f}%")
    print(f"  盈亏比:   {result.profit_ratio:.2f}")
    print(f"  总盈亏:   {result.total_pnl:+.0f} 元")
    print(f"  最大回撤: {result.max_drawdown_pct:.1f}%")
    print(f"  达标:     {'PASS' if result.passed else 'FAIL'}")
    if result.fail_reasons:
        for r in result.fail_reasons:
            print(f"    - {r}")
    print(f"{'='*60}\n")


def _run_report(report_type: str):
    """手动触发报告"""
    if report_type == "morning":
        from reports.morning import generate_morning_report
        report = generate_morning_report()
    elif report_type == "midday":
        from reports.midday import generate_midday_report
        report = generate_midday_report()
    else:
        from reports.closing import generate_closing_report
        report = generate_closing_report()

    from openclaw_adapter import push_message
    push_message(report["message"], title=f"DragonFlow {report_type}")


if __name__ == "__main__":
    main()
