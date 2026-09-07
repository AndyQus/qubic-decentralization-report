# Qubic Decentralization Report — Konzept

*Arbeitskonzept für ein Tool, das aus self-reported und On-chain-Daten einen laufenden
Bericht „Wie dezentral ist Qubic?" erzeugt und ihn als API bereitstellt, die Explorer
einbinden können.*

Status: Entwurf v0.2 · Owner: (Qubic-Community-Projekt) · Sprachen: Diese Datei ist die
deutsche Fassung; die englische liegt unter `docs/CONCEPT.md`. Beide werden synchron
gehalten.

---

## 1. Hintergrund — was CFB konkret verlangt hat

In der Diskussion im `#computor-operator`-Kanal hat Come-from-Beyond (CFB) eine konkrete,
umsetzbare Aufgabe formuliert:

> „Prepare a report showing revenue metrics and their dynamics. And clustering with number
> of computors in each cluster."
>
> „Explorers should add this report to their sites. It's a very important report, showing
> how decentralized Qubic is."

Dahinter stehen zwei Dinge:

**Das Problem — Kollusion / Sybil unter den Computors.** Das Qubic-Netzwerk wird von 676
Computors betrieben. Auf dem Papier sieht das nach 676 unabhängigen Betreibern aus. In der
Praxis kann eine einzelne Partei (ein Pool) viele Computor-Slots gleichzeitig kontrollieren
und koordinieren. Das ist ein klassischer **Sybil-Fall**: eine Entität, viele Identitäten.
AndreiBLRs Bild „Wir sehen Rauch und wissen, dass es brennt, aber die Ermittler sind die,
die das Feuer gelegt haben" beschreibt genau das — die Anzeichen der Konzentration sind
sichtbar, aber die, die ermitteln könnten, profitieren selbst davon.

**Die vorgeschlagene Abwehr — Self-Reporting.** CFBs früherer Punkt:

> „Qubic was the first implementing such theoretical anti-Sybil technique as self-reporting
> … Let's make it look more solid by using it to the fullest."

Self-Reporting ist eine anerkannte, aber selten umgesetzte Anti-Sybil-Idee: Betreiber
deklarieren freiwillig, welche Identitäten (Computor-Slots) sie kontrollieren. Qubic hat das
Rohmaterial dafür bereits, weil Pools veröffentlichen, welche ihrer Mitglieder Slots halten.
„Use it to the fullest" heißt: diese selbst deklarierte Zugehörigkeit in eine **öffentliche,
laufend aktualisierte Dezentralisierungs-Kennzahl** verwandeln, die auf den Explorern lebt.

CFBs Haltung zu den Risiken (sinngemäß aus dem Thread): Wenn Computors kolludieren, ist der
schlimmste Fall *suboptimaler Service / Performance*, keine kaputte Chain — und wenn die
Community die Konzentration klar *sehen* kann, kann sie selbst entscheiden. Aufgabe des
Tools ist also nicht, jemanden anzuklagen, sondern **Konzentration messbar und sichtbar zu
machen**.

Ein Bounty von ~2 Mrd. QU (Eko 1B + Broms 1B) ist für einen Report ausgelobt, der akzeptiert
und von Explorern eingebunden wird.

---

## 2. Was das Tool erzeugt

Ein logisches Artefakt — den **Decentralization Report** — in drei Formen:

1. **Eine maschinenlesbare API** (`/report/latest`, `/report/{epoch}` plus Unter-Ressourcen).
   Das ist das primäre Ergebnis: Explorer (qubic.org-Explorer, qubic.li, jetskis
   ANN-Explorer usw.) rufen sie ab und stellen sie beliebig dar. *Wir liefern die Daten;
   der Explorer ist der Konsument.*
2. **Ein Referenz-Dashboard** — unser eigenes Front-End, das dieselben Daten rendert,
   inklusive der **animierten Ansichten** (siehe §6). Dient zugleich als
   „Referenzimplementierung", die ein Explorer bei Bedarf übernehmen kann.
3. **Ein periodischer statischer Snapshot** (JSON + gerendertes PNG/SVG) pro Epoche, damit
   der Report archivierbar und zitierbar bleibt, selbst wenn der Live-Dienst ausfällt.

Jede Zahl im Report ist **aus öffentlichen Daten reproduzierbar** und wird mit ihren Inputs
ausgeliefert, sodass niemand uns vertrauen muss — ein Explorer oder ein Skeptiker kann alles
nachrechnen.

---

## 3. Datenquellen

| Ebene | Quelle | Was wir bekommen | Rolle |
|---|---|---|---|
| Konsens / Slots | Qubic RPC 2.0 (`rpc.qubic.org`) + Core-Node-Daten | Computor-Liste pro Epoche (676 IDs), Tick-Daten, Quorum-Infos | Grundwahrheit |
| Revenue | RPC / Archiver, vollständiges Transaction-Tracking (§4.3) | Revenue pro Computor pro Epoche, epochenscharf und vollständig paginiert | Grundwahrheit |
| **On-chain-Verknüpfung** | Qubic-Ledger via RPC | Auszahlungsziele, Fund-Flows zwischen Identitäten, der Payout-Graph | **Primäre Zuordnung** |
| Self-Reporting | Pool-APIs & öffentliche Deklarationen (qubic.li u. a.) plus ein kuratiertes Registry in diesem Repo | Welche Slots ein Pool/Betreiber für sich beansprucht | Beschriftet + ergänzt die Verknüpfung |
| Verhalten (optional) | Timing/Muster der Solution-Submissions, soweit beobachtbar | Signale, die mögliche nicht deklarierte Cluster markieren | Nur Markierung |

