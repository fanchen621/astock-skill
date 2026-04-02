"""
FastAPI + WebSocket 服务
═══════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import List

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from openclaw_adapter import register_ws_broadcast, get_dashboard_url

app = FastAPI(title="DragonFlow", version="1.0")

# ─── WebSocket 管理 ──────────────────────────────────────────────────────────
_ws_clients: List[WebSocket] = []


async def ws_broadcast(message: str):
    dead = []
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.remove(ws)


register_ws_broadcast(ws_broadcast)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    _ws_clients.append(ws)
    logger.info(f"[WS] 客户端连接，当前{len(_ws_clients)}个")
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in _ws_clients:
            _ws_clients.remove(ws)
        logger.info(f"[WS] 客户端断开，剩余{len(_ws_clients)}个")


# ─── Dashboard HTML ──────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html_path = Path(__file__).parent / "templates" / "dashboard.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>DragonFlow Dashboard</h1><p>模板文件未找到</p>"


# ─── API 路由 ─────────────────────────────────────────────────────────────────

@app.get("/api/status")
async def api_status():
    from openclaw_adapter import get_state
    return get_state()


@app.get("/api/positions")
async def api_positions():
    from models import get_session, Position
    session = get_session()
    try:
        return [{
            "code": p.code, "name": p.name,
            "buy_price": p.buy_price, "current_price": p.current_price,
            "shares": p.shares, "pnl": p.current_pnl,
            "pnl_pct": p.current_pnl_pct,
            "hold_days": p.hold_days, "strategy": p.strategy,
            "stop_loss": p.stop_loss, "tp1": p.take_profit_1,
        } for p in session.query(Position).all()]
    finally:
        session.close()


@app.get("/api/trades")
async def api_trades(limit: int = 50):
    from models import get_session, TradeRecord
    session = get_session()
    try:
        trades = session.query(TradeRecord).order_by(
            TradeRecord.timestamp.desc()).limit(limit).all()
        return [{
            "date": str(t.date), "code": t.code, "name": t.name,
            "direction": t.direction, "price": t.price,
            "shares": t.shares, "pnl": t.pnl, "pnl_pct": t.pnl_pct,
            "signal_type": t.signal_type, "zone": t.zone,
        } for t in trades]
    finally:
        session.close()


@app.get("/api/watchlist")
async def api_watchlist():
    import datetime
    from models import get_session, WatchList
    session = get_session()
    try:
        today = datetime.date.today()
        entries = session.query(WatchList).filter(
            WatchList.date >= today).order_by(WatchList.date.desc()).all()
        return [{
            "date": str(w.date), "code": w.code, "name": w.name,
            "pick_type": w.pick_type, "score": w.score,
            "entry_price": w.entry_price, "stop_price": w.stop_price,
            "status": w.status,
        } for w in entries]
    finally:
        session.close()


@app.get("/api/stats")
async def api_stats():
    from evolution.statistics import get_recent_stats
    return get_recent_stats(20)


@app.get("/api/evolution")
async def api_evolution():
    from evolution.optimizer import get_evolution_history
    return get_evolution_history(20)


@app.get("/api/snapshot")
async def api_snapshot():
    from feeds.market_snapshot import build_analysis_snapshot
    return build_analysis_snapshot()


@app.get("/api/sentiment")
async def api_sentiment():
    from feeds.sentiment import get_market_sentiment
    return get_market_sentiment()


@app.get("/api/health")
async def api_health():
    from selfheal.diagnostics import run_health_check
    return run_health_check()


@app.get("/api/sources")
async def api_sources():
    from feeds.resilient import get_all_source_status, get_active_sources
    return {"active": get_active_sources(), "sources": get_all_source_status()}


@app.get("/api/knowledge")
async def api_knowledge(limit: int = 30):
    from intelligence.knowledge_base import get_recent_knowledge
    return get_recent_knowledge(limit=limit)


@app.get("/api/sectors")
async def api_sectors():
    from feeds.akshare_data import get_sector_flow
    return get_sector_flow()
