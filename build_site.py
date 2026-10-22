"""
build_site.py — builds the data behind the website in site/.

The pages themselves (site/*.html, site/assets/) are hand-written and don't
change when polls do. This script regenerates only site/data/*.js:

    forecast.js   2028 estimates for every matchup in data/polls_2028.csv
    history.js    actual results, 1960-2024, from data/results_1960_2024.csv
    polls.js      every 2028 poll, with how much it counts and why
    senate.js     2026 Senate estimates and polls (from fetch_midterms.py's CSVs)
    house.js      2026 House estimates by district
    forecast_2026.js  2026 win probabilities (forecast_model.py; needs data/markets.csv
                  and data/approval.csv from fetch_markets.py)
    forecast_2028.js  2028 win probabilities per matchup (also needs data/economy.csv)
                  Both also carry the day-by-day history from data/forecast_history.csv,
                  which each build adds today's numbers to.

The data is written as small JS files (window.X = {...}) rather than JSON so
the pages work when opened straight from disk, with no local server needed.

Run after editing data/polls_2028.csv:
    python build_site.py
Then open site/index.html, or publish the site/ folder.
"""

import json
from pathlib import Path

import numpy as np
import pandas as pd

import forecast_model
import midterms
import polling_2028 as model

SITE_DATA = Path("site/data")
HISTORY_CSV = Path("data/results_1960_2024.csv")

# Bands for past results, by winning margin in points
RESULT_BANDS = [(15, "15+"), (5, "5–15"), (0, "under 5")]


def write_js(name: str, var: str, payload: dict) -> None:
    SITE_DATA.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False, default=_json_default)
    (SITE_DATA / f"{name}.js").write_text(f"window.{var} = {text};\n", encoding="utf-8")


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if np.isnan(o) else round(float(o), 3)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.strftime("%Y-%m-%d")
    raise TypeError(type(o))


def r2(x) -> float:
    return round(float(x), 2)


