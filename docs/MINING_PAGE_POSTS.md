# Neue Seite „Mining Live" — Discord & X, DE und EN

Kurzfassungen zum direkten Posten. Die Seite ist ein zweiter Blick neben dem
Report: der Report misst *Verteilung über Epochen*, diese Seite zeigt die
*Maschinerie im Moment*.

Seite: https://report.qubic.tools/dashboard/mining.html
Report: https://report.qubic.tools/dashboard/
Daten: `GET /v1/mining` — live von einem öffentlichen Qubic-Node über Peer-Port 21841

**Vor dem Posten prüfen:** Die Seite muss deployt und unter der obigen URL
erreichbar sein. Sie trägt selbst einen Beta-Hinweis — die Posts sagen das
ebenfalls, das bitte nicht wegkürzen.

---

# Deutsch

## Discord

> **Neu: Mining Live** 🐜
>
> Der Dezentralisierungs-Report zeigt, wie sich der Umsatz über die Computor-Slots
> verteilt — über ganze Epochen hinweg. Was er nicht zeigt: was die Maschine
> gerade *tut*. Dafür gibt es jetzt eine zweite Seite:
> https://report.qubic.tools/dashboard/mining.html
>
> Was dort live steht:
> • **Akzeptierte Lösungen** des Schwarms, mit Zuwachs pro Minute
> • **Node-Status** — welcher Peer abgefragt wurde, Latenz, Version, antwortende Nachbarn
> • **Die Aufgabe** — Eingangs-Trits, Sequenzlänge, Kontextfenster, Schwelle, dazu
>   der Daten-Hash, byteweise gegen den Core geprüft
> • **Revenue-Verteilung** über alle 676 Slots als Histogramm, mit Top-10-Anteil
>
> Alles kommt direkt von einem öffentlichen Qubic-Node über den Peer-Port (21841),
> die Peer-Liste von `api.qubic.li/Public/Peers`. Nichts ist geschätzt, nichts
> nachträglich geglättet — die Seite zeigt Messwerte und deutet sie nicht.
>
> Zwei Einschränkungen, damit die Zahlen richtig gelesen werden: Die
> **Revenue-Scores sind bis zum Epochenende nur näherungsweise** — das sagt die
> Core-Dokumentation selbst, nicht ich. Und die Seite ist **Beta**: sie hängt an
> einem einzelnen Node, und wenn der nicht antwortet, steht dort ehrlich „kein
> Node erreichbar" statt einer alten Zahl.
>
> Was die Seite ausdrücklich *nicht* sagt: was der Task inhaltlich darstellt. Das
> gibt das Protokoll nicht her, also steht dazu auch nichts da.
>
> Feedback gern hier — besonders, wenn eine Zahl nicht zu dem passt, was euer
> eigener Node sagt.

## X

> 🌐 Neu im $QUBIC Decentralization Report: **Mining Live**.
>
> Akzeptierte Lösungen im Minutentakt, Node-Latenz, das Task-Profil mit
> Core-Hash-Prüfung und die Revenue-Verteilung über alle 676 Slots.
>
> Live vom Node, nichts geschätzt. Beta.
>
> https://report.qubic.tools/dashboard/mining.html

*(255/280, URL als 23 Zeichen gerechnet.)*

---

# English

## Discord

> **New: Mining Live** 🐜
>
> The decentralization report shows how revenue spreads across the computor slots,
> epoch by epoch. What it does not show is what the machine is doing *right now*.
> That is what this second page is for:
> https://report.qubic.tools/dashboard/mining.html
>
> What runs live there:
> • **Accepted solutions** from the swarm, with growth per minute
> • **Node status** — which peer was queried, latency, version, how many neighbours answer
> • **The task** — input trits, sequence length, context window, threshold, plus the
>   data hash verified byte for byte against the core
> • **Revenue distribution** across all 676 slots as a histogram, with the top-10 share
>
> All of it comes straight from a public Qubic node over the peer port (21841),
> with the peer list from `api.qubic.li/Public/Peers`. Nothing is estimated,
> nothing smoothed after the fact — the page reports measurements and leaves the
> interpretation alone.
>
> Two limits, so the numbers are read correctly: the **revenue scores are
> approximate until an epoch closes** — that is the core's own documentation, not
> my caveat. And the page is **beta**: it hangs off a single node, and when that
> node does not answer it says "no node reachable" rather than showing you a stale
> figure.
>
> What the page deliberately does *not* claim: what the task actually represents.
> The protocol does not disclose that, so nothing is stated about it.
>
> Feedback welcome here — especially if a figure disagrees with what your own node
> reports.

## X

> 🌐 New in the $QUBIC Decentralization Report: **Mining Live**.
>
> Accepted solutions by the minute, node latency, the task profile checked against
> the core hash, and revenue spread across all 676 slots.
>
> Live from a node, nothing estimated. Beta.
>
> https://report.qubic.tools/dashboard/mining.html

*(264/280, URL counted as 23 characters.)*

---

## Hinweise

- **Beta nicht wegkürzen.** Die Seite trägt den Hinweis selbst im Footer; ein Post,
  der ihn verschweigt, verspricht mehr Verlässlichkeit als die eine Node-Abfrage
  hergibt.
- **„näherungsweise bis Epochenende" bleibt drin** und wird der Core-Doku
  zugeschrieben, nicht dem Report — sonst liest es sich wie eine Schwäche dieser
  Seite statt wie eine Eigenschaft der Sache.
- **Keine Deutung des Tasks.** Die Seite sagt bewusst nicht, wofür der Task
  inhaltlich steht; die Posts dürfen das nicht nachträglich behaupten.
- Der Discord-Post nennt die vier Abschnitte einzeln, der X-Post fasst sie in einen
  Satz — auf X ist der Link die Botschaft, nicht die Feature-Liste.
- Erst posten, wenn https://report.qubic.tools/dashboard/mining.html tatsächlich
  lädt (Deploy läuft über Docker Hub, der Watcher installiert nach dem Push).
