"""Testet Langfrist-Kennzahlen (gegen pandas), den Aktien-Scan und die Euro-Umrechnung. Aufruf: python tests/test_langfrist_aktien.py"""
import os, sys, math, random, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import pandas as pd
import collector as C

# ---- Langfrist-Kennzahlen gegen pandas
rng = random.Random(3)
p, c, h, l = 100.0, [], [], []
for _ in range(400):
    p *= 1 + rng.gauss(0.0003, 0.012)
    c.append(p); h.append(p * 1.004); l.append(p * 0.996)
daily = {"c": c, "h": h, "l": l}
price = c[-1] * 1.001
r = C.langfrist(daily, price, "Aufwärtstrend")
s = pd.Series(c)
def near(a, b, tol, name):
    assert a is not None and abs(a - b) <= tol, f"{name}: bot={a} pandas={b}"
    print(f"ok  {name:18s} bot={a:<10} pandas={b:.4f}")
near(r["r1m_pct"], (s.iloc[-1] / s.iloc[-22] - 1) * 100, 0.006, "Rendite 1 Monat")
near(r["r3m_pct"], (s.iloc[-1] / s.iloc[-64] - 1) * 100, 0.006, "Rendite 3 Monate")
near(r["r1j_pct"], (s.iloc[-1] / s.iloc[-252] - 1) * 100, 0.006, "Rendite 1 Jahr")
w = s.iloc[-252:]
near(r["max_rueckgang_pct"], ((w / w.cummax()) - 1).min() * 100, 0.06, "Größter Rückgang")
near(r["vol_pa_pct"], w.pct_change().apply(lambda x: x).pipe(lambda z: pd.Series([math.log(a / b) for a, b in zip(w.iloc[1:], w.iloc[:-1])]).std(ddof=0)) * math.sqrt(252) * 100, 0.06, "Schwankung p.a.")
hi52 = max(h[-252:])
near(r["abstand_hoch_pct"], min(0.0, (price / hi52 - 1) * 100), 0.006, "Abstand zum Hoch")
assert C.langfrist(daily, price, "Abwärtstrend")["lage"] == "Schwäche"
assert C.langfrist({"c": [100.0] * 300, "h": [100.0] * 299 + [100.0], "l": [100.0] * 300}, 80.0, "Seitwärts / Übergang")["lage"] == "Korrektur"
assert C.langfrist({"c": [100.0] * 300, "h": [100.0] * 300, "l": [100.0] * 300}, 93.0, "Aufwärtstrend")["lage"] == "Rücksetzer"
assert C.langfrist({"c": [100.0] * 300, "h": [100.0] * 300, "l": [100.0] * 300}, 100.0, "Aufwärtstrend")["lage"] == "intakt"
print("ok  Lage-Einordnung (Schwäche/Korrektur/Rücksetzer/intakt)")

# ---- Aktien-Scan: Wiederverwendung, Fehler, Alt-Werte behalten
cfg = {"intraday_interval": "15m", "intraday_range": "60d", "zone_strong": 50, "zone_soft": 25, "backtest_signal_score": 55,
       "backtest_horizons_bars": [8, 16], "order_fee_eur": 1.0, "trade_amount_eur": 50, "aktien_universum": [
    {"symbol": "AAA", "name": "Schon in der Liste", "group": "x"},
    {"symbol": "BBB", "name": "Klappt", "group": "x"},
    {"symbol": "CCC", "name": "Fällt aus, hat Altwert", "group": "x"},
    {"symbol": "DDD", "name": "Fällt aus, kein Altwert", "group": "x"}]}
calls = []
def fake_fetch(item, cfg_, demo, idx, now_ts, rng_):
    calls.append(item["symbol"])
    if item["symbol"] in ("CCC", "DDD"):
        raise RuntimeError("Yahoo blockt (429)")
    return C.demo_data(item, idx, now_ts)
C.fetch_symbol = fake_fetch
C.demo_data  # vorhanden
by_sym = {"AAA": {"symbol": "AAA", "name": "alt", "price": 10.0, "series": [1, 2, 3], "score": 5}}
prev = {"CCC": {"symbol": "CCC", "name": "Altwert", "price": 7.0, "score": 1}}
out, errs = C.scan_stocks(cfg, by_sym, {}, prev, False, int(time.time()))
syms = {o["symbol"]: o for o in out}
assert "AAA" not in calls, "bereits vorhandene Werte dürfen nicht erneut abgerufen werden"
assert syms["AAA"]["series"] is None and syms["AAA"]["name"] == "Schon in der Liste"
assert syms["BBB"]["price"] and syms["BBB"]["series"] is None
assert syms["CCC"]["stale"] and syms["CCC"]["price"] == 7.0       # Altwert bleibt, als veraltet markiert
assert "DDD" not in syms and errs == 2                             # ohne Altwert: weglassen, Fehler zählen
print("ok  Aktien-Scan: Wiederverwendung, Altwerte, Fehlerzählung")

# ---- Euro-Umrechnung / Wechselkurs-Ausfall
C.fetch_yahoo = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline"))
assert C.fx_rate({}, False, {"fx": {"eurusd": 1.17}}) == 1.17     # letzter bekannter Wert
assert C.fx_rate({}, False, {}) is None
C.fetch_yahoo = lambda *a, **k: {"c": [1.1, 9.9]}                  # unsinniger Kurs wird verworfen
assert C.fx_rate({}, False, {"fx": {"eurusd": 1.17}}) == 1.17
C.fetch_yahoo = lambda *a, **k: {"c": [1.1, 1.16]}
assert C.fx_rate({}, False, {}) == 1.16
print("ok  Wechselkurs: Ausfall, Unsinn, Normalfall")
print("alle Tests bestanden")
