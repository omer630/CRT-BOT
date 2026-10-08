"""CRT (Candle Range Theory) + CISD mantigi. Saf fonksiyonlar, ag/dosya yok.

Mum bicimi: (acilis_ms, open, high, low, close)  - eskiden yeniye, sadece KAPANMIS mumlar.

CRT:  C1 = aralik mumu, C2 = C1'in bir ucunu fitille supurup (sweep) C1 ARALIGINA
      GERI KAPATAN mum, C3 = C2'den sonraki mum (genisleme).
      Satis kurulumu: C2 C1 tepesini supurdu ve icerde kapandi. Alis: tersi.
CISD: Alt zaman diliminde, supurme yonundeki ardisik mumlarin (satis icin yukselen
      mumlar) en eskisinin ACILISININ diger tarafta kapanis. Sinyal bu mumun kapanisidir.
Hedefler: TP1 = C1 %50, TP2 = C1 karsi ucu. Stop = C2 ucunun hemen otesi.
"""
import ms_engine

TF_SEC = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400, "1w": 604800}
HTFS = ["1w", "1d", "4h", "1h", "15m"]
LTF_OF = {"1w": "4h", "1d": "1h", "4h": "15m", "1h": "5m", "15m": "1m"}
BIAS_OF = {"1w": "1w", "1d": "1w", "4h": "1d", "1h": "4h", "15m": "1h"}
T, O, H, L, C = range(5)


def tf_ms(tf):
    return TF_SEC[tf] * 1000


def detect_sweep(candles, req_inside=True):
    """Son kapanmis mum C2, bir oncesi C1. Gecerli sweep varsa sozluk, yoksa None."""
    if len(candles) < 2:
        return None
    c1, c2 = candles[-2], candles[-1]
    sell = c2[H] > c1[H] and c2[C] < c1[H] and (not req_inside or c2[C] >= c1[L])
    buy = c2[L] < c1[L] and c2[C] > c1[L] and (not req_inside or c2[C] <= c1[H])
    if sell == buy:                       # ikisi de yok ya da cift tarafli (belirsiz) -> sinyal yok
        return None
    return {"dir": -1 if sell else 1,
            "c1": [c1[T], c1[H], c1[L]], "c2": [c2[T], c2[H], c2[L], c2[C]]}


def find_cisd(ltf, start_i, d, min_open):
    """start_i'den itibaren CISD ara. d=-1 satis (yukselen mum serisi kirilir), d=+1 alis.
    Donus: (index, seviye) ya da None. min_open'dan once olusan CISD'ler sayilmaz ama seriyi sifirlar."""
    run_open = None
    prev_run = False
    for i in range(max(start_i, 0), len(ltf)):
        o, c = ltf[i][O], ltf[i][C]
        same = c > o if d < 0 else c < o
        if same:
            if not prev_run:
                run_open = o
            prev_run = True
        else:
            prev_run = False
            if run_open is not None:
                broke = c < run_open if d < 0 else c > run_open
                if broke:
                    lvl = run_open
                    run_open = None
                    if ltf[i][T] >= min_open:
                        return i, lvl
    return None


def c3_window(c2_open_ms, htf, market, forex_open=None):
    """C3 baslangic/bitis (ms). Forex'te hafta sonu kapanisini atlar."""
    start = c2_open_ms + tf_ms(htf)
    if market == "forex":
        start = _skip_weekend(start)
    return start, start + tf_ms(htf)


def _skip_weekend(ms):
    import datetime as _d
    t = _d.datetime.fromtimestamp(ms / 1000, _d.timezone.utc)
    wd, hr = t.weekday(), t.hour
    closed = (wd == 4 and hr >= 21) or wd == 5 or (wd == 6 and hr < 21)
    if not closed:
        return ms
    days = (6 - wd) % 7 if wd != 4 else 2
    base = (t + _d.timedelta(days=days)).replace(hour=21, minute=0, second=0, microsecond=0)
    return int(base.timestamp() * 1000)


