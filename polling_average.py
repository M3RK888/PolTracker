"""
polling_average.py — Phase 1 of the polling-map project.

Turns a pile of individual polls into one weighted average per state,
then (because this is 2016 data) checks the averages against what
actually happened on election day.

Data (real, public):
  data/polls_2016_raw.csv       HuffPost Pollster state + national polls,
                                via The Economist's open-source model repo
                                (github.com/TheEconomist/us-potus-model)
  data/results_1976_2016.csv    Actual state results, same repo

Run:
    pip install pandas plotly
    python polling_average.py

Outputs:
    state_averages_2016.csv     one row per state
    poll_average_map.html       interactive map of the averages
    polling_error_map.html      how far off the averages were

When 2028 polls exist, only `load_polls()` should need to change: as long
as it returns the standard columns below, everything else just works.
"""

import numpy as np
import pandas as pd
import plotly.express as px

# Standard columns every loader must return:
#   state, pollster, end_date, sample_size, voter_type, dem, rep
# dem / rep are vote shares in percent (e.g. 47.0, not 0.47).

ELECTION_DAY = pd.Timestamp("2016-11-08")
HALF_LIFE_DAYS = 14     # a poll 14 days old counts half as much as one from today
WINDOW_DAYS = 60        # ignore polls older than this
MIN_POLLS = 1           # states with fewer polls are left blank on the map

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


# ---------------------------------------------------------------------------
# 1. Load and clean
# ---------------------------------------------------------------------------
def load_polls(path: str = "data/polls_2016_raw.csv") -> pd.DataFrame:
    raw = pd.read_csv(path)

    df = pd.DataFrame({
        "state": raw["state"],
        "pollster": raw["pollster"],
        "end_date": pd.to_datetime(raw["end.date"]),
        "sample_size": raw["number.of.observations"],
        "voter_type": raw["population"],
        "question": raw["question.iteration"],
        "dem": raw["clinton"],
        "rep": raw["trump"],
    })

    # Drop national polls ("--"); this file is for the state map.
    df = df[df["state"] != "--"]

    # Drop subgroup rows like "Likely Voters - Democrat". We only want toplines.
    df = df[~df["voter_type"].str.contains(" - ", na=False)]

    # Some polls ask the question twice (e.g. with and without third parties).
    # Keep one version per poll: the first question asked.
    df = df.sort_values("question").drop_duplicates(
        ["state", "pollster", "end_date", "voter_type"], keep="first"
    )

    # If a poll reports both Likely and Registered voters, prefer Likely.
    df["lv_first"] = (df["voter_type"] != "Likely Voters").astype(int)
    df = df.sort_values("lv_first").drop_duplicates(
        ["state", "pollster", "end_date"], keep="first"
    )

    # Missing sample sizes: fill with the typical value so the poll still counts.
    df["sample_size"] = df["sample_size"].fillna(df["sample_size"].median())

    return df.drop(columns=["lv_first", "question"]).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 2. Weighting
# ---------------------------------------------------------------------------
def poll_weights(polls: pd.DataFrame, as_of: pd.Timestamp) -> pd.Series:
    """Weight = recency x sample size x pollster-flooding penalty."""
    age_days = (as_of - polls["end_date"]).dt.days.clip(lower=0)

    # Recency: exponential decay. Half the weight every HALF_LIFE_DAYS.
    recency = 0.5 ** (age_days / HALF_LIFE_DAYS)

    # Sample size: sampling error shrinks with sqrt(n), so weight by sqrt(n).
    # Capped so one giant online panel can't swamp everything.
    size = np.sqrt(polls["sample_size"].clip(upper=5000) / 600)

    # Pollster flooding: if one firm releases 10 polls of a state, don't let
    # it count 10x. Each pollster's polls share weight ~ 1/sqrt(count).
    count = polls.groupby(["state", "pollster"])["pollster"].transform("count")
    flooding = 1 / np.sqrt(count)

    return recency * size * flooding


