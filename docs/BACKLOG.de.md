# Backlog

Aufgaben aus dem Backlog-Board, in der Reihenfolge der Abarbeitung.

## Offen

- [ ] **3. Mobile Reihenfolge Kurs-Seite** — in der mobilen Ansicht steht der Chart direkt unter dem Kurs; die übrigen Panels folgen darunter.
- [ ] **4. Y-Achse rechts** — auf der Kurs-Seite die Y-Achse an der rechten Seite anzeigen.
- [ ] **5. Mobile Anpassung aller Seiten** — auf der Report-Seite läuft der Text über den Bereich hinaus; alle Seiten für mobile Endgeräte prüfen und korrigieren.
- [ ] **6. Neuanordnung Kurs-Seite (mobil)** — die Kurs-Seite insgesamt für kleine Displays neu ordnen (ergänzt Aufgabe 3).
- [ ] **7. Kalenderübersicht** — GitHub-artige Heatmap über steigende und fallende Tage.
- [ ] **8. Neuer API-Endpunkt** — liefert den letzten Kurs zurück.

## Erledigt

- [x] **1. 50-Tage-Durchschnitt im Chart** — `/v1/price/average` rechnet den Schnitt aus der permanenten Stundentabelle (`price_hours`), nicht aus dem gewählten Fenster: ein 50-Tage-Schnitt aus einer Stunde Messwerten wäre der Stundendurchschnitt unter falschem Namen. Die Kurs-Seite legt ihn als gestrichelte Linie über beide Zeichenarten, mit eigenem Schalter, Legende und Tooltip.

  Diese Seite füllt nichts zurück — die RPC liefert nur „jetzt". Solange weniger als 50 Tage aufgezeichnet sind, bleibt der Schalter stumm und die Seite sagt, wie lange es noch dauert, statt eine Linie aus zu wenig Daten zu zeichnen. Stand 23.09.2026 sind rund 2 Tage auf dem Server; die Linie erscheint von selbst, sobald die Aufzeichnung reicht.

- [x] **2. Favicon in Firefox** — `icon.svg` trug in einem Kommentar einen doppelten Bindestrich (`--gradient`) und war damit kein gültiges XML. Chrome überliest das, Firefox nicht: dort scheiterte das ganze Dokument am Parser. Und weil Firefox von den Icon-Zeilen **nur** das SVG anfordert und von sich aus auf nichts zurückfällt, blieb der Tab leer.

  Behoben, in Firefox 153 nachgemessen (vorher `parsererror`, jetzt `svg`). Zusätzlich steht die `.ico` jetzt ausdrücklich in jeder Seite — `/dashboard/favicon.ico` gibt es nicht, der automatische Griff ans Wurzelverzeichnis läuft von `/dashboard/` aus ins Leere. `log.html` hatte überhaupt keine Icon-Zeilen und hat jetzt welche.
