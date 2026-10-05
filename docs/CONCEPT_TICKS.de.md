# Ticks Live — Konzept

*Die Neuauflage von „Qubic Star Rain“ als Seite des Qubic Reports: ein Sternenflug, in dem
jeder Tick des Netzwerks als Würfel heranfliegt, mit Ticknummer, Leader-Computor und Inhalt —
dazu das Terminal links, jetzt abschaltbar. Gespeist von einem Bob-Node statt vom alten
`rt.qubic.li`, flüssig auf Desktop und Handy, und so gebaut, dass eine Apple Watch
wenigstens eine sinnvolle Rückfallstufe bekommt.*

Status: umgesetzt (05.10.2026) — `qdr/tickstream.py`, `/v1/ticks/*`, `dashboard/ticks.html` + `ticks.js`.
Offen ist nur Schritt 0 auf einer echten Apple Watch (§7). · Vorlage: `C:\Softwareentwicklung\qubic\qubic-star-rain`

---

## 1. Was die alte Anwendung macht

| Teil | Datei | Was es tut |
|---|---|---|
| Szene | `src/universe.ts` | Ein 2D-Canvas, 1000 Sterne, `requestAnimationFrame`-Schleife |
| Sterne | `src/elements/star.ts` | Punkt-Perspektive (`x / z`), pro Stern ein `arc()` + `fill()` pro Frame |
| Würfel | `src/elements/cube.ts` | Drahtgitter-Würfel aus 8 Ecken, fliegt von hinten nach vorn, **ohne Inhalt** |
| Live-Quelle | `src/live-cube.ts`, `wss.transfer.service.ts` | WebSocket `wss://rt.qubic.li/live` — **ein Würfel pro Nachricht** (Tick, QuTransfer, Seed, SystemInfo) |
| Terminal | `live-cube.ts` → `#terminal` | Jede Nachricht als Zeile oben eingefügt, **nie gekürzt** |
| Stats-Flug | `src/stats-info.ts` | Ein DOM-Text fliegt aus der Mitte heran, rotiert durch 11 Werte aus `/v1/latest-stats` |

Das Prinzip — Sternenflug, Würfel = Ereignis, Terminal links, Kennzahlen fliegen durch die
Mitte — bleibt. Die Umsetzung wird ersetzt.

### 1.1 Warum sie schlecht läuft (und auf der Watch gar nicht)

Die alte Quelle ist übrigens **nicht** tot: `wss://rt.qubic.li/live` lieferte am 05.10.2026
noch `{"Tick":83188033,"MessageType":"Tick"}`. Die Probleme liegen im Client:

1. **Layout in jedem Frame.** `StatsInfo.update()` setzt `style.fontSize` eines DOM-Elements
   60–120× pro Sekunde. Das erzwingt Style-Neuberechnung und Layout pro Frame — der teuerste
   Fehler im Projekt.
2. **Bewegung pro Frame statt pro Zeit.** `z -= speed` je Frame: Auf einem 120-Hz-iPhone
   (ProMotion) fliegt alles doppelt so schnell, auf einem langsamen Gerät ruckelt es *und*
   wird langsam.
3. **1000 `arc()`-Pfade pro Frame**, unabhängig von der Bildschirmgröße — auf einer
   Watch genauso viele wie auf einem 4K-Monitor.
4. **Das Terminal wächst unbegrenzt.** Nach einer Stunde hängen tausende DOM-Knoten im Baum.
5. **Je Nachricht ein Würfel.** Transfers, Seeds und Ticks erzeugen gemeinsam Würfelschwärme,
   ohne dass man sieht, was ein Würfel *ist*.
6. **Jeder `resize`-Listener wird pro Würfel registriert und nie entfernt** (Speicherleck).
7. Ballast: `<img>` im `<head>`, drei Google-Fonts-Requests + Material Icons, CSS-Nesting
   im `@media`-Block (ältere Safari-Versionen verwerfen den Block).

