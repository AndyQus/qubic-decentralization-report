#!/usr/bin/env python3
"""Generate dashboard/how-it-works.html from docs/CONCEPT.md (+ .de.md).

The page is a *derived artifact*: the concept documents are the single source of
truth, and this script re-renders the page whenever they change. Never edit the
generated HTML by hand -- run this instead (CI checks it is up to date, see
--check).

Markdown support is deliberately limited to the subset the concept uses
(headings, paragraphs, lists, tables, fenced code, blockquotes, and inline
code/bold/italic/links). That keeps the tool dependency-free, so the Docker
image does not grow a Markdown runtime just to serve one static page.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = {"en": ROOT / "docs" / "CONCEPT.md", "de": ROOT / "docs" / "CONCEPT.de.md"}
OUT = ROOT / "dashboard" / "how-it-works.html"

# Sections that are internal project bookkeeping rather than "how it works".
# Roadmap/open-questions age badly on a public page and duplicate the repo.
SKIP_SECTIONS = {"en": ("8.", "9."), "de": ("8.", "9.")}


# --------------------------------------------------------------------------
# inline markdown
# --------------------------------------------------------------------------
def inline(text: str) -> str:
    """Escape, then re-introduce the inline markup we allow."""
    out = html.escape(text, quote=False)
    # code spans first: their contents must not be processed further
    codes: list[str] = []

    def stash(m: re.Match[str]) -> str:
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    out = re.sub(r"`([^`]+)`", stash, out)
    out = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*([^*\n]+)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"\x00(\d+)\x00", lambda m: f"<code>{codes[int(m.group(1))]}</code>", out)
    return out


def slugify(text: str) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", s).strip("-") or "section"


def link_section_refs(body: str, toc_all: dict[str, str]) -> str:
    """Turn the concept's own cross-references (§4.4, §5.1, ...) into anchors.

    The concept is written to be read linearly with section numbers; on a single
    scrolling page those references are only useful if they jump.
    """
    def repl(m: re.Match[str]) -> str:
        num = m.group(1)
        anchor = toc_all.get(num)
        if not anchor:
            return m.group(0)
        return f'<a class="xref" href="#{anchor}">§{num}</a>'

    # skip refs already inside an anchor's text
    return re.sub(r"§(\d+(?:\.\d+)?)", repl, body)


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


# --------------------------------------------------------------------------
# block markdown
# --------------------------------------------------------------------------
def render(md: str, lang: str) -> tuple[str, list[tuple[str, str]], dict[str, str]]:
    """Return (html, toc, numbered).

    toc is a list of (anchor, title) for h2 headings; numbered maps a section
    number ("4.4") to its anchor so cross-references can be linked.
    """
    lines = md.split("\n")
    out: list[str] = []
    toc: list[tuple[str, str]] = []
    numbered: dict[str, str] = {}   # "4.4" -> anchor, for cross-reference links
    i = 0
    skipping = False

    while i < len(lines):
        line = lines[i]

        # fenced code
        if line.startswith("```"):
            i += 1
            buf: list[str] = []
            while i < len(lines) and not lines[i].startswith("```"):
                buf.append(lines[i])
                i += 1
            i += 1
            if not skipping:
                out.append("<pre><code>" + html.escape("\n".join(buf)) + "</code></pre>")
            continue

        # headings
        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            level, title = len(m.group(1)), m.group(2).strip()
            # The document's own H1 becomes the page hero, handled by the shell.
            if level == 1:
                i += 1
                continue
            if level == 2:
                num = title.split(None, 1)[0]
                skipping = num.startswith(SKIP_SECTIONS[lang])
            if not skipping:
                anchor = slugify(title)
                if level == 2:
                    toc.append((anchor, title))
                num_m = re.match(r"^(\d+(?:\.\d+)?)\.?\s", title)
                if num_m:
                    numbered[num_m.group(1)] = anchor
                out.append(f'<h{level} id="{anchor}">{inline(title)}</h{level}>')
            i += 1
            continue

        if skipping:
            i += 1
            continue

        # horizontal rule -> section separator
        if re.match(r"^-{3,}\s*$", line):
            out.append("<hr>")
            i += 1
            continue

        # table
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(
            r"^\s*\|[\s:|-]+\|\s*$", lines[i + 1]
        ):
            head = _split_row(line)
            i += 2
            body: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                body.append(_split_row(lines[i]))
                i += 1
            th = "".join(f"<th>{inline(c)}</th>" for c in head)
            rows = "".join(
                "<tr>" + "".join(f"<td>{inline(c)}</td>" for c in r) + "</tr>" for r in body
            )
            # wrapper keeps wide tables scrollable on phones instead of
            # forcing the whole page to scroll sideways
            out.append(
                f'<div class="tw"><table><thead><tr>{th}</tr></thead><tbody>{rows}</tbody></table></div>'
            )
            continue

        # lists (ordered / unordered, with continuation lines)
        m = re.match(r"^(\s*)([-*]|\d+[.)])\s+(.*)$", line)
        if m:
            ordered = not m.group(2) in ("-", "*")
            tag = "ol" if ordered else "ul"
            items: list[str] = []
            while i < len(lines):
                mm = re.match(r"^(\s*)([-*]|\d+[.)])\s+(.*)$", lines[i])
                if not mm:
                    # continuation of the previous bullet?
                    if items and lines[i].startswith("  ") and lines[i].strip():
                        items[-1] += " " + lines[i].strip()
                        i += 1
                        continue
                    break
                items.append(mm.group(3).strip())
                i += 1
            out.append(f"<{tag}>" + "".join(f"<li>{inline(x)}</li>" for x in items) + f"</{tag}>")
            continue

        # blockquote
        if line.startswith(">"):
            buf = []
            while i < len(lines) and lines[i].startswith(">"):
                buf.append(lines[i].lstrip("> ").rstrip())
                i += 1
            out.append(f"<blockquote>{inline(' '.join(buf))}</blockquote>")
            continue

        # paragraph
        if line.strip():
            buf = []
            while i < len(lines) and lines[i].strip() and not re.match(
                r"^(#{1,4}\s|```|>|\s*([-*]|\d+[.)])\s|\|)", lines[i]
            ) and not re.match(r"^-{3,}\s*$", lines[i]):
                buf.append(lines[i].strip())
                i += 1
            out.append(f"<p>{inline(' '.join(buf))}</p>")
            continue

        i += 1

    return "\n".join(out), toc, numbered


PAGE = """<!doctype html>
<html lang="en" data-theme="dark">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>How it works \u2014 Qubic Decentralization Report</title>
<meta name="description" content="{desc}" />
<!-- GENERATED FILE \u2014 do not edit.
     Source: docs/CONCEPT.md + docs/CONCEPT.de.md (concept-sha {digest})
     Regenerate: python scripts/build_how_it_works.py -->
