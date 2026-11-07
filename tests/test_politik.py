"""Selbsttest: politische Themen, Zuordnung zu Aktien, Ausfallschutz der Nachrichten (ohne Internet)."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import collector as C  # noqa: E402

cfg = C.load_json(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.json"), None)
pats, known = C.company_patterns(cfg), C.known_symbols(cfg)


def tag(title):
    it = {"title": title, "themes": C.headline_themes(title), "sentiment": C.headline_sentiment(title)}
    C.tag_item(it, pats, known)
    return it


# Themen
t = tag("Trump droht mit neuen Zöllen auf Chips aus China")
assert {"Handel & Zölle", "Politik & Regulierung", "Exportkontrollen & China"} <= set(t["themes"]), t["themes"]
assert "Exportkontrollen & China" in tag("USA verschärfen Exportkontrollen für KI-Chips")["themes"]
assert "Rüstung & Verteidigung" in tag("Bundeswehr bestellt Drohnen, Verteidigungshaushalt steigt")["themes"]
assert "Geopolitik" in tag("Spannungen im Nahen Osten nehmen zu")["themes"]
assert "Politik & Regulierung" in tag("EU-Kommission eröffnet Kartellverfahren gegen Google")["themes"]
print("ok  Themen")

# Genannte Werte: ganze Woerter, keine Zufallstreffer
assert tag("Nvidia übertrifft Erwartungen")["werte"] == ["NVDA"]
assert tag("Rheinmetall erhält Großauftrag der Bundeswehr")["werte"] == ["RHM.DE"]
assert set(tag("AMD und Intel kämpfen um Marktanteile")["werte"]) == {"AMD", "INTC"}
assert tag("Künstliche Intelligenz verändert die Arbeit")["werte"] == []        # nicht Intel
assert tag("Metadaten und Metaphern")["werte"] == []                            # nicht Meta
assert tag("Reise in den Amazonas")["werte"] == []                              # nicht Amazon
assert tag("Meta Platforms plant Investitionen")["werte"] == ["META"]
assert set(tag("Deutsche Bank und Allianz legen Zahlen vor")["werte"]) == {"DBK.DE", "ALV.DE"}
print("ok  genannte Werte")

# Themen-Zuordnung: genannte Werte tauchen nicht doppelt auf, unbekannte Kuerzel werden verworfen
t = tag("Zölle: Nvidia und Tesla im Visier")
assert "NVDA" in t["werte"] and "TSLA" in t["werte"]
assert "NVDA" not in t["werte_thema"] and "TSLA" not in t["werte_thema"] and "MBG.DE" in t["werte_thema"]
assert all(s in known for s in t["werte_thema"])
small = {"aktien_universum": [{"symbol": "NVDA", "stichworte": ["nvidia"]}], "watchlist": []}
t2 = {"title": "Neue Zölle", "themes": ["Handel & Zölle"]}
C.tag_item(t2, C.company_patterns(small), C.known_symbols(small))
assert t2["werte_thema"] == ["NVDA"], t2
assert set(C.themen_info(small)["Handel & Zölle"]["werte"]) == {"NVDA"}
print("ok  Themen-Zuordnung")

# Stimmung der neuen Woerter
assert C.headline_sentiment("Neue Exportkontrollen und Embargo belasten Chipfirmen") < 0
assert C.headline_sentiment("Regierung beschließt Steuersenkung und Entlastung") > 0
print("ok  Stimmung")

# Ausfallschutz: sind alle Quellen weg, bleiben die letzten guten Meldungen stehen
now = int(time.time())
old_doc = {"news": {"items": [{"title": "alt", "ts": now - 600}], "mood": {}, "themes": {}, "feeds": [], "themen_info": {}}}
orig = C.fetch_news
C.fetch_news = lambda c, n: {"items": [], "mood": {}, "themes": {}, "feeds": [{"name": "X", "ok": False, "n": 0, "error": "tot"}], "themen_info": {"a": 1}}
d = C.build_news_doc(cfg, False, now, old_doc)
assert d["news"]["items"][0]["title"] == "alt" and d["news"]["feeds"][0]["ok"] is False and d["news"]["themen_info"] == {"a": 1}
d = C.build_news_doc(cfg, False, now, {})
assert d["news"]["items"] == []
C.fetch_news = orig
d = C.build_news_doc(cfg, True, now, {})                                         # Demo
assert d["demo"] and len(d["news"]["items"]) == 7
print("ok  Ausfallschutz")
print("alle Politik-Tests bestanden")