Die genauen Endpoint-Namen werden während der Umsetzung gegen die Live-RPC verifiziert;
siehe `docs/DATA_SOURCES.md` (wird im Data-Mapping-Schritt gefüllt).

---

## 4. Die Analyse-Engines

### 4.1 Revenue-Metriken & Dynamik

Pro Epoche, pro Computor: erzieltes Revenue; aggregiert zu Cluster-Summen. Konzentration
wird mit gängigen, gut begründbaren Indizes gemessen, damit das Ergebnis keine Meinungssache
ist:

- **Gini-Koeffizient** des Revenues über die Betreiber (0 = perfekt gleich, 1 = eine Entität
  bekommt alles).
- **Herfindahl-Hirschman-Index (HHI)** der Revenue-Anteile der Betreiber.
- **Top-N-Anteil** (z. B. Anteil der größten 1 / 3 / 5 Betreiber).
- **Nakamoto-Koeffizient** — wie viele Betreiber kolludieren müssen, um >⅓ / >½ der Slots
  oder des Revenues zu kontrollieren (die zentrale „Wie dezentral"-Zahl).

„Dynamik" = alle obigen Werte als **Zeitreihe über die Epochen**, sodass Trends (steigt oder
sinkt die Konzentration?) sichtbar werden.

### 4.2 Clustering — Slots → Betreiber

Das ist der Kern. **On-chain-Verknüpfung ist die Standard-Zuordnungsebene, kein
Auffangbecken.** Self-Reporting ist das, was Betreiber *behaupten*; die On-chain-Verknüpfung
über den Payout-Graph ist das, was das Ledger *zeigt*. Beide laufen in jeder Epoche
unabhängig voneinander, und der Report veröffentlicht beide plus ihre Differenz. Ein Slot
gilt nur dann als „unattributed", wenn das Ledger selbst keine Verknüpfung zeigt — nie bloß
deshalb, weil niemand ein Self-Reporting eingereicht hat.

Die drei Ebenen und was jede schlussfolgern darf:

1. **On-chain-Verknüpfung (Standard, immer berechnet).** Abgeleitet aus dem vollständigen
   Payout-Graph: Jede Auszahlung am Epochenende an eine Computor-Identität wird
   weiterverfolgt. Slots, deren Auszahlungen auf demselben Ziel zusammenlaufen oder die aus
   einer gemeinsamen Quelle gespeist werden, sind ein wirtschaftlicher Eigentümer. Das läuft
   ganz ohne Registry und erzeugt das Basis-Clustering.