<style>
  :root{{
    --bg:#0c141b; --bg2:#101820; --surface:#16232e; --surface2:#1b2c38;
    --border:#263846; --text:#e6eef2; --muted:#8aa0ab; --faint:#5c7180;
    --accent:#22d3a6; --accent2:#3b9dff;
    --radius:14px; --mono:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
    --sans:system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  }}
  :root[data-theme="light"]{{
    --bg:#eef3f6; --bg2:#f6f9fb; --surface:#ffffff; --surface2:#f1f5f8;
    --border:#d8e2e8; --text:#132430; --muted:#546773; --faint:#8698a2;
    --accent:#0aa17e; --accent2:#1f7fe0;
  }}
{style}
</style>
</head>
<body>
<header class="top">
  <div class="top-inner">
    <div class="brand">
      <div class="logo" aria-hidden="true"></div>
      <div>
        <h1>Qubic Decentralization Report</h1>
        <div class="sub" id="nav-sub">How it works</div>
      </div>
    </div>
    <div class="spacer"></div>
    <div class="controls">
      <a class="back" href="./index.html" id="back-link">&larr; <span class="lbl">Dashboard</span></a>
      <div class="seg" id="lang-seg">
        <button data-lang="en">EN</button><button data-lang="de">DE</button>
      </div>
      <button class="icon-btn" id="theme-btn" title="Theme">&#127769;</button>
    </div>
  </div>
