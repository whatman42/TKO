"""
MODULE: tokocrypto_bot.persistence.ml_journal
DESCRIPTION: CL-0 Prediction + Outcome Journal (append-only, fail-safe).
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from tokocrypto_bot.persistence.database import DatabaseManager, get_db_transaction

logger = logging.getLogger("NVRA.MLJournal")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_prediction_id(
    symbol: str,
    feature_timestamp: int,
    model_version: str,
    feature_version: str,
    probability_up: float,
    probability_down: float,
) -> str:
    raw = (
        f"{symbol}|{feature_timestamp}|{model_version}|{feature_version}|"
        f"{probability_up:.10f}|{probability_down:.10f}"
    )
    return "PRED-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class MLJournal:
    def __init__(self, db_manager: DatabaseManager, exchange_id: str = "TOKOCRYPTO"):
        self.db = db_manager
        self.exchange_id = exchange_id or "TOKOCRYPTO"

    def record_prediction(
        self,
        *,
        symbol: str,
        feature_timestamp: int,
        feature_version: str,
        model_version: str,
        features: Dict[str, Any],
        probability_up: float,
        probability_down: float,
        confidence: float,
        prediction_valid: bool,
        prediction_status: str,
        decision_action: Optional[str] = None,
        decision_reasons: Optional[list] = None,
        market_regime: Optional[str] = None,
        cycle_id: Optional[str] = None,
        exchange_id: Optional[str] = None,
    ) -> Optional[str]:
        try:
            eid = exchange_id or self.exchange_id
            pred_id = make_prediction_id(
                symbol, int(feature_timestamp), str(model_version), str(feature_version),
                float(probability_up), float(probability_down),
            )
            features_json = json.dumps(features or {}, sort_keys=True, default=str)
            reasons_json = json.dumps(decision_reasons or [], default=str)
            with get_db_transaction(self.db) as conn:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ml_prediction_log (
                        prediction_id, exchange_id, symbol, feature_timestamp, logged_at,
                        feature_version, model_version, features_json,
                        probability_up, probability_down, confidence,
                        prediction_valid, prediction_status,
                        decision_action, decision_reasons_json, market_regime, cycle_id
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        pred_id, eid, symbol, int(feature_timestamp), _utc_now(),
                        str(feature_version), str(model_version), features_json,
                        float(probability_up), float(probability_down), float(confidence),
                        1 if prediction_valid else 0, str(prediction_status or ""),
                        decision_action, reasons_json, market_regime, cycle_id,
                    ),
                )
            return pred_id
        except Exception as e:
            logger.error("MLJournal.record_prediction failed (swallowed): %s", e)
            return None

    def link_outcome_to_prediction(
        self,
        *,
        symbol: str,
        fill_id: str,
        side: str,
        entry_price: float,
        quantity: float,
        client_order_id: Optional[str] = None,
        fee: float = 0.0,
        exchange_id: Optional[str] = None,
        fill_timestamp: Optional[str] = None,
        realized_pnl_usdt: Optional[float] = None,
        outcome_status: str = "OPEN",
    ) -> Optional[str]:
        try:
            eid = exchange_id or self.exchange_id
            fill_ts_ms = self._parse_ts_ms(fill_timestamp)
            with get_db_transaction(self.db) as conn:
                row = conn.execute(
                    """
                    SELECT prediction_id, feature_timestamp FROM ml_prediction_log
                    WHERE symbol = ? AND exchange_id = ?
                      AND feature_timestamp <= ?
                    ORDER BY feature_timestamp DESC, id DESC
                    LIMIT 1
                    """,
                    (symbol, eid, fill_ts_ms),
                ).fetchone()
                if not row:
                    row = conn.execute(
                        """
                        SELECT prediction_id, feature_timestamp FROM ml_prediction_log
                        WHERE symbol = ? AND exchange_id = ?
                        ORDER BY feature_timestamp DESC, id DESC
                        LIMIT 1
                        """,
                        (symbol, eid),
                    ).fetchone()
                if not row:
                    logger.info("MLJournal: no prediction to link for fill %s symbol %s", fill_id, symbol)
                    return None
                pred_id = row[0]
                feature_ts = int(row[1])
                holding = None
                if fill_ts_ms and feature_ts:
                    holding = max(0.0, (fill_ts_ms - feature_ts) / 1000.0)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO ml_trade_outcomes (
                        prediction_id, fill_id, client_order_id, exchange_id, symbol, side,
                        entry_price, quantity, fee, realized_pnl_usdt, holding_period_sec,
                        outcome_status, linked_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        pred_id, fill_id, client_order_id, eid, symbol, str(side).upper(),
                        float(entry_price), float(quantity), float(fee or 0.0),
                        realized_pnl_usdt, holding, outcome_status, _utc_now(),
                    ),
                )
            return pred_id
        except Exception as e:
            logger.error("MLJournal.link_outcome_to_prediction failed (swallowed): %s", e)
            return None

    @staticmethod
    def _parse_ts_ms(ts: Optional[str]) -> int:
        if ts is None:
            return int(datetime.now(timezone.utc).timestamp() * 1000)
        try:
            v = float(ts)
            if v > 1e12:
                return int(v)
            if v > 1e9:
                return int(v * 1000)
        except (TypeError, ValueError):
            pass
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception:
            return int(datetime.now(timezone.utc).timestamp() * 1000)

    def count_predictions(self, symbol: Optional[str] = None) -> int:
        try:
            conn = self.db.get_connection()
            try:
                if symbol:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM ml_prediction_log WHERE symbol=?", (symbol,)
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM ml_prediction_log").fetchone()
                return int(row[0] if row else 0)
            finally:
                conn.close()
        except Exception:
            return 0

    def count_outcomes(self, symbol: Optional[str] = None) -> int:
        try:
            conn = self.db.get_connection()
            try:
                if symbol:
                    row = conn.execute(
                        "SELECT COUNT(*) FROM ml_trade_outcomes WHERE symbol=?", (symbol,)
                    ).fetchone()
                else:
                    row = conn.execute("SELECT COUNT(*) FROM ml_trade_outcomes").fetchone()
                return int(row[0] if row else 0)
            finally:
                conn.close()
        except Exception:
            return 0

    def fetch_training_rows(self, min_rows: int = 1) -> list:
        """Return list of {features: dict, label: int} for continual learning."""
        try:
            conn = self.db.get_connection()
            try:
                rows = conn.execute(
                    """
                    SELECT p.features_json, p.decision_action, p.prediction_valid,
                           o.realized_pnl_usdt, o.side
                    FROM ml_prediction_log p
                    LEFT JOIN ml_trade_outcomes o ON o.prediction_id = p.prediction_id
                    WHERE p.prediction_valid = 1
                    ORDER BY p.feature_timestamp ASC
                    """
                ).fetchall()
            finally:
                conn.close()
            out = []
            for features_json, decision_action, _valid, pnl, side in rows:
                try:
                    feats = json.loads(features_json) if features_json else {}
                except Exception:
                    continue
                if not feats:
                    continue
                label = None
                if pnl is not None:
                    label = 1 if float(pnl) > 0 else 0
                else:
                    act = str(decision_action or "").upper()
                    if act == "BUY":
                        label = 1
                    elif act == "SELL":
                        label = 0
                if label is None:
                    continue
                out.append({"features": feats, "label": int(label)})
            return out
        except Exception as e:
            logger.error("fetch_training_rows failed: %s", e)
            return []
