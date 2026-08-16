"""
MODULE: tokocrypto_bot.exchange.circuit_breaker
DESCRIPTION: Exchange API circuit breaker for 429/rate-limit and cascade failures.
POLICY: Does NOT wrap POST orders. Safe for read/recovery operations only.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("NVRA.CircuitBreaker")


@dataclass
class CircuitBreaker:
    """
    Trip after `failure_threshold` consecutive failures.
    While OPEN: operations should fail closed (SAFE_MODE / NO_TRADE).
    Half-open after `recovery_timeout_sec`.
    """
    failure_threshold: int = 5
    recovery_timeout_sec: float = 60.0
    consecutive_failures: int = 0
    opened_at: Optional[float] = None
    last_error: str = ""

    def record_success(self) -> None:
        self.consecutive_failures = 0
        self.opened_at = None
        self.last_error = ""

    def record_failure(self, error: str = "") -> None:
        self.consecutive_failures += 1
        self.last_error = error or self.last_error
        if self.consecutive_failures >= self.failure_threshold:
            if self.opened_at is None:
                self.opened_at = time.time()
                logger.critical(
                    f"CircuitBreaker OPEN after {self.consecutive_failures} failures: {self.last_error}"
                )

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        elapsed = time.time() - self.opened_at
        if elapsed >= self.recovery_timeout_sec:
            # half-open: allow one probe
            logger.warning("CircuitBreaker HALF-OPEN — allowing probe request")
            return False
        return True

    def allow_request(self) -> bool:
        return not self.is_open()
