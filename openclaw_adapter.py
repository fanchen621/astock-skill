"""
OpenClaw 适配层
═══════════════════════════════════════════════════════════════════════════
所有与 OpenClaw 平台交互的调用（消息推送、Webview、日志、数据目录）
统一封装，上层业务代码零感知差异。

运行模式：
  - OpenClaw 环境 → 调用真实 OpenClaw API
  - 独立环境（开发/测试） → Rich 终端输出 + 文件日志
"""
from __future__ import annotations

import os
import sys
import json
import socket
import asyncio
import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

# ─── 运行时检测 ──────────────────────────────────────────────────────────────
_IN_OPENCLAW = os.environ.get("OPENCLAW_SKILL_ID") is not None
_SKILL_ROOT = Path(__file__).parent
_DATA_DIR = _SKILL_ROOT / "data"
_REPORT_DIR = _DATA_DIR / "reports"
_LOG_DIR = _DATA_DIR / "logs"

for _d in (_DATA_DIR, _REPORT_DIR, _LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

_console = Console()

# ─── WebSocket 广播注册 ────────────────────────────────────────────────────
_ws_broadcast: Optional[Callable] = None


def register_ws_broadcast(fn: Callable):
    global _ws_broadcast
    _ws_broadcast = fn


# ════════════════════════════════════════════════════════════════════════════
# 日志系统
# ════════════════════════════════════════════════════════════════════════════

def _setup_logging():
    log_file = _LOG_DIR / "dragonflow_{time:YYYY-MM-DD}.log"
    logger.remove()

    logger.add(sys.stderr, level="INFO",
               format="<green>{time:HH:mm:ss}</green> | "
                      "<level>{level: <8}</level> | "
                      "<cyan>{name}</cyan> - <level>{message}</level>",
               colorize=True)

    logger.add(str(log_file), level="DEBUG",
               rotation="1 day", retention="30 days", encoding="utf-8",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name} | {message}")

    if _IN_OPENCLAW:
        oc_log_endpoint = os.environ.get("OPENCLAW_LOG_ENDPOINT")
        if oc_log_endpoint:
            logger.add(_openclaw_log_sink, level="INFO", serialize=False)

    logger.info(f"[DragonFlow] 日志已初始化 | 目录: {_LOG_DIR}")


def _openclaw_log_sink(message):
    try:
        record = message.record
        payload = {
            "level": record["level"].name,
            "time": record["time"].isoformat(),
            "name": record["name"],
            "message": record["message"],
        }
        endpoint = os.environ.get("OPENCLAW_LOG_ENDPOINT", "")
        if endpoint and endpoint.startswith("http"):
            import requests
            requests.post(endpoint, json=payload, timeout=2)
    except Exception:
        pass


_setup_logging()


# ════════════════════════════════════════════════════════════════════════════
# 消息推送
# ════════════════════════════════════════════════════════════════════════════

def push_message(content: str, msg_type: str = "markdown",
                 title: str = "", urgent: bool = False) -> bool:
    if _IN_OPENCLAW:
        return _push_openclaw(content, msg_type, title, urgent)
    return _push_terminal(content, title, urgent)


def _push_openclaw(content: str, msg_type: str,
                   title: str, urgent: bool) -> bool:
    try:
        endpoint = os.environ.get("OPENCLAW_MSG_ENDPOINT", "")
        if not endpoint:
            return _push_terminal(content, title, urgent)
        import requests
        resp = requests.post(endpoint, json={
            "type": msg_type,
            "title": title or "DragonFlow",
            "content": content,
            "urgent": urgent,
            "skill_id": os.environ.get("OPENCLAW_SKILL_ID", "dragonflow"),
            "timestamp": datetime.datetime.now().isoformat(),
        }, timeout=5)
        return resp.status_code == 200
    except Exception as e:
        logger.warning(f"[推送] OpenClaw推送失败: {e}，降级到终端")
        return _push_terminal(content, title, urgent)


def _push_terminal(content: str, title: str, urgent: bool) -> bool:
    border = "red bold" if urgent else "cyan"
    header = title or "DragonFlow"
    try:
        _console.print(Panel(Markdown(content), title=header, border_style=border))
    except Exception:
        print(f"\n{'='*60}\n{header}\n{content}\n{'='*60}")
    return True


async def push_message_async(content: str, msg_type: str = "markdown",
                              title: str = "", urgent: bool = False):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, push_message, content, msg_type, title, urgent)


# ════════════════════════════════════════════════════════════════════════════
# WebView 控制
# ════════════════════════════════════════════════════════════════════════════

def open_webview(url: str, title: str = "DragonFlow Dashboard",
                 width: int = 1200, height: int = 800) -> bool:
    if _IN_OPENCLAW:
        try:
            endpoint = os.environ.get("OPENCLAW_WEBVIEW_ENDPOINT", "")
            if endpoint:
                import requests
                resp = requests.post(endpoint, json={
                    "url": url, "title": title, "width": width, "height": height,
                }, timeout=5)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"[Webview] 打开失败: {e}")
            return False
    _console.print(Panel(
        f"[bold cyan]Dashboard URL:[/bold cyan]\n\n  [link={url}]{url}[/link]",
        title=f"  {title}", border_style="green"))
    return True


# ════════════════════════════════════════════════════════════════════════════
# 路径 & 目录
# ════════════════════════════════════════════════════════════════════════════

def get_data_dir() -> Path:
    if _IN_OPENCLAW:
        oc_data = os.environ.get("OPENCLAW_DATA_DIR")
        if oc_data:
            p = Path(oc_data)
            p.mkdir(parents=True, exist_ok=True)
            return p
    return _DATA_DIR


