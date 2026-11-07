# ETF-Bot

Ein Bot, der ETF-Kurse laufend abholt, daraus Hoch-/Tief-Signale berechnet, Nachrichten mitliest und alles in einem Dashboard zeigt, das auf Handy und PC funktioniert.

**Wichtig:** Die Signale sind Rechenhilfen auf Basis vergangener Kurse. Sie sind keine Vorhersage und keine Anlageberatung. Ob sie etwas taugen, zeigt das Signal-Protokoll im Dashboard mit der Zeit.

## So funktioniert es

1. GitHub startet `collector.py` automatisch alle 15 Minuten (Mo bis Fr, tagsüber).
2. Der Bot holt Kurse (Yahoo Finance) und Schlagzeilen (RSS), rechnet und schreibt `data.json`.
3. Die Seite `index.html` liest diese Datei und aktualisiert sich selbst jede Minute.

Es wird kein Server und keine Bezahl-API gebraucht, und Python musst du nicht selbst installieren.

## Einstellungen ändern

Alles steht in `config.json`:

- `watchlist`: welche ETFs beobachtet werden (Kürzel wie bei Yahoo Finance, Xetra-ETFs enden auf `.DE`)
- `order_fee_eur` und `trade_amount_eur`: Annahme für die Gebühren-Hürde
- `aktien_universum`: welche Aktien im Bereich „Aktien“ durchsucht werden (Kürzel wie bei Yahoo Finance, deutsche Aktien enden auf `.DE`)
- `fx_symbol`: Wechselkurs für die Euro-Umrechnung von US-Werten (Standard `EURUSD=X`)
- `news_feeds`: welche Nachrichtenquellen gelesen werden

## Ausprobieren auf dem eigenen Rechner (optional)

```
python collector.py --demo      # Beispieldaten, ohne Internet
python collector.py             # echte Daten
python -m http.server -d docs   # Dashboard unter http://localhost:8000
python tests/test_indicators.py # Selbsttest der Kennzahlen (braucht pandas)
python tests/test_news.py       # Selbsttest der Nachrichten
python tests/test_langfrist_aktien.py  # Selbsttest Langfrist, Aktien-Scan, Wechselkurs
python tests/test_charts.py      # Selbsttest der Diagramm-Zeiträume
```

## Wie der Wert entsteht

Der Wert reicht von −100 (Hoch-Zone) bis +100 (Tief-Zone). Er ist ein gewichteter Durchschnitt aus:

| Baustein | Gewicht |
|---|---|
| RSI auf 15-Minuten-Kerzen | 25 |
| Lage im Bollinger-Band (15 Min) | 20 |
| Richtung des MACD (15 Min) | 15 |
| RSI auf Tageskerzen | 15 |
| Lage in der 20-Tage-Spanne | 10 |

Ab ±50 spricht das Dashboard von Tief- bzw. Hoch-Zone. Der Rückblick prüft für die letzten ~60 Tage, wie der Kurs nach früheren Signalen am selben Handelstag weiterlief, und vergleicht das mit dem Durchschnitt aller Zeitpunkte. Ein Signal ist nur dann etwas wert, wenn es klar besser ist als dieser Durchschnitt und die Gebühren übersteigt.

## Spalte „Kurzfrist-Kauf“

Das Dashboard leitet aus den Zahlen eine Einschätzung ab: **prüfen**, **abwarten** oder **eher nicht**, jeweils mit Begründung. Sie gilt nur für Kauf und Verkauf innerhalb weniger Stunden, nicht für einen Sparplan, und ist keine Anlageberatung. Die Regeln, in dieser Reihenfolge:

1. Sind die Gebühren (Hin und Zurück, in Prozent des Einsatzes) höher als die übliche Schwankung in 4 Stunden, heißt es **eher nicht**.
2. Steht der Wert in der Hoch-Zone, heißt es **eher nicht**.
3. Steht er in der Tief-Zone, aber im Abwärtstrend, heißt es **abwarten**.
4. In der Tief-Zone ohne Abwärtstrend gibt es nur dann **prüfen**, wenn der Rückblick (mindestens 20 Signale in ~60 Tagen) klar besser war als der Durchschnitt und nach Gebühren im Plus lag. Sonst **abwarten**.
5. Ohne Signal heißt es **abwarten**.

## Euro-Umrechnung

US-Werte (z. B. NVIDIA) werden in Dollar gehandelt. Das Dashboard zeigt zusätzlich eine Zeile „≈ … € umgerechnet“. Sie nutzt den letzten Tageskurs von EUR/USD und ist nur ein Richtwert: Dein Broker rechnet mit seinem eigenen Kurs und Handelsplatz, deshalb können Werte leicht abweichen.

## Abschnitt „Langfristig (Sparplan-Sicht)“

Eigener Abschnitt unterhalb der ETF-Karten. Pro ETF steht dort der Abstand zum 52-Wochen-Hoch, die Entwicklung über 1, 3 und 12 Monate, die Schwankung pro Jahr und der größte Rückgang innerhalb des letzten Jahres. Daraus folgt eine grobe „Lage“ (intakt, seitwärts, Rücksetzer, Korrektur, Schwäche). Das ordnet nur ein, wo die Kurse stehen. Für einen Sparplan zählen Dauer und Regelmäßigkeit mehr als der Einstiegszeitpunkt. Keine Anlageberatung, vergangene Entwicklung ist keine Garantie.

## Bereich „Aktien: mögliche Kurzfrist-Kandidaten“

Der Bot wertet bei jedem Lauf alle Aktien aus `aktien_universum` mit denselben Kennzahlen aus wie die ETFs und sortiert sie nach der Einschätzung aus „Kurzfrist-Kauf“. Die Spalte „Mindest-Einsatz“ zeigt, ab welchem Einsatz die Gebühren kleiner sind als die übliche 4-Stunden-Schwankung (2 × Gebühr ÷ Schwankung). Bei kleinen Beträgen steht deshalb fast überall „eher nicht“. Das ist beabsichtigt und ehrlich: Die Gebühren fressen den möglichen Gewinn. Schlägt Yahoo Finance bei einer Aktie fehl, bleibt der letzte Wert stehen und wird als „veraltet“ markiert.

## Zeiträume der Diagramme

Über den Karten stehen die Zeiträume 1 Tag, 7 Tage, 1 Monat, 6 Monate, 1 Jahr und Maximal. Bei 1 Tag und 7 Tagen sieht man die 15-Minuten-Kerzen mit Bollinger-Band. Darunter wird es gröber: 1 Monat zeigt Stundenkurse, 6 Monate und 1 Jahr Tageskurse, Maximal Wochenkurse seit dem Start des ETFs. Unter dem Diagramm steht die Veränderung im gewählten Zeitraum. Die längeren Verläufe liegen in `charts.json` und werden erst geladen, wenn du einen längeren Zeitraum anklickst. Dadurch bleibt `data.json` klein.
