"""MODULE: tokocrypto_bot.strategy.features - Feature Engineering P1-B"""
import logging
from dataclasses import dataclass
from typing import Dict
import numpy as np
import pandas as pd
logger = logging.getLogger("NVRA.FeatureEngine")
FEATURE_VERSION = "2026.1.0"
MIN_REQUIRED_CANDLES = 200
EXPECTED_FEATURE_COLUMNS = ["EMA50","EMA200","RSI14","ROC","ATR","volatility_regime","MACD_HIST","DI_plus","DI_minus","ema_ratio","bb_pband","obv_vs_ma","cmf","vwma_dev","drawdown_20"]
@dataclass(frozen=True)
class FeatureFrame:
    timestamp: int; symbol: str; feature_version: str; features: Dict[str, float]; is_valid: bool; error_reason: str = ""
class FeatureEngine:
    def __init__(self, feature_version=FEATURE_VERSION):
        self.feature_version = feature_version
    def compute_features(self, df_klines, symbol):
        if df_klines is None or df_klines.empty:
            return FeatureFrame(0, symbol, self.feature_version, {}, False, "Empty DataFrame")
        closed_df = df_klines[df_klines["is_complete"]==True].copy() if "is_complete" in df_klines.columns else df_klines.iloc[:-1].copy()
        if len(closed_df) < MIN_REQUIRED_CANDLES:
            return FeatureFrame(0, symbol, self.feature_version, {}, False, f"Insufficient history ({len(closed_df)})")
        closed_df.sort_values("timestamp", ascending=True, inplace=True); closed_df.reset_index(drop=True, inplace=True)
        try:
            close, high, low, volume = closed_df["close"].values, closed_df["high"].values, closed_df["low"].values, closed_df["volume"].values
            ema50, ema200 = self._ema(close,50), self._ema(close,200)
            ema_ratio = ema50 / np.where(ema200==0, np.nan, ema200)
            rsi14 = self._rsi(close, 14)
            roc = np.zeros_like(close); roc[12:] = (close[12:]-close[:-12])/np.where(close[:-12]==0, np.nan, close[:-12])
            tr = np.maximum(high[1:]-low[1:], np.maximum(np.abs(high[1:]-close[:-1]), np.abs(low[1:]-close[:-1])))
            tr = np.insert(tr, 0, high[0]-low[0]); atr = self._ema(tr, 14)
            returns = np.insert(np.diff(np.log(np.where(close==0,1e-8,close))), 0, 0.0)
            volatility_regime = pd.Series(returns).rolling(20).std().fillna(0.0).values
            ema12, ema26 = self._ema(close,12), self._ema(close,26)
            macd_hist = (ema12-ema26) - self._ema(ema12-ema26, 9)
            up_move, down_move = high[1:]-high[:-1], low[:-1]-low[1:]
            plus_dm = np.insert(np.where((up_move>down_move)&(up_move>0), up_move, 0.0), 0, 0.0)
            minus_dm = np.insert(np.where((down_move>up_move)&(down_move>0), down_move, 0.0), 0, 0.0)
            atr_safe = np.where(atr==0, 1e-8, atr)
            di_plus = (self._ema(plus_dm,14)/atr_safe)*100; di_minus = (self._ema(minus_dm,14)/atr_safe)*100
            sma20, std20 = pd.Series(close).rolling(20).mean().values, pd.Series(close).rolling(20).std().values
            bb_range = (sma20+2*std20)-(sma20-2*std20)
            bb_pband = (close-(sma20-2*std20))/np.where(bb_range==0, np.nan, bb_range)
            obv = np.cumsum(np.sign(np.diff(close, prepend=close[0]))*volume)
            obv_vs_ma = obv - pd.Series(obv).rolling(20).mean().values
            mfv = np.where((high-low)==0, 0.0, ((close-low)-(high-close))/(high-low))*volume
            cmf = pd.Series(mfv).rolling(20).sum().values / np.maximum(pd.Series(volume).rolling(20).sum().values, 1e-8)
            vwma = pd.Series(close*volume).rolling(20).sum().values / np.maximum(pd.Series(volume).rolling(20).sum().values, 1e-8)
            vwma_dev = (close-vwma)/np.where(vwma==0, np.nan, vwma)
            rolling_max = pd.Series(high).rolling(20).max().values
            drawdown_20 = (close-rolling_max)/np.where(rolling_max==0, np.nan, rolling_max)
            idx = -1; ts = int(closed_df["timestamp"].iloc[idx])
            raw = {"EMA50":float(ema50[idx]),"EMA200":float(ema200[idx]),"RSI14":float(rsi14[idx]),"ROC":float(roc[idx]),"ATR":float(atr[idx]),"volatility_regime":float(volatility_regime[idx]),"MACD_HIST":float(macd_hist[idx]),"DI_plus":float(di_plus[idx]),"DI_minus":float(di_minus[idx]),"ema_ratio":float(ema_ratio[idx]),"bb_pband":float(bb_pband[idx]),"obv_vs_ma":float(obv_vs_ma[idx]),"cmf":float(cmf[idx]),"vwma_dev":float(vwma_dev[idx]),"drawdown_20":float(drawdown_20[idx])}
            for col,val in raw.items():
                if np.isnan(val) or np.isinf(val):
                    return FeatureFrame(ts, symbol, self.feature_version, {}, False, f"NaN/Inf in {col}")
            return FeatureFrame(ts, symbol, self.feature_version, raw, True)
        except Exception as e:
            return FeatureFrame(0, symbol, self.feature_version, {}, False, str(e))
    def compute_multi_pair_features(self, klines_map):
        return {s: self.compute_features(df, s) for s, df in klines_map.items()}
    def _ema(self, values, period):
        return pd.Series(values).ewm(span=period, adjust=False).mean().values
    def _rsi(self, values, period=14):
        delta = np.diff(values)
        gain = np.where(delta>0, delta, 0.0); loss = np.where(delta<0, -delta, 0.0)
        gs = pd.Series(gain).ewm(alpha=1.0/period, adjust=False).mean()
        ls = pd.Series(loss).ewm(alpha=1.0/period, adjust=False).mean()
        rs = gs / np.where(ls==0, 1e-8, ls)
        return np.insert((100.0 - (100.0/(1.0+rs))).values, 0, 50.0)
