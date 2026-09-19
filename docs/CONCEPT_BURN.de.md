# Burn-Report — Konzept

*Eine dauerhafte Tagesansicht der verbrannten Qubic-Supply: was tatsächlich messbar ist,
woher die Zahlen kommen, und wie die Seite sie darstellt, ohne eine Auflösung zu erfinden,
die die Daten nicht hergeben.*

Status: Entwurf v0.1 · Sprache: Deutsch (die englische Fassung wird synchron gehalten:
[`CONCEPT_BURN.md`](CONCEPT_BURN.md))

---

## 1. Der Anlass

`explorer.qubic.org` zeigt eine **Burned Supply**. Das ist eine einzelne kumulative Zahl —
53.662.829.138.067 QU zum Zeitpunkt dieses Konzepts (19.09.2026, Epoche 231). Sie
beantwortet „wie viel wurde insgesamt verbrannt" und sonst nichts. Kein Tageswert, kein
Epochenwert, kein Verlauf, und keine Aufschlüsselung, *wofür* verbrannt wurde.

Die Anforderung: den Wert pro Tag speichern und anzeigen — nach Tag, Epoche und Jahr — als
Diagramm, oder als etwas Besseres als ein Diagramm.

## 2. Der Befund, der das gesamte Design bestimmt

**Der Burn-Zähler der öffentlichen RPC bewegt sich zwischen Epochengrenzen nicht.**

Direkt gemessen am 19.09.2026:

| Messung | `burnedQus` | Tick |
|---|---|---|
| t₀ | 53.662.829.138.067 | 80.822.754 |
| +94 s | 53.662.829.138.067 | 80.822.884 |
| +120 s | 53.662.829.138.067 | 80.822.919 |
| +180 s | 53.662.829.138.067 | 80.823.001 |
| +214 s | 53.662.829.138.067 | 80.823.048 |
| +275 s | 53.662.829.138.067 | 80.823.129 |

375 Ticks in ~4,6 Minuten. Der Zähler änderte sich um keinen einzigen QU
(`circulatingSupply` blieb über dieselben Messungen ebenso konstant).

Parallel dazu meldete ein Bob-Node für die Ticks 80.820.000–80.820.500 im selben Zeitraum
**913 Burn-Transfers von je exakt 1.000.000 QU** — 913.000.000 QU in 500 Ticks.

Es wird also kontinuierlich verbrannt; `latest-stats.burnedQus` berichtet es nur nicht
kontinuierlich. Der Wert ist ein **Epochengrenzen-Aggregat**. Eine Epoche dauert ~4,4 Tage.

**Die Konsequenz für dieses Feature.** `burnedQus` stündlich abzufragen und „pro Tag" zu
speichern, ergäbe eine Tabelle identischer Zahlen über vier Tage und dann einen Sprung. Das
als Tagesdiagramm zu zeichnen und die flache Strecke mit „0 QU heute verbrannt" zu
beschriften, wäre falsch — an diesem Tag wurden ~426 Milliarden QU verbrannt, der Zähler
hatte es nur noch nicht veröffentlicht. Den Sprung rückwirkend über die Tage zu
interpolieren wäre schlimmer: eine erfundene Kurve, präsentiert als Messung.

