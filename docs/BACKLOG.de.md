# Backlog

## Offen

_(nichts offen)_

## Erledigt

- [x] **9. Y-Achse: Ziffern statt Nullen** — die Achse rechts legt die führenden Nullen ab und zeigt die Ziffern: `0,00003234` → `3234`. Alle Marken teilen denselben Bezug (vom größten Wert), damit die Ziffern untereinander vergleichbar bleiben und eine Achse über einer Zehnerpotenz nicht jagt: `0900 / 1000 / 1100`. Der weggelassene Faktor steht als eine Zeile über der Achse — ohne ihn wäre „3234" keine Zahl mehr. Passt der Wert ohnehin in fünf Nachkommastellen, wird nichts abgetrennt: `0,123` bleibt `0,123`.

- [x] **10. Footer vereinheitlicht** — rechts steht auf allen sechs Seiten die laufende Code-Version aus `/health`. Vorher waren es vier verschiedene Dinge, und `mining.html` hatte eine ganz eigene Fußzeile (Copyright rechts, „Status & Log" zwischen den Rechtstexten). Die Navigation zwischen den Seiten steht im Kopf, wo sie hingehört.

- [x] **11. „Funktionsweise" → „Wie es funktioniert"** — nur `price.html` wich ab.

- [x] **12. Kurs-Animation vom letzten Besuch** — beim Verlassen merkt sich der Browser den zuletzt gezeigten Kurs. Beim nächsten Besuch beginnt die Hero-Zahl bei diesem alten Wert und zählt von dort auf den aktuellen hoch oder runter; die Kurve wird in derselben Bewegung von links nach rechts aufgedeckt. Dazu ein Satz, der sagt, seit wann und um wie viel.

  Die Animation ist nicht der Zweck, sondern die Antwort auf „was ist passiert, seit ich zuletzt hier war?". Sie unterbleibt deshalb, wenn sie nichts aussagen würde: älter als 7 Tage, Preis unverändert, Zeitstempel in der Zukunft (falsch gehende Uhr), `prefers-reduced-motion`, oder erster Besuch überhaupt. Gespeichert wird je Browser — „seit ich zuletzt hingesehen habe" ist eine Aussage über diesen Betrachter, nicht über die Daten.
