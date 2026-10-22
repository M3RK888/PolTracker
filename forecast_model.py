"""
forecast_model.py — win probabilities for 2026 (Senate, House) and 2028 (president).

The poll pages (midterms.py, polling_2028.py) estimate the margin in each race.
This turns estimates into chances of winning, in four steps:

  1. Three estimates per race, all as a margin (Democratic minus Republican, points):
       polls         the existing poll-based estimate
       fundamentals  a national environment from presidential approval, plus the
                     race's partisan lean, plus a bonus for incumbents running
       markets       Polymarket / Kalshi prices, turned into a margin (see market_margin)
  2. Blend them. Polls count more as Election Day gets closer:
       poll weight = 1 / (1 + days_left / 60)   (13 days out: 82%; two years out: 7%)
     The rest is split 60% markets, 40% fundamentals. A missing ingredient's
     weight is shared among the others.
  3. Uncertainty. The real result will differ from the blend. Some of that error
     is shared by every race (a national polling miss), some by a region, and
     some is just that race. All three grow with time left.
  4. Simulate the election 20,000 times with random errors, and count:
     a race's win probability is the share of simulations its side wins;
     control is the share of simulations a party reaches a majority.

Every number here is a judgment call, not fitted to past elections. That's why
the pages call this an unvalidated hobby model.

Run for sanity checks:
    python forecast_model.py
"""

import math
from statistics import NormalDist

import numpy as np
import pandas as pd

import midterms

AS_OF = midterms.AS_OF
ELECTION_2026 = pd.Timestamp("2026-11-03")
ELECTION_2028 = pd.Timestamp("2028-11-07")

N_DRAWS = 20_000
SEED = 2026                    # fixed, so the same inputs always give the same numbers
T_DF = 5                       # Student-t errors: like a bell curve, but big misses are likelier

POLL_DAYS = 60                 # poll weight = 1 / (1 + days / POLL_DAYS)
MARKET_SHARE = 0.6             # of the non-poll weight; fundamentals get the rest
MIN_VOLUME = 5_000             # ignore markets with less than $5,000 traded
P_CLAMP = (0.02, 0.98)         # a 99.9% market price says little about the margin

INCUMBENCY = 2.5               # points of margin for an incumbent on the ballot
REGION_SD = 2.0                # shared error within a Census region

# Midterm rule of thumb: since 1994 the president's party has run about 4.5 points
# behind in the House popular vote, plus about 1 point per 6 points of net approval
# (e.g. 2018: Trump about −10 net, Republicans lost by 8.6; 1998 and 2002: popular
# presidents, their party roughly broke even or gained).
MIDTERM_BASE = -4.5
MIDTERM_SLOPE = 0.17

SENATE_THRESHOLD = {"D": 51, "R": 50}   # Vice President Vance breaks a 50-50 tie
HOUSE_THRESHOLD = 218

REGIONS = {
    "Northeast": "CT ME MA NH RI VT NJ NY PA",
    "Midwest": "IL IN MI OH WI IA KS MN MO NE ND SD",
    "South": "DE DC FL GA MD NC SC VA WV AL KY MS TN AR LA OK TX",
    "West": "AZ CO ID MT NV NM UT WY AK CA HI OR WA",
}
REGION_OF = {s: i for i, (_, states) in enumerate(REGIONS.items()) for s in states.split()}

# Probability bands for the seat bar, Democratic chance of winning (lower bound)
BANDS = [(0.95, "Safe D"), (0.75, "Likely D"), (0.60, "Lean D"), (0.40, "Toss-up"),
         (0.25, "Lean R"), (0.05, "Likely R"), (0.0, "Safe R")]
BAND_LABELS = {"Safe D": "95%+ D", "Likely D": "75–95% D", "Lean D": "60–75% D", "Toss-up": "40–60%",
               "Lean R": "60–75% R", "Likely R": "75–95% R", "Safe R": "95%+ R"}


# ---------------------------------------------------------------------------
# Time, weights, uncertainty
# ---------------------------------------------------------------------------
def days_until(election: pd.Timestamp) -> int:
    return max(0, (election - AS_OF).days)