2. **Self-reported (Deklarationsebene, gemäß CFB).** Pools deklarieren ihre Slots in ein
   versioniertes Registry. Das *beschriftet* Verknüpfungs-Cluster (aus „Linked (ABCD…)" wird
   „Qubic.li Pool") und kann Slots zusammenführen, die die Chain noch nicht verknüpft hat. Es
   *trennt* aber nie, was die Chain verknüpft hat — eine Deklaration kann einen
   Auszahlungspfad nicht widerlegen.
3. **Verhaltens-Fingerprints (nur Markierung).** Korrelierte Submission-Timings/-Quellen
   können Slots *markieren* — als „mögliches nicht deklariertes Cluster", nie als harte
   Anschuldigung.

**Konfidenzstufen** bilden diese Reihenfolge ab:

| Stufe | Bedeutung |
|---|---|
| `declared+linked` | Self-reported **und** durch den Payout-Graph bestätigt — am stärksten |
| `linked` | On-chain bewiesen, (noch) nicht deklariert — die nicht deklarierte Konzentration |
| `declared` | Deklariert, noch keine On-chain-Bestätigung — reine Vertrauensbasis |
| `flagged` | Nur Verhaltenskorrelation, kein Beweis |
| `unattributed` | Das Ledger zeigt keine Verknüpfung und niemand hat deklariert — echt unbekannt |

**Warum die Reihenfolge für die Kennzahlen entscheidend ist.** Jeden nicht deklarierten Slot
als eigenen Betreiber zu zählen ist nicht neutral, sondern **systematisch zu optimistisch**:
Ein unverknüpfter Einzel-Slot bläht die Betreiberzahl auf und schiebt Nakamoto, Gini und HHI
Richtung „dezentraler als die Realität". Der bisherige Entwurf machte genau das zum Standard
und die On-chain-Ebene zur optionalen Anreicherung — das ist verkehrt herum. Der Report weist
deshalb eine **Verknüpfungs-Abdeckung** aus, damit erkennbar ist, wie viel der 676 tatsächlich
aufgelöst und wie viel nur angenommen ist.

Ausgabe pro Cluster: Anzahl Computors, Revenue, Anteil an Top-451, Konfidenz, verwendete
Evidenz und ein **deklariert-vs-erkannt**-Delta, das den „Rauch" quantifiziert.

### 4.3 Revenue-Ableitung — vollständiges Transaction-Tracking

Konzentrationszahlen sind nur so gut wie das Revenue darunter, und genau hier laufen
unabhängige Implementierungen auseinander. Die Ableitung ist deshalb exakt spezifiziert und
überprüfbar, statt auf ein Best-Effort-Scannen zu setzen:

- **Epochenscharf.** Auszahlungen werden der Epoche zugeordnet, für die sie abgerechnet
  werden — über deren Tick-Bereich (`initialTick` → `initialTick` der Folgeepoche), nicht
  danach, wann ein Transfer zufällig auftaucht. Qubic zahlt mit einer Epoche Verzögerung; ein
  Scan ohne Bereichsgrenzen vermischt zwei Epochen.
- **Vollständig.** Die Transfer-Historie wird **bis zur Erschöpfung paginiert**. Eine
  abgeschnittene erste Seite unterzählt still gerade die größten Betreiber — was die
  Konzentration *nach unten* verzerrt.
- **Auf einer verifizierten Arbitrator-Identität verankert.** Die Auszahlungsquelle wird gegen
  die Boundary-Tick-Transfers bestätigt und mit Tick-Nachweis im Repo festgehalten, statt als
  ungeprüfte Konstante mitgeschleppt zu werden.
- **Gegenprüfbar.** Jede Epoche liefert den Tick-Bereich und die Summen mit, aus denen sie
  abgeleitet wurde — so kann eine dritte Partei nachrechnen und sieht genau, woher eine
  Abweichung kommt: welche Epoche, welches Tick-Fenster, welche Transfers.
- **Abgeglichen.** Die abgeleiteten Summen werden gegen das bekannte Verteilungsmodell geprüft
  (Obergrenze pro Computor nach Betreibergebühr, Performance-/Slashing-Faktoren). Eine
  Abweichung wird als Warnung im Payload gemeldet, statt weggemittelt zu werden.

Wo eine Epoche nicht vollständig abgeleitet werden kann, wird sie als `partial` markiert und
aus den Kennzahlen herausgehalten, statt so veröffentlicht zu werden, als wäre sie belastbar.
### 4.4 Slot-Übergänge — Betreiber-Abgänge und Re-Identifikation überstehen

Alle bisherigen Schichten beschreiben **eine einzelne Epoche**. Das genügt nicht für die
Frage, die die Community tatsächlich stellt, wenn ein Pool dichtmacht: *Wohin gehen seine
Slots?* Ein Betreiber, der geht, nimmt seinen Anteil an den 676 Slots nicht mit — die Slots
werden neu vergeben, und der Konzentrationseffekt dieser Übergabe ist in keiner
Einzelepochen-Momentaufnahme sichtbar, weil jede Epoche für sich betrachtet in sich stimmig
aussieht.

Das ist die bauartbedingte Schwachstelle eines Per-Epochen-Reports. Konkret: Schließt ein
großer Pool, gibt es drei Ausgänge, die in einer Momentaufnahme **numerisch nicht
unterscheidbar** sind:

1. Slots gehen an echte, unabhängige neue Betreiber → realer Dezentralisierungs*gewinn*.
2. Slots werden von den verbleibenden großen Betreibern aufgesogen → Konzentration *steigt
   deutlich*.
3. Derselbe Betreiber kommt unter frischen Identitäten und ohne neue Selbstauskunft zurück →
   die Konzentration ist **real unverändert, scheint aber zu sinken**, weil die
   Nachfolge-Slots als unverknüpfte Einzelstücke in den Report eingehen.

Fall 3 ist der gefährliche: Ein abgehender Betreiber, der unter neuen IDs wieder einsteigt,
lässt die Kennzahlen genau in dem Moment *besser* aussehen, in dem das Netzwerk nichts
dazugelernt hat. Ein Report, der Fall 1 nicht von Fall 3 trennen kann, beschönigt das
Netzwerk systematisch nach jedem Pool-Abgang — also genau dann, wenn Leser sich am meisten
auf ihn verlassen.

**Der Epochen-zu-Epochen-Diff ist deshalb ein erstklassiges Ergebnis, keine abgeleitete
Ansicht.** Für jede Epochengrenze berechnet der Report das Identitäts-Delta gegenüber der
Vorepoche und klassifiziert es:

| Klasse | Bedeutung |
|---|---|
| `retained` | Identität in beiden Epochen vorhanden |
| `departed` | In Epoche *n-1* vorhanden, in *n* nicht mehr |
| `entered` | In Epoche *n-1* nicht vorhanden, in *n* neu |
| `succeeded` | Ein `entered`-Slot, der per Nachweis einem `departed`-Betreiber zuzuordnen ist |

**Nachfolger-Erkennung.** Eine `entered`-Identität wird gegen kürzlich `departed`-Betreiber
geprüft — mit denselben Ledger-Nachweisen, auf die sich §4.2 ohnehin stützt. Das fügt eine
Dimension hinzu, statt eine neue Vertrauensannahme einzuführen:

- **Gemeinsames Auszahlungsziel** — die Ausschüttungen der neuen Identität laufen auf eine
  Adresse zu, in die auch das abgegangene Cluster gezahlt hat. Das stärkste Signal; es nutzt
  den Payout-Graph unverändert.
- **Funding-Herkunft** — der Slot der neuen Identität wurde aus den bekannten Adressen des
  abgegangenen Clusters finanziert, oder seine frühen Abflüsse laufen dorthin zurück.
- **Kontinuität im Timing** — der neue Slot nimmt an der Grenz-Tick das Einreichungsmuster des
  abgegangenen Clusters wieder auf, ohne die Anlaufphase, die ein unabhängiger neuer Betreiber
  zeigt.

Ein Treffer hebt das Cluster auf `succeeded` und **trägt die Betreiber-Identität über die
Grenze hinweg**, sodass die Historie eines Betreibers auch dann durchgängig bleibt, wenn
sämtliche seiner Computor-IDs gewechselt haben. Ohne das setzt eine Umbenennung die gesamte
Zeitreihe eines Betreibers still auf null zurück — und die Auswertung belohnt ihn dafür.

**Konfidenz wird konservativ vererbt.** Eine `succeeded`-Verknüpfung ist ein Kontinuitäts-
nachweis, keine Selbstauskunft: Sie wird höchstens mit Konfidenz `linked` ausgewiesen und
fällt auf `flagged`, wenn sie nur vom Timing-Signal getragen wird. Eine allein auf Timing
gestützte Cluster-Kontinuität wird immer als Hypothese samt Nachweis ausgewiesen, damit ein
Leser einer einzelnen Verknüpfung widersprechen kann statt der ganzen Zahl.

**Churn wird als eigene Metrik veröffentlicht.** Pro Epochengrenze nennt der Report, wie
viele Slots den Besitzer gewechselt haben, welcher Anteil des Top-451-Revenue sich bewegt
hat, wie viel davon sich zu `succeeded` statt zu echtem `entered` auflösen ließ — und, als
ehrlicher Vorbehalt, **wie viel des Deltas unerklärt bleibt**. Ein hoher unerklärter Churn
ist selbst der Befund: Er bedeutet, dass das Vertrauen in die Kennzahlen dieser Epoche
geringer sein sollte, und genau so wird er ausgewiesen statt weggeglättet.

**Angekündigte Abgänge werden erfasst, bevor sie eintreten.** Die Selbstauskunfts-Registry
erhält ein optionales `status` pro Betreiber (`active` / `winding_down` / `closed`, mit einer
`effective`-Epoche). So ist eine bekannte Schließung *vor* der Grenze aktenkundig und der
Diff dieser Epoche kann interpretiert statt im Nachhinein rekonstruiert werden. Damit wird
aus einer Pool-Schließung ein vorab registriertes Ereignis mit einer erwarteten Slot-Zahl,
die aufgehen muss — statt eines nachträglichen Rätsels.

---


## 5. API-Form (Entwurf)

```
GET /report/latest                 → Zusammenfassung: Epoche, #Cluster, Nakamoto-Koeff., Gini, HHI, Top-Cluster
GET /report/{epoch}                → dasselbe, historisch
GET /clusters/{epoch}              → vollständige Cluster-Liste: id, label, computor_count, revenue, share, confidence
GET /computors/{epoch}            → pro Slot: id, cluster_id, revenue, evidence
GET /metrics/timeseries           → Konzentrationsindizes über die Epochen (speist die Charts)
GET /transitions/{epoch}          → Slot-Diff ggü. Epoche-1: retained / departed / entered / succeeded,
                                    Churn-Anteil am Top-451-Revenue, unerklärtes Delta (§4.4)
GET /operators/{id}/history       → ein Betreiber über die Epochen, durchgängig trotz ID-Wechsel (§4.4)
GET /report/{epoch}/snapshot.json → eingefrorener, archivierbarer Snapshot
```

Design-Prinzipien: read-only, cachebar, CORS-offen (Explorer binden client-seitig ein),
versioniert (`/v1/`); jede Antwort trägt `generated_at`, `data_sources` und einen
`reproducible: true`-Block, der die Inputs benennt.

---

## 5.1 Persistenz — ein Speicher statt Neuberechnung

Der Report ist eine **Zeitreihe**, Historie ist also das Produkt und kein Nebenprodukt. Jede
Antwort bei jedem Request neu aus der RPC zu bauen (bisheriger Entwurf) hat drei Mängel: Es
macht die API langsam und von der Verfügbarkeit der Gegenstelle abhängig, es verliert jede
Epoche, die die RPC nicht mehr ausliefert, und zwei Läufe können still unterschiedliche
Ergebnisse liefern, ohne dass festgehalten wäre, welcher welcher war. Das Überschreiben von
`api/sample/*.json` bei jedem Build hat die Vergangenheit schlicht weggeworfen.

**Speichermodell.** Das folgt dem Muster, das sich in den Schwesterprojekten
(`qubic_doge_stats`, `qubic_spotlight`) bereits bewährt hat: eine einzelne eingebettete,
dateibasierte Datenbank in einem gemounteten Volume, aufgelöst über dieselbe
`DATA_DIR`-Umgebungsvariable, die der RPC-Cache dieses Repos schon nutzt — kein
Datenbankserver zu betreiben, und die Datei ist trivial kopier- und archivierbar. Jene
Projekte nutzen LiteDB, weil sie .NET sind; das direkte Python-Äquivalent ist **SQLite**
(Standardbibliothek, keine neue Abhängigkeit, gleiche Semantik „eine Datei im Volume"). Wir
übernehmen die Architektur, nicht die Bibliothek.

Konkret aus `qubic_doge_stats` übernommen:

- die Trennung `UpdateLive()` / `FinalizeEpoch()` — die laufende Epoche wird bei jedem Poll
  aufgefrischt, eine abgeschlossene genau einmal finalisiert (exakt die Live-vs-Sealed-Regel
  unten);
- **Upsert mit Dedupe** statt blindem Insert, damit ein erneuter Poll nie Zeilen dupliziert;
- **„einmal korrekt gesetzt, unveränderlich"** — die Werte einer finalisierten Epoche werden
  von einem späteren Poll nicht überschrieben;
- ein **Backfill-Service** zum Füllen der Historie und zum Neuableiten unter neuer
  Code-Version;
- **Polling-Worker** auf unabhängigen Intervallen, damit ein langsamer Revenue-Pull nie den
  günstigen Tick-/Epochen-Zeiger blockiert.

Tabellen:

| Tabelle | Inhalt |
|---|---|
| `epochs` | eine Zeile pro Epoche: Tick-Bereich, Status (`sealed` / `partial` / `live`), wann zuerst und zuletzt berechnet |
| `computor_revenue` | pro Epoche und Identität: abgeleitetes Revenue + das Tick-Fenster, aus dem es stammt |
| `transfers` | die verfolgten Auszahlungs-Transfers hinter diesem Revenue — der Audit-Trail, der eine Abweichung erklärbar macht |
| `clusters` | pro Epoche: Cluster-Zugehörigkeit, Konfidenz, Evidenz |
| `reports` | der fertige Report als JSON pro Epoche, mit `code_version` |
| `linkage` | die Kanten des Payout-Graphen, damit Verknüpfung inkrementell wächst statt jedes Mal bei null zu beginnen |

**Sealed vs. live — die Kernregel.** Eine **abgeschlossene** Epoche ist unveränderlich: einmal
berechnet, geschrieben und danach für immer aus dem Speicher ausgeliefert. Ihre Eingangsdaten
können sich nicht mehr ändern, eine Neuberechnung birgt also nur das Risiko von Drift. Die
**laufende** Epoche ist ausdrücklich *nicht* versiegelt — sie wird in kurzem Intervall (und
auf Anforderung) neu berechnet und mit `status: "live"` plus dem Zeitpunkt der letzten
Aktualisierung ausgeliefert. So sehen Konsumenten immer den aktuellen Stand der laufenden
Epoche und können ihn von der abgeschlossenen Historie unterscheiden.

Eine Epoche wird erst versiegelt, wenn ihre Nachfolgerin begonnen hat **und** ihre
Revenue-Ableitung vollständig ist (vollständige Paginierung, Abgleich bestanden). Eine Epoche,
die mit Lücken schließt, bleibt `partial` und wird erneut versucht, statt falsch eingefroren
zu werden.

**Neuberechnung und Versionierung.** Ändert sich die Logik der Pipeline (korrigierter
Arbitrator, bessere Verknüpfungsregel), werden versiegelte Epochen *nicht* still
überschrieben: Ein Backfill läuft unter einer neuen `code_version`, und der Speicher behält
die vorherige Berechnung. So hat eine Zahl, die sich ändert, einen sichtbaren Grund — was
genau das „unsere Zahlen stimmen nicht überein"-Problem adressiert.

**Ingest ist inkrementell.** Rohdaten bleiben wie bisher zwischengespeichert (`data/raw/`),
aber die abgeleitete Ebene wird einmal geschrieben und vielfach gelesen. Ein Neustart, ein
RPC-Ausfall oder ein Explorer, der die API hämmert, lesen alle aus dem Speicher; nur die
laufende Epoche berührt das Netzwerk.

**Auswirkung auf die API.** `/v1/report/{epoch}` wird zu einem Speicher-Lookup und kann *jede*
historische Epoche ausliefern, nicht nur den zuletzt gebauten Snapshot. Antworten führen
`status` (`sealed` / `partial` / `live`), `computed_at` und `code_version` mit. Die statischen
`api/sample/*.json` bleiben nur noch als Kaltstart-Fallback für eine frische Installation mit
leerer Datenbank.

---

## 5.2 Gebaut für ein Netz in Bewegung

Das Netz steht nicht still: Epochen kommen laufend hinzu, Pools entstehen, verschmelzen und
verschwinden, die Analyse-Ebene bekommt neue Evidenztypen — und selbst „676 Computors" ist
eine aktuelle Konstante, kein Naturgesetz. Alles, was auf die heutige Form festgenagelt ist,
wird später still zur Falschaussage. Die Regeln, die das verhindern:

- **Keine Netzkonstante ist hartkodiert.** Slot-Zahlen, Epochennummern und Betreiberzahlen
  werden bei jedem Rendern aus den Daten gelesen. Insbesondere enthalten die
  **UI-Sprachdateien keine Zahlen**: Die Strings nutzen Platzhalter (`{slots}`, `{epoch}`,
  `{operators}`), die aus den Live-Daten gefüllt werden. So sagt eine heute geschriebene
  Übersetzung auch dann noch die Wahrheit, wenn sich das Netz ändert. Eine Sprachdatei darf
  nie an die Werte einer Epoche gebunden sein.
- **Neue Betreiber brauchen keine Code-Änderung.** Ein Pool, der ins Registry aufgenommen
  wird, oder ein Cluster, den der Payout-Graph neu offenlegt, erscheint einfach im nächsten
  Epochen-Report — das Clustering ist datengetrieben, nie eine fest gepflegte Liste.
- **Unbekannte Werte degradieren ehrlich.** Eine Konfidenzstufe, die diese Dashboard-Version
  noch nie gesehen hat, wird neutral *unter ihrem eigenen Namen* dargestellt, nie still als
  eine bestehende Stufe umetikettiert, und sie wird nicht als on-chain verknüpft gezählt.
  Im Zweifel „wir können das nicht bestätigen" ist die sichere Richtung.
- **Alte Leser, neue Daten.** Die Report-Payloads wachsen additiv: Konsumenten ignorieren
  unbekannte Felder, ein Explorer mit älterem Embed funktioniert also weiter.
- **Historie ist im Transport begrenzt, nicht auf der Platte.** Der Speicher behält jede
  Epoche für immer, aber die Standard-API-Sichten liefern ein aktuelles Fenster (52 Epochen
  für die Zeitreihe, 26 fürs Dashboard-Bundle), damit die Antwortgröße konstant bleibt,
  während die Historie wächst. Der volle Bereich bleibt auf Anfrage verfügbar (`?epochs=`).
- **Zukunftsfestigkeit wird getestet, nicht angenommen.** `tests/test_forward_compat.py`
  führt die Analyse mit 100 / 676 / 1 000 / 2 048 Slots aus, über einen wachsenden
  Epochenbereich, mit mitten in der Historie hinzukommenden Pools und einer injizierten
  unbekannten Konfidenzstufe.

---

## 6. Dashboard & Animation

Das Referenz-Dashboard rendert den Report und — dort, wo es wirklich etwas zu animieren
gibt — animiert es. Animation wird nur eingesetzt, wo Bewegung Bedeutung trägt, nicht als
Deko:

- **Cluster-Entwicklung über die Epochen** — eine animierte Treemap / Bubble-Chart, in der
  jede Bubble ein Betreiber ist, Größe = Slots oder Revenue; man spielt die Epochen ab und
  sieht Cluster wachsen, schrumpfen, verschmelzen oder sich aufteilen. Macht sichtbar, wie
  „ein Pool auf die Top-451-Schwelle zukriecht".
- **Nakamoto-Koeffizient-Timeline** — eine animierte Linie über die Epochen, mit markierten
  Gefahrenschwellen (⅓, ½).
- **Fund-Flow-/Verknüpfungsgraph** — ein Force-Directed-Graph aus Slots und
  Auszahlungs-Verbindungen, der sich zu Clustern einpendelt, sodass die On-chain-Verknüpfung
  buchstäblich beobachtbar wird.
- **Deklariert vs. erkannt** — ein Übergang, der das „offizielle" self-reported Clustering
  in das erkannte morpht, sodass die Lücke („der Rauch") selbst die Animation ist.

Statische Fallbacks sind immer verfügbar (jede animierte Ansicht hat ein Standbild), weil
die Explorer auch die statische Form einbinden können.

### 6.1 Dashboard-UX-Anforderungen

- **Layout / visuelle Sprache:** Orientierung an bestehenden Qubic-Community-Front-Ends
  (z. B. *Qubic Dividends*), damit sich der Report nativ im Ökosystem anfühlt — gleiche
  Karten-/Tabellen-/Dark-Ästhetik, sodass ein Explorer ihn ohne stilistischen Bruch
  einbinden kann.
- **Dark- / Light-Mode:** ein Umschalter im Header. Dark ist Standard (passt zum Ökosystem).
  Voll theme-aware — beide Modi sind gleichwertig, nicht nachträglich.
- **Sprachumschaltung DE / EN:** alle UI-Texte laufen über eine i18n-Schicht mit Deutsch und
  Englisch. Englisch ist Standard; Deutsch ist eine vollwertige, gleichrangige Übersetzung.
  So strukturiert, dass später weitere Sprachen ergänzt werden können (einfache Key →
  String-Dictionaries).
- **Persistenz:** sowohl die Theme- als auch die Sprachwahl werden in `localStorage`
  gespeichert und beim nächsten Besuch wiederhergestellt. Lese-/Schreibzugriffe sind in
  `try/catch` gekapselt, damit die Seite auch korrekt rendert, wenn Storage nicht verfügbar
  ist (privater Modus, blockierte Cookies) — dann greifen die Defaults (EN + Dark).

---

## 7. Warum das die Aufgabe erfüllt

- Liefert **genau** die zwei von CFB genannten Dinge: Revenue-Metriken + Dynamik und
  Clustering mit Computor-Anzahl pro Cluster.
- Baut auf **Self-Reporting als primärer Ebene** — der Anti-Sybil-Technik, die CFB „to the
  fullest" nutzen will — und ergänzt On-chain-/Verhaltens-Ebenen, um die Lücke zu
  quantifizieren.
- Wird als **API zum Einbinden für Explorer** ausgeliefert, was seine erklärte
  Distributionsanforderung ist.
- Neutral und reproduzierbar — misst Konzentration, klagt nicht an; jeder kann es
  nachrechnen, was das Problem des „fehlenden neutralen Ermittlers" beantwortet.

---

## 7.1 Community-Feedback und was sich geändert hat (v0.2)

Vier Rückmeldungen zum Entwurf v0.1 und wie das Konzept darauf antwortet. Die ersten drei
zeigten auf dieselbe Schwachstelle: eine solide Metrik-Ebene auf einer ungesicherten
Datenebene. Die vierte zeigt auf eine andere: einen Report, der immer nur eine Epoche auf
einmal betrachtet.

**1. „Meine Zahlen stimmen nicht mit deinen überein — ich habe seit etwa 6 Epochen
vollständiges Transaction-Tracking laufen."** (Kevarms)

Zutreffend — und die Untersuchung förderte etwas Größeres zutage als einen Paginierungsfehler.

v0.1 leitete Revenue aus einem einzigen, nicht paginierten Durchlauf über die Transfers des
Arbitrators ab, ohne Epochen-/Tick-Eingrenzung und mit unverifizierter Arbitrator-Identität.
§4.3 spezifiziert die Korrektur. Gegen die Live-RPC ausgeführt (07.09.2026) lieferte sie
jedoch **null** — und der Grund ist grundsätzlicher Natur (vollständige Belege in
`docs/DATA_SOURCES.md` §6):

- Die mitgeführte Arbitrator-Identität bezahlte **0 von 676** Computors — sie war schlicht
  falsch;
- Ein Scan von ~4 500 Transaktionen pro Computor über das gesamte Epoche-228-Fenster fand
  **überhaupt keine eingehenden Zahlungen**. Was Computor-Identitäten tatsächlich aussenden,
  ist ein Strom von `amount = 0`-Transaktionen an die Nulladresse — das sind
  **Solution-Submissions**, keine Auszahlungen;
- Dennoch meldet `/v1/balances` 0,5–1,6 Mrd. QU eingehenden Wert pro Computor. Der Wert ist
  real, aber **die Transfers, die ihn tragen, werden von den Transaktions-Endpunkten nicht
  ausgeliefert**.

**Fazit: Computor-Revenue wird durch Protokoll-Emission gutgeschrieben, nicht durch einen
Transfer, den die öffentliche RPC sichtbar macht.** „Arbitrator-Auszahlungen verfolgen" kann
also nicht funktionieren, so vollständig man auch paginiert — die Datensätze existieren dort
nicht. Das ist mit hoher Wahrscheinlichkeit die Wurzel der Abweichung, und es bedeutet: Keine
der beiden Implementierungen lässt sich gegen die andere prüfen, solange nicht beide offenlegen,
welche Quelle sie verwenden.

Was wir stattdessen tun (§4.3, implementiert): Revenue wird als **Differenz des kumulativen
`incomingAmount` jedes Computors über die Epochengrenze** gemessen, auf Basis von
Balance-Snapshots, die wir selbst nehmen — genau wofür der Speicher (§5.1) existiert. Gegen
die Live-Chain verifiziert. Das ist in sich geschlossen, reproduzierbar und liefert ab der
nächsten Epochengrenze korrekte Zahlen.

Zwei Dinge bleiben zu tun, das zweite direkt mit Kevarms:

1. Ein `qubic.li`-Score-API-Token anfragen — der schnellste Weg zu *historischem* Revenue und
   eine unabhängige Gegenprobe.
2. **Methoden vergleichen, nicht nur Zahlen.** Da die öffentlichen Transfer-Endpunkte diese
   Zahlungen nicht führen, muss ein konvergierender 6-Epochen-Datensatz aus einer anderen
   Quelle stammen (Node-Feed, Pool-API oder Balance-Deltas wie jetzt bei uns). Das zu klären
   ist wertvoller als ein Streit über Summen — und es ist der eigentliche Abnahmetest.

Aus derselben Session kamen zusätzlich drei konkrete Endpoint-Korrekturen (falsche Pfade, ein
**Paginierungslimit von 250 Zeilen** und wo die Epochen-Tick-Fenster tatsächlich liegen); alle
sind in `docs/DATA_SOURCES.md` §6.1 festgehalten, damit der nächste Implementierer sie nicht
wiederholt.

**2. „Gutes Tooling, grober Input; On-chain-Verknüpfung sollte der Standard sein, nicht das
Auffangbecken namens ‚unattributed'."** (Jure Ursic Cergol)

Als architektonische Korrektur angenommen. In v0.1 existierte `apply_onchain_linkage()`, wurde
aber vom Report-Pfad **nie aufgerufen** — die einzige tatsächliche Zuordnung kam aus einem
Registry, dessen `computors`-Listen bewusst leer sind. Damit wurde jeder der 676 Slots zu einem
eigenen „unattributed"-Einzelcluster, und die Kennzahlen beschrieben 676 fiktive unabhängige
Betreiber. §4.2 dreht die Schichtung um: Der Payout-Graph wird in jeder Epoche berechnet und
liefert das Basis-Clustering; Self-Reporting beschriftet und ergänzt es, kann aber nie
trennen, was die Chain verknüpft hat. „Unattributed" heißt jetzt *das Ledger zeigt keine
Verknüpfung*, nicht *niemand hat ein Formular eingereicht* — und der Report weist seine
Verknüpfungs-Abdeckung aus, damit erkennbar ist, wie viel des Netzwerks aufgelöst und wie
viel nur angenommen ist.

**3. „Musst du nichts persistieren?"** (Admin)

Doch. v0.1 baute jede Antwort aus der RPC neu und überschrieb bei jedem Build seine einzigen
Snapshot-Dateien — Historie ging verloren und keine zwei Läufe waren vergleichbar. §5.1 führt
einen SQLite-Speicher mit genau der Trennung ein, die das Projekt braucht: **die aktuelle
Epoche wird immer live neu berechnet**, damit die laufende Epoche nie veraltet, während
**abgeschlossene Epochen versiegelt und aus dem Speicher ausgeliefert** werden — Historie ist
damit unveränderlich, archivierbar und nicht mehr davon abhängig, dass die RPC alte Epochen
noch ausliefert. Eine Logikänderung löst einen versionierten Backfill aus statt eines stillen
Überschreibens — ändert sich also eine Zahl, gibt es dafür einen festgehaltenen Grund.

**4. „Apool macht dicht — wohin gehen die IDs, die sie benutzt haben?"** (Vaintor)

Das Konzept hatte darauf keine Antwort, denn jede Schicht in §4.2 argumentiert innerhalb
einer einzelnen Epoche. Eine Pool-Schließung ist genau das Ereignis, das ein
Per-Epochen-Report nicht deuten kann: Die Slots des abgehenden Betreibers werden neu
vergeben, und ob sie bei neuen unabhängigen Betreibern landen, von den verbliebenen großen
Pools aufgesogen werden oder unter frischen Identitäten desselben Betreibers zurückkommen —
jeder dieser Fälle ergibt eine in sich stimmige Momentaufnahme. Schlimmer noch: Der dritte
Fall lässt die Kennzahlen *besser* aussehen, weil die Nachfolge-Slots als unverknüpfte
Einzelstücke eingehen und die Betreiberzahl aufblähen. Der Report hätte das Netzwerk also
genau dann beschönigt, wenn er hätte warnen müssen.

§4.4 ergänzt die fehlende Dimension: einen Epochen-zu-Epochen-Identitäts-Diff
(`retained`/`departed`/`entered`/`succeeded`) als erstklassiges Ergebnis, eine
Nachfolger-Erkennung, die den bestehenden Payout-Graph nutzt, um die Betreiber-Identität über
einen ID-Wechsel hinwegzutragen, eine veröffentlichte Churn-Metrik mit explizit
*unerklärtem* Anteil sowie ein optionales `status`-Feld in der Registry, damit eine
angekündigte Schließung vor der Grenze aktenkundig ist statt im Nachhinein rekonstruiert zu
werden. Die Frage ist zugleich ein konkreter Abnahmetest: Wenn Apools Abgangsepoche schließt,
muss der Report sagen können, wohin diese Slots gegangen sind — und klar benennen, welchen
Teil der Bewegung er nicht erklären konnte.

---

## 8. Offene Fragen für den nächsten Schritt

- Genaue RPC-Endpoints und Rate-Limits für die Revenue-Historie pro Computor (in
  `docs/DATA_SOURCES.md` festhalten).
- Format/Autorität des Pool-Self-Reportings — gibt es einen kanonischen Feed, oder kuratieren
  wir ein Registry und lassen Pools ihre Einträge per PR ergänzen?
- Schwellenwerte der Nachfolger-Erkennung (§4.4) — wie viel Überschneidung beim
  Auszahlungsziel begründet eine `succeeded`-Verknüpfung, und wie viele Epochen bleibt ein
  `departed`-Betreiber im Kandidatenpool, bevor ein eintretender Slot als echt neu gilt?
- Apools Abgangsepoche — die Grenze festnageln und den Slot-Stand *vor* dem Abgang sichern,
  damit der Übergangs-Diff eine Vergleichsbasis hat (§4.4).
- Ob ein abgehender Betreiber seinen Rückzug überhaupt deklariert (`status: winding_down`),
  oder ob Abgänge immer erst im Nachhinein erkannt werden müssen.
- Welche Explorer sind das erste Integrationsziel, und welches Einbettungsformat bevorzugen
  sie?
- Bounty-Akzeptanzkriterien — was genau muss der Report enthalten, um „akzeptiert" und auf
  einer Seite eingebunden zu werden?

---

## 9. Roadmap

1. **Konzept** (dieses Dokument) ✅
2. **Data Mapping** — Endpoints gegen die Live-RPC bestätigt, in `docs/DATA_SOURCES.md`
   dokumentiert ✅
3. **Revenue-Engine** — Gini / HHI / Top-N / Nakamoto implementiert + Unit-getestet
   (`qdr/metrics.py`) ✅
4. **Clustering-Engine** — Registry + Verknüpfung implementiert (`qdr/clustering.py`); **On-chain-Verknüpfung vom optionalen Hook zur Standard-Zuordnungsebene befördert** (§4.2) ✅
4d. **Slot-Übergänge** — Epochen-zu-Epochen-Identitäts-Diff, Nachfolger-Erkennung,
   Churn-Metrik mit explizit unerklärtem Anteil, `status`-Feld in der Registry (§4.4) 🔶
4b. **Revenue-Ableitung** — implementiert und gegen die Live-Chain verifiziert.
   Payout-Tracking erwies sich gegen die öffentliche RPC als unmöglich (Revenue ist
   Protokoll-Emission, kein sichtbarer Transfer — DATA_SOURCES §6.2), daher Ableitung über
   **Balance-Deltas über die Epochengrenze**. Erfordert den laufenden Snapshot-Worker ✅
4c. **Persistenz** — SQLite-Speicher, sealed vs. live, versionierte Neuberechnung (§5.1) ✅
4e. **Zukunftsfestigkeit** — keine hartkodierten Netzkonstanten, datengetriebene i18n,
   begrenzte API-Fenster, getestet mit 100–2048 Slots (§5.2) ✅
   implementiert + getestet (`qdr/clustering.py`); On-chain-Ebene wird noch angereichert 🔶
5. **API** — FastAPI-Service, der den Report ausliefert (`api/server.py`), CORS-offen, mit
   einem `/v1/dashboard-data`-Bundle und statischem Sample-Fallback ✅
6. **Dashboard** — eigenständige SPA (`dashboard/index.html`): animierte Betreiber-Treemap,
   Nakamoto/Gini-Timeline, Betreiber-Tabelle, DE/EN + Dark/Light mit localStorage ✅
7. **Explorer-Integration** — Einbettungsformat fertig (Widget + iframe + rohe API,
   `docs/EMBEDDING.md`, `dashboard/embed.js`); Rollout mit erstem Partner offen 🔶

Bisher umgesetzt: `qdr/` (Client, Metrics, Clustering, Report); ein Self-Reporting-Registry
mit Validator (`data/self_reporting/`, `scripts/validate_registry.py`); Tests (`tests/`, alle
grün); der API-Service (`api/`, der auch Dashboard und Beispiele ausliefert); das
Referenz-Dashboard (`dashboard/`); ein einbettbares Widget (`dashboard/embed.js`); eine
Live-Pull-CLI (`scripts/build_report.py`); und VS-Code-F5→Chrome-Konfigs (`.vscode/`).
Live-Läufe brauchen Netzzugang zu `rpc.qubic.org` (in der Cowork-Sandbox blockiert, von einem
normalen Rechner aus problemlos); alles Übrige läuft heute auf den generierten Beispieldaten.
