#!/usr/bin/env python3
"""CRT + CISD Telegram sinyal botu.

Calistirma:   python bot.py            (surekli calisir; Telegram'dan /ayarlar ile yonetilir)
Deneme:       python bot.py --test     (Telegram'a deneme mesaji atar)
Ortam degiskenleri: TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, OANDA_TOKEN (forex icin), OANDA_ENV=practice|live
"""
import argparse
import json
import os
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import requests

import crt
import menu
import sources
from crt import BIAS_OF, LTF_OF, TF_SEC

HERE = os.path.dirname(os.path.abspath(__file__))
TFU = {"1w": "1W", "1d": "1D", "4h": "4H", "1h": "1H", "15m": "15m", "5m": "5m", "1m": "1m"}
SETTLE_SEC = 2
TRADE_TTL_MS = 21 * 86400 * 1000


# ------------------------------------------------------------- telegram
class TG:
    def __init__(self, token, chat_id, session=None, sleep=time.sleep):
        self.token, self.chat_id = token, str(chat_id)
        self.s = session or requests.Session()
        self.sleep = sleep

    def call(self, method, payload, timeout=20):
        url = f"https://api.telegram.org/bot{self.token}/{method}"
        for attempt in range(4):
            r = self.s.post(url, json=payload, timeout=timeout)
            if r.status_code == 429:
                wait = 1
                try:
                    wait = int(r.json().get("parameters", {}).get("retry_after", 1))
                except Exception:
                    pass
                self.sleep(min(wait, 30) + 1)
                continue
            if r.status_code >= 500:
                self.sleep(2 * (attempt + 1))
                continue
            j = {}
            try:
                j = r.json()
            except Exception:
                pass
            if r.status_code == 400 and "not modified" in str(j.get("description", "")):
                return None
            if r.status_code >= 400:
                raise RuntimeError(f"Telegram {method} {r.status_code}: {j.get('description')}")
            return j.get("result")
        raise RuntimeError(f"Telegram {method}: tekrar denemeler bitti")

    def send(self, text, reply_to=None, markup=None):
        p = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_to:
            p["reply_to_message_id"] = reply_to
            p["allow_sending_without_reply"] = True
        if markup:
            p["reply_markup"] = {"inline_keyboard": markup}
        res = self.call("sendMessage", p)
        return res["message_id"] if res else None

    def edit(self, msg_id, text, markup=None):
        p = {"chat_id": self.chat_id, "message_id": msg_id, "text": text, "parse_mode": "HTML",
             "disable_web_page_preview": True}
        p["reply_markup"] = {"inline_keyboard": markup or []}
        return self.call("editMessageText", p)

    def answer(self, cb_id, text=""):
        try:
            self.call("answerCallbackQuery", {"callback_query_id": cb_id, "text": text})
        except Exception:
            pass

    def updates(self, offset, timeout=5):
        return self.call("getUpdates", {"offset": offset, "timeout": timeout,
                                        "allowed_updates": ["message", "callback_query"]}, timeout=timeout + 15) or []

    def set_commands(self):
        cmds = [{"command": "ayarlar", "description": "Ayar menüsü"},
                {"command": "durum", "description": "Bot durumu"},
                {"command": "ekle", "description": "Parite ekle: /ekle BTCUSDT EURUSD"},
                {"command": "cikar", "description": "Parite çıkar: /cikar BTCUSDT"},
                {"command": "test", "description": "Bağlantı testi"},
                {"command": "yardim", "description": "Yardım"}]
        try:
            self.call("setMyCommands", {"commands": cmds})
        except Exception:
            pass


# --------------------------------------------------------------- mesaj
def fmt_px(p):
    a = abs(p)
    dec = 2 if a >= 1000 else 3 if a >= 100 else 4 if a >= 10 else 5 if a >= 0.1 else 6 if a >= 0.01 else 8
    return f"{p:.{dec}f}"


