"""
polling_2028.py — the 2028 presidential map.

The problem: as of fall 2026 there are almost no STATE polls for 2028, only
national ones. So each state's estimate is built in two layers:

  1. Baseline = the state's 2024 result, shifted by how much the national
     polls have moved since 2024 ("uniform swing").
         e.g. PA was R+1.7 in 2024, nationally R+1.5. If national polls now
         say D+3.0, that's a 4.5-pt swing toward Dems, so PA baseline = D+2.8.
  2. If the state has its own polls, blend them with the baseline. The more
     (and fresher) state polls, the less the baseline matters. Each state poll
     is first trend-adjusted: shifted by how much the national average has
     moved since that poll was taken, so old polls aren't stuck in the past.

This is the same basic idea professional forecasters use early in a cycle.

Data:
  data/polls_2028.csv           YOU maintain this. One row per poll, per
                                matchup. state = postal code, or US for national.
  data/results_1960_2024.csv    actual results by state; the 2024 rows are the
                                baseline (built by fetch_history.py)

Run:
    python polling_2028.py

Prints a summary for one matchup (DEM vs REP below). The website itself is built
by build_site.py, which uses these functions for every matchup.
"""

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Settings — change these and rerun
# ---------------------------------------------------------------------------
DEM = "Newsom"           # which hypothetical matchup to map
REP = "Vance"

def today() -> pd.Timestamp:
    """Today's date in US Eastern time, wherever the build runs. The cloud runs on
    UTC and a laptop on local time, so without this an evening build could be filed
    under a different day than the morning one."""
    return pd.Timestamp.now(tz="America/New_York").tz_localize(None).normalize()


AS_OF = today()          # build the average as of today
HALF_LIFE_DAYS = 90      # polls are sparse this early, so decay slowly
WINDOW_DAYS = 400        # ignore polls older than this
PRIOR_WEIGHT = 1.5       # how much the baseline counts, in "fresh polls" (see blend_state)

STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}

# Electoral votes, 2024-2028 apportionment (2020 census). Maine and Nebraska
# split some EVs by district; here all of a state's EVs go to its statewide leader.
ELECTORAL_VOTES = {
    "AL": 9, "AK": 3, "AZ": 11, "AR": 6, "CA": 54, "CO": 10, "CT": 7, "DE": 3, "DC": 3,
    "FL": 30, "GA": 16, "HI": 4, "ID": 4, "IL": 19, "IN": 11, "IA": 6, "KS": 6, "KY": 8,
    "LA": 8, "ME": 4, "MD": 10, "MA": 11, "MI": 15, "MN": 10, "MS": 6, "MO": 10, "MT": 4,
    "NE": 5, "NV": 6, "NH": 4, "NJ": 14, "NM": 5, "NY": 28, "NC": 16, "ND": 3, "OH": 17,
    "OK": 7, "OR": 8, "PA": 19, "RI": 4, "SC": 9, "SD": 3, "TN": 11, "TX": 40, "UT": 6,
    "VT": 3, "VA": 13, "WA": 12, "WV": 4, "WI": 10, "WY": 3,
}

# Ratings by size of the estimated margin, in points. Below the last one is a tossup.
RATING_CUTOFFS = [(15, "Almost certain"), (5, "Likely"), (2, "Lean")]

# Left-to-right order of the electoral vote bar, with colors that grade from
# deep blue through gray to deep red.
RATING_COLORS = {
    "Almost certain D": "#1d4f91", "Likely D": "#4f86c6", "Lean D": "#a6c8ea",
    "Tossup": "#bdbdbd",
    "Lean R": "#f2aaa0", "Likely R": "#d6604d", "Almost certain R": "#a21d2a",
}


# ---------------------------------------------------------------------------
# 1. Load
# ---------------------------------------------------------------------------
def load_polls(path: str = "data/polls_2028.csv") -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["start_date", "end_date"])
    df["sample_size_reported"] = df["sample_size"].notna()
    df["sample_size"] = df["sample_size"].fillna(df["sample_size"].median())
    df["margin"] = df["dem"] - df["rep"]
    return df


def load_2024(path: str = "data/results_1960_2024.csv") -> tuple[pd.DataFrame, float]:
    res = pd.read_csv(path)
    res = res[(res["year"] == 2024) & ~res["is_district"]].copy()
    res["margin_2024"] = (res["dem_votes"] - res["rep_votes"]) / res["total_votes"] * 100
    totals = res[["dem_votes", "rep_votes", "total_votes"]].sum()
    national_2024 = (totals["dem_votes"] - totals["rep_votes"]) / totals["total_votes"] * 100
    return res[["state", "margin_2024"]], national_2024


# ---------------------------------------------------------------------------
# 2. Weighting (same idea as the 2016 script)
# ---------------------------------------------------------------------------
def poll_weights(polls: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    age_days = (as_of - polls["end_date"]).dt.days.clip(lower=0)
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)
    size = np.sqrt(polls["sample_size"].clip(upper=5000) / 600)
    count = polls.groupby(["state", "pollster"])["pollster"].transform("count")
    flooding = 1 / np.sqrt(count)
    return recency * size * flooding