</header>

<div class="wrap">
{body}
  <footer>
    <span>&copy; <span id="foot-year">2026</span> Qubic &ndash; Sponsored by AndyQus</span>
    <span>&bull;</span>
    <a href="https://github.com/AndyQus/qubic-decentralization-report" target="_blank" rel="noreferrer">GitHub</a>
  </footer>
</div>

<script>
(function(){{
  "use strict";
  // Same storage keys as the dashboard, so theme and language carry across pages.
  var LS_THEME="qdr.theme", LS_LANG="qdr.lang", root=document.documentElement;
  function get(k){{ try{{ return localStorage.getItem(k); }}catch(e){{ return null; }} }}
  function set(k,v){{ try{{ localStorage.setItem(k,v); }}catch(e){{}} }}

  var NAV={{en:{{sub:"How it works",back:"Dashboard"}},de:{{sub:"Wie es funktioniert",back:"Dashboard"}}}};
  var lang = get(LS_LANG)==="de" ? "de" : "en";
  var theme = get(LS_THEME)==="light" ? "light" : "dark";

  function applyTheme(){{
    root.setAttribute("data-theme",theme);
    document.getElementById("theme-btn").textContent = theme==="dark" ? "\u1f319" : "\u2600\ufe0f";
  }}
  function applyLang(){{
    root.setAttribute("lang",lang);
    var blocks=document.querySelectorAll("[data-lang-block]");
    for(var i=0;i<blocks.length;i++){{
      blocks[i].hidden = blocks[i].getAttribute("data-lang-block")!==lang;
    }}
    document.getElementById("nav-sub").textContent = NAV[lang].sub;
    document.querySelector("#back-link .lbl").textContent = NAV[lang].back;
    var btns=document.querySelectorAll("#lang-seg button");
    for(var j=0;j<btns.length;j++){{
      btns[j].classList.toggle("active", btns[j].dataset.lang===lang);
    }}
  }}

  var lb=document.querySelectorAll("#lang-seg button");
  for(var k=0;k<lb.length;k++){{
    lb[k].addEventListener("click",function(){{
      lang=this.dataset.lang; set(LS_LANG,lang); applyLang();
    }});
  }}
  document.getElementById("theme-btn").addEventListener("click",function(){{
    theme = theme==="dark" ? "light" : "dark"; set(LS_THEME,theme); applyTheme();
  }});
  document.getElementById("foot-year").textContent = new Date().getFullYear();

  applyTheme(); applyLang();
}})();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------
# page shell
# --------------------------------------------------------------------------
UI = {
    "en": {
        "nav": "How it works",
        "back": "Dashboard",
        "hero_sub": "The concept behind the report \u2014 what is measured, why those metrics, and where attribution stops being provable.",
        "toc": "On this page",
        "generated": "Generated from",
        "generated_tail": "\u2014 edit the concept, not this page.",
        "source": "Read the source document on GitHub",
    },
    "de": {
        "nav": "Wie es funktioniert",
        "back": "Dashboard",
        "hero_sub": "Das Konzept hinter dem Report \u2014 was gemessen wird, warum diese Metriken, und wo die Zuordnung aufh\u00f6rt, beweisbar zu sein.",
        "toc": "Auf dieser Seite",
        "generated": "Generiert aus",
        "generated_tail": "\u2014 bearbeite das Konzept, nicht diese Seite.",
        "source": "Quelldokument auf GitHub lesen",
    },
}

REPO = "https://github.com/AndyQus/qubic-decentralization-report/blob/main/docs"