def base_weights(t: int) -> dict:
    wp = 1 / (1 + t / POLL_DAYS)
    return {"polls": wp, "markets": (1 - wp) * MARKET_SHARE, "fundamentals": (1 - wp) * (1 - MARKET_SHARE)}


def weights_for(t: int, have: dict) -> dict:
    """Base weights with missing ingredients dropped and the rest scaled to add to 1."""
    w = {k: v for k, v in base_weights(t).items() if have.get(k)}
    total = sum(w.values())
    return {k: w.get(k, 0.0) / total for k in ("polls", "markets", "fundamentals")}


def sigmas(t: int, s0: float) -> dict:
    """Standard deviations (points of margin) of the error on the final margin."""
    return {"national": math.sqrt(3 ** 2 + 0.06 * t), "region": REGION_SD,
            "race": math.sqrt(s0 ** 2 + 0.05 * t)}


def total_sigma(sig: dict) -> float:
    return math.sqrt(sum(v ** 2 for v in sig.values()))


def market_margin(p: float, sigma: float) -> float:
    """The margin that would give this win probability under the model's uncertainty."""
    return sigma * NormalDist().inv_cdf(min(max(p, P_CLAMP[0]), P_CLAMP[1]))


def t_errors(rng, sd: float, size) -> np.ndarray:
    # Student-t rescaled so its standard deviation is sd
    return rng.standard_t(T_DF, size=size) * sd / math.sqrt(T_DF / (T_DF - 2))


def band(p: float) -> str:
    return next(label for lo, label in BANDS if p >= lo)


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------
def load_markets() -> pd.DataFrame:
    try:
        m = pd.read_csv("data/markets.csv")
    except FileNotFoundError:
        return pd.DataFrame(columns=["race", "source", "probability_dem", "volume", "label", "fetched_at"])
    return m


def market_for(markets: pd.DataFrame, race: str) -> dict | None:
    """Average of the sources that list this race with enough trading volume."""
    rows = markets[(markets["race"] == race) & (markets["volume"] >= MIN_VOLUME)]
    if rows.empty:
        return None
    return {"p": float(rows["probability_dem"].mean()),
            "sources": {r["source"]: round(float(r["probability_dem"]), 4) for _, r in rows.iterrows()}}


def control_markets(markets: pd.DataFrame, race: str) -> dict:
    rows = markets[markets["race"] == race]
    return {r["source"]: {"p_dem": round(float(r["probability_dem"]), 4), "volume": int(r["volume"])}
            for _, r in rows.iterrows()}


def approval() -> dict:
    a = pd.read_csv("data/approval.csv").iloc[0]
    return {"date": a["date"], "approve": float(a["approve"]), "disapprove": float(a["disapprove"]),
            "net": float(a["net"]), "source": a["source"]}


def midterm_environment(net_approval: float) -> float:
    """Fundamentals national environment for 2026, as a Democratic margin.
    The president (Trump) is a Republican, so his party's margin is flipped."""
    return -(MIDTERM_BASE + MIDTERM_SLOPE * net_approval)


