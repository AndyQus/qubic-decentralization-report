# Posts: one X message, and a Discord explanation per page

Two deliverables, ready to post:

1. **X** — one message about the report as a whole (DE and EN).
2. **Discord** — one explanation per page, five pages (DE and EN).

Site: https://report.qubic.tools/dashboard/

| Page | URL |
|---|---|
| Report (start page) | https://report.qubic.tools/dashboard/ |
| Burn Report | https://report.qubic.tools/dashboard/burn.html |
| Mining Live | https://report.qubic.tools/dashboard/mining.html |
| How it works | https://report.qubic.tools/dashboard/how-it-works.html |
| Status & Log | https://report.qubic.tools/dashboard/log.html |

## Figures used, and where they come from

All figures below were read from the live deployment on 2026-09-20 and belong to
**epoch 230, the newest *sealed* epoch**. Epoch 231 was still running at the time
of writing, and its revenue is partial by definition — never quote a running
epoch in a post.

| | |
|---|---|
| Newest sealed epoch | 230 |
| Computors paid | 676 / 676 (`transfers_matched: 676`, `complete: true`) |
| Revenue, epoch 230 | 179,468,234,779 QU |
| Gini (by revenue) | 0.0093 |
| Top slot | 0.15 % of revenue |
| Nakamoto ⅓ / ½ | 223 / 334 slots |
| Declared operators | 0 |
| Burned supply | 53,662,829,138,067 QU = 23.2 % of supply |

**Re-read the numbers before posting** if more than a few days have passed —
`/v1/report/latest` tells you the newest epoch and whether it is `sealed` or
`live`; use the newest `sealed` one.

**One caveat that must survive editing:** the burn page currently reports the
official cumulative counter, but its own per-day measurement is still empty
(`events: 0`, `coverage: null`) until the worker has observed burn events across
an epoch boundary. The Discord text for that page says so. Do not cut it — the
page would otherwise promise a daily series it cannot yet show.

---

# X

Pick one. Both are single posts; the thread version already exists in
`docs/ANNOUNCEMENT_LIVE.md` and is not repeated here.

## English

> $QUBIC Decentralization Report — live: report.qubic.tools
>
> Revenue per computor slot, per epoch, from the chain. Epoch 230 sealed,
> 676/676 computors paid, Gini 0.0093 — payouts are almost flat.
>
> What it doesn't say: who operates those slots.

*(246/280, URL counted as 23 characters — verified.)*

## Deutsch

> $QUBIC Dezentralisierungs-Report — live: report.qubic.tools
>
> Umsatz pro Computor-Slot, pro Epoche, aus der Chain. Epoche 230 abgeschlossen,
> 676/676 Computors bezahlt, Gini 0,0093 — Auszahlungen nahezu flach.
>
> Was er nicht sagt: wer die Slots betreibt.

*(256/280, URL als 23 Zeichen gerechnet — geprüft.)*

---

# Discord — one explanation per page

Five posts. They can go out together as one thread, or one per day. Each stands
on its own: someone who reads only one of them still knows what that page is and
what it refuses to claim.

---

# Deutsch

## 1 — Report (Startseite)

> **Der Report** — https://report.qubic.tools/dashboard/
>
> Die Startseite beantwortet eine Frage: **wie verteilt sich der Umsatz über die
> 676 Computor-Slots?**
>
> Drei Ansichten:
> • **Umsatz über die Slots** — alle 676 auf einen Blick, je Slot ein Feld
> • **Konzentration über die Zeit** — Gini, HHI, Nakamoto ⅓/½ über die Epochen
> • **Tabelle dieser Epoche** — Slot für Slot, sortierbar
>
> Gemessen, Epoche 230 (abgeschlossen): 676 von 676 Computors bezahlt,
> 179.468.234.779 QU Umsatz, Gini 0,0093, der bestbezahlte Slot hält 0,15 %.
> Nakamoto ⅓ = 223. **Die Auszahlungen sind nahezu flach** — das ist das
> Ergebnis, nicht meine Meinung.
>
> Und jetzt die Einschränkung, die auf jeder Zahl der Seite mitläuft: das sind
> **Slots, keine Betreiber**. Nakamoto ⅓ = 223 heißt 223 von 676 *Slots*, nicht
> 223 unabhängige Betreiber. Wo ein Betreiber mehrere Slots hält, ist die
> tatsächliche Konzentration höher — möglicherweise deutlich.
>
> Warum ich das nicht auflösen kann: **eingetragene Betreiber: 0**. On-Chain gibt
> es nichts herzuleiten — jeder Computor wird von derselben Null-Adresse
> gutgeschrieben (Protokoll-Emission, keine Überweisung), und die ausgehenden
> Transfers sind einheitlich 1.000.000-QU-Burns an dieselbe Adresse. Kein
> Transfer-Graph verbindet zwei Computors. Der Report meldet das als
> `linkage_coverage: 0`, statt daraus Unabhängigkeit zu behaupten.
>
> Die zweite Hälfte hängt also an euch: Ein Pool trägt seine Slots per Pull
> Request in `data/self_reporting/pools.json` ein, und die Betreiber-Ansicht
> schaltet sich für diesen Pool ohne Code-Änderung frei. Die Git-Historie ist der
> Prüfpfad.

