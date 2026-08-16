"""MODULE: tokocrypto_bot.exchange.tokocrypto_client - Direct API client with non-retry POST/cancel"""
import hmac, hashlib, time, requests, logging
from typing import Dict, Any, List, Optional
from urllib.parse import urlencode
logger = logging.getLogger("NVRA.TokocryptoClient")
class RateLimitError(Exception): pass
class TokocryptoDirectClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str = "https://api.tokocrypto.com"):
        self.api_key, self.api_secret, self.base_url = api_key, api_secret, base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key, "User-Agent": "NVRA-TradingEngine/2026.5"})
    def _generate_signature(self, params):
        return hmac.new(self.api_secret.encode(), urlencode(params).encode(), hashlib.sha256).hexdigest()
    def fetch_account_balances(self):
        params = {"timestamp": int(time.time()*1000)}; params["signature"] = self._generate_signature(params)
        res = self.session.get(f"{self.base_url}/api/v3/account", params=params, timeout=10.0)
        if res.status_code == 429: raise RateLimitError("429")
        res.raise_for_status(); data = res.json(); balances = {}
        for item in data.get("balances", []):
            free, locked = float(item["free"]), float(item["locked"])
            if free > 0 or locked > 0: balances[item["asset"]] = {"free": free, "locked": locked}
        return balances
    def fetch_open_orders(self, symbol=None):
        params = {"timestamp": int(time.time()*1000)}
        if symbol: params["symbol"] = symbol
        params["signature"] = self._generate_signature(params)
        res = self.session.get(f"{self.base_url}/api/v3/openOrders", params=params, timeout=10.0)
        if res.status_code == 429: raise RateLimitError("429")
        res.raise_for_status(); return res.json()
    def fetch_order_by_client_id(self, symbol, client_order_id):
        params = {"symbol": symbol, "origClientOrderId": client_order_id, "timestamp": int(time.time()*1000)}
        params["signature"] = self._generate_signature(params)
        try:
            res = self.session.get(f"{self.base_url}/api/v3/order", params=params, timeout=10.0)
            if res.status_code == 429: raise RateLimitError("429")
            if res.status_code == 404: return None
            res.raise_for_status(); return res.json()
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (404, 400): return None
            raise
    def fetch_recent_trades(self, symbol, limit=50):
        params = {"symbol": symbol, "limit": limit, "timestamp": int(time.time()*1000)}
        params["signature"] = self._generate_signature(params)
        res = self.session.get(f"{self.base_url}/api/v3/myTrades", params=params, timeout=10.0)
        if res.status_code == 429: raise RateLimitError("429")
        res.raise_for_status(); return res.json()
    def post_order_non_retry(self, symbol, side, order_type, quantity, price, client_order_id):
        params = {"symbol": symbol, "side": side.upper(), "type": order_type.upper(), "quantity": quantity, "newClientOrderId": client_order_id, "timestamp": int(time.time()*1000)}
        if price and order_type.upper() == "LIMIT": params["price"] = price; params["timeInForce"] = "GTC"
        params["signature"] = self._generate_signature(params)
        res = self.session.post(f"{self.base_url}/api/v3/order", data=params, timeout=8.0)
        if res.status_code == 429: raise RateLimitError("POST 429 no retry")
        res.raise_for_status(); return res.json()
    def cancel_order_non_retry(self, symbol, client_order_id=None, exchange_order_id=None):
        params = {"symbol": symbol, "timestamp": int(time.time()*1000)}
        if client_order_id: params["origClientOrderId"] = client_order_id
        elif exchange_order_id: params["orderId"] = exchange_order_id
        else: raise ValueError("need client_order_id or exchange_order_id")
        params["signature"] = self._generate_signature(params)
        res = self.session.delete(f"{self.base_url}/api/v3/order", params=params, timeout=8.0)
        if res.status_code == 429: raise RateLimitError("CANCEL 429")
        res.raise_for_status(); return res.json()
