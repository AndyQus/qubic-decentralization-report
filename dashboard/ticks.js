/* dashboard/ticks.js — Ticks Live.
 *
 * Die Neuauflage von "Qubic Star Rain": jeder Tick des Netzwerks fliegt als
 * Würfel heran, mit Nummer und Inhalt. Konzept: docs/CONCEPT_TICKS.de.md.
 *
 * Was gegenüber dem Original anders ist, und warum (Konzept §1.1, §5):
 *
 *   * Alles, was sich bewegt, ist EIN Canvas. Das Original setzte die
 *     Schriftgröße eines DOM-Elements in jedem Frame — Layout 60-120x pro
 *     Sekunde. Würfeltexte werden hier einmal in kleine Offscreen-Canvases
 *     gerendert und danach nur noch skaliert gezeichnet.
 *   * Bewegung pro ZEIT (dt), nicht pro Frame: auf 60 Hz, 120 Hz und einem
 *     langsamen Gerät fliegt alles gleich schnell.
 *   * Ein Würfel pro TICK, nicht pro Nachricht, mit Nummer und Inhalt.
 *   * Das Terminal hält höchstens 250 Zeilen (ältere fliegen raus) und schreibt
 *     einmal pro Frame.
 *   * Die Seite misst ihre eigene Frame-Zeit und nimmt Last weg, bevor es ruckelt.
 *
 * Datenweg: /v1/ticks/stream (SSE). Hört die Seite 12 s lang nichts — auch
 * keinen Heartbeat —, schaltet sie auf /v1/ticks/recent (Polling) um. Das deckt
 * Proxys ab, die Streams puffern, und Browser ohne EventSource.
 *
 * Kein Build-Schritt, keine Library, kein CDN — wie alle Seiten hier.
 */
