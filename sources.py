"""Veri kaynaklari: Binance USDT-M vadeli (kripto) ve OANDA (forex / metal / endeks)."""
import os
import time
from datetime import datetime, timezone

import requests

FAPI = "https://fapi.binance.com"
OANDA_HOSTS = {"practice": "https://api-fxpractice.oanda.com", "live": "https://api-fxtrade.oanda.com"}
OKX = "https://www.okx.com"
OKX_BAR = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1Dutc", "1w": "1Wutc"}
OANDA_GRAN = {"1m": "M1", "5m": "M5", "15m": "M15", "1h": "H1", "4h": "H4", "1d": "D", "1w": "W"}

# --------------------------------------------------------------- katalog
CRYPTO_TOP = ("BTC ETH BNB SOL XRP DOGE ADA AVAX TRX LINK DOT LTC BCH ATOM UNI ETC FIL NEAR APT ARB OP SUI INJ TIA "
              "SEI RUNE AAVE MKR RNDR FET GRT IMX LDO STX ICP HBAR VET ALGO SAND MANA AXS GALA APE CRV SNX COMP DYDX "
              "EGLD THETA FTM FLOW XLM EOS XTZ KAVA ENS PEPE WLD JUP PYTH ORDI WIF BONK FLOKI JTO STRK DYM ONDO ENA "
              "TON NOT TAO W AEVO PENDLE ETHFI BOME TRB 1000SHIB 1000PEPE 1000FLOKI 1000BONK 1000SATS").split()
CRYPTO_FALLBACK = [s + "USDT" for s in CRYPTO_TOP]

FOREX_GROUPS = [
    ("maj", "Majör", ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD"]),
    ("crs", "Çapraz", ["EUR_GBP", "EUR_JPY", "EUR_CHF", "EUR_AUD", "EUR_CAD", "EUR_NZD", "GBP_JPY", "GBP_CHF",
                       "GBP_AUD", "GBP_CAD", "GBP_NZD", "AUD_JPY", "AUD_CHF", "AUD_CAD", "AUD_NZD", "NZD_JPY",
                       "NZD_CHF", "NZD_CAD", "CAD_JPY", "CAD_CHF", "CHF_JPY"]),
    ("met", "Metal", ["XAU_USD", "XAG_USD", "XPT_USD", "XPD_USD", "XCU_USD"]),
    ("idx", "Endeks", ["SPX500_USD", "NAS100_USD", "US30_USD", "US2000_USD", "DE30_EUR", "UK100_GBP", "FR40_EUR",
                       "EU50_EUR", "JP225_USD", "HK33_HKD", "AU200_AUD", "CN50_USD", "NL25_EUR", "SG30_SGD",
                       "CH20_CHF", "ES35_EUR"]),
    ("eng", "Enerji", ["WTICO_USD", "BCO_USD", "NATGAS_USD"]),
]
FOREX_ALL = [s for _, _, lst in FOREX_GROUPS for s in lst]


def display_name(market, symbol):
    return symbol if market == "crypto" else symbol.replace("_", "")


def tv_symbol(market, symbol):
    return f"BINANCE:{symbol}.P" if market == "crypto" else f"OANDA:{symbol.replace('_', '')}"


def _parse_oanda_time(s):
    return int(datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() * 1000)


class Sources:
    def __init__(self, session=None, env=None, sleep=time.sleep):
        self.s = session or requests.Session()
        self.env = env if env is not None else os.environ
        self.sleep = sleep

    # ---- ortak GET (429/5xx icin geri cekilme)
    def _get(self, url, params=None, headers=None):
        err = None
        for k in range(4):
            r = self.s.get(url, params=params, headers=headers, timeout=20)
            if r.status_code == 429 or r.status_code >= 500:
                wait = 1.5 * (k + 1)
                try:
                    wait = max(wait, float(r.headers.get("Retry-After", 0)))
                except Exception:
                    pass
                err = RuntimeError(f"HTTP {r.status_code}")
                self.sleep(min(wait, 20))
                continue
            if r.status_code in (403, 451):
                raise RuntimeError(f"HTTP {r.status_code} (bolge/izin engeli)")
            r.raise_for_status()
            return r.json()
        raise err or RuntimeError("istek basarisiz")

    def fetch(self, market, symbol, tf, limit, now_ms):
        if market == "crypto" and self._okx():
            inst = symbol[:-4] + "-USDT-SWAP" if symbol.endswith("USDT") else symbol
            j = self._get(OKX + "/api/v5/market/candles",
                          {"instId": inst, "bar": OKX_BAR[tf], "limit": min(limit, 300)})
            if str(j.get("code")) != "0":
                raise RuntimeError("OKX: " + str(j.get("msg") or j.get("code")))
            out = []
            for k in j.get("data", []):                      # en yeni basta; confirm=1 kapanmis mum
                if len(k) > 8 and str(k[8]) != "1":
                    continue
                out.append((int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4])))
            out.sort()
            return out
        if market == "crypto":
            base = self.env.get("BINANCE_FAPI_BASE", FAPI)
            rows = self._get(base + "/fapi/v1/klines", {"symbol": symbol, "interval": tf, "limit": limit})
            return [(int(k[0]), float(k[1]), float(k[2]), float(k[3]), float(k[4]))
                    for k in rows if int(k[6]) < now_ms]
        if market == "forex":
            tok = self.env.get("OANDA_TOKEN", "")
            if not tok:
                raise RuntimeError("OANDA_TOKEN tanimli degil")
            host = OANDA_HOSTS.get(self.env.get("OANDA_ENV", "practice"), OANDA_HOSTS["practice"])
            j = self._get(f"{host}/v3/instruments/{symbol}/candles",
                          {"granularity": OANDA_GRAN[tf], "count": limit, "price": "M"},
                          {"Authorization": "Bearer " + tok})
            out = []
            for c in j.get("candles", []):
                if c.get("complete") and "mid" in c:
                    m = c["mid"]
                    out.append((_parse_oanda_time(c["time"]), float(m["o"]), float(m["h"]), float(m["l"]), float(m["c"])))
            return out
        raise ValueError("bilinmeyen market: " + market)

    def _okx(self):
        return str(self.env.get("CRYPTO_SOURCE", "binance")).lower() == "okx"

    def crypto_symbols(self):
        """Binance USDT-M surekli (perpetual) kontratlari; alinamazsa statik liste."""
        if self._okx():
            try:
                j = self._get(OKX + "/api/v5/public/instruments", {"instType": "SWAP"})
                live = {d["instId"][:-len("-USDT-SWAP")] + "USDT" for d in j.get("data", [])
                        if d.get("instId", "").endswith("-USDT-SWAP") and d.get("state") == "live"}
                if len(live) > 50:
                    top = [x for x in CRYPTO_FALLBACK if x in live]
                    return top + sorted(live - set(top))
            except Exception:
                pass
            return list(CRYPTO_FALLBACK)
        try:
            base = self.env.get("BINANCE_FAPI_BASE", FAPI)
            j = self._get(base + "/fapi/v1/exchangeInfo")
            syms = sorted(s["symbol"] for s in j["symbols"]
                          if s.get("contractType") == "PERPETUAL" and s.get("status") == "TRADING"
                          and s.get("quoteAsset") == "USDT" and s["symbol"].isascii())
            if len(syms) > 50:
                top = [s for s in CRYPTO_FALLBACK if s in set(syms)]
                rest = [s for s in syms if s not in set(top)]
                return top + rest
        except Exception:
            pass
        return list(CRYPTO_FALLBACK)
