# Konzept: Das Netz für den Tipp nach seiner Vorhersage wählen (BTC-Seite)

Stand: 2026-11-24 · umgesetzt in **v0.17.0**, live ab Epoche 241 (Wechsel am 2026-11-25; Epoche 240 war die Testepoche)
Baut auf `docs/CONCEPT_BTC_PREDICTION.de.md` auf (Nachbau der Netze, Stufe 2).

**English version:** [CONCEPT_BTC_RANKING.md](CONCEPT_BTC_RANKING.md). Beide Fassungen sagen
dasselbe; eine Änderung an der einen wird auch in der anderen gemacht.

Dieses Dokument hält fest, **warum** die BTC-Seite ihr Netz so wählt, wie sie es tut. Grundlage
ist der Austausch mit Come-from-Beyond (CFB) vom 2026-11-24. Wer später etwas ändert, findet
hier, was CFB gesagt hat, was wir daraus gemacht haben und welche Werte noch offen sind.

---

## 1. Der Austausch mit CFB (2026-11-24, Discord)

### CFBs Hinweis (19:56)

> "Hi. I heard you use top 1 model for predictions. You should use another rating to get which
> model is top 1. Instead of best score -- use model which predicts the most accurate. Score
> based on historical data is a kludge, we use it only until we see which model predicts the
> best."

**Was er meint:** Der Score ist die Fehlerzahl eines Netzes über den Jahresrahmen (8760 Stunden
plus Shift). Er misst, wie gut ein Netz eine **bekannte** Folge nachbildet. Das ist
Kurvenanpassung. Bei 1024 Neuronen und tausenden Mutationen pro Stunde gewinnt so das am
stärksten angepasste Netz, nicht das, das am besten vorhersagt. Qubic nutzt den Score fürs
Mining nur, bis sichtbar ist, welche Netze wirklich treffen.

**Was wir bis v0.16.14 taten:** `Tracker._step` in `qdr/colony.py` nahm
`best_ant_solutions(epoch, 1)`, also das Netz mit dem geringsten protokollierten Fehler. Es baute
dessen Linie nach, und dieses eine Netz gab den Tipp. Genau dieses „Top 1 nach Score“ meint CFB.

### Unsere Antwort mit dem Vorschlag (20:27)

> Thanks for the hint, that makes sense. Here is how we would do it:
>
> - The networks have no inputs, so each network's output for every hour is fixed the moment it
>   is created (anchor tick).
> - For every network we rebuild, we count hits only on hours the oracle stored after that
>   network's anchor tick. These are genuine forecasts, not curve fitting.
> - We rebuild the lineages of the top trees. Every node along them is a candidate.
> - The network with the best measured hit rate makes the call. It only counts after a minimum
>   number of hours, and we rank by the conservative lower bound of the rate, so a network cannot
>   win by luck. Until enough hours are measured, we fall back to the score and say so on the
>   page.
> - Every call is stored before its hour and never changed. Each call records which rule picked
>   it.
>
> Three questions:
> 1. What window do you mean by "most accurate": all live hours of the epoch, or a rolling
>    window, e.g. the last 24 or 72 hours?
> 2. Should a "?" (trit 2) count as a miss, as the scorer does, or as an abstention?
> 3. Is the core going to rank by prediction accuracy itself, or publish the best networks'
>    outputs? Then we would take the data from there and keep our rebuild only as a cross-check.

### CFBs Antworten (20:30)

> 1. I don't know, we cannot use whole epoch because models are retrained, pick some number from
>    practice
> 2. Abstention
> 3. Core will do that because need it for correct list of future computors

### Was daraus folgt

