# Trendlinien auf der Price-Seite — Konzept

*Automatisch gesetzte Widerstands- und Unterstützungslinien im Kurschart: wie „die zwei
höchsten und die zwei tiefsten Punkte verbinden“ zu einer Regel wird, die ein Rechner
reproduzierbar anwenden kann, wie die Linien gespeichert werden, und wie die Seite mit zu
vielen Linien umgeht.*

Status: v0.2 — Entscheidungen getroffen (§10), Phase 1 umgesetzt und an echten Daten
kalibriert (§11) · Sprache: Deutsch (eine englische Fassung folgt, sobald das Konzept steht)

---

## 1. Der Anlass

Vorlage ist ein von Hand gezeichneter Chart (QUBIC/USDT, Monatskerzen, MEXC). Darin stehen:

- eine **Widerstandslinie** über zwei Hochs (oben, fallend),
- eine **Unterstützungslinie** unter zwei Tiefs (unten, fallend),
- dazu **Parallelen**, die zusammen einen **Kanal** bilden, und eine gestrichelte
  **Mittellinie**,
- Pfeile an den Stellen, an denen der Kurs eine Linie berührt hat.

Die Anforderung: Die Price-Seite soll solche Linien **immer** zeigen, **selbst** setzen und
**speichern**. Wenn es mehr als zwei sinnvolle Linien gibt, sollen es mehr sein dürfen —
und wenn es zu viele werden, müssen sie sich ein- und ausblenden lassen.

## 2. Der Befund, der das Design bestimmt

**Die Vorlage zeigt Jahre. Diese Seite hat Tage.**

Stand 26.09.2026 (`/v1/price/coverage`): 116 Stundenzeilen, erste Stunde 21.09.2026
13:00 UTC. Die Aufzeichnung beginnt beim ersten Lauf des Workers, die RPC liefert nur
„jetzt“, und nachgefüllt wird nichts (siehe README, „No sample data, ever“). Die
Monatshistorie aus dem Bild von MEXC zu übernehmen, ist ausgeschlossen: MEXC verbietet das
Weiterveröffentlichen (AGB Ziffer 17d), und genau deshalb kommt der Preis dieser Seite aus
Qubics eigener RPC.

Zwei Folgen:

1. **Anfangs gibt es nur kurzfristige Linien.** Eine Linie über zwei Stundenhochs der
   letzten Tage ist ehrlich und nützlich, aber sie ist keine Mehrjahres-Trendlinie. Die
   langen Linien wachsen mit der Aufzeichnung. Nach einigen Wochen gibt es Tageslinien, die
   dem Bild nahekommen.
2. **Die Seite muss sagen, wenn sie noch keine Linie ziehen kann.** Das ist dasselbe Muster
   wie beim 50-Tage-Schnitt (`ready`, `sma-note`): lieber ein Satz „braucht noch N Tage“ als
   eine Linie aus zu wenig Punkten.

## 3. Aus „zwei höchste Punkte“ wird eine Regel

Wörtlich genommen funktioniert „verbinde die zwei höchsten Punkte“ nicht. Die zwei höchsten
Kerzen liegen meist direkt nebeneinander, und die Linie zwischen ihnen sagt nichts aus. Die
Hand im Bild macht implizit drei Dinge, die der Rechner explizit machen muss:

### 3.1 Wendepunkte statt Einzelwerte

Ein **Hoch-Wendepunkt** ist eine Stunde (bzw. ein Tag), deren Hoch über den Hochs der `k`
Nachbarn links **und** rechts liegt. Für Tiefs gilt das spiegelbildlich. `k` bestimmt die
Größe der Schwünge, auf die es ankommt (Werte je Skala in §4). Plateaus (der Preis steht oft minutenlang still, siehe „Stufen“) zählen einmal, beim
ersten Zeitpunkt.

Ein Wendepunkt ist erst dann bestätigt, wenn `k` Perioden nach ihm vergangen sind. Das ist
der unvermeidliche Verzug jeder Pivot-Methode, und die Seite verschweigt ihn nicht.

### 3.2 Die Hüllen-Regel: welche zwei Wendepunkte

Für die **Widerstandslinie**:

1. Über alle Hoch-Wendepunkte im Suchbereich wird die **obere konvexe Hülle** gelegt.
   Jede Kante dieser Hülle ist eine Linie, über der kein anderer Wendepunkt liegt. Das ist
   genau die Eigenschaft, die die Hand im Bild sucht: Die Linie liegt *auf* den Kerzen,
   nicht durch sie hindurch.
