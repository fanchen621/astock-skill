"""
AutoPilot 运行时（来自Codex，大幅增强）
═══════════════════════════════════════════════════════════════════════════
3秒tick循环，盘中核心引擎：
  每tick：
    1. 新浪API获取实时指数（<2s）
    2. 更新持仓现价
    3. 检查止损/止盈信号（tick级，确保-1.8%不延迟）
    4. 分钟级：检查买入信号、市场区间变化
  非交易时段：60秒tick，仅做数据缓存
"""
from __future__ import annotations

import json
import threading
import time
import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from loguru import logger

from feeds.resilient import resilient_fetch_indices, resilient_fetch_stocks
from feeds.market_snapshot import build_market_env_data
from feeds.sentiment import get_market_sentiment, save_sentiment_snapshot
from selfheal.diagnostics import record_data_success, record_data_error, record_tick_latency
from selfheal.doctor import auto_heal
from strategy.zone_classifier import classify_zone
from strategy.emotion_cycle import get_emotion_cycle
from strategy.trade_rules import check_sell_signal, check_buy_signal, check_add_signal
from strategy.position_manager import get_position_policy
from engine.simulator import TradeSimulator
from models import get_session, Position, WatchList
from openclaw_adapter import is_trading_hours, is_trading_day, update_state, push_message