| Frage | CFBs Antwort | Unsere Entscheidung |
|---|---|---|
| 1. Zeitfenster | Nicht die ganze Epoche, weil die Modelle neu trainiert werden. Die Zahl soll sich aus der Praxis ergeben. | **Gleitendes Fenster von 48 Live-Stunden** als Startwert, einstellbar (`QDR_COLONY_WINDOW_H`). Nach den ersten Epochen anhand der Daten prüfen (Abschnitt 6). |
| 2. „?“ (Trit 2) | Enthaltung | „?“ zählt weder als Treffer noch als Fehler, **beim Ranking und in allen Quoten der Seite**. Bis v0.16.14 zählte es als Fehler. |
| 3. Rangliste im Core | Der Core wird das selbst tun, weil er es für die richtige Liste der künftigen Computoren braucht. | Unser Ranking ist eine Übergangslösung und später eine **Gegenprobe**. Sobald der Core es veröffentlicht (neue Nachrichtentypen ≥ 82, siehe Skill `pruefe-btc-vorhersage`), nehmen wir die Daten von dort. |

Bemerkenswert an Antwort 3: Die Vorhersagegüte soll künftig mitbestimmen, wer Computor wird.
Das Ranking ist also nicht nur eine Frage unserer Seite, sondern des Protokolls.

---

## 2. Die Einsicht, die es machbar macht

Die Netze haben **keine Eingänge**. `calls()` in `tools/replayer/replay_bpp9000.cpp` lässt das
Netz aus seinem Startzustand laufen und liest die Ziele nie. Die Ausgabe eines Netzes für jede
Stunde steht also fest, **sobald das Netz existiert**.

Daraus folgt: Für jedes nachgebaute Netz lässt sich **rückwirkend und trotzdem ehrlich**
ausrechnen, wie es jede Stunde getippt hätte, die es bei seiner Entstehung nicht kannte. Wir
müssen nicht tagelang warten, um eine Bilanz zu haben. Und wer eine Linie nachbaut, hat jeden
Vorfahren gleich mit. Jeder Knoten ist ein eigenes, geprüftes Netz und damit ein Kandidat.

```
 Stunde h:         h1    h2    h3    h4    h5    h6    h7
 gespeichert in:  1000  1010  1020  1030  1040  1050  1060   (Tick)
 Netz geboren in Tick 1015
   zählt Stunde h, wenn h-2 NACH der Geburt gespeichert wurde:
   h-2 = h3 (1020) → h5 zählt;  h4, h6 … zählen auch;  h1-h4 nicht
```

**Die Regel im Detail** (`net_record`): Eine Stunde *h* zählt für ein Netz nur, wenn die Stunde
*h−2* in einem Tick **nach** dem Tick des Netzes gespeichert wurde. Das Oracle speichert eine
Stunde frühestens 300 s nach ihrem Ende. *h−2* wird also ein paar Minuten nach Beginn von *h−1*
gespeichert, und das Netz existierte sicher, bevor *h* begann. Diese Grenze ist strenger als
„nach dem Anker-Tick“ aus der Nachricht an CFB. Sie verhindert auch, dass ein Miner, der den
Binance-Kurs schon kennt, ein Netz kurz vor dem Speichern einer Stunde passend einreicht. Der
Preis dafür ist eine Stunde Bilanz pro Netz.

## 3. Die Regel

1. **Kandidaten:** alle nachgebauten und geprüften Netze der laufenden Epoche, also alle Knoten
   der nachgebauten Linien.
2. **Linien:** die beste Linie nach Score (wie bisher) und die besten Linien der nächsten
   Identitäten, je Identität ihr bester Baum. Zusammen sind es `QDR_COLONY_LINES` = 4.
3. **Bilanz:** Treffer und Fehler auf den Stunden aus Abschnitt 2, nur innerhalb der letzten
   48 Live-Stunden. „?“ oder eine fehlende Ausgabe ist eine Enthaltung.
4. **Rang:** die untere Grenze des 95-%-Wilson-Intervalls der Trefferquote. So schlägt 5 von 5
   (untere Grenze 57 %) nicht automatisch 60 von 100 (50 %), und kleine Stichproben werden
   bestraft. Bei Gleichstand gewinnt das Netz mit mehr Stunden, dann mit höherem Shift, dann mit
   weniger Fehlern, dann das neuere.
