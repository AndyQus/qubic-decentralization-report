# Aufruf an die Pools — Discord & X, DE und EN

Kurzfassungen zum direkten Posten, jeweils mit Link auf die Registry. Die
ausführliche Discord-Fassung mit der kompletten Datei steht in
`docs/POOL_REQUEST.de.md`.

Registry: https://github.com/AndyQus/qubic-decentralization-report/blob/main/data/self_reporting/pools.json
Report: https://report.qubic.tools/dashboard/

Stand der Zahlen (live gelesen, 2026-09-08): Epochen 220–228 abgeschlossen,
Nakamoto ⅓ = 222 von 676 Slots, eingetragene Betreiber: **0**.

---

# Deutsch

## Discord

> **Pools: tragt eure Computor-Slots ein** 📋
>
> Der Dezentralisierungs-Report (https://report.qubic.tools/dashboard/) misst
> den Umsatz pro Computor-Slot aus der Chain. Was er **nicht** kann: sagen, wer
> diese Slots betreibt.
>
> Das ist keine Bequemlichkeit, sondern eine Sackgasse: Computor-Umsatz ist
> Protokoll-Emission und keine Überweisung, alle Computors werden von derselben
> Null-Adresse gutgeschrieben, und ihre Abflüsse sind einheitliche Burns. Es gibt
> auch keine API, aus der sich das auslesen ließe — der öffentliche RPC kennt
> keine Betreiber, `api.qubic.li/Public/Peers` liefert nur IP-Adressen. Die
> Selbstauskunft ist damit nicht eine Möglichkeit unter mehreren, sondern die
> einzige.
>
> Deshalb steht auf jeder Zahl im Report **Slots, nicht Betreiber**: Nakamoto ⅓
> = 222 heißt 222 von 676 *Slots*. Hält ein Pool mehrere davon, ist die echte
> Konzentration höher als angezeigt.
>
> Die Registry liegt offen — für **qubic.li**, **Apool** und
> **MinerLab/Solutions** steht der Rahmen bereits drin, aber wie ihr seht, ist
> **jede `computors`-Liste leer**:
> https://github.com/AndyQus/qubic-decentralization-report/blob/main/data/self_reporting/pools.json
>
> Genau da gehören eure Identitäten hinein (60 Zeichen, A–Z):
>
> ```json
>       "computors": [
>         "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
>         "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
>       ],
> ```
>
> Nicht gelistete Pools hängen einen neuen Block an `pools` an — gleiche Felder,
> `id` frei wählbar.
>
> Zwei Wege, beide gleich recht:
> • **Hier antworten** mit Pool-Name und Identitäten — ich übernehme es in einen PR.
> • **Selbst einen PR öffnen** gegen `data/self_reporting/pools.json`.
>
> Danach erscheint ihr im Report als Betreiber mit Slot-Anzahl und Umsatzanteil
> statt als „unattributed". Keine Codeänderung nötig, der Eintrag genügt.
>
> Zwei Dinge vorweg, damit klar ist, worauf ihr euch einlasst: Die Angabe ist
> **öffentlich und versioniert** — die Git-Historie ist der Prüfpfad, und das ist
> Absicht. Und `verified` bleibt `false`, bis die Angabe belegt ist; eine
> Selbstauskunft ist ein Hinweis, kein Eigentumsnachweis, und der Report
> kennzeichnet das so.
>
> Wer nichts einträgt, bleibt „unattributed". Das ist kein Vorwurf — nur das,
> was der Report dann eben anzeigt.

## X

> 🌐$QUBIC Decentralization Report updated.
>
> Pools: tragt eure Computor-Slots ein. Wer sie betreibt, gibt das Ledger nicht
> her — keine API zeigt es.
>
> Darum: Nakamoto 222 von 676 *Slots*, nicht Betreiber.
>
> Einträge nehme ich nur im Discord entgegen. Registry:
> https://github.com/AndyQus/qubic-decentralization-report/blob/main/data/self_reporting/pools.json

*(279/280, URL als 23 Zeichen gerechnet — praktisch am Anschlag, jede Ergänzung
muss also gleich viel wieder einsparen.)*

---

# English

## Discord

> **Pools: declare your computor slots** 📋
>
> The decentralization report (https://report.qubic.tools/dashboard/) measures
> revenue per computor slot from the chain. What it **cannot** do is tell you who
> operates those slots.
>
> That is a dead end, not an omission: computor revenue is protocol emission
> rather than a transfer, every computor is credited by the same null address,
> and their outflows are uniform burns. No API exposes it either — the public RPC
> carries no operator data, and `api.qubic.li/Public/Peers` returns IP addresses
> only. Self-reporting is not one option among several; it is the only one.
>
> So every figure in the report says **slots, not operators**: Nakamoto ⅓ = 222
> means 222 of 676 *slots*. If one pool holds several, real concentration is
> higher than the number shown.
>
> The registry is public — **qubic.li**, **Apool** and **MinerLab/Solutions**
> already have their frame in it, but as you can see, **every `computors` list is
> empty**:
> https://github.com/AndyQus/qubic-decentralization-report/blob/main/data/self_reporting/pools.json
>
> That is where your identities go (60 characters, A–Z):
>
> ```json
>       "computors": [
>         "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
>         "BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"
>       ],
> ```
>
> Pools not listed yet append a new block to `pools` — same fields, `id` is
> yours to choose.
>
> Two routes, both fine:
> • **Reply here** with your pool name and identities — I will carry it into a PR.
> • **Open a PR yourself** against `data/self_reporting/pools.json`.
>
> After that the report groups your slots under your name, with slot count and
> revenue share, instead of showing them as "unattributed". No code change — the
> entry is enough.
>
> Two things stated up front: the declaration is **public and versioned** — git
> history is the audit trail, and that is the point of it. And `verified` stays
> `false` until the declaration is corroborated; a self-report is a claim, not
> proof of ownership, and the report labels it that way.
>
> Anyone who declares nothing stays "unattributed". That is not an accusation —
> it is simply what the report then shows.

## X

> 🌐$QUBIC Decentralization Report updated.
>
> Pools: declare your computor slots. Who operates them, the ledger does not
> disclose — and no API exposes it.
>
> So: Nakamoto 222 of 676 *slots*, not operators.
>
> Declarations via Discord only. Registry:
> https://github.com/AndyQus/qubic-decentralization-report/blob/main/data/self_reporting/pools.json

*(265/280, URL counted as 23 characters.)*

---

## Hinweise

- **„Slots, nicht Betreiber" / "slots, not operators" wörtlich lassen** — das
  Dashboard benutzt dieselbe Formulierung, wer über den Post kommt, findet
  dieselbe Aussage wieder.
- **Nicht sagen, die Einträge lägen „bereit"**, ohne dazuzusagen, dass die Listen
  leer sind: ein Pool könnte sonst annehmen, seine Slots seien längst erfasst und
  es sei nichts zu tun.
- Der X-Post nennt bewusst die Registry, nicht das Dashboard: die Bitte ist die
  Botschaft, nicht der Report.
- **X leitet Einträge ausdrücklich nach Discord.** Damit bleiben die Angaben an
  einem Ort, an dem nachgefragt werden kann — auf X wären sie verstreut und
  schwer zuzuordnen. Die Discord-Fassungen nennen die Chat-Antwort deshalb
  zuerst und den eigenen PR als zweiten Weg; wer selbst einen PR öffnet, ist
  natürlich weiterhin willkommen.
- Zahlen ändern sich an jeder Epochengrenze (~4,4 Tage). Bei späterem Posten neu
  aus `https://report.qubic.tools/v1/dashboard-data` lesen.