(function () {
  "use strict";

  /* ── Helpers ──────────────────────────────────────────────────────────── */
  var root = document.documentElement;
  var params = new URLSearchParams(location.search);
  function safeGet(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function safeSet(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function $(id) { return document.getElementById(id); }
  function now() { return (window.performance && performance.now) ? performance.now() : Date.now(); }
  function esc(s) {
    return (s == null ? "" : String(s)).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* ── i18n ─────────────────────────────────────────────────────────────── */
  var LS_LANG = "qdr.lang";
  var I18N = {
    en: {
      navReport: "Report", burnReport: "Burn", priceReport: "Price", miningLive: "Mining",
      ticksLive: "Ticks", howItWorks: "How it works",
      subtitle: "Every tick · Qubic Report",
      termTitle: "new ticks in the network", waiting: "waiting for the first tick …",
      fTick: "Ticks", fTx: "Transfers", fBurn: "Burns", fContract: "Contracts",
      lgTick: "tick", lgEmpty: "empty", lgSkip: "skipped", lgBurn: "burn",
      lgBig: "large transfer", lgContract: "contract call", lgSol: "⛏ mining solutions",
      btnTerm: "Terminal", btnStats: "Stats",
      searchPh: "Search tick  /", searchGo: "Open tick",
      layoutFree: "Free", layoutWorm: "Wormhole",
      srcConnecting: "connecting", srcStream: "live · stream", srcPoll: "live · polling",
      srcStale: "node silent", srcOff: "offline", srcPaused: "paused",
      epoch: "Epoch {n}", rate: "{n} ticks/s",
      sess: "{ticks} ticks · {tx} tx · ⛏ {sol} · 🔥 {burn} QU",
      tx: "tx", empty: "empty", skipped: "skipped", catchUp: "catch-up",
      burned: "burned", moved: "moved", solutions: "solutions", leader: "leader",
      dTransactions: "Transactions", dEvents: "Other events", dLeader: "Leader (tick proposer)",
      dLoading: "loading …", dFailed: "This tick could not be loaded: {e}",
      dNoTx: "No transactions in this tick.", dSkipped: "This tick was skipped: no computor produced it in time, so it carries no data.",
      dEmpty: "This tick was produced but carries no transactions.",
      dCluster: "operator (report): {c}", dUnattributed: "operator: not attributed in the report",
      dMore: "+ {n} more", dExplorer: "open in explorer",
      dSolNote: "{n} mining solutions submitted in this tick (each pays 1,000,000 QU to the null address and gets it back in the same transaction — no burn).",
      dNote: "Live from a Bob node. Older ticks are read on demand and only exist for the running epoch.",
      kTransfer: "transfer", kBurn: "burn", kContract: "contract", kSolution: "solution",
      kSystem: "system", kCall: "call", kUnknown: "tx", kFailed: "failed",
      stEpoch: "epoch", stTick: "current tick", stTicksEpoch: "ticks in this epoch",
      stEmpty: "empty ticks this epoch", stQuality: "tick quality", stAddr: "active addresses",
      stSupply: "circulating supply", stBurned: "burned QU (total)", stPrice: "price",
      stMcap: "market cap", stProgress: "epoch progress", stRate: "ticks per second",
      stSolMin: "mining solutions / min", stTxTick: "transactions per tick",
      noApi: "This page was opened as a file and has no API. Start the server and open it through it: python -m uvicorn api.server:app --port 8000 → http://127.0.0.1:8000/dashboard/ticks.html",
      discLabel: "Disclaimer:", discBeta: "This page is currently in beta status.",
      discData: "Ticks are relayed live from a Bob node; nothing on this page is stored or estimated.",
      discNoGuarantee: "We do not guarantee the completeness, accuracy, or availability of the data.",
      discDemo: "Use it for analysis purposes only — not as a basis for investment decisions.",
      tos: "Terms of Service", privacy: "Privacy Policy", codeVersion: "code",
      sponsored: "Qubic – Sponsored by AndyQus",
    },
    de: {
      navReport: "Report", burnReport: "Burn", priceReport: "Kurs", miningLive: "Mining",
      ticksLive: "Ticks", howItWorks: "So funktioniert's",
      subtitle: "Jeder Tick · Qubic Report",
      termTitle: "neue Ticks im Netzwerk", waiting: "warte auf den ersten Tick …",
      fTick: "Ticks", fTx: "Transfers", fBurn: "Burns", fContract: "Verträge",
      lgTick: "Tick", lgEmpty: "leer", lgSkip: "übersprungen", lgBurn: "Burn",
      lgBig: "großer Transfer", lgContract: "Vertragsaufruf", lgSol: "⛏ Mining-Lösungen",
      btnTerm: "Terminal", btnStats: "Stats",
      searchPh: "Tick suchen  /", searchGo: "Tick öffnen",
      layoutFree: "Frei", layoutWorm: "Wurmloch",
      srcConnecting: "verbinde", srcStream: "live · Stream", srcPoll: "live · Abfrage",
      srcStale: "Node schweigt", srcOff: "offline", srcPaused: "pausiert",
      epoch: "Epoche {n}", rate: "{n} Ticks/s",
      sess: "{ticks} Ticks · {tx} Tx · ⛏ {sol} · 🔥 {burn} QU",
      tx: "Tx", empty: "leer", skipped: "übersprungen", catchUp: "Nachlauf",
      burned: "verbrannt", moved: "bewegt", solutions: "Lösungen", leader: "Leader",
      dTransactions: "Transaktionen", dEvents: "Weitere Ereignisse", dLeader: "Leader (Tick-Ersteller)",
      dLoading: "wird geladen …", dFailed: "Dieser Tick ließ sich nicht laden: {e}",
      dNoTx: "Keine Transaktionen in diesem Tick.", dSkipped: "Dieser Tick wurde übersprungen: Kein Computor hat ihn rechtzeitig erzeugt, er trägt keine Daten.",
      dEmpty: "Dieser Tick wurde erzeugt, enthält aber keine Transaktionen.",
      dCluster: "Betreiber (Report): {c}", dUnattributed: "Betreiber: im Report nicht zugeordnet",
      dMore: "+ {n} weitere", dExplorer: "im Explorer öffnen",
      dSolNote: "{n} Mining-Lösungen in diesem Tick eingereicht (jede zahlt 1.000.000 QU an die Null-Adresse und bekommt sie in derselben Transaktion zurück — kein Burn).",
      dNote: "Live von einem Bob-Node. Ältere Ticks werden bei Bedarf gelesen und existieren nur für die laufende Epoche.",
      kTransfer: "Transfer", kBurn: "Burn", kContract: "Vertrag", kSolution: "Lösung",
      kSystem: "System", kCall: "Aufruf", kUnknown: "Tx", kFailed: "fehlgeschlagen",
      stEpoch: "Epoche", stTick: "aktueller Tick", stTicksEpoch: "Ticks in dieser Epoche",
      stEmpty: "leere Ticks dieser Epoche", stQuality: "Tick-Qualität", stAddr: "aktive Adressen",
      stSupply: "umlaufende Menge", stBurned: "verbrannte QU (gesamt)", stPrice: "Kurs",
      stMcap: "Marktkapitalisierung", stProgress: "Epochenfortschritt", stRate: "Ticks pro Sekunde",
      stSolMin: "Mining-Lösungen / Min", stTxTick: "Transaktionen pro Tick",
      noApi: "Diese Seite wurde als Datei geöffnet und hat keine API. Starte den Server und öffne sie darüber: python -m uvicorn api.server:app --port 8000 → http://127.0.0.1:8000/dashboard/ticks.html",
      discLabel: "Hinweis:", discBeta: "Diese Seite befindet sich im Beta-Status.",
      discData: "Die Ticks kommen live von einem Bob-Node; auf dieser Seite wird nichts gespeichert oder geschätzt.",
      discNoGuarantee: "Wir garantieren weder Vollständigkeit noch Richtigkeit oder Verfügbarkeit der Daten.",
      discDemo: "Nur zu Analysezwecken verwenden — nicht als Grundlage für Investitionsentscheidungen.",
      tos: "Nutzungsbedingungen", privacy: "Datenschutz", codeVersion: "Code",
      sponsored: "Qubic – Sponsored by AndyQus",
    }
  };
  var lang = safeGet(LS_LANG) || "en";
  if (!I18N[lang]) lang = "en";
  function t(k, vars) {
    var s = (I18N[lang] && I18N[lang][k]) || I18N.en[k] || k;
    if (!vars || s.indexOf("{") < 0) return s;
    return s.replace(/\{(\w+)\}/g, function (m, n) { return vars[n] != null ? vars[n] : m; });
  }
  function locale() { return lang === "de" ? "de-DE" : "en-US"; }
  function fmtInt(n) { return n == null ? "–" : Number(n).toLocaleString(locale()); }
  /* QU in a few characters: 184.8 M, 1.2 B (Mrd. in German). */
  function fmtQu(n) {
    if (n == null) return "–";
    var a = Math.abs(n), u, d;
    if (a >= 1e12) { d = 1e12; u = lang === "de" ? " Bio." : " T"; }
    else if (a >= 1e9) { d = 1e9; u = lang === "de" ? " Mrd." : " B"; }
    else if (a >= 1e6) { d = 1e6; u = lang === "de" ? " Mio." : " M"; }
    else if (a >= 1e4) { d = 1e3; u = lang === "de" ? " Tsd." : " K"; }
    else return fmtInt(n);
    return (n / d).toLocaleString(locale(), { maximumFractionDigits: 1 }) + u;
  }
  function fmtTime(ts) {
    var d = ts ? new Date(ts * 1000) : new Date();
    return d.toLocaleTimeString(locale(), { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  }

  /* ── API base (same resolution as the other pages) ────────────────────── */
  var apiParam = params.get("api");
  var servedOverHttp = location.protocol.indexOf("http") === 0;
  var apiBase = apiParam !== null ? apiParam.replace(/\/$/, "") : (servedOverHttp ? "" : null);
  function apiUrl(p) { return (apiBase || "") + p; }
  var EXPLORER = "https://explorer.qubic.org/network";

  /* ── State ────────────────────────────────────────────────────────────── */
  var S = {
    lastTick: 0,
    seen: {}, seenList: [],
    byTick: {}, byTickList: [],
    arrivals: [],                // [perfNow, tickTs] of recent ticks, for the rate
    totals: { ticks: 0, tx: 0, sol: 0, burn: 0 },
    solWindow: [],               // [perfNow, solutions] for solutions/min
    txWindow: [],                // tx counts of the last ticks
    status: null, feed: "connecting",
    paused: false,
    pulse: null,
    // A contract called in most ticks (Random's "Reveal and Commit", today) is
    // routine, not news: it would colour every cube and drown the rare call.
    contractSeen: {}, contractTicks: 0,
    // Transfer sizes seen, to call the top 1 % "large" rather than a fixed number.
    amounts: []
  };

  function remember(map, list, key, val, max) {
    if (!(key in map)) { list.push(key); if (list.length > max) delete map[list.shift()]; }
    map[key] = val;
  }

  /* Until a dozen ticks have been seen there is no telling routine from rare,
     and colouring everything as "contract call" is the worse mistake. */
  function isRoutine(idx) {
    return S.contractTicks < 12 || (S.contractSeen[idx] || 0) / S.contractTicks > 0.6;
  }
  function bigThreshold() {
    if (S.amounts.length < 50) return 1e9;
    var a = S.amounts.slice().sort(function (x, y) { return x - y; });
    return Math.max(1e8, a[Math.floor(a.length * 0.99)]);
  }

  /* Everything the scene, the terminal and the text view need to know about a
     tick, derived once when it arrives. */
  function classify(s) {
    var big = 0, notable = [];
    var thr = bigThreshold();
    (s.top || []).forEach(function (tx) {
      if (tx.kind === "transfer" && tx.qu >= thr) big = Math.max(big, tx.qu);
    });
    (s.contracts || []).forEach(function (c) { if (!isRoutine(c.index)) notable.push(c); });
    var main = s.state === "skipped" ? "skip" : s.state === "empty" ? "empty"
      : s.burned > 0 ? "burn" : big ? "big" : notable.length ? "contract" : "tick";
    return { main: main, big: big, contracts: notable };
  }

  /* ── Feed: SSE with a polling fallback ────────────────────────────────── */
  var Feed = {
    es: null, pollTimer: null, watchdog: null, lastMsg: 0, kind: null, forcePoll: false,
    start: function () {
      this.stop();
      if (apiBase === null) { showNotice(t("noApi")); setSource("off"); return; }
      var preferPoll = this.forcePoll || !window.EventSource;
      if (preferPoll) this.poll(); else this.sse();
    },
    stop: function () {
      if (this.es) { this.es.close(); this.es = null; }
      clearTimeout(this.pollTimer); this.pollTimer = null;
      clearInterval(this.watchdog); this.watchdog = null;
    },
    sse: function () {
      var self = this;
      this.kind = "sse";
      this.lastMsg = Date.now();
      var url = apiUrl("/v1/ticks/stream" + (S.lastTick ? "?after=" + S.lastTick : ""));
      var es = new EventSource(url);
      this.es = es;
      es.addEventListener("tick", function (e) {
        self.lastMsg = Date.now();
        try { onTick(JSON.parse(e.data)); } catch (err) {}
      });
      es.addEventListener("status", function (e) {
        self.lastMsg = Date.now();
        try { onStatus(JSON.parse(e.data)); } catch (err) {}
      });
      /* EventSource reconnects on its own after an error and resumes with
         Last-Event-ID; the watchdog decides when that is not good enough. */
      this.watchdog = setInterval(function () {
        if (Date.now() - self.lastMsg > 12000) {
          self.forcePoll = true;    // for this page view; a reload tries the stream again
          self.start();
        }
      }, 2000);
    },
    poll: function () {
      var self = this;
      this.kind = "poll";
      var interval = 1000;
      function round() {
        var q = "/v1/ticks/recent?limit=" + (S.lastTick ? 60 : 12) + (S.lastTick ? "&after=" + S.lastTick : "");
        fetch(apiUrl(q), { headers: { accept: "application/json" }, cache: "no-store" })
          .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
          .then(function (d) {
            (d.ticks || []).forEach(onTick);
            onStatus(d.status);
          })
          .catch(function () { setSource("off"); })
          .then(function () { if (self.kind === "poll") self.pollTimer = setTimeout(round, interval); });
      }
      round();
    }
  };

  function onStatus(st) {
    S.status = st;
    if (!st) return setSource("off");
    if (st.stale && S.totals.ticks) return setSource("stale");
    if (S.totals.ticks) setSource(Feed.kind === "sse" ? "stream" : "poll");
  }

  function onTick(s) {
    if (!s || !s.tick || S.seen[s.tick]) return;
    remember(S.seen, S.seenList, s.tick, 1, 2000);
    var isNew = s.tick > S.lastTick;
    if (isNew) S.lastTick = s.tick;

    S.contractTicks++;
    (s.contracts || []).forEach(function (c) { S.contractSeen[c.index] = (S.contractSeen[c.index] || 0) + 1; });
    if (S.contractTicks > 400) {     // decay, so "routine" follows the present
      S.contractTicks /= 2;
      for (var k in S.contractSeen) S.contractSeen[k] /= 2;
    }
    (s.top || []).forEach(function (tx) {
      if (tx.kind === "transfer" && tx.qu > 0) { S.amounts.push(tx.qu); if (S.amounts.length > 600) S.amounts.shift(); }
    });

    s._c = classify(s);
    remember(S.byTick, S.byTickList, s.tick, s, 400);
    var p = now();
    S.totals.ticks++; S.totals.tx += s.tx || 0; S.totals.sol += s.solutions || 0; S.totals.burn += s.burned || 0;
    S.arrivals.push([p, s.ts]); if (S.arrivals.length > 120) S.arrivals.shift();
    S.solWindow.push([p, s.solutions || 0]);
    while (S.solWindow.length && p - S.solWindow[0][0] > 60000) S.solWindow.shift();
    S.txWindow.push(s.tx || 0); if (S.txWindow.length > 100) S.txWindow.shift();

    if (S.feed === "connecting" || S.feed === "off") setSource(Feed.kind === "sse" ? "stream" : "poll");
    hideNotice();
    Term.push(s);
    if (isNew) Hud.tick(s);
    if (Scene.on && !S.paused) Scene.enqueue(s);
  }

  /* Ticks per second from the ticks' own timestamps where they have them — the
     arrival times bunch up whenever the stream delivers a backlog. */
  function tickRate() {
    var a = S.arrivals.filter(function (x) { return x[1]; });
    if (!a.length) return null;
    // Only the last 90 s: a backlog that spans a gap would read as a crawl.
    var newest = a[a.length - 1][1];
    a = a.filter(function (x) { return x[1] >= newest - 90; });
    if (a.length < 5) return null;
    var first = a[Math.max(0, a.length - 60)], last = a[a.length - 1];
    var span = last[1] - first[1];
    var n = a.length - Math.max(0, a.length - 60) - 1;
    return span > 0 ? n / span : null;
  }

  /* ── HUD ──────────────────────────────────────────────────────────────── */
  function setSource(kind) {
    if (S.paused) kind = "paused";
    S.feed = kind;
    var dot = $("hud-dot"), txt = $("hud-src");
    var map = { connecting: ["", "srcConnecting"], stream: ["live", "srcStream"], poll: ["poll", "srcPoll"],
      stale: ["stale", "srcStale"], off: ["off", "srcOff"], paused: ["stale", "srcPaused"] };
    var m = map[kind] || map.connecting;
    dot.className = "dot " + m[0];
    txt.textContent = t(m[1]);
  }

  var Hud = {
    tick: function (s) {
      var big = $("hud-tick");
      big.textContent = "Tick " + fmtInt(s.tick);
      big.classList.remove("flash"); void big.offsetWidth; big.classList.add("flash");
      if (s.epoch) $("hud-epoch").textContent = t("epoch", { n: s.epoch });
      var r = tickRate();
      $("hud-rate").textContent = r ? t("rate", { n: r.toLocaleString(locale(), { maximumFractionDigits: 1 }) }) : "–";
      $("hud-sess").textContent = t("sess", { ticks: fmtInt(S.totals.ticks), tx: fmtInt(S.totals.tx),
        sol: fmtInt(S.totals.sol), burn: fmtQu(S.totals.burn) });
    }
  };

  function showNotice(msg) { var n = $("notice"); n.textContent = msg; n.hidden = false; }
  function hideNotice() { var n = $("notice"); if (!n.hidden) n.hidden = true; }

  /* ── Terminal ─────────────────────────────────────────────────────────── */
  var LS_TERM = "qdr.ticks.term";
  var LS_FILTER = "qdr.ticks.filters";
  var Term = {
    // At most this many lines in the DOM: the oldest are removed as new ones
    // arrive, so the page stays as fast after a day as after a minute.
    el: null, list: null, pending: [], max: 250, scheduled: false, open: true,
    init: function () {
      this.el = $("term"); this.list = $("term-list");
      var saved = safeGet(LS_TERM);
      this.setOpen(saved === null ? window.innerWidth >= 900 : saved === "1", false);
      var self = this;
      $("term-btn").addEventListener("click", function () { self.setOpen(!self.open, true); });
      $("term-x").addEventListener("click", function () { self.setOpen(false, true); });
      var f = {};
      try { f = JSON.parse(safeGet(LS_FILTER) || "{}") || {}; } catch (e) { f = {}; }
      document.querySelectorAll("#term-filters .chip").forEach(function (b) {
        var k = b.dataset.k;
        if (k in f) b.setAttribute("aria-pressed", f[k] ? "true" : "false");
        self.list.classList.toggle("hide-" + k, b.getAttribute("aria-pressed") !== "true");
        b.addEventListener("click", function () {
          var on = b.getAttribute("aria-pressed") !== "true";
          b.setAttribute("aria-pressed", on ? "true" : "false");
          self.list.classList.toggle("hide-" + k, !on);
          f[k] = on; safeSet(LS_FILTER, JSON.stringify(f));
        });
      });
      this.list.addEventListener("click", function (e) {
        var row = e.target.closest ? e.target.closest(".tl") : null;
        if (row && row.dataset.tick) Detail.open(+row.dataset.tick);
      });
    },
    setOpen: function (on, persist) {
      this.open = on;
      this.el.hidden = !on;
      $("term-btn").setAttribute("aria-pressed", on ? "true" : "false");
      if (persist) safeSet(LS_TERM, on ? "1" : "0");
    },
    line: function (cls, tick, html) {
      var d = document.createElement("div");
      d.className = "tl " + cls;
      d.dataset.tick = tick;
      d.innerHTML = html;
      return d;
    },
    push: function (s) {
      var c = s._c, out = [];
      var tm = '<span class="tm">' + esc(fmtTime(s.ts)) + "</span> ";
      var head = tm + '<span class="tn">#' + esc(fmtInt(s.tick)) + "</span> ";
      var bits;
      if (s.state === "skipped") bits = t("skipped");
      else if (s.state === "empty") bits = t("empty");
      else {
        bits = fmtInt(s.tx) + " " + t("tx");
        if (s.solutions) bits += " · ⛏" + s.solutions;
        if (c.contracts.length) bits += " · " + c.contracts.map(function (x) { return x.name || "#" + x.index; }).join(", ");
        if (s.burned) bits += " · 🔥 " + fmtQu(s.burned);
      }
      bits += " · L" + (s.leader && s.leader.index != null ? s.leader.index : "?");
      if (s.catch_up) bits += " · " + t("catchUp");
      out.push(this.line("k-tick s-" + s.state, s.tick, head + esc(bits)));
      if (s.burned) out.push(this.line("sub k-burn", s.tick, "🔥 " + esc(fmtQu(s.burned)) + " QU " + esc(t("burned"))));
      (s.top || []).forEach(function (tx) {
        if (tx.kind === "transfer" && tx.qu > 0) {
          out.push(Term.line("sub k-tx", s.tick, esc(fmtQu(tx.qu)) + " QU " + esc(tx.from) + " → " + esc(tx.to)));
        } else if (tx.kind === "contract" && tx.contract && !isRoutine(tx.contract.index)) {
          out.push(Term.line("sub k-contract", s.tick, esc(tx.contract.name || "#" + tx.contract.index) +
            (tx.contract.proc ? " · " + esc(tx.contract.proc) : "") + (tx.qu ? " · " + esc(fmtQu(tx.qu)) + " QU" : "")));
        }
      });
      // Newest on top: queued in reverse, so the tick line sits above its sub-lines.
      for (var i = out.length - 1; i >= 0; i--) this.pending.push(out[i]);
      this.schedule();
    },
    schedule: function () {
      if (this.scheduled) return;
      this.scheduled = true;
      var self = this;
      requestAnimationFrame(function () { self.flush(); });
    },
    flush: function () {
      this.scheduled = false;
      if (!this.pending.length) return;
      var empty = $("term-empty");
      if (empty) empty.remove();
      var frag = document.createDocumentFragment();
      for (var i = this.pending.length - 1; i >= 0; i--) frag.appendChild(this.pending[i]);
      this.pending.length = 0;
      this.list.insertBefore(frag, this.list.firstChild);
      while (this.list.childNodes.length > this.max) this.list.removeChild(this.list.lastChild);
    }
  };

  /* ── Scene: stars, cubes ─────────────────────────────────────────────── */
  var COLORS = {
    tick: "#4ee0fc", empty: "#93a3bb", skip: "#ff5d6c", burn: "#ff9b3d",
    sol: "#3ddc97", contract: "#a395ff", big: "#ffd166", ink: "#e8f6ff"
  };
  var Z_FAR = 10, Z_NEAR = 1.3;
  // Cubes start twice as far back as the stars' horizon, so a tick first shows
  // up at half the size it used to: a speck in the distance that grows.
  var CUBE_FAR = 20;
  // One knob for how big every cube is (05.10.2026: +10 %).
  var CUBE_SCALE = 1.1;
  // How much of its own depth a cube shows. At 1 a cube close to the camera has
  // a front face far larger than its back and stretches into a tunnel at the
  // screen edge; flattened like this it reads as a cube to the end, while the
  // flight itself stays in full perspective.
  var CUBE_DEPTH = 0.3;
  // Where a cube has faded out: 90 % of the way in at the screen's edge, the
  // full 100 % in the middle. An edge cube is leaving the picture anyway; a
  // central one would vanish in plain view, so it flies all the way in.
  var Z_GONE = Z_NEAR + 0.1 * (CUBE_FAR - Z_NEAR);
  var Z_GONE_CENTRE = Z_NEAR;
  var FADE_DEPTH = 0.08 * (CUBE_FAR - Z_NEAR);
  // The stars fly a little faster than the cubes (20 %), not past them: the
  // cubes are what the page is about, the stars only say "moving".
  var STAR_VS_CUBE = 1.2;
  // Wormhole layout: angle between consecutive cubes, how fast the whole thing
  // turns (rad/s), and the tube's radius as a share of the world's half-width.
  var WORM_STEP = 0.62, WORM_SPIN = 0.22, WORM_RADIUS = 0.5;
  var CUBE_EDGES = [0, 1, 1, 2, 2, 3, 3, 0, 4, 5, 5, 6, 6, 7, 7, 4, 0, 4, 1, 5, 2, 6, 3, 7];
  var CUBE_V = [-1, -1, -1, 1, -1, -1, 1, 1, -1, -1, 1, -1, -1, -1, 1, 1, -1, 1, 1, 1, 1, -1, 1, 1];

  var Scene = {
    on: false, canvas: null, ctx: null, w: 0, h: 0, dpr: 1, F: 1, spread: 1,
    starN: 0, sx: null, sy: null, sz: null,
    cubes: [], queue: [], lastSpawn: 0, last: 0, raf: 0,
    speed: 1, speedTarget: 1, quality: 1, ema: 16, slowFor: 0,
    // 15 s over twice the depth: the same closing speed up front as before.
    flight: 15, maxCubes: 48, maxQueue: 6, stats: false,
    // "free": each cube placed where it never meets another. "worm": every cube
    // takes the next place on a spiral that turns as it comes at the viewer,
    // stars included, so one looks down a wormhole.
    layout: "free", spiralAng: 0,
    init: function () {
      this.on = true;
      this.canvas = $("sky");
      this.ctx = this.canvas.getContext("2d", { alpha: false });
      var self = this;
      /* Stats-Flug vorerst abgeschaltet (Wunsch 05.10.2026: die Kennzahlen sollen
         nicht wie im alten Projekt nach vorn fliegen). Der Code bleibt, damit eine
         andere Darstellung darauf aufsetzen kann.
      var savedStats = safeGet("qdr.ticks.stats");
      if (savedStats !== null) this.stats = savedStats === "1";
      $("stats-btn").setAttribute("aria-pressed", this.stats ? "true" : "false");
      $("stats-btn").addEventListener("click", function () {
        self.stats = !self.stats;
        $("stats-btn").setAttribute("aria-pressed", self.stats ? "true" : "false");
        safeSet("qdr.ticks.stats", self.stats ? "1" : "0");
      });
      */
      this.resize();
      var rt = null;
      window.addEventListener("resize", function () {
        clearTimeout(rt); rt = setTimeout(function () { self.resize(); }, 150);
      }, { passive: true });
      this.canvas.addEventListener("click", function (e) { self.click(e); });
      this.canvas.addEventListener("mousemove", function (e) { self.hover(e); }, { passive: true });
      this.start();
    },
    resize: function () {
      var r = this.canvas.getBoundingClientRect();
      // Capped at 2: a 3x phone would paint 2.25x the pixels for no visible gain.
      this.dpr = Math.min(window.devicePixelRatio || 1, 2);
      this.w = Math.max(1, Math.round(r.width)); this.h = Math.max(1, Math.round(r.height));
      this.canvas.width = Math.round(this.w * this.dpr);
      this.canvas.height = Math.round(this.h * this.dpr);
      this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
      this.F = Math.min(this.w, this.h) * 0.75;
      // World half-width that reaches the screen edge at z = 4.
      this.spread = (Math.max(this.w, this.h) / 2) / this.F * 4;
      this.buildStars();
    },
    buildStars: function () {
      var area = this.w * this.h;
      // The original drew 1000 on any screen; this scales to the area and caps
      // there, so a phone gets a dense sky without paying for a monitor's.
      var n = Math.round(area / 1300);
      n = Math.min(Math.max(n, 300), 1000);
      n = Math.round(n * this.quality);
      this.starN = n;
      this.sx = new Float32Array(n); this.sy = new Float32Array(n); this.sz = new Float32Array(n);
      for (var i = 0; i < n; i++) this.respawnStar(i, Math.random() * Z_FAR);
    },
    respawnStar: function (i, z) {
      this.sx[i] = (Math.random() * 2 - 1) * this.spread * 2.2;
      this.sy[i] = (Math.random() * 2 - 1) * this.spread * 2.2;
      this.sz[i] = z;
    },
    start: function () {
      if (this.raf) return;
      var self = this;
      this.last = 0;
      this.raf = requestAnimationFrame(function f(ts) { self.frame(ts); self.raf = requestAnimationFrame(f); });
    },
    stop: function () { if (this.raf) cancelAnimationFrame(this.raf); this.raf = 0; },

    enqueue: function (s) {
      this.queue.push(s);
      // Behind (a backlog after reconnecting, a hidden tab): only the newest fly.
      // The terminal has them all.
      if (this.queue.length > this.maxQueue) this.queue.splice(0, this.queue.length - this.maxQueue);
    },
    /* How far a cube placed at (x, y) would stay from every cube in flight, over
       the whole of its flight: < 1 means that at some moment the two would
       overlap on screen. Checking only the moment of spawning is not enough —
       a near cube grows and drifts outward, and a far one launched later can
       run into it. Every cube closes at the same speed, so each pair can be
       stepped forward together. */
    /* The depth at which a cube at (x, y) has faded out: closer for one flying
       down the middle, the full Z_GONE for one heading for the edge. */
    goneAt: function (x, y) {
      var r = Math.sqrt(x * x + y * y) / (this.spread || 1);
      var k = Math.min(1, Math.max(0, (r - 0.25) / 0.45));     // 0 centre .. 1 edge
      return Z_GONE_CENTRE + (Z_GONE - Z_GONE_CENTRE) * k;
    },
    clearance: function (x, y, size) {
      var worst = Infinity, gb = this.goneAt(x, y);
      for (var i = 0; i < this.cubes.length; i++) {
        var a = this.cubes[i];
        // Stepped in proportion to the distance: on screen a cube moves as 1/z,
        // so near the camera a fixed step would jump right past a collision.
        for (var dz = 0; ; ) {
          if (a.z - dz <= a.gone || CUBE_FAR - dz <= gb) break;     // faded out by then
          var za = a.z - dz - a.size * CUBE_DEPTH, zb = CUBE_FAR - dz - size * CUBE_DEPTH;   // front faces
          dz += Math.max(0.02, 0.06 * Math.min(za, zb));
          var fa = 1 / za, fb = 1 / zb;
          // Half-widths plus a gap, so neighbours never touch either.
          var r = (a.size * fa + size * fb) * 1.15;
          var m = Math.max(Math.abs(a.x * fa - x * fb), Math.abs(a.y * fa - y * fb)) / r;
          if (m < worst) worst = m;
          if (worst < 1) return worst;
        }
      }
      return worst;
    },
    /* Somewhere free for a new cube, or null when there is none right now. */
    place: function (size) {
      var best = null, bestM = -1;
      // Uniform over the screen's shape rather than around a circle: the corners
      // are room too. sqrt() spreads the radius evenly over the area.
      var aspect = this.h / this.w;
      for (var n = 0; n < 120; n++) {
        var ang = Math.random() * Math.PI * 2;
        var rad = Math.sqrt(0.02 + Math.random() * 0.98) * this.spread * 1.1;
        var x = Math.cos(ang) * rad, y = Math.sin(ang) * rad * Math.max(0.6, Math.min(1.4, aspect * 1.2));
        var m = this.clearance(x, y, size);
        if (m >= 1) return { x: x, y: y };
        if (m > bestM) { bestM = m; best = { x: x, y: y }; }
      }
      return null;
    },
    /* A point at angle `ang` and radius `rad` around the line of flight. A
       circle, not squeezed to the screen: then turning everything is a rigid
       rotation, under which nothing that was apart can come to overlap. */
    wormPos: function (ang, rad) {
      return { x: Math.cos(ang) * rad, y: Math.sin(ang) * rad };
    },
    /* Switch layout. Into the wormhole every cube stays where it is and from
       then on everything turns together; new cubes take their places on the
       spiral. Back to free, the cubes stop turning where they are. Nothing
       jumps, and nothing passes through anything. */
    setLayout: function (l) {
      this.layout = l;
      if (l === "worm") {
        var far = null;
        this.cubes.forEach(function (c) {
          c.ang = Math.atan2(c.y, c.x); c.rad = Math.sqrt(c.x * c.x + c.y * c.y);
          c.tx = c.x; c.ty = c.y;
          if (!far || c.z > far.z) far = c;
        });
        this.spiralAng = far ? far.ang + WORM_STEP : 0;
      } else {
        this.cubes.forEach(function (c) { c.tx = c.x; c.ty = c.y; });
      }
    },
    spawn: function (s) {
      var c = s._c || classify(s);
      var size = s.state === "ok" ? Math.min(0.34, 0.14 + 0.05 * Math.log2(1 + (s.tx || 0))) : 0.11;
      size *= CUBE_SCALE;
      var pos, ang = 0, rad = 0;
      if (this.layout === "worm") {
        // Next place on the spiral: consecutive cubes sit WORM_STEP apart in
        // angle. Checked like a free placement — everything turns together, so
        // in the turning frame the others stand still and the check holds. A
        // place still blocked by a cube from before the switch means wait.
        ang = this.spiralAng; rad = this.spread * WORM_RADIUS;
        pos = this.wormPos(ang, rad);
        if (this.clearance(pos.x, pos.y, size) < 1) return false;
        this.spiralAng += WORM_STEP;
      } else {
        // Never inside another cube: no free spot means wait, not overlap.
        pos = this.place(size);
        if (!pos) return false;
      }
      this.cubes.push({
        ang: ang, rad: rad, tx: pos.x, ty: pos.y,
        s: s, x: pos.x, y: pos.y, z: CUBE_FAR, gone: this.goneAt(pos.x, pos.y),
        // Fest ausgerichtet, wie im Original: die Würfel fliegen, sie drehen sich nicht.
        size: size,
        color: COLORS[c.main] || COLORS.tick, glow: c.main !== "tick" && c.main !== "empty",
        dashed: s.state !== "ok",
        num: this.numberLabel(s), body: this.bodyLabel(s, c),
        px: 0, py: 0, half: 0
      });
      return true;
    },
    /* The text on a cube, rendered ONCE into small canvases and from then on only
       scaled. Drawing text per frame per cube is what made the original stutter.

       Two pieces, both on the front face: the tick number along its bottom
       edge, and the content above it, one item per line. */
    numberLabel: function (s) {
      var W = 300, H = 64, cv = document.createElement("canvas");
      cv.width = W; cv.height = H;
      var g = cv.getContext("2d");
      g.textAlign = "center"; g.textBaseline = "middle";
      g.shadowColor = "rgba(0,0,0,.85)"; g.shadowBlur = 8;
      g.fillStyle = s.state === "skipped" ? COLORS.skip : COLORS.ink;
      g.font = "700 50px 'Space Grotesk', system-ui, sans-serif";
      g.fillText(fmtInt(s.tick), W / 2, H / 2);
      if (s.state === "skipped") g.fillRect(W * 0.12, H / 2, W * 0.76, 4);
      return cv;
    },
    bodyLabel: function (s, c) {
      var lines = [];
      if (s.state === "skipped") lines.push([t("skipped"), COLORS.skip]);
      else if (s.state === "empty") lines.push([t("empty"), COLORS.empty]);
      else {
        lines.push([s.tx + " " + t("tx"), "#b0faff"]);
        if (s.solutions) lines.push(["⛏ " + s.solutions, COLORS.sol]);
        if (s.burned) lines.push(["🔥 " + fmtQu(s.burned), COLORS.burn]);
        if (c.big) lines.push([fmtQu(c.big) + " QU", COLORS.big]);
        c.contracts.slice(0, 2).forEach(function (k) { lines.push([k.name || "#" + k.index, COLORS.contract]); });
      }
      var W = 220, H = 220, cv = document.createElement("canvas");
      cv.width = W; cv.height = H;
      var g = cv.getContext("2d");
      g.textAlign = "center"; g.textBaseline = "middle";
      g.shadowColor = "rgba(0,0,0,.9)"; g.shadowBlur = 6;
      g.font = "600 34px 'Space Mono', ui-monospace, monospace";
      var lh = Math.min(46, (H - 16) / lines.length);
      var y = H / 2 - (lines.length - 1) * lh / 2;
      lines.forEach(function (l) {
        var wid = g.measureText(l[0]).width, k = wid > W - 20 ? (W - 20) / wid : 1;
        g.save(); g.translate(W / 2, y); g.scale(k, k);
        g.fillStyle = l[1]; g.fillText(l[0], 0, 0);
        g.restore();
        y += lh;
      });
      return cv;
    },

    frame: function (ts) {
      if (!this.last) this.last = ts;
      var dtMs = ts - this.last; this.last = ts;
      var dt = Math.min(dtMs, 50) / 1000;
      this.perf(dtMs);
      this.speed += (this.speedTarget * (S.paused ? 0 : 1) - this.speed) * Math.min(1, dt * 4);
      var v = this.speed;

      // Spawn: no faster than the network's own pace, so a backlog does not
      // arrive as a wall of cubes, and only when there is room: a new cube must
      // never cut short one still in flight. What waits is trimmed to the newest.
      if (this.queue.length && this.cubes.length < this.maxCubes &&
          ts - this.lastSpawn > (this.queue.length > 2 ? 180 : 320) && v > 0.3) {
        // No free spot: the tick stays queued and is tried again shortly.
        if (this.spawn(this.queue[0])) this.queue.shift();
        this.lastSpawn = ts;
      }

      var ctx = this.ctx, w = this.w, h = this.h, cx = w / 2, cy = h / 2, F = this.F;
      ctx.fillStyle = "#05070d";
      ctx.fillRect(0, 0, w, h);

      // Stars. Like the original Star Rain: white, many, fast, and growing as
      // they come closer (size ~ 1/z), so it reads as flying through space.
      // No streaks: a star gets bigger, it does not smear. Three brightness
      // bands, one path each; the near band is drawn round, the far ones as
      // squares, which at one or two pixels look the same and cost less.
      var sv = (CUBE_FAR - Z_NEAR) / this.flight * STAR_VS_CUBE * v * dt;
      var n = this.starN, sx = this.sx, sy = this.sy, sz = this.sz;
      var dots = [[], [], []];
      // In the wormhole the sky turns with the spiral.
      var worm = this.layout === "worm";
      var rc = worm ? Math.cos(WORM_SPIN * v * dt) : 1, rs = worm ? Math.sin(WORM_SPIN * v * dt) : 0;
      for (var i = 0; i < n; i++) {
        if (worm) { var ox = sx[i]; sx[i] = ox * rc - sy[i] * rs; sy[i] = ox * rs + sy[i] * rc; }
        var z = sz[i] - sv;
        if (z < 0.12) { this.respawnStar(i, Z_FAR); z = Z_FAR; }
        sz[i] = z;
        var inv = F / z;
        var px = cx + sx[i] * inv, py = cy + sy[i] * inv;
        if (px < -8 || px > w + 8 || py < -8 || py > h + 8) continue;
        // Diameter in px: ~1 far away, up to 7 right in front.
        dots[z < 2.5 ? 2 : z < 6 ? 1 : 0].push(px, py, Math.min(7, 0.5 + 8 / z));
      }
      var fills = ["rgba(200,235,255,0.6)", "rgba(235,248,255,0.88)", "#ffffff"];
      for (var b = 0; b < 3; b++) {
        var arr = dots[b]; if (!arr.length) continue;
        ctx.fillStyle = fills[b];
        ctx.beginPath();
        for (var j = 0; j < arr.length; j += 3) {
          var d = arr[j + 2];
          if (b === 2) { ctx.moveTo(arr[j] + d / 2, arr[j + 1]); ctx.arc(arr[j], arr[j + 1], d / 2, 0, 6.2832); }
          else ctx.rect(arr[j] - d / 2, arr[j + 1] - d / 2, d, d);
        }
        ctx.fill();
      }

      // Stats-Flug vorerst aus (siehe Scene.init):
      // if (this.stats) Stats.draw(ctx, cx, cy, dt * v, ts);

      // Cubes, far to near, so near ones paint over far ones.
      var cv = (CUBE_FAR - Z_NEAR) / this.flight * v * dt;
      var alive = [];
      for (var k = 0; k < this.cubes.length; k++) {
        var c = this.cubes[k];
        c.z -= cv;
        if (worm) {
          c.ang += WORM_SPIN * v * dt;
          var wp = this.wormPos(c.ang, c.rad);
          c.x = c.tx = wp.x; c.y = c.ty = wp.y;
        }
        if (c.z > c.gone) alive.push(c);
      }
      this.cubes = alive;
      alive.sort(function (a, b) { return b.z - a.z; });
      for (var m = 0; m < alive.length; m++) this.drawCube(alive[m], cx, cy, F);
    },
    drawCube: function (c, cx, cy, F) {
      var ctx = this.ctx;
      var p = (CUBE_FAR - c.z) / (CUBE_FAR - Z_NEAR);        // 0 far .. 1 near
      var alpha = Math.min(1, p / 0.06) * Math.min(1, (c.z - c.gone) / FADE_DEPTH);
      if (alpha <= 0.01) return;
      var pts = this._pts || (this._pts = new Float32Array(16));
      for (var i = 0; i < 8; i++) {
        var zz = c.z + CUBE_V[i * 3 + 2] * c.size * CUBE_DEPTH; if (zz < 0.05) return;
        pts[i * 2] = cx + (c.x + CUBE_V[i * 3] * c.size) / zz * F;
        pts[i * 2 + 1] = cy + (c.y + CUBE_V[i * 3 + 1] * c.size) / zz * F;
      }
      ctx.beginPath();
      for (var e = 0; e < CUBE_EDGES.length; e += 2) {
        var a = CUBE_EDGES[e] * 2, b = CUBE_EDGES[e + 1] * 2;
        ctx.moveTo(pts[a], pts[a + 1]); ctx.lineTo(pts[b], pts[b + 1]);
      }
      if (c.dashed && ctx.setLineDash) ctx.setLineDash([4, 4]);
      ctx.strokeStyle = c.color;
      if (c.glow && this.quality >= 1) {
        ctx.globalAlpha = alpha * 0.22; ctx.lineWidth = 5; ctx.stroke();
      }
      ctx.globalAlpha = alpha * 0.9; ctx.lineWidth = 1.3; ctx.stroke();
      if (c.dashed && ctx.setLineDash) ctx.setLineDash([]);

      // The front face: what the number and the content are written on. The cube
      // is not rotated, so the face projects directly.
      var zf = c.z - c.size * CUBE_DEPTH;
      var half = c.size / zf * F;
      var pcx = cx + c.x / zf * F, pcy = cy + c.y / zf * F;
      c.px = pcx; c.py = pcy; c.half = half;

      // Gone before the cube reaches the camera: text the size of the screen is
      // not more readable, it just covers the next ones.
      var la = alpha;
      if (la > 0.02) {
        // The tick number along the bottom edge of the front face.
        var lw = half * 2 * 0.88, lh = lw * c.num.height / c.num.width;
        if (lw > 30) {
          ctx.globalAlpha = la;
          ctx.drawImage(c.num, pcx - lw / 2, pcy + half - lh - half * 0.04, lw, lh);
        }
        // The content above it, one item per line, in the space the number
        // leaves: nudged up so the two never overlap.
        var bw = half * 2 * 0.72;
        if (bw > 30) {
          ctx.globalAlpha = la;
          ctx.drawImage(c.body, pcx - bw / 2, pcy - half * 0.14 - bw / 2, bw, bw);
        }
      }
      ctx.globalAlpha = 1;
    },
    pick: function (e) {
      var r = this.canvas.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      var best = null;
      for (var i = 0; i < this.cubes.length; i++) {
        var c = this.cubes[i], hit = Math.max(c.half * 1.4, 16);
        if (Math.abs(x - c.px) < hit && Math.abs(y - c.py) < hit) {
          if (!best || c.z < best.z) best = c;
        }
      }
      return best;
    },
    click: function (e) { var c = this.pick(e); if (c) Detail.open(c.s.tick); },
    hover: function (e) { this.canvas.classList.toggle("hover", !!this.pick(e)); },
    /* Adaptive quality: a smoothed frame time over budget for a while first drops
       the glow and halves the stars; on a small device that still cannot keep up,
       it gives up the animation for the text view. */
    perf: function (dtMs) {
      if (dtMs <= 0 || dtMs > 1000) return;
      this.ema += (dtMs - this.ema) * 0.05;
      if (this.ema > 30) this.slowFor += dtMs; else this.slowFor = 0;
      if (this.slowFor > 2500 && this.quality > 0.5) {
        this.quality = 0.5; this.slowFor = 0; this.buildStars();
      }
    }
  };

  /* ── Stats flight: one figure at a time flies out of the centre ──────────
     Vorerst nicht gezeichnet (siehe Scene.init). Bleibt, weil die Kennzahlen
     eine andere Darstellung bekommen sollen und dafür hier schon gesammelt
     werden. */
  var Stats = {
    items: [], idx: 0, cur: null, z: Z_FAR, period: 7,
    refresh: function () {
      var p = S.pulse, list = [];
      var rate = tickRate();
      if (p) {
        list.push([t("stEpoch"), fmtInt(p.epoch)]);
        list.push([t("stTicksEpoch"), fmtInt(p.ticks_in_epoch)]);
        if (p.epoch_progress != null) list.push([t("stProgress"), (p.epoch_progress * 100).toLocaleString(locale(), { maximumFractionDigits: 1 }) + " %"]);
        if (p.tick_quality != null) list.push([t("stQuality"), p.tick_quality.toLocaleString(locale(), { maximumFractionDigits: 2 }) + " %"]);
        list.push([t("stEmpty"), fmtInt(p.empty_ticks)]);
        list.push([t("stAddr"), fmtInt(p.active_addresses)]);
        list.push([t("stSupply"), fmtQu(p.circulating_supply) + " QU"]);
        list.push([t("stBurned"), fmtQu(p.burned_qus) + " QU"]);
        if (p.price) list.push([t("stPrice"), "$" + p.price.toPrecision(3)]);
        if (p.market_cap) list.push([t("stMcap"), "$" + fmtQu(p.market_cap)]);
      }
      if (rate) list.push([t("stRate"), rate.toLocaleString(locale(), { maximumFractionDigits: 2 })]);
      var sol = S.solWindow.reduce(function (a, x) { return a + x[1]; }, 0);
      if (S.solWindow.length > 20) list.push([t("stSolMin"), fmtInt(sol)]);
      if (S.txWindow.length > 20) list.push([t("stTxTick"), (S.txWindow.reduce(function (a, b) { return a + b; }, 0) / S.txWindow.length).toLocaleString(locale(), { maximumFractionDigits: 1 })]);
      this.items = list;
    },
    draw: function (ctx, cx, cy, dtv) {
      if (!this.cur) {
        if (!this.items.length) { this.refresh(); if (!this.items.length) return; }
        this.cur = this.items[this.idx % this.items.length];
        this.idx++;
        if (this.idx % this.items.length === 0) this.refresh();
        this.z = Z_FAR;
      }
      this.z -= (Z_FAR - 1.4) / this.period * dtv;
      if (this.z <= 1.4) { this.cur = null; return; }
      var p = (Z_FAR - this.z) / (Z_FAR - 1.4);
      var alpha = Math.min(1, p / 0.15) * Math.min(1, (1 - p) / 0.25);
      var k = 3.2 / this.z;
      ctx.textAlign = "center"; ctx.textBaseline = "middle";
      ctx.globalAlpha = alpha * 0.85;
      ctx.fillStyle = "#ffffff";
      ctx.font = "500 " + Math.round(13 * k) + "px 'Space Grotesk', system-ui, sans-serif";
      ctx.fillText(this.cur[0], cx, cy - 22 * k);
      ctx.globalAlpha = alpha;
      ctx.fillStyle = "#61f0fe";
      ctx.font = "700 " + Math.round(30 * k) + "px 'Space Grotesk', system-ui, sans-serif";
      ctx.fillText(this.cur[1], cx, cy + 6 * k);
      ctx.globalAlpha = 1;
    }
  };

  function loadPulse() {
    if (apiBase === null) return;
    fetch(apiUrl("/v1/pulse"), { headers: { accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) { S.pulse = d; Stats.refresh(); } })
      .catch(function () {});
  }

  /* ── Detail panel ─────────────────────────────────────────────────────── */
  var Detail = {
    shown: false, tick: null, req: 0,
    init: function () {
      var self = this;
      $("d-close").addEventListener("click", function () { self.close(); });
      /* A click anywhere outside the panel closes it. Not when that click is
         what (re)opens it: a cube, a terminal line or the search field switch
         the panel to another tick instead. Those handlers run first, on their
         own element; this one sees the click after it has bubbled up. */
      document.addEventListener("click", function (e) {
        if (!self.shown) return;
        var el = e.target;
        if (el.closest && el.closest("#detail, #tick-search, #term-list .tl")) return;
        if (el === Scene.canvas && Scene.pick(e)) return;
        self.close();
      });
    },
    close: function () {
      this.shown = false; this.tick = null;
      $("detail").hidden = true;
      Scene.speedTarget = 1;
    },
    open: function (tick) {
      var self = this, req = ++this.req;
      this.shown = true; this.tick = tick;
      $("detail").hidden = false;
      Scene.speedTarget = 0.18;    // slow the flight, so there is time to read
      var s = S.byTick[tick];
      $("d-title").textContent = "Tick " + fmtInt(tick);
      $("d-sub").textContent = s ? this.subline(s) : "";
      $("d-body").innerHTML = '<div class="d-more">' + esc(t("dLoading")) + "</div>";
      fetch(apiUrl("/v1/ticks/" + tick), { headers: { accept: "application/json" } })
        .then(function (r) {
          if (!r.ok) return r.json().catch(function () { return {}; }).then(function (b) { throw new Error(b.detail || "HTTP " + r.status); });
          return r.json();
        })
        .then(function (d) { if (req === self.req) self.render(d); })
        .catch(function (e) {
          if (req === self.req) $("d-body").innerHTML = '<div class="d-more">' + esc(t("dFailed", { e: e.message })) + "</div>";
        });
    },
    subline: function (s) {
      var parts = [];
      if (s.ts) parts.push(new Date(s.ts * 1000).toLocaleString(locale()));
      if (s.epoch) parts.push(t("epoch", { n: s.epoch }));
      if (s.state !== "ok") parts.push(t(s.state));
      if (s.catch_up) parts.push(t("catchUp"));
      return parts.join(" · ");
    },
    idLink: function (id) {
      return id ? '<a href="' + EXPLORER + "/address/" + encodeURIComponent(id) + '" target="_blank" rel="noreferrer">' + esc(id) + "</a>" : "–";
    },
    shortLink: function (id) {
      if (!id) return "–";
      var short = id.length > 14 ? id.slice(0, 6) + "…" + id.slice(-4) : id;
      return '<a href="' + EXPLORER + "/address/" + encodeURIComponent(id) + '" target="_blank" rel="noreferrer" title="' + esc(id) + '">' + esc(short) + "</a>";
    },
    kindLabel: function (k) {
      return { transfer: "kTransfer", burn: "kBurn", contract: "kContract", solution: "kSolution",
        system: "kSystem", call: "kCall", unknown: "kUnknown" }[k] || "kUnknown";
    },
    render: function (d) {
      $("d-title").textContent = "Tick " + fmtInt(d.tick);
      $("d-sub").textContent = this.subline(d);
      var h = "";
      h += '<div class="d-kpis">' +
        '<div class="d-kpi"><b>' + fmtInt(d.tx) + "</b><span>" + esc(t("dTransactions")) + "</span></div>" +
        '<div class="d-kpi"><b>' + fmtInt(d.solutions) + "</b><span>" + esc(t("solutions")) + "</span></div>" +
        '<div class="d-kpi"><b>' + fmtQu(d.burned) + "</b><span>QU " + esc(t("burned")) + "</span></div>" +
        '<div class="d-kpi"><b>' + fmtQu(d.qu_moved) + "</b><span>QU " + esc(t("moved")) + "</span></div>" +
        '<div class="d-kpi"><b>' + fmtInt(d.logs) + "</b><span>Logs</span></div>" +
        '<div class="d-kpi"><b>' + (d.leader && d.leader.index != null ? "#" + d.leader.index : "–") + "</b><span>" + esc(t("leader")) + "</span></div>" +
        "</div>";

      if (d.state === "skipped") h += '<div class="d-more">' + esc(t("dSkipped")) + "</div>";
      else if (d.state === "empty") h += '<div class="d-more">' + esc(t("dEmpty")) + "</div>";

      if (d.leader && d.leader.id) {
        h += '<div class="d-sec">' + esc(t("dLeader")) + "</div>";
        h += '<div class="d-leader">#' + d.leader.index + " · " + this.idLink(d.leader.id) + "<br>" +
          esc(d.leader.cluster ? t("dCluster", { c: d.leader.cluster }) : t("dUnattributed")) + "</div>";
      }

      var txs = d.transactions || [];
      var shown = txs.filter(function (x) { return x.kind !== "solution" && x.kind !== "system"; });
      var hidden = txs.length - shown.length;
      if (txs.length) {
        h += '<div class="d-sec">' + esc(t("dTransactions")) + " · " + txs.length + "</div>";
        if (d.solutions) h += '<div class="d-more">⛏ ' + esc(t("dSolNote", { n: fmtInt(d.solutions) })) + "</div>";
        var self = this;
        shown.slice(0, 60).forEach(function (x) {
          var kind = x.ok === false ? "failed" : x.kind;
          var what = x.contract ? esc(x.contract.name || "#" + x.contract.index) + (x.contract.proc ? " · " + esc(x.contract.proc) : "") : "";
          var amt = x.burned ? fmtQu(x.burned) + " QU 🔥" : (x.qu ? fmtQu(x.qu) + " QU" : "");
          h += '<div class="d-tx"><div class="row1"><span class="kind ' + kind + '">' +
            esc(t(x.ok === false ? "kFailed" : self.kindLabel(x.kind))) + "</span>" +
            (what ? "<span>" + what + "</span>" : "") +
            '<span class="amt">' + esc(amt) + "</span></div>" +
            '<div class="ids">' + self.shortLink(x.from) + " → " + self.shortLink(x.to) +
            (x.h ? ' · <a href="' + EXPLORER + "/tx/" + encodeURIComponent(x.h) + '" target="_blank" rel="noreferrer">' + esc(t("dExplorer")) + "</a>" : "") +
            "</div></div>";
        });
        if (shown.length > 60) h += '<div class="d-more">' + esc(t("dMore", { n: shown.length - 60 })) + "</div>";
        if (hidden) h += '<div class="d-more">' + esc(t("dMore", { n: hidden })) + " · " + esc(t("kSolution")) + " / " + esc(t("kSystem")) + "</div>";
      } else if (d.state === "ok") {
        h += '<div class="d-more">' + esc(t("dNoTx")) + "</div>";
      }

      var ev = (d.events || []).filter(function (e) { return e.type !== "QU_TRANSFER"; });
      if (ev.length) {
        h += '<details class="d-events"><summary>' + esc(t("dEvents")) + " · " + ev.length + "</summary>";
        ev.slice(0, 80).forEach(function (e) {
          var rest = Object.keys(e).filter(function (k) { return k !== "type" && k !== "tx"; })
            .map(function (k) { return k + "=" + (typeof e[k] === "number" ? fmtInt(e[k]) : e[k]); }).join(" · ");
          h += '<div class="d-ev">' + esc(e.type) + (rest ? " · " + esc(rest) : "") + "</div>";
        });
        h += "</details>";
      }
      h += '<div class="d-note">' + esc(t("dNote")) + ' <a href="' + EXPLORER + "/tick/" + d.tick + '" target="_blank" rel="noreferrer" style="color:var(--c-tick)">' + esc(t("dExplorer")) + "</a></div>";
      $("d-body").innerHTML = h;
    }
  };

  /* ── Wiring ───────────────────────────────────────────────────────────── */
  function applyLang() {
    root.setAttribute("lang", lang);
    document.querySelectorAll("[data-i18n]").forEach(function (el) { el.textContent = t(el.getAttribute("data-i18n")); });
    document.querySelectorAll("[data-i18n-ph]").forEach(function (el) {
      el.placeholder = t(el.getAttribute("data-i18n-ph")); el.setAttribute("aria-label", t(el.getAttribute("data-i18n-ph")).replace(/\s+\/$/, ""));
    });
    document.querySelectorAll("[data-i18n-aria]").forEach(function (el) { el.setAttribute("aria-label", t(el.getAttribute("data-i18n-aria"))); });
    document.querySelectorAll("#lang-seg button").forEach(function (b) { b.classList.toggle("active", b.dataset.lang === lang); });
    setSource(S.feed);
    Stats.refresh();
  }
  document.querySelectorAll("#lang-seg button").forEach(function (b) {
    b.addEventListener("click", function () { lang = b.dataset.lang; safeSet(LS_LANG, lang); applyLang(); });
  });

  var LS_THEME = "qdr.theme";
  var theme = safeGet(LS_THEME) || "dark";
  function applyTheme(th) {
    root.setAttribute("data-theme", th);
    $("theme-btn").textContent = th === "dark" ? "🌙" : "☀️";
  }
  applyTheme(theme);
  $("theme-btn").addEventListener("click", function () {
    theme = theme === "dark" ? "light" : "dark"; safeSet(LS_THEME, theme); applyTheme(theme);
  });

  (function initNav() {
    var toggle = $("nav-toggle"), nav = $("site-nav");
    if (!toggle || !nav) return;
    function setOpen(open) { nav.classList.toggle("open", open); toggle.setAttribute("aria-expanded", open ? "true" : "false"); }
    toggle.addEventListener("click", function (e) { e.stopPropagation(); setOpen(!nav.classList.contains("open")); });
    document.addEventListener("click", function (e) { if (nav.classList.contains("open") && !nav.contains(e.target)) setOpen(false); });
    window.addEventListener("resize", function () { if (window.innerWidth > 860) setOpen(false); }, { passive: true });
  })();

  function fitStage() {
    var hdr = $("hdr");
    if (hdr) root.style.setProperty("--hdr", hdr.offsetHeight + "px");
  }

  function setPaused(p) {
    S.paused = p;
    $("pause-btn").setAttribute("aria-pressed", p ? "true" : "false");
    $("pause-btn").textContent = p ? "▶" : "⏸";
    if (p) setSource("paused"); else { S.feed = "connecting"; onStatus(S.status); }
  }

  function init() {
    $("foot-year").textContent = new Date().getFullYear();
    fitStage();
    window.addEventListener("resize", fitStage, { passive: true });
    applyLang();

    var lay = $("layout-sel");
    var savedLayout = safeGet("qdr.ticks.layout") === "worm" ? "worm" : "free";
    lay.value = savedLayout;
    if (Scene.on) Scene.setLayout(savedLayout);
    lay.addEventListener("change", function () {
      safeSet("qdr.ticks.layout", lay.value);
      Scene.setLayout(lay.value);
    });

    $("pause-btn").addEventListener("click", function () { setPaused(!S.paused); });

    /* Tick search. Takes the number however it was copied — "83.205.230",
       "83,205,230" or "#83205230" — and opens the same panel a click on a cube
       opens. Older ticks come from the node, inside the running epoch. */
    var q = $("tick-q");
    $("tick-search").addEventListener("submit", function (e) {
      e.preventDefault();
      var digits = (q.value || "").replace(/\D/g, "");
      var tick = digits ? parseInt(digits, 10) : 0;
      q.classList.toggle("bad", !(tick > 0));
      if (!(tick > 0)) return;
      q.blur();
      Detail.open(tick);
    });
    q.addEventListener("input", function () { q.classList.remove("bad"); });

    Term.init(); Scene.init(); Detail.init();
    // ?debug: the scene on window, for checking placement from a browser console.
    if (params.has("debug")) { window.__ticksScene = Scene; window.__ticksFeed = onTick; }

    document.addEventListener("keydown", function (e) {
      if (e.target && /INPUT|SELECT|TEXTAREA/.test(e.target.tagName)) return;
      if (e.key === "Escape") { if (Detail.shown) Detail.close(); }
      else if (e.key === "/") { e.preventDefault(); $("tick-q").focus(); }
      else if (e.key === "t" || e.key === "T") Term.setOpen(!Term.open, true);
      // Stats-Flug vorerst aus: else if (e.key === "s" || e.key === "S") $("stats-btn").click();
      else if (e.key === " " && e.target === document.body) { e.preventDefault(); setPaused(!S.paused); }
    });

    // Code version in the footer, like every page: from /health, not invented.
    if (apiBase !== null) {
      fetch(apiUrl("/health")).then(function (r) { return r.ok ? r.json() : null; })
        .then(function (d) { if (d && d.version) $("code-ver").textContent = d.version; }).catch(function () {});
    }

    Feed.start();
    // Nur der Stats-Flug braucht /v1/pulse; solange er aus ist, wird nicht gefragt.
    // loadPulse();
    // setInterval(loadPulse, 60000);

    // A hidden tab neither draws nor streams; coming back resumes after the
    // last tick seen, and the backlog lands in the terminal, not as a swarm.
    document.addEventListener("visibilitychange", function () {
      if (document.hidden) { Feed.stop(); Scene.stop(); }
      else { Scene.queue.length = 0; if (Scene.on) Scene.start(); Feed.start(); }
    });
  }

  init();
})();