5. **Mindeststichprobe:** Ein Netz braucht 12 entschiedene Stunden (Treffer + Fehler), um gewählt
   zu werden (`QDR_COLONY_MIN_DECIDED`).
6. **Rückfall:** Solange kein Netz 12 Stunden hat, also etwa in den ersten 14 Stunden jeder
   Epoche, tippt das Netz mit dem besten Score. Der Tipp wird als `fallback` gespeichert, und die
   Seite sagt das.

### Beide Regeln laufen parallel

Jede Stunde werden **zwei** Tipps gespeichert. Beide werden vor ihrer Stunde festgehalten und nie
geändert.

| Regel | Tabelle | Netz |
|---|---|---|
| `score` | `btc_calls` (unverändert seit v0.16.0) | das vollständig nachgebaute Netz mit dem besten Score |
| `accuracy` | `btc_calls_accuracy` (neu) | das Netz mit der besten Bilanz, sonst das Score-Netz (`fallback` = 1); dazu `net_hits`, `net_decided` und `net_abstained` zum Zeitpunkt der Wahl |

Die Seite zeigt den Tipp der Regel `accuracy`. Für Stunden vor v0.17.0 gibt es nur Tipps nach
Score. Unter der Trefferquote stehen beide Regeln auf **denselben** Stunden nebeneinander.

**Warum parallel:** Läuft bei der neuen Regel etwas schief, läuft die alte unverändert weiter und
die Daten haben keine Lücke. Dazu können wir belegen, ob CFBs Regel wirklich besser trifft. Das
ist auch die Antwort, die wir CFB später geben können.

