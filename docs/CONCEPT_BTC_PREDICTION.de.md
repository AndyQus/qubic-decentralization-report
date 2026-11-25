# Konzept: BTC-Vorhersage der Ameisenkolonie (BPP-9000, ab Epoch 240)

**English version:** [CONCEPT_BTC_PREDICTION.md](CONCEPT_BTC_PREDICTION.md). Beide Fassungen
sagen dasselbe; eine Änderung an der einen wird auch in der anderen gemacht.

> **Status (2026-11-18): Stufen 0, 1 und 2 umgesetzt.** Stufe 2 ist in §5a beschrieben. Die Mining-Seite liest die
> Aufgabe live (Nachricht 80, K12-geprüft). Die Seite `dashboard/btc.html` ist verlinkt und zeigt
> nur Richtungen, keine Kurse (Entscheidung §7.1). Die Anfrage an die Core-Entwickler steht in
> [`BTC_CALL_REQUEST.md`](BTC_CALL_REQUEST.md) (Entscheidung §7.2: beides). Löst die
> Schlussfolgerung „keine Prognose“ aus [`CONCEPT_BTC_ORACLE.de.md`](CONCEPT_BTC_ORACLE.de.md)
> ab.
>
> **Seit v0.17.0 (2026-11-24):** Das Netz für den Tipp wird nach seiner Vorhersage gewählt, nicht
> mehr nach dem Score — siehe [`CONCEPT_BTC_RANKING.de.md`](CONCEPT_BTC_RANKING.de.md).

Erstellt: 2026-11-18 · Geprüft gegen: `qubic/core` @ `2fee231` (Release v1.313.0), Live-Node
Epoch 240, Tick 91 910 766

---

## 0. Ergebnis in drei Sätzen

1. **Das Netz trainiert jetzt auf echten, live einlaufenden BTC-Stunden.** Die feste Task-Datei
   gibt es nicht mehr. Die Nodes holen jede Stunde den BTC/USDT-Kurs über das Preis-Oracle
   (Binance) on-chain. Das ist live gemessen, siehe §3.
2. **Die Vorhersage existiert implizit, wird aber nirgends veröffentlicht.** Jedes Netz gibt
   eine Folge von Trits aus. Die Ausgabe nach der letzten bekannten Stunde ist sein „Tipp“ für
   die nächste Stunde. Kein Node gibt diesen Tipp aus.
3. **Die Netze selbst sind nur indirekt öffentlich.** Der direkte Abruf
   (`REQUEST_ANT_PARENT_ANN`) braucht eine Operator-Signatur. Alles, was man zum **Nachbau**
   braucht, ist aber öffentlich. Ein eigener Nachbau ist der Kern dieses Konzepts (§5).

Antwort auf „Können wir die Prognose jetzt zeigen?“: **Teilweise.** Den Live-Feed, die
Aufgabe und die Aktivität der Kolonie können wir sofort zeigen. Den Tipp selbst können wir erst
zeigen, wenn wir die Netze nachbauen oder Qubic einen öffentlichen Abruf schafft.

---

## 1. Wie BPP-9000 jetzt funktioniert (am Code verifiziert)

Quelle: `doc/ant_colony_mining.md`, `src/mining/price_feed.h`, `src/mining/score_bpp9000.h`.

```
  Binance BTC/USDT ──► Preis-Oracle ──► jede Node speichert Stunde H (on-chain, Konsens)
                                              │
                         target[H] = 1, wenn Kurs(H) ≥ Kurs(H−1), sonst 0  (gerundet auf ganze USDT)
                                              │
        ┌─────────────────────────────────────┴─────────────────────────────────────┐
        │  8760 Ziele (1 Jahr bis Epochenstart = baseHour)  │ +1 Ziel pro neuer Stunde │
        │◄──────────────── Rahmen bei shift 0 ─────────────►│  … bis SHIFT_CAP = 168   │
        └───────────────────────────────────────────────────┴──────────────────────────┘
                                              ▲
   Netz: 1024 ternäre Neuronen, je 3 Nachbarn + LUT, **keine Eingänge**.
   Es läuft aus einem festen Startzustand und gibt pro Schritt ein Trit aus:
   0 = runter · 1 = rauf · 2 = „weiß nicht“ (zählt im Scorer als Fehler)
```