**Zur Apple Watch, ehrlich:** watchOS hat keinen Safari. Webseiten öffnen sich nur über Links
(Nachrichten, Mail) in einer abgespeckten WebKit-Ansicht mit wenig Rechenzeit. Welche
Web-APIs dort zuverlässig laufen (WebSocket, EventSource, Canvas, `requestAnimationFrame`),
ist nicht verlässlich dokumentiert — das wird **gemessen, nicht vermutet** (§7, Schritt 0).
Das Konzept ist deshalb in Stufen gebaut: volle Szene, wenn das Gerät sie trägt, sonst eine
reine Text-Ansicht, die nur `fetch` braucht.

---

## 2. Datenquelle: Bob-Node (gemessen am 05.10.2026)

Der Bob-Node, den der Report schon für Revenue und Burns nutzt (`QDR_BOB_URL`), hat einen
WebSocket-Endpunkt `/ws/qubic` mit `qubic_subscribe`. Gültige Abos laut Node:
`newTicks`, `logs`, `transfers`, `tickStream`.

20 Sekunden mitgeschnitten an `wss://bob.qubic.li/ws/qubic`:

| Abo | Nachrichten / 20 s | Datenmenge / 20 s | pro Tick | Inhalt |
|---|---|---|---|---|
| `tickStream` | 36 | 0,73 MB | ~20 KB | Tick, Leader, Zähler, **Transaktionen inkl. Logs** |
| `newTicks` | 36 | **26 MB** | ~720 KB | Tick-Header + **alle 676 Votes** |
| `logs` / `transfers` | je Log-Eintrag | — | — | einzelne Events (Transfer, Custom Message …) |

→ ~1,8 Ticks/s. Ein `tickStream`-Ereignis enthält alles, was ein Würfel zeigen soll:

```json
{ "epoch": 233, "tick": 83187977, "computorIndex": 93,
  "hasNoTickData": false, "isSkipped": false, "isCatchUp": false,
  "timestamp": "2026-10-05T12:29:08Z", "totalLogs": 28, "totalTxs": 11,
  "transactions": [ { "hash": "ehrk…", "from": "ZTZE…", "amount": 1,
                      "executed": true, "inputData": "0x…", … } ] }
```

Ergänzend per JSON-RPC (POST auf `QDR_BOB_URL`), alle getestet:

| Methode | Zweck hier |
|---|---|
| `qubic_getTickNumber` `[]` | aktueller Tick (Poll-Fallback) |
| `qubic_getTickByNumber` `[tick]` | Tick-Header + Tx-Hashes (Detailansicht, Poll-Fallback) |
| `qubic_getLogs` `[{"fromTick":a,"toTick":b}]` | Events der Ticks (Detailansicht) |
| `qubic_getComputors` `[epoch]` | die 676 Computoren **in Index-Reihenfolge** |

Zwei Fallen, die beim Test aufgefallen sind:

* `qubic_getComputors` hängt an jede Identity ein `÷` (U+00F7) an — vor dem Vergleich
  abschneiden.
* Mining-Lösungen stehen als Log `type 255` (Custom Message) mit Präfix `ANT_SOLU`
  (`414e545f534f4c55` hex) im Tick. Damit kann ein Würfel „enthält Mining-Lösungen“ zeigen —
  die direkte Brücke zu Mining Live. Der Rest des Payloads (im Test stand dort u. a. der
  Klartext `qli-cpu`) ist vor jeder Anzeige gegen `qubic/core` zu prüfen, nicht zu raten.

**Bob oder Lite-Node:** Der Relay (§3) spricht nur „Bob-kompatibel“. Ein eigener Bob-Node
oder ein Lite-/Home-Node, der dieselben Methoden und `/ws/qubic` anbietet, wird über
`QDR_BOB_URL` eingesetzt. Bietet ein Node keinen WebSocket, fällt der Relay automatisch auf
Polling zurück (§3.2) — dieselbe Seite, nur mit 1 s Takt statt Push.

---

## 3. Architektur: ein Relay im API-Container

