/* dashboard/wake.js — "Bildschirm wachhalten", für alle Seiten.
 *
 * Hält über die Screen Wake Lock API das Display an, solange die Seite offen
 * und sichtbar ist. Gedacht für die Ansichten, die man nebenher laufen lässt:
 * Kurs und Mining Live sind Dauerbildschirme, und alle 30 Sekunden aufs
 * Display zu tippen ist genau die Sorte Reibung, die eine Live-Seite wertlos
 * macht.
 *
 * WAS DIE API NICHT KANN, und warum der Schalter trotzdem so aussieht, wie er
 * aussieht:
 *
 *   * Eine Sperre wird nur nach einer BENUTZERGESTE gewährt. Ein Aufruf beim
 *     Laden schlägt in Chrome mit NotAllowedError fehl. Die Voreinstellung ist
 *     deshalb zwar "an", greifen kann sie aber erst bei der ersten Berührung
 *     der Seite — bis dahin steht der Schalter auf "wartet". Das ist kein
 *     Fehler dieser Umsetzung, sondern die Bedingung des Browsers.
 *   * Das System nimmt die Sperre zurück, sobald der Tab in den Hintergrund
 *     geht. Beim Zurückkommen muss sie neu angefordert werden.
 *   * Sie braucht HTTPS. Über file:// oder http:// gibt es sie nicht.
 *   * Safari kennt sie erst ab iOS 16.4; ältere iPhones und iPads zeigen den
 *     Schalter gar nicht erst.
 *
 * Der Punkt im Schalter meint deshalb "die Sperre wird GEHALTEN", nicht "sie
 * wurde angefordert". Alles andere wäre eine Behauptung über den Bildschirm,
 * die das Skript nicht einlösen kann.
 */
(function () {
  "use strict";

  var LS_KEY = "qdr.wakeLock";
  var supported = ("wakeLock" in navigator)
    && navigator.wakeLock && typeof navigator.wakeLock.request === "function";

  var btn = document.getElementById("wake-btn");
  if (!btn) return;
  /* Das Markup liefert den Knopf ausgeblendet aus. Das ist Absicht: kann der
     Browser die API nicht, soll er gar nicht erst aufblitzen. Erst hier, wo
     die Unterstützung feststeht, wird er sichtbar. */
  if (!supported) { btn.hidden = true; return; }
  btn.hidden = false;

  /* Voreinstellung: an. Wer ihn einmal ausschaltet, für den bleibt er aus. */
  function stored() {
    try { return localStorage.getItem(LS_KEY); } catch (e) { return null; }
  }
  function store(v) {
    try { localStorage.setItem(LS_KEY, v); } catch (e) {}
  }
  var wanted = stored() !== "0";

  var lock = null;

  function label() {
    var de = (document.documentElement.getAttribute("lang") || "en")
      .toLowerCase().indexOf("de") === 0;
    var held = !!lock;
    btn.setAttribute("aria-pressed", held ? "true" : "false");
    var txt = de
      ? (!wanted ? "Bildschirm aus"
         : (held ? "Bildschirm bleibt an" : "Bildschirm: wartet"))
      : (!wanted ? "Screen may sleep"
         : (held ? "Screen stays on" : "Screen: waiting"));
    var title = de
      ? (held ? "Der Bildschirm bleibt an, solange diese Seite sichtbar ist."
              : (wanted ? "Wird beim ersten Tippen auf die Seite aktiv — der Browser erlaubt es nicht vorher."
                        : "Der Bildschirm darf wie gewohnt abschalten."))
      : (held ? "The screen stays on while this page is visible."
              : (wanted ? "Takes effect on your first tap — the browser does not allow it sooner."
                        : "The screen may dim as usual."));
    btn.title = title;
    var t = btn.querySelector(".wtext");
    if (t) t.textContent = txt;
  }

  function release() {
    if (!lock) return;
    var l = lock;
    lock = null;
    try { l.release(); } catch (e) {}
    label();
  }

  function acquire() {
    if (!wanted || lock) return;
    if (document.visibilityState !== "visible") return;
    navigator.wakeLock.request("screen").then(function (l) {
      lock = l;
      /* Das System kann die Sperre jederzeit zurücknehmen — beim Tabwechsel,
         bei niedrigem Akkustand. Dann muss der Schalter das auch zeigen. */
      l.addEventListener("release", function () {
        if (lock === l) lock = null;
        label();
      });
      label();
    }).catch(function () {
      /* NotAllowedError ist hier der Normalfall, nicht der Ausnahmefall: vor
         der ersten Geste lehnt der Browser ab. Kein Grund für eine Meldung —
         die nächste Berührung versucht es erneut. */
      lock = null;
      label();
    });
  }

  btn.addEventListener("click", function () {
    wanted = !wanted;
    store(wanted ? "1" : "0");
    if (wanted) acquire(); else release();
    label();
  });

  /* Erster Versuch sofort (klappt, wo der Browser es erlaubt), und danach bei
     der ersten echten Geste — das ist der Moment, in dem Chrome sie gewährt.
     `once: true`, damit daraus kein Dauerhorchen auf jeden Klick wird. */
  acquire();
  ["pointerdown", "keydown"].forEach(function (ev) {
    window.addEventListener(ev, function () { acquire(); }, { once: true, passive: true });
  });

  /* Zurück im Vordergrund: neu anfordern, denn das System hat sie entzogen. */
  document.addEventListener("visibilitychange", function () {
    if (document.visibilityState === "visible") acquire(); else label();
  });

  /* Ein Sprachwechsel schreibt `lang` am <html> um; die Beschriftung folgt. */
  if (window.MutationObserver) {
    new MutationObserver(label).observe(document.documentElement,
      { attributes: true, attributeFilter: ["lang"] });
  }

  label();
})();
