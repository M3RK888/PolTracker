"""
midterms.py — 2026 Senate and House estimates.

Same idea as the 2028 map, adapted to a midterm two weeks out:

  1. National environment = the average of the generic congressional ballot
     averages published by poll aggregators (Decision Desk HQ, RealClearPolitics, ...).
  2. Baseline for a state or district = environment + its partisan lean.
     Lean comes from the Cook Partisan Voting Index (PVI): R+5 means about 5 points
     more Republican than the nation in two-party share, which is about 10 points
     of margin. So baseline margin = environment + 2 × PVI.
  3. Senate races are then blended with their own polls, weighted like the 2028
     polls but with a much shorter half-life, since the race is nearly over.
     House districts are almost never polled, so they use the baseline alone.

Inputs come from fetch_midterms.py. Used by build_site.py.
"""

import numpy as np
import pandas as pd

from polling_2028 import RATING_COLORS, RATING_CUTOFFS, STATE_NAMES, rate

HALF_LIFE_DAYS = 21      # close to Election Day, last month's polls matter much less
WINDOW_DAYS = 120
PRIOR_WEIGHT = 1.5       # the baseline counts as about 1.5 fresh 600-person polls
AS_OF = pd.Timestamp.today().normalize()

SENATE_NOT_UP = {"R": 31, "D": 34}   # 32 Democrats + 2 independents who caucus with them
SENATE_SEATS, HOUSE_SEATS = 100, 435


def environment() -> tuple[float, list[dict]]:
    g = pd.read_csv("data/generic_ballot_2026.csv")
    g["margin"] = g["dem"] - g["rep"]
    return float(g["margin"].mean()), g.to_dict("records")


def poll_weights(polls: pd.DataFrame) -> pd.Series:
    age = (AS_OF - polls["end_date"]).dt.days.clip(lower=0)
    recency = 0.5 ** (age / HALF_LIFE_DAYS)
    size = np.sqrt(polls["sample_size"].clip(upper=5000) / 600)
    flooding = 1 / np.sqrt(polls.groupby(["state", "pollster"])["pollster"].transform("count"))
    return recency * size * flooding


def senate() -> dict:
    env, _ = environment()
    races = pd.read_csv("data/senate_2026_races.csv")
    polls = pd.read_csv("data/senate_2026_polls.csv", parse_dates=["start_date", "end_date"])
    polls["sample_reported"] = polls["sample_size"].notna()
    polls["sample_size"] = polls["sample_size"].fillna(polls["sample_size"].median())
    polls["margin"] = polls["dem"] - polls["rep"]
    recent = (polls["end_date"] <= AS_OF) & (polls["end_date"] > AS_OF - pd.Timedelta(days=WINDOW_DAYS))
    polls["used"] = recent
    polls["w"] = 0.0
    polls.loc[recent, "w"] = poll_weights(polls[recent])

    out = []
    for _, r in races.iterrows():
        baseline = env + 2 * r["pvi"]
        sp = polls[(polls["state"] == r["state"]) & polls["used"]]
        total = PRIOR_WEIGHT + sp["w"].sum()
        estimate = (PRIOR_WEIGHT * baseline + (sp["w"] * sp["margin"]).sum()) / total
        polls.loc[sp.index, "share"] = sp["w"] / total
        poll_avg = float(np.average(sp["margin"], weights=sp["w"])) if len(sp) else None
        out.append({
            "state": r["state"], "name": r["state_name"] + (" (special)" if r["special"] else ""),
            "special": bool(r["special"]), "pvi": r["pvi"],
            "incumbent": r["incumbent"], "incumbent_party": r["incumbent_party"],
            "rep": r["rep_candidate"], "opp": r["opp_candidate"], "opp_party": r["opp_party"],
            "baseline": round(baseline, 2), "poll_avg": None if poll_avg is None else round(poll_avg, 2),
            "estimate": round(estimate, 2), "rating": rate(estimate),
            "n_polls": int(len(sp)), "baseline_share": round(PRIOR_WEIGHT / total, 3),
        })

    segs = {k: 0 for k in RATING_COLORS}
    for r in out:
        segs[r["rating"]] += 1
    d = SENATE_NOT_UP["D"] + sum(v for k, v in segs.items() if k.endswith(" D"))
    rp = SENATE_NOT_UP["R"] + sum(v for k, v in segs.items() if k.endswith(" R"))

    poll_rows = []
    for _, p in polls.sort_values("end_date", ascending=False).iterrows():
        poll_rows.append({
            "state": p["state"], "pollster": p["pollster"],
            "start": None if pd.isna(p["start_date"]) else p["start_date"].strftime("%Y-%m-%d"),
            "end": p["end_date"].strftime("%Y-%m-%d"),
            "sample_size": int(p["sample_size"]) if p["sample_reported"] else None,
            "voter_type": None if pd.isna(p["voter_type"]) else p["voter_type"],
            "dem_candidate": p["dem_candidate"], "rep_candidate": p["rep_candidate"],
            "dem": float(p["dem"]), "rep": float(p["rep"]), "margin": round(float(p["margin"]), 2),
            "share": None if pd.isna(p.get("share")) else round(float(p["share"]), 3),
            "used": bool(p["used"]), "source": p["source"],
        })

    return {
        "as_of": AS_OF.strftime("%Y-%m-%d"), "environment": round(env, 2),
        "not_up": SENATE_NOT_UP, "seats": {"D": d, "R": rp, "Tossup": segs["Tossup"]},
        "segments": [{"rating": k, "seats": v} for k, v in segs.items()],
        "races": out, "polls": poll_rows,
        "settings": {"half_life_days": HALF_LIFE_DAYS, "window_days": WINDOW_DAYS, "prior_weight": PRIOR_WEIGHT},
    }


