"""
score_forecast.py — grade the 2026 forecast against the results.

Run after Election Day (November 3, 2026), once Wikipedia's Senate and House
election articles show results. It can be re-run as late races are called;
undecided races are listed and left out of the score.

What gets graded: each race's forecast from the Election Day build, saved in
data/race_forecast_history.csv (the daily run happens hours before polls close).
The prediction markets' Election Day prices for the same races are graded the
same way, as a comparison. See scoring.py for what each number means.

    python score_forecast.py            # prints a report
Writes data/score_2026.json (for a results page) and data/results_2026.csv.
"""

import json
import sys

import pandas as pd

import fetch_midterms
import forecast_model
import midterms
import scoring

FORECAST_DATE = forecast_model.ELECTION_2026.strftime("%Y-%m-%d")


def election_day_forecast() -> pd.DataFrame:
    h = pd.read_csv(forecast_model.RACE_HISTORY_CSV, dtype={"date": str})
    day = h.loc[h["date"] <= FORECAST_DATE, "date"].max()
    if day != FORECAST_DATE:
        print(f"Note: no forecast saved for {FORECAST_DATE}; grading the last one before it ({day}).")
    return h[h["date"] == day].copy(), day


def results_2026() -> pd.DataFrame:
    races, _ = fetch_midterms.senate(2026, with_polls=False)
    districts, _ = fetch_midterms.house(2026)
    rows = []
    for chamber, items, key in (("senate", races, "state"), ("house", districts, "district")):
        for r in items:
            o = scoring.race_outcome(r["results"])
            rows.append({"chamber": chamber, "race": r[key],
                         "dem_won": None if o is None else o["dem_won"],
                         "margin": None if o is None else o["margin"]})
    return pd.DataFrame(rows)


def main() -> None:
    fc, day = election_day_forecast()
    res = results_2026()
    res.to_csv("data/results_2026.csv", index=False)
    df = fc.merge(res, on=["chamber", "race"], how="left")
    pending = df[df["dem_won"].isna()]
    done = df[df["dem_won"].notna()].copy()
    done["dem_won"] = done["dem_won"].astype(bool)
    if done.empty:
        sys.exit("No results yet: Wikipedia's 2026 articles don't show vote shares. Try again after races are called.")

    report = {"forecast_date": day, "chambers": {}}
    for chamber in ("senate", "house"):
        d = done[done["chamber"] == chamber]
        if d.empty:
            continue
        s = scoring.score(d)
        # Biggest surprises: lowest chance the forecast gave the side that actually won
        d = d.assign(p_winner=d["p_dem"].where(d["dem_won"], 1 - d["p_dem"]))
        s["surprises"] = d.nsmallest(5, "p_winner")[["race", "p_dem", "p_winner", "dem_won", "median", "margin"]].to_dict("records")
        s["pending"] = pending.loc[pending["chamber"] == chamber, "race"].tolist()
        report["chambers"][chamber] = s
        print(scoring.describe(s, f"\n{chamber.title()} ({len(d)} races decided, {len(s['pending'])} pending)"))
        for x in s["surprises"]:
            print(f"    surprise: {x['race']}: the forecast gave the winner {x['p_winner']:.0%}")

    # Control of each chamber, once every race is decided
    ctrl = pd.read_csv(forecast_model.HISTORY_CSV, dtype={"date": str})
    ctrl = ctrl[ctrl["date"] == day].set_index("race")
    for chamber, seats_not_up, need in (("senate", midterms.SENATE_NOT_UP["D"], 51), ("house", 0, 218)):
        d = df[df["chamber"] == chamber]
        if d["dem_won"].isna().any() or chamber not in ctrl.index:
            continue
        dem_seats = seats_not_up + int(d["dem_won"].astype(bool).sum())
        p = float(ctrl.loc[chamber, "p_dem"])
        report["chambers"][chamber]["control"] = {"dem_seats": dem_seats, "dem_controls": dem_seats >= need,
                                                  "forecast_p_dem": p, "forecast_median": int(ctrl.loc[chamber, "median"])}
        print(f"\n{chamber.title()} control: Democrats won {dem_seats} seats "
              f"({'control' if dem_seats >= need else 'no control'}); forecast gave them {p:.0%}, "
              f"median {int(ctrl.loc[chamber, 'median'])} seats")

    with open("data/score_2026.json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, default=lambda o: None if pd.isna(o) else o)
    print("\nWrote data/score_2026.json and data/results_2026.csv")


if __name__ == "__main__":
    main()
