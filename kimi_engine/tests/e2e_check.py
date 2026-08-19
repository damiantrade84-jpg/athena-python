"""In-process end-to-end check: engine scan + HTTP API + chart payload.
Run: python tests/e2e_check.py
"""
from __future__ import annotations

import json
import threading
import time
from http.server import ThreadingHTTPServer

import requests

import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from kimi.engine import Engine
import server as srv_mod

PORT = 7155

print("== 1. engine scan (live Binance data) ==")
eng = srv_mod.engine
t0 = time.time()
eng.scan_once()
print(f"scan took {time.time() - t0:.1f}s, symbols scored: {list(eng.scores.keys())}")
snap = eng.snapshot()
print("equity:", snap["account"]["equity"], "| active signals:", len(snap["signals"]),
      "| errors:", snap["errors"][:2])
for sym, d in list(snap["symbols"].items())[:3]:
    print(f"  {sym}: px={d['price']} L={d['cards']['long']['total']} "
          f"S={d['cards']['short']['total']}")

print("== 2. signal geometry (if any) or forced evaluate ==")
from kimi.signals import evaluate
from kimi.config import Config
cs_e = eng.feed.klines("BTCUSDT", Config.TF_ENTRY)
cs_x = eng.feed.klines("BTCUSDT", Config.TF_CONTEXT)
cs_b = eng.feed.klines("BTCUSDT", Config.TF_BIAS)
sig, cards = evaluate(cs_e, cs_x, cs_b, min_score=60)
print("BTC cards:", [(c.direction, c.total, c.grade) for c in cards])
if sig:
    print("signal:", sig.id, sig.symbol, sig.direction, sig.entry, sig.sl, sig.tp1, sig.tp2)
    eng.signals[sig.id] = sig   # register as ACTIVE; execute() only trades scan-owned signals
    r = eng.execute(sig.id)
    print("paper execute:", r.get("ok"), r.get("error", ""))

print("== 3. HTTP API ==")
httpd = ThreadingHTTPServer(("127.0.0.1", PORT), srv_mod.Handler)
th = threading.Thread(target=httpd.serve_forever, daemon=True)
th.start()
time.sleep(0.3)
r = requests.get(f"http://127.0.0.1:{PORT}/api/state", timeout=5)
d = r.json()
print("GET /api/state:", r.status_code, "engine:", d["engine"], "scans:", d["scanCount"])
r = requests.get(f"http://127.0.0.1:{PORT}/api/chart?symbol=BTCUSDT&tf=15m", timeout=10)
c = r.json()
print("GET /api/chart:", r.status_code, "candles:", len(c.get("candles", [])),
      "events:", len(c.get("events", [])), "src:", c.get("source"))
r = requests.get(f"http://127.0.0.1:{PORT}/", timeout=5)
print("GET /:", r.status_code, "bytes:", len(r.content))
r = requests.post(f"http://127.0.0.1:{PORT}/api/kill", json={"on": True}, timeout=5)
print("POST /api/kill:", r.json())
r = requests.post(f"http://127.0.0.1:{PORT}/api/kill", json={"on": False}, timeout=5)
print("POST /api/kill off:", r.json())
httpd.shutdown()
print("ALL E2E CHECKS DONE")