def in_window(polls: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    keep = (polls["end_date"] <= as_of) & (polls["end_date"] > as_of - pd.Timedelta(days=WINDOW_DAYS))
    out = polls[keep].copy()
    out["w"] = poll_weights(out, as_of)
    return out


# ---------------------------------------------------------------------------
# 3. National average -> swing -> state estimates
# ---------------------------------------------------------------------------
def national_average(polls: pd.DataFrame) -> tuple[float, int]:
    nat = polls[polls["state"] == "US"]
    if nat.empty:
        raise ValueError("No national polls for this matchup in the window.")
    return np.average(nat["margin"], weights=nat["w"]), len(nat)


def national_average_on(matchup: pd.DataFrame, date: pd.Timestamp) -> float | None:
    """The national average as it stood on `date`, or None if no national polls yet."""
    nat = in_window(matchup[matchup["state"] == "US"], date)
    if nat.empty:
        return None
    return np.average(nat["margin"], weights=nat["w"])


def trend_adjust(window: pd.DataFrame, matchup: pd.DataFrame, nat_now: float) -> pd.DataFrame:
    """Shift each state poll by how far the national average has moved since it was taken.

    e.g. a PA poll showing D+1 when the nation was R+1, with the nation now at
    D+3, is treated as D+5. Polls taken before any national poll are left as-is.
    """
    out = window.copy()
    out["margin"] = out["margin"].astype(float)
    out["margin_raw"] = out["margin"]
    nat_then = {}
    for i, r in out[out["state"] != "US"].iterrows():
        d = r["end_date"]
        if d not in nat_then:
            nat_then[d] = national_average_on(matchup, d)
        if nat_then[d] is not None:
            out.at[i, "margin"] = r["margin"] + (nat_now - nat_then[d])
    return out


def blend_state(baseline: float, state_polls: pd.DataFrame) -> float:
    """Weighted average of the baseline and the state's own polls.

    The baseline gets weight PRIOR_WEIGHT. A fresh 600-person poll has weight
    about 1.0, so with PRIOR_WEIGHT = 1.5 the baseline is worth roughly one
    and a half fresh polls. Three fresh state polls would outweigh it 2:1.
    """
    if state_polls.empty:
        return baseline
    total_w = PRIOR_WEIGHT + state_polls["w"].sum()
    return (PRIOR_WEIGHT * baseline + (state_polls["w"] * state_polls["margin"]).sum()) / total_w


def matchup_polls(polls: pd.DataFrame, dem: str, rep: str) -> tuple[pd.DataFrame, float, int]:
    """Polls for one matchup inside the window, with weights (`w`) and trend-adjusted
    margins (`margin`; the original is kept as `margin_raw`). Also returns the
    national average and how many national polls it uses."""
    matchup = polls[(polls["dem_candidate"] == dem) & (polls["rep_candidate"] == rep)]
    window = in_window(matchup, AS_OF)
    nat_avg, n_nat = national_average(window)
    return trend_adjust(window, matchup, nat_avg), nat_avg, n_nat


def state_estimates(polls: pd.DataFrame, dem: str = DEM, rep: str = REP) -> tuple[pd.DataFrame, dict]:
    window, nat_avg, n_nat = matchup_polls(polls, dem, rep)
    results_2024, national_2024 = load_2024()
    swing = nat_avg - national_2024

    rows = []
    for _, r in results_2024.iterrows():
        baseline = r["margin_2024"] + swing
        sp = window[window["state"] == r["state"]]
        rows.append({
            "state": r["state"],
            "state_name": STATE_NAMES[r["state"]],
            "margin_2024": r["margin_2024"],
            "baseline": baseline,
            "estimate": blend_state(baseline, sp),
            "n_state_polls": len(sp),
            "ev": ELECTORAL_VOTES[r["state"]],
        })
    est = pd.DataFrame(rows)
    est["rating"] = est["estimate"].apply(rate)

    summary = {"national_avg": nat_avg, "national_2024": national_2024,
               "swing": swing, "n_national_polls": n_nat}
    return est, summary


def rate(margin: float) -> str:
    for cutoff, label in RATING_CUTOFFS:
        if abs(margin) >= cutoff:
            return f"{label} {'D' if margin > 0 else 'R'}"
    return "Tossup"


def ev_totals(est: pd.DataFrame) -> dict:
    """EVs per rating, plus each side's total excluding tossups."""
    by_rating = est.groupby("rating")["ev"].sum()
    totals = {r: int(by_rating.get(r, 0)) for r in RATING_COLORS}
    totals["D"] = sum(v for r, v in totals.items() if r.endswith(" D"))
    totals["R"] = sum(v for r, v in totals.items() if r.endswith(" R"))
    return totals


# ---------------------------------------------------------------------------
def fmt_margin(m: float) -> str:
    if abs(m) < 0.05:
        return "Even"
    return f"{'D' if m > 0 else 'R'}+{abs(m):.1f}"


if __name__ == "__main__":
    polls = load_polls()
    est, s = state_estimates(polls)

    print(f"Matchup: {DEM} (D) vs {REP} (R), as of {AS_OF.date()}")
    print(f"National average: {fmt_margin(s['national_avg'])} from {s['n_national_polls']} polls")
    print(f"2024 national result: {fmt_margin(s['national_2024'])}")
    print(f"Swing applied to every state: {s['swing']:+.1f} pts")
    flips = est[np.sign(est["estimate"]) != np.sign(est["margin_2024"])]
    print(f"States that flip vs 2024: {', '.join(flips['state_name']) or 'none'}")
    t = ev_totals(est)
    print(f"Electoral votes (excluding tossups): {DEM} {t['D']}, {REP} {t['R']}, tossup {t['Tossup']}")
    print("  " + " · ".join(f"{r} {t[r]}" for r in RATING_COLORS))
