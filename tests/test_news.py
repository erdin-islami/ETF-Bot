"""Testet das Einlesen der Nachrichten mit nachgebauten Feeds (ohne Internet). Aufruf: python tests/test_news.py"""
import os, sys, time
from email.utils import format_datetime
from datetime import datetime, timezone, timedelta
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import collector as C

now = datetime.now(timezone.utc)
def item(title, source, hours_ago, link="https://example.com/a"):
    d = format_datetime(now - timedelta(hours=hours_ago))
    return f"<item><title>{title}</title><link>{link}</link><pubDate>{d}</pubDate><source url='https://x'>{source}</source></item>"

feed1 = "<?xml version='1.0'?><rss><channel>" + "".join([
    item("Dax im Rekordhoch nach Zinssenkung - Handelsblatt", "Handelsblatt", 1),
    item("Neue Zölle: Börsen fürchten Handelskrieg - Reuters", "Reuters", 2),
    item("Neue Zölle: Börsen fürchten Handelskrieg - Reuters", "Reuters", 2.1, "https://example.com/dup"),   # Doppelt
    item("Uralte Meldung", "Alt", 90),                                                                            # zu alt
    item("Ohne Link", "X", 1, link="javascript:alert(1)"),                                                        # unsicherer Link
    item("Waffenruhe im Nahost-Konflikt &amp; Ölpreis fällt", "Tagesschau", 3),
    item("Nvidia-Chips: KI-Boom treibt Tech-Werte, Rally geht weiter", "CNBC", 4),
    item("Sorgen vor Rezession: Kurssturz in Asien", "FT", 5),
]) + "</channel></rss>"

evil = b"<?xml version='1.0'?><!DOCTYPE lolz [<!ENTITY a 'aaaa'>]><rss><channel><item><title>&a;</title></item></channel></rss>"

calls = {"n": 0}
def fake_get(url, **kw):
    calls["n"] += 1
    if "evil" in url: return evil
    if "down" in url: raise RuntimeError("Feed nicht erreichbar")
    return feed1.encode("utf-8")
C.http_get = fake_get

cfg = {"news_max_age_hours": 48, "news_max_items": 40, "news_feeds": [
    {"name": "A", "url": "https://ok/1"}, {"name": "Boese", "url": "https://evil/2"}, {"name": "Down", "url": "https://down/3"}]}
res = C.fetch_news(cfg, int(time.time()))
titles = [i["title"] for i in res["items"]]
print("\n".join(f"{i['sentiment']:+.2f} {i['themes']} {i['title']}  [{i['source']}]" for i in res["items"]))
print(res["mood"], res["themes"])
print([ (f["name"], f["ok"]) for f in res["feeds"]])

assert len(titles) == 5, titles                                   # Duplikat, Uralt-Meldung und javascript:-Link raus
assert not any("Uralte" in t or "Ohne Link" in t for t in titles)
assert not any(t.endswith("Handelsblatt") for t in titles)        # Quellen-Suffix entfernt
assert any("&" in t and "&amp;" not in t for t in titles)         # HTML-Entity sauber
assert [f["ok"] for f in res["feeds"]] == [True, False, False]    # kaputte Feeds isoliert
assert all(i["url"].startswith("https://") for i in res["items"])
assert res["mood"]["n"] == 5 and res["mood"]["label"] in ("neutral", "eher positiv", "eher angespannt")
print("alle News-Tests bestanden")