def evaluate(setup, ltf, cfg, now_ms):
    """Kurulumu alt zaman dilimi mumlariyla degerlendir.
    ('signal', dict) | ('expired', neden) | ('invalid', neden) | ('wait', None)"""
    d = setup["dir"]
    c3s, c3e = setup["c3_start"], setup["c3_end"]
    c1h, c1l = setup["c1"][1], setup["c1"][2]
    c2h, c2l = setup["c2"][1], setup["c2"][2]
    ext = c2h if d < 0 else c2l
    tp1 = (c1h + c1l) / 2.0
    tp2 = c1l if d < 0 else c1h
    if now_ms >= c3e:
        return "expired", "C3 suresi bitti"
    if not ltf:
        return "wait", None
    # sweep ucunun bulundugu mum: C2 icindeki son 'ext' mumu
    start_i = None
    for i, b in enumerate(ltf):
        if b[T] < setup["c2"][0]:
            continue
        if b[T] >= c3s:
            break
        if (d < 0 and b[H] >= ext - 1e-12 * abs(ext)) or (d > 0 and b[L] <= ext + 1e-12 * abs(ext)):
            start_i = i
    if start_i is None:
        for i, b in enumerate(ltf):
            if b[T] >= c3s:
                start_i = i
                break
    if start_i is None:
        return "wait", None
    res = find_cisd(ltf, start_i, d, c3s)
    last = len(ltf) - 1
    # C3 icinde stop ya da hedef ihlali (CISD'den once ya da CISD mumunda)
    limit_i = res[0] if res else last
    for i in range(len(ltf)):
        b = ltf[i]
        if b[T] < c3s:
            continue
        if i > limit_i:
            break
        if (d < 0 and b[H] > ext) or (d > 0 and b[L] < ext):
            return "invalid", "C2 ucu asildi"
        if (d < 0 and b[L] <= tp1) or (d > 0 and b[H] >= tp1):
            return "invalid", "TP1 CISD'den once doldu"
    if not res:
        return "wait", None
    idx, lvl = res
    if last - idx > cfg["max_age_bars"]:
        return "expired", "CISD gec kaldi"
    entry = ltf[idx][C]
    buf = cfg["sl_buffer_pct"] / 100.0
    sl = ext * (1 + buf) if d < 0 else ext * (1 - buf)
    risk = (sl - entry) if d < 0 else (entry - sl)
    reward = (entry - tp1) if d < 0 else (tp1 - entry)
    if risk <= 0 or reward <= 0:
        return "invalid", "giris stop/TP1 arasinda degil"
    if reward / risk < cfg["min_rr"]:
        return "invalid", "RR yetersiz"
    return "signal", {"dir": d, "entry": entry, "sl": sl, "tp1": tp1, "tp2": tp2,
                      "cisd_level": lvl, "cisd_open": ltf[idx][T], "rr1": reward / risk,
                      "rr2": abs(tp2 - entry) / risk}


def bias_dir(candles):
    """Market yapisi motoru ile yon (+1/-1/0)."""
    if len(candles) < 30:
        return 0
    ev = ms_engine.run([c[O] for c in candles], [c[H] for c in candles],
                       [c[L] for c in candles], [c[C] for c in candles], 0.0)[0]
    d = 0
    for e in ev:
        if e[0] in ("INIT", "BOS", "MSB"):
            d = e[2]
    return d


def grade(sig_dir, mkt_dir):
    if mkt_dir == 0:
        return "unknown"
    return "with" if sig_dir == mkt_dir else "counter"


def track_trade(trade, ltf):
    """Giristen sonraki mumlarla TP/SL takibi. trade'i yerinde gunceller, degisenleri dondurur."""
    d = trade["dir"]
    ev = []
    for b in ltf:
        if b[T] <= trade["entry_open"] or b[T] <= trade.get("last_open", 0) or trade["closed"]:
            continue
        sl_hit = b[H] >= trade["sl"] if d < 0 else b[L] <= trade["sl"]
        t1 = b[L] <= trade["tp1"] if d < 0 else b[H] >= trade["tp1"]
        t2 = b[L] <= trade["tp2"] if d < 0 else b[H] >= trade["tp2"]
        if sl_hit:                                   # ayni mumda hem stop hem hedef: once stop (temkinli)
            trade["sl_hit"] = True
            trade["closed"] = True
            ev.append("sl")
        else:
            if t1 and not trade["tp1_hit"]:
                trade["tp1_hit"] = True
                ev.append("tp1")
            if t2:
                trade["tp2_hit"] = True
                trade["tp1_hit"] = True
                trade["closed"] = True
                ev.append("tp2")
        trade["last_open"] = b[T]
    return ev