STYLE = """
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--text);font-family:var(--sans);
    -webkit-font-smoothing:antialiased;line-height:1.6}
  a{color:var(--accent2);text-decoration:none}
  a:hover{text-decoration:underline}
  .wrap{max-width:860px;margin:0 auto;padding:0 20px 72px}

  header.top{position:sticky;top:0;z-index:20;background:linear-gradient(180deg,var(--bg2),var(--bg2) 70%,transparent);
    border-bottom:1px solid var(--border);backdrop-filter:blur(6px)}
  .top-inner{max-width:860px;margin:0 auto;padding:14px 20px;display:flex;align-items:center;gap:14px;flex-wrap:wrap}
  .brand{display:flex;align-items:center;gap:12px;min-width:0}
  .logo{width:34px;height:34px;border-radius:9px;flex:0 0 auto;
    background:radial-gradient(circle at 30% 30%,var(--accent),#0b7d63);
    box-shadow:0 0 0 1px rgba(255,255,255,.08) inset}
  .brand h1{font-size:16px;margin:0;font-weight:650;letter-spacing:.2px}
  .brand .sub{font-size:12px;color:var(--muted);margin-top:1px}
  .spacer{flex:1 1 auto}
  .controls{display:flex;align-items:center;gap:8px}
  .seg{display:inline-flex;border:1px solid var(--border);border-radius:9px;overflow:hidden;background:var(--surface)}
  .seg button{background:transparent;border:0;color:var(--muted);padding:7px 11px;font-size:12.5px;
    font-weight:600;cursor:pointer;font-family:var(--sans)}
  .seg button.active{background:var(--accent);color:#04231b}
  :root[data-theme="light"] .seg button.active{color:#fff}
  .icon-btn{width:38px;height:34px;border:1px solid var(--border);border-radius:9px;background:var(--surface);
    color:var(--text);cursor:pointer;font-size:15px;display:inline-flex;align-items:center;justify-content:center}
  .back{font-size:13px;font-weight:600;color:var(--muted);white-space:nowrap}
  .back:hover{color:var(--text);text-decoration:none}

  .hero{padding:30px 0 6px}
  .hero h2{font-size:clamp(22px,4.6vw,30px);margin:0 0 8px;letter-spacing:-.2px;line-height:1.25}
  .hero p{margin:0;color:var(--muted);font-size:15px;max-width:64ch}

  .toc{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
    padding:16px 18px;margin:26px 0 8px}
  .toc h3{margin:0 0 10px;font-size:12px;text-transform:uppercase;letter-spacing:.09em;color:var(--faint)}
  .toc ol{margin:0;padding-left:20px;columns:2;column-gap:26px}
  .toc li{margin:4px 0;font-size:13.5px;break-inside:avoid}

  .doc h2{font-size:clamp(19px,3.4vw,23px);margin:38px 0 10px;padding-top:14px;
    border-top:1px solid var(--border);letter-spacing:-.15px;line-height:1.3;scroll-margin-top:84px}
  .doc h3{font-size:clamp(16px,2.8vw,18px);margin:26px 0 8px;color:var(--text);scroll-margin-top:84px}
  .doc h4{font-size:15px;margin:20px 0 6px;color:var(--muted);scroll-margin-top:84px}
  .doc p{margin:11px 0;font-size:14.6px}
  .doc ul,.doc ol{margin:11px 0;padding-left:22px;font-size:14.6px}
  .doc li{margin:6px 0}
  .doc hr{border:0;border-top:1px solid var(--border);margin:30px 0;opacity:.55}
  .doc strong{color:var(--text);font-weight:650}
  .doc code{font-family:var(--mono);font-size:.88em;background:var(--surface2);
    border:1px solid var(--border);border-radius:5px;padding:1px 5px;
    overflow-wrap:anywhere}
  .doc pre{background:var(--surface);border:1px solid var(--border);border-radius:10px;
    padding:13px 15px;overflow-x:auto;-webkit-overflow-scrolling:touch}
  .doc pre code{background:none;border:0;padding:0;font-size:12.6px;line-height:1.5;white-space:pre}
  .doc blockquote{margin:14px 0;padding:10px 16px;border-left:3px solid var(--accent);
    background:var(--surface);border-radius:0 8px 8px 0;color:var(--muted)}
  .doc blockquote p{margin:0}

  /* wide tables scroll inside their own box so the page never scrolls sideways */
  .tw{overflow-x:auto;-webkit-overflow-scrolling:touch;margin:14px 0;
    border:1px solid var(--border);border-radius:10px}
  .doc table{border-collapse:collapse;width:100%;font-size:13.6px;min-width:420px}
  .doc th,.doc td{padding:9px 13px;text-align:left;border-bottom:1px solid var(--border);vertical-align:top}
  .doc th{background:var(--surface2);font-weight:650;font-size:12.4px;
    text-transform:uppercase;letter-spacing:.05em;color:var(--muted);white-space:nowrap}
  .doc tbody tr:last-child td{border-bottom:0}

  .gennote{margin:30px 0 0;padding:12px 15px;border-radius:10px;font-size:12.6px;
    color:var(--muted);background:var(--surface);border:1px solid var(--border)}
  .gennote code{font-family:var(--mono);color:var(--text)}
  .doc a.xref{color:var(--accent);font-weight:600;white-space:nowrap}

  footer{margin-top:30px;padding-top:16px;border-top:1px solid var(--border);
    font-size:12px;color:var(--faint);display:flex;gap:10px;flex-wrap:wrap;align-items:center}

  @media (max-width:640px){
    .wrap{padding:0 15px 56px}
    .top-inner{padding:11px 15px;gap:10px}
    .brand .sub{display:none}
    .brand h1{font-size:15px}
    .toc ol{columns:1}
    .doc table{min-width:340px}
    .doc th,.doc td{padding:8px 10px}
  }
  /* very narrow phones: drop the back-link label, keep the arrow as the target */
  @media (max-width:380px){
    .back span.lbl{display:none}
    .doc pre code{font-size:11.6px}
  }
  @media (prefers-reduced-motion:no-preference){html{scroll-behavior:smooth}}
"""