# ---------------------------------------------------------------------------
# House hex map: one hexagon per district, each state a contiguous blob grown
# around its rough geographic center. Positions within a state are not
# geographic; the point is that every seat is the same size.
# ---------------------------------------------------------------------------
CENTROIDS = {   # approximate (longitude, latitude)
    "AL": (-86.8, 32.8), "AZ": (-111.7, 34.3), "AR": (-92.4, 34.9), "CA": (-119.5, 37.2),
    "CO": (-105.5, 39.0), "CT": (-72.7, 41.6), "DE": (-75.5, 39.0), "FL": (-81.7, 28.6),
    "GA": (-83.4, 32.7), "ID": (-114.6, 44.4), "IL": (-89.2, 40.0), "IN": (-86.3, 39.9),
    "IA": (-93.5, 42.1), "KS": (-98.4, 38.5), "KY": (-85.3, 37.5), "LA": (-92.0, 31.0),
    "ME": (-69.2, 45.4), "MD": (-76.8, 39.0), "MA": (-71.8, 42.3), "MI": (-84.7, 43.6),
    "MN": (-94.3, 46.3), "MS": (-89.7, 32.7), "MO": (-92.5, 38.4), "MT": (-109.6, 47.0),
    "NE": (-99.8, 41.5), "NV": (-116.6, 39.3), "NH": (-71.6, 43.7), "NJ": (-74.7, 40.2),
    "NM": (-106.1, 34.4), "NY": (-75.5, 42.9), "NC": (-79.4, 35.5), "ND": (-100.5, 47.5),
    "OH": (-82.8, 40.3), "OK": (-97.5, 35.6), "OR": (-120.5, 43.9), "PA": (-77.6, 40.9),
    "RI": (-71.5, 41.7), "SC": (-80.9, 33.9), "SD": (-100.2, 44.4), "TN": (-86.3, 35.9),
    "TX": (-99.3, 31.5), "UT": (-111.7, 39.3), "VT": (-72.7, 44.1), "VA": (-78.8, 37.5),
    "WA": (-120.4, 47.4), "WV": (-80.6, 38.6), "WI": (-89.9, 44.6), "WY": (-107.5, 43.0),
    "AK": (-124.0, 25.0), "HI": (-113.0, 24.0),   # placed bottom-left, as on most US maps
}
HEX_DIRS = [(1, 0), (0, 1), (-1, 1), (-1, 0), (0, -1), (1, -1)]