# ---------------------------------------------------------------------------
# 2028 forecast + polls
# ---------------------------------------------------------------------------
def build_forecast(polls: pd.DataFrame) -> tuple[dict, dict]:
    pairs = (polls.groupby(["dem_candidate", "rep_candidate"]).size()
             .reset_index(name="n").sort_values("n", ascending=False))
    matchups, scored = [], []

    for _, p in pairs.iterrows():
        dem, rep = p["dem_candidate"], p["rep_candidate"]
        mid = f"{dem}-{rep}".lower().replace(" ", "-")
        all_polls = polls[(polls["dem_candidate"] == dem) & (polls["rep_candidate"] == rep)]
        try:
            window, nat_avg, n_nat = model.matchup_polls(polls, dem, rep)
            est, s = model.state_estimates(polls, dem, rep)
        except ValueError:
            latest = all_polls["end_date"].max()
            matchups.append({
                "id": mid, "dem": dem, "rep": rep, "available": False,
                "n_polls": len(all_polls),
                "reason": (f"No national polls in the last {model.WINDOW_DAYS} days "
                           f"(latest ended {latest:%b} {latest.day}, {latest.year})" if pd.notna(latest)
                           else "No national polls yet"),
            })
            continue

        totals = model.ev_totals(est)
        matchups.append({
            "id": mid, "dem": dem, "rep": rep, "available": True,
            "n_polls": len(all_polls), "n_national": n_nat,
            "n_state_polls": int((window["state"] != "US").sum()),
            "national_avg": r2(nat_avg), "national_2024": r2(s["national_2024"]), "swing": r2(s["swing"]),
            "ev": {"D": totals["D"], "R": totals["R"], "Tossup": totals["Tossup"]},
            "segments": [{"rating": r, "ev": totals[r]} for r in model.RATING_COLORS],
            "states": [{
                "state": r["state"], "name": r["state_name"], "ev": r["ev"],
                "margin_2024": r2(r["margin_2024"]), "baseline": r2(r["baseline"]),
                "estimate": r2(r["estimate"]), "rating": r["rating"], "n_state_polls": r["n_state_polls"],
            } for _, r in est.iterrows()],
        })
        scored.append(window.assign(nat_avg=nat_avg))

    # How much each poll counts. National polls: share of the national average.
    # State polls: share of that state's estimate (the 2024 baseline holds the rest).
    used = pd.concat(scored) if scored else pd.DataFrame(columns=["w"])
    if not used.empty:
        key = ["dem_candidate", "rep_candidate", "state"]
        group_w = used.groupby(key)["w"].transform("sum")
        is_nat = used["state"] == "US"
        used["share"] = np.where(is_nat, used["w"] / group_w, used["w"] / (group_w + model.PRIOR_WEIGHT))

    oldest_used = model.AS_OF - pd.Timedelta(days=model.WINDOW_DAYS)
    rows = []
    for i, p in polls.sort_values("end_date", ascending=False).iterrows():
        u = used.loc[i] if i in used.index else None
        if u is not None:
            status = "Used"
        elif p["end_date"] <= oldest_used:
            status = "Too old"
        elif p["end_date"] > model.AS_OF:
            status = "Not yet counted"
        else:
            status = "No national poll"
        rows.append({
            "state": p["state"],
            "state_name": "National" if p["state"] == "US" else model.STATE_NAMES.get(p["state"], p["state"]),
            "pollster": p["pollster"],
            "start": p["start_date"] if pd.notna(p["start_date"]) else None,
            "end": p["end_date"],
            "sample_size": int(p["sample_size"]) if p["sample_size_reported"] else None,
            "voter_type": p["voter_type"] if pd.notna(p["voter_type"]) else None,
            "dem_candidate": p["dem_candidate"], "rep_candidate": p["rep_candidate"],
            "dem": r2(p["dem"]), "rep": r2(p["rep"]), "margin": r2(p["margin"]),
            "adjusted": r2(u["margin"]) if u is not None and p["state"] != "US" else None,
            "weight": round(float(u["w"]), 3) if u is not None else None,
            "share": round(float(u["share"]), 3) if u is not None else None,
            "status": status,
            "source": p["source"] if pd.notna(p["source"]) else None,
        })

    default = next((m["id"] for m in matchups if m["available"]), None)
    forecast = {
        "as_of": model.AS_OF, "default": default, "matchups": matchups,
        "ratings": [{"name": r, "color": c} for r, c in model.RATING_COLORS.items()],
        "settings": {"half_life_days": model.HALF_LIFE_DAYS, "window_days": model.WINDOW_DAYS,
                     "prior_weight": model.PRIOR_WEIGHT,
                     "cutoffs": [{"min": c, "label": l} for c, l in model.RATING_CUTOFFS]},
    }
    poll_data = {"as_of": model.AS_OF, "window_days": model.WINDOW_DAYS,
                 "half_life_days": model.HALF_LIFE_DAYS, "polls": rows}
    return forecast, poll_data


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------
def band(margin: float) -> str:
    return next(label for cutoff, label in RESULT_BANDS if abs(margin) >= cutoff)


def build_history() -> dict:
    res = pd.read_csv(HISTORY_CSV)
    res["margin"] = (res["dem_votes"] - res["rep_votes"]) / res["total_votes"] * 100
    res["top_other_pct"] = res["top_other_votes"] / res["total_votes"] * 100
    years = []
    for year, y in res.groupby("year"):
        states = y[~y["is_district"]]
        nat = states[["dem_votes", "rep_votes", "total_votes"]].sum()

        # EV bar: each candidate's EVs, grouped by how big the margin was where they won them
        seg = {f"D {b}": 0 for _, b in RESULT_BANDS} | {"Other": 0} | {f"R {b}": 0 for _, b in reversed(RESULT_BANDS)}
        for _, u in y.iterrows():
            seg[f"D {band(u['margin'])}"] += u["ev_dem"]
            seg[f"R {band(u['margin'])}"] += u["ev_rep"]
            seg["Other"] += u["ev_other"]

        units = []
        for _, u in y.iterrows():
            top = max(u["dem_votes"], u["rep_votes"], u["top_other_votes"])
            winner = "D" if top == u["dem_votes"] else "R" if top == u["rep_votes"] else "O"
            units.append({
                "unit": u["unit"], "state": u["state"], "district": bool(u["is_district"]),
                "name": (model.STATE_NAMES[u["state"]] + (f" {u['unit'][3:]}{ordinal(u['unit'][3:])} district"
                                                          if u["is_district"] else "")),
                "dem_votes": u["dem_votes"], "rep_votes": u["rep_votes"], "total": u["total_votes"],
                "dem_pct": r2(u["dem_votes"] / u["total_votes"] * 100),
                "rep_pct": r2(u["rep_votes"] / u["total_votes"] * 100),
                "margin": r2(u["margin"]), "winner": winner,
                "ev_total": u["ev_total"], "ev_dem": u["ev_dem"], "ev_rep": u["ev_rep"], "ev_other": u["ev_other"],
                "top_other": u["top_other"] if isinstance(u["top_other"], str) else None,
                "top_other_pct": r2(u["top_other_pct"]),
            })

        years.append({
            "year": int(year), "dem": y["dem_candidate"].iloc[0], "rep": y["rep_candidate"].iloc[0],
            "dem_votes": nat["dem_votes"], "rep_votes": nat["rep_votes"], "total": nat["total_votes"],
            "dem_pct": r2(nat["dem_votes"] / nat["total_votes"] * 100),
            "rep_pct": r2(nat["rep_votes"] / nat["total_votes"] * 100),
            "ev": {"D": y["ev_dem"].sum(), "R": y["ev_rep"].sum(), "Other": y["ev_other"].sum()},
            "segments": [{"band": k, "ev": v} for k, v in seg.items()],
            "units": units,
        })
    return {"years": years, "bands": [b for _, b in RESULT_BANDS]}