## 2 — Burn Report

> **Burn Report** — https://report.qubic.tools/dashboard/burn.html
>
> Wie viel QUBIC wurde vernichtet, und wodurch?
>
> Aktuell gemessen: **53.662.829.138.067 QU verbrannt — 23,2 % der Menge.**
>
> Warum die Seite überhaupt existiert, statt einfach den offiziellen Zähler zu
> spiegeln: `/v1/latest-stats → burnedQus` ist kumulativ und maßgeblich, aber er
> ist ein **Epochen-Aggregat**. Über 375 Ticks gemessen hat er sich um keinen
> einzigen QU bewegt. Eine Tagesreihe kann daraus nicht entstehen. Also zählt
> dieses Projekt die Burn-Ereignisse selbst aus den Tick-Logs eines Bob-Nodes und
> gleicht die Summe an jeder Epochengrenze gegen den offiziellen Zähler ab —
> dieses Verhältnis ist die Coverage-Zahl auf der Seite.
>
> Zwei Dinge, die die Seite bewusst trennt: **Contract-Burns** stehen in einem
> eigenen Panel und werden nie mit den Transfer-Burns zusammengezählt — das sind
> verschiedene Arten von Burn. Contract-Namen kommen aus der Registry, die Qubic
> für den offiziellen Explorer veröffentlicht; ein Index, den die Registry nicht
> kennt, bleibt ein Index und bekommt keinen erfundenen Namen.
>
> **Stand jetzt ehrlich gesagt:** die eigene Messung läuft erst an. Der Gesamtwert
> oben steht, die **Tagesreihe und die Coverage-Zahl bleiben aber leer, bis der
> Worker Burn-Ereignisse über eine volle Epochengrenze hinweg beobachtet hat.**
> Solange zeigt die Seite dort nichts statt einer hochgerechneten Zahl. Wo die
> Coverage später unter 100 % liegt, existiert eine Burn-Kategorie, die dieser
> Scan noch nicht erkennt — auch das wird gemeldet und nicht versteckt.

## 3 — Mining Live

> **Mining Live** — https://report.qubic.tools/dashboard/mining.html
>
> Der Report misst *Verteilung über Epochen*. Diese Seite zeigt die **Maschinerie
> im Moment**.
>
> Was dort live steht:
> • **Akzeptierte Lösungen** des Schwarms, mit Zuwachs pro Minute
> • **Node-Status** — welcher Peer abgefragt wurde, Latenz, Version, antwortende
>   Nachbarn
> • **Die Aufgabe** — Eingangs-Trits, Sequenzlänge, Kontextfenster, Schwelle,
>   dazu der Daten-Hash, byteweise gegen den Core geprüft
> • **Revenue-Verteilung** über alle 676 Slots als Histogramm, mit Top-10-Anteil
>
> Alles kommt direkt von einem öffentlichen Qubic-Node über den Peer-Port
> (21841), die Peer-Liste von `api.qubic.li/Public/Peers`. Nichts ist geschätzt,
> nichts nachträglich geglättet.
>
> Zwei Einschränkungen, damit die Zahlen richtig gelesen werden: Die
> **Revenue-Scores sind bis zum Epochenende nur näherungsweise** — das sagt die
> Core-Dokumentation selbst, nicht ich. Und die Seite ist **Beta**: sie hängt an
> einem einzelnen Node, und wenn der nicht antwortet, steht dort ehrlich „kein
> Node erreichbar" statt einer alten Zahl.
>
> Was die Seite ausdrücklich *nicht* sagt: wofür der Task inhaltlich steht. Das
> gibt das Protokoll nicht her, also steht dazu auch nichts da.

## 4 — Wie es funktioniert