2. Eine Kante scheidet aus, wenn ihre Anker näher als `min_sep` beieinanderliegen, wenn
   eine Kerze zwischen den Ankern mehr als `tol` durch die Linie sticht oder wenn die Linie
   schon gebrochen ist (§5).
3. Von den übrigen Kanten gewinnt die mit den **meisten Berührungen**. Bei Gleichstand
   entscheidet der jüngere zweite Anker, danach die längere Spanne.

Häufig gewinnt die Kante vom höchsten Hoch zum nächsten Wendepunkt, also wörtlich „die zwei
höchsten Punkte verbinden“. Zwingend ist das aber nicht. Die Regel ist deterministisch: Aus
denselben Daten entsteht immer dieselbe Linie.

**Anker sind nur bestätigte Wendepunkte**, und die Wendepunkte werden auf der *ganzen* Reihe
bestimmt, nicht nur im Suchbereich. Die stündliche Nachsimulation der ersten Tage (§11)
zeigte, warum das nötig ist: Mit beliebigen Kerzen als Anker gewann immer die neueste, und
die Linie verschob sich fast jede Stunde. Mit Wendepunkten nur im Suchbereich zog dessen
wandernder linker Rand den ersten Anker mit. Jetzt ändert sich eine Linie nur, wenn der
Markt einen neuen Schwung macht.

```
 Preis
  │   ●A1                               A1, A2 = Wendepunkte auf der
  │    ╲  ·
  │     ╲    ·  ●A2                              oberen Hülle
  │      ╲ ·      ╲
  │   ·   ╲    ·    ╲   ·               kein Hoch liegt über der Linie
  │         ·         ╲       ·
  └──────────────────────────────── Zeit
```

Die **Unterstützungslinie** entsteht spiegelbildlich: Tief-Wendepunkte, untere Hülle, kein
Tief darunter.

### 3.3 Qualität: Berührungen und Mindestabstand

Eine Linie wird nur gespeichert, wenn

- die Anker mindestens `min_sep` Perioden auseinanderliegen (je Skala, §4; sonst sind es
  wieder zwei Nachbarkerzen),
- und sie bewertet ist: **Berührungen** = Wendepunkte, die höchstens `tol` (Vorschlag:
  0,5 % des Preises) von der Linie entfernt liegen. Das sind die Pfeile im Bild. Zwei
  Berührungen (die Anker) sind das Minimum, jede weitere macht die Linie belastbarer und
  steht im Tooltip.

## 4. Die Linienfamilie

Pro Skala höchstens diese vier Linien. Mehr zeigt auch das Bild nicht:

| Linie | Entsteht aus | Zeichnung |
|---|---|---|
| Widerstand | obere Hülle (§3.2) | durchgezogen, Farbe `--price-down` |
| Unterstützung | untere Hülle | durchgezogen, Farbe `--price-up` |
| Kanal-Parallele | Parallele zur stärkeren Linie durch den extremsten Gegen-Wendepunkt | dünn |
| Mittellinie | Mitte zwischen Linie und Parallele | gestrichelt |

Einen **Kanal** gibt es nur, wenn Widerstand und Unterstützung annähernd parallel laufen
(Steigungen weichen höchstens 30 % voneinander ab). Andernfalls bilden die beiden Linien
einen Keil oder ein Dreieck, und eine Mittellinie wäre dort bedeutungslos. Die Seite zeigt
dann nur die beiden Linien, benennt die Form aber im Tooltip.

Die Parallele muss, wie jede Linie, **mindestens zweimal berührt** werden. Eine Parallele
durch eine einzelne Spitze wäre ein Band um diese Spitze und kein Kanal, in dem sich der Kurs
bewegt. Fällt die Parallele praktisch auf die Gegenlinie, wird sie weggelassen, und nur die
Mittellinie kommt dazu. Zwei übereinanderliegende Linien läsen sich sonst wie eine dicke.

### Skalen

| Skala | Kerzen | `k` | `min_sep` | `tol` | Suchbereich | Fenster | Bereit ab |
|---|---|---|---|---|---|---|---|
| `short` | Stunde | 3 | 5 h | 0,5 % | 48 h | 24 h | 24 Stunden |
| `hour` | Stunde | 6 | 18 h | 0,5 % | 14 Tage | 7 d | 72 Stunden |
| `day` | Tag (aus `price_hours`) | 3 | 9 Tage | 1 % | alles | Alle | 30 Tage |

