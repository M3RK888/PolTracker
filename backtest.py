"""
backtest.py — run the forecast model on past Senate elections and grade it.

For each past cycle it rebuilds the Senate forecast as it would have looked a
set number of days before Election Day, using only information available then,
and compares it with what actually happened.

What it uses, per race (all from Wikipedia's race articles, cached in data/.cache):
  polls      the race's general-election polls whose fieldwork had ended by that
             day, weighted and blended exactly as midterms.py does today
  lean       the state's partisan lean, from the two previous presidential
             elections (state margin minus national margin, averaged). Past
             articles don't list Cook's PVI, so this stands in for it.
  national   the national environment for the baseline: what that year's Senate
  mood       polls implied (each polled race's average minus its lean, then the
             middle value), since past generic-ballot averages aren't on Wikipedia
  results    the vote shares from each year's Senate elections article

What it can't test: prediction markets and the approval-based fundamentals (no
past data), so here polls carry all the weight. Today, 12 days out, the live
model gives polls 83%, so this covers most of what drives it.

The uncertainty settings (national, regional and race-level misses) are the
live model's, from forecast_model.py, so this checks whether they were sized right.

    python backtest.py          # prints a report; first run fetches ~140 articles (a few minutes)
Writes data/backtest_races.csv (every race, every cycle) and data/backtest_summary.json.
"""

import json

import numpy as np
import pandas as pd

import fetch_midterms
import forecast_model as model
import midterms
import scoring

CYCLES = {2018: "2018-11-06", 2020: "2020-11-03", 2022: "2022-11-08", 2024: "2024-11-05"}
DAYS_OUT = [12, 1]          # as of today's distance from Election Day, and Election Eve
RESULTS_CSV = "data/results_1960_2024.csv"


def state_lean(year: int) -> dict[str, float]:
    """Average of (state margin - national margin) over the two presidential
    elections before `year`, in points of margin (Democratic positive)."""
    res = pd.read_csv(RESULTS_CSV)
    res = res[~res["is_district"]]
    pres_years = sorted(y for y in res["year"].unique() if y < year)[-2:]
    leans = []
    for y in pres_years:
        r = res[res["year"] == y]
        nat = (r["dem_votes"].sum() - r["rep_votes"].sum()) / r["total_votes"].sum() * 100
        leans.append(((r["dem_votes"] - r["rep_votes"]) / r["total_votes"] * 100 - nat).set_axis(r["state"]))
    return pd.concat(leans, axis=1).mean(axis=1).to_dict()


def forecast_cycle(races: list[dict], polls: pd.DataFrame, lean: dict, election: pd.Timestamp, days_out: int) -> tuple[list[dict], float]:
    as_of = election - pd.Timedelta(days=days_out)
    midterms.AS_OF = model.AS_OF = as_of
    p = polls[(polls["end_date"] <= as_of) & (polls["end_date"] > as_of - pd.Timedelta(days=midterms.WINDOW_DAYS))].copy()
    p["state"] = p["race"]                           # poll_weights groups by "state": one group per race
    p["w"] = midterms.poll_weights(p) if len(p) else []

    # National environment implied by the polls (see the top of this file)
    implied = [np.average(g["margin"], weights=g["w"]) - lean[g["race"].iloc[0][:2]]
               for _, g in p.groupby("race") if g["w"].sum() > 0]
    env = float(np.median(implied)) if implied else 0.0

    out = []
    for r in races:
        key = r["state"] + ("-special" if r["special"] else "")
        baseline = env + lean[r["state"]]
        rp = p[p["race"] == key]
        total = midterms.PRIOR_WEIGHT + rp["w"].sum()
        estimate = (midterms.PRIOR_WEIGHT * baseline + (rp["w"] * rp["margin"]).sum()) / total
        out.append({"key": key, "region_state": r["state"], "polls": float(estimate), "fundamentals": None,
                    "market": None, "n_polls": int(len(rp)), "baseline": baseline})
    out, _, sig = model.forecast_races(out, days_out, 4.0)
    for r in out:
        r["sigma_total"] = sig["total"]
    return out, env


def main() -> None:
    rows, envs = [], {}
    for year, day in CYCLES.items():
        election = pd.Timestamp(day)
        races, polls = fetch_midterms.senate(year, refresh=False)
        polls = pd.DataFrame(polls)
        polls["end_date"] = pd.to_datetime(polls["end_date"])
        polls["sample_size"] = pd.to_numeric(polls["sample_size"], errors="coerce")
        polls["sample_size"] = polls["sample_size"].fillna(polls["sample_size"].median())
        polls["margin"] = polls["dem"] - polls["rep"]
        lean = state_lean(year)
        outcome = {r["state"] + ("-special" if r["special"] else ""): scoring.race_outcome(r["results"]) for r in races}
        for days_out in DAYS_OUT:
            fc, env = forecast_cycle(races, polls, lean, election, days_out)
            envs[f"{year} ({days_out} days out)"] = round(env, 2)
            for r in fc:
                o = outcome.get(r["key"])
                if o is None:
                    continue
                rows.append({"year": year, "days_out": days_out, "race": r["key"], "n_polls": r["n_polls"],
                             "p_dem": round(r["p_dem"], 4), "median": round(r["median"], 2), "lo": round(r["lo"], 2),
                             "hi": round(r["hi"], 2), "blended": round(r["blended"], 2), "sigma_total": r["sigma_total"],
                             "dem_won": o["dem_won"], "margin": o["margin"]})
    df = pd.DataFrame(rows)
    df.to_csv("data/backtest_races.csv", index=False)

    summary = {"environment_implied_by_polls": envs, "by_cycle": {}, "pooled": {}, "uncertainty": {}}
    for days_out in DAYS_OUT:
        d = df[df["days_out"] == days_out]
        s = scoring.score(d)
        summary["pooled"][days_out] = s
        print(scoring.describe(s, f"\nAll four cycles, {days_out} day{'s' if days_out != 1 else ''} before Election Day"))
        for year in CYCLES:
            sy = scoring.score(d[d["year"] == year])
            summary["by_cycle"][f"{year}/{days_out}"] = sy
            m = sy.get("margin", {})
            print(f"  {year}: {sy['races']} races, called right {sy['called_right']:.0%}, Brier {sy['brier']:.3f}, "
                  f"bias {m.get('bias', float('nan')):+.1f}, in 80% range {m.get('in_80pct_range', float('nan')):.0%}")

        # Was the uncertainty sized right? Find the multiple of the model's spread that
        # best explains how far results actually landed from the forecast.
        e = (d["margin"] - d["blended"]).dropna().to_numpy()
        sd = float(d["sigma_total"].iloc[0])
        ks = np.arange(0.4, 2.01, 0.05)
        ll = [scoring.t_logpdf(e, k * sd).mean() for k in ks]
        best = float(ks[int(np.argmax(ll))])
        summary["uncertainty"][days_out] = {"model_sd": round(sd, 2), "best_multiple": round(best, 2),
                                            "actual_sd": round(float(e.std()), 2)}
        print(f"  Uncertainty: the model's total spread is {sd:.1f} pts; results fit best at {best:.2f}x that "
              f"({'about right' if 0.85 <= best <= 1.15 else 'too wide' if best < 1 else 'too narrow'}). "
              f"Actual misses had a spread of {e.std():.1f} pts.")

    print("\nNational environment implied by each year's polls:", envs)
    with open("data/backtest_summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, default=str)
    print("Wrote data/backtest_races.csv and data/backtest_summary.json")


if __name__ == "__main__":
    main()
