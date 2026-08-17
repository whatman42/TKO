"""
MODULE: tokocrypto_bot.ml.trade_fill_sync
DESCRIPTION: Sync exchange myTrades fills into local fills + ML journal.
Tokocrypto trade fields: id/tradeId, orderId, price, qty, quoteQty,
commission, commissionAsset, isBuyer, time — treated as source of truth.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from tokocrypto_bot.persistence.database import get_db_transaction
from tokocrypto_bot.persistence.ml_journal import MLJournal

logger = logging.getLogger("NVRA.TradeFillSync")


def _utc_iso_from_ms(ms: Optional[int]) -> str:
    if not ms:
        return datetime.now(timezone.utc).isoformat()
    try:
        return datetime.fromtimestamp(int(ms) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).isoformat()


def normalize_tokocrypto_trade(raw: Dict[str, Any], symbol: str) -> Optional[Dict[str, Any]]:
    """Map Tokocrypto/Binance-style myTrades row to internal fill dict."""
    try:
        trade_id = raw.get("id") or raw.get("tradeId") or raw.get("trade_id")
        if trade_id is None:
            return None
        price = float(raw.get("price") or 0.0)
        qty = float(raw.get("qty") or raw.get("quantity") or 0.0)
        if qty <= 0 or price < 0:
            return None
        is_buyer = raw.get("isBuyer")
        if is_buyer is None:
            is_buyer = raw.get("is_buyer")
        if is_buyer is None:
            side = str(raw.get("side") or "").upper()
            is_buyer = side == "BUY"
        else:
            is_buyer = bool(is_buyer)
        side = "BUY" if is_buyer else "SELL"
        commission = float(raw.get("commission") or raw.get("fee") or 0.0)
        commission_asset = raw.get("commissionAsset") or raw.get("fee_asset")
        order_id = raw.get("orderId") or raw.get("order_id")
        quote_qty = raw.get("quoteQty")
        try:
            quote_qty_f = float(quote_qty) if quote_qty is not None else price * qty
        except (TypeError, ValueError):
            quote_qty_f = price * qty
        t_ms = raw.get("time") or raw.get("timestamp")
        try:
            t_ms_i = int(t_ms) if t_ms is not None else 0
        except (TypeError, ValueError):
            t_ms_i = 0
        return {
            "trade_id": str(trade_id),
            "fill_id": f"TRADE-{trade_id}",
            "exchange_order_id": str(order_id) if order_id is not None else None,
            "symbol": symbol,
            "side": side,
            "price": price,
            "quantity": qty,
            "quote_qty": quote_qty_f,
            "commission": commission,
            "commission_asset": commission_asset,
            "time_ms": t_ms_i,
            "timestamp": _utc_iso_from_ms(t_ms_i),
            "is_buyer": is_buyer,
            "raw": raw,
        }
    except Exception as e:
        logger.warning("normalize trade failed: %s", e)
        return None


class TradeFillSync:
    """Pull myTrades → idempotent fills → journal outcomes with FIFO PnL when possible."""

    def __init__(self, state_manager, exchange_adapter, exchange_id: str = "TOKOCRYPTO"):
        self.state_mgr = state_manager
        self.exchange = exchange_adapter
        self.exchange_id = exchange_id or "TOKOCRYPTO"
        self.journal = MLJournal(state_manager.db, exchange_id=self.exchange_id)
        # simple in-memory FIFO cost basis per symbol for PnL on SELL fills this session
        self._lot_queue: Dict[str, List[Dict[str, float]]] = {}

    def _load_open_lots_from_db(self, symbol: str) -> None:
        if symbol in self._lot_queue:
            return
        lots: List[Dict[str, float]] = []
        try:
            conn = self.state_mgr.db.get_connection()
            try:
                rows = conn.execute(
                    """
                    SELECT price, quantity, side FROM fills
                    WHERE exchange_id=? AND symbol=?
                    ORDER BY timestamp ASC, id ASC
                    """,
                    (self.exchange_id, symbol),
                ).fetchall()
            finally:
                conn.close()
            for price, qty, side in rows:
                side_u = str(side or "").upper()
                q = float(qty or 0.0)
                p = float(price or 0.0)
                if q <= 0:
                    continue
                if side_u == "BUY":
                    lots.append({"qty": q, "price": p})
                elif side_u == "SELL":
                    remain = q
                    while remain > 1e-12 and lots:
                        lot = lots[0]
                        take = min(lot["qty"], remain)
                        lot["qty"] -= take
                        remain -= take
                        if lot["qty"] <= 1e-12:
                            lots.pop(0)
            self._lot_queue[symbol] = lots
        except Exception as e:
            logger.warning("load lots failed %s: %s", symbol, e)
            self._lot_queue[symbol] = []

    def _fifo_realized_pnl(self, symbol: str, side: str, qty: float, price: float, fee: float) -> Optional[float]:
        """Return realized PnL for SELL against FIFO buys; BUY returns None (open)."""
        self._load_open_lots_from_db(symbol)
        lots = self._lot_queue.setdefault(symbol, [])
        if side == "BUY":
            lots.append({"qty": float(qty), "price": float(price)})
            return None
        # SELL
        remain = float(qty)
        cost = 0.0
        sold = 0.0
        while remain > 1e-12 and lots:
            lot = lots[0]
            take = min(lot["qty"], remain)
            cost += take * lot["price"]
            lot["qty"] -= take
            remain -= take
            sold += take
            if lot["qty"] <= 1e-12:
                lots.pop(0)
        if sold <= 1e-12:
            return None
        proceeds = sold * float(price)
        pnl = proceeds - cost - float(fee or 0.0)
        return pnl

    def _upsert_fill(self, fill: Dict[str, Any], client_order_id: Optional[str]) -> bool:
        """Insert fill if new. Returns True if inserted."""
        with get_db_transaction(self.state_mgr.db) as conn:
            cur = conn.execute(
                """
                INSERT INTO fills (
                    fill_id, client_order_id, exchange_order_id, symbol, side,
                    price, quantity, fee, fee_asset, timestamp, exchange_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(fill_id) DO NOTHING
                """,
                (
                    fill["fill_id"],
                    client_order_id,
                    fill.get("exchange_order_id"),
                    fill["symbol"],
                    fill["side"],
                    fill["price"],
                    fill["quantity"],
                    fill.get("commission") or 0.0,
                    fill.get("commission_asset"),
                    fill["timestamp"],
                    self.exchange_id,
                ),
            )
            return cur.rowcount > 0

    def _resolve_client_order_id(self, fill: Dict[str, Any]) -> Optional[str]:
        oid = fill.get("exchange_order_id")
        if not oid:
            return None
        try:
            conn = self.state_mgr.db.get_connection()
            try:
                row = conn.execute(
                    """
                    SELECT client_order_id FROM orders
                    WHERE exchange_id=? AND (exchange_order_id=? OR client_order_id=?)
                    LIMIT 1
                    """,
                    (self.exchange_id, str(oid), str(oid)),
                ).fetchone()
                return row[0] if row else None
            finally:
                conn.close()
        except Exception:
            return None

    def sync_symbol(self, symbol: str, limit: int = 100) -> int:
        """Fetch myTrades for symbol; persist new fills; journal with PnL when closable.
        Returns number of newly ingested trades.
        """
        fetch = getattr(self.exchange, "fetch_recent_trades", None)
        if fetch is None:
            return 0
        try:
            raw_list = fetch(symbol=symbol, limit=limit) or []
        except Exception as e:
            logger.error("fetch_recent_trades failed %s: %s", symbol, e)
            return 0
        if not isinstance(raw_list, list):
            return 0

        new_count = 0
        for raw in raw_list:
            if not isinstance(raw, dict):
                continue
            fill = normalize_tokocrypto_trade(raw, symbol)
            if not fill:
                continue
            cid = self._resolve_client_order_id(fill) or f"EXCHANGE-TRADE-{fill['trade_id']}"
            inserted = self._upsert_fill(fill, cid)
            if not inserted:
                continue
            new_count += 1
            fee = float(fill.get("commission") or 0.0)
            # fee in base asset approx to quote if needed — keep raw commission as fee unit
            pnl = self._fifo_realized_pnl(
                symbol, fill["side"], fill["quantity"], fill["price"], fee
            )
            outcome = "CLOSED" if pnl is not None else "OPEN"
            try:
                self.journal.link_outcome_to_prediction(
                    symbol=symbol,
                    fill_id=fill["fill_id"],
                    side=fill["side"],
                    entry_price=fill["price"],
                    quantity=fill["quantity"],
                    client_order_id=cid,
                    fee=fee,
                    exchange_id=self.exchange_id,
                    fill_timestamp=fill["timestamp"],
                    realized_pnl_usdt=pnl,
                    outcome_status=outcome,
                )
            except Exception as e:
                logger.error("journal link after trade sync failed: %s", e)
        if new_count:
            logger.info("TradeFillSync %s: ingested %d new trades", symbol, new_count)
        return new_count

    def sync_symbols(self, symbols: Sequence[str], limit: int = 100) -> int:
        total = 0
        for sym in symbols:
            if not sym:
                continue
            total += self.sync_symbol(str(sym), limit=limit)
        return total

    def symbols_from_journal_and_orders(self) -> List[str]:
        """Discover symbols that need trade sync."""
        found = set()
        try:
            conn = self.state_mgr.db.get_connection()
            try:
                for row in conn.execute(
                    "SELECT DISTINCT symbol FROM ml_prediction_log WHERE exchange_id=?",
                    (self.exchange_id,),
                ):
                    found.add(row[0])
                for row in conn.execute(
                    "SELECT DISTINCT symbol FROM orders WHERE exchange_id=?",
                    (self.exchange_id,),
                ):
                    found.add(row[0])
            finally:
                conn.close()
        except Exception as e:
            logger.warning("symbol discovery failed: %s", e)
        return sorted(s for s in found if s)