def _centers(sizes: dict[str, int]) -> dict[str, np.ndarray]:
    """State centers in hex-pixel space (hex circumradius 1). Each state is a circle
    with the area of its hexes, placed at its geographic position, then circles are
    nudged apart until they don't overlap (a Dorling cartogram)."""
    scale = 1.47                                  # sized so 435 hexes fill the map
    origin = {s: np.array([(CENTROIDS[s][0] + 125) * 0.79 * scale, (49.5 - CENTROIDS[s][1]) * scale])
              for s in sizes}
    radius = {s: 0.909 * np.sqrt(n) + 0.45 for s, n in sizes.items()}   # hex area 2.598 = pi r^2 / n
    pos = {s: p.copy() for s, p in origin.items()}
    states = list(sizes)
    for _ in range(400):
        for i, a in enumerate(states):
            for b in states[i + 1:]:
                d = pos[b] - pos[a]
                dist = np.hypot(*d) or 1e-6
                overlap = radius[a] + radius[b] - dist
                if overlap > 0:                   # push apart, bigger states move less
                    push = d / dist * overlap
                    wa = radius[b] / (radius[a] + radius[b])
                    pos[a] -= push * wa
                    pos[b] += push * (1 - wa)
        for s in states:                          # gentle pull back toward the real location
            pos[s] += 0.02 * (origin[s] - pos[s])
    return pos


def hex_layout(districts: list[dict]) -> dict[str, tuple[int, int]]:
    by_state = {}
    for d in districts:
        by_state.setdefault(d["state"], []).append(d["id"])
    centers = _centers({s: len(v) for s, v in by_state.items()})

    def pixel(c):
        return np.array([np.sqrt(3) * (c[0] + c[1] / 2), 1.5 * c[1]])

    taken, out = set(), {}
    # Biggest first; each state grows outward from its center as one connected piece
    for s in sorted(by_state, key=lambda s: -len(by_state[s])):
        ctr = centers[s]
        r0 = ctr[1] / 1.5
        q0 = ctr[0] / np.sqrt(3) - r0 / 2
        near = sorted(((round(q0) + dq, round(r0) + dr) for dq in range(-12, 13) for dr in range(-12, 13)),
                      key=lambda c: np.hypot(*(pixel(c) - ctr)))
        blob = [next(c for c in near if c not in taken)]
        taken.add(blob[0])
        while len(blob) < len(by_state[s]):
            frontier = {(c[0] + dq, c[1] + dr) for c in blob for dq, dr in HEX_DIRS} - taken
            cell = min(frontier, key=lambda c: np.hypot(*(pixel(c) - ctr)))
            blob.append(cell)
            taken.add(cell)
        # number districts in reading order (top-left first), so 1, 2, 3... sit together
        blob.sort(key=lambda c: (c[1], c[0]))
        out.update(zip(sorted(by_state[s]), blob))
    return out


def house() -> dict:
    env, generic = environment()
    districts = pd.read_csv("data/house_2026_districts.csv", keep_default_na=False)
    out, segs = [], {k: 0 for k in RATING_COLORS}
    for _, d in districts.iterrows():
        est = env + 2 * float(d["pvi"])
        rating = rate(est)
        segs[rating] += 1
        out.append({
            "id": d["district"], "state": d["state"], "number": int(d["number"]),
            "name": f"{STATE_NAMES[d['state']]} {'at-large' if d['number'] == 0 else d['number']}",
            "pvi": float(d["pvi"]), "estimate": round(est, 2), "rating": rating,
            "incumbent": d["incumbent"], "incumbent_party": d["incumbent_party"],
            "dem": d["dem_candidate"], "rep": d["rep_candidate"], "others": d["other_candidates"],
        })
    layout = hex_layout(out)
    for d in out:
        d["q"], d["r"] = map(int, layout[d["id"]])
    dem = sum(v for k, v in segs.items() if k.endswith(" D"))
    rep = sum(v for k, v in segs.items() if k.endswith(" R"))
    return {
        "as_of": AS_OF.strftime("%Y-%m-%d"), "environment": round(env, 2),
        "generic": [{**g, "margin": round(g["margin"], 2)} for g in generic],
        "seats": {"D": dem, "R": rep, "Tossup": segs["Tossup"]},
        "segments": [{"rating": k, "seats": v} for k, v in segs.items()],
        "districts": out,
    }


if __name__ == "__main__":
    s, h = senate(), house()
    print(f"Environment: D{s['environment']:+.1f}")
    print(f"Senate: D {s['seats']['D']}, R {s['seats']['R']}, tossup {s['seats']['Tossup']}")
    for r in sorted(s["races"], key=lambda r: abs(r["estimate"]))[:12]:
        print(f"  {r['name']:22} {r['estimate']:+6.1f}  {r['rating']:17} polls {r['n_polls']:3}  "
              f"poll avg {r['poll_avg'] if r['poll_avg'] is not None else '—':>6}  baseline {r['baseline']:+.1f}")
    print(f"House: D {h['seats']['D']}, R {h['seats']['R']}, tossup {h['seats']['Tossup']}")
