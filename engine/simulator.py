"""
模拟交易引擎
═══════════════════════════════════════════════════════════════════════════
执行交易信号 → 写入 TradeRecord / Position 表
  - 精确 B/S 时间戳
  - 手续费模拟（买万2.5，卖万2.5+千1印花税）
  - 1万本金，100股整手
  - 仅交易沪深主板（双重校验）
"""
from __future__ import annotations

import datetime
from typing import Optional

from loguru import logger

from config import TRADE_RULES
from models import get_session, Position, TradeRecord
from strategy.signal import TradeSignal
from strategy.trade_rules import is_allowed_code

BUY_FEE_RATE = TRADE_RULES["buy_fee_rate"]
SELL_FEE_RATE = TRADE_RULES["sell_fee_rate"]


class TradeSimulator:

    def execute_signal(self, signal: TradeSignal) -> Optional[dict]:
        if not is_allowed_code(signal.code):
            logger.warning(f"[模拟] {signal.code} 非主板，拒绝")
            return None
        if signal.direction == "BUY":
            return self._execute_buy(signal)
        return self._execute_sell(signal)

    def _execute_buy(self, signal: TradeSignal) -> Optional[dict]:
        session = get_session()
        try:
            # 已有持仓不重复买
            if session.query(Position).filter_by(code=signal.code).first():
                if signal.signal_type != "加仓":
                    logger.info(f"[模拟] {signal.code} 已持仓，跳过")
                    return None

            fee = round(signal.price * signal.shares * BUY_FEE_RATE, 2)
            amount = round(signal.price * signal.shares, 2)
            total = amount + fee

            if signal.signal_type == "加仓":
                # 加仓逻辑：更新现有持仓
                pos = session.query(Position).filter_by(code=signal.code).first()
                if pos:
                    old_cost = pos.cost or 0
                    old_shares = pos.shares or 0
                    pos.shares = old_shares + signal.shares
                    pos.cost = old_cost + total
                    pos.buy_price = pos.cost / pos.shares  # 重算均价
                    # 重算止损止盈
                    pos.stop_loss = round(pos.buy_price * (1 + TRADE_RULES["stop_loss_pct"]), 2)
                    pos.take_profit_1 = round(pos.buy_price * (1 + TRADE_RULES["take_profit_1_pct"]), 2)
                    pos.take_profit_2 = round(pos.buy_price * (1 + TRADE_RULES["take_profit_2_pct"]), 2)
                    pos.add_price = round(pos.buy_price * (1 + TRADE_RULES["add_trigger_pct"]), 2)
            else:
                # 新建持仓
                pos = Position(
                    code=signal.code, name=signal.name,
                    buy_date=datetime.date.today(),
                    buy_time=signal.timestamp,
                    buy_price=signal.price,
                    shares=signal.shares,
                    cost=total,
                    current_price=signal.price,
                    current_pnl=0.0,
                    current_pnl_pct=0.0,
                    stop_loss=round(signal.price * (1 + TRADE_RULES["stop_loss_pct"]), 2),
                    take_profit_1=round(signal.price * (1 + TRADE_RULES["take_profit_1_pct"]), 2),
                    take_profit_2=round(signal.price * (1 + TRADE_RULES["take_profit_2_pct"]), 2),
                    add_price=round(signal.price * (1 + TRADE_RULES["add_trigger_pct"]), 2),
                    strategy=signal.signal_type,
                    pick_type=signal.pick_type,
                    reason=signal.signal_reason,
                    hold_days=0,
                )
                session.add(pos)

            # 交易记录
            rec = TradeRecord(
                date=datetime.date.today(),
                code=signal.code, name=signal.name,
                direction="BUY",
                price=signal.price, shares=signal.shares,
                amount=total, timestamp=signal.timestamp,
                signal_type=signal.signal_type,
                signal_reason=signal.signal_reason,
                minute_price=signal.minute_price,
                volume_ratio=signal.volume_ratio,
                zone=signal.zone, emotion=signal.emotion,
                zt_count_at=signal.zt_count,
                strategy_ver=signal.strategy_ver,
            )
            session.add(rec)
            session.commit()

            logger.info(f"[模拟] BUY {signal.code} {signal.name} "
                        f"x{signal.shares}股 @{signal.price} ={total:.0f}元")
            return {
                "action": "BUY", "code": signal.code, "name": signal.name,
                "price": signal.price, "shares": signal.shares,
                "amount": amount, "fee": fee, "total": total,
                "signal_type": signal.signal_type,
                "timestamp": str(signal.timestamp),
            }
        except Exception as e:
            session.rollback()
            logger.error(f"[模拟] 买入失败 {signal.code}: {e}")
            return None
        finally:
            session.close()

    def _execute_sell(self, signal: TradeSignal) -> Optional[dict]:
        session = get_session()
        try:
            pos = session.query(Position).filter_by(code=signal.code).first()
            if not pos:
                logger.warning(f"[模拟] 卖出无持仓 {signal.code}")
                return None

            sell_shares = min(signal.shares, pos.shares)
            fee = round(signal.price * sell_shares * SELL_FEE_RATE, 2)
            sell_amount = round(signal.price * sell_shares - fee, 2)

            cost_per = pos.cost / pos.shares if pos.shares > 0 else pos.buy_price
            cost_sold = cost_per * sell_shares
            pnl = round(sell_amount - cost_sold, 2)
            pnl_pct = round(pnl / cost_sold * 100, 2) if cost_sold > 0 else 0.0
            hold_min = int((signal.timestamp - pos.buy_time).total_seconds() / 60) if pos.buy_time else 0

            rec = TradeRecord(
                date=datetime.date.today(),
                code=signal.code, name=signal.name,
                direction="SELL",
                price=signal.price, shares=sell_shares,
                amount=sell_amount, timestamp=signal.timestamp,
                signal_type=signal.signal_type,
                signal_reason=signal.signal_reason,
                minute_price=signal.minute_price,
                volume_ratio=signal.volume_ratio,
                buy_price=pos.buy_price,
                pnl=pnl, pnl_pct=pnl_pct,
                hold_minutes=hold_min,
                zone=signal.zone, emotion=signal.emotion,
                strategy_ver=signal.strategy_ver,
            )
            session.add(rec)

            remaining = pos.shares - sell_shares
            if remaining <= 0:
                session.delete(pos)
            else:
                pos.shares = remaining
                pos.cost = cost_per * remaining

            session.commit()

            pnl_str = f"+{pnl:.0f}" if pnl >= 0 else f"{pnl:.0f}"
            logger.info(f"[模拟] SELL {signal.code} {signal.name} "
                        f"x{sell_shares}股 @{signal.price} PnL:{pnl_str}({pnl_pct:+.1f}%)")
            return {
                "action": "SELL", "code": signal.code, "name": signal.name,
                "price": signal.price, "shares": sell_shares,
                "sell_amount": sell_amount, "fee": fee,
                "pnl": pnl, "pnl_pct": pnl_pct,
                "hold_minutes": hold_min,
                "signal_type": signal.signal_type,
                "timestamp": str(signal.timestamp),
            }
        except Exception as e:
            session.rollback()
            logger.error(f"[模拟] 卖出失败 {signal.code}: {e}")
            return None
        finally:
            session.close()

    def update_positions_price(self, realtime_map: dict):
        """批量更新持仓现价和浮盈"""
        session = get_session()
        try:
            for pos in session.query(Position).all():
                rt = realtime_map.get(pos.code, {})
                if not rt:
                    continue
                cp = float(rt.get("price", pos.buy_price))
                cost_per = pos.cost / pos.shares if pos.shares > 0 else pos.buy_price
                pos.current_price = cp
                pos.current_pnl = round((cp - cost_per) * pos.shares, 2)
                pos.current_pnl_pct = round((cp / pos.buy_price - 1) * 100, 2) if pos.buy_price else 0
            session.commit()
        except Exception as e:
            session.rollback()
            logger.debug(f"[模拟] 持仓更新失败: {e}")
        finally:
            session.close()

    def update_hold_days(self):
        """每日开盘时更新持仓天数"""
        session = get_session()
        try:
            today = datetime.date.today()
            for pos in session.query(Position).all():
                if pos.buy_date:
                    pos.hold_days = (today - pos.buy_date).days
            session.commit()
        except Exception:
            session.rollback()
        finally:
            session.close()