def state_averages(polls: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """One weighted average per state, using polls in the window before as_of."""
    window = polls[
        (polls["end_date"] <= as_of)
        & (polls["end_date"] > as_of - pd.Timedelta(days=WINDOW_DAYS))
    ].copy()
    window["w"] = poll_weights(window, as_of)
    window["margin"] = window["dem"] - window["rep"]

    def summarize(g: pd.DataFrame) -> pd.Series:
        w = g["w"]
        margin = np.average(g["margin"], weights=w)
        # Effective sample size: how many respondents the average is "worth".
        eff_n = (w * g["sample_size"]).sum() / w.sum() * (w.sum() ** 2 / (w ** 2).sum())
        # Rough 95% margin of error on the D-R margin. Real polls disagree more
        # than pure sampling error says, so this understates true uncertainty.
        moe = 2 * 1.96 * np.sqrt(0.25 / eff_n) * 100
        return pd.Series({
            "dem": np.average(g["dem"], weights=w),
            "rep": np.average(g["rep"], weights=w),
            "margin": margin,
            "n_polls": len(g),
            "n_pollsters": g["pollster"].nunique(),
            "latest_poll": g["end_date"].max().date(),
            "moe": moe,
        })

    out = window.groupby("state").apply(summarize, include_groups=False).reset_index()
    out = out[out["n_polls"] >= MIN_POLLS]
    out["state_name"] = out["state"].map(STATE_NAMES)
    return out


# ---------------------------------------------------------------------------
# 3. Check against reality (only possible for past elections)
# ---------------------------------------------------------------------------
def add_actual_results(avg: pd.DataFrame, path: str = "data/results_1976_2016.csv",
                       year: int = 2016) -> pd.DataFrame:
    res = pd.read_csv(path)
    res = res[res["year"] == year]
    res = res.assign(actual_margin=(res["dem"] - res["rep"]) * 100)[["state", "actual_margin"]]
    out = avg.merge(res, on="state", how="left")
    # Positive error = polls were too favorable to the Democrat.
    out["error"] = out["margin"] - out["actual_margin"]
    return out


# ---------------------------------------------------------------------------
# 4. Maps
# ---------------------------------------------------------------------------
def fmt_margin(m: float) -> str:
    if abs(m) < 0.05:
        return "Even"
    return f"{'D' if m > 0 else 'R'}+{abs(m):.1f}"


def margin_map(avg: pd.DataFrame, title: str):
    df = avg.assign(
        avg_label=avg["margin"].apply(fmt_margin),
        moe_label=avg["moe"].map(lambda x: f"±{x:.1f}"),
    )
    fig = px.choropleth(
        df, locations="state", locationmode="USA-states", scope="usa",
        color="margin", color_continuous_scale="RdBu", range_color=(-25, 25),
        hover_name="state_name",
        hover_data={"avg_label": True, "moe_label": True, "n_polls": True,
                    "n_pollsters": True, "latest_poll": True,
                    "margin": False, "state": False},
        labels={"avg_label": "Average", "moe_label": "Margin of error",
                "n_polls": "Polls", "n_pollsters": "Pollsters",
                "latest_poll": "Latest poll"},
        title=title,
    )
    fig.update_layout(
        coloraxis_colorbar=dict(title="Margin", tickvals=[-20, -10, 0, 10, 20],
                                ticktext=["R+20", "R+10", "Even", "D+10", "D+20"]),
        margin=dict(l=0, r=0, t=50, b=0),
    )
    return fig


def error_map(avg: pd.DataFrame, title: str):
    df = avg.dropna(subset=["actual_margin"]).assign(
        avg_label=lambda d: d["margin"].apply(fmt_margin),
        actual_label=lambda d: d["actual_margin"].apply(fmt_margin),
        miss_label=lambda d: d["error"].apply(
            lambda e: f"{abs(e):.1f} pts too {'Dem' if e > 0 else 'Rep'}"),
    )
    fig = px.choropleth(
        df, locations="state", locationmode="USA-states", scope="usa",
        color="error", color_continuous_scale="PuOr", range_color=(-12, 12),
        hover_name="state_name",
        hover_data={"avg_label": True, "actual_label": True, "miss_label": True,
                    "n_polls": True, "error": False, "state": False},
        labels={"avg_label": "Poll average", "actual_label": "Actual result",
                "miss_label": "Miss", "n_polls": "Polls"},
        title=title,
    )
    fig.update_layout(
        coloraxis_colorbar=dict(title="Polls vs result", tickvals=[-10, 0, 10],
                                ticktext=["Too Rep", "Exact", "Too Dem"]),
        margin=dict(l=0, r=0, t=50, b=0),
    )
    return fig


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    polls = load_polls()
    print(f"Loaded {len(polls):,} state polls across {polls['state'].nunique()} states")

    avg = state_averages(polls, as_of=ELECTION_DAY)
    avg = add_actual_results(avg)
    avg.to_csv("state_averages_2016.csv", index=False)

    print(f"States with an average: {len(avg)}")
    print(f"Average absolute miss:  {avg['error'].abs().mean():.1f} pts")
    print(f"Average directional miss: {avg['error'].mean():+.1f} pts "
          "(positive = polls leaned Dem)")
    wrong = avg[np.sign(avg["margin"]) != np.sign(avg["actual_margin"])]
    print(f"Wrong winner: {', '.join(wrong['state_name']) or 'none'}")

    margin_map(avg, "2016 Final State Polling Averages (Clinton vs. Trump)") \
        .write_html("poll_average_map.html", include_plotlyjs="cdn")
    error_map(avg, "How Far Off Were the 2016 State Polls?") \
        .write_html("polling_error_map.html", include_plotlyjs="cdn")
    print("Wrote poll_average_map.html, polling_error_map.html, state_averages_2016.csv")