> **Wie es funktioniert** — https://report.qubic.tools/dashboard/how-it-works.html
>
> Die Methode, offen und vollständig — auf Deutsch und Englisch auf derselben
> Seite.
>
> Wer eine Zahl aus dem Report weiterverwenden will, sollte vorher hier
> nachgelesen haben. Die Seite erklärt:
> • **was CFB konkret verlangt hat** und was das Tool daraus macht
> • **die Datenquellen** — welche Zahl aus welcher Quelle stammt
> • **die Analyse-Engines** — wie Gini, HHI und Nakamoto hier gerechnet werden
> • **Persistenz** — warum abgeschlossene Epochen einmal berechnet und *versiegelt*
>   werden, während die laufende Epoche live nachgerechnet wird
> • **zwei Takte: was live ist und was nicht** — der wichtigste Abschnitt, wenn
>   ihr wissen wollt, wie alt eine Zahl gerade ist
> • **wo der Report steht** — inklusive dem, was noch fehlt
>
> Der Grundsatz dahinter: keine Beispieldaten, nie. Ist der Speicher leer,
> antwortet die API mit 503 und die Seite sagt, dass sie noch baut — sie erfindet
> keine Platzhalter. Jede Zahl ist damit beschriftet, was sie misst.

## 5 — Status & Log

> **Status & Log** — https://report.qubic.tools/dashboard/log.html
>
> Die Seite für eine einzige Frage: **läuft der Dienst gerade sauber, oder
> nicht?**
>
> Das ist kein Schaustück, sondern die Diagnose-Seite — dafür gedacht, dass ihr
> nachsehen könnt, statt mir glauben zu müssen. Wenn der Report mal nichts
> anzeigt, steht hier, ob er noch baut oder ob etwas kaputt ist. Der Unterschied
> ist wichtig und die Seite sagt ihn deutlich.
>
> Sie zeigt:
> • ein **Urteil oben** — vier Checks, im Klartext, mit Problemliste falls nötig
> • **Epochen im Speicher** — welche sind `sealed`, welche läuft
> • **das Ingest-Log** — die letzten Zeilen des Workers, ungefiltert
>
> Sie fragt `/v1/diagnostics` und `/v1/log` ab und aktualisiert sich alle 15
> Sekunden selbst.
>
> Eine Regel, die den ganzen Report trägt und hier am sichtbarsten wird: **der
> Report erscheint erst, wenn mindestens eine Epoche `sealed` ist** — eine
> laufende zählt nicht. Lieber „baut noch" als eine halbe Zahl.
>
> Zwei Hinweise: die Seite ist **Beta**, und sie gibt es **derzeit nur auf
> Deutsch** — im Gegensatz zu allen anderen Seiten.

---

# English

## 1 — The Report (start page)

> **The Report** — https://report.qubic.tools/dashboard/
>
> The start page answers one question: **how is revenue spread across the 676
> computor slots?**
>
> Three views:
> • **Revenue across slots** — all 676 at a glance, one cell per slot
> • **Concentration over time** — Gini, HHI, Nakamoto ⅓/½ across epochs
> • **Table for this epoch** — slot by slot, sortable
>
> Measured, epoch 230 (sealed): 676 of 676 computors paid, 179,468,234,779 QU in
> revenue, Gini 0.0093, the best-paid slot holding 0.15 %. Nakamoto ⅓ = 223.
> **The payouts are almost flat** — that is the result, not my opinion.
>
> Now the limit that travels with every figure on the page: these are **slots,
> not operators**. Nakamoto ⅓ = 223 means 223 of 676 *slots*, not 223 independent
> operators. Where one operator holds several slots, the real concentration is
> higher — possibly much higher.
>
> Why I cannot resolve that: **declared operators: 0**. On-chain there is nothing
> to derive — every computor is credited by the same null address (protocol
> emission, not a transfer), and their outgoing transfers are uniformly
> 1,000,000 QU burns to that same address. No transfer graph links two computors.
> The report reports this as `linkage_coverage: 0` rather than dressing it up as
> independence.
>
> So the second half depends on you: a pool declares its slots via a pull request
> against `data/self_reporting/pools.json`, and the operator view turns itself on
> for that pool with no code change. Git history is the audit trail.

## 2 — Burn Report

> **Burn Report** — https://report.qubic.tools/dashboard/burn.html
>
> How much QUBIC has been destroyed, and by what?
>
> Currently measured: **53,662,829,138,067 QU burned — 23.2 % of supply.**
>
> Why this page exists at all instead of simply mirroring the official counter:
> `/v1/latest-stats → burnedQus` is cumulative and authoritative, but it is an
> **epoch aggregate**. Measured across 375 ticks it did not move by a single QU.
> A per-day series cannot come from it. So this project counts the burn events
> themselves from a Bob node's tick logs and reconciles the sum against the
> official counter at every epoch boundary — that ratio is the coverage figure on
> the page.
>
> Two things the page deliberately keeps apart: **contract burns** sit in their
> own panel and are never summed with the transfer burns above — they are a
> different kind of burn. Contract names come from the registry Qubic publishes
> for the official explorer; an index that registry does not list stays an index
> and gets no invented name.
>
> **Where it honestly stands right now:** the project's own measurement is only
> starting up. The total above is solid, but the **per-day series and the
> coverage figure stay empty until the worker has observed burn events across a
> full epoch boundary.** Until then the page shows nothing there rather than an
> extrapolated number. Where coverage later falls below 100 %, a burn category
> exists that this scan does not recognise yet — that gets reported too, not
> hidden.

