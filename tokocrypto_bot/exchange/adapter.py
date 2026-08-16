"""
MODULE: tokocrypto_bot.exchange.adapter
DESCRIPTION: Phase 1.8-B ExchangeAdapter Protocol + fail-closed Factory.
             Core depends on this abstraction; concrete venues implement it.
             Binance trading is NOT implemented — unsupported exchange fails closed.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from tokocrypto_bot.persistence.exchange_ids import DEFAULT_ACCOUNT_ID, DEFAULT_EXCHANGE_ID


class UnsupportedExchangeError(RuntimeError):
    """Raised when factory is asked for an exchange without a trading adapter."""

    def __init__(self, exchange_id: str, message: str | None = None):
        self.exchange_id = (exchange_id or "").upper()
        msg = message or (
            f"Unsupported exchange for LIVE trading adapter: {self.exchange_id}. "
            f"Only TOKOCRYPTO is implemented. Binance trading is NOT implemented."
        )
        super().__init__(msg)


@runtime_checkable
class ExchangeAdapter(Protocol):
    """
    Minimal exchange surface required by execution / reconciliation / protection.

    Method names match the existing Tokocrypto client and test doubles so that
    Phase 1.2–1.7 semantics remain unchanged (structural typing / duck typing).
    """

    def fetch_account_balances(self) -> Dict[str, Dict[str, float]]:
        """Return asset -> {free, locked} balances."""
        ...

    def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Return open orders, optionally filtered by symbol."""
        ...

    def fetch_order_by_client_id(
        self, symbol: str, client_order_id: str
    ) -> Optional[Dict[str, Any]]:
        """Lookup a single order by client order id; None if not found."""
        ...

    def fetch_recent_trades(self, symbol: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Recent fills/trades for symbol."""
        ...

    def post_order_non_retry(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: float,
        price: Optional[float],
        client_order_id: str,
    ) -> Dict[str, Any]:
        """Single-shot order POST — no automated retry."""
        ...

    def cancel_order_non_retry(
        self,
        symbol: str,
        client_order_id: Optional[str] = None,
        exchange_order_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Single-shot cancel — no automated retry."""
        ...

    def post_stop_loss_limit_non_retry(
        self,
        symbol: str,
        side: str,
        quantity: float,
        stop_price: float,
        limit_price: float = None,
        client_order_id: str = None,
    ) -> Dict[str, Any]:
        """Single-shot protective STOP_LOSS_LIMIT POST — no automated retry."""
        ...


def create_exchange_adapter(
    exchange_id: str,
    api_key: str,
    api_secret: str,
    account_id: str = DEFAULT_ACCOUNT_ID,
    base_url: Optional[str] = None,
) -> ExchangeAdapter:
    """
    Fail-closed factory: TOKOCRYPTO → TokocryptoDirectClient; anything else → error.

    Attaches exchange_id / account_id attributes for identity isolation.
    Does NOT implement Binance trading.
    """
    eid = (exchange_id or DEFAULT_EXCHANGE_ID).strip().upper() or DEFAULT_EXCHANGE_ID
    aid = (account_id or DEFAULT_ACCOUNT_ID).strip() or DEFAULT_ACCOUNT_ID

    if eid == "TOKOCRYPTO":
        from tokocrypto_bot.exchange.tokocrypto_client import TokocryptoDirectClient

        kwargs: Dict[str, Any] = {"api_key": api_key, "api_secret": api_secret}
        if base_url:
            kwargs["base_url"] = base_url
        client = TokocryptoDirectClient(**kwargs)
        # Identity metadata for isolation (not used for signing)
        client.exchange_id = eid  # type: ignore[attr-defined]
        client.account_id = aid  # type: ignore[attr-defined]
        return client

    # Explicit fail-closed for BINANCE and any unknown venue
    raise UnsupportedExchangeError(eid)