def format_trade(t):
    d = t["dir"]
    head = ("🟢 BUY" if d > 0 else "🔴 SELL") + f" #{t['disp']} | " + \
           {"with": "A+ Setup", "counter": "Ters Yön Setup"}.get(t["grade"], "Setup")
    mk = {1: "▲ Yükseliş", -1: "▼ Düşüş", 0: "— Belirsiz"}[t["bias_dir"]]
    uy = {"with": "uyumlu ✅", "counter": "ters ⚠️"}.get(t["grade"], "belirsiz")
    if t.get("expired"):
        durum = "⚪ Süre doldu"
    elif t["tp2_hit"]:
        durum = "🏆 TP2 tamamlandı"
    elif t["sl_hit"] and t["tp1_hit"]:
        durum = "🔴 TP1 sonrası stop (kapandı)"
    elif t["sl_hit"]:
        durum = "🔴 Stop oldu (kapandı)"
    elif t["tp1_hit"]:
        durum = "🟢 TP1 alındı · Aktif"
    else:
        durum = "🔵 Aktif"
    return "\n".join([
        head, f"{t['tv']} · {TFU[t['ltf']]}", "",
        f"Bias Mode: CRT {TFU[t['htf']]} + CISD {TFU[t['ltf']]}",
        f"Market yönü: {TFU[t['bias_tf']]} {mk} ({uy})", "",
        f"Market Entry: {fmt_px(t['entry'])} ✅",
        f"TP1: {fmt_px(t['tp1'])} {'✅' if t['tp1_hit'] else '▫️'}",
        f"TP2: {fmt_px(t['tp2'])} {'✅' if t['tp2_hit'] else '▫️'}",
        f"SL: {fmt_px(t['sl'])} {'❌' if t['sl_hit'] else '▫️'}", "",
        f"Durum: {durum}",
        *([f"⏱ Sinyal ~{t['late_min']} dk gecikmeli geldi, giriş fiyatı güncel olmayabilir."]
          if t.get("late_min", 0) >= 5 else []), "",
        "<i>Otomatik sinyaldir, yatırım tavsiyesi değildir.</i>"])


