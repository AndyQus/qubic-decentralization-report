# Nachfrage an die Pools — fertiger JSON-Block

Für den Fall, dass auf „ein PR gegen `data/self_reporting/pools.json`" die
Rückfrage kommt: *was genau soll da rein?* Diese Nachricht beantwortet das, ohne
dass jemand erst das Repo lesen muss — der Pool kann direkt im Chat antworten,
und der Eintrag wird dann übernommen.

Der wichtigste Punkt für die Erwartungshaltung: **es gibt keine Quelle, aus der
sich die Betreiber auslesen ließen.** Geprüft am 2026-09-08:

- Der öffentliche RPC (`rpc.qubic.org`) liefert Netzwerkstatistik, keine
  Betreiberzuordnung.
- `api.qubic.li/Public/Peers` gibt IP-Adressen und Ticks zurück — keine einzige
  60-Zeichen-Identität, also keine Verbindung Computor → Pool.
- On-Chain findet der Report nichts Belegbares: alle Computors werden von
  derselben Null-Adresse gutgeschrieben, ihre Abflüsse sind einheitliche Burns.

Deshalb ist die Selbstauskunft nicht eine von mehreren Optionen, sondern der
einzige Weg. Das gehört in die Frage hinein — sonst klingt sie nach Bürokratie
statt nach Notwendigkeit.

---

## Discord-Nachricht

> **An die Pool-Betreiber: eure Slots im Dezentralisierungs-Report**
> https://report.qubic.tools/dashboard/
>
> Der Report misst den Umsatz pro Computor-Slot aus der Chain. Was er **nicht**
> kann: sagen, wer diese Slots betreibt. Das Ledger gibt keine Eigentümerschaft
> preis — Computor-Umsatz ist Protokoll-Emission und keine Überweisung, alle
> Computors werden von derselben Null-Adresse gutgeschrieben. Es gibt auch keine
> API, aus der sich das auslesen ließe (der öffentliche RPC kennt keine
> Betreiber, `api.qubic.li/Public/Peers` liefert nur IPs).
>
> Darum steht auf jeder Zahl **Slots, nicht Betreiber**: Nakamoto ⅓ = 222 heißt
> 222 von 676 *Slots*. Hält ein Pool mehrere davon, ist die echte Konzentration
> höher als angezeigt — der Report behauptet ausdrücklich nicht, dass die 676
> Slots 676 unabhängigen Betreibern gehören.
>
> Auflösen lässt sich das nur, wenn ihr eure Identitäten selbst angebt. Die
> Gruppierung läuft bereits in jeder Epoche, sie braucht nur die Einträge:
>
> ```json
> {
>   "id": "euer-slug",
>   "label": "Euer Pool",
>   "url": "https://...",
>   "source": "wo diese Angabe veröffentlicht ist",
>   "verified": false,
>   "computors": [
>     "AAAA...  (60 Zeichen, A-Z)",
>     "BBBB...  (60 Zeichen, A-Z)"
>   ],
>   "status": "active"
> }
> ```
>
> Für **qubic.li**, **Apool** und **MinerLab/Solutions** liegen die Einträge
> schon bereit — dort fehlt nur die `computors`-Liste. Andere Pools legen einen
> neuen Block an.
>
> Zwei Wege, beide gleich recht:
> • **Hier im Chat antworten** — ich übernehme es in einen PR.
> • **Selbst einen PR öffnen** gegen `data/self_reporting/pools.json`.
>
> Was danach passiert: Der Report gruppiert eure Slots unter eurem Namen, und ihr
> erscheint mit Slot-Anzahl und Umsatzanteil in der Betreiber-Tabelle statt als
> „unattributed". Keine Codeänderung nötig — der Eintrag genügt.
>
> Zwei Dinge vorweg, damit klar ist, worauf ihr euch einlasst:
> • Die Angabe ist **öffentlich und versioniert** — die Git-Historie ist der
>   Prüfpfad, und das ist Absicht: sie ist der Grund, warum die Angabe etwas wert
>   ist. Korrigieren oder ergänzen könnt ihr sie jederzeit.
> • `verified` bleibt `false`, bis die Angabe belegt ist (signierte Aussage oder
>   passende On-Chain-Spur). Eine Selbstauskunft ist ein Hinweis, kein
>   Eigentumsnachweis — der Report kennzeichnet das so.
>
> Wer nichts angibt, bleibt „unattributed". Das ist kein Vorwurf, sondern genau
> das, was der Report dann anzeigt.

---

## Kurzfassung (falls die lange Version zu viel ist)

> **Pools: welche Computor-Identitäten gehören euch?**
>
> Der Dezentralisierungs-Report (https://report.qubic.tools/dashboard/) misst den
> Umsatz je Slot, kann aber nicht sagen, wer sie betreibt — das Ledger gibt es
> nicht her, und es gibt keine API dafür. Darum heißt es dort überall „Slots,
> nicht Betreiber": Nakamoto ⅓ = 222 sind 222 von 676 *Slots*, nicht Betreiber.
>
> Antwortet einfach hier mit euren 60-Zeichen-Identitäten und eurem Pool-Namen,
> dann trage ich sie ein — oder direkt per PR gegen
> `data/self_reporting/pools.json`. Danach erscheint ihr im Report als Betreiber
> mit Slot-Anzahl und Umsatzanteil statt als „unattributed".
>
> Für qubic.li, Apool und MinerLab/Solutions stehen die Einträge schon bereit,
> es fehlt nur die Liste der Identitäten.

---

## Wenn Einträge hereinkommen

Ablauf, damit nichts Ungeprüftes in den Report gerät:

1. Identitäten in `data/self_reporting/pools.json` in die `computors`-Liste des
   Pools eintragen, `source` auf die Herkunft setzen (z. B. „Discord-Angabe von
   @name, 2026-09-08").
2. Prüfen:

   ```
   python scripts/validate_registry.py --epoch <aktuelle Epoche>
   ```

   Das ist keine Formalie — der Validator prüft gegen die echten Computors der
   Epoche. Getestet: er meldet `malformed identity` bei falschem Format und
   `not a computor in epoch N` bei einer ID, die in der Epoche gar nicht
   Computor war, und beanstandet Identitäten, die zwei Pools zugleich für sich
   beanspruchen.
3. PR öffnen. `verified` bleibt `false`, solange nur die Selbstauskunft vorliegt.
4. Der nächste Ingest-Lauf übernimmt es — kein Deploy, kein Codeeingriff.

**`computors`-Listen niemals raten.** Sie starten bewusst leer; ein Eintrag
bekommt Identitäten erst, wenn eine echte Angabe dahintersteht. Ein falsch
geratener Eintrag wäre schlimmer als eine leere Liste, weil er wie eine Messung
aussieht.