def same_person(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a.split()[-1].lower() == b.split()[-1].lower()


# ---------------------------------------------------------------------------
# Core: blend + simulate
# ---------------------------------------------------------------------------
def draw_errors(races: list[dict], sig: dict) -> np.ndarray:
    """Simulated misses (draws x races): national + regional + each race's own."""
    rng = np.random.default_rng(SEED)
    region_idx = np.array([REGION_OF[r["region_state"]] for r in races])
    national = t_errors(rng, sig["national"], (N_DRAWS, 1))
    regional = t_errors(rng, sig["region"], (N_DRAWS, len(REGIONS)))[:, region_idx]
    return national + regional + t_errors(rng, sig["race"], (N_DRAWS, len(races)))


def forecast_races(races: list[dict], t: int, s0: float, errors: np.ndarray | None = None) -> tuple[list[dict], np.ndarray, dict]:
    """races: [{key, region_state, polls, fundamentals, market (dict|None)}]; a race
    may instead carry a ready-made market_margin. Returns the races with forecast
    fields added, the simulated margins (draws x races), and the uncertainty used."""
    sig = sigmas(t, s0)
    sd = total_sigma(sig)
    blended = []
    for r in races:
        m = r.get("market")
        if "market_margin" not in r:
            r["market_margin"] = None if m is None else market_margin(m["p"], sd)
        have = {"polls": r["polls"] is not None, "markets": r["market_margin"] is not None,
                "fundamentals": r["fundamentals"] is not None}
        w = weights_for(t, have)
        r["weights"] = w
        r["blended"] = sum(w[k] * v for k, v in (("polls", r["polls"]), ("markets", r["market_margin"]),
                                                  ("fundamentals", r["fundamentals"])) if w[k])
        blended.append(r["blended"])

    if errors is None:
        errors = draw_errors(races, sig)
    sims = np.array(blended)[None, :] + errors

    for i, r in enumerate(races):
        col = sims[:, i]
        lo, med, hi = np.percentile(col, [10, 50, 90])
        r["p_dem"] = float((col > 0).mean())
        r["median"], r["lo"], r["hi"] = float(med), float(lo), float(hi)
        r["band"] = band(r["p_dem"])
    return races, sims, {k: round(v, 2) for k, v in sig.items()} | {"total": round(sd, 2)}


def seat_summary(dem_seats: np.ndarray, d_needs: int, r_needs: int, total: int) -> dict:
    values, counts = np.unique(dem_seats, return_counts=True)
    lo, med, hi = np.percentile(dem_seats, [10, 50, 90])
    return {
        "p_dem_control": round(float((dem_seats >= d_needs).mean()), 4),
        "p_rep_control": round(float((total - dem_seats >= r_needs).mean()), 4),
        "dem_median": int(med), "dem_lo": int(lo), "dem_hi": int(hi), "dem_mean": round(float(dem_seats.mean()), 1),
        "histogram": [{"seats": int(v), "p": round(c / len(dem_seats), 5)} for v, c in zip(values, counts)],
    }


def band_segments(races: list[dict], seat_key=None) -> list[dict]:
    counts = {label: 0 for _, label in BANDS}
    for r in races:
        counts[r["band"]] += r[seat_key] if seat_key else 1
    return [{"band": b, "label": BAND_LABELS[b], "seats": c} for b, c in counts.items()]


def r2(x):
    return None if x is None else round(float(x), 2)


def race_output(r: dict) -> dict:
    m = r.get("market")
    return {
        "key": r["key"], "p_dem": round(r["p_dem"], 4), "band": r["band"],
        "median": r2(r["median"]), "lo": r2(r["lo"]), "hi": r2(r["hi"]),
        "polls": r2(r["polls"]), "fundamentals": r2(r["fundamentals"]),
        "market_p": None if m is None else round(m["p"], 4), "market_sources": None if m is None else m["sources"],
        "market_margin": r2(r["market_margin"]), "blended": r2(r["blended"]),
        "weights": {k: round(v, 3) for k, v in r["weights"].items()},
        **{k: r[k] for k in ("incumbent_running", "incumbent_bonus") if k in r},
    }


# ---------------------------------------------------------------------------
# 2026
# ---------------------------------------------------------------------------
def forecast_2026(markets: pd.DataFrame | None = None, appr: dict | None = None,
                  generic: list[dict] | None = None) -> dict:
    """Today's forecast from the data files. backfill_history.py passes in the
    inputs as they stood on a past date instead."""
    t = days_until(ELECTION_2026)
    markets = load_markets() if markets is None else markets
    appr = approval() if appr is None else appr
    env_fund = midterm_environment(appr["net"])

    # Senate --------------------------------------------------------------
    sen = midterms.senate(generic)
    races = []
    for r in sen["races"]:
        inc_running = same_person(r["incumbent"], r["rep"]) or same_person(r["incumbent"], r["opp"])
        bonus = 0.0
        if inc_running:
            bonus = INCUMBENCY if r["incumbent_party"] != "R" else -INCUMBENCY
        races.append({
            "key": r["state"], "region_state": r["state"],
            "polls": r["estimate"], "fundamentals": env_fund + 2 * r["pvi"] + bonus,
            "market": market_for(markets, f"senate:{r['state']}"),
            "incumbent_running": inc_running, "incumbent_bonus": bonus,
        })
    races, sims, sig_s = forecast_races(races, t, s0=4.0)
    dem_seats = midterms.SENATE_NOT_UP["D"] + (sims > 0).sum(axis=1)
    senate = {
        "summary": seat_summary(dem_seats, SENATE_THRESHOLD["D"], SENATE_THRESHOLD["R"], midterms.SENATE_SEATS),
        "segments": band_segments(races), "sigma": sig_s,
        "markets": control_markets(markets, "control:senate"),
        "races": [race_output(r) for r in races],
    }

    # House ---------------------------------------------------------------
    hou = midterms.house(generic)
    dists = pd.read_csv("data/house_2026_districts.csv", keep_default_na=False).set_index("district")
    races = []
    for d in hou["districts"]:
        row = dists.loc[d["id"]]
        inc_running = d["incumbent_party"] in ("D", "R") and (
            same_person(row["incumbent"], row["dem_candidate"]) or same_person(row["incumbent"], row["rep_candidate"]))
        bonus = (INCUMBENCY if d["incumbent_party"] == "D" else -INCUMBENCY) if inc_running else 0.0
        races.append({
            "key": d["id"], "region_state": d["state"],
            "polls": d["estimate"], "fundamentals": env_fund + 2 * d["pvi"] + bonus,
            "market": market_for(markets, f"house:{d['id']}"),
            "incumbent_running": bool(inc_running), "incumbent_bonus": bonus,
        })
    races, sims, sig_h = forecast_races(races, t, s0=6.0)
    dem_seats = (sims > 0).sum(axis=1)
    house = {
        "summary": seat_summary(dem_seats, HOUSE_THRESHOLD, HOUSE_THRESHOLD, midterms.HOUSE_SEATS),
        "segments": band_segments(races), "sigma": sig_h,
        "markets": control_markets(markets, "control:house"),
        "races": [race_output(r) for r in races],
    }

    fetched = markets["fetched_at"].max() if len(markets) else None
    return {
        "as_of": AS_OF.strftime("%Y-%m-%d"), "election": ELECTION_2026.strftime("%Y-%m-%d"), "days_out": t,
        "weights": {k: round(v, 3) for k, v in base_weights(t).items()},
        "approval": appr, "environment": {"polls": sen["environment"], "fundamentals": round(env_fund, 2)},
        "markets_fetched": fetched,
        "settings": {
            "draws": N_DRAWS, "t_df": T_DF, "poll_days": POLL_DAYS, "market_share": MARKET_SHARE,
            "min_volume": MIN_VOLUME, "p_clamp": list(P_CLAMP), "incumbency": INCUMBENCY,
            "region_sd": REGION_SD, "midterm_base": MIDTERM_BASE, "midterm_slope": MIDTERM_SLOPE,
            "bands": [{"band": b, "label": BAND_LABELS[b], "min": lo} for lo, b in BANDS],
        },
        "senate": senate, "house": house,
    }


# ---------------------------------------------------------------------------
# 2028
# ---------------------------------------------------------------------------
# Fundamentals for an open-seat presidential race (Trump can't run again), loosely
# after Abramowitz's "time for change" model: the president's party gains about
# 0.2 points of margin per point of net approval, and loses about 2 points per
# point that unemployment rose over the past year. Two years out these readings
# will change a lot, so the result is shrunk toward an even race by
# 1 / (1 + days_left / 365): about a third today, all of it on Election Day.
PRES_APPROVAL_SLOPE = 0.2
PRES_UNEMPLOYMENT_SLOPE = -2.0
PRES_SHRINK_DAYS = 365
EV_TO_WIN = 270


def presidential_environment(net_approval: float, unemployment_change: float, t: int) -> dict:
    raw = PRES_APPROVAL_SLOPE * net_approval + PRES_UNEMPLOYMENT_SLOPE * unemployment_change   # Republican margin
    shrink = 1 / (1 + t / PRES_SHRINK_DAYS)
    return {"raw_rep": round(raw, 2), "shrink": round(shrink, 3), "dem": round(-raw * shrink, 2)}


def market_shift(lean: np.ndarray, ev: np.ndarray, errors: np.ndarray, p: float) -> float:
    """The national margin x at which states at (lean + x) give Democrats a p chance
    of 270 electoral votes, using the model's own simulated misses."""
    p = min(max(p, P_CLAMP[0]), P_CLAMP[1])
    lo, hi = -40.0, 40.0
    for _ in range(40):
        x = (lo + hi) / 2
        win = (((lean + x)[None, :] + errors > 0) * ev).sum(axis=1) >= EV_TO_WIN
        lo, hi = (x, hi) if win.mean() < p else (lo, x)
    return (lo + hi) / 2


def economy() -> dict:
    e = pd.read_csv("data/economy.csv").iloc[0]
    return {"date": e["date"], "unemployment": float(e["unemployment"]), "change": float(e["change"]), "source": e["source"]}


def forecast_2028() -> dict:
    import polling_2028 as pm

    t = days_until(ELECTION_2028)
    markets = load_markets()
    appr, econ = approval(), economy()
    env = presidential_environment(appr["net"], econ["change"], t)
    market = market_for(markets, "control:president")
    polls = pm.load_polls()
    pairs = polls.groupby(["dem_candidate", "rep_candidate"]).size().reset_index(name="n").sort_values("n", ascending=False)

    out = []
    for _, pair in pairs.iterrows():
        dem, rep = pair["dem_candidate"], pair["rep_candidate"]
        try:
            est, s = pm.state_estimates(polls, dem, rep)
        except ValueError:
            continue                                   # no recent national polls: no map on the poll page either
        lean = (est["margin_2024"] - s["national_2024"]).to_numpy()
        ev = est["ev"].to_numpy()
        races = [{"key": r["state"], "region_state": r["state"], "polls": float(r["estimate"]),
                  "fundamentals": env["dem"] + lean[i]} for i, (_, r) in enumerate(est.iterrows())]
        errors = draw_errors(races, sigmas(t, 4.0))
        shift = None
        if market is not None:
            shift = market_shift(lean, ev, errors, market["p"])
            for i, r in enumerate(races):
                r["market_margin"] = lean[i] + shift
        races, sims, sig_out = forecast_races(races, t, 4.0, errors)
        dem_ev = ((sims > 0) * ev[None, :]).sum(axis=1)
        values, counts = np.unique(dem_ev // 5 * 5, return_counts=True)
        lo_, med, hi_ = np.percentile(dem_ev, [10, 50, 90])
        segs = {b: 0 for _, b in BANDS}
        for r, e in zip(races, ev):
            segs[r["band"]] += int(e)
        states = []
        for i, ((_, r), f) in enumerate(zip(est.iterrows(), races)):
            states.append(race_output(f) | {"name": r["state_name"], "ev": int(r["ev"]), "lean": r2(lean[i])})
        out.append({
            "id": f"{dem}-{rep}".lower().replace(" ", "-"), "dem": dem, "rep": rep,
            "summary": {
                "p_dem": round(float((dem_ev >= EV_TO_WIN).mean()), 4),
                "p_rep": round(float((538 - dem_ev >= EV_TO_WIN).mean()), 4),
                "p_tie": round(float((dem_ev == 269).mean()), 4),
                "ev_median": int(med), "ev_lo": int(lo_), "ev_hi": int(hi_),
                "histogram": [{"seats": int(v), "p": round(c / N_DRAWS, 5)} for v, c in zip(values, counts)],
            },
            "segments": [{"band": b, "label": BAND_LABELS[b], "ev": v} for b, v in segs.items()],
            "national": {"polls": r2(s["national_avg"]), "fundamentals": env["dem"], "market": r2(shift)},
            "sigma": sig_out, "states": states,
        })

    return {
        "as_of": AS_OF.strftime("%Y-%m-%d"), "election": ELECTION_2028.strftime("%Y-%m-%d"), "days_out": t,
        "weights": {k: round(v, 3) for k, v in base_weights(t).items()},
        "approval": appr, "economy": econ, "environment": env,
        "market": None if market is None else {"p_dem": round(market["p"], 4), "sources": market["sources"]},
        "markets": control_markets(markets, "control:president"),
        "markets_fetched": markets["fetched_at"].max() if len(markets) else None,
        "settings": {"draws": N_DRAWS, "poll_days": POLL_DAYS, "approval_slope": PRES_APPROVAL_SLOPE,
                     "unemployment_slope": PRES_UNEMPLOYMENT_SLOPE, "shrink_days": PRES_SHRINK_DAYS,
                     "min_volume": MIN_VOLUME, "region_sd": REGION_SD},
        "matchups": out,
    }


# ---------------------------------------------------------------------------
# History, for the tracker charts
# ---------------------------------------------------------------------------
# data/forecast_history.csv holds one row per day per race:
#   race   "senate", "house", or "president:<matchup id>"
#   kind   "daily"          recorded by build_site.py on that day
#          "reconstructed"  rebuilt later by backfill_history.py from the data as it stood then
HISTORY_CSV = "data/forecast_history.csv"
HISTORY_COLUMNS = ["date", "race", "kind", "p_dem", "p_rep", "median", "lo", "hi",
                   "market_dem", "polymarket", "kalshi"]


def _history_row(date, race, kind, p_dem, p_rep, median, lo, hi, markets: dict) -> dict:
    poly, kal = (markets.get(s, {}).get("p_dem") for s in ("Polymarket", "Kalshi"))
    have = [v for v in (poly, kal) if v is not None]
    return {"date": date, "race": race, "kind": kind, "p_dem": p_dem, "p_rep": p_rep,
            "median": median, "lo": lo, "hi": hi,
            "market_dem": round(sum(have) / len(have), 4) if have else None, "polymarket": poly, "kalshi": kal}


def history_rows(f26: dict | None = None, f28: dict | None = None, kind: str = "daily") -> list[dict]:
    rows = []
    if f26:
        for chamber in ("senate", "house"):
            s = f26[chamber]["summary"]
            rows.append(_history_row(f26["as_of"], chamber, kind, s["p_dem_control"], s["p_rep_control"],
                                     s["dem_median"], s["dem_lo"], s["dem_hi"], f26[chamber]["markets"]))
    if f28:
        for m in f28["matchups"]:
            s = m["summary"]
            rows.append(_history_row(f28["as_of"], f"president:{m['id']}", kind, s["p_dem"], s["p_rep"],
                                     s["ev_median"], s["ev_lo"], s["ev_hi"], f28["markets"]))
    return rows


def load_history() -> pd.DataFrame:
    try:
        return pd.read_csv(HISTORY_CSV, dtype={"date": str})
    except FileNotFoundError:
        return pd.DataFrame(columns=HISTORY_COLUMNS)


def save_history(rows: list[dict]) -> pd.DataFrame:
    """Add rows, replacing any already there for the same date and race
    (if the site is rebuilt twice in a day, the later run wins)."""
    old, new = load_history(), pd.DataFrame(rows, columns=HISTORY_COLUMNS)
    keys = set(zip(new["date"], new["race"]))
    old = old[[(d, r) not in keys for d, r in zip(old["date"], old["race"])]]
    out = pd.concat([df for df in (old, new) if not df.empty]).sort_values(["race", "date"])
    out.to_csv(HISTORY_CSV, index=False)
    return out


# data/race_forecast_history.csv: every 2026 Senate race and House district, every day.
# The Election Day rows are what score_forecast.py grades against the results.
RACE_HISTORY_CSV = "data/race_forecast_history.csv"
RACE_HISTORY_COLUMNS = ["date", "chamber", "race", "p_dem", "median", "lo", "hi",
                        "polls", "fundamentals", "market_p", "blended"]


def race_history_rows(f26: dict) -> list[dict]:
    return [{"date": f26["as_of"], "chamber": chamber, "race": r["key"],
             **{c: r[c] for c in RACE_HISTORY_COLUMNS[3:]}}
            for chamber in ("senate", "house") for r in f26[chamber]["races"]]


def save_race_history(rows: list[dict]) -> None:
    """Add rows, replacing any already there for the same date, chamber and race."""
    try:
        old = pd.read_csv(RACE_HISTORY_CSV, dtype={"date": str})
    except FileNotFoundError:
        old = pd.DataFrame(columns=RACE_HISTORY_COLUMNS)
    new = pd.DataFrame(rows, columns=RACE_HISTORY_COLUMNS)
    keys = set(zip(new["date"], new["chamber"], new["race"]))
    old = old[[k not in keys for k in zip(old["date"], old["chamber"], old["race"])]]
    out = pd.concat([df for df in (old, new) if not df.empty]).sort_values(["date", "chamber", "race"])
    out.to_csv(RACE_HISTORY_CSV, index=False)


def history_series(hist: pd.DataFrame, race: str) -> list[dict]:
    h = hist[hist["race"] == race]
    cols = ["date", "kind", "p_dem", "p_rep", "median", "lo", "hi", "market_dem"]
    return [{c: (None if pd.isna(r[c]) else r[c]) for c in cols} for _, r in h.iterrows()]


# ---------------------------------------------------------------------------
# Sanity checks
# ---------------------------------------------------------------------------
def _check():
    t = days_until(ELECTION_2026)
    w = base_weights(t)
    print(f"{t} days out: polls {w['polls']:.0%}, markets {w['markets']:.0%}, fundamentals {w['fundamentals']:.0%}")
    print(f"Two years out: polls {base_weights(730)['polls']:.0%}")

    def one(polls, fund=None, market=None):
        r = [{"key": "X", "region_state": "PA", "polls": polls, "fundamentals": fund,
              "market": None if market is None else {"p": market, "sources": {}}}]
        return forecast_races(r, t, 4.0)[0][0]["p_dem"]

    even = one(0.0)
    print(f"Race at 0 margin: {even:.1%} (want ~50%)")
    assert abs(even - 0.5) < 0.02
    ps = [one(m) for m in (-6, -3, 0, 3, 6)]
    print("Margins -6..+6 ->", " ".join(f"{p:.0%}" for p in ps), "(want rising)")
    assert all(a < b for a, b in zip(ps, ps[1:]))
    assert one(0.0, market=0.8) > one(0.0, market=0.5) > one(0.0, market=0.2)
    assert one(0.0, fund=5.0) > even
    print("Market and fundamentals nudges move the right way.")

    f = forecast_2026()
    for ch in ("senate", "house"):
        s, m = f[ch]["summary"], f[ch]["markets"]
        print(f"{ch.title()}: Democrats control {s['p_dem_control']:.0%} (median {s['dem_median']} seats, "
              f"80% range {s['dem_lo']}-{s['dem_hi']}); markets: "
              + ", ".join(f"{k} {v['p_dem']:.0%}" for k, v in m.items()))
    print("Closest Senate races:")
    for r in sorted(f["senate"]["races"], key=lambda r: abs(r["p_dem"] - 0.5))[:10]:
        print(f"  {r['key']}  D {r['p_dem']:6.1%}  blended {r['blended']:+5.1f}  polls {r['polls']:+5.1f}  "
              f"fund {r['fundamentals']:+5.1f}  market {r['market_p'] if r['market_p'] is not None else '—'}")


    f = forecast_2028()
    print(f"2028 ({f['days_out']} days out): weights {f['weights']}; fundamentals {f['environment']}; "
          f"markets {f['market']}")
    for m in f["matchups"]:
        s = m["summary"]
        print(f"  {m['dem']:14} vs {m['rep']:6} D {s['p_dem']:.0%}  R {s['p_rep']:.0%}  tie {s['p_tie']:.1%}  "
              f"EV median {s['ev_median']} ({s['ev_lo']}-{s['ev_hi']})  national: {m['national']}")


if __name__ == "__main__":
    _check()
