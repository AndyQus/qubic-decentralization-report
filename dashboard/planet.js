/* dashboard/planet.js — der Qubic-Planet im Hintergrund von Ticks Live.

   Reine Dekoration hinter dem Sternenflug: ein kleiner Gasriese, der sich dreht,
   mit der Qubic-Logomark mittig auf der Vorderseite. Eigene Leinwand #planet
   unter #sky (z-index 0), damit die Würfel davor vorbeifliegen und kein Klick
   bei ihm hängen bleibt. Keine Daten, keine Abhängigkeit zu ticks.js.

   Aufbau: Wolkenbänder und Wirbelstürme liegen in einer Längen-/Breitenkarte und
   drehen mit der Kugel. Das Logo dagegen ist wie ein Abziehbild von vorn auf
   die Kugel projiziert und steht still, damit es gerade und scharf bleibt.
   Pro Pixel der Scheibe sind Breite, Länge, Licht und Logo vorberechnet; pro
   Bild bleibt nur ein Nachschlagen in der Karte. Gezeichnet wird mit 30 fps,
   bei prefers-reduced-motion einmal als Standbild ohne Anflug. */
(function () {
  "use strict";
  var cv = document.getElementById("planet");
  if (!cv || !cv.getContext) return;
  var ctx = cv.getContext("2d");

  // Die Logomark, wörtlich aus assets/icon.svg: zwei Balken im 14.0035 × 24 Raster.
  var BARS = [{ x: 0, y: 0, w: 6.01795, h: 18.0012 }, { x: 8.02734, y: 0, w: 5.97616, h: 24 }];
  var LW = 14.0035, LH = 24, HL = 1.05;              // HL: Logohöhe in Kugelradien
  var VIOLET = [126, 111, 255], CYAN = [78, 224, 252];
  var TW = 512, TH = 256, SPIN = .15;                // rad/s, eine Umdrehung in rund 40 s
  var reduce = window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;

  function mix(a, b, k) { return [a[0] + (b[0] - a[0]) * k, a[1] + (b[1] - a[1]) * k, a[2] + (b[2] - a[2]) * k]; }
  function gradAt(u, v) { return mix(VIOLET, CYAN, Math.min(1, Math.max(0, (u / LW + v / LH) / 2))); }

  // Karte der Oberfläche: Bänder, die sich mit der Länge ändern, und helle Stürme,
  // an denen man die Drehung sieht. [Länge, Breite, Größe, Farbe]
  var STORMS = [[-2.4, .35, .55, [120, 150, 230]], [-.6, -.42, .38, [90, 200, 235]], [1.1, .12, .7, [140, 110, 240]],
                [2.5, -.18, .45, [80, 180, 220]], [.3, .62, .3, [170, 160, 255]], [1.9, .5, .25, [100, 220, 240]]];
  var tex = new Uint8ClampedArray(TW * TH * 3);
  (function () {
    var c1 = [16, 20, 52], c2 = [42, 34, 104], c3 = [18, 58, 92];
    for (var y = 0; y < TH; y++) {
      var lat = Math.PI / 2 - (y + .5) / TH * Math.PI;
      for (var x = 0; x < TW; x++) {
        var lon = (x + .5) / TW * Math.PI * 2 - Math.PI, i = (y * TW + x) * 3;
        var w = Math.sin(lat * 9 + Math.sin(lon * 3 + lat * 4) * 1.4 + Math.sin(lon * 7) * .5) * .5 + .5;
        var w2 = Math.sin(lat * 23 + Math.cos(lon * 5) * 2.2) * .5 + .5;
        var c = mix(mix(c1, c2, w), c3, w2 * .5);
        for (var q = 0; q < STORMS.length; q++) {
          var S = STORMS[q], dl = Math.atan2(Math.sin(lon - S[0]), Math.cos(lon - S[0]));
          var dd = (dl * dl) / (S[2] * S[2]) + ((lat - S[1]) * (lat - S[1])) / (S[2] * S[2] * .16);
          if (dd < 1) {
            var ring = .5 + .5 * Math.sin(Math.sqrt(dd) * 11 + Math.atan2(lat - S[1], dl) * 2);
            c = mix(c, S[3], (1 - dd) * (.55 + .35 * ring));
          }
        }
        tex[i] = c[0]; tex[i + 1] = c[1]; tex[i + 2] = c[2];
      }
    }
  })();

  var W = 0, R = 0, rr = 0, off = null, octx = null, img = null;
  var row = null, lon = null, lit = null, logo = null, logoA = null;

  function layout() {
    var dpr = Math.min(2, window.devicePixelRatio || 1);
    W = cv.offsetWidth; if (W < 8) return;          // offsetWidth: ohne den Anflug-Maßstab
    cv.width = Math.round(W * dpr); cv.height = Math.round(W * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    R = W / 2 / 1.2;                                   // Rand für die Atmosphäre
    rr = Math.max(8, Math.min(140, Math.round(R * dpr)));
    var sz = rr * 2, n = sz * sz, k = LH / HL, pu = k / rr;
    off = document.createElement("canvas"); off.width = off.height = sz;
    octx = off.getContext("2d"); img = octx.createImageData(sz, sz);
    row = new Int32Array(n).fill(-1); lon = new Float32Array(n); lit = new Float32Array(n);
    logo = new Float32Array(n * 3); logoA = new Float32Array(n);
    var L = [-.62, -.5, .6], ln = Math.hypot(L[0], L[1], L[2]);
    for (var py = 0; py < sz; py++) for (var px = 0; px < sz; px++) {
      var x = (px + .5 - rr) / rr, y = (py + .5 - rr) / rr, s = x * x + y * y;
      if (s > 1) continue;
      var z = Math.sqrt(1 - s), i = py * sz + px;
      row[i] = Math.min(TH - 1, Math.floor((Math.asin(y) / Math.PI + .5) * TH)) * TW;
      lon[i] = Math.atan2(x, z);
      lit[i] = .06 + .94 * Math.pow(Math.max(0, (x * L[0] + y * L[1] + z * L[2]) / ln), .85);
      // Logo als Abziehbild, Kante weich über ein Pixel.
      var u = x * k + LW / 2, v = y * k + LH / 2, sd = 99;
      for (var m = 0; m < 2; m++) {
        var B = BARS[m], dx = Math.max(B.x - u, u - B.x - B.w), dy = Math.max(B.y - v, v - B.y - B.h);
        sd = Math.min(sd, Math.max(dx, dy) > 0 ? Math.hypot(Math.max(dx, 0), Math.max(dy, 0)) : Math.max(dx, dy));
      }
      var a = Math.min(1, Math.max(0, .5 - sd / (pu / Math.max(z, .2))));
      if (a > 0) {
        var c = gradAt(Math.min(LW, Math.max(0, u)), Math.min(LH, Math.max(0, v)));
        logo[i * 3] = c[0]; logo[i * 3 + 1] = c[1]; logo[i * 3 + 2] = c[2];
        logoA[i] = a * (.6 + .4 * lit[i]);             // auf der Nachtseite gedämpft, nie ganz aus
      }
    }
  }

  function draw(t) {
    if (!off) return;
    var d = img.data, rot = reduce ? 0 : t * SPIN, n = rr * rr * 4, f = TW / (Math.PI * 2);
    for (var i = 0; i < n; i++) {
      var o = i * 4;
      if (row[i] < 0) { d[o + 3] = 0; continue; }
      var tx = Math.floor((lon[i] - rot) * f + TW / 2) % TW; if (tx < 0) tx += TW;
      var j = (row[i] + tx) * 3, l = lit[i], r = tex[j] * l, g = tex[j + 1] * l, b = tex[j + 2] * l, e = logoA[i];
      if (e > 0) { r += (logo[i * 3] - r) * e; g += (logo[i * 3 + 1] - g) * e; b += (logo[i * 3 + 2] - b) * e; }
      d[o] = r; d[o + 1] = g; d[o + 2] = b; d[o + 3] = 255;
    }
    octx.putImageData(img, 0, 0);
    var C = W / 2;
    ctx.clearRect(0, 0, W, W);
    ctx.drawImage(off, C - R, C - R, R * 2, R * 2);
    // Atmosphäre: ein cyanfarbener Saum.
    ctx.globalCompositeOperation = "lighter";
    var at = ctx.createRadialGradient(C, C, R * .9, C, C, R * 1.16);
    at.addColorStop(0, "rgba(78,224,252,0)"); at.addColorStop(.42, "rgba(78,224,252,.3)"); at.addColorStop(1, "rgba(78,224,252,0)");
    ctx.fillStyle = at; ctx.fillRect(0, 0, W, W);
    ctx.globalCompositeOperation = "source-over";
  }

  /* Anflug: Der Planet kreist um die Mitte der Bühne, in dieselbe Richtung wie
     der Sternenregen (ticks.js dreht das All mit SPIN 0.22 rad/s im Uhrzeigersinn),
     aber achtmal langsamer: Er ist weit weg, also wandert er kaum. Abstand zur
     Mitte ein Drittel der kürzeren Bühnenseite. Dabei kommt er näher: Größe wie
     in einer Perspektive (1 / Abstand), und er rückt mit der Nähe etwas nach
     außen. Nach APPROACH Sekunden blendet er aus und taucht klein und fern
     wieder auf. Steht der Flug (Pause), steht auch er. Alles nur über
     transform/opacity der Leinwand, die Pixel werden dafür nicht neu gerechnet. */
  var ORBIT = 0.22 / 8, APPROACH = 240, FADE = 10, START = -Math.PI / 4;   // Start oben rechts
  var stage = cv.parentElement, pauseBtn = document.getElementById("pause-btn");
  var ang = START, fT = FADE, speed = 1, fLast = 0;
  function fly(now) {
    var dt = fLast ? Math.min(.1, (now - fLast) / 1000) : 0; fLast = now;
    var paused = pauseBtn && pauseBtn.getAttribute("aria-pressed") === "true";
    speed += ((paused ? 0 : 1) - speed) * Math.min(1, dt * 4);
    ang += ORBIT * speed * dt; fT += speed * dt;
    var p = (fT % APPROACH) / APPROACH, s = 1 / (1.8 - p * .8);   // Abstand 1.8 → 1.0, Größe 0.56 → 1.0
    var sw = stage.clientWidth, sh = stage.clientHeight, rad = Math.min(sw, sh) / 3 * (.9 + .2 * p);
    var x = sw / 2 + Math.cos(ang) * rad - W / 2, y = sh / 2 + Math.sin(ang) * rad - W / 2;
    var tt = fT % APPROACH, op = Math.min(1, tt / FADE, (APPROACH - tt) / FADE);
    cv.style.transform = "translate(" + x.toFixed(1) + "px," + y.toFixed(1) + "px) scale(" + s.toFixed(4) + ")";
    cv.style.opacity = op.toFixed(3);
  }

  var last = 0, tSpin = 0;
  function loop(now) {
    requestAnimationFrame(loop);
    if (now - last < 33) return;                       // 30 fps reichen für eine langsame Drehung
    tSpin += Math.min(.1, (now - (last || now)) / 1000) * speed; last = now;
    fly(now); draw(tSpin);
  }
  if (window.ResizeObserver) new ResizeObserver(function () { layout(); draw(tSpin); }).observe(cv);
  else window.addEventListener("resize", function () { layout(); });
  layout(); draw(0); fly(performance.now());
  if (!reduce) requestAnimationFrame(loop);
})();
