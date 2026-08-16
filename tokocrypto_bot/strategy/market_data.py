"""MODULE: tokocrypto_bot.strategy.market_data - stale-data fail-closed"""
import time, requests, logging, pandas as pd
from enum import Enum
from dataclasses import dataclass
from typing import List, Optional, Any
logger = logging.getLogger("NVRA.MarketData")
class DataSource(str, Enum):
    TOKOCRYPTO="TOKOCRYPTO"; BINANCE_PUBLIC_API="BINANCE_PUBLIC_API"; UNKNOWN="UNKNOWN"
@dataclass(frozen=True)
class OHLCVFrame:
    timestamp: int; symbol: str; open: float; high: float; low: float; close: float; volume: float; source: DataSource; is_complete: bool
class MarketDataEngine:
    def __init__(self, tokocrypto_base_url="https://api.tokocrypto.com", binance_fallback_url="https://api.binance.com", max_staleness_seconds=300.0):
        self.tokocrypto_url=tokocrypto_base_url.rstrip("/"); self.binance_fallback_url=binance_fallback_url.rstrip("/")
        self.max_staleness_seconds=max_staleness_seconds
        self.session=requests.Session(); self.session.headers.update({"User-Agent":"NVRA-DataEngine/2026.5"})
    def fetch_klines(self, symbol, interval="1m", limit=100):
        frames=self._fetch_tokocrypto_klines(symbol, interval, limit)
        if frames: return frames
        frames=self._fetch_binance_fallback_klines(symbol, interval, limit)
        return frames or []
    def get_klines_dataframe(self, symbol, interval="1m", limit=100):
        frames=self.fetch_klines(symbol, interval, limit)
        if not frames: return pd.DataFrame()
        data=[{"timestamp":f.timestamp,"open":f.open,"high":f.high,"low":f.low,"close":f.close,"volume":f.volume,"source":f.source.value,"is_complete":f.is_complete} for f in frames]
        df=pd.DataFrame(data); df["datetime"]=pd.to_datetime(df["timestamp"], unit="ms", utc=True); return df
    def _fetch_tokocrypto_klines(self, symbol, interval, limit):
        try:
            res=self.session.get(f"{self.tokocrypto_url}/api/v3/klines", params={"symbol":symbol,"interval":interval,"limit":limit}, timeout=5.0)
            res.raise_for_status(); return self._parse_raw_klines(symbol, res.json(), DataSource.TOKOCRYPTO)
        except Exception as e:
            logger.error(f"Tokocrypto klines error: {e}"); return None
    def _fetch_binance_fallback_klines(self, symbol, interval, limit):
        try:
            res=self.session.get(f"{self.binance_fallback_url}/api/v3/klines", params={"symbol":symbol,"interval":interval,"limit":limit}, timeout=5.0)
            res.raise_for_status(); return self._parse_raw_klines(symbol, res.json(), DataSource.BINANCE_PUBLIC_API)
        except Exception as e:
            logger.error(f"Binance fallback error: {e}"); return None
    def _parse_raw_klines(self, symbol, raw_klines, source):
        frames=[]; now_ms=int(time.time()*1000)
        for k in raw_klines:
            open_time=int(k[0]); close_time=int(k[6]) if len(k)>6 else open_time+59999
            frames.append(OHLCVFrame(open_time, symbol, float(k[1]), float(k[2]), float(k[3]), float(k[4]), float(k[5]), source, close_time<now_ms))
        if frames:
            age=(now_ms-frames[-1].timestamp)/1000.0
            if age > self.max_staleness_seconds:
                logger.error(f"STALE data {symbol} age={age:.1f}s — discard (NO_TRADE)")
                return []
        return frames