```
Bob /ws/qubic ──(1 Verbindung, ~36 KB/s)──► qdr/tickstream.py
                                               │  verdichten auf ~1 KB/Tick
                                               │  Ringpuffer: letzte 300 Ticks
                                               ▼
                         ┌─────────────────────┴─────────────────────┐
              GET /v1/ticks/stream (SSE)                GET /v1/ticks/recent?after=N
              Browser, Handy, Desktop                   Watch / Fallback (fetch-Poll)
```

**Warum nicht der Browser direkt an Bob:**

1. **Bandbreite.** `tickStream` sind ~36 KB/s ≈ **130 MB pro Stunde** pro Zuschauer.
   Verdichtet sind es ~2 KB/s (gemessen: 1,0–1,4 KB pro Tick). Auf dem Handy-Netz und erst recht auf der Watch entscheidet
   das allein.
2. **Fremde Nodes.** Wie bei `/v1/mining`: Egal wie viele Browser zusehen, Bob sieht genau
   **eine** Verbindung. Ist niemand da, wird sie nach 60 s geschlossen.
3. **Konfigurierbar.** Wer seinen eigenen Node hat, setzt `QDR_BOB_URL` — die Seite bleibt
   gleich.
4. **Anreicherung**, die der Browser nicht kann: Leader-Index → Computor-Identity → Pool/Cluster
   aus dem Report, Burn-Netting aus `qdr/burn.py`, Vertragsnamen aus `qdr/contracts.py`.

**Warum im API-Container und nicht im Worker:** Das sind flüchtige Live-Daten wie
`/v1/pulse` — nichts davon wird gespeichert, die API bleibt lesend gegenüber dem Store
(`test_api_is_read_only` bleibt grün). Der Worker müsste sie sonst durch die Datenbank
reichen, nur damit die API sie wieder ausliest.

**Neue Abhängigkeit: keine.** `uvicorn[standard]` bringt `websockets` (13.1 lokal) schon mit.

**Deployment:** nur Docker Hub, kein Host-Zugriff (siehe Memory). Alles muss im Image
funktionieren. Ein Reverse-Proxy vor `report.qubic.tools` könnte SSE puffern; dagegen
`X-Accel-Buffering: no` + `Cache-Control: no-cache`, und der Client schaltet von selbst auf
Polling um, wenn 10 s lang kein Ereignis (auch kein Heartbeat) ankommt.

### 3.1 Das verdichtete Tick-Objekt

```json
{ "tick": 83187977, "epoch": 233, "ts": 1791203348,
  "state": "ok",                 // ok | empty | skipped
  "leader": { "index": 93, "id": "ABCD…WXYZ", "cluster": "Unattributed" },
  "tx": 11, "tx_failed": 0, "logs": 28,
  "qu_moved": 2000002, "burned": 0,
  "solutions": 1,                // ANT_SOLU-Custom-Messages
  "contracts": [ { "index": 1, "name": "QX", "calls": 2 } ],
  "top": [ { "h": "ehrk…", "kind": "transfer", "from": "ZTZE…", "to": "DAAA…", "qu": 1 } ]
}
```

`top` hat höchstens 5 Einträge (größte Beträge zuerst). Die volle Liste holt erst die
Detailansicht.

### 3.2 Endpunkte

| Endpunkt | Zweck |
|---|---|
| `GET /v1/ticks/stream` | SSE. `id:` = Ticknummer, damit ein Reconnect per `Last-Event-ID` die verpassten Ticks aus dem Ringpuffer nachbekommt. Heartbeat alle 15 s. |
| `GET /v1/ticks/recent?after=<tick>&limit=50` | Dasselbe als JSON-Liste — für Watch, alte Browser und den automatischen Fallback. |
| `GET /v1/ticks/{tick}` | Details: alle Transaktionen und Events des Ticks (Bob `getTickByNumber` + `getLogs`), 10 min gecacht. Nur im Bereich des laufenden Epochs (Bob hält ältere Tick-Logs nicht, DATA_SOURCES §7.2). |

Status ehrlich ausweisen, wie überall im Report: `source: "bob-ws" | "bob-poll"`,
`stale: true` wenn Bob schweigt, `catch_up: true` wenn Bob nachläuft (`isCatchUp`).

