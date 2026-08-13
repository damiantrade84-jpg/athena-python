"""Per-sleeve edge attribution for ASE candidates.

Answers one question: is each alpha sleeve (tsmom / carry / xsec / meanrev)
carrying directional information, or is the meta-model being handed noise?

Read-only. Consumes the existing labeled candidate set (phase1_events.parquet)
and touches no scoring, label, schema, or execution path.

Method and its limit
--------------------
``net_R`` is realized along the *candidate* direction chosen by arbitration,
not along each sleeve's own direction. So:

* AGREE rows (sleeve direction == candidate direction) are the sleeve's own
  realized outcome, and are the honest measure of its edge.
* OPPOSE rows are the outcome of trading *against* the sleeve. These are NOT
  negated to infer the sleeve's edge: the triple barrier is directional, so
  flipping direction changes which barrier is struck first. They are reported
  as "what happened when this sleeve was overruled", nothing more.
* SOLO rows are candidates where this sleeve was the only one that fired —
  the cleanest available read on the sleeve standing alone.

A sleeve with a real edge should show AGREE expectancy meaningfully above the
family/horizon baseline, and SOLO expectancy above zero net of costs.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

DEFAULT_EVENTS = Path(__file__).resolve().parent / "output" / "phase1_events.parquet"
SLEEVES = ("tsmom", "carry", "xsec", "meanrev")


def _metrics(net_r: np.ndarray, gross_r: np.ndarray | None = None) -> dict[str, Any]:
    """Expectancy block. SQN100 caps N at 100 so a large sample cannot inflate it."""
    r = np.asarray(net_r, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 2:
        return {"n": n}
    mean = float(r.mean())
    std = float(r.std(ddof=1))
    out: dict[str, Any] = {
        "n": n,
        "exp_R": mean,
        "std": std,
        "sqn100": mean / std * math.sqrt(min(n, 100)) if std > 0 else float("nan"),
        "win": float((r > 0).mean()),
    }
    if gross_r is not None:
        g = np.asarray(gross_r, dtype=float)
        g = g[np.isfinite(g)]
        if len(g):
            out["gross_R"] = float(g.mean())
    return out


def explode_signals(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (candidate, fired sleeve) with the sleeve's own direction."""
    rows: list[dict[str, Any]] = []
    for idx, raw, direction in zip(
        df.index, df["primary_signals"].to_numpy(), df["direction"].to_numpy()
    ):
        try:
            signals = json.loads(raw or "[]")
        except (TypeError, ValueError):
            continue
        for signal in signals:
            name = str(signal.get("name") or "")
            if not name:
                continue
            rows.append(
                {
                    "row": idx,
                    "sleeve": name,
                    "sleeve_dir": int(signal.get("direction") or 0),
                    "strength": float(signal.get("rawStrength") or 0.0),
                    "candidate_dir": int(direction),
                    "fired_count": len(signals),
                }
            )
    return pd.DataFrame(rows)


def _fmt(label: str, m: dict[str, Any], width: int = 34) -> str:
    if m.get("n", 0) < 2:
        return f"  {label:<{width}} n={m.get('n', 0):>5}  (insufficient)"
    gross = f"  gross={m['gross_R']:+.3f}" if "gross_R" in m else ""
    return (
        f"  {label:<{width}} n={m['n']:>5}  exp={m['exp_R']:+.3f}R  "
        f"SQN100={m['sqn100']:+5.2f}  win={m['win'] * 100:4.1f}%{gross}"
    )


def report(
    events: pd.DataFrame,
    *,
    sleeves: Iterable[str] = SLEEVES,
    min_n: int = 30,
) -> None:
    exploded = explode_signals(events)
    if exploded.empty:
        print("no sleeve signals found in the event set")
        return

    net = events["net_R"]
    gross = events["gross_R"] if "gross_R" in events.columns else None

    print(f"ASE per-sleeve edge attribution — {len(events)} labeled candidates")
    print(f"min_n={min_n} for a reportable cell. SQN100 caps N at 100.\n")

    overall = _metrics(net.to_numpy(), None if gross is None else gross.to_numpy())
    print(_fmt("ALL CANDIDATES (baseline)", overall))
    print()

    for (family, horizon), group in events.groupby(["family", "horizon"], sort=True):
        sub = exploded[exploded["row"].isin(group.index)]
        base = _metrics(
            group["net_R"].to_numpy(),
            None if gross is None else group["gross_R"].to_numpy(),
        )
        print("=" * 104)
        print(f"{family} / {horizon}")
        print("=" * 104)
        print(_fmt("baseline (all candidates)", base))

        available = sorted(sub["sleeve"].unique().tolist())
        print(f"  sleeves that ever fire here: {', '.join(available) or 'none'}")

        for sleeve in sleeves:
            cells = sub[sub["sleeve"] == sleeve]
            if cells.empty:
                continue
            agree_rows = cells.loc[cells["sleeve_dir"] == cells["candidate_dir"], "row"]
            oppose_rows = cells.loc[
                (cells["sleeve_dir"] != 0)
                & (cells["sleeve_dir"] != cells["candidate_dir"]),
                "row",
            ]
            solo_rows = cells.loc[
                (cells["fired_count"] == 1)
                & (cells["sleeve_dir"] == cells["candidate_dir"]),
                "row",
            ]
            for label, rows in (
                (f"{sleeve} AGREE", agree_rows),
                (f"{sleeve} SOLO", solo_rows),
                (f"{sleeve} OPPOSE (overruled)", oppose_rows),
            ):
                selected = group.loc[group.index.intersection(rows)]
                if len(selected) < min_n:
                    continue
                print(
                    _fmt(
                        label,
                        _metrics(
                            selected["net_R"].to_numpy(),
                            None if gross is None else selected["gross_R"].to_numpy(),
                        ),
                    )
                )
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--events", default=str(DEFAULT_EVENTS))
    parser.add_argument("--min-n", type=int, default=30)
    args = parser.parse_args()

    path = Path(args.events)
    if not path.exists():
        print(f"event set not found: {path}")
        return 1
    report(pd.read_parquet(path), min_n=args.min_n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
