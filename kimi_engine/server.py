"""KIMI Engine server — zero-dependency stdlib HTTP + SSE.

    python server.py [--host H] [--port P]

Routes:
  GET  /                  dashboard
  GET  /app.js /styles.css
  GET  /api/state         full snapshot
  GET  /api/chart?symbol=&tf=
  GET  /api/stream        SSE snapshot push (3s)
  POST /api/execute       {"id": "...", "venue": "auto|host|paper|bybit"}
  POST /api/close         {"id": "..."}
  POST /api/kill          {"on": true|false}
  POST /api/config        {"minScore": 75, "autoTrade": false}
"""
from __future__ import annotations

import argparse
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from kimi.config import Config
from kimi.engine import Engine

ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"

engine = Engine()


def _json_body(handler: BaseHTTPRequestHandler) -> dict:
    n = int(handler.headers.get("Content-Length") or 0)
    if n <= 0:
        return {}
    try:
        return json.loads(handler.rfile.read(n).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


class Handler(BaseHTTPRequestHandler):
    server_version = "KIMI/1.0"

    def log_message(self, *args) -> None:  # quiet
        pass

    # ------------------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: object, code: int = 200) -> None:
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _static(self, name: str, ctype: str) -> None:
        p = WEB / name
        if p.is_file():
            self._send(200, p.read_bytes(), ctype)
        else:
            self._send(404, b"not found", "text/plain")

    # ------------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            self._static("index.html", "text/html; charset=utf-8")
        elif u.path == "/app.js":
            self._static("app.js", "application/javascript; charset=utf-8")
        elif u.path == "/styles.css":
            self._static("styles.css", "text/css; charset=utf-8")
        elif u.path == "/api/state":
            self._json(engine.snapshot())
        elif u.path == "/api/symbols":
            self._json({"active": list(engine.symbols),
                        "available": engine.feed.available_symbols()})
        elif u.path == "/api/chart":
            sym = (q.get("symbol", [Config.SYMBOLS[0]])[0] or Config.SYMBOLS[0]).upper()
            tf = q.get("tf", [Config.TF_CONTEXT])[0]
            try:
                self._json(engine.chart(sym, tf))
            except Exception as exc:  # noqa: BLE001
                self._json({"error": str(exc)}, 502)
        elif u.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                for _ in range(1200):  # ~1h, then client reconnects
                    payload = json.dumps(engine.snapshot())
                    self.wfile.write(f"data: {payload}\n\n".encode())
                    self.wfile.flush()
                    time.sleep(3)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        else:
            self._send(404, b"not found", "text/plain")

    def do_POST(self) -> None:  # noqa: N802
        u = urlparse(self.path)
        body = _json_body(self)
        if u.path == "/api/execute":
            self._json(engine.execute(str(body.get("id", "")),
                                      str(body.get("venue", "auto"))))
        elif u.path == "/api/close":
            self._json(engine.close_position(str(body.get("id", ""))))
        elif u.path == "/api/kill":
            self._json(engine.set_kill(bool(body.get("on"))))
        elif u.path == "/api/config":
            if "minScore" in body:
                try:
                    engine.min_score = float(body["minScore"])
                except (TypeError, ValueError):
                    pass
            if "autoTrade" in body:
                engine.auto_trade = bool(body["autoTrade"])
            self._json({"ok": True, "minScore": engine.min_score,
                        "autoTrade": engine.auto_trade})
        elif u.path == "/api/symbols":
            self._json(engine.set_symbols(body.get("symbols", [])))
        elif u.path == "/api/scan":
            self._json(engine.scan_now())
        else:
            self._send(404, b"not found", "text/plain")


def main() -> None:
    ap = argparse.ArgumentParser(description="KIMI Engine")
    ap.add_argument("--host", default=Config.HOST)
    ap.add_argument("--port", type=int, default=Config.PORT)
    args, _ = ap.parse_known_args()

    engine.start()
    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"KIMI Engine 1.0 — http://{args.host}:{args.port}  "
          f"(paper={Config.BROKER == 'paper' or not Config.LIVE_ENABLED}, "
          f"symbols={len(Config.SYMBOLS)})", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        srv.server_close()


if __name__ == "__main__":
    main()