Ursprünglich waren es zwei Skalen. Die Kalibrierung zeigte, dass eine Stundenskala nicht
gleichzeitig zum 7-Tage- und zum 24-Stunden-Fenster passt. Über die Woche war die richtige
Widerstandslinie eine Verbindung zweier Hochs, die vier Tage auseinanderlagen. Den
Abverkauf der letzten 24 Stunden erklärte diese Linie nicht. Deshalb bekommt **jedes Fenster
seine eigene Skala**.

In den Fenstern **1 h** und **6 h** gibt es keine Linien. Auf Minutendaten wäre jede Linie
Rauschen. Der Schalter ist dort deaktiviert und erklärt per Tooltip, warum.

Grundlage ist `price_hours`, nicht `price_points`: Die Stundentabelle ist dauerhaft, die
Punkte werden nach 90 Tagen gelöscht. Eine gespeicherte Linie darf nie auf Daten zeigen, die
es nicht mehr gibt.

## 5. Speichern: Linien sind Ereignisse, keine Berechnung beim Laden

Würde der Browser die Linien bei jedem Laden neu berechnen, spränge die Linie, sobald eine
neue Stunde hinzukommt, und niemand könnte später nachsehen, welche Linie am Dienstag
galt. Deshalb berechnet der **Worker** sie und schreibt sie fest.

```sql
CREATE TABLE IF NOT EXISTS price_trendlines (
    id           INTEGER PRIMARY KEY,
    scale        TEXT NOT NULL,     -- 'short' | 'hour' | 'day'
    kind         TEXT NOT NULL,     -- 'resistance' | 'support' | 'parallel' | 'mid'
    t1 INTEGER NOT NULL, p1 REAL NOT NULL,   -- Anker 1 (unix, USD/QU)
    t2 INTEGER NOT NULL, p2 REAL NOT NULL,   -- Anker 2
    touches      INTEGER NOT NULL,  -- Berührungen inkl. Anker
    found_at     INTEGER NOT NULL,  -- wann der Worker sie gesetzt hat
    status       TEXT NOT NULL,     -- 'active' | 'broken' | 'replaced'
    ended_at     INTEGER,           -- Bruch- bzw. Ersetzungszeitpunkt
    channel_id   INTEGER            -- verbindet die Linien eines Kanals
);
```

**Lebenszyklus** (einmal pro Stunde, direkt nach `rollup_price_hours`):

1. **Anker sind unveränderlich.** Eine gespeicherte Linie wird nie verschoben, nur beendet.
2. **Bruch:** Zwei aufeinanderfolgende Stundenschlusskurse (bei der Tagesskala zwei
   Tagesschlusskurse) liegen mehr als `tol` jenseits der Linie. Dann gilt `status='broken'`,
   `ended_at` = erste dieser Stunden. Ein kurzer Docht durch die Linie ist kein Bruch, und
   das Bild zeigt genau solche Dochte.
3. **Ersetzung:** Findet die Regel aus §3 mit neuen Daten ein anderes Ankerpaar (z. B. ein
   neues Hoch über Anker 1), wird die alte Linie `replaced` und die neue eingefügt.
4. **Sonst** bleibt alles, wie es ist. Nur `touches` darf wachsen.

So entsteht nebenbei ein **Archiv**: welche Linien es gab, wie lange sie hielten und wie oft
sie berührt wurden. Das kann keine Börsenseite, auf der jemand Linien von Hand zieht.

### API

`GET /v1/price/trendlines?scale=short|hour|day&status=active|all&since=<unix>`

```json
{
  "scale": "hour",
  "ready": true,
  "hours_on_record": 116,
  "hours_needed": 72,
  "lines": [
    { "id": 12, "kind": "resistance", "t1": 1790100000, "p1": 4.21e-7,
      "t2": 1790280000, "p2": 4.02e-7, "touches": 3,
      "status": "active", "found_at": 1790283600, "channel_id": 4 }
  ]
}
```

Der Endpunkt liefert nur Anker. Wo die Linie im sichtbaren Fenster liegt, rechnet der
Browser aus: `p(t) = p1 + (p2 − p1)·(t − t1)/(t2 − t1)`.

## 6. Anzeige, und was bei zu vielen Linien passiert

### Bedienung

