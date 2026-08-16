"""MODULE: tokocrypto_bot.strategy.pair_universe - Dynamic Pair Discovery"""
import time, requests, logging
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional, Set
logger = logging.getLogger("NVRA.PairUniverse")
@dataclass(frozen=True)
class SymbolRules:
    symbol: str; base_asset: str; quote_asset: str; status: str
    min_price: float; max_price: float; tick_size: float
    min_qty: float; max_qty: float; step_size: float; min_notional: float
    is_spot_trading_allowed: bool
@dataclass(frozen=True)
class PairUniverseConfig:
    allowed_quote_assets: Set[str] = field(default_factory=lambda: {"USDT", "BIDR", "IDR", "BTC", "ETH"})
    min_24h_volume_usdt: float = 50000.0
    max_active_pairs: int = 50
    cache_ttl_seconds: float = 3600.0
class PairUniverseEngine:
    def __init__(self, base_url: str = "https://api.tokocrypto.com", config: Optional[PairUniverseConfig] = None):
        self.base_url = base_url.rstrip("/")
        self.config = config or PairUniverseConfig()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "NVRA-PairUniverse/2026.5"})
        self._cached_universe: List[SymbolRules] = []
        self._last_update_time: float = 0.0
    def get_active_universe(self, force_refresh: bool = False) -> List[SymbolRules]:
        now = time.time()
        if not force_refresh and self._cached_universe and (now - self._last_update_time < self.config.cache_ttl_seconds):
            return self._cached_universe
        symbol_rules = self._fetch_exchange_info()
        if not symbol_rules: return self._cached_universe
        ticker_24h = self._fetch_24h_tickers()
        active = []
        for rule in symbol_rules:
            if not rule.is_spot_trading_allowed or rule.status != "TRADING": continue
            if rule.quote_asset not in self.config.allowed_quote_assets: continue
            if ticker_24h.get(rule.symbol, 0.0) < self.config.min_24h_volume_usdt: continue
            active.append(rule)
        active.sort(key=lambda r: ticker_24h.get(r.symbol, 0.0), reverse=True)
        active = active[:self.config.max_active_pairs]
        self._cached_universe = active
        self._last_update_time = now
        return self._cached_universe
    def _fetch_exchange_info(self) -> List[SymbolRules]:
        try:
            res = self.session.get(f"{self.base_url}/api/v3/exchangeInfo", timeout=10.0)
            res.raise_for_status(); data = res.json(); rules_list = []
            for s in data.get("symbols", []):
                min_price=tick_size=max_price=min_qty=step_size=max_qty=0.0; min_notional=10.0
                for f in s.get("filters", []):
                    ft = f.get("filterType")
                    if ft=="PRICE_FILTER": min_price=float(f.get("minPrice",0)); max_price=float(f.get("maxPrice",0)); tick_size=float(f.get("tickSize",0))
                    elif ft=="LOT_SIZE": min_qty=float(f.get("minQty",0)); max_qty=float(f.get("maxQty",0)); step_size=float(f.get("stepSize",0))
                    elif ft in ("MIN_NOTIONAL","NOTIONAL"): min_notional=float(f.get("minNotional", f.get("notional",10.0)))
                rules_list.append(SymbolRules(s["symbol"], s["baseAsset"], s["quoteAsset"], s["status"], min_price, max_price, tick_size, min_qty, max_qty, step_size, min_notional, s.get("isSpotTradingAllowed", True)))
            return rules_list
        except Exception as e:
            logger.error(f"exchangeInfo error: {e}"); return []
    def _fetch_24h_tickers(self) -> Dict[str, float]:
        try:
            res = self.session.get(f"{self.base_url}/api/v3/ticker/24hr", timeout=10.0)
            res.raise_for_status()
            return {item["symbol"]: float(item.get("quoteVolume", 0.0)) for item in res.json()}
        except Exception as e:
            logger.error(f"24h ticker error: {e}"); return {}
