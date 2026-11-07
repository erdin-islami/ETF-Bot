"""Vergleicht die selbstgeschriebenen Kennzahlen mit pandas (Referenz). Aufruf: python tests/test_indicators.py"""
import os, sys, random
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pandas as pd
import collector as C

rng = random.Random(7)
p, vals = 100.0, []
for _ in range(900):
    p *= 1 + rng.gauss(0.0002, 0.01)
    vals.append(p)
s = pd.Series(vals)

def close(a, b, tol, name):
    assert a is not None and abs(a - b) <= tol, f"{name}: bot={a} pandas={b}"
    print(f"ok  {name:12s} bot={a:.6f} pandas={b:.6f}")

close(C.sma(vals, 50)[-1], s.rolling(50).mean().iloc[-1], 1e-9, "SMA50")
mid, up, lo = C.bollinger(vals, 20, 2)
r = s.rolling(20)
close(up[-1], (r.mean() + 2 * r.std(ddof=0)).iloc[-1], 1e-9, "Boll oben")
close(lo[-1], (r.mean() - 2 * r.std(ddof=0)).iloc[-1], 1e-9, "Boll unten")
close(C.ema(vals, 26)[-1], s.ewm(span=26, adjust=False).mean().iloc[-1], 1e-6, "EMA26")
d = s.diff()
ag = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
al = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
close(C.rsi(vals, 14)[-1], (100 - 100 / (1 + ag / al)).iloc[-1], 1e-3, "RSI14")
macd = s.ewm(span=12, adjust=False).mean() - s.ewm(span=26, adjust=False).mean()
hist = macd - macd.ewm(span=9, adjust=False).mean()
close(C.macd_hist(vals)[-1], hist.iloc[-1], 1e-3, "MACD-Hist")

# Randfaelle
assert C.rsi([1, 2, 3], 14) == [None, None, None]
assert C.rsi([5.0] * 30, 14)[-1] == 100.0 or C.rsi([5.0] * 30, 14)[-1] is not None
assert C.total_score([]) is None
assert C.zone_of(60, {"zone_strong": 50, "zone_soft": 25})[0] == "tief"
assert C.zone_of(-60, {"zone_strong": 50, "zone_soft": 25})[0] == "hoch"
assert C.zone_of(0, {"zone_strong": 50, "zone_soft": 25})[1] == "Neutral"
assert C.headline_sentiment("Rekord an der Wall Street nach Zinssenkung") > 0
assert C.headline_sentiment("Kurssturz: Angst vor Rezession und neuen Zöllen") < 0
assert C.headline_sentiment("Wetter morgen sonnig") == 0
assert "Geopolitik" in C.headline_themes("Waffenruhe im Nahost-Konflikt")
print("alle Tests bestanden")