Die stehende Regel dieses Projekts ist, dass nichts auf der Seite geschätzt oder erfunden
ist (`README`: „No sample data, ever"). Die Tagesauflösung muss also *verdient* werden,
indem wir die Ereignisse selbst zählen.

## 3. Woher die Daten wirklich kommen

Zwei Quellen mit zwei verschiedenen Aufgaben.

### 3.1 Bob-Node — die Messung

`qubic_getLogs` eines Bob-Nodes über einen Tick-Bereich liefert die einzelnen
Burn-Ereignisse. Das Projekt spricht für das Computor-Revenue ohnehin mit Bob
(`qdr/bob.py`) — das hier ist die Erweiterung einer bestehenden Integration, keine neue
Abhängigkeit.

Zwei Ereignisformen sind relevant, und sie sind **nicht** dasselbe:

**(a) `QU_TRANSFER` an die Null-Adresse.** Ziel
`AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAFXIB`. Gemessen über 500 Ticks:
913 Transfers zu **je exakt 1.000.000 QU**. Das sind die Computor-Burns, die dieses Projekt
bereits dokumentiert (`DATA_SOURCES` §6, `CONCEPT` §4.2) — der einheitliche Abfluss, den
jeder Computor erzeugt. Hochgerechnet: ~426 Mrd. QU/Tag, ~2,56 Bio. QU/Epoche.

> **Implementierungsfalle, einmal bereits bezahlt.** Eine Qubic-Identity hat **60 Zeichen**.
> Wer die Null-Adresse über ein 40-Zeichen-Präfix matcht, trifft still gar nichts, und die
> Burn-Summe kommt als saubere, plausible Null heraus. `qdr/revenue.py` hat genau dafür
> `BURN_PREFIXES` und `_is_burn_address()` — wiederverwenden, nicht neu herleiten.

**(b) `BURNING`-Log-Ereignisse.** Ein eigener Log-Typ mit `amount` und — das Wertvolle —
**`contractIndexBurnedFor`**. Das ist die einzige Quelle, die sagt, *wofür* verbrannt wurde.
Gemessen in `qubic_getEndEpochLogs`:

| Epoche | Ereignisse | Summe | Nach Contract-Index |
|---|---|---|---|
| 225 | 5 | 385.481 QU | 27: 35.001 · 19: 350.000 · 12: 480 · 10: 0 · 9: 0 |
| 226 | 5 | 322.652 QU | 27: 10.009 · 19: 300.000 · 12: 0 · 10: 0 · 9: 12.643 |
| 227 | 5 | 338.122 QU | 27: 44.006 · 19: 250.000 · 12: 604 · 10: 0 · 9: 43.512 |
| 228 | 4 | 478.850 QU | 19: 450.000 · 12: 0 · 10: 0 · 9: 28.850 |

Man beachte die Größenordnung: das sind ~0,3–0,5 **Millionen** QU pro Epoche gegenüber
~2,56 **Billionen** QU aus den Computor-Burns. Beides sind echte Burns; sie unterscheiden
sich um sechs Größenordnungen. Die Seite darf sie nicht in einen Balken addieren und die
Contract-Burns darin verschwinden lassen — sie sind gerade deshalb interessant, weil sie
zuordenbar sind.

Die `BURNING`-Ereignisse traten in den End-Epoch-Logs auf und **nicht** in der geprüften
Stichprobe normaler Ticks. Ob Contract-Burns auch mitten in der Epoche vorkommen, ist eine
offene Frage (§9, Q1) — der Ingest beantwortet sie empirisch, statt dass das Konzept sie
annimmt.

### 3.2 RPC `burnedQus` — der Anker

`GET /v1/latest-stats` → `data.burnedQus`, dieselbe Zahl, die der Explorer zeigt. Sie ist
die maßgebliche kumulative Gesamtsumme. Ihre Aufgabe hier ist **Abgleich, nicht
Auflösung**: an jeder Epochengrenze wird unsere Ereignissumme gegen das offizielle Delta
geprüft.

```
offizielles_delta = burnedQus(Ende Epoche N) − burnedQus(Ende Epoche N−1)
gemessenes_delta  = Σ unserer Burn-Ereignisse in Epoche N
coverage          = gemessenes_delta / offizielles_delta
```

Die Coverage wird in jeder Antwort mitgeliefert. Übersieht unser Scan eine Burn-Kategorie,
fällt die Coverage unter 1,0 und sagt es — dieselbe Disziplin, die `linkage_coverage: 0`
bei der Betreiber-Zuordnung bereits anwendet. Ein stiller Fehlbetrag ist genau der
Fehlermodus, gegen den hier konstruiert wird.

### 3.3 Der Aufwand, ehrlich benannt

Bobs Tick-Logs sind schwer: **~2,6 MB für 500 Ticks**, und der Node deckelt eine Anfrage
bei ~1000 Ticks, egal welcher Bereich angefragt wird. Eine volle Epoche mit 1,4 Mio. Ticks
sind ~1.400 Aufrufe und Gigabytes an JSON. Das ist keine Operation pro Request und nicht
einmal eine beiläufige pro Epoche.

Zwei Konsequenzen, beide bereits Projektmuster:

1. **Der Worker macht es, inkrementell.** Jeder Durchlauf scannt nur vom letzten gescannten
   Tick bis zum aktuellen und speichert das Aggregat. `scan_tick_transfers()` paginiert
   bereits um das Node-Limit herum; es braucht ein `max_calls`-Budget pro Durchlauf und
   einen Fortsetzungszeiger.
2. **Gespeichert werden nur Aggregate, nie die Rohereignisse.** Eine Tick-Bucket-Zeile,
   nicht 913 Zeilen pro 500 Ticks.

## 4. Was gespeichert wird

Eine neue Tabelle, nach den bestehenden Konventionen des Stores (`qdr/store.py`):
unveränderliche Beobachtungen, Upsert mit Dedupe, kein stilles Überschreiben.

```sql
-- Burn-Summen pro Tick-Fenster. Die Rohereignisse werden NICHT aufbewahrt: bei
-- ~900 Burns je 500 Ticks ist das Ereignis-Log Gigabytes pro Epoche und sagt
-- nichts, was das Aggregat nicht sagt. Das Fenster ist das, was eine Zahl für
-- Dritte nachrechenbar macht.
CREATE TABLE IF NOT EXISTS burn_buckets (
    from_tick     INTEGER NOT NULL,   -- inklusive
    to_tick       INTEGER NOT NULL,   -- inklusive
    epoch         INTEGER NOT NULL,
    day           TEXT NOT NULL,      -- UTC-Datum, 'YYYY-MM-DD', aus den Event-Timestamps
    burned        INTEGER NOT NULL DEFAULT 0,   -- Σ QU_TRANSFER an Null
    burn_events   INTEGER NOT NULL DEFAULT 0,
    contract_burned INTEGER NOT NULL DEFAULT 0, -- Σ BURNING-Ereignisse
    by_contract   TEXT,               -- json {contractIndex: amount}
    scanned_at    INTEGER NOT NULL,
    PRIMARY KEY (from_tick, to_tick)
);
CREATE INDEX IF NOT EXISTS ix_burn_epoch ON burn_buckets(epoch);
CREATE INDEX IF NOT EXISTS ix_burn_day   ON burn_buckets(day);

-- Der offizielle kumulative Zähler, an Epochengrenzen abgetastet. Das ist der
-- Anker, gegen den die gemessenen Buckets abgeglichen werden, und die einzige
-- Zahl, die direkt mit explorer.qubic.org vergleichbar ist.
CREATE TABLE IF NOT EXISTS burn_totals (
    epoch         INTEGER PRIMARY KEY,
    burned_total  INTEGER NOT NULL,   -- burnedQus zum Epochenschluss
    circulating   INTEGER,
    tick          INTEGER,
    observed_at   INTEGER NOT NULL
);
```

**Warum eine Tagesspalte und nicht nur Ticks.** „Pro Tag" ist das, was gefordert war, und
eine Tick-Nummer ist kein Datum. Bobs Log-Einträge tragen einen `timestamp`
(`"26-09-02 12:00:05"` in den beobachteten Daten), der Tag wird also aus den Ereignissen
abgelesen statt aus einer angenommenen Tick-Rate berechnet. Wo ein Bucket über Mitternacht
reicht, wird er an der Grenze geteilt — ein Bucket ist eine Scan-Einheit, keine
Berichtseinheit.

**Aggregation für die Ansichten.** Tag / Epoche / Jahr sind allesamt `SUM(burned) GROUP BY`
über diese eine Tabelle. Nichts wird dreifach gespeichert.

### 4.1 Die Ehrlichkeitsregel für die Historie

Der Worker kann Burns erst **ab dem ersten je gescannten Tick** messen. Alles davor hat
keinen Tageswert und wird nie einen bekommen — Bobs Historie ist endlich, und ein Jahr
Ticks nachzuscannen ist nicht verhältnismäßig.

Die Reihe hat daher zwei klar getrennte Regime, und die API beschriftet jeden Punkt:

| Regime | Quelle | Auflösung | Label |
|---|---|---|---|
| Vor dem ersten Scan | `burnedQus`-Deltas an Epochengrenzen | pro Epoche (~4,4 T) | `measured: false` |
| Ab dem ersten Scan | Bob-Ereignisse, aggregiert | pro Tag / pro Stunde | `measured: true` |

Das Diagramm zeichnet das ältere Regime als epochenbreite Stufen und das neuere als
Tageskurve, und sagt, was was ist. Es glättet das alte Regime **nicht** zu vorgetäuschten
Tageswerten. Der Leser muss sehen können, wo die echte Messung beginnt.

## 5. API

Drei Endpunkte nach den bestehenden `/v1`-Konventionen (read-only, CORS offen, jede Antwort
benennt ihre Herkunft).

```
GET /v1/burn/latest
    { burned_total, circulating, burned_share, epoch, tick,
      rate: { per_day, per_epoch, measured_over_ticks },
      last_24h: { burned, events, measured },
      coverage: { measured_delta, official_delta, ratio },
      source: "rpc:latest-stats + bob:getLogs", observed_at }

GET /v1/burn/series?by=day|epoch|year&limit=90
    { by, series: [ { key, from_tick, to_tick, burned, events,
                      contract_burned, cumulative, measured } ],
      first_measured_day, generated_at, code_version }

GET /v1/burn/contracts?epoch=231
    { epoch, total, by_contract: [ { index, burned, events, share } ] }
```

`/v1/burn/latest` ist der günstige Endpunkt, den eine Live-Seite pollt; er wird wie
`/v1/pulse` gecacht (TTL wenige Sekunden), damit viele Betrachter zu einem einzigen
Upstream-Read zusammenfallen. Der Series-Endpunkt ist standardmäßig gefenstert — Historie
wächst nur, und `DEFAULT_TIMESERIES_EPOCHS` setzt den Präzedenzfall.

Das Dashboard-Bundle (`/v1/dashboard-data`) bleibt unangetastet. Burn ist eine eigene Seite
mit eigenen Fetches; es ins Bundle zu falten, würde die Payload des Hauptreports für Daten
wachsen lassen, die er nicht zeigt.

## 6. Die Seite: `dashboard/burn.html`

Ein Geschwister von `mining.html` — dieselben Design-Tokens, dieselbe Theme-Behandlung,
derselbe i18n-Mechanismus, kein Build-Schritt, kein CDN, keine externe Library. Verlinkt im
Header neben „Mining Live".

### 6.1 Hero — der Ofen

Oben auf der Seite steht eine Live-Burn-Visualisierung, und ihre Regel lautet: **jedes
Partikel ist ein gemessenes Ereignis, niemals Dekoration.**

- Ein Zähler mit der kumulativen Gesamtsumme, als hochlaufender Kilometerzähler animiert.
- Darunter ein Ofen: jedes 1.000.000-QU-Burn-Ereignis, das der letzte Scan tatsächlich
  erfasst hat, fällt als Funke hinein und wird verzehrt. 913 Ereignisse pro 500 Ticks sind
  rund 5/Sekunde — genug, um wirklich lebendig zu wirken, ohne erfunden zu sein.
- Meldet der Scan nichts, fällt **nichts**. Ein toter Ofen ist eine wahre Aussage über die
  Daten und unendlich besser als eine dekorative Schleife, die Fantasie-QU verbrennt,
  während der Node nicht erreichbar ist.
- Burn-Rate (QU/Tag) und der verbrannte Anteil an der Gesamtsupply stehen als schlichte
  Zahlen daneben, denn das ist es, was Leute zitieren werden.

Die Bewegung ist gedeckelt, und `prefers-reduced-motion` schaltet die Partikel vollständig
ab und lässt Zähler und Zahlen stehen — die Seite muss ohne Animation vollständig lesbar
sein.

### 6.2 Die Reihe — Diagramm

Unter dem Hero das, was eigentlich gefragt war: die gespeicherte Historie, mit einer
segmentierten Umschaltung **Tag · Epoche · Jahr** und einem Schalter **pro Periode
verbrannt** vs. **kumulativ**.

Gezeichnet als Inline-SVG, handgeschrieben wie die übrigen Diagramme des Dashboards:

- Balken für pro Periode, eine Linie für kumulativ.
- Das Regime vor der Messung wird sichtbar anders dargestellt (schraffiert / geringerer
  Kontrast), mit einer Markierung „hier beginnt die Messung". Die Legende benennt beides.
- Hover/Tap liefert einen Tooltip mit der exakten Zahl, dem zugrunde liegenden Tick-Fenster
  und dem Hinweis, ob dieser Punkt gemessen oder aus dem Epochenzähler abgeleitet ist.
- Auf dem Telefon behält das Diagramm seinen eigenen horizontalen Scroll-Container; der
  Seitenkörper scrollt nie seitwärts.

### 6.3 Contract-Burns

Ein kleines eigenes Panel, nicht in die Hauptbalken gemischt — der Unterschied von sechs
Größenordnungen würde sie unsichtbar machen. Eine Rangliste pro Contract-Index mit Anteil,
und der ehrliche Hinweis, dass ein Contract-Index noch kein Contract-*Name* ist: die
Zuordnung Index → Name braucht eine Quelle, die wir noch nicht haben (§9, Q2). Bis dahin
wird der Index als Index gezeigt.

### 6.4 Mobil

Gleiches Vorgehen wie bei den bestehenden Seiten: eine Spalte unter ~700 px, der
Hero-Zähler skaliert per `clamp()` herunter, die segmentierte Umschaltung wird
vollbreit, Touch-Ziele ≥ 44 px, und die Partikelzahl wird auf kleinen Bildschirmen
reduziert (eine Telefon-GPU braucht keine 900 Sprites).

### 6.5 Sprachen

DE und EN über das `data-i18n`-Attribut-Wörterbuch, das die anderen Seiten bereits nutzen
(`mining.html` hat ~90 Schlüssel, `index.html` ~46). Die Sprache folgt derselben
Persistenz und demselben `navigator.language`-Fallback. Beide Wörterbücher werden
vollständig ausgeliefert — ein fehlender Schlüssel darf einem Leser niemals als roher
Schlüsselname erscheinen.

## 7. Wie es läuft

Der Ingest-Worker bekommt eine weitere Aufgabe neben denen, die `scripts/worker.sh` bereits
hat:

```
scripts/ingest.py --burn-scan          # den Burn-Scan bis zum aktuellen Tick vorrücken
scripts/ingest.py --burn-scan --from-tick N
scripts/ingest.py --burn-status        # was gescannt wurde, und die Coverage
```

In der Watch-Schleife läuft er nach dem Live-Epochen-Refresh, mit einem Aufrufbudget pro
Durchlauf, damit ein Durchlauf weder den Worker noch den öffentlichen Bob-Node
monopolisiert. Er speichert seinen Fortsetzungszeiger, sodass ein Neustart fortsetzt statt
neu zu scannen.

An jeder Epochengrenze tastet er `burnedQus` in `burn_totals` ab und berechnet die Coverage
für die gerade geschlossene Epoche. Eine Coverage, die von 1,0 wegdriftet, ist ein Befund
und kein zu versteckender Fehler — sie bedeutet, dass es eine Burn-Kategorie gibt, die der
Scan noch nicht erkennt, und sie gehört auf die Seite und in `/v1/burn/latest`.

## 8. Reihenfolge der Umsetzung

Jeder Schritt ist für sich nützlich und für sich überprüfbar.

| # | Schritt | Fertig, wenn |
|---|---|---|
| 1 | `burn_totals` + Abtastung an Epochengrenzen im Worker | `/v1/burn/latest` liefert die offizielle Summe und stimmt mit dem Explorer überein |
| 2 | `burn_buckets` + Bob-Scan (inkrementell, budgetiert, fortsetzbar) | eine Tagessumme ist aus Ereignissen gemessen, Tests decken die 60-Zeichen-Adressfalle ab |
| 3 | `/v1/burn/series` mit Tag/Epoche/Jahr + Trennung gemessen/abgeleitet | die Reihe beantwortet alle drei Granularitäten und beschriftet jeden Punkt |
| 4 | `dashboard/burn.html` — zuerst das Diagramm, DE/EN, mobil | das Diagramm ist korrekt und auf dem Telefon lesbar, bevor es irgendeine Animation gibt |
| 5 | Der Ofen-Hero, getrieben von echten Ereigniszahlen | Partikel hören auf, wenn die Daten aufhören |
| 6 | Contract-Burn-Panel | Aufschlüsselung pro Index mit Anteilen |

Diagramm vor Animation ist bewusst gewählt: das Diagramm ist das Ergebnis, der Ofen ist
das, was Leute hinschauen lässt. Die andere Reihenfolge riskiert eine schöne Seite mit
einer falschen Zahl darin.

## 9. Offene Fragen

**Q1 — Treten `BURNING`-Ereignisse mitten in der Epoche auf?** Beobachtet in
`qubic_getEndEpochLogs` (4–5 pro Epoche); die 500-Tick-Stichprobe normaler Ticks enthielt
keine. Der Scan zählt sie, wo immer sie auftreten; die Antwort wird hier festgehalten,
sobald eine volle Epoche gescannt ist. Bis dahin spiegelt `contract_burned` womöglich nur
Ereignisse an der Epochengrenze.

**Q2 — Was sind die Contract-Indizes?** Gemessen: 9, 10, 12, 19, 27. Index 19 ist der
größte und stetigste (250k–450k QU/Epoche). Die Zuordnung Index → Contract-Name braucht
eine Quelle (Core-Quelltext oder die Community). Solange es keine gibt, zeigt die Seite
Indizes und keine geratenen Namen.

**Q3 — Ist der Computor-Burn der *gesamte* Nicht-Contract-Burn?** Jeder Null-Transfer in
der Stichprobe betrug exakt 1.000.000 QU, was zum bekannten einheitlichen Computor-Abfluss
passt. Landet die Coverage (§3.2) nahe 1,0, lautet die Antwort ja; fällt sie kürzer aus,
existiert ein weiterer Burn-Pfad, und die Lücke zeigt darauf. Genau deshalb wird die
Coverage veröffentlicht statt angenommen.

**Q4 — Wie weit zurück kann Bob liefern?** Bestimmt, wie viel des „vor der Messung"-Regimes
sich durch einen einmaligen Backfill in gemessene Daten verwandeln ließe. Ein Experiment
wert, bevor man es für unverhältnismäßig erklärt.

## 10. Was das nicht ist

Es erklärt nicht, *warum* Qubic verbrennt, was es verbrennt, es prognostiziert nicht, und
es stellt die Burn-Rate nicht als Bewertungssignal dar. Es misst eine veröffentlichte Größe
in feinerer Auflösung als sie veröffentlicht wird, legt offen, wie, und zeigt, wo die
eigene Messung beginnt. Das ist derselbe Vertrag, unter dem der übrige Report arbeitet.
