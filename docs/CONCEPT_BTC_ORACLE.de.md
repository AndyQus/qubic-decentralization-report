# Konzept: BTC-Richtungsanzeige aus dem Qubic-Mining-Algorithmus

> **Status: Konzept, nicht implementiert.** Dieses Dokument prüft zuerst, ob die
> gewünschte Anzeige überhaupt mit echten Daten befüllbar ist. Ergebnis: **teilweise
> — der zentrale Wunsch (Prognose für die nächste Stunde) ist mit dem heutigen
> Protokollstand nicht erfüllbar.** Warum, steht in §2. Was stattdessen geht, in §4.

Erstellt: 2026-09-09 · Geprüft gegen: `qubic/core` @ `main`, Epoch 230, `rpc.qubic.org`

---

## 1. Was tatsächlich im Protokoll steht (verifiziert am Quellcode)

Der seit Epoch 228 aktive Mining-Algorithmus heißt **BPP-9000** und läuft in einer
Suchstruktur namens **Ant Colony**. Beides sind getrennte Dinge: Ant Colony ist die
*Suchstruktur* (Lösungsbaum, Annahmeregeln, Ranking), BPP-9000 der *Algorithmus*
(was eine Lösung ist und wie sie bewertet wird). Quelle: `doc/ant_colony_mining.md`.

Die Parameter aus `src/public_settings.h` (Produktionswerte, nicht Testwerte):

| Konstante | Wert | Bedeutung |
|---|---|---|
| `BPP9000_NUMBER_OF_INPUT_NEURONS` | 18 | 18 Eingangs-Trits pro Zeitschritt |
| `BPP9000_NUMBER_OF_OUTPUT_NEURONS` | 1 | **genau eine** Ausgabe |
| `BPP9000_SEQUENCE_LENGTH` | `24 * 365` = 8760 | **stündliche** Reihe über ein Jahr |
| `BPP9000_WINDOW_WIDTH` | `24 * 28` = 672 | Kontextfenster: **4 Wochen** |
| `BPP9000_NUMBER_OF_WINDOWS` | 8088 | bewertete Fenster = Score-Obergrenze |
| `BPP9000_SOLUTION_THRESHOLD_DEFAULT` | 4000 | Annahmeschwelle (pro Epoch änderbar) |

Der Scorer (`src/mining/score_bpp9000.h`) ist ein **rekurrentes ternäres Netz**:

```
// bpp9000 scorer: recurrent ternary ANN (trits {0,1,2}, 2=UNKNOWN)
static constexpr unsigned char TRIT_UNKNOWN = 2;
```

Bewertet wird so: das Netz bekommt ein Fenster von 672 Stunden gefüttert und muss den
**nächsten** Wert danach treffen (`expected = outputs[base + l + windowWidth][0]`).
Jede Abweichung zählt einen Fehler. **Score = Fehleranzahl in [0, 8088], kleiner ist
besser.** Am Epochenende werden die 676 besten Identitäten zu Computors.

**Damit stimmt deine Beschreibung im Kern:** Es ist eine Richtungsprognose auf einer
stündlichen Zeitreihe, und "steigt / fällt / weiß nicht" ist als Trit-Alphabet
`{0, 1, 2}` mit `2 = UNKNOWN` tatsächlich im Code vorhanden.

---

## 2. Die zwei Befunde, die das Vorhaben einschränken

### 2.1 Es gibt keine Live-Prognose — der Algorithmus rechnet gegen die Vergangenheit

Das ist der entscheidende Punkt. Die Aufgabe steht in einer **statischen Datei**
(`data/bpp9000.task`, 44.749 Bytes, im Repo eingecheckt). Jede Epoch legt sich per
`REQUEST_ANT_EPOCH_CONTEXT` auf einen festen `dataHash` fest, und die Doku ist dabei
unmissverständlich:

> `topologyHash` / `dataHash` identify the exact task the node scores against. […]
> If either differs you are holding the wrong task: **stop.**

