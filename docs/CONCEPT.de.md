# Qubic Decentralization Report — Konzept

*Arbeitskonzept für ein Tool, das aus self-reported und On-chain-Daten einen laufenden
Bericht „Wie dezentral ist Qubic?" erzeugt und ihn als API bereitstellt, die Explorer
einbinden können.*

Status: Entwurf v0.1 · Owner: (Qubic-Community-Projekt) · Sprachen: Diese Datei ist die
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

| Ebene | Quelle | Was wir bekommen |
|---|---|---|
| Konsens / Slots | Qubic RPC 2.0 (`rpc.qubic.org`) + Core-Node-Daten | Computor-Liste pro Epoche (676 IDs), Tick-Daten, Quorum-Infos |
| Revenue | RPC / Archiver | Revenue pro Computor pro Epoche, Auszahlungsflüsse, Top-451-Verteilung |
| Self-Reporting | Pool-APIs & öffentliche Deklarationen (qubic.li u. a.) plus ein kuratiertes Registry in diesem Repo | Welche Slots ein Pool/Betreiber für sich beansprucht |
| On-chain | Qubic-Ledger via RPC | Auszahlungs-Zieladressen, Fund-Flows zwischen Identitäten |
| Verhalten (optional) | Timing/Muster der Solution-Submissions, soweit beobachtbar | Signale, um self-reported Cluster zu bestätigen oder zu hinterfragen |

Die genauen Endpoint-Namen werden während der Umsetzung gegen die Live-RPC verifiziert;
siehe `docs/DATA_SOURCES.md` (wird im Data-Mapping-Schritt gefüllt).

---

## 4. Die zwei Analyse-Engines

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

Das ist der Kern, geschichtet vom Vertrauenswürdigsten zum am stärksten Abgeleiteten. Jeder
Computor-Slot wird einem Cluster zugeordnet, mit **Konfidenzstufe und verwendeter Evidenz**,
damit der Report transparent macht, was deklariert und was abgeleitet ist:

1. **Self-reported (primär, gemäß CFB).** Pools deklarieren ihre Slots; wir nehmen diese
   Deklarationen in ein versioniertes Registry auf. Das ist das „offizielle" Basis-Clustering.
2. **On-chain-Verknüpfung.** Gemeinsame Auszahlungsziele, über Epochen wiederverwendete
   Identitäten und Fund-Flow-Graphen führen Slots zusammen, die nachweislich denselben
   wirtschaftlichen Eigentümer haben.
3. **Verhaltens-Fingerprints (nur Markierung).** Korrelierte Submission-Timings/-Quellen
   können Slots *markieren*, die sich wie eine Einheit verhalten, auch wenn sie getrennt
   deklariert sind — als „mögliches nicht deklariertes Cluster" ausgewiesen, nie als harte
   Anschuldigung.

Ausgabe pro Cluster: Anzahl Computors, Revenue, Anteil an Top-451 und ein
**deklariert-vs-erkannt**-Delta, das den „Rauch" quantifiziert.

---

## 5. API-Form (Entwurf)

```
GET /report/latest                 → Zusammenfassung: Epoche, #Cluster, Nakamoto-Koeff., Gini, HHI, Top-Cluster
GET /report/{epoch}                → dasselbe, historisch
GET /clusters/{epoch}              → vollständige Cluster-Liste: id, label, computor_count, revenue, share, confidence
GET /computors/{epoch}            → pro Slot: id, cluster_id, revenue, evidence
GET /metrics/timeseries           → Konzentrationsindizes über die Epochen (speist die Charts)
GET /report/{epoch}/snapshot.json → eingefrorener, archivierbarer Snapshot
```

Design-Prinzipien: read-only, cachebar, CORS-offen (Explorer binden client-seitig ein),
versioniert (`/v1/`); jede Antwort trägt `generated_at`, `data_sources` und einen
`reproducible: true`-Block, der die Inputs benennt.

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

## 8. Offene Fragen für den nächsten Schritt

- Genaue RPC-Endpoints und Rate-Limits für die Revenue-Historie pro Computor (in
  `docs/DATA_SOURCES.md` festhalten).
- Format/Autorität des Pool-Self-Reportings — gibt es einen kanonischen Feed, oder kuratieren
  wir ein Registry und lassen Pools ihre Einträge per PR ergänzen?
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
4. **Clustering-Engine** — self-reported Registry + On-chain-Verknüpfungs-Hook
   implementiert + getestet (`qdr/clustering.py`); On-chain-Ebene wird noch angereichert 🔶
5. **API** — den berechneten Report ausliefern (als Nächstes).
6. **Dashboard** — statische Ansichten, dann die animierten (DE/EN, Dark/Light).
7. **Explorer-Integration** — Einbettungsformat + erster Partner.

Bisher umgesetzt: `qdr/` (Client, Metrics, Clustering, Report), ein Self-Reporting-Registry
(`data/self_reporting/`), Tests (`tests/`, alle grün) und ein Beispiel-Report + Zeitreihe
fürs Dashboard (`api/sample/`). Live-Läufe brauchen Netzzugang zu `rpc.qubic.org` (in der
aktuellen Build-Umgebung blockiert).
