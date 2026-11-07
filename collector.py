#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ETF-Bot - Datensammler und Analyse
==================================

Was dieses Programm tut (nur Python-Standardbibliothek, nichts zu installieren):

  1. Kurse holen (Yahoo Finance, kostenlos): 15-Minuten-Kurse und Tageskurse
  2. Kennzahlen berechnen (RSI, Bollinger-Baender, MACD, gleitende Durchschnitte)
  3. Daraus einen Hoch-/Tief-Wert von -100 (Hoch-Zone) bis +100 (Tief-Zone) bilden
  4. Pruefen, wie solche Signale in den letzten ~60 Tagen tatsaechlich ausgingen (Rueckblick)
  5. Nachrichten aus kostenlosen RSS-Feeds holen und grob nach Stimmung/Thema sortieren
  6. Alles in docs/data.json schreiben - die Dashboard-Seite liest nur diese Datei

Aufruf:
    python collector.py              # echte Daten
    python collector.py --demo       # Beispieldaten ohne Internet (zum Testen)
    python collector.py --out ordner # Ausgabeordner (Standard: docs)

Wichtig: Die Werte sind Rechenhilfen auf Basis vergangener Kurse. Sie sind keine
Vorhersage und keine Anlageberatung.
"""

import argparse
import json
import math
import os
import random
import re
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
UA = "Mozilla/5.0 (compatible; etf-bot/1.0; +https://github.com)"
BARS_PER_4H = 16  # bei 15-Minuten-Kerzen


# --------------------------------------------------------------------------
# Hilfsfunktionen: Netzwerk, Dateien
# --------------------------------------------------------------------------

def http_get(url, timeout=20, retries=3, max_bytes=4_000_000):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError("Antwort zu gross")
                return data
        except (urllib.error.URLError, TimeoutError, ValueError, ConnectionError) as exc:
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{type(last).__name__}: {last}")


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def rnd(x, n=2):
    return None if x is None else round(x, n)


# --------------------------------------------------------------------------
# Kursdaten holen
# --------------------------------------------------------------------------

def fetch_yahoo(symbol, interval, rng):
    """Liefert dict mit t (Unix-Zeit), o, h, l, c und meta. Wirft bei Fehler."""
    last = None
    for host in ("query1", "query2"):
        url = (
            f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}"
            f"?interval={interval}&range={rng}&includePrePost=false"
        )
        try:
            payload = json.loads(http_get(url, retries=2))
            chart = payload.get("chart", {})
            if chart.get("error"):
                raise RuntimeError(str(chart["error"].get("description", chart["error"])))
            res = chart["result"][0]
            q = res["indicators"]["quote"][0]
            out = {"t": [], "o": [], "h": [], "l": [], "c": []}
            for i, ts in enumerate(res.get("timestamp") or []):
                c = q["close"][i]
                if c is None:
                    continue
                out["t"].append(int(ts))
                out["c"].append(float(c))
                out["o"].append(float(q["open"][i] if q["open"][i] is not None else c))
                out["h"].append(float(q["high"][i] if q["high"][i] is not None else c))
                out["l"].append(float(q["low"][i] if q["low"][i] is not None else c))
            out["meta"] = res.get("meta", {})
            if not out["c"]:
                raise RuntimeError("keine Kurse erhalten")
            return out
        except Exception as exc:  # noqa: BLE001 - jeder Fehler -> naechster Host
            last = exc
    raise RuntimeError(f"Yahoo-Abruf fehlgeschlagen ({last})")


# --------------------------------------------------------------------------
# Kennzahlen (alle ohne Zusatzpakete)
# --------------------------------------------------------------------------

def sma(vals, n):
    out = [None] * len(vals)
    s = 0.0
    for i, v in enumerate(vals):
        s += v
        if i >= n:
            s -= vals[i - n]
        if i >= n - 1:
            out[i] = s / n
    return out


def ema(vals, n):
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    k = 2.0 / (n + 1)
    e = sum(vals[:n]) / n
    out[n - 1] = e
    for i in range(n, len(vals)):
        e = vals[i] * k + e * (1 - k)
        out[i] = e
    return out


def rsi(vals, n=14):
    """RSI nach Wilder: 0-100. Unter 30 gilt als ueberverkauft, ueber 70 als ueberkauft."""
    out = [None] * len(vals)
    if len(vals) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        d = vals[i] - vals[i - 1]
        gain += max(d, 0.0)
        loss += max(-d, 0.0)
    ag, al = gain / n, loss / n
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    for i in range(n + 1, len(vals)):
        d = vals[i] - vals[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1 + ag / al)
    return out


def bollinger(vals, n=20, k=2.0):
    mid = sma(vals, n)
    up = [None] * len(vals)
    lo = [None] * len(vals)
    for i in range(n - 1, len(vals)):
        window = vals[i - n + 1 : i + 1]
        m = mid[i]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / n)
        up[i] = m + k * sd
        lo[i] = m - k * sd
    return mid, up, lo


def macd_hist(vals, fast=12, slow=26, sig=9):
    ef, es = ema(vals, fast), ema(vals, slow)
    macd = [(a - b) if a is not None and b is not None else None for a, b in zip(ef, es)]
    first = next((i for i, v in enumerate(macd) if v is not None), None)
    hist = [None] * len(vals)
    if first is None:
        return hist
    valid = macd[first:]
    sg = ema(valid, sig)
    for j, s in enumerate(sg):
        if s is not None:
            hist[first + j] = valid[j] - s
    return hist


def clip(x, a=-1.0, b=1.0):
    return max(a, min(b, x))


# --------------------------------------------------------------------------
# Signal-Bewertung
# --------------------------------------------------------------------------
# Jede Teilbewertung liegt zwischen -1 (Hoch-Zone) und +1 (Tief-Zone) und hat ein Gewicht.
# Gesamtwert = gewichteter Durchschnitt * 100.

def rsi_text(r):
    if r is None:
        return "keine Daten"
    if r < 30:
        return "überverkauft"
    if r < 40:
        return "eher schwach"
    if r <= 60:
        return "neutral"
    if r <= 70:
        return "eher stark"
    return "überkauft"


def bb_text(b):
    if b < 0:
        return "unter dem unteren Band"
    if b < 0.2:
        return "nahe dem unteren Band"
    if b <= 0.8:
        return "im mittleren Bereich"
    if b <= 1:
        return "nahe dem oberen Band"
    return "über dem oberen Band"


def macd_part(hist, i):
    """Dreht der MACD gerade nach oben/unten? Gibt (wert, text) oder None."""
    if i < 1 or hist[i] is None or hist[i - 1] is None:
        return None
    h, d = hist[i], hist[i] - hist[i - 1]
    if h < 0 and d > 0:
        return 1.0, "dreht von unten nach oben"
    if h > 0 and d < 0:
        return -1.0, "dreht von oben nach unten"
    if h < 0:
        return 0.3, "fällt noch weiter"
    return -0.3, "steigt noch weiter"


def intraday_parts(i, rsi_a, up, lo, closes, hist):
    """Kurzfrist-Teil des Signals (15-Minuten-Kerzen). Liefert Liste (key, wert, gewicht, rohwert, text)."""
    parts = []
    r = rsi_a[i]
    if r is not None:
        parts.append(("rsi15", clip((50 - r) / 20), 25, r, rsi_text(r)))
    if up[i] is not None and up[i] > lo[i]:
        b = (closes[i] - lo[i]) / (up[i] - lo[i])
        parts.append(("bb15", clip((0.5 - b) * 2), 20, b, bb_text(b)))
    m = macd_part(hist, i)
    if m is not None:
        parts.append(("macd15", m[0], 15, hist[i], m[1]))
    return parts


def total_score(parts):
    w = sum(p[2] for p in parts)
    if w == 0:
        return None
    return 100.0 * sum(p[1] * p[2] for p in parts) / w


def zone_of(score, cfg):
    if score is None:
        return None, "Keine Daten"
    if score >= cfg["zone_strong"]:
        return "tief", "Tief-Zone"
    if score >= cfg["zone_soft"]:
        return None, "Eher tief"
    if score <= -cfg["zone_strong"]:
        return "hoch", "Hoch-Zone"
    if score <= -cfg["zone_soft"]:
        return None, "Eher hoch"
    return None, "Neutral"


def day_index(ts, off):
    return (ts + off) // 86400


def backtest(t, c, off, scores, threshold, horizons, cost_pct, cooldown=8):
    """Wie sah der Kurs nach frueheren Tief-Signalen am selben Handelstag aus?
    Vergleich: Durchschnitt ueber ALLE Zeitpunkte (Basis). Ein Signal ist nur dann nuetzlich,
    wenn es deutlich besser ist als die Basis."""
    days = [day_index(x, off) for x in t]
    result = {}
    for h in horizons:
        sig, base = [], []
        last_sig = -10**9
        for i in range(len(c) - h):
            if days[i + h] != days[i]:
                continue
            r = (c[i + h] / c[i] - 1.0) * 100.0
            base.append(r)
            s = scores[i]
            # Abstand = Haltedauer, damit sich zwei Signale nicht ueberlappen
            if s is not None and s >= threshold and i - last_sig >= max(cooldown, h):
                sig.append(r)
                last_sig = i
        n = len(sig)
        avg = statistics.fmean(sig) if sig else None
        bavg = statistics.fmean(base) if base else None
        # Zufallstest: Weicht der Signal-Schnitt klar vom Schnitt aller Zeitpunkte ab?
        z = None
        if n >= 5 and bavg is not None:
            sd = statistics.pstdev(sig)
            if sd > 0:
                z = (avg - bavg) / (sd / math.sqrt(n))
        if n < 20:
            urteil = "zu wenige Signale für eine Aussage"
        elif z is not None and z >= 2:
            urteil = "besser als der Durchschnitt (nur Vergangenheit, keine Garantie)"
        elif z is not None and z <= -2:
            urteil = "schlechter als der Durchschnitt"
        else:
            urteil = "kein Unterschied zum Zufall erkennbar"
        result[str(h)] = {
            "stunden": h / 4,
            "signale": n,
            "schnitt_pct": rnd(avg, 3),
            "trefferquote_pct": rnd(100.0 * sum(1 for r in sig if r > 0) / n, 1) if n else None,
            "basis_schnitt_pct": rnd(bavg, 3),
            "nach_kosten_pct": rnd(avg - cost_pct, 3) if avg is not None else None,
            "z": rnd(z, 2),
            "urteil": urteil,
        }
    return result


def langfrist(daily, price, trend):
    """Kennzahlen fuer die lange Sicht (aus Tageskursen). Ordnet nur ein, keine Empfehlung."""
    dc, dh, dl = daily["c"], daily["h"], daily["l"]
    n = len(dc)

    def ret(days):
        return (dc[-1] / dc[-1 - days] - 1.0) * 100.0 if n > days else None

    y = min(n, 252)
    window = dc[-y:]
    hi52, lo52 = max(dh[-y:]), min(dl[-y:])
    peak, mdd = window[0], 0.0
    for x in window:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1.0)
    rets = [math.log(window[i] / window[i - 1]) for i in range(1, len(window))]
    vol = statistics.pstdev(rets) * math.sqrt(252) * 100.0 if len(rets) > 20 else None
    dd = min(0.0, (price / hi52 - 1.0) * 100.0)
    if trend == "Abwärtstrend":
        lage, text = "Schwäche", "Abwärtstrend: Der Kurs liegt unter dem 200-Tage-Schnitt."
    elif dd <= -15:
        lage, text = "Korrektur", "Mehr als 15 % unter dem 52-Wochen-Hoch."
    elif dd <= -5:
        lage, text = "Rücksetzer", "Zwischen 5 und 15 % unter dem 52-Wochen-Hoch."
    elif trend == "Aufwärtstrend":
        lage, text = "intakt", "Aufwärtstrend, nahe am 52-Wochen-Hoch."
    else:
        lage, text = "seitwärts", "Kein klarer Trend, nahe am 52-Wochen-Hoch."
    return {
        "lage": lage, "text": text, "trend": trend,
        "r1m_pct": rnd(ret(21), 2), "r3m_pct": rnd(ret(63), 2), "r6m_pct": rnd(ret(126), 2),
        "r1j_pct": rnd((dc[-1] / dc[-y] - 1.0) * 100.0, 2) if n >= 240 else None,
        "abstand_hoch_pct": rnd(dd, 2), "hoch_52w": rnd(hi52, 3), "tief_52w": rnd(lo52, 3),
        "vol_pa_pct": rnd(vol, 1), "max_rueckgang_pct": rnd(mdd * 100.0, 1),
    }


def build_charts(t, c, off, daily):
    """Reihen fuer die laengeren Diagramm-Zeitraeume, nur aus schon geladenen Daten."""
    last = t[-1]
    # 1 Monat: Kurse zur vollen Stunde (Zeit am Handelsplatz) plus der letzte Kurs
    cut = last - 31 * 86400
    ks = [k for k in range(len(t)) if t[k] >= cut and (t[k] + off) % 3600 == 0]
    if not ks or ks[-1] != len(t) - 1:
        ks.append(len(t) - 1)
    out = {"1m": {"t": [t[k] for k in ks], "c": [rnd(c[k], 4) for k in ks]}}
    dt, dc = daily["t"], daily["c"]
    for key, days in (("6m", 183), ("1y", 366)):
        cut = last - days * 86400
        ds = [k for k in range(len(dt)) if dt[k] >= cut]
        out[key] = {"t": [dt[k] for k in ds], "c": [rnd(dc[k], 4) for k in ds]}
    return out


def max_series(item, daily, demo, prev_chart):
    """Gesamter Kursverlauf in Wochenkerzen. Bei Ausfall bleibt der letzte gute Verlauf stehen."""
    try:
        if demo:
            d = {"t": daily["t"][::-5][::-1], "c": daily["c"][::-5][::-1]}
        else:
            d = fetch_yahoo(item["symbol"], "1wk", "max")
            time.sleep(0.4)
        if len(d["c"]) < 3:
            raise RuntimeError("zu wenig Daten")
        return {"t": d["t"], "c": [rnd(x, 4) for x in d["c"]]}
    except Exception as exc:  # noqa: BLE001
        print(f"Hinweis: Gesamtverlauf für {item['symbol']} fehlt ({str(exc)[:120]})", file=sys.stderr)
        return (prev_chart or {}).get("max")


def analyze(item, intr, daily, cfg, now_ts, with_series=True):
    t, c, h, l = intr["t"], intr["c"], intr["h"], intr["l"]
    meta = intr["meta"]
    off = int(meta.get("gmtoffset", 0))
    if len(c) < 80 or len(daily["c"]) < 40:
        raise ValueError("zu wenig Kursdaten für die Berechnung")

    rs = rsi(c)
    mid, up, lo = bollinger(c)
    hist = macd_hist(c)
    n = len(c)
    i = n - 1

    # ---- Kurzfrist-Teil
    parts = intraday_parts(i, rs, up, lo, c, hist)
    names = {"rsi15": "RSI (15 Min)", "bb15": "Bollinger-Position (15 Min)", "macd15": "MACD-Richtung (15 Min)"}
    comps = [
        {"name": names[k], "effekt": rnd(v, 2), "gewicht": w, "wert": rnd(raw, 3), "text": txt}
        for k, v, w, raw, txt in parts
    ]

    # ---- Tages-Teil (Kontext)
    dc, dh, dl = daily["c"], daily["h"], daily["l"]
    rsd = rsi(dc)
    full = list(parts)
    if rsd[-1] is not None:
        full.append(("rsi_d", clip((50 - rsd[-1]) / 20), 15, rsd[-1], rsi_text(rsd[-1])))
        comps.append({"name": "RSI (Tag)", "effekt": rnd(full[-1][1], 2), "gewicht": 15,
                      "wert": rnd(rsd[-1], 1), "text": rsi_text(rsd[-1])})
    hi20, lo20 = max(dh[-20:]), min(dl[-20:])
    price = c[-1]
    pos20 = (price - lo20) / (hi20 - lo20) if hi20 > lo20 else 0.5
    p20_text = ("nahe dem 20-Tage-Tief" if pos20 < 0.2 else "nahe dem 20-Tage-Hoch" if pos20 > 0.8
                else "im mittleren Bereich der letzten 20 Tage")
    full.append(("pos20", clip((0.5 - pos20) * 2), 10, pos20, p20_text))
    comps.append({"name": "Lage in der 20-Tage-Spanne", "effekt": rnd(full[-1][1], 2), "gewicht": 10,
                  "wert": rnd(pos20, 3), "text": p20_text})

    score = total_score(full)
    zone, label = zone_of(score, cfg)

    # ---- Trend
    s50 = sma(dc, 50)[-1]
    s200 = sma(dc, 200)[-1]
    last_d = dc[-1]
    if s50 is not None and s200 is not None:
        trend = ("Aufwärtstrend" if last_d > s200 and s50 > s200
                 else "Abwärtstrend" if last_d < s200 and s50 < s200 else "Seitwärts / Übergang")
    elif s50 is not None:
        trend = "Aufwärtstrend" if last_d > s50 else "Abwärtstrend"
    else:
        trend = "unklar"

    # ---- Tagesveraenderung
    last_day = day_index(t[-1], off)
    prev_close = None
    for ts, cl in zip(daily["t"], dc):
        if day_index(ts, off) < last_day:
            prev_close = cl
    day_change = (price / prev_close - 1.0) * 100.0 if prev_close else None

    # ---- typische Schwankung der naechsten 4 Stunden (nur Statistik, keine Richtung)
    days_all = [day_index(x, off) for x in t]
    recent_start = max(0, n - 5 * 34)
    rets = [math.log(c[k] / c[k - 1]) for k in range(max(1, recent_start), n) if days_all[k] == days_all[k - 1]]
    vol4h = statistics.pstdev(rets) * math.sqrt(BARS_PER_4H) * 100.0 if len(rets) > 20 else None

    # ---- Rueckblick (Backtest) mit dem Kurzfrist-Teil
    scores_hist = [None] * n
    for k in range(n):
        scores_hist[k] = total_score(intraday_parts(k, rs, up, lo, c, hist)) if k >= 35 else None
    cost_pct = 2.0 * cfg["order_fee_eur"] / cfg["trade_amount_eur"] * 100.0
    bt = backtest(t, c, off, scores_hist, cfg["backtest_signal_score"], cfg["backtest_horizons_bars"], cost_pct)

    # ---- Reihe fuer das Diagramm: die letzten 5 Handelstage
    uniq = sorted(set(days_all))
    first_day = uniq[-5] if len(uniq) >= 5 else uniq[0]
    start = next(k for k, d in enumerate(days_all) if d >= first_day)
    series = {
        "t": t[start:],
        "c": [rnd(x, 4) for x in c[start:]],
        "u": [rnd(x, 4) for x in up[start:]],
        "l": [rnd(x, 4) for x in lo[start:]],
        "rsi": [rnd(x, 1) for x in rs[start:]],
    }

    reg = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    return {
        "symbol": item["symbol"],
        "name": item["name"],
        "group": item.get("group", ""),
        "currency": meta.get("currency", ""),
        "price": rnd(price, 4),
        "change_pct_day": rnd(day_change, 2),
        "last_bar_ts": t[-1],
        "gmtoffset": off,
        "session_start": reg.get("start"),
        "session_end": reg.get("end"),
        "score": rnd(score, 1),
        "zone": zone,
        "label": label,
        "trend": trend,
        "komponenten": comps,
        "kennzahlen": {
            "rsi15": rnd(rs[-1], 1),
            "rsi_tag": rnd(rsd[-1], 1),
            "pct_b": rnd((c[-1] - lo[-1]) / (up[-1] - lo[-1]), 3) if up[-1] and up[-1] > lo[-1] else None,
            "sma50": rnd(s50, 3),
            "sma200": rnd(s200, 3),
            "tief_20d": rnd(lo20, 3),
            "hoch_20d": rnd(hi20, 3),
            "lage_20d": rnd(pos20, 3),
            "schwankung_4h_pct": rnd(vol4h, 2),
        },
        "rueckblick": bt,
        "series": series if with_series else None,
        "charts": build_charts(t, c, off, daily) if with_series else None,
        "langfrist": langfrist(daily, price, trend),
        "error": None,
        "stale": False,
    }


# --------------------------------------------------------------------------
# Nachrichten
# --------------------------------------------------------------------------

NEG = [
    "crash", "einbruch", "einbrüche", "verlust", "verluste", "rezession", "zölle", "strafzoll", "handelskrieg",
    "krieg", "angriff", "eskalation", "sanktion", "kurssturz", "absturz", "verkaufswelle", "sorgen", "angst",
    "krise", "pleite", "insolvenz", "herabgestuft", "warnt", "warnung", "shutdown", "zinserhöhung", "schwäche",
    "plunge", "selloff", "sell-off", "slump", "tumble", "fears", "recession", "tariff", "attack", "escalat",
    "sanctions", "downgrade", "warns", "crisis", "bankrupt", "layoff", "default", "losses", "worst",
    "exportkontroll", "embargo", "verbot", "strafe", "haushaltsstreit", "kartellverfahren", "export control",
]
POS = [
    "rekord", "rally", "erholung", "gewinne", "aufschwung", "zinssenkung", "einigung", "waffenruhe",
    "entspannung", "kursplus", "hochgestuft", "übertrifft", "erholt", "surge", "record", "rebound", "soar",
    "jump", "gains", "rate cut", "cuts rates", "deal", "beats", "upgrade", "ceasefire", "easing", "optimis",
    "steuersenkung", "entlastung", "konjunkturpaket", "großauftrag", "milliardenauftrag",
]
THEMES = {
    "Zinsen & Notenbanken": ["zins*", "fed", "ezb", "notenbank*", "powell", "lagarde", "inflation*", "rate cut", "interest rate*"],
    "Handel & Zölle": ["zoll*", "zölle*", "tariff*", "handelskrieg*", "trade war", "sanktion*", "sanctions"],
    "Geopolitik": ["krieg*", "war", "ukraine", "russland", "russia", "iran", "israel", "gaza", "nahost", "nahen osten", "naher osten", "middle east", "taiwan", "nato", "angriff*", "attack*", "ceasefire", "waffenruhe*"],
    "Konjunktur": ["konjunktur*", "rezession*", "recession", "arbeitsmarkt*", "jobs", "bip", "gdp", "pmi", "verbraucher*", "unemployment"],
    "Tech & KI": ["nvidia", "ki", "künstliche intelligenz", "ai", "chip*", "apple", "microsoft", "tesla", "alphabet", "amazon", "openai", "halbleiter*"],
    "Rohstoffe": ["öl", "ölpreis*", "oil", "gold", "goldpreis*", "opec", "gas", "erdgas", "energie*", "energy"],
    "Politik & Regulierung": ["regier*", "wahl*", "wähler*", "koalition*", "kongress", "congress", "senat", "weißes haus", "white house",
                              "trump", "haushalt*", "schuldenbremse", "steuer*", "subvention*", "gesetz*",
                              "verbot*", "bundestag", "parlament*", "präsident*", "election*", "shutdown"],
    "Regulierung & Kartellrecht": ["regulier*", "regulat*", "kartell*", "antitrust", "monopol*", "eu-kommission", "strafzahlung*"],
    "Exportkontrollen & China": ["exportkontroll*", "export control*", "exportverbot*", "embargo*", "china", "chinesisch*", "peking",
                                 "beijing", "seltene erden", "rare earth*", "chipverbot*", "huawei"],
    "Rüstung & Verteidigung": ["rüstung*", "verteidigung*", "bundeswehr", "defense", "defence", "nato", "militär*",
                               "waffenlieferung*", "drohne*", "rheinmetall"],
}

# Welche Werte ein Thema betreffen koennte (grobe Faustregel, keine Prognose)
THEME_WERTE = {
    "Handel & Zölle": (["TSLA", "MBG.DE", "SIE.DE", "AAPL", "NVDA", "AMD", "AVGO", "MU", "INTC"],
                       "Zölle treffen vor allem Autobauer sowie Chip- und Elektronikkonzerne mit weltweiten Lieferketten."),
    "Exportkontrollen & China": (["NVDA", "AMD", "AVGO", "MU", "INTC", "AAPL", "TSLA"],
                                 "Exportregeln und der China-Handel betreffen vor allem Chipfirmen und Konzerne mit großem China-Geschäft."),
    "Rüstung & Verteidigung": (["RHM.DE", "PLTR"], "Höhere Verteidigungsausgaben kommen vor allem Rüstungsfirmen zugute."),
    "Regulierung & Kartellrecht": (["GOOGL", "META", "AMZN", "MSFT", "AAPL", "JPM", "DBK.DE"],
                                   "Regulierung und Kartellrecht betreffen vor allem Tech-Giganten und Banken."),
    "Geopolitik": (["RHM.DE"], "Konflikte und Spannungen wirken oft auf Rüstungswerte."),
}
_NEG_RE = [re.compile(r"\b" + re.escape(w), re.I) for w in NEG]
_POS_RE = [re.compile(r"\b" + re.escape(w), re.I) for w in POS]


def _theme_regex(words):
    parts = [re.escape(w[:-1]) if w.endswith("*") else re.escape(w) + r"\b" for w in words]
    return re.compile(r"\b(?:" + "|".join(parts) + ")", re.I)


_THEME_RE = {k: _theme_regex(v) for k, v in THEMES.items()}


def headline_sentiment(title):
    neg = sum(1 for r in _NEG_RE if r.search(title))
    pos = sum(1 for r in _POS_RE if r.search(title))
    if neg + pos == 0:
        return 0.0
    return round((pos - neg) / (pos + neg), 2)


def headline_themes(title):
    return [k for k, r in _THEME_RE.items() if r.search(title)]


def known_symbols(cfg):
    return {x["symbol"] for x in list(cfg.get("aktien_universum", [])) + list(cfg.get("watchlist", []))}


def company_patterns(cfg):
    """Kuerzel -> Regex aus den 'stichworte' von Aktien und Watchlist (ganze Woerter, Gross/Klein egal)."""
    pats = {}
    for x in list(cfg.get("aktien_universum", [])) + list(cfg.get("watchlist", [])):
        words = x.get("stichworte")
        if words:
            pats[x["symbol"]] = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")\b", re.I)
    return pats


def tag_item(it, pats, known):
    """werte = in der Schlagzeile genannt; werte_thema = koennte ueber das Thema betroffen sein."""
    genannt = [sym for sym, r in pats.items() if r.search(it["title"])]
    thema = []
    for th in it.get("themes", []):
        for sym in THEME_WERTE.get(th, ([], ""))[0]:
            if sym in known and sym not in genannt and sym not in thema:
                thema.append(sym)
    it["werte"] = genannt
    it["werte_thema"] = thema


def themen_info(cfg):
    known = known_symbols(cfg)
    return {th: {"werte": [x for x in w if x in known], "warum": why} for th, (w, why) in THEME_WERTE.items()}


def clean_text(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = re.sub(r"&amp;", "&", s)
    s = re.sub(r"&quot;|&#34;", '"', s)
    s = re.sub(r"&#39;|&apos;", "'", s)
    return re.sub(r"\s+", " ", s).strip()


def parse_feed(xml_bytes, feed_name):
    # Kein DOCTYPE/ENTITY zulassen (Schutz vor Entity-Bomben in fremden Feeds)
    head = xml_bytes[:4096].lower()
    if b"<!doctype" in head or b"<!entity" in xml_bytes[:20000].lower():
        raise ValueError("Feed enthält DOCTYPE/ENTITY, wird aus Sicherheitsgründen ignoriert")
    root = ET.fromstring(xml_bytes)
    items = []
    for it in root.iter("item"):
        title = clean_text(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        if not title or not link.startswith(("http://", "https://")):
            continue
        source = clean_text(it.findtext("source")) or feed_name
        if source and title.endswith(" - " + source):
            title = title[: -len(source) - 3].strip()
        try:
            dt = parsedate_to_datetime(it.findtext("pubDate") or "")
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            continue
        items.append({"title": title[:220], "url": link, "source": source[:60], "ts": int(dt.timestamp()), "feed": feed_name})
    return items


def fetch_news(cfg, now_ts):
    collected, feeds = [], []
    only_topic = {f["name"] for f in cfg["news_feeds"] if f.get("nur_mit_thema")}
    for feed in cfg["news_feeds"]:
        try:
            items = parse_feed(http_get(feed["url"], retries=2, max_bytes=2_000_000), feed["name"])
            collected.extend(items[:25])
            feeds.append({"name": feed["name"], "ok": True, "n": len(items[:25])})
        except Exception as exc:  # noqa: BLE001
            feeds.append({"name": feed["name"], "ok": False, "n": 0, "error": str(exc)[:120]})
    max_age = cfg["news_max_age_hours"] * 3600
    pats, known = company_patterns(cfg), known_symbols(cfg)
    seen, out = set(), []
    for it in sorted(collected, key=lambda x: -x["ts"]):
        if now_ts - it["ts"] > max_age or it["ts"] > now_ts + 3600:
            continue
        key = re.sub(r"\W+", " ", it["title"].lower())[:80]
        if key in seen:
            continue
        seen.add(key)
        it["sentiment"] = headline_sentiment(it["title"])
        it["themes"] = headline_themes(it["title"])
        tag_item(it, pats, known)
        # Allgemeine Quellen (z. B. Tagesschau Inland): nur Meldungen mit Themen- oder Aktien-Bezug
        if it["feed"] in only_topic and not it["themes"] and not it["werte"]:
            continue
        out.append(it)
    out = out[: cfg["news_max_items"]]
    recent = [x["sentiment"] for x in out if now_ts - x["ts"] <= 24 * 3600]
    mood = {"n": len(recent), "score": None, "label": "zu wenige Meldungen"}
    if len(recent) >= 5:
        avg = statistics.fmean(recent)
        mood["score"] = round(avg, 2)
        mood["label"] = "eher positiv" if avg > 0.12 else "eher angespannt" if avg < -0.12 else "neutral"
    theme_count = {}
    for x in out:
        for th in x["themes"]:
            theme_count[th] = theme_count.get(th, 0) + 1
    return {"items": out, "mood": mood, "themes": theme_count, "feeds": feeds, "themen_info": themen_info(cfg)}


def build_news_doc(cfg, demo, now_ts, prev_doc):
    """Inhalt von news.json. Faellt jede Quelle aus, bleibt der letzte gute Stand stehen."""
    news = demo_news(now_ts, cfg) if demo else fetch_news(cfg, now_ts)
    old = (prev_doc or {}).get("news") or {}
    if not demo and not news["items"] and old.get("items"):
        news = dict(old, feeds=news["feeds"], themen_info=news["themen_info"])
    return {"generated_at": iso(now_ts), "demo": bool(demo), "news": news}


# --------------------------------------------------------------------------
# Signal-Protokoll (Papierhandel): was passierte NACH einem Signal wirklich?
# --------------------------------------------------------------------------

def forward_return(t, c, off, ts0, price0, hours, now_ts):
    target = ts0 + int(hours * 3600)
    if now_ts < target + 1800:
        return None
    for ti, ci in zip(t, c):
        if target <= ti <= target + 3600:
            return (ci / price0 - 1.0) * 100.0
    d0 = day_index(ts0, off)
    same = [(ti, ci) for ti, ci in zip(t, c) if day_index(ti, off) == d0 and ti >= ts0]
    if same and same[-1][0] < target:
        return (same[-1][1] / price0 - 1.0) * 100.0  # bis Handelsschluss
    return None


def update_log(log_entries, symbols, raw, prev_zones, now_ts):
    for s in symbols:
        if s.get("error") or s.get("stale") or s["zone"] is None:
            continue
        if prev_zones.get(s["symbol"]) != s["zone"]:
            log_entries.append({
                "ts": s["last_bar_ts"], "symbol": s["symbol"], "name": s["name"], "typ": s["zone"],
                "score": s["score"], "kurs": s["price"], "ret_2h_pct": None, "ret_4h_pct": None,
            })
    for e in log_entries:
        series = raw.get(e["symbol"])
        if not series:
            continue
        t, c, off = series
        if e["ret_2h_pct"] is None:
            r = forward_return(t, c, off, e["ts"], e["kurs"], 2, now_ts)
            e["ret_2h_pct"] = rnd(r, 3)
        if e["ret_4h_pct"] is None:
            r = forward_return(t, c, off, e["ts"], e["kurs"], 4, now_ts)
            e["ret_4h_pct"] = rnd(r, 3)
    return log_entries[-300:]


# --------------------------------------------------------------------------
# Beispieldaten (ohne Internet)
# --------------------------------------------------------------------------

def demo_data(item, index, now_ts):
    rng = random.Random(sum(map(ord, item["symbol"])))
    off = 3600
    # Handelstage rueckwaerts (Mo-Fr)
    days, d = [], datetime.fromtimestamp(now_ts, timezone.utc).date()
    while len(days) < 520:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    price = rng.uniform(40, 700)
    closes = []
    for _ in days:
        price *= 1 + rng.gauss(0.0004, 0.009)
        closes.append(price)
    daily = {"t": [], "o": [], "h": [], "l": [], "c": []}
    for day, cl in zip(days, closes):
        ts = int(datetime(day.year, day.month, day.day, 8, 0, tzinfo=timezone.utc).timestamp())
        daily["t"].append(ts)
        daily["c"].append(cl)
        daily["o"].append(cl)
        daily["h"].append(cl * (1 + abs(rng.gauss(0, 0.004))))
        daily["l"].append(cl * (1 - abs(rng.gauss(0, 0.004))))
    intr = {"t": [], "o": [], "h": [], "l": [], "c": []}
    noise = 0.0
    for di in range(len(days) - 41, len(days)):
        day = days[di]
        open_p = closes[di - 1]
        close_p = closes[di]
        start = datetime(day.year, day.month, day.day, 8, 0, tzinfo=timezone.utc)
        for k in range(34):
            ts = int((start + timedelta(minutes=15 * k)).timestamp())
            base = open_p + (close_p - open_p) * (k + 1) / 34
            noise += -0.3 * noise + rng.gauss(0, 0.0014)
            p = close_p if k == 33 else base * (1 + noise)
            intr["t"].append(ts)
            intr["c"].append(p)
            intr["o"].append(p)
            intr["h"].append(p * 1.0006)
            intr["l"].append(p * 0.9994)
    # damit beide Zonen sichtbar sind: erstes ETF faellt, drittes steigt am Ende
    twist = {0: -1, 2: +1}.get(index)
    if twist:
        for k in range(1, 13):
            intr["c"][-k] *= 1 + twist * 0.03 * (13 - k) / 12
        intr["c"][-1] = intr["c"][-1]
    last_day = days[-1]
    s0 = int(datetime(last_day.year, last_day.month, last_day.day, 8, 0, tzinfo=timezone.utc).timestamp())
    intr["meta"] = {"gmtoffset": off, "currency": "EUR" if item["symbol"].endswith(".DE") else "USD",
                    "currentTradingPeriod": {"regular": {"start": s0, "end": s0 + 8.5 * 3600}}}
    daily["meta"] = intr["meta"]
    return intr, daily


def demo_news(now_ts, cfg):
    titles = [
        "Beispiel: Notenbank signalisiert Zinspause, Märkte reagieren gelassen",
        "Beispiel: Neue Zölle belasten Autobauer und Technologiewerte",
        "Beispiel: Nvidia warnt vor strengeren Exportkontrollen für China",
        "Beispiel: Bundestag beschließt höhere Verteidigungsausgaben, Rheinmetall im Plus",
        "Beispiel: EU-Kommission eröffnet Kartellverfahren gegen Tech-Konzerne",
        "Beispiel: Spannungen im Nahen Osten treiben den Ölpreis",
        "Beispiel: Konjunkturdaten fallen besser aus als erwartet",
    ]
    pats, known = company_patterns(cfg), known_symbols(cfg)
    items = []
    for i, t in enumerate(titles):
        it = {"title": t, "url": "", "source": "Demo", "ts": now_ts - 1800 * (i + 1), "feed": "Demo",
              "sentiment": headline_sentiment(t), "themes": headline_themes(t)}
        tag_item(it, pats, known)
        items.append(it)
    theme_count = {}
    for it in items:
        for th in it["themes"]:
            theme_count[th] = theme_count.get(th, 0) + 1
    return {"items": items, "mood": {"n": len(items), "score": 0.0, "label": "neutral"}, "themes": theme_count,
            "feeds": [{"name": "Demo", "ok": True, "n": len(items)}], "themen_info": themen_info(cfg)}


# --------------------------------------------------------------------------
# Hauptprogramm
# --------------------------------------------------------------------------

def vix_info(cfg, demo, now_ts):
    try:
        if demo:
            return {"value": 17.4, "change_pct": -1.2, "level": "normal"}
        d = fetch_yahoo(cfg["vix_symbol"], "1d", "1mo")
        v = d["c"][-1]
        ch = (v / d["c"][-2] - 1.0) * 100.0 if len(d["c"]) > 1 else None
        level = "niedrig" if v < 15 else "normal" if v < 20 else "erhöht" if v < 30 else "hoch"
        return {"value": rnd(v, 2), "change_pct": rnd(ch, 2), "level": level}
    except Exception as exc:  # noqa: BLE001
        return {"value": None, "change_pct": None, "level": None, "error": str(exc)[:120]}


def fetch_symbol(item, cfg, demo, idx, now_ts, daily_range):
    if demo:
        return demo_data(item, idx, now_ts)
    intr = fetch_yahoo(item["symbol"], cfg["intraday_interval"], cfg["intraday_range"])
    time.sleep(0.4)
    daily = fetch_yahoo(item["symbol"], "1d", daily_range)
    time.sleep(0.4)
    return intr, daily


def fx_rate(cfg, demo, prev):
    """Wechselkurs: wie viele US-Dollar ein Euro kostet. Bei Ausfall der letzte bekannte Wert."""
    if demo:
        return 1.16
    try:
        r = fetch_yahoo(cfg.get("fx_symbol", "EURUSD=X"), "1d", "5d")["c"][-1]
        if 0.5 < r < 2.0:
            return r
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER Wechselkurs: {str(exc)[:150]}", file=sys.stderr)
    return (prev.get("fx") or {}).get("eurusd")


def scan_stocks(cfg, by_sym, raw, prev_aktien, demo, now_ts, budget_s=420):
    """Durchsucht die Aktienliste mit derselben Bewertung wie die ETFs.
    Werte, die schon in der Beobachtungsliste sind, werden wiederverwendet (spart Abrufe)."""
    start = time.time()
    out, errors = [], 0
    for idx, item in enumerate(cfg.get("aktien_universum", [])):
        sym = item["symbol"]
        if sym in by_sym:
            r = dict(by_sym[sym])
            r["series"] = None
            r["charts"] = None
            r["name"], r["group"] = item["name"], item.get("group", "")
            out.append(r)
            continue
        try:
            if time.time() - start > budget_s:
                raise RuntimeError("Zeitbudget aufgebraucht")
            intr, daily = fetch_symbol(item, cfg, demo, 100 + idx, now_ts, cfg.get("aktien_daily_range", "1y"))
            res = analyze(item, intr, daily, cfg, now_ts, with_series=False)
            raw[sym] = (intr["t"], intr["c"], res["gmtoffset"])
            out.append(res)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            msg = str(exc)[:150]
            print(f"FEHLER (Aktie) {sym}: {msg}", file=sys.stderr)
            old = prev_aktien.get(sym)
            if old and old.get("price") is not None:
                old = dict(old)
                old["stale"], old["error"] = True, msg
                out.append(old)
    return out, errors


def main():
    ap = argparse.ArgumentParser(description="ETF-Bot Datensammler")
    ap.add_argument("--demo", action="store_true", help="Beispieldaten statt echter Kurse")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs"), help="Ausgabeordner")
    ap.add_argument("--news-only", action="store_true", help="Nur die Nachrichten (news.json) aktualisieren")
    args = ap.parse_args()

    cfg = load_json(os.path.join(ROOT, "config.json"), None)
    if cfg is None:
        sys.exit("config.json fehlt oder ist kaputt")
    out_dir = args.out
    data_path = os.path.join(out_dir, "data.json")
    log_path = os.path.join(out_dir, "signal_log.json")
    charts_path = os.path.join(out_dir, "charts.json")
    news_path = os.path.join(out_dir, "news.json")

    prev_news = load_json(news_path, {})
    if prev_news.get("demo"):
        prev_news = {}
    if args.news_only:
        now_ts = int(time.time())
        doc = build_news_doc(cfg, args.demo, now_ts, prev_news)
        write_json(news_path, doc)
        feeds = doc["news"].get("feeds", [])
        print(f"Nachrichten: {len(doc['news']['items'])} Meldungen, Quellen ok {sum(1 for f in feeds if f['ok'])}/{len(feeds)} -> {news_path}")
        for f in feeds:
            if not f["ok"]:
                print(f"Hinweis: Quelle '{f['name']}' nicht erreichbar ({f.get('error', '')})", file=sys.stderr)
        return

    prev = load_json(data_path, {})
    if prev.get("demo"):
        prev = {}
    prev_charts = load_json(charts_path, {})
    prev_charts = {} if (prev_charts.get("demo") or not isinstance(prev_charts.get("symbols"), dict)) else prev_charts["symbols"]
    log = load_json(log_path, {})
    entries = [] if (log.get("demo") or not isinstance(log.get("entries"), list)) else log["entries"]
    prev_syms = {s["symbol"]: s for s in prev.get("symbols", []) if isinstance(s, dict) and "symbol" in s}
    prev_aktien = {s["symbol"]: s for s in prev.get("aktien", []) if isinstance(s, dict) and "symbol" in s}
    prev_zones = {k: v.get("zone") for k, v in prev_aktien.items()}
    prev_zones.update({k: v.get("zone") for k, v in prev_syms.items()})

    now_ts = int(time.time())
    symbols, raw = [], {}
    ok_count = 0
    for idx, item in enumerate(cfg["watchlist"]):
        try:
            intr, daily = fetch_symbol(item, cfg, args.demo, idx, now_ts, cfg["daily_range"])
            res = analyze(item, intr, daily, cfg, now_ts)
            res["charts"]["max"] = max_series(item, daily, args.demo, prev_charts.get(item["symbol"]))
            raw[item["symbol"]] = (intr["t"], intr["c"], res["gmtoffset"])
            symbols.append(res)
            ok_count += 1
            print(f"OK    {item['symbol']:9s} Kurs {res['price']:>10} Wert {res['score']:>6} {res['label']}")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)[:200]
            print(f"FEHLER {item['symbol']}: {msg}", file=sys.stderr)
            old = prev_syms.get(item["symbol"])
            if old and not old.get("error"):
                old = dict(old)
                old["stale"] = True
                old["error"] = msg
                symbols.append(old)
            else:
                symbols.append({"symbol": item["symbol"], "name": item["name"], "group": item.get("group", ""),
                                "error": msg, "stale": False, "zone": None, "label": "Keine Daten", "score": None})

    # Die laengeren Verlaeufe kommen in eine eigene Datei, damit data.json klein bleibt
    charts = {}
    for res in symbols:
        ch = res.pop("charts", None) or prev_charts.get(res["symbol"])
        if ch:
            charts[res["symbol"]] = ch
    by_sym = {s["symbol"]: s for s in symbols if s.get("price") is not None}
    aktien, aktien_err = scan_stocks(cfg, by_sym, raw, prev_aktien, args.demo, now_ts)
    print(f"Aktien: {len(aktien)} von {len(cfg.get('aktien_universum', []))} ausgewertet, {aktien_err} Fehler")

    # Euro-Umrechnung fuer alles, was in Dollar notiert
    fx = fx_rate(cfg, args.demo, prev)
    for res in symbols + aktien:
        if res.get("price") is None:
            continue
        if res.get("currency") == "EUR":
            res["price_eur"] = res["price"]
        elif res.get("currency") == "USD" and fx:
            res["price_eur"] = rnd(res["price"] / fx, 2)

    news_doc = build_news_doc(cfg, args.demo, now_ts, prev_news)
    news = news_doc["news"]
    market = {"vix": vix_info(cfg, args.demo, now_ts)}
    # Bei Ausfall nicht leer ueberschreiben, sondern die letzten guten Werte behalten
    if not args.demo:
        old_vix = prev.get("market", {}).get("vix", {})
        if market["vix"].get("value") is None and old_vix.get("value") is not None:
            market["vix"] = dict(old_vix, stale=True)

    log_syms = symbols + [a for a in aktien if a["symbol"] not in by_sym]
    entries = update_log(entries, log_syms, raw, prev_zones, now_ts) if not args.demo else []
    if args.demo:
        # ein paar Beispiel-Eintraege, damit die Tabelle im Dashboard nicht leer ist
        for k, (sym, typ, r2, r4) in enumerate([("SXR8.DE", "tief", 0.21, 0.34), ("EUNL.DE", "hoch", -0.08, 0.05),
                                                 ("QDVE.DE", "tief", -0.12, 0.18)]):
            s = next((x for x in symbols if x["symbol"] == sym), None)
            if s and not s.get("error"):
                entries.append({"ts": now_ts - 86400 * (k + 1), "symbol": sym, "name": s["name"], "typ": typ,
                                "score": 58.0 if typ == "tief" else -61.0, "kurs": s["price"],
                                "ret_2h_pct": r2, "ret_4h_pct": r4})

    cost_pct = 2.0 * cfg["order_fee_eur"] / cfg["trade_amount_eur"] * 100.0
    result = {
        "generated_at": iso(now_ts),
        "demo": bool(args.demo),
        "ok_symbols": ok_count,
        "settings": {
            "order_fee_eur": cfg["order_fee_eur"],
            "trade_amount_eur": cfg["trade_amount_eur"],
            "round_trip_cost_pct": rnd(cost_pct, 2),
            "zone_strong": cfg["zone_strong"],
            "backtest_signal_score": cfg["backtest_signal_score"],
        },
        "fx": {"eurusd": rnd(fx, 4) if fx else None},
        "market": market,
        "symbols": symbols,
        "aktien": aktien,
        "aktien_status": {"gesamt": len(cfg.get("aktien_universum", [])), "ausgewertet": len(aktien), "fehler": aktien_err},
    }
    for res in aktien:
        res.pop("charts", None)
    write_json(data_path, result)
    write_json(news_path, news_doc)
    write_json(charts_path, {"generated_at": iso(now_ts), "demo": bool(args.demo), "symbols": charts})
    write_json(log_path, {"demo": bool(args.demo), "entries": entries})
    print(f"Fertig: {ok_count}/{len(cfg['watchlist'])} ETFs, {len(aktien)} Aktien, {len(news['items'])} Meldungen -> {data_path}")


if __name__ == "__main__":
    main()