- **Fehler** = Anzahl falscher Trits im Rahmen von 8760 Stunden. Weniger ist besser.
- **Annahme-Schwelle** (Epoch 240): 4350 Fehler, also knapp besser als ein Münzwurf.
- **Shift**: Hat ein Netz höchstens 3504 Fehler im Rahmen (`ADVANCE_THRESHOLD`, das sind mindestens
  60 % Treffer über ein Jahr), rückt der Rahmen eine Stunde weiter. Dort wird neu bewertet. Der
  Rahmen kann aber nur so weit rücken, wie echte neue Stunden schon gespeichert sind
  (`shiftLimit`). Ein höherer Shift schlägt immer einen geringeren Fehler.
- **Kolonie**: Jede Mining-Identität hat einen Baum. Ein Kind erbt das Netz seines Elternknotens,
  mutiert die Verdrahtung (1000 Schritte, Seed aus Pubkey, Nonce und Anker-Tick) und muss den
  Elternknoten schlagen. Das gilt **auf den Daten des Kindes**, also inklusive der neuen
  Stunden.
- **Neue Stunden**: Der Leader jedes 5. Ticks darf das Oracle für die nächste fehlende Stunde
  fragen, frühestens 300 s nach der vollen Stunde. Nach 24 h Ausfall wird die Stunde mit dem
  Vorstundenkurs aufgefüllt (Flag-Bit 0) und als „rauf“ gewertet.

(Seit v0.17.0 zählt unsere Seite ein „weiß nicht“ als Enthaltung, auf CFBs Wort — siehe
`CONCEPT_BTC_RANKING.de.md`.)

### Was daran Vorhersage ist und was nicht

Das Netz sieht **keine Kurse**. Es ist ein Folgengenerator, der eine Jahresfolge aus
Rauf/Runter-Bits möglichst gut nachbildet. Sein Tipp für die nächste Stunde ist die
Fortsetzung dieser Folge. Ob das mehr als Zufall ist, lässt sich nur auf eine Weise ehrlich
prüfen:

> **Ein Tipp zählt nur, wenn das Netz vor der Stunde feststand.** Für Stunde *h* wird nur ein
> Netz gewertet, dessen Anker-Tick vor dem Tick liegt, in dem *h* gespeichert wurde
> (`appendTick`). Dann ist *h* echt „aus der Stichprobe“.

Das Protokoll erzwingt das schon für den Shift: Ein Netz rückt nur über Stunden vor, die es nicht
kannte, sofern es vorher geprägt wurde. Unsere Seite misst genau das, und zwar gegen die
Messlatte 50 %.

---

## 2. Was öffentlich lesbar ist

| Quelle | Weg | Signatur | Inhalt | Status |
|---|---|---|---|---|
| Epoch-Kontext (76/77) | Peer-Port 21841 | keine | Schwelle, Lösungszahl, Spectrum-Digest (= Root-Seed), `dataHash` | nutzen wir schon |
| **Preisreihe (78/79)** | Peer-Port | keine | stündliche BTC-Kurse, `baseHour`, `nextHour`, Ausfallzähler, Digest | **neu, live geprüft** |
| **Task-Daten (80/81)** | Peer-Port | keine | Ziele und `shiftLimit` für einen Anker-Tick | **neu, live geprüft** |
| Lösungs-Tx (`inputType` 12) | Tick-Daten (Bob/RPC) | – | Pubkey, Parent-Ref, Anker-Tick, Nonce, behaupteter Fehler | öffentlich |
| Log `ANT_SOLU` (Custom-Message 255) | Bob-Logs | – | Ergebnis je Lösung (Valid/Reject…) und nachgerechneter Fehler, **ohne Shift** | öffentlich, `qdr/tickstream.py` kennt Typ 255 |
| Identity-Tree (72/73), Parent-ANN (74/75) | Peer-Port | **Operator** | Shift je Knoten, Netz-Bytes | für uns gesperrt |
| `antColonySolutions.eoe` | Datei auf der Node | – | beste 676 Netze am Epochenende | nicht abrufbar |

---

## 3. Live-Messung (2026-11-18, ca. 12:45 UTC, Node 82.197.173.133)