Ein weiterer Schalter in `.chart-controls`, gebaut wie „50-Tage-Schnitt“:
**„Trendlinien“**. Die Wahl bleibt in `localStorage` (`qdr.price.trend`). **Standard: an**
(entschieden, §10).

Die Legende bekommt pro sichtbarer Linienart einen **anklickbaren Eintrag**:

```
■ Preis   ╱ Widerstand   ╱ Unterstützung   ┅ Kanal   ◌ Gebrochene (aus)
```

Ein Klick blendet die jeweilige Art aus oder ein. Auch das wird in `localStorage`
gespeichert. Damit ist „ein- und ausblenden“ pro Art gelöst, ohne ein eigenes Menü.

### Die Regeln gegen Überfüllung

1. **Pro Skala höchstens vier aktive Linien** (§4). Das begrenzt das System selbst.
2. **Pro Fenster nur eine Skala.** 24 h zeigt `short`, 7 d zeigt `hour`, „Alle“ zeigt
   `day`. Mehrere Skalen gleichzeitig zu zeigen, wäre genau das Durcheinander, das
   vermieden werden soll.
3. **Gebrochene und ersetzte Linien** sind standardmäßig aus. Eingeschaltet erscheinen sie
   blass, enden am Bruchpunkt mit einem kleinen ×, und es werden höchstens die letzten drei
   gezeigt.
4. **Nur Linien, die das Fenster schneiden.** Eine Linie, deren Verlauf komplett außerhalb
   der Preisspanne des Fensters liegt, wird nicht an den Rand geklemmt (anders als der
   Durchschnitt). Eine geklemmte Schräge würde eine falsche Steigung zeigen.
5. **Die Linie endet bei „jetzt“.** Sie wird nicht in die Zukunft verlängert. Zwischen den
   Ankern wird sie durchgezogen gezeichnet, danach bis zum rechten Rand dünner. Das ist die
   Strecke, auf der sie sich bisher bewährt hat.

### Tooltip

Beim Überfahren einer Linie steht dort: Art, beide Anker (Zeit, Preis), Berührungen,
Steigung in % pro Tag, gefunden am und, falls gebrochen, gebrochen am. Die Berührungspunkte
bekommen kleine Kreise, das sind die Pfeile aus dem Bild.

### Einordnung auf der Seite

Ein Satz unter dem Chart, in derselben Tonlage wie der Hinweis zu den Stufen:
*„Die Linien sind Geometrie aus den bisherigen Hochs und Tiefs, gesetzt nach einer festen
Regel. Sie sind keine Prognose.“*

**Achse:** Der Chart hat eine lineare Preisachse. Die Linien werden deshalb im linearen Raum
berechnet, damit sie auch gerade aussehen. Eine logarithmische Achse ist ein eigenes Thema
(erst bei „Alle“ über viele Monate relevant) und würde dann beide Rechnungen umstellen.

## 7. Und von Hand gezogene Linien?

Möglich, aber nur **pro Betrachter**, im eigenen Browser (`localStorage`): auf den Chart
klicken, zwei Punkte setzen, fertig. Serverseitig gespeicherte Handlinien bräuchten ein
Login oder einen Schreibschlüssel. Der Container hat bewusst keine Secrets (siehe
Deploy-Entscheidungen), und eine offene Schreibschnittstelle würde missbraucht. Vorschlag:
als spätere, getrennte Phase, erst wenn die automatischen Linien sich bewährt haben.

## 8. Das Archiv auf der Seite

Entschieden (§10): Das Archiv wird sichtbar. Unter dem Chart steht eine schmale Tabelle der
Linien des gewählten Fensters, die neueste oben, höchstens 10 Zeilen. Standardmäßig ist sie
eingeklappt, damit der Chart der Blickfang bleibt.

| Linie | Anker | Berührungen | Steigung | Ergebnis |
|---|---|---|---|---|
| Widerstand | 22.09. 05:00 → 23.09. 13:00 | 4 | −0,5 %/Tag | gebrochen 25.09. 08:00 (nach oben) |
| Unterstützung | 23.09. 20:00 → 25.09. 04:00 | 3 | +0,8 %/Tag | hält |

Die Spalte „Ergebnis“ ist der eigentliche Wert: *hält*, *gebrochen am … (nach oben/unten)*
oder *ersetzt am …* (durch eine neue Linie, ohne Bruch). Ein Klick auf eine Zeile hebt die
Linie im Chart hervor, auch wenn sie gebrochen und sonst ausgeblendet ist. Die Tabelle nennt
außerdem, seit wann Linien aufgezeichnet werden. Ein Archiv ohne Rückfüllung beginnt beim
ersten Lauf, und das muss dort stehen.

