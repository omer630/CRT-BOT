"""Ayarlar: varsayilanlar, kalicilik ve Telegram satir-ici (inline) menu."""
import copy
import json
import os

import sources
from crt import HTFS, LTF_OF

PAGE = 12
TF_LABEL = {"1w": "Haftalık", "1d": "Günlük", "4h": "4 Saatlik", "1h": "1 Saatlik", "15m": "15 Dakika"}
DIR_LABEL = {"with": "Market yönünde", "counter": "Ters yönde", "all": "Hepsi"}
RR_STEPS = [0.8, 1.0, 1.5, 2.0]
BUF_STEPS = [0.0, 0.05, 0.1, 0.2, 0.5]

DEFAULTS = {
    "markets": {"crypto": True, "forex": False},
    "htf": {tf: True for tf in HTFS},
    "direction": "all",
    "crypto": [s for s in sources.CRYPTO_FALLBACK[:30]],
    "forex": ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CHF", "AUD_USD", "USD_CAD", "NZD_USD", "XAU_USD", "XAG_USD",
              "SPX500_USD", "NAS100_USD", "US30_USD"],
    "min_rr": 1.0,
    "sl_buffer_pct": 0.05,
    "notify_updates": True,
    "req_inside": True,
}


def load_settings(path):
    st = copy.deepcopy(DEFAULTS)
    try:
        with open(path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        for k, v in saved.items():
            if k in ("markets", "htf") and isinstance(v, dict):
                st[k].update(v)
            else:
                st[k] = v
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return st


def save_settings(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)


def enabled_symbols(st):
    """[(market, symbol)] - acik marketlerdeki secili pariteler."""
    out = []
    if st["markets"].get("crypto"):
        out += [("crypto", s) for s in st["crypto"]]
    if st["markets"].get("forex"):
        out += [("forex", s) for s in st["forex"]]
    return out


def active_htfs(st):
    return [tf for tf in HTFS if st["htf"].get(tf)]


def _ck(b):
    return "✅" if b else "❌"


def _btn(t, d):
    assert len(d.encode()) <= 64, d
    return {"text": t, "callback_data": d}


def _fx_group(gid):
    for g, name, lst in sources.FOREX_GROUPS:
        if g == gid:
            return name, lst
    raise KeyError(gid)


def summary(st, cat):
    tfs = " ".join(tf for tf in active_htfs(st)) or "—"
    nc = len([s for s in st["crypto"] if s in set(cat["crypto"])]) if cat.get("crypto") else len(st["crypto"])
    nf = len([s for s in st["forex"] if s in sources.FOREX_ALL])
    lines = ["<b>⚙️ CRT Bot Ayarları</b>", "",
             f"Kripto (Binance vadeli): {_ck(st['markets']['crypto'])}  ·  seçili {nc}/{len(cat['crypto'])}",
             f"Forex/Metal/Endeks (OANDA): {_ck(st['markets']['forex'])}  ·  seçili {nf}/{len(sources.FOREX_ALL)}",
             f"CRT zaman dilimleri: {tfs}",
             f"Sinyal yönü: {DIR_LABEL[st['direction']]}",
             f"Min RR (TP1): {st['min_rr']:g}  ·  SL payı: %{st['sl_buffer_pct']:g}",
             f"TP/SL güncelleme bildirimi: {_ck(st['notify_updates'])}"]
    return "\n".join(lines)


def render(view, st, cat):
    """(metin, klavye) dondurur."""
    p = view.split("|")
    back = [_btn("◀ Ana menü", "v|main")]
    if p[0] == "main":
        nc = len(st["crypto"])
        nf = len(st["forex"])
        kb = [[_btn(f"Kripto {_ck(st['markets']['crypto'])}", "xm|crypto"),
               _btn(f"Forex {_ck(st['markets']['forex'])}", "xm|forex")],
              [_btn(f"Kripto pariteleri ({nc})", "v|pc|0"), _btn(f"Forex pariteleri ({nf})", "v|fx")],
              [_btn("Zaman dilimleri", "v|tf"), _btn(f"Yön: {DIR_LABEL[st['direction']]}", "v|dir")],
              [_btn("Gelişmiş", "v|adv"), _btn("Durum", "v|stat")],
              [_btn("Kapat", "v|close")]]
        return summary(st, cat), kb
    if p[0] == "tf":
        kb = [[_btn(f"{TF_LABEL[tf]} → CISD {LTF_OF[tf]} {_ck(st['htf'][tf])}", f"xt|{tf}")] for tf in HTFS]
        return "<b>CRT aranacak zaman dilimleri</b>\nHer CRT için onay (CISD) zaman dilimi yanında yazıyor.", kb + [back]
    if p[0] == "dir":
        kb = [[_btn(("● " if st["direction"] == k else "○ ") + v, f"xd|{k}")] for k, v in DIR_LABEL.items()]
        txt = ("<b>Sinyal yönü</b>\nMarket yönü: CRT'nin bir üst zaman diliminde market yapısı yönü.\n"
               "• Market yönünde: yalnızca A+ (uyumlu) sinyaller\n• Ters yönde: yalnızca market yönüne ters\n• Hepsi")
        return txt, kb + [back]
    if p[0] == "pc":
        page = int(p[1])
        lst = cat["crypto"]
        pages = max(1, -(-len(lst) // PAGE))
        page = min(max(page, 0), pages - 1)
        chunk = lst[page * PAGE:(page + 1) * PAGE]
        on = set(st["crypto"])
        kb = []
        for i in range(0, len(chunk), 2):
            kb.append([_btn(f"{'✅' if s in on else '⬜'} {s[:-4] if s.endswith('USDT') else s}", f"xc|{s}|{page}")
                       for s in chunk[i:i + 2]])
        kb.append([_btn("◀", f"v|pc|{max(page - 1, 0)}"), _btn(f"{page + 1}/{pages}", f"v|pc|{page}"),
                   _btn("▶", f"v|pc|{min(page + 1, pages - 1)}")])
        kb.append([_btn("Sayfayı aç", f"xa|c|{page}|1"), _btn("Sayfayı kapat", f"xa|c|{page}|0")])
        kb.append([_btn("Tümünü aç", "xa|c|*|1"), _btn("Tümünü kapat", "xa|c|*|0")])
        return f"<b>Kripto pariteleri</b> (Binance USDT vadeli)\nSeçili: {len(st['crypto'])}/{len(lst)}", kb + [back]
    if p[0] == "fx":
        kb = []
        for g, name, lst in sources.FOREX_GROUPS:
            n = len([s for s in lst if s in set(st["forex"])])
            kb.append([_btn(f"{name} ({n}/{len(lst)})", f"v|fg|{g}|0")])
        kb.append([_btn("Hepsini aç", "xa|f|*|1"), _btn("Hepsini kapat", "xa|f|*|0")])
        return f"<b>Forex / Metal / Endeks</b>\nSeçili: {len(st['forex'])}/{len(sources.FOREX_ALL)}", kb + [back]
    if p[0] == "fg":
        gid, page = p[1], int(p[2])
        name, lst = _fx_group(gid)
        pages = max(1, -(-len(lst) // PAGE))
        page = min(max(page, 0), pages - 1)
        chunk = lst[page * PAGE:(page + 1) * PAGE]
        on = set(st["forex"])
        kb = []
        for i in range(0, len(chunk), 2):
            kb.append([_btn(f"{'✅' if s in on else '⬜'} {sources.display_name('forex', s)}", f"xf|{s}|{gid}|{page}")
                       for s in chunk[i:i + 2]])
        if pages > 1:
            kb.append([_btn("◀", f"v|fg|{gid}|{max(page - 1, 0)}"), _btn(f"{page + 1}/{pages}", f"v|fg|{gid}|{page}"),
                       _btn("▶", f"v|fg|{gid}|{min(page + 1, pages - 1)}")])
        kb.append([_btn("Grubu aç", f"xa|f|{gid}|1"), _btn("Grubu kapat", f"xa|f|{gid}|0")])
        return f"<b>{name}</b>", kb + [[_btn("◀ Forex", "v|fx")], back]
    if p[0] == "adv":
        kb = [[_btn(f"Min RR (TP1): {st['min_rr']:g}", "xr|rr")],
              [_btn(f"SL payı: %{st['sl_buffer_pct']:g}", "xr|sl")],
              [_btn(f"TP/SL bildirimi {_ck(st['notify_updates'])}", "xr|notify")],
              [_btn(f"C2 C1 içinde kapanmalı {_ck(st['req_inside'])}", "xr|inside")]]
        return "<b>Gelişmiş</b>\nDeğere basınca sıradaki seçeneğe geçer.", kb + [back]
    if p[0] == "stat":
        return summary(st, cat), [back]
    return summary(st, cat), [[_btn("Menü", "v|main")]]


def _cycle(steps, cur):
    try:
        return steps[(steps.index(cur) + 1) % len(steps)]
    except ValueError:
        return steps[0]


def callback(data, st, cat):
    """Dugme verisini isler, ayarlari gunceller. Gosterilecek yeni gorunumu dondurur
    ('close' ise menu kapatilir)."""
    p = data.split("|")
    k = p[0]
    if k == "v":
        return "|".join(p[1:])
    if k == "xm":
        st["markets"][p[1]] = not st["markets"][p[1]]
        return "main"
    if k == "xt":
        st["htf"][p[1]] = not st["htf"][p[1]]
        return "tf"
    if k == "xd" and p[1] in DIR_LABEL:
        st["direction"] = p[1]
        return "dir"
    if k == "xc":
        _toggle(st["crypto"], p[1])
        return f"pc|{p[2]}"
    if k == "xf":
        _toggle(st["forex"], p[1])
        return f"fg|{p[2]}|{p[3]}"
    if k == "xa":
        on = p[3] == "1"
        if p[1] == "c":
            if p[2] == "*":
                items = list(cat["crypto"])
            else:
                page = int(p[2])
                items = cat["crypto"][page * PAGE:(page + 1) * PAGE]
            _bulk(st["crypto"], items, on)
            return "pc|0" if p[2] == "*" else f"pc|{p[2]}"
        if p[2] == "*":
            _bulk(st["forex"], sources.FOREX_ALL, on)
            return "fx"
        _bulk(st["forex"], _fx_group(p[2])[1], on)
        return f"fg|{p[2]}|0"
    if k == "xr":
        if p[1] == "rr":
            st["min_rr"] = _cycle(RR_STEPS, st["min_rr"])
        elif p[1] == "sl":
            st["sl_buffer_pct"] = _cycle(BUF_STEPS, st["sl_buffer_pct"])
        elif p[1] == "notify":
            st["notify_updates"] = not st["notify_updates"]
        elif p[1] == "inside":
            st["req_inside"] = not st["req_inside"]
        return "adv"
    return "main"


def _toggle(lst, s):
    if s in lst:
        lst.remove(s)
    else:
        lst.append(s)


def _bulk(lst, items, on):
    for s in items:
        if on and s not in lst:
            lst.append(s)
        if not on and s in lst:
            lst.remove(s)
