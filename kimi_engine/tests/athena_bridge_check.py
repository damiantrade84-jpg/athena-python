"""Validate KIMI Engine ↔ Athena bridge: register routes on a bare Flask app
and exercise the /kimi endpoints exactly as athena.py would expose them.
Run with the project venv:  .venv/Scripts/python.exe tests/athena_bridge_check.py
"""
from __future__ import annotations

import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parent.parent.parent  # athena-python/
sys.path.insert(0, str(ROOT))

from flask import Flask  # noqa: E402

app = Flask(__name__)
from tools.kimi_bridge import register_kimi_routes  # noqa: E402
register_kimi_routes(app)

client = app.test_client()

print("== existing bridge routes still work ==")
r = client.get("/api/kimi/health")
print("GET /api/kimi/health:", r.status_code, r.get_json().get("status"))

print("== KIMI Engine routes ==")
t0 = time.time()
r = client.get("/kimi/")
html = r.get_data(as_text=True)
print("GET /kimi/:", r.status_code, "| KIMI_BASE injected:", 'window.KIMI_BASE = "/kimi"' in html)

r = client.get("/kimi")
print("GET /kimi redirect:", r.status_code, r.headers.get("Location"))

r = client.get("/kimi/app.js")
print("GET /kimi/app.js:", r.status_code, "bytes:", len(r.get_data()))
r = client.get("/kimi/styles.css")
print("GET /kimi/styles.css:", r.status_code, "bytes:", len(r.get_data()))

# engine boot thread needs a moment to come up
for _ in range(30):
    r = client.get("/kimi/api/state")
    d = r.get_json()
    if d.get("running"):
        break
    time.sleep(2)
print("GET /kimi/api/state:", r.status_code, "| engine:", d.get("engine"),
      "| running:", d.get("running"), "| scans:", d.get("scanCount"),
      f"(boot {time.time() - t0:.0f}s)")

r = client.get("/kimi/api/chart?symbol=BTCUSDT&tf=15m")
c = r.get_json()
print("GET /kimi/api/chart:", r.status_code, "| candles:", len(c.get("candles", [])),
      "| events:", len(c.get("events", [])), "| src:", c.get("source"))

r = client.post("/kimi/api/kill", json={"on": True})
print("POST /kimi/api/kill:", r.get_json())
r = client.post("/kimi/api/kill", json={"on": False})
print("POST /kimi/api/kill off:", r.get_json())

r = client.post("/kimi/api/config", json={"minScore": 78})
print("POST /kimi/api/config:", r.get_json())
r = client.post("/kimi/api/config", json={"minScore": 75})

sig = (client.get("/kimi/api/state").get_json().get("signals") or [])
if sig:
    r = client.post("/kimi/api/execute", json={"id": sig[0]["id"], "venue": "paper"})
    print("POST /kimi/api/execute:", r.get_json().get("ok"), r.get_json().get("venue"))
else:
    print("no live signal this scan — execute path already proven in engine tests")

print("ATHENA BRIDGE CHECK DONE")