class AutoPilotRuntime:

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.runtime_dir = base_dir / "data" / "runtime"
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.state_file = self.runtime_dir / "runtime_state.json"

        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._broadcast: Optional[Callable[[str], None]] = None
        self._simulator = TradeSimulator()

        # 运行时状态
        self.current_zone = "unknown"
        self.current_emotion = "unknown"
        self.current_sentiment_modifier = 1.0
        self.last_zone_check = 0.0
        self.last_buy_check = 0.0
        self.last_sentiment_check = 0.0
        self.last_heal_check = 0.0
        self.tick_count = 0
        self.executed_signals: set = set()

        # 交易计划缓存（由收盘报告写入）
        self._trade_plans: List = []

        # 舆情数据
        self._market_sentiment: Dict = {}

    def set_broadcast(self, fn: Callable[[str], None]):
        self._broadcast = fn

    def set_trade_plans(self, plans: List):
        self._trade_plans = plans
        logger.info(f"[AutoPilot] 加载 {len(plans)} 个交易计划")

    def _emit(self, event: str, payload: dict):
        if self._broadcast:
            try:
                self._broadcast(json.dumps(
                    {"event": event, "data": payload},
                    ensure_ascii=False, default=str))
            except Exception:
                pass

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="autopilot")
        self._thread.start()
        update_state(autopilot_running=True)
        logger.info("[AutoPilot] 已启动，3秒tick循环")

    def stop(self):
        self._stop_evt.set()
        update_state(autopilot_running=False)
        logger.info("[AutoPilot] 已停止")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ════════════════════════════════════════════════════════════════════
    # 主循环
    # ════════════════════════════════════════════════════════════════════

    def _loop(self):
        while not self._stop_evt.is_set():
            started = time.perf_counter()
            try:
                if is_trading_day() and is_trading_hours():
                    self._tick_trading()
                else:
                    self._tick_idle()
            except Exception as e:
                logger.error(f"[AutoPilot] tick异常: {e}")

            elapsed = time.perf_counter() - started
            # 交易时段3秒，非交易60秒
            interval = 3.0 if (is_trading_day() and is_trading_hours()) else 60.0
            sleep_sec = max(0.1, interval - elapsed)
            self._stop_evt.wait(timeout=sleep_sec)

    # ────────────────────────────────────────────────────────────────────
    # 交易时段tick
    # ────────────────────────────────────────────────────────────────────

    def _tick_trading(self):
        self.tick_count += 1
        now = time.time()
        tick_start = time.perf_counter()

        # 1. 弹性获取实时指数（自动多源切换）
        try:
            indices = resilient_fetch_indices()
            record_data_success("index")
        except Exception as e:
            record_data_error("index", str(e), "fetch_fail")
            return

        # 2. 获取持仓的实时行情
        session = get_session()
        positions = session.query(Position).all()
        pos_codes = [p.code for p in positions]
        session.close()

        rt_map = {}
        if pos_codes:
            try:
                rt_map = resilient_fetch_stocks(pos_codes)
                record_data_success("stock")
            except Exception as e:
                record_data_error("stock", str(e), "fetch_fail")
            self._simulator.update_positions_price(rt_map)

        # 3. 每30秒更新市场区间和情绪
        if now - self.last_zone_check > 30:
            self._update_zone_emotion()
            self.last_zone_check = now

        # 4. 每5分钟更新舆情
        if now - self.last_sentiment_check > 300:
            self._update_sentiment()
            self.last_sentiment_check = now

        # 5. 每tick检查卖出信号（止损不延迟！）
        self._check_sells(rt_map)

        # 6. 每60秒检查买入信号
        if now - self.last_buy_check > 60:
            self._check_buys(rt_map)
            self.last_buy_check = now

        # 7. 每60秒自愈检查
        if now - self.last_heal_check > 60:
            try:
                auto_heal()
            except Exception:
                pass
            self.last_heal_check = now

        # 8. 记录tick延迟
        elapsed_ms = (time.perf_counter() - tick_start) * 1000
        record_tick_latency(elapsed_ms)

        # 9. 广播到Dashboard
        self._emit("market_tick", {
            "ts": datetime.datetime.now().strftime("%H:%M:%S"),
            "indices": indices,
            "zone": self.current_zone,
            "emotion": self.current_emotion,
            "sentiment": self._market_sentiment,
            "positions": self._get_positions_snapshot(),
            "tick": self.tick_count,
            "tick_ms": round(elapsed_ms, 1),
        })

        # 每小时清理信号缓存
        if datetime.datetime.now().minute == 0 and datetime.datetime.now().second < 5:
            self.executed_signals.clear()

    def _tick_idle(self):
        """非交易时段：知识爬虫/舆情/自愈"""
        now = time.time()
        # 每30分钟爬舆情
        if now - self.last_sentiment_check > 1800:
            self._update_sentiment()
            self.last_sentiment_check = now
        # 每6小时自愈全检
        if now - self.last_heal_check > 21600:
            try:
                auto_heal()
            except Exception:
                pass
            self.last_heal_check = now

    # ────────────────────────────────────────────────────────────────────
    # 区间/情绪更新
    # ────────────────────────────────────────────────────────────────────

    def _update_zone_emotion(self):
        try:
            env_data = build_market_env_data()
            zone, notes = classify_zone(env_data)
            zt_count = env_data.get("limit_up_count", 0)
            emotion = get_emotion_cycle(zt_count)

            old_zone = self.current_zone
            self.current_zone = zone
            self.current_emotion = emotion

            update_state(current_zone=zone, current_emotion=emotion)

            # 区间变化通知
            if old_zone != "unknown" and old_zone != zone:
                msg = f"**市场区间变化** {old_zone} -> {zone}\n{notes[0] if notes else ''}"
                push_message(msg, title="DragonFlow 区间变化",
                             urgent=(zone == "risk"))
                logger.info(f"[AutoPilot] 区间变化: {old_zone} -> {zone}")

        except Exception as e:
            logger.debug(f"[AutoPilot] 区间更新失败: {e}")

    # ────────────────────────────────────────────────────────────────────
    # 舆情更新
    # ────────────────────────────────────────────────────────────────────

    def _update_sentiment(self):
        try:
            self._market_sentiment = get_market_sentiment()
            self.current_sentiment_modifier = self._market_sentiment.get("modifier", 1.0)
            save_sentiment_snapshot()

            label = self._market_sentiment.get("label", "中性")
            rsi = self._market_sentiment.get("rsi", 0.5)
            if label in ("极度乐观", "极度恐慌"):
                push_message(
                    f"**舆情预警** RSI={rsi:.2f} [{label}]\n"
                    f"仓位修正: x{self.current_sentiment_modifier}",
                    title="DragonFlow 舆情", urgent=True)

            self._emit("sentiment_update", self._market_sentiment)
        except Exception as e:
            logger.debug(f"[AutoPilot] 舆情更新失败: {e}")

    # ────────────────────────────────────────────────────────────────────
    # 卖出检查（每tick执行，止损不延迟）
    # ────────────────────────────────────────────────────────────────────

    def _check_sells(self, rt_map: dict):
        session = get_session()
        try:
            positions = session.query(Position).all()
            now = datetime.datetime.now()
            force_close = (now.hour == 14 and now.minute >= 50)

            for pos in positions:
                rt = rt_map.get(pos.code, {})
                if not rt:
                    continue

                pos_dict = {
                    "code": pos.code, "name": pos.name,
                    "buy_price": pos.buy_price, "shares": pos.shares,
                    "buy_date": pos.buy_date, "hold_days": pos.hold_days or 0,
                }

                sig = check_sell_signal(
                    pos_dict, rt,
                    self.current_zone, self.current_emotion,
                    force_close)

                if sig:
                    sig_key = f"SELL_{sig.code}_{now.strftime('%H%M')}"
                    if sig_key in self.executed_signals:
                        continue

                    result = self._simulator.execute_signal(sig)
                    if result:
                        self.executed_signals.add(sig_key)
                        self._notify_trade(result)
                        self._emit("trade_signal", result)
        finally:
            session.close()

    # ────────────────────────────────────────────────────────────────────
    # 买入检查（每分钟执行）
    # ────────────────────────────────────────────────────────────────────

    def _check_buys(self, rt_map: dict):
        if self.current_zone == "risk":
            return
        if not self._trade_plans:
            return

        session = get_session()
        try:
            pos_count = session.query(Position).count()

            # 也从watchlist获取当日候选
            today_wl = session.query(WatchList).filter_by(
                date=datetime.date.today()).filter(
                WatchList.status.in_(["pending", "watching"])).all()
            session.close()

            # 获取候选股实时行情
            plan_codes = [p.code for p in self._trade_plans]
            wl_codes = [w.code for w in today_wl]
            all_codes = list(set(plan_codes + wl_codes))
            if not all_codes:
                return

            rt_all = resilient_fetch_stocks(all_codes)

            for plan in self._trade_plans:
                rt = rt_all.get(plan.code, {})
                if not rt:
                    continue

                sig = check_buy_signal(
                    plan, rt,
                    self.current_zone, self.current_emotion,
                    pos_count)

                if sig:
                    now = datetime.datetime.now()
                    sig_key = f"BUY_{sig.code}_{now.strftime('%H%M')}"
                    if sig_key in self.executed_signals:
                        continue

                    result = self._simulator.execute_signal(sig)
                    if result:
                        self.executed_signals.add(sig_key)
                        pos_count += 1
                        self._notify_trade(result)
                        self._emit("trade_signal", result)
        except Exception as e:
            logger.debug(f"[AutoPilot] 买入检查异常: {e}")

    # ────────────────────────────────────────────────────────────────────
    # 辅助
    # ────────────────────────────────────────────────────────────────────

    def _get_positions_snapshot(self) -> list:
        session = get_session()
        try:
            return [{
                "code": p.code, "name": p.name,
                "buy_price": p.buy_price, "current_price": p.current_price,
                "shares": p.shares,
                "pnl": p.current_pnl, "pnl_pct": p.current_pnl_pct,
                "hold_days": p.hold_days, "strategy": p.strategy,
            } for p in session.query(Position).all()]
        finally:
            session.close()

    def _notify_trade(self, trade: dict):
        action = trade.get("action", "")
        code = trade.get("code", "")
        name = trade.get("name", "")
        price = trade.get("price", 0)
        shares = trade.get("shares", 0)

        if action == "BUY":
            msg = (f"**模拟买入** {code} {name}\n"
                   f"- {price:.2f} x {shares}股 = {trade.get('total',0):.0f}元\n"
                   f"- 信号: {trade.get('signal_type','')}")
        else:
            pnl = trade.get("pnl", 0)
            pnl_pct = trade.get("pnl_pct", 0)
            emoji = "+" if pnl >= 0 else ""
            msg = (f"**模拟卖出** {code} {name}\n"
                   f"- {price:.2f} x {shares}股\n"
                   f"- 盈亏: {emoji}{pnl:.0f}元 ({pnl_pct:+.1f}%)\n"
                   f"- 信号: {trade.get('signal_type','')}")

        push_message(msg, title="DragonFlow 交易信号", urgent=(action == "BUY"))


# ════════════════════════════════════════════════════════════════════════════
# 单例
# ════════════════════════════════════════════════════════════════════════════

_RUNTIME: Optional[AutoPilotRuntime] = None


def get_runtime(base_dir: Optional[Path] = None) -> AutoPilotRuntime:
    global _RUNTIME
    if _RUNTIME is None:
        if base_dir is None:
            base_dir = Path(__file__).parent.parent
        _RUNTIME = AutoPilotRuntime(base_dir)
    return _RUNTIME
