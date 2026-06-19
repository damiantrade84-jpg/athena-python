import json, glob, os, statistics
from datetime import datetime, timezone

EXP = {"D1":86400, "H4":14400, "H1":3600}
def parse(t): return datetime.fromisoformat(t.replace("Z","+00:00"))

rows=[]
for f in sorted(glob.glob("data/frozen/2026-05-30/candles/*.json")):
    base=os.path.basename(f)[:-5]
    sym, tf, prov = base.split("__")
    d=json.load(open(f,encoding="utf-8"))
    if not isinstance(d,list): d=d.get("candles") or d.get("rows") or []
    ts=[parse(c.get("time") or c.get("datetime")) for c in d]
    n=len(ts)
    dup=sum(1 for i in range(1,n) if ts[i]==ts[i-1])
    # gap estimate vs expected step (count missing slots, ignoring weekends crudely)
    exp=EXP.get(tf)
    gaps=0
    if exp and n>1:
        for i in range(1,n):
            dt=(ts[i]-ts[i-1]).total_seconds()
            if dt>exp*1.5: gaps+=1
    span_days=(ts[-1]-ts[0]).total_seconds()/86400 if n>1 else 0
    volzero=sum(1 for c in d if not c.get("vol"))
    rows.append((sym,tf,prov,n,ts[0].date().isoformat(),ts[-1].date().isoformat(),round(span_days/365.25,2),dup,gaps,volzero))

print(f"{'symbol':14}{'tf':4}{'prov':22}{'n':>6}{'first':>12}{'last':>12}{'yrs':>6}{'dup':>5}{'gapruns':>8}{'vol0':>6}")
for r in rows:
    print(f"{r[0]:14}{r[1]:4}{r[2]:22}{r[3]:>6}{r[4]:>12}{r[5]:>12}{r[6]:>6}{r[7]:>5}{r[8]:>8}{r[9]:>6}")

# group summary
from collections import defaultdict
syms=defaultdict(set)
for r in rows: syms[r[0]].add(r[1])
print("\nsymbols:",len(syms))
print("tfs present per symbol sample:", {s:sorted(t) for s,t in list(syms.items())[:3]})
