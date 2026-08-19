"""Capture live engine state + chart data and emit a static preview page
(file://-openable) that stubs the API with the captured payloads.
Run: python tests/make_snapshot.py
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kimi.engine import Engine

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB = ROOT / "web"

eng = Engine()
eng.scan_once()
state = eng.snapshot()
chart = eng.chart("BTCUSDT", "15m")

(WEB / "_snapshot_data.js").write_text(
    "window.SNAP = " + json.dumps({"state": state, "chart": chart}) + ";\n",
    encoding="utf-8")

html = (WEB / "index.html").read_text(encoding="utf-8")
html = html.replace('href="/styles.css"', 'href="./styles.css"')
html = html.replace('src="/app.js"', 'src="./_snapshot_stub.js"></script><script src="./app.js"')
stub = """
window.EventSource = class {
  constructor() {
    setTimeout(() => this.onmessage && this.onmessage({ data: JSON.stringify(window.SNAP.state) }), 400);
  }
};
const _fetch = window.fetch.bind(window);
window.fetch = (url) => {
  const u = String(url);
  if (u.startsWith('/api/state')) return Promise.resolve(new Response(JSON.stringify(window.SNAP.state)));
  if (u.startsWith('/api/chart')) return Promise.resolve(new Response(JSON.stringify(window.SNAP.chart)));
  if (u.startsWith('/api/')) return Promise.resolve(new Response(JSON.stringify({ ok: true })));
  return _fetch(url);
};
"""
(WEB / "_snapshot_stub.js").write_text(stub, encoding="utf-8")
html = html.replace('src="./_snapshot_stub.js"></script><script src="./app.js"',
                    'src="./_snapshot_data.js"></script><script src="./_snapshot_stub.js"></script><script src="./app.js"')
(WEB / "preview_snapshot.html").write_text(html, encoding="utf-8")
print("snapshot page written:",
      (WEB / "preview_snapshot.html").as_uri(),
      "| signals:", len(state["signals"]), "| scans:", state["scanCount"])