def build() -> str:
    docs: dict[str, dict] = {}
    for lang, path in SOURCES.items():
        md = path.read_text(encoding="utf-8")
        body, toc, numbered = render(md, lang)
        docs[lang] = {"body": link_section_refs(body, numbered), "toc": toc,
                      "file": path.name}

    parts: list[str] = []
    for lang in ("en", "de"):
        d, ui = docs[lang], UI[lang]
        toc_items = "".join(
            '<li><a href="#{}">{}</a></li>'.format(a, html.escape(t)) for a, t in d["toc"]
        )
        parts.append(
            '<div class="lang-block" data-lang-block="{lang}">'
            '<div class="hero"><h2>{nav}</h2><p>{sub}</p></div>'
            '<nav class="toc"><h3>{toc}</h3><ol>{items}</ol></nav>'
            '<article class="doc">{body}</article>'
            '<p class="gennote">{gen} <code>docs/{file}</code> {tail} '
            '<a href="{repo}/{file}" target="_blank" rel="noreferrer">{src}</a></p>'
            "</div>".format(
                lang=lang, nav=ui["nav"], sub=ui["hero_sub"], toc=ui["toc"],
                items=toc_items, body=d["body"], gen=ui["generated"],
                file=d["file"], tail=ui["generated_tail"], repo=REPO, src=ui["source"],
            )
        )

    body = "\n".join(parts)
    digest = hashlib.sha256(
        b"".join(SOURCES[l].read_bytes() for l in ("en", "de"))
    ).hexdigest()[:12]

    return PAGE.format(style=STYLE, body=body, digest=digest,
                       desc=html.escape(UI["en"]["hero_sub"]))


def main() -> int:
    ap = argparse.ArgumentParser(description="Regenerate the /how-it-works page.")
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the page is stale (for CI / pre-commit)")
    args = ap.parse_args()

    page = build()
    if args.check:
        current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
        if current != page:
            print("STALE: {} is out of date with docs/CONCEPT*.md\n"
                  "       run: python scripts/build_how_it_works.py".format(
                      OUT.relative_to(ROOT)), file=sys.stderr)
            return 1
        print("OK: {} is in sync with the concept".format(OUT.relative_to(ROOT)))
        return 0

    OUT.write_text(page, encoding="utf-8")
    counts = {l: len(render(SOURCES[l].read_text(encoding="utf-8"), l)[1]) for l in ("en", "de")}
    print("wrote {} ({:,} bytes) \u2014 {} EN / {} DE sections".format(
        OUT.relative_to(ROOT), len(page), counts["en"], counts["de"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