def get_reports_dir() -> Path:
    d = get_data_dir() / "reports"
    d.mkdir(exist_ok=True)
    return d


def get_skill_root() -> Path:
    return _SKILL_ROOT


def get_templates_dir() -> Path:
    return _SKILL_ROOT / "api" / "templates"


# ════════════════════════════════════════════════════════════════════════════
# 端口管理
# ════════════════════════════════════════════════════════════════════════════

_ACTIVE_PORT: Optional[int] = None


def find_available_port(start: int = 8888, end: int = 8999) -> int:
    global _ACTIVE_PORT
    if _ACTIVE_PORT and _is_port_free(_ACTIVE_PORT):
        return _ACTIVE_PORT
    for port in range(start, end + 1):
        if _is_port_free(port):
            _ACTIVE_PORT = port
            logger.info(f"[端口] 自动选定: {port}")
            return port
    raise RuntimeError(f"端口 {start}-{end} 全部被占用")


def _is_port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def get_dashboard_url() -> str:
    return f"http://127.0.0.1:{_ACTIVE_PORT or 8888}"


def set_active_port(port: int):
    global _ACTIVE_PORT
    _ACTIVE_PORT = port


# ════════════════════════════════════════════════════════════════════════════
# Dashboard 广播（WebSocket）
# ════════════════════════════════════════════════════════════════════════════

async def broadcast_to_dashboard(event: str, data: Any):
    if _ws_broadcast is not None:
        try:
            payload = {"event": event, "data": data,
                       "ts": datetime.datetime.now().isoformat()}
            await _ws_broadcast(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception as e:
            logger.warning(f"[WS广播] {event} 失败: {e}")


# ════════════════════════════════════════════════════════════════════════════
# 交易日判断
# ════════════════════════════════════════════════════════════════════════════

def is_trading_day(date: Optional[datetime.date] = None) -> bool:
    if date is None:
        date = datetime.date.today()
    try:
        import chinese_calendar as cc
        return cc.is_workday(date) and not cc.is_holiday(date)
    except ImportError:
        return date.weekday() < 5


def is_trading_hours(now: Optional[datetime.datetime] = None) -> bool:
    if now is None:
        now = datetime.datetime.now()
    t = now.time()
    return ((datetime.time(9, 30) <= t <= datetime.time(11, 30))
            or (datetime.time(13, 0) <= t <= datetime.time(15, 0)))


def is_pre_market(now: Optional[datetime.datetime] = None) -> bool:
    """竞价时段 9:15-9:30"""
    if now is None:
        now = datetime.datetime.now()
    t = now.time()
    return datetime.time(9, 15) <= t <= datetime.time(9, 30)


def next_trading_day(from_date: Optional[datetime.date] = None) -> datetime.date:
    if from_date is None:
        from_date = datetime.date.today()
    d = from_date + datetime.timedelta(days=1)
    while not is_trading_day(d):
        d += datetime.timedelta(days=1)
    return d


def get_next_schedule_info() -> dict:
    now = datetime.datetime.now()
    today = now.date()
    schedules = [
        ("早盘速递", datetime.time(8, 50)),
        ("午盘分析", datetime.time(11, 35)),
        ("收盘报告", datetime.time(15, 35)),
    ]
    for name, t in schedules:
        scheduled = datetime.datetime.combine(today, t)
        if scheduled > now and is_trading_day(today):
            return {"name": name, "time": scheduled.strftime("%H:%M"),
                    "date": today.isoformat()}
    nxt = next_trading_day(today)
    return {"name": "早盘速递", "time": "08:50", "date": nxt.isoformat()}


# ════════════════════════════════════════════════════════════════════════════
# 系统状态
# ════════════════════════════════════════════════════════════════════════════

_SYSTEM_STATE: dict = {
    "started_at": None,
    "scheduler_running": False,
    "autopilot_running": False,
    "web_running": False,
    "last_morning": None,
    "last_midday": None,
    "last_closing": None,
    "current_zone": "unknown",
    "current_emotion": "unknown",
    "db_size_mb": 0.0,
}


def update_state(**kwargs):
    _SYSTEM_STATE.update(kwargs)


def get_state() -> dict:
    db_path = get_data_dir() / "dragonflow.db"
    if db_path.exists():
        _SYSTEM_STATE["db_size_mb"] = round(db_path.stat().st_size / 1024 / 1024, 2)
    return dict(_SYSTEM_STATE)


def format_status_message() -> str:
    s = get_state()
    nxt = get_next_schedule_info()
    td = "Y" if is_trading_day() else "N"
    th = "交易中" if is_trading_hours() else "休市"
    url = get_dashboard_url()
    zone_emoji = {"super_attack": "S", "attack": "A",
                  "range": "R", "risk": "X"}.get(s["current_zone"], "?")

    return f"""## DragonFlow 系统状态

| 项目 | 状态 |
|------|------|
| 调度器 | {"运行中" if s['scheduler_running'] else "未启动"} |
| AutoPilot | {"运行中" if s['autopilot_running'] else "未启动"} |
| Web | {"运行 " + url if s['web_running'] else "未启动"} |
| 交易日 | {td} | 时段 | {th} |
| 市场区间 | [{zone_emoji}] {s['current_zone']} |
| 情绪周期 | {s['current_emotion']} |
| 数据库 | {s['db_size_mb']} MB |

**最近执行**: 早盘{s.get('last_morning') or '-'} / 午盘{s.get('last_midday') or '-'} / 收盘{s.get('last_closing') or '-'}
**下次任务**: {nxt['date']} {nxt['time']} {nxt['name']}
**Dashboard**: {url}
"""