### 3.3 Konfiguration

| Variable | Default | Zweck |
|---|---|---|
| `QDR_BOB_WS` | aus `QDR_BOB_URL` abgeleitet (`https://h/qubic` → `wss://h/ws/qubic`) | WebSocket-Endpunkt |
| `QDR_TICKS_BUFFER` | `300` | Ticks im Ringpuffer (~3 min) |
| `QDR_TICKS_IDLE` | `60` | Sekunden ohne Zuschauer, bis die Bob-Verbindung schließt |

---

## 4. Die Seite `dashboard/ticks.html`

Neuer Menüpunkt **Ticks** in der Navigation aller Seiten (Report · Burn · Price · Mining ·
**Ticks**). Wie die übrigen Seiten: reines HTML/JS ohne Build, `theme.css`, EN/DE, `wake.js`
(Bildschirm wachhalten passt genau zu dieser Seite).

```
┌──────────────────────────────────────────────────────────────────────┐
│ Qubic Report   Report Burn Price Mining [Ticks]        EN DE  🌙     │
├───────────────────┬──────────────────────────────────────────────────┤
│ ▸ TERMINAL   [×]  │                ·      ·                          │
│ 12:29:08 #83187977│        ·   ┌───────────┐       ·                 │
│   11 tx · L93     │            │ 83.187.977│            ·            │
│ 12:29:07 #83187976│     ·      │ 11 tx · ⛏1│                         │
│   burn 1 Mio QU   │            └───────────┘   ·                     │
│ 12:29:07 #83187975│   ·              ·     ┌─────┐                   │
│   leer            │                        │ 976 │      ·            │
│ …                 │                ·       └─────┘                   │
│ [Ticks][Tx][Burn] │                                                  │
│ [⛏][Verträge]     │  Tick 83.187.977 · Epoche 233 · 1,8 Ticks/s  ● live │
└───────────────────┴──────────────────────────────────────────────────┘
```

### 4.1 Würfel = Tick

**Ein Würfel pro Tick**, nicht pro Nachricht. Auf der Vorderseite steht die Ticknummer, darunter
eine kurze Zeile mit dem Inhalt. Die Art des Ticks ist sofort ablesbar:

| Tick | Darstellung |
|---|---|
| normal | Cyan-Drahtgitter (wie bisher), Größe wächst mit `log(tx)` |
| leer (`hasNoTickData`) | schmal, gedimmt, gestrichelt — „nichts drin“ |
| übersprungen (`isSkipped`) | rot, Nummer durchgestrichen |
| mit Burn | orange glühende Kanten |
| mit Mining-Lösungen (`ANT_SOLU`) | grüner Kern, `⛏ n` |
| mit großem Transfer (Schwelle konfigurierbar) | goldene Kante |
| Smart-Contract-Aufrufe | Vertragsname als Kürzel (`QX`, `QUTIL` …) |

Mehrere Merkmale kombinieren sich (Kantenfarbe = wichtigstes, Badges = die übrigen).

Antippen/Klicken eines Würfels oder einer Terminalzeile öffnet ein **Detailpanel**: Leader
(Index, Identity, Pool/Cluster aus dem Report), alle Transaktionen mit Von/An/Betrag/Status,
Vertragsaufrufe, Burns, Lösungen — jede Tx verlinkt auf `explorer.qubic.org`. Solange das Panel
offen ist, verlangsamt sich der Flug, damit man lesen kann.

### 4.2 Terminal links

* Ein- und ausschaltbar per Button und Taste **T**; der Zustand bleibt in `localStorage`
  (in `try/catch`, wie `wake.js`).
* Filter-Chips: Ticks · Transaktionen · Burns · Lösungen · Verträge.
* **Fest begrenzt auf 250 Zeilen**; ältere werden entfernt, neue Zeilen per
  `DocumentFragment` gesammelt einmal pro Frame eingefügt.
* Unter 600 px Breite standardmäßig aus; dann als Overlay von links einblendbar.

### 4.3 Stats-Flug

