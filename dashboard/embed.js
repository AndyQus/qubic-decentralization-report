/*!
 * Qubic Decentralization Report — embeddable widget
 * Zero dependencies. Drop into any explorer page:
 *
 *   <div id="qdr" data-api="https://your-report-host"></div>
 *   <script src="https://your-report-host/dashboard/embed.js"></script>
 *   <script>QDRWidget.render(document.getElementById('qdr'));</script>
 *
 * Options (2nd arg or data-* attrs): { api, lang:'en'|'de', theme:'dark'|'light',
 * compact:true }. It renders a headline (operators, Nakamoto ⅓, top operator) and a
 * proportion bar of the largest operators. It fetches <api>/v1/dashboard-data.
 */
(function (global) {
  "use strict";

  var T = {
    en: { operators: "operators", from676: "from 676 slots", nak: "Nakamoto ⅓",
          nakSub: "to control ⅓ of revenue", top: "top operator", declared: "declared",
          unattributed: "unattributed", epoch: "Epoch", sample: "sample data",
          title: "Qubic decentralization" },
    de: { operators: "Betreiber", from676: "von 676 Slots", nak: "Nakamoto ⅓",
          nakSub: "für ⅓ des Umsatzes", top: "Top-Betreiber", declared: "deklariert",
          unattributed: "nicht zugeordnet", epoch: "Epoche", sample: "Beispieldaten",
          title: "Qubic-Dezentralisierung" }
  };

  var CSS = [
    ".qdr-w{--a:#22d3a6;--u:#5c7180;--bg:#16232e;--bd:#263846;--tx:#e6eef2;--mut:#8aa0ab;",
    "font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);",
    "border:1px solid var(--bd);border-radius:14px;padding:16px 18px;color:var(--tx);max-width:520px}",
    ".qdr-w.light{--bg:#fff;--bd:#d8e2e8;--tx:#132430;--mut:#546773;--a:#0aa17e;--u:#93a6b0}",
    ".qdr-w a{color:var(--a);text-decoration:none}",
    ".qdr-h{display:flex;align-items:baseline;justify-content:space-between;gap:10px;margin-bottom:12px}",
    ".qdr-h b{font-size:14px;font-weight:650}.qdr-h .ep{font-size:12px;color:var(--mut);font-family:ui-monospace,monospace}",
    ".qdr-k{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:12px}",
    ".qdr-k .v{font-size:24px;font-weight:720;line-height:1}.qdr-k .v.a{color:var(--a)}",
    ".qdr-k .l{font-size:11px;color:var(--mut);margin-top:3px}",
    ".qdr-bar{display:flex;height:14px;border-radius:5px;overflow:hidden;margin-bottom:6px}",
    ".qdr-bar i{display:block;height:100%}",
    ".qdr-lg{font-size:11px;color:var(--mut);display:flex;flex-wrap:wrap;gap:4px 12px}",
    ".qdr-lg span{white-space:nowrap}.qdr-lg b{color:var(--tx);font-weight:600}",
    ".qdr-badge{font-size:10px;color:#f5a623;border:1px solid rgba(245,166,35,.4);border-radius:999px;padding:1px 7px;margin-left:6px}",
    ".qdr-ft{font-size:10px;color:var(--mut);margin-top:10px}"
  ].join("");

  function injectCss() {
    if (document.getElementById("qdr-embed-css")) return;
    var s = document.createElement("style");
    s.id = "qdr-embed-css"; s.textContent = CSS; document.head.appendChild(s);
  }

  function palette(i) {
    var c = ["#22d3a6", "#3b9dff", "#a78bfa", "#f5a623", "#ff8fa3", "#4dd0e1", "#9ccc65"];
    return c[i % c.length];
  }

  function fmt(n) { return (n == null ? "–" : ("" + n).replace(/\B(?=(\d{3})+(?!\d))/g, " ")); }
  function esc(s){ return (""+s).replace(/[&<>"]/g,function(c){return{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c];}); }

  function render(el, opts) {
    if (!el) return;
    opts = opts || {};
    var api = opts.api || el.getAttribute("data-api") || "";
    var lang = opts.lang || el.getAttribute("data-lang") || "en";
    var theme = opts.theme || el.getAttribute("data-theme") || "dark";
    var t = T[lang] || T.en;
    injectCss();
    el.className = "qdr-w" + (theme === "light" ? " light" : "");
    el.innerHTML = '<div class="qdr-ft">…</div>';

    fetch(api.replace(/\/$/, "") + "/v1/dashboard-data")
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then(function (d) {
        var series = (d.timeseries && d.timeseries.series) || [];
        var s = series[series.length - 1] || {};
        var ec = (d.epoch_clusters && d.epoch_clusters.epochs) || [];
        var bubbles = (ec[ec.length - 1] && ec[ec.length - 1].bubbles) || [];
        bubbles = bubbles.slice().sort(function (a, b) { return (b.revenue_share || 0) - (a.revenue_share || 0); });

        var bar = bubbles.map(function (b, i) {
          var col = b.confidence === "unattributed" ? "var(--u)" : palette(i);
          return '<i style="width:' + ((b.revenue_share || 0) * 100).toFixed(2) + '%;background:' + col + '" title="' + esc(b.label) + '"></i>';
        }).join("");

        var lg = bubbles.slice(0, 5).map(function (b, i) {
          var col = b.confidence === "unattributed" ? "var(--u)" : palette(i);
          return '<span><b style="color:' + col + '">■</b> ' + esc(b.label) + " " + ((b.revenue_share || 0) * 100).toFixed(1) + "%</span>";
        }).join("");

        el.innerHTML =
          '<div class="qdr-h"><b>' + t.title + (d.sample ? '<span class="qdr-badge">' + t.sample + "</span>" : "") +
          '</b><span class="ep">' + t.epoch + " " + (s.epoch != null ? s.epoch : "–") + "</span></div>" +
          '<div class="qdr-k">' +
            '<div><div class="v a">' + fmt(s.operators) + '</div><div class="l">' + t.operators + " · " + t.from676 + "</div></div>" +
            '<div><div class="v" style="color:#f5a623">' + fmt(s.nakamoto_one_third_revenue) + '</div><div class="l">' + t.nak + " · " + t.nakSub + "</div></div>" +
            '<div><div class="v">' + (s.top1_share != null ? (s.top1_share * 100).toFixed(1) + "%" : "–") + '</div><div class="l">' + t.top + "</div></div>" +
          "</div>" +
          '<div class="qdr-bar">' + bar + "</div>" +
          '<div class="qdr-lg">' + lg + "</div>" +
          '<div class="qdr-ft">Qubic Decentralization Report · <a href="' + esc(api) + '/dashboard/" target="_blank" rel="noopener">details</a></div>';
      })
      .catch(function (e) {
        el.innerHTML = '<div class="qdr-ft">Qubic Decentralization Report — data unavailable (' + esc(e.message) + ")</div>";
      });
  }

  function auto() {
    var nodes = document.querySelectorAll("[data-qdr-auto]");
    for (var i = 0; i < nodes.length; i++) render(nodes[i]);
  }

  global.QDRWidget = { render: render, auto: auto };
  if (document.readyState !== "loading") auto();
  else document.addEventListener("DOMContentLoaded", auto);
})(window);
