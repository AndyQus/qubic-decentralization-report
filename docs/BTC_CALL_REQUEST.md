# Request to the core developers: make the colony's call readable

Drafts for asking Qubic to publish what the ant colony's networks say about
the next BTC hour. Background and the alternative of rebuilding the networks
ourselves: [`CONCEPT_BTC_PREDICTION.de.md`](CONCEPT_BTC_PREDICTION.de.md) §5.

Checked against `qubic/core` v1.313.0 (`2fee231`), live epoch 240, 2026-11-18.

Status: the report now rebuilds the networks itself (`tools/replayer`, see the concept
§5a), so the request is no longer blocking. It still stands, because a published
call would replace a rebuild that every observer has to repeat.

**Who posts:** the maintainer, by hand. Nothing here is sent automatically.

---

## GitHub issue for qubic/core (English)

**Title:** Public read of the BPP-9000 networks' output for the next hour

> Since v1.309 the BPP-9000 targets come from the live BTC/USDT feed through the
> Price oracle, and `REQUEST_PRICE_SERIES` (78) and `REQUEST_BPP9000_TASK_DATA` (80)
> make the task public. Thank you, both work well from outside. We read them on
> https://report.qubic.tools/dashboard/btc.html and verify the targets against
> `dataHash` with K12.
>
> One thing cannot be read from outside: what the networks *say*. A network's
> output after `WINDOW_WIDTH + shiftLimit` is its call for the next hour, and that
> is the number people expect when they hear "the miners predict Bitcoin". Today it
> is only reachable through `REQUEST_ANT_PARENT_ANN` (74) and
> `REQUEST_ANT_IDENTITY_TREE` (72). Both are operator-signed, and the end-of-epoch
> export `antColonySolutions.eoe` stays on the node.
>
> **Proposal.** Any one of these would be enough, smallest first:
>
> 1. A public request `REQUEST_BPP9000_CALL` (anchor tick in, out: `nextHour`, and
>    the next-hour output trit (0/1/2) of the epoch's current best `(shift, error)`
>    network, with its identity, shift and error). One trit per tick costs
>    almost nothing to serve.
> 2. The same plus a tally over the stored top-N networks (count of 0 / 1 / 2).
>    That is the colony's vote.
> 3. Making 74 (`REQUEST_ANT_PARENT_ANN`) public for stored nodes. Anyone could
>    then roll a network forward with the reference scorer.
>
> Why it matters: it turns "the network trains on Bitcoin" into something anyone
> can check. A call is only meaningful if it was fixed before its hour was stored
> (`appendTick`). With (1) or (2) an outside observer can record calls ahead of
> time and publish an honest hit rate against 50 %.
>
> Without it we would have to rebuild networks from public data: the root from the
> pubkey and the spectrum digest, then replaying each walk from the transaction's
> nonce and the anchor tick digest. That is possible, but it duplicates every
> node's scoring work and is fragile across releases.

---

## Discord: Deutsch

> **Bitte an die Core-Devs: den Tipp der Kolonie lesbar machen** 🐜📈
>
> Seit Epoche 240 trainiert BPP-9000 auf echten BTC-Stunden aus dem Preis-Oracle.
> Die Aufgabe ist über die neuen Nachrichten 78/80 öffentlich. Danke, das läuft:
> https://report.qubic.tools/dashboard/btc.html
>
> Was fehlt, ist das, was alle eigentlich sehen wollen: **was die Netze für die
> nächste Stunde sagen.** Das geht heute nur mit Operator-Signatur (72/74).
>
> Vorschlag: eine öffentliche Abfrage, die pro Tick die Ausgabe des aktuell besten
> Netzes für die nächste Stunde liefert (▲/▼/?), gern mit Stimmverteilung der
> Top-Netze. Dann kann jeder die Tipps vor der Stunde aufzeichnen und eine ehrliche
> Trefferquote gegen 50 % zeigen.
>
> Details: https://github.com/AndyQus/qubic-decentralization-report/blob/main/docs/BTC_CALL_REQUEST.md

## Discord: English

> **Request to the core devs: make the colony's call readable** 🐜📈
>
> Since epoch 240 BPP-9000 trains on real BTC hours from the Price oracle, and the
> task is public via the new messages 78/80. Thanks, it works:
> https://report.qubic.tools/dashboard/btc.html
>
> What's missing is the part everyone actually wants to see: **what the networks
> say about the next hour.** Today that needs an operator signature (72/74).
>
> Proposal: a public read that returns, per tick, the current best network's output
> for the next hour (▲/▼/?), ideally with the vote of the top networks. Anyone could
> then record calls before the hour arrives and publish an honest hit rate against
> 50 %.
>
> Details: https://github.com/AndyQus/qubic-decentralization-report/blob/main/docs/BTC_CALL_REQUEST.md
