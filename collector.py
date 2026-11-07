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


def analyze(item, intr, daily, cfg, now_ts):
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
        "series": series,
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
]
POS = [
    "rekord", "rally", "erholung", "gewinne", "aufschwung", "zinssenkung", "einigung", "waffenruhe",
    "entspannung", "kursplus", "hochgestuft", "übertrifft", "erholt", "surge", "record", "rebound", "soar",
    "jump", "gains", "rate cut", "cuts rates", "deal", "beats", "upgrade", "ceasefire", "easing", "optimis",
]
THEMES = {
    "Zinsen & Notenbanken": ["zins*", "fed", "ezb", "notenbank*", "powell", "lagarde", "inflation*", "rate cut", "interest rate*"],
    "Handel & Zölle": ["zoll*", "zölle", "tariff*", "handelskrieg*", "trade war", "sanktion*", "sanctions"],
    "Geopolitik": ["krieg*", "war", "ukraine", "russland", "russia", "iran", "israel", "gaza", "nahost", "taiwan", "nato", "angriff*", "attack*", "ceasefire", "waffenruhe*"],
    "Konjunktur": ["konjunktur*", "rezession*", "recession", "arbeitsmarkt*", "jobs", "bip", "gdp", "pmi", "verbraucher*", "unemployment"],
    "Tech & KI": ["nvidia", "ki", "künstliche intelligenz", "ai", "chip*", "apple", "microsoft", "tesla", "alphabet", "amazon", "openai", "halbleiter*"],
    "Rohstoffe": ["öl", "ölpreis*", "oil", "gold", "goldpreis*", "opec", "gas", "erdgas", "energie*", "energy"],
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
    for feed in cfg["news_feeds"]:
        try:
            items = parse_feed(http_get(feed["url"], retries=2, max_bytes=2_000_000), feed["name"])
            collected.extend(items[:25])
            feeds.append({"name": feed["name"], "ok": True, "n": len(items[:25])})
        except Exception as exc:  # noqa: BLE001
            feeds.append({"name": feed["name"], "ok": False, "n": 0, "error": str(exc)[:120]})
    max_age = cfg["news_max_age_hours"] * 3600
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
    return {"items": out, "mood": mood, "themes": theme_count, "feeds": feeds}


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


def demo_news(now_ts):
    base = [
        ("Beispiel: Notenbank signalisiert Zinspause, Märkte reagieren gelassen", 0.0, ["Zinsen & Notenbanken"]),
        ("Beispiel: Neue Zölle belasten Technologiewerte", -0.5, ["Handel & Zölle", "Tech & KI"]),
        ("Beispiel: Rekordstände an der Wall Street nach starken Unternehmenszahlen", 0.7, ["Tech & KI"]),
        ("Beispiel: Spannungen im Nahen Osten treiben den Ölpreis", -0.6, ["Geopolitik", "Rohstoffe"]),
        ("Beispiel: Konjunkturdaten fallen besser aus als erwartet", 0.5, ["Konjunktur"]),
    ]
    items = [{"title": t, "url": "", "source": "Demo", "ts": now_ts - 1800 * (i + 1), "feed": "Demo",
              "sentiment": s, "themes": th} for i, (t, s, th) in enumerate(base)]
    return {"items": items, "mood": {"n": 5, "score": 0.1, "label": "neutral"},
            "themes": {"Tech & KI": 2, "Zinsen & Notenbanken": 1, "Handel & Zölle": 1, "Geopolitik": 1, "Rohstoffe": 1, "Konjunktur": 1},
            "feeds": [{"name": "Demo", "ok": True, "n": 5}]}


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


def main():
    ap = argparse.ArgumentParser(description="ETF-Bot Datensammler")
    ap.add_argument("--demo", action="store_true", help="Beispieldaten statt echter Kurse")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs"), help="Ausgabeordner")
    args = ap.parse_args()

    cfg = load_json(os.path.join(ROOT, "config.json"), None)
    if cfg is None:
        sys.exit("config.json fehlt oder ist kaputt")
    out_dir = args.out
    data_path = os.path.join(out_dir, "data.json")
    log_path = os.path.join(out_dir, "signal_log.json")

    prev = load_json(data_path, {})
    if prev.get("demo"):
        prev = {}
    log = load_json(log_path, {})
    entries = [] if (log.get("demo") or not isinstance(log.get("entries"), list)) else log["entries"]
    prev_syms = {s["symbol"]: s for s in prev.get("symbols", []) if isinstance(s, dict) and "symbol" in s}
    prev_zones = {k: v.get("zone") for k, v in prev_syms.items()}

    now_ts = int(time.time())
    symbols, raw = [], {}
    ok_count = 0
    for idx, item in enumerate(cfg["watchlist"]):
        try:
            if args.demo:
                intr, daily = demo_data(item, idx, now_ts)
            else:
                intr = fetch_yahoo(item["symbol"], cfg["intraday_interval"], cfg["intraday_range"])
                time.sleep(0.4)
                daily = fetch_yahoo(item["symbol"], "1d", cfg["daily_range"])
                time.sleep(0.4)
            res = analyze(item, intr, daily, cfg, now_ts)
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

    news = demo_news(now_ts) if args.demo else fetch_news(cfg, now_ts)
    market = {"vix": vix_info(cfg, args.demo, now_ts)}
    # Bei Ausfall nicht leer ueberschreiben, sondern die letzten guten Werte behalten
    if not args.demo:
        if not news["items"] and prev.get("news", {}).get("items"):
            keep = dict(prev["news"])
            keep["feeds"] = news["feeds"]
            news = keep
        old_vix = prev.get("market", {}).get("vix", {})
        if market["vix"].get("value") is None and old_vix.get("value") is not None:
            market["vix"] = dict(old_vix, stale=True)

    entries = update_log(entries, symbols, raw, prev_zones, now_ts) if not args.demo else []
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
        "market": market,
        "symbols": symbols,
        "news": news,
    }
    write_json(data_path, result)
    write_json(log_path, {"demo": bool(args.demo), "entries": entries})
    print(f"Fertig: {ok_count}/{len(cfg['watchlist'])} ETFs, {len(news['items'])} Meldungen -> {data_path}")


if __name__ == "__main__":
    main()