Der Text, der aus der Mitte heranfliegt, bleibt — aber aus der eigenen API (`/v1/pulse`,
`/v1/price/latest`, `/v1/burn/latest`) statt direkt aus der RPC, und **im Canvas gezeichnet**,
nicht als DOM-Element. Abschaltbar wie das Terminal.

---

## 5. „Flüssig“: was die Render-Schleife anders macht

| Alt | Neu |
|---|---|
| DOM-Text, `fontSize` pro Frame | Alles in **einem** Canvas. Würfeltexte werden einmal in ein kleines Offscreen-Canvas gerendert und danach nur noch skaliert per `drawImage` gezeichnet. |
| Bewegung pro Frame | Bewegung pro **Zeit** (`dt` aus dem rAF-Timestamp, gekappt bei 50 ms) — gleich schnell auf 60 Hz, 120 Hz und einem langsamen Gerät. |
| 1000 Sterne fix, `arc()` je Stern | Anzahl skaliert mit Fläche (≈ 1 Stern / 2500 px², max. 800); Positionen in `Float32Array`; kleine Sterne als `fillRect`, ein Pfad pro Helligkeitsstufe statt je Stern. |
| Würfel ohne Obergrenze | Max. 40 aktive Würfel; bei Rückstand (Tab war im Hintergrund) werden Ticks nur noch ins Terminal geschrieben, nicht alle nachgeflogen. |
| `resize`-Listener pro Objekt | Ein Listener, entprellt; `devicePixelRatio` auf 2 gekappt. |
| läuft im Hintergrund weiter | `visibilitychange` → Schleife und Stream pausieren; beim Zurückkommen holt `/v1/ticks/recent` die Lücke. |
| — | **Adaptive Qualität:** Liegt der gleitende Frame-Mittelwert über 20 ms, werden zuerst Glow, dann Sterne reduziert. |
| — | `prefers-reduced-motion` → kein Flug, die Ticks erscheinen als ruhige Liste. |
| Google Fonts + Material Icons | Systemschrift / die Fonts, die `theme.css` ohnehin lädt. |

Ziel: konstante 60 fps auf einem Mittelklasse-Handy, < 5 % CPU auf dem Desktop.

---

## 6. Apple Watch: Stufenmodell

Die Seite entscheidet beim Start selbst, was das Gerät kann, statt auf einen User-Agent zu
raten:

| Stufe | Bedingung | Darstellung |
|---|---|---|
| **Voll** | Canvas + rAF, Breite ≥ 320 px | Szene, Terminal, Stats-Flug; Stream per EventSource, sonst Polling |
| **Kompakt** | Canvas + rAF, Breite < 320 px (Watch; Handys ab 360 px laufen voll) | 150 Sterne, **ein** Würfel zur Zeit, große Ticknummer, kein Terminal, Polling über `/v1/ticks/recent` alle 2 s |
| **Text** | kein Canvas/rAF oder Frame-Zeit dauerhaft > 50 ms | nur HTML: aktuelle Ticknummer groß, darunter Tx · Burn · ⛏, per `fetch`-Poll; keine Animation |

Erzwingen per URL zum Testen: `ticks.html?mode=compact` / `?mode=text`.

---

## 7. Umsetzungsschritte

0. **Watch-Probe** (½ h): `ticks.html?probe` zeigt nur eine Tabelle — Canvas? rAF und
   gemessene fps? EventSource? WebSocket? `fetch`? Bildschirmgröße, DPR. Auf der Watch per
   Link aus Nachrichten öffnen, Ergebnis notieren. Danach steht fest, welche Stufe die Watch
   bekommt.
1. **`qdr/tickstream.py`** — Bob-WS-Client (asyncio, Reconnect mit Backoff), Poll-Fallback,
   Verdichtung, Ringpuffer. Tests mit aufgezeichneten `tickStream`-Nachrichten als Fixtures
   (wie `test_bob.py`): leerer Tick, übersprungener Tick, Burn mit Rückerstattung im selben
   Tx (darf **nicht** als Burn zählen, DATA_SOURCES §7.2b), `ANT_SOLU`, Vertragsaufruf.
