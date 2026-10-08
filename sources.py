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


YAHOO_MAP = {
    "XAU_USD": "GC=F", "XAG_USD": "SI=F", "XPT_USD": "PL=F", "XPD_USD": "PA=F", "XCU_USD": "HG=F",
    "SPX500_USD": "ES=F", "NAS100_USD": "NQ=F", "US30_USD": "YM=F", "US2000_USD": "RTY=F",
    "DE30_EUR": "^GDAXI", "UK100_GBP": "^FTSE", "FR40_EUR": "^FCHI", "EU50_EUR": "^STOXX50E",
    "JP225_USD": "^N225", "HK33_HKD": "^HSI", "AU200_AUD": "^AXJO", "NL25_EUR": "^AEX",
    "CH20_CHF": "^SSMI", "ES35_EUR": "^IBEX",
    "WTICO_USD": "CL=F", "BCO_USD": "BZ=F", "NATGAS_USD": "NG=F",
}
HOUR = 3600_000


def yahoo_ticker(instr):
    if instr in YAHOO_MAP:
        return YAHOO_MAP[instr]
    a, _, b = instr.partition("_")
    if len(a) == 3 and len(b) == 3:
        return f"{a}{b}=X"
    raise ValueError("Yahoo karsiligi yok: " + instr)


def _ny_offset_ms(ts_ms):
    from zoneinfo import ZoneInfo
    d = datetime.fromtimestamp(ts_ms / 1000, ZoneInfo("America/New_York"))
    return int(d.utcoffset().total_seconds() * 1000)


def aggregate_ny(bars, dur_ms, weekly=False):
    """1s mumlarindan OANDA tarzi (New York 17:00 hizali) 4s / 1G / 1H mumlar uret."""
    out = {}
    for b in bars:
        off = _ny_offset_ms(b[0])
        adj = b[0] + off - 17 * HOUR                      # 17:00 NY = gun baslangici
        if weekly:
            day = adj // 86400_000
            start_adj = ((day + 4) // 7 * 7 - 4) * 86400_000      # Pazar baslangicli hafta
        else:
            start_adj = adj // dur_ms * dur_ms
        start = start_adj + 17 * HOUR - _ny_offset_ms(start_adj + 17 * HOUR)
        cur = out.get(start)
        if cur is None:
            out[start] = [start, b[1], b[2], b[3], b[4]]
        else:
            cur[2] = max(cur[2], b[2]); cur[3] = min(cur[3], b[3]); cur[4] = b[4]
    return [tuple(v) for _, v in sorted(out.items())]


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
        if market == "forex" and not self.env.get("OANDA_TOKEN", ""):
            return self._yahoo(symbol, tf, limit, now_ms)
        if market == "forex":
            tok = self.env.get("OANDA_TOKEN", "")
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

    def _yahoo_raw(self, ticker, interval, rng):
        j = self._get("https://query1.finance.yahoo.com/v8/finance/chart/" + ticker,
                      {"interval": interval, "range": rng, "includePrePost": "false"},
                      {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"})
        res = (j.get("chart") or {}).get("result")
        if not res:
            raise RuntimeError("Yahoo: veri yok " + ticker)
        r = res[0]
        q = r["indicators"]["quote"][0]
        out = []
        for i, t in enumerate(r.get("timestamp") or []):
            o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
            if None in (o, h, l, c):
                continue
            out.append((int(t) * 1000, float(o), float(h), float(l), float(c)))
        return out

    def _yahoo(self, instr, tf, limit, now_ms):
        """Hesapsiz forex/metal/endeks verisi (Yahoo Finance, resmi olmayan uc nokta)."""
        tk = yahoo_ticker(instr)
        dur = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}[tf] * 1000
        if tf in ("1m", "5m", "15m"):
            bars = self._yahoo_raw(tk, tf, "5d" if tf == "1m" else "1mo")
        elif tf == "1h":
            bars = self._yahoo_raw(tk, "60m", "3mo")
        elif tf == "4h":
            bars = aggregate_ny(self._yahoo_raw(tk, "60m", "6mo"), dur)
        elif tf == "1d":
            bars = aggregate_ny(self._yahoo_raw(tk, "60m", "2y"), dur)
        else:
            bars = aggregate_ny(self._yahoo_raw(tk, "60m", "2y"), dur, weekly=True)
        bars = [b for b in bars if b[0] + dur <= now_ms]
        return bars[-limit:]

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