```
Preisreihe: epoch=240 synced=1 source=1 (Bootstrap) baseHour=40316 (08:00 UTC) nextHour=40321 (13:00 UTC)
            4 Oracle-Abfragen erfolgreich, 0 Fehlschläge, 0 aufgefüllt
  09:00  64 685.33  appendTick 91 910 016   flags 0
  10:00  64 650.84  appendTick 91 910 026
  11:00  64 373.18  appendTick 91 910 040
  12:00  64 231.52  appendTick 91 910 051
  (Stunden ≤ 08:00 tragen flags = 2, kommen also aus der Bootstrap-Datei. Bit 1 ist nicht dokumentiert.)
Task-Daten: anchor=91 910 761 valid=1 shiftLimit=4 targets=8764 (4370 × rauf)
Kolonie:    solution_count = 0 (die Epoche ist ein paar Stunden alt)
```

Damit ist belegt: Das Netz bekommt seit heute Morgen echte neue BTC-Stunden. Bis Epochenende
kommen bis zu 168 Stunden dazu.

---

## 4. Seitenidee: „Kolonie gegen Bitcoin“

Eine animierte Seite (`dashboard/btc.html`) im Stil von Ticks Live, also dunkel, ruhig und mit
Bewegung, die etwas bedeutet. Drei Ebenen, die nacheinander freigeschaltet werden.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  KOLONIE GEGEN BITCOIN                         Epoch 240 · Stunde 5 von 168  │
│                                                                              │
│        ╭───────── nächste Oracle-Stunde in 23:41 ─────────╮                  │
│        │      ◉  Kolonie-Tipp für 14:00 UTC:  ▲ RAUF      │  ← Stufe 2       │
│        │      Bestes Netz ▲ · Schwarm 412 ▲ / 263 ▼ / 1 ?  │                 │
│        ╰──────────────────────────────────────────────────╯                  │
│                                                                              │
│  STUNDENBAND (läuft nach links, sobald eine Stunde einläuft)                 │
│  … ▲ ▲ ▼ ▼ ▲ ▼ ▲ ▲ │ ▼  ▼  ▼  ▼  │ ?                                         │
│      Vorjahr (Rahmen)  │ live seit 08:00 │ offen                             │
│                    Tipp:  ▼  ▲  ▼  ▼     ← Stufe 2: Icon grün = Treffer, rot  │
│                                                                              │
│  TREFFERQUOTE   ━━━━━━━━━━━━━━━━━ 50 % Münzwurf ┈┈┈┈┈┈┈┈┈┈┈┈  ← Stufe 2      │
│  (rollierend, je Epoche, mit Konfidenzband)                                  │
│                                                                              │
│  KOLONIE       Lösungen 1 284 · angenommen 71 % · bester Fehler 4 102        │
│                ● ● ●  (ein Puls pro angenommener Lösung, live aus Logs)      │
│  ORACLE        4/4 Stunden pünktlich · 0 aufgefüllt · Digest 64e8…           │
└──────────────────────────────────────────────────────────────────────────────┘
```

**Animation mit Bedeutung:**
- **Countdown-Ring** bis zur nächsten Oracle-Stunde (volle Stunde + ≥ 300 s). Läuft die
  Stunde ein, rutscht das Band eine Zelle nach links, und der Tipp darunter wird grün
  oder rot aufgedeckt.
- **Puls** für jede angenommene Lösung (aus den `ANT_SOLU`-Logs über den vorhandenen
  Tick-Stream), gedimmt für abgelehnte.
- **Wachsender Rahmen**: Ein Balken zeigt `shiftLimit`, also wie weit die Kolonie in die
  „Zukunft“ gewertet werden darf (0 → 168).

**Farbsprache (seit v0.17.4):** Grün und Rot bedeuten auf der Seite nur noch eines: ob ein
Tipp getroffen hat. Ein Tipp ist überall dasselbe Icon. Der weiße Pfeil ist die getippte
Richtung (▲ rauf oder gleich, ▼ runter), die Füllung das Urteil: **grün** getroffen, **rot**
verfehlt, **grau** noch nicht ausgewertet (laufende und kommende Stunden, gestrichelt umrandet,
wenn die Stunde noch bevorsteht). Ein „?“ (Enthaltung) ist gelb umrandet und ohne Füllung. Ein
verfehlter Tipp trägt zusätzlich einen weißen Innenrand, damit das Urteil auch bei
Rot-Grün-Sehschwäche lesbar bleibt; Tooltip und Screenreader nennen es in Worten. Was Bitcoin
selbst getan hat, kann weder richtig noch falsch sein und erscheint deshalb in einem neutralen
Schieferblau: die Zellen im Stundenband, Stunden ohne Tipp in der Aufzeichnung, die Richtung und
der Kursbalken in der Übersicht.

**Historie:** je Stunde ein Datensatz (Stunde, rauf/runter, Tipp des besten Netzes,
Schwarmverteilung, Treffer). Daraus ergeben sich die Trefferquote pro Epoche und ein Archiv der
Epochen. Die Oracle-Stunden lassen sich über Nachricht 78 auch rückwirkend holen, 256 pro
Anfrage. Die **Tipps** gibt es dagegen erst ab unserem ersten Lauf, wie überall im Projekt.
Die Seite meldet das selbst (`ready`, `unwatched`).

---

## 5. Umsetzung in Stufen

### Stufe 0 – Mining-Seite reparieren (Pflicht, unabhängig vom Rest)

`qdr/antask.py` sucht die Task-Datei über den Live-Hash. Diese Datei gibt es seit Epoch 240
nicht mehr: `/v1/mining` liefert `hash_verified: null`, das Prüfskript meldet
`task_unresolved`. Neu: Aufgabe über Nachricht 80 lesen. `dataHash` = K12 der ersten
8760 Ziele, damit gegenprüfen. Die Terminal-Zeile „Prognose“ / „N offene Stunden“
(`tail_unknown`) auf `shiftLimit` umstellen.

### Stufe 1 – Live-Feed ohne Netze (sofort machbar)

- `qdr/antnode.py`: `query_price_series()` (78) und `query_task_data()` (80). Der Prototyp
  steht im Scratchpad und ist geprüft.
- Worker-Job alle 5 min: neue Stunden, Oracle-Zähler und `shiftLimit` speichern. Dazu die
  `ANT_SOLU`-Logs aus dem Tick-Stream (Anzahl, Ergebnis, Fehlerverteilung je Stunde).
- Endpunkte `/v1/btc/series`, `/v1/btc/colony`.
- Seite mit Stundenband, Countdown, Kolonie-Puls und Oracle-Gesundheit. Statt des Tipps steht
  ehrlich: „Die Netze der Kolonie sind nicht öffentlich – Tipp folgt“.

### Stufe 2 – Die Netze nachbauen und den Tipp zeigen

Alles Nötige ist öffentlich:

| Baustein | Quelle |
|---|---|
| Zufallspool (≈ 512 MB, `POOL_VEC_SIZE`) | aus dem Spectrum-Digest (Nachricht 76), `generateRandom2Pool` |
| Root-Netz einer Identität | `deriveRootANN(pubkey, pool)` |
| Kind-Netz | Parent-Netz + Walk mit Seed `K12(pubkey ‖ nonce ‖ anchorTickDigest)` |
| `anchorTickDigest` | `K12(anchorTick ‖ K12(TickData))`, TickData öffentlich |
| Ziele des Kinds | Nachricht 80 für den Anker-Tick |
| Kontrolle | nachgerechneter Fehler == `claimedScore` und Log-Ergebnis `Valid` |

Ein **Replayer** in C++ übernimmt den Scorer unverändert aus `qubic/core` (Referenz:
`test/score_bpp9000_reference.h`, 409 Zeilen, portabel, oder die AVX2-Version aus
`src/mining/score_bpp9000.h`). Er baut die Linien der besten Bäume nach und lässt das beste
Netz über `8760 + shiftLimit` hinaus laufen. Das nächste Trit ist der Tipp. Daneben
stimmen alle nachgebauten Netze ab (Schwarm).

Aufwand und Risiko: Der Scorer muss **bit-genau** sein, und jede Lösung kostet einen vollen
Walk (1000 Bewertungen). Es reicht aber, nur die Linien der Spitzenreiter nachzubauen. Der Pool
braucht etwa 512 MB RAM auf dem Host. Vor dem Einbau steht ein Offline-Beweis: Ein Netz einer
abgeschlossenen Epoche nachbauen und seinen Fehler gegen das Log prüfen.

**Parallel, als Abkürzung:** die Core-Entwickler bitten, eine öffentliche Abfrage
„Ausgabe des besten Netzes für Stunde h“ einzubauen oder Nachricht 74 öffentlich zu machen
(Vorbild: `docs/POOL_REQUEST.de.md`). Wenn das kommt, entfällt der Replayer.

### 5a. Stufe 2 umgesetzt: so entsteht der Tipp (2026-11-18)

- **Nachbau:** `tools/replayer` übersetzt den Referenz-Scorer aus `qubic/core` (Tag aus
  `QUBIC_CORE_TAG` im Dockerfile) zu einem statischen Programm im Image. Er ist bit-genau
  geprüft: Testdaten des Cores (Kette 11, Tiefe 0 → 5966/0) und eine Live-Lösung aus
  Epoch 240 (Tick 91.912.670 → 4332, wie protokolliert). Ein Walk dauert etwa 11 s auf
  einem Kern.
- **Lösungen:** `qdr/colony.py` liest ab dem ersten Tick der Epoche alle `ANT_SOLU`-Logs
  (Bob, `logType: 255`) und speichert die angenommenen Lösungen. Die Adresse einer Lösung
  ist (Tick, Transaktionsindex).
- **Bestes Netz:** das mit dem geringsten protokollierten Fehler. Seine Linie wird
  inkrementell nachgebaut: Der Prozess hält Pool und Netze, und der neueste Stand liegt im
  Store. **Jeder Walk muss den protokollierten Fehler exakt treffen**, sonst gibt es
  keinen Tipp.
- **Tipp:** Für die Stunde, die das Oracle als Nächstes speichert, wird die Ausgabe des
  besten geprüften Netzes einmal festgehalten (`btc_calls`). Das geschieht nur, solange
  diese Stunde noch nicht im Netz ist. Ein Tipp wird nie überschrieben.
- **Auswertung:** gegen das Ziel-Bit der Stunde (`btc_hours.up`). „Unsicher“ (2) zählt
  wie im Scorer als Fehler. Quoten gibt es für 6 h, 24 h, heute, pro Tag und pro Epoche,
  jeweils neben „immer ▲“ und „wie Vorstunde“.
- **Endpunkte:** `/v1/btc` (Stunden mit Tipp und ✓/✗, aktueller Tipp, Quoten),
  `/v1/btc/summary` (klein, für E-Ink und Widgets).

Messung am 2026-11-18: 6.707 Lösungen in den ersten 2,5 h, die führende Linie 111 tief,
bester Fehler 3.489. Das liegt unter der Vorrück-Grenze von 3.504, die Netze rücken also
in die Live-Stunden vor.

Grenze: Der Rang nach Logs berücksichtigt nur den Fehler. Den Shift kennt erst der
Nachbau. Das Netz für den Tipp ist das beste geprüfte nach (Shift, Fehler).

> **Seit v0.17.0 (2026-11-24) überholt:** Auf Hinweis von Come-from-Beyond wählt die Seite das
> Netz nach seiner gemessenen Vorhersage, nicht mehr nach dem Score. Der Score-Tipp wird
> parallel weiter gespeichert, und „?“ ist jetzt eine Enthaltung statt eines Fehlers. Das
> Gespräch, die Gründe und die Regel stehen in `docs/CONCEPT_BTC_RANKING.de.md`.

### Stufe 3 – Archiv und Bewertung

Trefferquote je Epoche mit Konfidenzintervall, „bestes Netz vs. Schwarm vs. Münzwurf vs.
‚wie letzte Stunde‘“. Diese naive Vergleichsgröße gehört dazu, damit eine Quote über 50 %
nicht mehr verspricht, als sie hält.

---

## 6. Regeln für die Seite

- Kein Tipp ohne nachgebautes und verifiziertes Netz. Keine Schätzung, kein Platzhalterwert.
- Tipps werden **vor** der Stunde gespeichert und nie nachträglich geändert. Die Seite zeigt
  den Speicherzeitpunkt.
- Ein „?“ (Trit 2) wird als eigene Kategorie gezeigt, nicht als Treffer.
- Ein deutlicher Hinweis: „Forschungsexperiment des Qubic-Netzes, keine Anlageempfehlung“.

## 7. Entscheidungen (getroffen am 2026-11-18)

Ergebnis: (1) nur Richtung, (2) beides, also nachbauen und Qubic fragen, (3) sofort verlinkt.
Die ursprünglichen Fragen:

1. **BTC-Kurs anzeigen oder nur ▲/▼?** Die Kurse stammen von Binance, kommen aber aus Qubics
   On-Chain-Konsens. Unsere Regel ist bisher: keine Exchange-Daten weiterverbreiten
   (Memory „Price source licensing“). Mein Vorschlag: Nur die Richtungsbits und die
   Trefferquote zeigen, den Kurs selbst erst nach Prüfung der Binance-Bedingungen.
2. **Stufe 2 selbst bauen oder zuerst Qubic fragen?** Mein Vorschlag: beides. Den
   Offline-Beweis machen und die Anfrage schreiben.
3. **Seite verlinkt oder zunächst unverlinkt?**

## 8. Epochenende: die letzten Stunden ohne Tipp (2026-11-25)

**Beobachtung:** Am Morgen des 25.11. zeigte die Seite für die laufende und die nächste Stunde
nur „?“, dazu „Tipp folgt vor 10:00“. Der Tracker lief (Zustand `tracking`, alle 231 Netze der
Linie geprüft), schrieb aber keinen Tipp mehr.

**Ursache, kein Fehler:** Ein Netz hat genau `SHIFT_CAP` = 168 Ausgaben nach dem ersten Rahmen,
eine je Stunde `base_hour + 1 … base_hour + 168`. In Epoche 240 war `base_hour` = 40316
(18.11., 08:00 UTC), die letzte Stunde mit Tipp endete also am 25.11. um 08:00 UTC. Die Epoche
endet aber erst um 12:00 UTC. Für die Stunden dazwischen gibt `_call` nichts zurück
(`j >= SHIFT_CAP`). Auch der Core sagt nur diese 168 Stunden voraus
(`doc/ant_colony_mining.md` §2.4a: `H = baseHour + 1 .. baseHour + SHIFT_CAP`). Für diese
Stunden gibt es also nirgends eine Vorhersage, auch nicht im Netzwerk selbst. Weil Epoche 240
die erste mit Live-Feed ist, fiel das erst jetzt auf.

**Wie lang die Lücke ist:** Sie hängt davon ab, wie weit `base_hour` vor dem Epochenstart liegt.
In Epoche 240 waren es 4 Stunden. Nach dem Wechsel kommt die Zeit hinzu, bis die neuen Netze
nachgebaut sind.

**Auf der Seite (v0.17.3):** Liegt die laufende oder die nächste Stunde hinter den 168 Stunden
der Epoche, steht dort „kein Tipp: die 168 Stunden der Epoche sind ausgeschöpft ⓘ“. Ein Tooltip
erklärt es, auf Deutsch und Englisch, auch im Tagesdiagramm. Vorher stand dort fälschlich
„Tipp folgt vor …“. Der Hinweis erscheint nur in diesem Zeitraum (`capInfo` in `btc.html`
rechnet aus `task.base_hour`, `task.shift_cap` und `oracle.next_hour`).

## Quellen

- `qubic/core` @ `2fee231`: `doc/ant_colony_mining.md` §2.3a, §2.7d–e; `src/mining/price_feed.h`;
  `src/mining/score_bpp9000.h:2974–3021` (Ausgabe, Shift); `src/mining/rating.h`;
  `src/logging/logging.h:195` (`AntSolutionLogMessage`); `src/qubic.cpp:3568` (Log-Inhalt);
  `src/public_settings.h:123–134` (Konstanten); `test/score_bpp9000_reference.h`
- Live-Abfrage der Nachrichten 78/80 auf dem Peer-Port 21841, siehe §3
- https://qubic.org/blog-detail/qubic-bpp-9000-mining-algorithm-explained (Stand Oktober,
  beschreibt noch die Datei-Variante)
- https://qubic.org/blog-detail/qubic-all-hands-recap-september-2026-neuraxon-arc-agi-3
  (Ankündigung „live Bitcoin prediction prototype … through the new oracle“)
