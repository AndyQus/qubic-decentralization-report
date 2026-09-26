"""Serve the local dashboard files against the live API — for F5 frontend work.

The local store only holds what a local worker saw, and much of it cannot be
fetched after the fact: a price reading or a mining sample describes a moment,
so every hour the laptop was off is a hole that never fills. The live instance
was watching. For work on the pages themselves, the simplest way to real data is
to not use a local store at all.

This server answers /dashboard/* and /examples/* from the working tree — so an
edited page shows up on reload — and forwards every other request (/v1/*,
/health, /api, the root icons) to the live deployment. Same origin for both, so
the pages need no ?api= parameter and links between them keep working.

It only reads: the API is read-only, and anything but GET/HEAD is refused here
rather than forwarded. Backend changes are not exercised — that is what the
"Dashboard + live API" launch with a local API and worker is for.

    python scripts/dev_live_proxy.py [--port 8001] [--upstream https://report.qubic.tools]
"""

from __future__ import annotations

import argparse
import mimetypes
import sys
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent.parent
LOCAL_DIRS = {"dashboard": ROOT / "dashboard", "examples": ROOT / "examples"}
# The live server serves this generated page under a clean URL; answer it from
# the local copy too, so the page being edited is the one that is shown.
LOCAL_ALIASES = {"/how-it-works": ROOT / "dashboard" / "how-it-works.html"}
# Upstream headers worth passing on. Hop-by-hop and length headers are left
# out: the body is re-sent whole and its length recomputed.
PASS_HEADERS = ("content-type", "cache-control", "etag", "last-modified", "retry-after")
# Marker path the start task uses to recognise an already-running proxy.
PING = "/__dev_live_proxy"

mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("text/javascript", ".js")


class Handler(BaseHTTPRequestHandler):
    upstream = ""

    def do_GET(self):
        self._handle(send_body=True)

    def do_HEAD(self):
        self._handle(send_body=False)

    def _refuse(self):
        self._reply(405, b"read-only dev proxy\n", {"content-type": "text/plain"}, True)

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse

    def _handle(self, send_body: bool):
        path = unquote(urlsplit(self.path).path)
        if path == PING:
            return self._reply(200, b"ok\n", {"content-type": "text/plain"}, send_body)
        if path == "/":
            return self._reply(307, b"", {"location": "/dashboard/"}, send_body)
        if path.strip("/") in LOCAL_DIRS and not path.endswith("/"):
            return self._reply(307, b"", {"location": path + "/"}, send_body)
        local = self._local_file(path)
        if local is not None:
            return self._serve_file(local, send_body)
        if path.split("/")[1] in LOCAL_DIRS:
            return self._reply(404, b"not found\n", {"content-type": "text/plain"}, send_body)
        self._forward(send_body)

    def _local_file(self, path: str) -> Path | None:
        if path in LOCAL_ALIASES:
            return LOCAL_ALIASES[path]
        parts = path.lstrip("/").split("/", 1)
        base = LOCAL_DIRS.get(parts[0])
        if base is None or len(parts) == 1:
            return None
        target = (base / parts[1]).resolve()
        if base.resolve() not in target.parents and target != base.resolve():
            return None  # ../ escape
        if target.is_dir():
            target = target / "index.html"
        return target if target.is_file() else None

    def _serve_file(self, file: Path, send_body: bool):
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/json":
            ctype += "; charset=utf-8"
        # no-cache, like the live server: an edited page must show on reload.
        self._reply(200, file.read_bytes(),
                    {"content-type": ctype, "cache-control": "no-cache"}, send_body)

    def _forward(self, send_body: bool):
        req = urllib.request.Request(
            self.upstream + self.path,
            method=self.command,
            headers={"accept": self.headers.get("accept", "*/*"),
                     "user-agent": "qdr-dev-live-proxy"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                status, body, headers = r.status, r.read(), r.headers
        except urllib.error.HTTPError as e:
            # A 503 "still building" from live is information, not a proxy failure.
            status, body, headers = e.code, e.read(), e.headers
        except (urllib.error.URLError, TimeoutError) as e:
            msg = f'{{"detail": "dev proxy: {self.upstream} unreachable ({e})"}}'
            return self._reply(502, msg.encode(), {"content-type": "application/json"}, send_body)
        out = {k: headers[k] for k in PASS_HEADERS if headers.get(k)}
        self._reply(status, body, out, send_body)

    def _reply(self, status: int, body: bytes, headers: dict, send_body: bool):
        self.send_response(status)
        for k, v in headers.items():
            self.send_header(k, v)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        if send_body and body:
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        sys.stdout.write("%s %s\n" % (self.address_string(), fmt % args))
        sys.stdout.flush()


def already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{PING}", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--upstream", default="https://report.qubic.tools")
    args = ap.parse_args()
    Handler.upstream = args.upstream.rstrip("/")

    url = f"http://127.0.0.1:{args.port}/dashboard/"
    print(f"Starting live proxy: local dashboard, data from {Handler.upstream}", flush=True)
    # A second launch (or a leftover from a previous session) reuses the running
    # proxy instead of failing to bind and taking the browser launch down with it.
    if already_running(args.port):
        print(f"Live proxy running on {url} (reusing existing proxy)", flush=True)
        return 0
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Live proxy running on {url}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