Ein Miner, der gegen andere Daten rechnet, bekommt seine Lösung abgelehnt und verliert
die Kaution. Die Trainingsdaten **müssen** für alle identisch und damit im Voraus
bekannt sein — sonst wäre der Score nicht netzwerkweit nachrechenbar.

Folge: Das Netz sagt **keine zukünftige Stunde** vorher. Es rekonstruiert 8088
*bereits vergangene* Stunden aus je 4 Wochen Kontext, und der Score misst, wie gut das
gelingt. Es existiert im gesamten Core **kein Pfad**, der ein trainiertes Netz auf
aktuelle Marktdaten anwendet und ein Ergebnis veröffentlicht. Es gibt kein Feld, keine
Transaktion und keinen RPC-Endpunkt für eine Vorhersage.

**Eine Anzeige "BTC in der nächsten Stunde: steigt" lässt sich aus dem Protokoll
heraus also nicht mit echten Daten füllen.** Sie zu bauen hieße, eine Zahl zu erfinden
— genau das, was dieses Projekt an keiner Stelle tut (siehe README: "No sample data,
ever").

### 2.2 Nirgends im Code steht, dass es BTC ist

Das Task-Format (`src/mining/task_file.h`) ist bewusst inhaltsagnostisch: Header,
Topologie-Block, Datenblock aus gepackten Trits, fünf pro Byte. Der Core weiß nicht,
was die 18 Eingangs-Trits bedeuten. Die Wörter "bitcoin", "price" oder "BTC" kommen
weder im Core noch in `doc/ant_colony_mining.md` vor.

Was die Dekodierung der echten Task-Datei ergibt (nachgerechnet, §6):

```
numPairs T = 8761, Eingangs-Trits = 18, Ausgabe-Trits = 1
Verteilung der Zielwerte:  0 → 4344   1 → 4417   2 → 0
```

Zwei Dinge daran sind aufschlussreich:

- **8761 Zeilen = 8760 Stunden + 1.** Exakt ein Jahr stündlicher Daten. Das stützt die
  Finanzzeitreihen-Deutung klar.
- **Die Zielwerte sind rein binär, ~49,6 % zu 50,4 %.** Der Wert `2` (UNKNOWN) kommt
  als Ziel **kein einziges Mal** vor.

Das heißt: `UNKNOWN` ist zwar ein gültiger *interner* Zustand des Netzes (ein Neuron,
das sich noch nicht festgelegt hat), aber **niemals eine gültige Antwort**. Gibt das
Netz `2` aus, wo `0` oder `1` erwartet wird, ist das schlicht ein Fehler. Die von dir
erwartete dritte Kategorie "ich weiß es nicht" existiert damit **nicht als Aussage des
Modells** — sie ist ein Rechenzustand, kein Ergebnis.

Die ~50/50-Verteilung passt zu einer Auf-/Ab-Reihe eines Finanzwerts. Dass es *BTC*
ist, ist eine plausible Community-Annahme, aber aus den offiziellen Quellen **nicht
belegt**. Ein seriöses Dashboard darf das nicht als Tatsache behaupten.

---

## 3. Konsequenz für deine Fragen

| Dein Wunsch | Machbar? |
|---|---|
| Anzeige "BTC nächste Stunde: steigt/fällt" | **Nein.** Kein Live-Inferenz-Pfad im Protokoll (§2.1). |
| Dritte Kategorie "weiß nicht" | **Nein.** Als Ziel nie verwendet; nur interner Zustand (§2.2). |
| Trefferquote des Algorithmus | **Nur mit Operator-Schlüssel** — und als Score gegen historische Daten, nicht live (§4.3). |
| Fortschritt der laufenden Epoch | **Ja**, ohne Zusatzrechte — live geprüft (§4.3). |
| Daten 1 Woche halten, dann löschen | **Ja**, unkritisch (§5). |
| Unverlinkte Seite | **Ja**, technisch trivial — "unlisted", kein Schutz (so gewünscht, §5). |

---

## 4. Was stattdessen sinnvoll ist: eine "Mining Accuracy"-Seite

Statt einer erfundenen Prognose lässt sich etwas bauen, das **vollständig aus echten
Daten** besteht und inhaltlich nah an deinem Interesse liegt: **Wie gut ist das
Netzwerk beim Vorhersagen tatsächlich — und wird es besser?**

Das ist messbar, weil der Score genau das ausdrückt.

### 4.1 Die Kennzahl

Aus dem besten Score einer Epoch wird eine verständliche Trefferquote:

```
Trefferquote = (8088 − Score) / 8088
```

Ein Score von 4000 (die Annahmeschwelle) entspricht ~50,5 % — also praktisch Zufall.
Ein Score von 3000 entspricht ~62,9 %. Diese Umrechnung macht den Fortschritt lesbar
und ist zugleich ehrlich: Sie zeigt sofort, wie nah an der Zufallsgrenze das Netz noch
arbeitet.

### 4.2 Die Anzeige (Seite `dashboard/accuracy.html`, nicht verlinkt)

1. **Großes Tacho / Balken**: aktuelle beste Trefferquote der laufenden Epoch, mit der
   50-%-Zufallslinie als fest eingezeichnete Referenz. Ohne diese Linie wären 55 %
   beeindruckend; mit ihr sieht man, dass es 5 Punkte über Raten sind.
2. **Verlauf über die Epochen**: kommt das Netz voran, oder stagniert es?
3. **Baumfortschritt der laufenden Epoch**: `solutionCount` und die Score-Verbesserung
   seit Epochenstart — der Ant-Colony-Effekt wird direkt sichtbar.
4. **Erklärtext, prominent, nicht im Kleingedruckten**: was hier gemessen wird
   (Rekonstruktion historischer Stunden), was nicht (keine Zukunftsprognose), und dass
   der Inhalt der Daten offiziell unbestätigt ist.

### 4.3 Datenquellen — praktisch geprüft, nicht nur aus der Doku

Der Node-Zugang wurde am 2026-09-09 gegen das Mainnet **real getestet**. Ergebnis:

| Feld | Quelle | Status |
|---|---|---|
| `threshold`, `solutionCount`, `epoch`, `dataHash`, `freeAnnSlots` | `REQUEST_ANT_EPOCH_CONTEXT` (76/77), **public** | ✅ **funktioniert** |
| bester Score je Identität | `REQUEST_ANT_IDENTITY_TREE` (72/73) | ❌ operator-signiert, ohne Schlüssel keine Antwort |
| Computor-Liste, Tick, Epoch | `rpc.qubic.org` | ✅ |

Diese Daten liegen **nicht** auf `rpc.qubic.org` (geprüft: `/v1/tick-info` und
`/v1/latest-stats` liefern nur Tick-, Epoch- und Preisdaten). Sie existieren
ausschließlich auf dem **Peer-Port 21841**.

#### Peers finden (deine Frage)

Es gibt **keinen offiziellen Endpunkt** für Node-IPs. Wichtig zur Einordnung:
Computors sind *Identitäten* (60-stellige IDs), keine Adressen — aus der
Computor-Liste lässt sich also keine IP ableiten. Zwei Quellen funktionieren:

1. **`https://api.qubic.li/Public/Peers`** — öffentlich, liefert IP + aktuellen Tick
   je Node. Der Tick-Rückstand ist die brauchbarste Sortierung: er zeigt, welcher Node
   synchron ist. Gemessen: 10 Peers, Rückstand 0 bis 105 Ticks.
2. **Bootstrap-IPs aus `qubic/go-qubic-nodes`** als Fallback. Deren eigenes README
   warnt, dass die Liste *nicht gepflegt* wird — im Test war tatsächlich nur einer von
   drei erreichbar. Also Fallback, nicht Primärquelle.

Eine epochenweise Neuermittlung ist nicht nötig: Die Peerliste ist ohnehin bei jedem
Abruf aktuell. Sinnvoll ist, sie bei **jedem Ingest-Lauf** neu zu ziehen, statt IPs
fest zu verdrahten — Nodes kommen und gehen.

#### Latenzmessung (alle 10 Peers, TCP-Connect auf 21841)

```
91.210.226.133    21 ms   Tick-Lag   3      45.152.160.226   32 ms   Lag   3
82.165.30.175     31 ms   Tick-Lag   1      85.215.253.84    34 ms   Lag  62
145.239.149.54    32 ms   Tick-Lag  62      82.197.173.130   46 ms   Lag   0
193.29.182.11     32 ms   Tick-Lag 105      82.197.173.133   46 ms   Lag  61
45.152.160.100    32 ms   Tick-Lag  61      147.135.8.168   116 ms   Lag   1
```

Alle 10 waren per TCP erreichbar; auf `REQUEST_ANT_EPOCH_CONTEXT` antworteten **8 von
10**. Empfehlung: nach Tick-Rückstand sortieren und der Reihe nach durchprobieren —
schnelle Latenz nützt nichts, wenn der Node hinterherhinkt.

#### Die Live-Antwort (Epoch 230, verifiziert)

```
epoch                 230
threshold             4000
freshnessWindow      15000
solutionCount         3398  → 3595 rund 15 Minuten später
freeAnnSlotsCount  8385013
maxChildrenPerParent     0   (= unbegrenzt)
dataHash    979cdc2247d2ca4ed3d614bf27896384cb1c9c3d804af6ede6b59fc52c0e3dfa
```

Zwei Dinge sind daran wichtig:

- Der gelieferte `dataHash` und `topologyHash` stimmen **byteweise** mit
  `BPP9000_DATA_HASH` / `BPP9000_TOPOLOGY_HASH` aus `public_settings.h` überein. Damit
  ist bewiesen, dass die in §2.2 analysierte Task-Datei **genau die ist, gegen die das
  Netzwerk gerade minet** — die 50/50-Auswertung gilt für den Live-Task.
- `solutionCount` steigt sichtbar (3398 → 3595 in ~15 Min). Der Wert taugt also als
  echter Fortschrittsindikator für die laufende Epoch.

Implementiert in **`qdr/antnode.py`** (`python -m qdr.antnode` gibt obiges aus).

#### Zwei weitere Quellen, ebenfalls ohne Signatur

Nachträglich getestet: Es gibt zwei weitere unsignierte Abfragen, die deutlich mehr
liefern als erwartet.

**`REQUEST_SYSTEM_INFO` (46/47)**, 128 Byte — Node-Version, Epoch, Tick,
`initialTick`, Anzahl Entities und Transaktionen. Damit lässt sich der Fortschritt
innerhalb der Epoch exakt berechnen (`tick − initialTick`).

**`REQUEST_REVENUE_DATA` (70/71)**, 16.238 Byte — und das ist der eigentliche Fund:

```
unsigned int tick; unsigned short dogeK; long long ipc;
unsigned long long txScore[676];
unsigned long long oracleScore[676];
unsigned long long dogeScore[676];
```

**Drei Revenue-Score-Arrays über alle 676 Computors, komplett ohne Operator-Schlüssel.**
Live gemessen (Epoch 230): `txScore` und `oracleScore` sind für **676/676** Slots
befüllt, `dogeScore` durchgehend 0. Der Top-10-Anteil liegt bei 1,7 % — also sehr
gleichmäßig verteilt.

Einschränkung, die der Core selbst dokumentiert: Diese Werte sind **mitten in der Epoch
näherungsweise** und erst zum Epochenende exakt. Das passt aber genau zur bestehenden
`live` / `sealed`-Unterscheidung des Reports.

#### Was damit nicht geht

Die **Mining-Trefferquote** bleibt unerreichbar: `REQUEST_ANT_IDENTITY_TREE` ist
operator-signiert (40 Byte Payload + 64 Byte Signatur) und wird nur für Identitäten
beantwortet, deren Operator-Schlüssel die Anfrage signiert hat. Ein unsignierter
Versuch wurde vom Node schlicht ignoriert (getestet, keine Antwort vom Typ 73).

**Was ein Operator-Schlüssel ist:** der private Schlüssel des Betreibers eines
Computors — kein API-Key, den man beantragen könnte. Man besitzt ihn, wenn man selbst
einen Computor betreibt. Und selbst dann gälte er nur für die *eigenen* Slots: Für eine
netzwerkweite Trefferquote bräuchte man die Schlüssel aller 676 Betreiber. Das ist
protokollseitig so gewollt und praktisch ausgeschlossen.

**Konsequenz:** Der Trefferquoten-Tacho aus §4.2 entfällt. Was bleibt, ist mehr als
genug für eine eigene Seite — siehe §4.4.

---

### 4.4 Die Seite, wie sie jetzt aussehen soll — "Was passiert durch das Mining"

Nach den Messungen ist klar, was diese Seite sein kann: **keine Prognose-Anzeige,
sondern ein Live-Blick auf die Mining-Maschinerie.** Alles darin ist gemessen, nichts
geschätzt, und nichts davon braucht Sonderrechte.

**1 · Der Schwarm arbeitet (Hauptelement)**
`solutionCount` live, mit Zuwachs pro Minute. Gemessen stieg der Wert von 3398 auf
5330 innerhalb einer knappen Stunde — der Ant-Colony-Effekt ist damit direkt sichtbar:
jede dieser Lösungen hat ihren Elternknoten geschlagen und die Schwelle unterboten.
Dazu der Epochenfortschritt aus `tick − initialTick`.

**2 · Die Aufgabe, gegen die gerechnet wird**
Das Task-Profil aus §1: 18 Eingangs-Trits, stündliche Reihe über ein Jahr, 4 Wochen
Kontextfenster, 8088 bewertete Fenster. Dazu der Live-`dataHash` mit dem Hinweis, dass
er byteweise dem im Core hinterlegten entspricht — der Besucher sieht also, dass hier
die echte, laufende Aufgabe beschrieben wird. Und die 50/50-Zielverteilung aus §2.2 als
das, was sie ist: der Beleg, dass es eine Richtungsreihe ist, und der Grund, warum die
Schwelle 4000 knapp über Zufall liegt.

**3 · Was das Mining einbringt (Revenue-Verteilung)**
Aus `REQUEST_REVENUE_DATA`: `txScore` und `oracleScore` über alle 676 Slots, als
Verteilung. Das ergänzt den Hauptreport um eine **Live-Sicht** — der stützt sich bisher
auf das Bob-Node-Log am Epochenende, hier kommen die Werte mitten in der Epoch direkt
vom Node. Mit klarer Kennzeichnung als "näherungsweise bis Epochenende".

**4 · Die Einordnung, gut sichtbar**
Was das Mining *nicht* tut: keine Vorhersage der nächsten Stunde, keine Aussage über
den heutigen BTC-Kurs, und offiziell nicht einmal bestätigt, *dass* es BTC ist. Dieser
Kasten gehört nach oben, nicht ins Kleingedruckte — er ist der Grund, warum die Seite
seriös bleibt.

---

## 5. Technische Umsetzung

### Unverlinkte Seite
`dashboard/accuracy.html`, aus keiner Navigation verlinkt. Wird über `StaticFiles`
(`api/server.py:518`) automatisch mit ausgeliefert, kein Routing nötig.

**Klarstellung:** Nicht verlinkt heißt nicht privat. Die URL ist für jeden erreichbar,
der sie kennt oder rät, und Suchmaschinen finden sie, sobald sie irgendwo auftaucht.
Soll sie wirklich nicht öffentlich sein, braucht es echten Schutz (Token in der URL
oder Basic Auth) — sag Bescheid, wenn das gewünscht ist.

### Speicherung mit 7-Tage-Verfall
Neue Tabelle im bestehenden SQLite-Store (`qdr/store.py`):

```sql
CREATE TABLE IF NOT EXISTS mining_accuracy (
    epoch          INTEGER NOT NULL,
    observed_at    INTEGER NOT NULL,   -- unix ts
    best_score     INTEGER,            -- Fehleranzahl, kleiner ist besser
    threshold      INTEGER,
    solution_count INTEGER,
    source         TEXT NOT NULL,      -- 'ant-epoch-context' | 'node-tree'
    PRIMARY KEY (epoch, observed_at)
);
CREATE INDEX IF NOT EXISTS ix_macc_ts ON mining_accuracy(observed_at);
```

Aufräumen im bestehenden Ingest-Lauf (`scripts/ingest.py`), keine neue Infrastruktur:

```sql
DELETE FROM mining_accuracy WHERE observed_at < strftime('%s','now') - 7*86400;
```

Die 7 Tage entsprechen ziemlich genau einer Epoch — der Verlauf über mehrere Epochen
wäre damit weg. Falls die Kurve aus §4.2 Punkt 2 über Epochen laufen soll, empfehle
ich, **pro Epoch einen aggregierten Endwert dauerhaft** zu behalten (das sind ~52
Zeilen pro Jahr) und nur die feingranularen Messpunkte nach 7 Tagen zu löschen.

### Endpunkt
`GET /v1/mining-accuracy` — gleiche Konventionen wie der Rest: `status`,
`computed_at`, `code_version`, und 503 mit Begründung, wenn nichts gemessen wurde.

---

## 6. Nachrechnen (Reproduzierbarkeit)

```bash
gh api "repos/qubic/core/contents/data/bpp9000.task" --jq '.content' | base64 -d > bpp9000.task
python - <<'PY'
import struct, collections
d = open('bpp9000.task','rb').read()
magic, ver, N, M, T, P, K = struct.unpack_from('<IIIIQII', d, 0)
topo = 96 + (N + M + 1 + P*K)*4
data = d[topo:]
inb, outb = (N+4)//5, (M+4)//5
outs = [data[i*(inb+outb)+inb] % 3 for i in range(T)]
print('N=%d M=%d T=%d' % (N, M, T), collections.Counter(outs))
PY
```

Erwartete Ausgabe: `N=18 M=1 T=8761 Counter({1: 4417, 0: 4344})`

---

## 7. Status der offenen Punkte

Beide Fragen sind geklärt:

- **Node-Zugang** — funktioniert ohne eigenen Node und ohne Sonderrechte. Peers über
  `api.qubic.li/Public/Peers`, drei unsignierte Abfragen (46, 70, 76) liefern alles
  Nötige. Implementiert und live getestet in `qdr/antnode.py`.
- **Operator-Schlüssel** — nicht vorhanden und **nicht beschaffbar**: Er gehört dem
  Betreiber eines Computors, ist kein beantragbarer Zugang, und selbst mit einem
  einzelnen käme man nur an dessen eigene Slots. Die Mining-Trefferquote entfällt damit
  endgültig.

**Umsetzbar ist §4.4** — die Seite zeigt die Mining-Maschinerie live statt einer
Prognose. Damit ist sie inhaltlich sogar näher am Projektcharakter: gemessene Fakten
über das Netzwerk, keine Vorhersagen.

Nächster Schritt: `dashboard/mining.html` bauen, `qdr/antnode.py` in den Ingest-Lauf
hängen, Endpunkt `/v1/mining` ergänzen.

---

## Quellen

- `qubic/core` — `doc/ant_colony_mining.md`, `src/public_settings.h`,
  `src/mining/score_bpp9000.h`, `src/mining/task_file.h`, `data/bpp9000.task`
- [Qubic Blog: Ant Colony Mining Is Live (Epoch 228)](https://qubic.org/blog-detail/qubic-ant-colony-mining-live-epoch-228)
- [Qubic Docs: UPoW](https://docs.qubic.org/learn/upow/)
- `rpc.qubic.org` — `/v1/tick-info`, `/v1/latest-stats` (geprüft 2026-09-09, Epoch 230)