Die Daten kommen aus demselben Endpunkt mit `status=all`. Eine eigene Tabelle braucht es
nicht.

## 9. Umsetzung in Phasen

| Phase | Inhalt | Dateien |
|---|---|---|
| 1 ✅ | Pivot- und Hüllenalgorithmus als reine Funktion, mit Tests auf synthetischen Reihen (fallender Kanal, Dreieck, Zufallsreihe, Plateau, zu wenig Daten) | `qdr/trendlines.py`, `tests/test_trendlines.py` |
| 2 | Tabelle, Lebenszyklus im Worker nach dem Stunden-Rollup, API-Endpunkt mit `ready` | `qdr/store.py`, `qdr/pipeline.py`, `api/server.py` |
| 3 | Zeichnen in `drawSteps`/`drawFlow`, Schalter, Legenden-Toggles, Tooltip, Archivtabelle (§8), Hinweistext DE/EN | `dashboard/price.html` |
| 4 | Tagesskala freischalten, sobald ≈ 30 Tage aufgezeichnet sind (läuft automatisch über `ready`) | — |
| 5 (optional) | Handlinien pro Betrachter | `dashboard/price.html` |

Phase 1 ist das eigentliche Risiko und lässt sich isoliert prüfen: Liefert der Algorithmus
auf der echten Reihe der ersten fünf Tage Linien, die ein Mensch auch so ziehen würde? Erst
wenn ja, lohnen die Phasen 2 und 3.

## 10. Entscheidungen

Getroffen am 26.09.2026:

1. **Standard an.** Die Linien sind der Grund für das Feature.
2. **Parameter** werden an den echten Daten kalibriert (§11) und danach nicht mehr
   stillschweigend geändert, weil sonst das Archiv seine Vergleichbarkeit verliert. Muss
   doch einmal geändert werden, bekommt die Skala einen neuen Namen (z. B. `hour2`), und
   die alten Linien bleiben unter dem alten Namen im Archiv.
3. **Das Archiv wird sichtbar** (§8).

## 11. Kalibrierung an den ersten 116 Stunden (Phase 1)

Grundlage: `/v1/price/series?resolution=hour` am 26.09.2026, 21.09. 13:00 bis 26.09. 08:00
UTC. Der Kurs fiel am 21./22.09. von 4,43 auf 4,01 ·10⁻⁷ USD, lief dann drei Tage seitwärts
bis leicht fallend, sprang am 25.09. um 08:00 auf 4,55 und fiel bis heute Morgen auf 4,28.

**Ein Einzelbild genügt nicht.** Jede Parameterwahl wurde zusätzlich *stündlich
nachgespielt*: Die Erkennung lief für jede Stunde nur auf den Daten, die zu diesem Zeitpunkt
vorlagen. Erst das zeigte das Springen der Linien (§3.2), das auf einem Einzelbild
unsichtbar ist.

| Wechsel der Linien in 116 h | vorher (Kerzen als Anker) | nachher (Wendepunkte als Anker) |
|---|---|---|
| Skala `hour` | ≈ 20 | 7 |
| Skala `short` | ≈ 70 | 19 |

**Skala `hour`, Stand 24.09. 21:00:** ein fallender Kanal. Der Widerstand vom 22.09. 05:00
zum 23.09. 13:00 hatte 4 Berührungen, die Unterstützung vom 21.09. 20:00 zum 22.09. 23:00
hatte 3, dazu die Mittellinie. Das ist dieselbe Figur wie in der Vorlage. Der Widerstand wurde
am 25.09. um 08:00 als gebrochen erkannt, in genau der Stunde des Sprungs.

**Skala `short`, jetzt:** eine fallende Widerstandslinie von der Spitze (25.09. 12:00) über
das nächste Hoch (18:00), also über den Abverkauf, und die steigende Unterstützung unter der
Seitwärtsphase mit 3 Berührungen.

Geändert gegenüber v0.1: Wendepunkte statt Kerzen als Anker (§3.2), eine dritte Skala (§4),
`min_sep` für `short` von 8 auf 5 Stunden (bei 8 fand sich die fallende Linie von der
Spitze aus nicht), und die Kanalregeln (zwei Berührungen der Parallele, keine doppelte
Linie).