**Gegen Selbstbetrug:** Wer aus vielen Netzen das mit der besten Bilanz wählt, erwischt oft das
glücklichste (Winner's Curse). Deshalb wird nicht die rückwirkend beste Bilanz bewertet, sondern
die **Auswahlregel**: Jeder Tipp der Regel steht vor seiner Stunde fest.

## 4. Umsetzung (v0.17.0)

| Baustein | Was |
|---|---|
| `qdr/colony.py` | `hour_points`, `net_record`, `wilson_low`, `select_network`. `Tracker._advance` baut eine Linie inkrementell nach und speichert die Ausgaben jedes geprüften Netzes, `_side_lines` die weiteren Linien reihum, `_rank` erstellt die Rangliste, `_call` schreibt beide Tipps. Ein Netz einer Nebenlinie, das seinen Fehler nicht trifft, wird samt Nachfolgern beiseitegelegt. Die Score-Linie bricht dabei nicht ab. |
| `qdr/store.py` | Tabellen `colony_outputs` (168 Trits je Netz) und `btc_calls_accuracy`. Dazu `best_ant_solutions_per_identity`. |
| `judge`/`rate` | „?“ ist eine Enthaltung: `hit` = null, `abstained` = true. `rate.calls` zählt nur entschiedene Tipps. |
| `/v1/btc` | `call.rule`, `call.fallback`, `call.network.record`; `call.rules` (beide Regeln); `call.selection` (Regel, Fenster, Rangliste der besten 10, Linien); `stats.rules` (beide Quoten auf denselben Stunden). |
| `dashboard/btc.html` | Der Satz unter dem Tipp nennt die Bilanz des Netzes oder den Rückfall. Die Rangliste steht unter „Wie die Tipps entstehen“, der Regelvergleich unter der Trefferquote. „?“ ist überall eine Enthaltung. |
| Tests | `tests/test_colony.py`: Grenze „nicht gekannt“, Fenster, Enthaltung, Wilson, Auswahl und Rückfall, zweite Linie, Nebenlinie mit falschem Fehler, API mit beiden Regeln. |

## 5. Rechenbudget

Ein Walk dauert etwa 11 s auf einem Kern. Die Score-Linie bekommt wie bisher bis zu 25 Walks pro
Schritt, die anderen Linien zusammen 10 (`QDR_COLONY_SIDE_WALKS`). Den ersten Platz bekommt
dabei jedes Mal eine andere Linie. Eine neue Linie ist 100 bis 150 Knoten tief, ihr Aufholen
dauert also einige Stunden. In dieser Zeit sind ihre bisherigen Knoten schon Kandidaten. Die
Ausgaben eines Netzes kosten einen einzigen Rollout (Millisekunden). Insgesamt ist das bis zu
etwa eine halbe Stunde Rechenzeit eines Kerns pro Stunde.

## 6. Offen und zu beobachten

- **Fenster (48 h) und Mindeststichprobe (12):** Beide Werte sind Startwerte „aus der Praxis“,
  wie CFB es wollte. Nach ein bis zwei Epochen anhand von `stats.rules` und der Rangliste prüfen:
  Wechselt das gewählte Netz zu oft, ist das Fenster zu kurz. Gewinnen alte Netze, die längst
  überholt sind, ist es zu lang.
- **Anzahl der Linien (4):** Mehr Linien bedeuten mehr Kandidaten, aber auch mehr Rechenzeit.
  Prüfen, ob die Nebenlinien mithalten (`call.selection.lines`, Zustand `waiting`).
- **Core-Ranking:** Sobald der Core die Vorhersagegüte selbst veröffentlicht (CFB, Antwort 3),
  dessen Daten übernehmen und unser Ranking als Gegenprobe zeigen. Das kommt mit v1.314.0, siehe
  Abschnitt 9.
- **Rückmeldung an CFB:** Nach der ersten vollen Epoche die Zahlen „nach Bilanz gegen nach
  Score“ schicken.

---

## 7. Erste Messung: Live-Test vor dem Deploy (2026-11-24, Epoche 240)

**Aufbau:** Image v0.17.0 lokal gebaut, Tracker im Container mit dem echten Replayer gegen das
Live-Netz. Die Datenbank war eine Wegwerf-Datei, das Skript lag im Scratchpad. Damit der Test
kurz blieb, las der Scan nur die ersten 6.000 Ticks der Epoche, und pro Schritt waren 6 + 6
Walks erlaubt. Die Linien waren deshalb kurz, aber alle Netze echt und geprüft.

**Ergebnis:**

| Schritt | Dauer | Was passierte |
|---|---|---|
| 0 | 278 s | 6 Walks Score-Linie (Tiefe 6 von 145), 6 Walks Nebenlinie 2. Linien 3 und 4 `waiting`. Rangliste erstellt, **Treffer-Tipp nach Bilanz** geschrieben (kein Rückfall). |
| 1 | 272 s | Score-Linie Tiefe 12. Diesmal kam Nebenlinie 3 zuerst an die Reihe, wie vorgesehen. Neues Netz an der Spitze. Der Tipp für dieselbe Stunde wurde **nicht** überschrieben (`new: false`). |
| 2 | 20 s | `no-task`: Ein Node antwortete nicht auf die Task-Abfrage. Das ist das bekannte, vorübergehende Verhalten; der nächste Schritt versucht es erneut. |

- 24 Netze aus 3 Identitäten gespeichert, jedes mit genau 168 Trits.
- Einen Score-Tipp gab es nicht, weil die Score-Linie noch nicht vollständig nachgebaut war.
  Das ist gewollt: Der Score-Tipp kommt nur vom vollständigen Blatt.
- Die Spitze der Rangliste nach Schritt 1:

| Netz (Tick) | Tiefe | Fehler (Score) | Richtig | Quote | Mindestens (Wilson) |
|---|---|---|---|---|---|
| 91.912.523 | 8 | 4146 | 34 / 46 (2 ?) | 73,9 % | 59,7 % |
| 91.912.518 | 7 | 4157 | 33 / 47 (1 ?) | 70,2 % | 56,0 % |
| 91.912.513 | 6 | 4170 | 31 / 47 (1 ?) | 66,0 % | 51,7 % |
| 91.911.185 | 1 | 4265 | 29 / 46 (2 ?) | 63,0 % | 48,6 % |

**Was das zeigt:**

- Frühe Netze mit **schlechtem** Score (über 4100 Fehler; das Blatt der Linie hat 3431) treffen
  auf den Stunden, die sie nicht kannten, deutlich besser als 50 %. Score und Vorhersage sind
  also nicht dasselbe. Das stützt CFBs Hinweis.
- **Vorsicht:** Bei vielen Kandidaten liegt das beste fast immer über 50 %, auch durch Glück
  (Winner's Curse). Ob die Regel wirklich etwas taugt, zeigt erst der Vergleich der vorab
  festgehaltenen Tipps beider Regeln (`stats.rules`), nicht diese Rangliste.
- Ein Schritt mit 12 Walks dauerte etwa 4,5 Minuten, also gut 11 s pro Walk, wie gemessen.

## 8. Betrieb: wie es läuft und woran man es sieht

**Ein Schritt des Trackers** (`scripts/ingest.py`, Thread `colony-tracker`, etwa jede Minute
oder sobald der vorige Schritt fertig ist):

1. Task und Preisreihe vom Node lesen, `dataHash` prüfen.
2. Neue Lösungen aus den Bob-Logs lesen (`scan`).
3. Score-Linie: bis zu 25 Walks. Jedes geprüfte Netz bekommt seine 168 Trits in
   `colony_outputs`.
4. Rangliste aus allen gespeicherten Netzen der Epoche (`_rank`).
5. Für die kommende Stunde beide Tipps schreiben, sofern sie noch nicht begonnen hat und das
   Oracle sie noch nicht kennt.
6. Nebenlinien: zusammen bis zu 10 Walks, jedes Mal eine andere zuerst. Ihre neuen Netze kommen
   im nächsten Schritt in die Rangliste (seit v0.17.2; vorher standen die Nebenlinien vor dem Tipp).

**Beim Deploy (Neustart):** Der Replayer startet neu und lädt pro Linie das zuletzt gespeicherte
Netz (`colony_nets`). Der Score-Tipp läuft deshalb ohne Lücke weiter. Netze aus der Zeit vor
v0.17.0 haben noch keine Trits. Für das geladene Netz werden sie sofort berechnet, für ältere
Vorfahren nicht. Die Nebenlinien werden von vorn nachgebaut, ihre frühen Netze haben aber schon
eine Bilanz.

**Beim Epochenwechsel:** Neuer Spectrum-Digest → neuer Pool (512 MB, als Datei unter
`DATA_DIR/raw`), Replayer-Neustart, alte Netze und Trits werden gelöscht (eine Epoche bleibt).
In den ersten etwa 14 Stunden hat kein Netz 12 gewertete Stunden. Die Treffer-Regel fällt dann
auf den Score zurück (`fallback` = 1), und die Seite sagt das.

**Woran man sieht, ob es läuft** (`GET /v1/btc`):

| Feld | Gesund | Wenn nicht |
|---|---|---|
| `call.tracker` | `tracking` oder `catching-up` | Grund in `call.reason` / `call.detail` |
| `call.rule` | `accuracy` | `score`: Stunde vor v0.17.0 oder kein Tipp der Treffer-Regel |
| `call.fallback` | `false`, sobald ein Netz 12 Stunden hat | `true` am Epochenanfang ist normal |
| `call.selection.chosen` | ein Netz mit `decided` ≥ 12 | `null` = Rückfall auf den Score |
| `call.selection.lines[].state` | `tracking` / `catching-up` | dauerhaft `waiting` = zu wenig Walks; `mismatch` = Linie beiseitegelegt; `lineage-incomplete` = Vorfahren fehlen in den Logs |
| `stats.rules` | beide Quoten auf denselben Stunden | `null` = noch kein Treffer-Tipp |

**Stellschrauben** (Umgebungsvariablen, ändern nur per neuem Image, weil wir keinen Serverzugang
haben): `QDR_COLONY_LINES`, `QDR_COLONY_SIDE_WALKS`, `QDR_COLONY_WINDOW_H`,
`QDR_COLONY_MIN_DECIDED`, siehe README.

**Vorfall beim ersten Deploy (v0.17.0, 2026-11-24):** Der neue Tracker schloss auf
report.qubic.tools über 30 Minuten keinen Schritt ab. Es gab keine Rangliste und keinen Tipp für
die nächste Stunde. Ursache war die Abfrage „bester Tipp je Identität“ (`best_ant_solutions_per_identity`):
Als Fensterfunktion sortierte sie in jedem Schritt alle angenommenen Lösungen der Epoche, und eine
Epoche hat Millionen davon. Gemessen bei 3 Mio. Zeilen: 54 s auf einem Desktop, gegenüber 0,04 s
für die alte Abfrage. Der Live-Test vorher hatte nur die ersten 6.000 Ticks gelesen und konnte das
nicht zeigen. **Behoben in v0.17.2:** Die Abfrage liest den Index (epoch, score) in Score-Reihenfolge
und hört bei der vierten Identität auf (0,055 s, gleiches Ergebnis). Außerdem schreibt der Tracker
die Tipps jetzt **vor** den Nebenlinien, damit diese den Tipp nie aufhalten. Nebenlinien fragen
höchstens 5 Nodes nach Tick-Daten und laden fehlende Vorfahren je Linie nur einmal nach.
**Lehre:** Live-Tests mit der Datenmenge einer vollen Epoche machen, nicht nur mit dem Anfang.

**Vorfall beim Epochenwechsel (240 → 241, 2026-11-25):** Ab etwa 14:30 UTC meldete der Tracker
`mismatch`: Ein nachgebautes Netz traf den protokollierten Fehler nicht mehr. Die Tipps kamen nur
noch vom zuletzt geprüften, schwachen Netz (`fallback`). Ursache: Wir lesen die Preisreihe ab
`base_hour` einschließlich, und `shift_limit_at` zählte jeden Punkt mit Append-Tick vor dem Anker.
In Epoche 240 kam die Basisstunde aus der Bootstrap-Datei (Append-Tick 0, übersprungen). In Epoche
241 wurde sie in Epoche 240 live gespeichert (Tick 92239961) und zählte mit. Jedes Shift-Limit lag
damit um eins zu hoch. Das fiel erst auf, als die ersten Netze am echten Limit hätten weiterrutschen
können. **Behoben in v0.17.4:** `live_append_ticks` zählt nur Stunden nach `base_hour`
(doc §2.3a). Geprüft gegen die Node selbst (Nachricht 80) an 8 Ankern: neu immer gleich, alt
immer +1. **Lehre:** Annahmen über die Preisreihe am Epochenrand gegen die Node prüfen, nicht nur
gegen die erste Epoche.

**Rückweg:** Image v0.16.14 erneut pushen. Die neuen Tabellen stören die alte Version nicht.
Die Tipps beider Regeln bleiben gespeichert.

---

## 9. Offizielle Zählung im Core ab v1.314.0 (Stand 2026-11-25)

**Anlass:** Am 25.11. fragte QubicChurch auf Discord, ob wir das v1.314.0-Update gesehen haben:
Der Core zähle jetzt selbst getroffene und gezählte Stunden je ID und ranke ab 12 Treffern nach
Trefferquote, also gebe es einen offiziellen Weg zu zählen.

**Stand im Code:** Ein Release v1.314.0 gibt es noch nicht, aber `develop` enthält PR #1077
„Bpp9000: live feed with prediction“ (gemergt am 23.11.) und die Parameter für Epoche 241
(Tick 92260000). Regeln laut `doc/ant_colony_mining.md` §2.4a:

- **Qualifikation:** eine Identität mit einer angenommenen Lösung mit Shift ≥ 1 oder mit Shift 0
  und höchstens 3504 Fehlern.
- **Umschalten:** Die Epoche wechselt von Training zu Vorhersage, sobald sich 226 Computoren
  qualifiziert haben, aber nicht mehr, wenn höchstens 24 Stunden übrig sind. Ein Wechsel je Epoche.
- **Antwort:** Je Stunde H die Ausgabe des Netzes an der Position für H (dieselben 168 Stunden
  wie bei uns). Nach dem Umschalten ersetzt jede angenommene Lösung der Identität ihre Antworten
  für die offenen Stunden, auch eine schwächere. Frist: 30 Minuten vor dem **Ende** von H.
- **Zählung:** je Identität, über die ganze Vorhersagephase. Keine Antwort (?) zählt als gezählt,
  aber nicht getroffen. Gerankt nach Quote ab 12 Treffern. Gleichstand: mehr gezählte Stunden,
  dann Shift, Fehler, Tick.
- **Die Sitze werden nach dieser Rangliste vergeben** (451 Computoren bleiben, 225 Plätze für die
  Besten aus restlichen Computoren und Kandidaten). Damit hängt die Wahl der Computoren ab
  v1.314.0 an der Vorhersagegüte; das betrifft das Hauptthema dieses Berichts.
- **Lesbar:** Nachrichten 82/83 (Phase) und 84/85 (Stand einer Identität: `hours`, `correct`),
  Operator-Befehl 21 (ganze Rangliste, nicht öffentlich), Event-Logs `PRED_SWI`, `PRED_ANS`
  (Antwort je Identität und Stunde, mit der antwortenden Lösung), `PRED_RES` (Ergebnis je Stunde).

**Unterschiede zu unserer Regel (v0.17):**

| | Core v1.314.0 | wir v0.17 |
|---|---|---|
| Einheit | Identität, Zähler laufen über Netzwechsel weiter | einzelnes Netz |
| Zeitraum | ganze Vorhersagephase | letzte 48 Stunden |
| Schwelle | 12 Treffer | 12 gewertete Stunden |
| „?“ | zählt als falsch | Enthaltung (so hatte CFB es am 24.11. gesagt) |
| Frist | 30 min vor dem Ende der Stunde, also mitten in der Bewegung | vor dem Beginn der Stunde |
| Wer tippt | jede qualifizierte Identität | ein Netz, nach Bilanz gewählt |

**Offene Frage an die Core-Devs:** Die Frist liegt in der Mitte der Stunde, deren Bewegung
vorhergesagt wird. Weil jede neue angenommene Lösung die offenen Antworten ersetzt, könnte ein
Miner um :29 ein Netz veröffentlichen, dessen Antwort zur ersten halben Stunde passt. Ist das
gewollt? (Im Discord-Antwortvorschlag vom 25.11. gestellt.)

**Wann wir umstellen (Empfehlung):**

1. **Jetzt nichts.** Bis zum Umschalten in Epoche 241 gibt es keine offiziellen Zahlen; unser
   Tracker läuft weiter. Nach dem Epochenwechsel prüfen, ob er mit v1.314.0 sauber anläuft.
2. **Sobald Epoche 241 umgeschaltet hat** (Nachricht 82/83 live auf einem Node prüfen, dann
   gibt es echte Daten zum Testen): die offiziellen Zähler lesen und auf der Seite **neben**
   unserer Rangliste zeigen, als Gegenprobe. Dazu die Sitz-Frage für den Bericht.
   **Vorbereitet in v0.17.3:** `antnode` liest Nachricht 82/83 (Phase) und 84/85 (Stand einer
   Identität), aber erst ab Epoche 241, weil ältere Nodes nicht antworten und jede stumme Node
   den vollen Timeout kosten würde. `/v1/btc` liefert `prediction`, die Seite zeigt unter der
   Statuszeile „Trainingsphase: x von 226 Computoren qualifiziert“ bzw. „Vorhersagephase seit …“
   mit Tooltip (de + en). Vor dem Wechsel (25.11., 09:50 UTC) antwortete noch keine Node (alle
   v1.313); das Wire-Format ist also erst nach 12:00 UTC live geprüft.
3. **Ab Beginn von Epoche 242:** der offizielle Erstplatzierte gibt den Tipp der Seite, unsere
   Regel läuft als Gegenprobe mit. Der Wechsel an einer Epochengrenze hält jede Epoche in sich
   einheitlich. Dabei bleibt unsere Frist: gezählt wird die Antwort, die vor Beginn der Stunde
   feststand, nicht die bis :30 nachgereichte.
4. **„?“ angleichen:** In unserer Quote „?“ wie der Core als falsch zählen oder beide Zahlen
   zeigen. Das widerspricht CFBs Aussage vom 24.11.; sie war vor dem Core-Code. Mit Schritt 3
   entscheiden.

## 10. „Die besten Computoren“ auf der Seite (v0.17.6, 2026-11-27)

**Anlass:** Ein User schrieb, einige Computoren lägen über 70 %, und schlug vor, die besten ins
Dashboard zu holen. Die Zahl stimmte: das beste Netz stand bei 34 von 48 = 70,8 % (Wilson-Untergrenze
56,8 %). Zwei Einschränkungen gehören aber dazu, und die Seite zeigt beide:

- **Rückblick ist keine Vorhersage.** Unsere eigenen Tipps, jeder vom Netz mit der jeweils besten
  Bilanz und vor seiner Stunde festgelegt, trafen über dieselbe Zeit deutlich weniger (Stand
  27.11.: 24 von 48 laut `stats.rules.measured`; die Epochenquote aller Tipps 20 von 46). Wer über
  48 Stunden der Beste war, ist es danach oft nicht mehr: Regression zur Mitte.
- **Wir sehen nur wenige Computoren.** Die alte Rangliste zeigte zehn Netze, alle von derselben
  Identität. Wir bauen nur `QDR_COLONY_LINES` = 4 Linien nach, nicht alle 676 Computoren.

**Umsetzung:**

- `colony.board()` hält **eine Zeile pro Identität** (ihr bestes Netz nach `rank_key`, also nach
  derselben Regel, nach der gewählt wird), mit `strip` – dem Urteil pro Stunde des Fensters
  (`1` richtig, `0` falsch, `?` Enthaltung, `.` nicht gezählt, aus `net_strip()`, das dieselbe
  Regel wie `net_record()` nutzt) –, `wilson`/`wilson_high` (95-%-Spanne) und der 60-stelligen
  Identität (`antask.identity_from_pubkey`, geprüft an der Null-Adresse `…FXIB`).
- Der Worker legt das in den Status, `/v1/btc` liefert es unter `call.selection.board`, zusammen
  mit den Stunden des Fensters und `always_up` auf genau diesen Stunden.
- Die Seite zeigt den Bereich als eigenes Panel unter dem Kolonie-Puls: oben vier Chips (beste Bilanz ·
  tatsächlich getippt · immer hoch · Münzwurf), dann pro Computor den Stundenstreifen und ein
  Punktdiagramm der Quote mit Spanne, gegen 50 % und „immer hoch“. Die Farben sind die der
  Tipp-Icons. Die alte Netz-Tabelle im Info-Bereich bleibt als Detail.

**Offen (Stufe 2):** die offizielle Zählung des Cores daneben. Die Phase läuft (seit Tick
92.275.481, 612 qualifiziert), aber Nachricht 84 beantwortete am 27.11. die Identität unseres
besten Netzes nicht. Zu klären: Request-Format, und ob der Core Computor- statt Miner-Identitäten
zählt. Dann alle Identitäten abfragen und als eigene Zeilen bzw. eigene Box zeigen.
