"""
MODULE: tokocrypto_bot.execution.order_state_machine
DESCRIPTION: Hardened Order State Machine & Strict Idempotency Key Generator for Tokocrypto.
"""

import hashlib
import logging
from enum import Enum
from typing import Set, Dict

logger = logging.getLogger("NVRA.OrderStateMachine")

MAX_CLIENT_ORDER_ID_LENGTH = 36  # Batas maksimum Tokocrypto/Binance API

class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"

class OrderStatus(str, Enum):
    CREATED = "CREATED"
    SUBMITTING = "SUBMITTING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    UNKNOWN = "UNKNOWN"
    RECONCILING = "RECONCILING"
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

class InvalidStateTransitionException(Exception):
    """Exception khusus jika terjadi transisi state ilegal."""
    pass

class InvalidClientOrderIdException(Exception):
    """Exception khusus jika format atau panjang clientOrderId melanggar batas API."""
    pass

class OrderStateMachine:
    # Matriks Transisi Valid yang Dikunci Ketat
    VALID_TRANSITIONS: Dict[OrderStatus, Set[OrderStatus]] = {
        OrderStatus.CREATED: {OrderStatus.SUBMITTING, OrderStatus.CANCELED, OrderStatus.REJECTED},
        OrderStatus.SUBMITTING: {OrderStatus.ACKNOWLEDGED, OrderStatus.UNKNOWN, OrderStatus.REJECTED},
        OrderStatus.ACKNOWLEDGED: {
            OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
            OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.UNKNOWN
        },
        OrderStatus.UNKNOWN: {OrderStatus.RECONCILING},
        OrderStatus.RECONCILING: {
            OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED,
            OrderStatus.CANCELED, OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.UNKNOWN
        },
        OrderStatus.NEW: {
            OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELED,
            OrderStatus.REJECTED, OrderStatus.EXPIRED, OrderStatus.UNKNOWN
        },
        OrderStatus.PARTIALLY_FILLED: {
            OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.UNKNOWN
        },
        # Terminal states: Benar-benar Imutabel!
        OrderStatus.FILLED: set(),
        OrderStatus.CANCELED: set(),
        OrderStatus.REJECTED: set(),
        OrderStatus.EXPIRED: set(),
    }

    @classmethod
    def validate_transition(cls, current_state: OrderStatus, target_state: OrderStatus) -> bool:
        if current_state == target_state:
            return True

        allowed = cls.VALID_TRANSITIONS.get(current_state, set())
        if target_state not in allowed:
            err_msg = f"ILLEGAL STATE TRANSITION BLOCKED: [{current_state.value}] -> [{target_state.value}]"
            logger.critical(err_msg)
            raise InvalidStateTransitionException(err_msg)
        return True

    @staticmethod
    def validate_side(side: str) -> OrderSide:
        side_upper = side.upper()
        if side_upper not in (OrderSide.BUY.value, OrderSide.SELL.value):
            raise ValueError(f"Invalid Order Side: '{side}'. Must be 'BUY' or 'SELL'.")
        return OrderSide(side_upper)

    @classmethod
    def generate_client_order_id(cls, execution_id: str, signal_id: str, symbol: str, side: str) -> str:
        validated_side = cls.validate_side(side)

        # Reject pathological inputs that would indicate misuse / overflow risk
        if len(execution_id) > 48 or len(signal_id) > 48:
            raise InvalidClientOrderIdException(
                f"execution_id/signal_id too long for safe client_order_id generation "
                f"(execution_id={len(execution_id)}, signal_id={len(signal_id)})."
            )

        raw_seed = f"{execution_id}:{signal_id}:{symbol}:{validated_side.value}"
        seed_hash = hashlib.sha256(raw_seed.encode('utf-8')).hexdigest()[:12].upper()
        
        clean_symbol = symbol.replace("_", "").replace("-", "")[:6]
        side_code = "B" if validated_side == OrderSide.BUY else "S"

        # Format: QBOT-{hash12}-{symbol}-{side} -> Misal: QBOT-A1B2C3D4E5F6-BTCUSD-B (26 Karakter)
        client_order_id = f"QBOT-{seed_hash}-{clean_symbol}-{side_code}"

        if len(client_order_id) > MAX_CLIENT_ORDER_ID_LENGTH:
            raise InvalidClientOrderIdException(
                f"Generated client_order_id '{client_order_id}' exceeds maximum allowed length of {MAX_CLIENT_ORDER_ID_LENGTH} characters."
            )

        return client_order_id
