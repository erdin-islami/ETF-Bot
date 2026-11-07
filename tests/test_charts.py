"""Selbsttest fuer die laengeren Diagramm-Zeitraeume (ohne Internet)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import collector  # noqa: E402

DAY = 86400
off = 3600
# 40 Tage lang 15-Minuten-Kerzen, 09:00 bis 17:15 (lokal), nur Werktage
start = 1_760_000_000 - (1_760_000_000 % DAY)
t, c = [], []
for d in range(40):
    day0 = start + d * DAY
    if ((day0 // DAY) + 4) % 7 in (5, 6):  # Sa, So ueberspringen
        continue
    for q in range(34):
        t.append(day0 + 8 * 3600 + q * 900)  # 09:00 lokal = 08:00 UTC bei off=3600
        c.append(100.0 + len(c) * 0.01)
daily = {"t": [t[-1] - (399 - k) * DAY for k in range(400)], "c": [50.0 + k * 0.1 for k in range(400)]}

ch = collector.build_charts(t, c, off, daily)
last = t[-1]
m = ch["1m"]
assert m["t"][-1] == last, "letzter Kurs muss dabei sein"
assert all(x >= last - 31 * DAY for x in m["t"]), "nichts aelter als 31 Tage"
assert all((x + off) % 3600 == 0 for x in m["t"][:-1]), "sonst nur volle Stunden"
assert m["t"] == sorted(m["t"]) and len(m["t"]) == len(m["c"])
assert 100 < len(m["t"]) < 400, len(m["t"])
for key, days in (("6m", 183), ("1y", 366)):
    x = ch[key]["t"]
    assert all(v >= last - days * DAY for v in x) and x == sorted(x) and len(x) == len(ch[key]["c"]), key
assert len(ch["1y"]["t"]) > len(ch["6m"]["t"]) > 100
print("ok  build_charts")

# Gesamtverlauf: Erfolg
orig = collector.fetch_yahoo
collector.fetch_yahoo = lambda sym, interval, rng: {"t": [1, 2, 3, 4], "c": [1.0, 2.0, 3.0, 4.0]}
collector.time.sleep = lambda s: None
r = collector.max_series({"symbol": "X"}, daily, False, None)
assert r == {"t": [1, 2, 3, 4], "c": [1.0, 2.0, 3.0, 4.0]}, r
print("ok  max_series Erfolg")


# Gesamtverlauf: Ausfall -> letzter guter Verlauf bleibt, ohne Vorgaenger None
def boom(*a, **k):
    raise RuntimeError("Yahoo kaputt")


collector.fetch_yahoo = boom
old = {"max": {"t": [9], "c": [9.0]}}
assert collector.max_series({"symbol": "X"}, daily, False, old) == {"t": [9], "c": [9.0]}
assert collector.max_series({"symbol": "X"}, daily, False, None) is None
# zu wenig Daten zaehlt als Ausfall
collector.fetch_yahoo = lambda *a, **k: {"t": [1], "c": [1.0]}
assert collector.max_series({"symbol": "X"}, daily, False, old) == {"t": [9], "c": [9.0]}
collector.fetch_yahoo = orig
print("ok  max_series Ausfall")
print("alle Diagramm-Tests bestanden")