## 3 — Mining Live

> **Mining Live** — https://report.qubic.tools/dashboard/mining.html
>
> The report measures *distribution across epochs*. This page shows the
> **machinery right now**.
>
> What runs live there:
> • **Accepted solutions** from the swarm, with growth per minute
> • **Node status** — which peer was queried, latency, version, how many
>   neighbours answer
> • **The task** — input trits, sequence length, context window, threshold, plus
>   the data hash verified byte for byte against the core
> • **Revenue distribution** across all 676 slots as a histogram, with the top-10
>   share
>
> All of it comes straight from a public Qubic node over the peer port (21841),
> with the peer list from `api.qubic.li/Public/Peers`. Nothing is estimated,
> nothing smoothed after the fact.
>
> Two limits, so the numbers are read correctly: the **revenue scores are
> approximate until an epoch closes** — that is the core's own documentation, not
> my caveat. And the page is **beta**: it hangs off a single node, and when that
> node does not answer it says "no node reachable" rather than showing you a
> stale figure.
>
> What the page deliberately does *not* claim: what the task actually represents.
> The protocol does not disclose that, so nothing is stated about it.

## 4 — How it works

> **How it works** — https://report.qubic.tools/dashboard/how-it-works.html
>
> The method, in the open and in full — English and German on the same page.
>
> If you want to reuse a figure from the report, read this first. The page
> covers:
> • **what CFB actually asked for**, and what the tool makes of it
> • **the data sources** — which figure comes from which source
> • **the analytical engines** — how Gini, HHI and Nakamoto are computed here
> • **persistence** — why closed epochs are computed once and *sealed*, while the
>   running epoch is recomputed live
> • **two clocks: what is live and what is not** — the section to read if you want
>   to know how old a given number is
> • **where the report stands** — including what is still missing
>
> The principle underneath: no sample data, ever. If the store is empty the API
> answers 503 and the page says it is still building — it does not invent
> placeholders. Every figure is labelled for what it measures.

## 5 — Status & Log

> **Status & Log** — https://report.qubic.tools/dashboard/log.html
>
> A page for one question: **is the service running cleanly right now, or not?**
>
> This is not a showcase, it is the diagnostics page — there so you can check
> instead of taking my word for it. If the report ever shows nothing, this page
> tells you whether it is still building or actually broken. That difference
> matters, and the page states it plainly.
>
> It shows:
> • a **verdict at the top** — four checks in plain language, with a problem list
>   if needed
> • **epochs in the store** — which are `sealed`, which one is running
> • **the ingest log** — the worker's last lines, unfiltered
>
> It polls `/v1/diagnostics` and `/v1/log` and refreshes itself every 15 seconds.
>
> One rule that carries the whole report and is most visible here: **the report
> only appears once at least one epoch is `sealed`** — a running one does not
> count. Better "still building" than half a number.
>
> Two notes: the page is **beta**, and it is **currently German-only**, unlike
> every other page.

---

## Notes for whoever posts this

- **Quote sealed epochs only.** Epoch 231 was running while this was written and
  its revenue is partial (`complete: false`, plus a warning about epoch bleed).
  Check `/v1/report/latest` and use the newest `sealed` epoch.
- **Do not cut the burn page's "measurement still starting up" paragraph.**
  Without it the post promises a daily series that the page does not yet show.
- **Do not cut "slots, not operators."** It is the single most important
  qualifier in the whole project; a post that drops it overstates what the
  Nakamoto figures mean.
- **Do not cut the beta notices** on Mining Live and Status & Log. Both pages
  carry them in their own footers.
- **The Status & Log post names the German-only limitation** — better said up
  front than discovered by an English reader clicking through.
- The X post is one message on purpose. The 4-post thread already exists in
  `docs/ANNOUNCEMENT_LIVE.md` (EN) and `docs/ANNOUNCEMENT_LIVE.de.md` (DE).
- Post only once the URLs actually load — deploys go via Docker Hub and the
  watcher installs after the push.
