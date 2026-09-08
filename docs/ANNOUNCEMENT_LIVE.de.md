# Ankündigung — der Report ist live unter report.qubic.tools

Deutsche Fassung von `docs/ANNOUNCEMENT_LIVE.md`. Beide sagen dasselbe; dies ist
keine Wort-für-Wort-Übersetzung, sondern die deutsche Formulierung derselben
Aussagen — die englischen Posts sind an der Zeichengrenze gebaut, die deutschen
mussten dafür anders geschnitten werden.

Alle Zahlen wurden vor dem Schreiben aus dem Live-Deployment gelesen:

| | |
|---|---|
| Abgeschlossene Epochen | 220–228 (9) |
| Neueste abgeschlossene | 228 |
| Computors | 676 |
| Umsatz, Epoche 228 | 178.477.462.349 QU |
| Identische Auszahlungen | 418 von 676 Slots (61,8 %) zu je 268.701.925 QU |
| Spanne | 3,02-fach zwischen höchstem und niedrigstem Slot |
| Top-Slot | 0,15 % des Umsatzes |
| Gini | 0,0139 |
| Nakamoto ⅓ / ½ | 222 / 333 Slots |
| Eingetragene Betreiber | 0 |

`<link>` vor dem Posten durch https://report.qubic.tools/dashboard/ ersetzen.

---

## X / Twitter

### Einzelpost (empfohlen)

> Qubic Dezentralisierungs-Report — live: report.qubic.tools
>
> Umsatz pro Computor-Slot, pro Epoche, aus der Chain. 9 Epochen abgeschlossen,
> je 676/676 Computors bezahlt.
>
> In Epoche 228 wurden 61,8 % der Slots exakt gleich vergütet.
>
> Was er nicht sagt: wer sie betreibt.

*(272/280, URL von X als 23 Zeichen gerechnet.)*

### Thread (4 Posts)

**1/**

> Der Qubic Dezentralisierungs-Report ist live.
>
> report.qubic.tools
>
> Umsatz pro Computor-Slot, pro Epoche, aus der Chain. Epochen 220-228
> abgeschlossen, je 676/676 Computors bezahlt.
>
> Und die schwierigere Frage, ehrlich: wer diese Slots betreibt, wissen wir
> nicht. 🧵

**2/**

> Warum das Aufwand war: Der öffentliche RPC zeigt die Computor-Auszahlungen gar
> nicht.
>
> Sie sind Protokoll-Emission, keine Überweisung — ein Computor hat keinen
> Zahlungseingang, sein Guthaben wächst aber um Hunderte Millionen QU pro Epoche.
>
> Bobs End-Epoch-Log trägt sie.

**3/**

> Ein gemessenes Ergebnis, Epoche 228:
>
> 418 von 676 Slots — 61,8 % — wurden exakt gleich vergütet: 268.701.925 QU. Der
> bestverdienende Slot bekam das 3,02-fache des schlechtesten und hält 0,15 % des
> Umsatzes.
>
> Gini 0,0139. Die Auszahlungen sind nahezu flach.

**4/**

> Was er nicht sagt: wie viele unabhängige Betreiber es gibt.
>
> Darum zählt jede Zahl Slots, nicht Betreiber. Nakamoto ⅓ = 222 heißt 222 von
> 676 *Slots*. Hält ein Betreiber mehrere, ist die echte Konzentration höher.
>
> Pools: per PR eintragen, dann erscheint die Betreiber-Ansicht.

---

## Discord

### Allgemeiner Channel

> **Der Qubic Dezentralisierungs-Report ist live** 📊
> https://report.qubic.tools/dashboard/
>
> Umsatz pro Computor-Slot, pro Epoche, direkt aus Chain-Daten. Neun Epochen sind
> abgeschlossen (220–228), in jeder wurden 676/676 Computors bezahlt — dazu Gini,
> Nakamoto ⅓/½ und wie sie sich über die Epochen bewegen.
>
> Ein Ergebnis, das für sich steht: In Epoche 228 wurden **418 von 676 Slots
> exakt gleich vergütet** — je 268.701.925 QU. Der bestverdienende Slot bekam das
> 3,02-fache des schlechtesten und hält 0,15 % des Umsatzes. Die Auszahlungen
> sind also nahezu flach pro Slot, Gini liegt bei 0,0139.
>
> Was der Report bewusst **nicht** behauptet: wie viele unabhängige Betreiber
> hinter diesen 676 Slots stehen. Computor-Umsatz ist Protokoll-Emission und
> keine Überweisung — das Ledger gibt also keine Eigentümerschaft preis, und
> bisher hat sich kein Pool selbst eingetragen. Deshalb ist jede Zahl auf der
> Seite als **Slots, nicht Betreiber** ausgewiesen: Nakamoto ⅓ = 222 heißt 222
> von 676 *Slots*. Hält ein Betreiber mehrere davon, ist die echte Konzentration
> höher als die angezeigte Zahl.
>
> Genau da kommt die Community ins Spiel. Die Gruppierung läuft bereits in jeder
> Epoche — sie braucht Einträge, keinen weiteren Code. Ein PR gegen
> `data/self_reporting/pools.json` genügt, dann schaltet sich die
> Betreiber-Ansicht für euren Pool frei.
>
> Repo: <link>

### #computor-operator (Anknüpfung an den früheren Post)

> Der Report ist jetzt erreichbar: https://report.qubic.tools/dashboard/
>
> Seit dem letzten Post sind die Epochen 220–228 abgeschlossen, je 676/676
> Computors bezahlt. Epoche 228: 61,8 % der Slots exakt gleich vergütet zu je
> 268.701.925 QU, Faktor 3,02 zwischen höchstem und niedrigstem, Gini 0,0139,
> Nakamoto ⅓ bei 222 von 676 Slots.
>
> Die Selbstauskunft steht weiterhin bei null, die Betreiber-Zuordnung bleibt
> daher leer und jede Zahl ausgewiesen als Slots, nicht Betreiber. Für qubic.li,
> Apool und MinerLab/Solutions liegen Einträge bereit — ein PR gegen
> `data/self_reporting/pools.json` schaltet die Betreiber-Ansicht für euren Pool
> frei, ganz ohne Codeänderung.
>
> Die Herleitung des Umsatzes ist in `docs/DATA_SOURCES.md` dokumentiert, falls
> ihr sie gegen eure eigenen Zahlen prüfen wollt.

---

## Hinweise zum Posten

- **61,8 % nicht zu „die meisten" oder „fast zwei Drittel" runden.** Der exakte
  Wert ist der Punkt: er ist gemessen und auf der Seite nachprüfbar.
- **„Slots, nicht Betreiber" wörtlich beibehalten.** Das Dashboard benutzt genau
  diese Formulierung — wer über einen Post dorthin kommt, findet dieselbe
  Aussage wieder.
- Der Einzelpost ist dem Thread vorzuziehen, sofern kein längerer Text gewünscht
  ist: die Kernaussage überlebt die Kürzung, der Thread liefert vor allem
  Kontext.
- Jeder X-Post wurde gezählt, nicht geschätzt (URL als 23 Zeichen nach X' Regel):
  Einzelpost 272, 1/ 269, 2/ 270, 3/ 256, 4/ 277. Die deutschen Fassungen sind
  länger als die englischen, mehrere Entwürfe mussten dafür gekürzt werden.
- Die Zahlen ändern sich an jeder Epochengrenze (~4,4 Tage). Wird dies mehr als
  ein paar Tage nach Erstellung gepostet, die Werte neu aus
  `https://report.qubic.tools/v1/dashboard-data` lesen statt der Tabelle oben zu
  vertrauen.