# ----------------------------------------------------------------- bot
class Bot:
    def __init__(self, tg, src, settings_path, state_path, log=print, workers=8):
        self.tg, self.src, self.log = tg, src, log
        self.sp, self.stp = settings_path, state_path
        self.settings = menu.load_settings(settings_path)
        self.state = self._load_state()
        self.cat = {"crypto": list(sources.CRYPTO_FALLBACK)}
        self.workers = workers
        self.cat_ts = 0
        self.late_min = 0          # >0: GitHub modu, CISD'nin bu kadar dakika gecikmesine izin ver

    # ---- durum dosyasi
    def _load_state(self):
        try:
            with open(self.stp, "r", encoding="utf-8") as f:
                st = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            st = {}
        for k, v in (("offset", 0), ("ticks", {}), ("setups", {}), ("trades", {}), ("seen", []), ("fail", {})):
            st.setdefault(k, v)
        return st

    def save(self):
        self.state["seen"] = self.state["seen"][-3000:]
        tmp = self.stp + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f)
        os.replace(tmp, self.stp)
        menu.save_settings(self.sp, self.settings)

    def refresh_catalog(self, now_ms, force=False):
        if force or now_ms - self.cat_ts > 86400 * 1000:
            self.cat["crypto"] = self.src.crypto_symbols()
            self.cat_ts = now_ms

    # ---- zamanlayici
    def _due(self, key, period, now_ms):
        sec = now_ms // 1000
        b = sec // period
        if self.state["ticks"].get(key) == b or sec % period < SETTLE_SEC:
            return False
        self.state["ticks"][key] = b
        return True

    def _skip(self, key, now_ms):
        f = self.state["fail"].get(key)
        return bool(f and f.get("until", 0) > now_ms)

    def _fail(self, key, now_ms, err):
        f = self.state["fail"].setdefault(key, {"n": 0, "until": 0})
        f["n"] += 1
        if f["n"] >= 5:
            f["until"] = now_ms + 6 * 3600 * 1000
            f["n"] = 0
            self.log(f"[UYARI] {key} 6 saat atlandi: {err}")

    def _ok(self, key):
        self.state["fail"].pop(key, None)

    def _fetch_many(self, items, tf, limit, now_ms):
        """items: [(market, sym)] -> {(market,sym): candles}. Hatalar sayilir, o parite atlanir."""
        out = {}

        def one(it):
            return self.src.fetch(it[0], it[1], tf, limit, now_ms)

        todo = [it for it in items if not self._skip(f"{it[0]}:{it[1]}", now_ms)]
        if self.workers > 1 and len(todo) > 1:
            with ThreadPoolExecutor(max_workers=self.workers) as ex:
                futs = [(it, ex.submit(one, it)) for it in todo]
                results = []
                for it, fu in futs:
                    try:
                        results.append((it, fu.result(), None))
                    except Exception as e:
                        results.append((it, None, e))
        else:
            results = []
            for it in todo:
                try:
                    results.append((it, one(it), None))
                except Exception as e:
                    results.append((it, None, e))
        for it, res, err in results:
            key = f"{it[0]}:{it[1]}"
            if err is not None:
                self._fail(key, now_ms, err)
            else:
                self._ok(key)
                out[it] = res
        return out

    def tick(self, now_ms):
        s = self.settings
        for htf in menu.active_htfs(s):
            if self._due("scan|" + htf, min(TF_SEC[htf], 3600), now_ms):
                self.scan_htf(htf, now_ms)
        ltfs = {x["ltf"] for x in self.state["setups"].values()} | \
               {x["ltf"] for x in self.state["trades"].values() if not x["closed"]}
        for ltf in sorted(ltfs):
            if self._due("mon|" + ltf, min(TF_SEC[ltf], 3600), now_ms):
                self.monitor(ltf, now_ms)

    # ---- CRT arama
    def scan_htf(self, htf, now_ms):
        items = menu.enabled_symbols(self.settings)
        data = self._fetch_many(items, htf, 6, now_ms)
        n = 0
        for (market, sym), candles in data.items():
            if not candles or candles[-1][0] + 2 * crt.tf_ms(htf) < now_ms:
                continue                                    # eski veri (piyasa kapali)
            sw = crt.detect_sweep(candles, self.settings["req_inside"])
            if not sw:
                continue
            sid = f"{market}:{sym}|{htf}|{sw['c2'][0]}|{sw['dir']}"
            if sid in self.state["setups"] or sid in self.state["seen"]:
                continue
            c3s, c3e = crt.c3_window(sw["c2"][0], htf, market)
            if now_ms >= c3e:
                continue
            self.state["setups"][sid] = {"id": sid, "market": market, "sym": sym, "htf": htf, "ltf": LTF_OF[htf],
                                         "dir": sw["dir"], "c1": sw["c1"], "c2": sw["c2"],
                                         "c3_start": c3s, "c3_end": c3e, "created": now_ms}
            self.state["seen"].append(sid)
            n += 1
        self.log(f"[TARAMA] {htf}: {len(data)} parite, yeni CRT: {n}")

    # ---- CISD izleme + islem takibi
    def monitor(self, ltf, now_ms):
        s = self.settings
        en = set(menu.enabled_symbols(s))
        active = set(menu.active_htfs(s))
        setups = [x for x in self.state["setups"].values() if x["ltf"] == ltf]
        trades = [x for x in self.state["trades"].values() if x["ltf"] == ltf and not x["closed"]]
        for x in list(setups):
            if (x["market"], x["sym"]) not in en or x["htf"] not in active:
                self.state["setups"].pop(x["id"], None)
                setups.remove(x)
        items = sorted({(x["market"], x["sym"]) for x in setups} | {(x["market"], x["sym"]) for x in trades})
        if not items:
            return
        data = self._fetch_many(items, ltf, 300, now_ms)
        age = 1
        if self.late_min > 0:
            age = max(1, -(-self.late_min * 60 // TF_SEC[ltf]))
        cfg = {"min_rr": s["min_rr"], "sl_buffer_pct": s["sl_buffer_pct"], "max_age_bars": age}
        for x in setups:
            candles = data.get((x["market"], x["sym"]))
            if candles is None:
                continue
            res, info = crt.evaluate(x, candles, cfg, now_ms)
            if res == "signal":
                self.state["setups"].pop(x["id"], None)
                self.emit_signal(x, info, now_ms)
            elif res in ("expired", "invalid"):
                self.state["setups"].pop(x["id"], None)
                self.log(f"[KURULUM] {x['id']} {res}: {info}")
        for t in trades:
            candles = data.get((t["market"], t["sym"]))
            if candles is None:
                if now_ms - t["created"] > TRADE_TTL_MS and not t["closed"]:
                    self._finish_expired(t)
                continue
            ev = crt.track_trade(t, candles)
            if now_ms - t["created"] > TRADE_TTL_MS and not t["closed"]:
                self._finish_expired(t)
                ev.append("expired")
            if ev:
                self._push_update(t, ev)

    def _finish_expired(self, t):
        t["expired"] = True
        t["closed"] = True

    def _push_update(self, t, ev):
        try:
            if t.get("msg_id"):
                self.tg.edit(t["msg_id"], format_trade(t))
            if self.settings["notify_updates"] and t.get("msg_id"):
                names = {"tp1": "TP1 ✅", "tp2": "TP2 ✅ 🏆", "sl": "Stop ❌", "expired": "Süre doldu"}
                self.tg.send(f"#{t['disp']} · " + " · ".join(names[e] for e in ev), reply_to=t["msg_id"])
        except Exception as e:
            self.log(f"[HATA] mesaj guncellenemedi: {e}")

    def emit_signal(self, x, info, now_ms):
        market, sym, htf = x["market"], x["sym"], x["htf"]
        md = 0
        try:
            bc = self.src.fetch(market, sym, BIAS_OF[htf], 500, now_ms)
            md = crt.bias_dir(bc)
        except Exception as e:
            self.log(f"[UYARI] market yonu alinamadi {sym}: {e}")
        g = crt.grade(info["dir"], md)
        mode = self.settings["direction"]
        if (mode == "with" and g != "with") or (mode == "counter" and g != "counter"):
            self.log(f"[FILTRE] {x['id']} yon filtresine takildi ({g})")
            return
        t = {"id": f"{x['id']}|{info['cisd_open']}", "market": market, "sym": sym,
             "disp": sources.display_name(market, sym), "tv": sources.tv_symbol(market, sym),
             "dir": info["dir"], "grade": g, "htf": htf, "ltf": x["ltf"], "bias_tf": BIAS_OF[htf], "bias_dir": md,
             "entry": info["entry"], "sl": info["sl"], "tp1": info["tp1"], "tp2": info["tp2"],
             "entry_open": info["cisd_open"], "created": now_ms, "msg_id": None,
             "late_min": max(0, int((now_ms - info["cisd_open"]) / 60000) - TF_SEC[x["ltf"]] // 60),
             "tp1_hit": False, "tp2_hit": False, "sl_hit": False, "closed": False, "expired": False}
        try:
            t["msg_id"] = self.tg.send(format_trade(t))
        except Exception as e:
            self.log(f"[HATA] sinyal gonderilemedi: {e}")
            return
        self.state["trades"][t["id"]] = t
        self.log(f"[SINYAL] {t['id']} {g}")
        # eski kapali islemleri temizle
        for k in [k for k, v in self.state["trades"].items() if v["closed"] and now_ms - v["created"] > TRADE_TTL_MS]:
            self.state["trades"].pop(k, None)

    # ---- Telegram komutlari / menu
    def status_text(self):
        st = self.state
        open_t = len([t for t in st["trades"].values() if not t["closed"]])
        return (menu.summary(self.settings, self.cat) +
                f"\n\nBekleyen CRT kurulumu: {len(st['setups'])}\nAçık işlem: {open_t}")

    def _find_symbol(self, tok):
        tok = tok.upper().replace("/", "")
        if tok in self.cat["crypto"]:
            return "crypto", tok
        if tok + "USDT" in self.cat["crypto"]:
            return "crypto", tok + "USDT"
        for f in sources.FOREX_ALL:
            if f == tok or f.replace("_", "") == tok:
                return "forex", f
        return None, None

    def handle_update(self, u):
        if "callback_query" in u:
            cq = u["callback_query"]
            msg = cq.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != self.tg.chat_id:
                self.tg.answer(cq["id"], "Yetkisiz")
                return
            view = menu.callback(cq.get("data", ""), self.settings, self.cat)
            if view == "close":
                self.tg.edit(msg["message_id"], "Menü kapatıldı. /ayarlar ile tekrar açabilirsin.")
            else:
                text, kb = menu.render(view, self.settings, self.cat)
                self.tg.edit(msg["message_id"], text, kb)
            self.tg.answer(cq["id"])
            self.save()
            return
        m = u.get("message")
        if not m or str(m.get("chat", {}).get("id")) != self.tg.chat_id:
            return
        text = (m.get("text") or "").strip()
        cmd, *args = text.split()
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/menu", "/ayarlar"):
            t, kb = menu.render("main", self.settings, self.cat)
            self.tg.send(t, markup=kb)
        elif cmd == "/durum":
            self.tg.send(self.status_text())
        elif cmd == "/test":
            self.tg.send("✅ Bot çalışıyor.")
        elif cmd in ("/ekle", "/cikar"):
            done, bad = [], []
            for a in args:
                mk, sy = self._find_symbol(a)
                if not sy:
                    bad.append(a)
                    continue
                lst = self.settings[mk]
                if cmd == "/ekle" and sy not in lst:
                    lst.append(sy)
                if cmd == "/cikar" and sy in lst:
                    lst.remove(sy)
                done.append(sy)
            self.tg.send(("✔ " + ", ".join(done) if done else "") + ("\n❓ Bulunamadı: " + ", ".join(bad) if bad else "")
                         or "Kullanım: /ekle BTCUSDT EURUSD")
            self.save()
        elif cmd == "/yardim":
            self.tg.send("/ayarlar – menü\n/durum – özet\n/ekle SEMBOL… – parite ekle\n/cikar SEMBOL… – parite çıkar\n/test")

    def run_once(self, now_ms=None):
        """GitHub Actions: tek tur. Bekleyen Telegram komutlarini isle, tara, kaydet."""
        now = now_ms or int(time.time() * 1000)
        try:
            self.refresh_catalog(now, force=True)
        except Exception as e:
            self.log(f"[HATA] katalog: {e}")
        if not self.state.get("greeted"):
            self.tg.set_commands()
            self.tg.send("🤖 CRT bot (GitHub modu) başladı. Ayarlar için /ayarlar")
            self.state["greeted"] = True
        try:
            for u in self.tg.updates(self.state["offset"], timeout=0):
                self.state["offset"] = u["update_id"] + 1
                try:
                    self.handle_update(u)
                except Exception:
                    self.log("[HATA] komut:\n" + traceback.format_exc())
        except Exception as e:
            self.log(f"[HATA] getUpdates: {e}")
        try:
            self.tick(now_ms or int(time.time() * 1000))
        except Exception:
            self.log("[HATA] tick:\n" + traceback.format_exc())
        self.save()

    def run_forever(self):
        now = int(time.time() * 1000)
        self.refresh_catalog(now, force=True)
        self.tg.set_commands()
        if not self.state.get("greeted"):
            self.tg.send("🤖 CRT bot başladı. Ayarlar için /ayarlar")
            self.state["greeted"] = True
        while True:
            now = int(time.time() * 1000)
            try:
                self.refresh_catalog(now)
                self.tick(now)
            except Exception:
                self.log("[HATA] tick:\n" + traceback.format_exc())
            try:
                for u in self.tg.updates(self.state["offset"], timeout=5):
                    self.state["offset"] = u["update_id"] + 1
                    try:
                        self.handle_update(u)
                    except Exception:
                        self.log("[HATA] komut:\n" + traceback.format_exc())
            except Exception as e:
                self.log(f"[HATA] getUpdates: {e}")
                time.sleep(5)
            self.save()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--once", action="store_true", help="tek tur calis (GitHub Actions)")
    a = ap.parse_args(argv)
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat:
        sys.exit("TELEGRAM_BOT_TOKEN ve TELEGRAM_CHAT_ID tanimli olmali")
    tg = TG(token, chat)
    if a.test:
        tg.send("✅ Bot bağlantısı çalışıyor.")
        print("Deneme mesaji gonderildi.")
        return 0
    bot = Bot(tg, sources.Sources(), os.path.join(HERE, "settings.json"), os.path.join(HERE, "state.json"))
    if a.once:
        bot.late_min = int(os.environ.get("LATE_MIN", "30"))
        bot.run_once()
        return 0
    bot.run_forever()


if __name__ == "__main__":
    sys.exit(main())
