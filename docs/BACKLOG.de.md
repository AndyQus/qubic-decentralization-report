# Backlog

## Offen

_(nichts offen)_

## Erledigt (letzte Runde)

- [x] **Bildschirm wachhalten** — Schalter in der Fußzeile aller sechs Seiten, Voreinstellung an. Nutzt die Screen Wake Lock API; blendet sich aus, wo der Browser sie nicht kennt (Safari erst ab iOS 16.4). Der Punkt zeigt, ob die Sperre wirklich GEHALTEN wird — der Browser gewährt sie erst nach der ersten Berührung der Seite, und das System kann sie jederzeit entziehen.
- [x] **Navigation** — „Mining Live" heißt in der Navigation jetzt „Mining"; „Wie es funktioniert" ist aus der Navigation in die Fußzeile gewandert. Oben stehen damit nur noch die vier Ansichten mit Daten.
- [x] **Refresh-Ticker unter den Chart** — auf Kurs und Mining. Er sagt etwas über die gezeigten Daten, nicht über die Seite, und steht jetzt neben dem, worauf er sich bezieht.
- [x] **Kurs-Seite: Luft und Text** — mehr Abstand zwischen Preis und Diagrammkarte auf dem Telefon; die Beschreibung des Kursverlaufs auf einen Satz gekürzt.
- [x] **Faktorzeile abgeschaltet** — „0,0000000…" über der Achse ist auskommentiert, nicht gelöscht: `axisLabels` liefert den Wert weiter, nur das Zeichnen ist stillgelegt. Zurückholen = Kommentarzeichen entfernen.
