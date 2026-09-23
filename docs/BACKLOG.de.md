# Backlog

Aufgaben aus dem Backlog-Board. Alle acht sind abgearbeitet.

## Offen

_(nichts offen)_

## Erledigt

- [x] **1. 50-Tage-Durchschnitt im Chart** — `/v1/price/average` rechnet den Schnitt aus der permanenten Stundentabelle (`price_hours`), nicht aus dem gewählten Fenster: ein 50-Tage-Schnitt aus einer Stunde Messwerten wäre der Stundendurchschnitt unter falschem Namen. Die Kurs-Seite legt ihn als gestrichelte Linie über beide Zeichenarten, mit eigenem Schalter, Legende und Tooltip.

  Diese Seite füllt nichts zurück — die RPC liefert nur „jetzt". Solange weniger als 50 Tage aufgezeichnet sind, bleibt der Schalter stumm und die Seite sagt, wie lange es noch dauert, statt eine Linie aus zu wenig Daten zu zeichnen. Stand 23.09.2026 sind rund 2 Tage auf dem Server; die Linie erscheint von selbst, sobald die Aufzeichnung reicht.

- [x] **2. Favicon in Firefox** — `icon.svg` trug in einem Kommentar einen doppelten Bindestrich (`--gradient`) und war damit kein gültiges XML. Chrome überliest das, Firefox nicht: dort scheiterte das ganze Dokument am Parser. Und weil Firefox von den Icon-Zeilen **nur** das SVG anfordert und von sich aus auf nichts zurückfällt, blieb der Tab leer.

  Behoben, in Firefox 153 nachgemessen (vorher `parsererror`, jetzt `svg`). Zusätzlich steht die `.ico` jetzt ausdrücklich in jeder Seite — `/dashboard/favicon.ico` gibt es nicht, der automatische Griff ans Wurzelverzeichnis läuft von `/dashboard/` aus ins Leere. `log.html` hatte überhaupt keine Icon-Zeilen und hat jetzt welche.

- [x] **3./6. Kurs-Seite für mobile Endgeräte neu geordnet** — unter 620 px folgt das Diagramm direkt auf den Preis, die fünf Kennzahl-Kacheln rücken darunter und stehen dort zu zweit statt einzeln. Vorher stapelten sie sich auf rund 300 px und schoben das Diagramm auf y≈608, also unter die erste Bildschirmhöhe; jetzt beginnt es bei y≈196.

  Umgesetzt über `display:contents` am Hero, damit seine Kinder als Flex-Kinder von `.qs-main` einzeln sortierbar werden. Das geht nur, weil der Hero auf dieser Seite keine eigene Fläche hat. Am Desktop bleibt alles wie zuvor.

- [x] **4. Y-Achse rechts** — die Preisachse steht jetzt am rechten Rand, in beiden Zeichenarten. Der jüngste Wert liegt am rechten Ende der Kurve, die Achse steht damit neben der Zahl, um die es geht. Der gemeinsame Vorsatz der Achsenwerte sitzt darüber statt links oben.

- [x] **5. Mobile Anpassung aller Seiten** — geprüft wurden alle sechs Seiten bei 320/390/412/620/621/768/1280 px auf Inhalt, der ohne scrollbaren Vorfahren über den Rand läuft. Gefunden und behoben auf `how-it-works.html`: die Tabelle der Datenquellen war auf eine `min-width` festgenagelt, die ihre letzte Spalte bei jeder Telefonbreite außerhalb des Kastens hielt. Sie bricht jetzt um, verliert auf sehr schmalen Schirmen die `min-width` und trägt einen Scroll-Schatten, der zeigt, dass rechts noch etwas steht.

  Nebenbei aufgefallen: der Themenknopf derselben Seite zeigte nach dem ersten Umschalten `ἱ9` statt 🌙 — der Mond liegt jenseits von U+FFFF und passt nicht in eine Escape mit vier Ziffern.

- [x] **7. Kalenderübersicht** — `/v1/price/days` liefert eine Zeile je Tag, die Kurs-Seite zeichnet daraus ein GitHub-artiges Raster: sieben Zeilen, Wochen als Spalten, steigende Tage in der Leitfarbe, fallende rot, drei Stufen je Richtung an festen Schwellen (0,5 / 1,5 / 3 %).

  Der vierte Zustand ist der wichtigste: ein Tag ohne Messung wird **schraffiert** und nicht neutral getönt. Auf 365 Feldern wäre ein unbeobachteter Tag in der neutralen Farbe eine unsichtbare Falschaussage — das Raster behauptete Ruhe, wo niemand hingesehen hat. Ein Tag mit weniger als 12 aufgezeichneten Stunden zählt dabei schon als nicht gemessen.

- [x] **8. Neuer API-Endpunkt für den letzten Kurs** — `/v1/price/now` gibt dieselbe Messung wie `/v1/price/latest`, aber in rund einem Zehntel der Bytes (156 statt 1506). Erhalten bleiben `at`, `age_s`, `held_for_s` und `status`: ohne sie zeichnet ein Ticker einen stehengebliebenen Worker als lebenden Kurs. Kein Volumen-Feld, hier so wenig wie anderswo.
