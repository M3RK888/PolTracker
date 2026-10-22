"""
scoring.py — how good were the forecast's probabilities?

Shared by score_forecast.py (grades 2026 after the election) and backtest.py
(runs the model on past elections and grades it). Plain-language guide:

  Called right     share of races where the side given over 50% won. Easy to look
                   good on, since most races aren't close, so it's also reported
                   for competitive races only (5% to 95%).
  Brier score      average of (probability - outcome)^2, with outcome 1 if the
                   Democratic side won and 0 if not. 0 is perfect; always saying
                   50% scores 0.25. Lower is better.
  Log loss         like Brier, but punishes confident misses much harder (a 99%
                   call that loses costs a lot). Lower is better.
  Calibration      group races by forecast probability and check how often that
                   side actually won. Races given 70-80% should be won about 75%
                   of the time; much more often means the model was too timid,
                   much less often means it was overconfident.
  Margin bias      average of (actual margin - forecast margin). Positive means
                   Democrats did better than forecast, like a polling miss.
  80% range hits   share of results inside the forecast's 80% range. About 80%
                   means the stated uncertainty was right; much less means it was
                   too narrow.
"""

import math

import numpy as np
import pandas as pd

CALIBRATION_BINS = [0.0, 0.05, 0.25, 0.4, 0.6, 0.75, 0.95, 1.0]


def race_outcome(results: list[tuple[str, str, float | None, bool]]) -> dict | None:
    """Turn a race's candidate results [(name, party, share, won)] into
    {dem_won, margin, two_way}: the Democratic side is the strongest non-Republican (an
    independent like Dan Osborn counts), margin is their share minus the
    Republican's. Ranked-choice races list each round; the last round counts.
    Returns None if the race has no results yet."""
    final = {}
    for name, party, share, won in results:
        if share is not None:
            final[name] = (party, share, won)        # later rounds overwrite earlier ones
    if not final:
        return None
    rep = max((s for p, s, _ in final.values() if p == "R"), default=None)
    opp = max((s for p, s, _ in final.values() if p != "R"), default=None)
    winners = [p for p, _, w in final.values() if w]
    if winners:
        dem_won = winners[-1] != "R"
    else:
        dem_won = (opp or 0) > (rep or 0)
    margin = None if rep is None or opp is None else round(opp - rep, 2)   # None: no R (or no D-side) on the ballot
    # Not a two-way race if two candidates on the same side each got 10%+ (Louisiana's
    # all-party ballots, Alaska 2022's two Republicans): the margin isn't comparable then.
    sides = ["R" if p == "R" else "D" for p, s, _ in final.values() if s >= 10]
    two_way = margin is not None and sides.count("R") == 1 and sides.count("D") == 1
    return {"dem_won": dem_won, "margin": margin if two_way else None, "two_way": two_way}


def score(df: pd.DataFrame) -> dict:
    """df columns: p_dem, dem_won (bool); optional: median, lo, hi, margin (actual),
    market_p. Returns the numbers described at the top of this file."""
    df = df.dropna(subset=["p_dem", "dem_won"])
    o = df["dem_won"].astype(float).to_numpy()
    p = df["p_dem"].to_numpy(dtype=float)
    pc = np.clip(p, 0.01, 0.99)
    out = {
        "races": int(len(df)),
        "called_right": round(float(((p > 0.5) == (o == 1)).mean()), 4),
        "brier": round(float(((p - o) ** 2).mean()), 4),
        "log_loss": round(float(-(o * np.log(pc) + (1 - o) * np.log(1 - pc)).mean()), 4),
    }
    close = (p > 0.05) & (p < 0.95)
    out["competitive"] = {
        "races": int(close.sum()),
        "called_right": round(float(((p[close] > 0.5) == (o[close] == 1)).mean()), 4) if close.any() else None,
        "brier": round(float(((p[close] - o[close]) ** 2).mean()), 4) if close.any() else None,
    }
    bins = []
    for lo, hi in zip(CALIBRATION_BINS, CALIBRATION_BINS[1:]):
        m = (p >= lo) & ((p < hi) if hi < 1 else (p <= hi))
        if m.any():
            bins.append({"from": lo, "to": hi, "races": int(m.sum()),
                         "forecast": round(float(p[m].mean()), 3), "dem_won": round(float(o[m].mean()), 3)})
    out["calibration"] = bins

    if {"median", "margin"} <= set(df.columns):
        mm = df.dropna(subset=["median", "margin"])
        err = mm["margin"] - mm["median"]
        out["margin"] = {"races": int(len(mm)), "bias": round(float(err.mean()), 2),
                         "mean_abs_error": round(float(err.abs().mean()), 2)}
        if {"lo", "hi"} <= set(df.columns):
            inside = (mm["margin"] >= mm["lo"]) & (mm["margin"] <= mm["hi"])
            out["margin"]["in_80pct_range"] = round(float(inside.mean()), 3)

    if "market_p" in df.columns:
        mk = df.dropna(subset=["market_p"])
        if len(mk):
            om, pm, pf = mk["dem_won"].astype(float), mk["market_p"].astype(float), mk["p_dem"].astype(float)
            out["vs_markets"] = {"races": int(len(mk)),
                                 "forecast_brier": round(float(((pf - om) ** 2).mean()), 4),
                                 "market_brier": round(float(((pm - om) ** 2).mean()), 4)}
    return out


def t_logpdf(x: np.ndarray, sd: float, df: int = 5) -> np.ndarray:
    """Log density of a Student-t with standard deviation sd (the model's error shape)."""
    scale = sd / math.sqrt(df / (df - 2))
    z = x / scale
    return (math.lgamma((df + 1) / 2) - math.lgamma(df / 2) - 0.5 * math.log(df * math.pi)
            - np.log(scale) - (df + 1) / 2 * np.log1p(z ** 2 / df))


def describe(s: dict, title: str) -> str:
    """A short plain-text report of score()'s numbers."""
    lines = [title, f"  Races: {s['races']}; called right: {s['called_right']:.0%}; "
                    f"Brier {s['brier']:.3f} (always-50% scores 0.250); log loss {s['log_loss']:.3f}"]
    c = s["competitive"]
    if c["races"]:
        lines.append(f"  Competitive races (5-95%): {c['races']}; called right {c['called_right']:.0%}; Brier {c['brier']:.3f}")
    if "margin" in s:
        m = s["margin"]
        lines.append(f"  Margin: bias {m['bias']:+.1f} (positive = Democrats beat the forecast), "
                     f"average miss {m['mean_abs_error']:.1f} pts"
                     + (f", inside the 80% range {m['in_80pct_range']:.0%}" if "in_80pct_range" in m else ""))
    lines.append("  Calibration (forecast D chance -> how often D side won):")
    for b in s["calibration"]:
        lines.append(f"    {b['from']:.0%}-{b['to']:.0%}: {b['races']:3} races, forecast {b['forecast']:.0%}, won {b['dem_won']:.0%}")
    if "vs_markets" in s:
        v = s["vs_markets"]
        lines.append(f"  Against the markets ({v['races']} races): forecast Brier {v['forecast_brier']:.3f}, "
                     f"markets {v['market_brier']:.3f}")
    return "\n".join(lines)