2. **Endpunkte** in `api/server.py` + Eintrag im `/api`-Index. Test: SSE-Resume per
   `Last-Event-ID`, `recent?after=`, Leerlauf schließt die Upstream-Verbindung.
3. **Leader-Anreicherung:** `qubic_getComputors [epoch]` einmal pro Epoche (`÷` abschneiden),
   Identity → Cluster aus dem Report des laufenden Epochs.
4. **`dashboard/ticks.html` + `ticks.js`** — Szene, Würfel, Terminal, Detailpanel, Stufen.
5. **Navigation** auf allen Seiten um „Ticks“ erweitern; `verify_dashboard.py` /
   `test_branding.py` anpassen.
6. **Verifizieren:** lokal gegen `bob.qubic.li`; Performance-Profil in Chrome (Desktop +
   Handy-Emulation mit 4× CPU-Drosselung); echte Geräte: iPhone, Android, Watch.
7. Doku: Abschnitt in `DATA_SOURCES.md` (Bob WebSocket), `README`, englische Fassung dieses
   Konzepts.

## 7a. Umsetzungsstand (05.10.2026)

| Schritt | Stand |
|---|---|
| 0 Watch-Probe | `ticks.html?probe` gebaut; **auf der echten Watch noch zu öffnen** |
| 1 `qdr/tickstream.py` | fertig; Tests mit 5 aufgezeichneten Ticks (`tests/fixtures/tickstream_sample.jsonl`) |
| 2 Endpunkte | `/v1/ticks/stream`, `/recent`, `/{tick}`; Stream endet nach 10 min und setzt per `Last-Event-ID` nahtlos fort |
| 3 Leader | `qubic_getComputors` + Cluster aus dem gespeicherten Report |
| 4 Seite | fertig; gemessen 60 fps (Chromium, Desktop, Handy, Watch-Größe) |
| 5 Navigation | „Ticks“ auf allen Seiten |
| 7 Doku | `DATA_SOURCES.md` §7.4, README |

Abweichungen vom Entwurf, jeweils aus einer Messung:

* Das Terminal zeigt den Vertrag **Random** (`Reveal and Commit`) nicht als Ereignis:
  er läuft in jedem Tick. Allgemein gilt ein Vertrag, der in mehr als 60 % der Ticks
  vorkommt, als Routine — so fällt ein seltener Aufruf (z. B. `Qx · Add to Ask Order`) auf.
* `qubic_getComputors` liefert hinter den 60 Buchstaben nicht nur `÷`, sondern teils
  weiteren Speicherinhalt; behalten werden genau 60 Buchstaben A–Z.
* Ältere Ticks (nicht mehr im Puffer) bekommen volle Transaktionsdaten über
  `qubic_getTransactionByHash`, parallel und begrenzt.

Nachtrag (05.10.2026, v0.13.0): Die Stufen *kompakt* und *Text* sowie `?probe` sind
entfallen — die Seite hat nur noch die volle Darstellung (§6 gilt damit nicht mehr).
Neu: Tick-Suchfeld, Layout „Wurmloch“ (Spirale, die sich dreht), Würfel überlappen nie
(Platzierung rechnet den ganzen Flug voraus), Panel schließt bei Klick daneben.

## 8. Offene Punkte

* Was die Watch tatsächlich kann — klärt Schritt 0: `https://report.qubic.tools/dashboard/ticks.html?probe` per Nachricht an sich selbst schicken und auf der Watch öffnen.
* Bedeutung des `ANT_SOLU`-Payloads über das Präfix hinaus (gegen `qubic/core` prüfen).
* „Großer Transfer“ ist umgesetzt als oberes 1 % der zuletzt gesehenen Beträge, mindestens
  100 Mio. QU (vor 50 Beobachtungen: 1 Mrd.). Ob das im Alltag zu oft oder zu selten
  anschlägt, zeigt erst der Betrieb.
* Ob der eigene Home-Node `/ws/qubic` anbietet — falls nicht, läuft er über den Poll-Fallback.