def ordinal(n: str) -> str:
    return {"1": "st", "2": "nd", "3": "rd"}.get(n, "th")


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    polls = model.load_polls()
    forecast, poll_data = build_forecast(polls)
    write_js("forecast", "FORECAST", forecast)
    write_js("polls", "POLLS", poll_data)
    write_js("history", "HISTORY", build_history())

    senate, house = midterms.senate(), midterms.house()
    write_js("senate", "SENATE", senate)
    write_js("house", "HOUSE", house)
    f26 = forecast_model.forecast_2026()
    f28 = forecast_model.forecast_2028()
    # Add today's numbers to the tracker's history, then ship the whole history with each forecast
    # The 2026 record stops on Election Day: after that, market prices follow the
    # results, so later numbers wouldn't be a forecast. Election Day itself is kept
    # (the daily run happens hours before polls close) and is what gets scored.
    before_election = forecast_model.AS_OF <= forecast_model.ELECTION_2026
    hist = forecast_model.save_history(forecast_model.history_rows(f26 if before_election else None, f28))
    if before_election:
        forecast_model.save_race_history(forecast_model.race_history_rows(f26))
    f26["history"] = {c: forecast_model.history_series(hist, c) for c in ("senate", "house")}
    f28["history"] = {m["id"]: forecast_model.history_series(hist, f"president:{m['id']}") for m in f28["matchups"]}
    write_js("forecast_2026", "FORECAST_2026", f26)
    write_js("forecast_2028", "FORECAST_2028", f28)

    avail = [f"{m['dem']} vs {m['rep']}" for m in forecast["matchups"] if m["available"]]
    unavail = [f"{m['dem']} vs {m['rep']}" for m in forecast["matchups"] if not m["available"]]
    print(f"Matchups with a map: {', '.join(avail) or 'none'}")
    if unavail:
        print(f"Listed but no map (no recent national polls): {', '.join(unavail)}")
    print(f"Polls: {len(poll_data['polls'])} ({sum(p['status'] == 'Used' for p in poll_data['polls'])} used)")
    print(f"Senate: D {senate['seats']['D']}, R {senate['seats']['R']}, tossup {senate['seats']['Tossup']}; "
          f"House: D {house['seats']['D']}, R {house['seats']['R']}, tossup {house['seats']['Tossup']}")
    print(f"Forecast ({f26['days_out']} days out): Democrats win the Senate {f26['senate']['summary']['p_dem_control']:.0%}, "
          f"the House {f26['house']['summary']['p_dem_control']:.0%}; 2028 ({f28['days_out']} days out): "
          + ", ".join(f"{m['dem']} {m['summary']['p_dem']:.0%} vs {m['rep']}" for m in f28["matchups"][:3]))
    print(f"Wrote site/data/forecast.js, polls.js, history.js, senate.js, house.js, forecast_2026.js, forecast_2028.js")
