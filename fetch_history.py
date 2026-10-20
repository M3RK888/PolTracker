"""
fetch_history.py — state-by-state presidential results, 1960-2024.

Source: the "Results by state" table on each election's Wikipedia page.
Those tables also record the electoral votes each candidate actually got in
each state, which matters for faithless electors, unpledged electors (1960),
Wallace (1968), and the Maine / Nebraska district splits.

Run once (pages are cached in data/.cache/wiki, so reruns are offline):
    pip install pandas lxml
    python fetch_history.py

Output:
    data/results_1960_2024.csv  one row per state per year, plus one row per
                                Maine / Nebraska congressional district
                                (unit = "ME-2" etc.) in years they split.

The script stops with an error if any year's national electoral vote totals
don't match the official count, so a Wikipedia table change can't slip in
bad numbers silently.
"""

import io
import re
import time
import urllib.request
from pathlib import Path

import lxml.html
import pandas as pd

CACHE = Path("data/.cache/wiki")
OUT = Path("data/results_1960_2024.csv")

# year: (Democratic nominee, Republican nominee)
NOMINEES = {
    1960: ("John F. Kennedy", "Richard Nixon"),
    1964: ("Lyndon B. Johnson", "Barry Goldwater"),
    1968: ("Hubert Humphrey", "Richard Nixon"),
    1972: ("George McGovern", "Richard Nixon"),
    1976: ("Jimmy Carter", "Gerald Ford"),
    1980: ("Jimmy Carter", "Ronald Reagan"),
    1984: ("Walter Mondale", "Ronald Reagan"),
    1988: ("Michael Dukakis", "George H. W. Bush"),
    1992: ("Bill Clinton", "George H. W. Bush"),
    1996: ("Bill Clinton", "Bob Dole"),
    2000: ("Al Gore", "George W. Bush"),
    2004: ("John Kerry", "George W. Bush"),
    2008: ("Barack Obama", "John McCain"),
    2012: ("Barack Obama", "Mitt Romney"),
    2016: ("Hillary Clinton", "Donald Trump"),
    2020: ("Joe Biden", "Donald Trump"),
    2024: ("Kamala Harris", "Donald Trump"),
}

# Official national electoral votes: (Dem, Rep, other). "Other" covers
# third parties, unpledged and faithless electors, and the 2000 DC abstention.
OFFICIAL_EV = {
    1960: (303, 219, 15), 1964: (486, 52, 0), 1968: (191, 301, 46), 1972: (17, 520, 1),
    1976: (297, 240, 1), 1980: (49, 489, 0), 1984: (13, 525, 0), 1988: (111, 426, 1),
    1992: (370, 168, 0), 1996: (379, 159, 0), 2000: (266, 271, 1), 2004: (251, 286, 1),
    2008: (365, 173, 0), 2012: (332, 206, 0), 2016: (227, 304, 7), 2020: (306, 232, 0),
    2024: (226, 312, 0),
}

STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "District of Columbia": "DC",
    "Florida": "FL", "Georgia": "GA", "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL",
    "Indiana": "IN", "Iowa": "IA", "Kansas": "KS", "Kentucky": "KY", "Louisiana": "LA",
    "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA", "Michigan": "MI", "Minnesota": "MN",
    "Mississippi": "MS", "Missouri": "MO", "Montana": "MT", "Nebraska": "NE", "Nevada": "NV",
    "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM", "New York": "NY",
    "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK", "Oregon": "OR",
    "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC", "South Dakota": "SD",
    "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT", "Virginia": "VA",
    "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
DC_NAMES = {"D.C.", "D. C.", "Washington, D.C.", "Washington D.C."}
ORDINALS = {"1st": 1, "2nd": 2, "3rd": 3}


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------
def page(year: int) -> str:
    path = CACHE / f"{year}.html"
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        url = f"https://en.wikipedia.org/wiki/{year}_United_States_presidential_election"
        req = urllib.request.Request(url, headers={"User-Agent": "pollmap-history-fetch/1.0"})
        path.write_bytes(urllib.request.urlopen(req, timeout=60).read())
        time.sleep(1)   # be polite to Wikipedia
    return path.read_text(encoding="utf-8")


def results_table(html: str) -> pd.DataFrame:
    """The 'Results by state' table: the first wikitable listing every state with
    multi-row headers. Some unrelated tables on these pages break read_html, so
    each candidate table is parsed on its own."""
    doc = lxml.html.fromstring(html)
    for t in doc.xpath("//table[contains(@class,'wikitable')]"):
        text = t.text_content()
        if "Alabama" not in text or "Wyoming" not in text:
            continue
        try:
            df = pd.read_html(io.StringIO(lxml.html.tostring(t, encoding="unicode")))[0]
        except (ValueError, IndexError):
            continue
        if len(df) >= 50 and isinstance(df.columns, pd.MultiIndex):
            return df
    raise ValueError("results-by-state table not found")


# ---------------------------------------------------------------------------
# Clean
# ---------------------------------------------------------------------------
def num(v) -> float:
    """'1,234,567' / '−1.2%' / '–' / '12[a]' -> a number (0 for blanks and dashes)."""
    if isinstance(v, (int, float)) and not pd.isna(v):
        return float(v)
    s = re.sub(r"\[.*?\]", "", str(v)).replace(",", "").replace("%", "").replace("−", "-").strip()
    try:
        return float(s)
    except ValueError:
        return 0.0


def unit_code(raw: str) -> str | None:
    """State name or district label -> 'PA', 'DC', 'ME-2', 'NE-1'; None for totals/junk."""
    name = re.sub(r"\[.*?\]", "", str(raw)).replace("†", "").replace("*", "").strip()
    name = re.sub(r"Tooltip.*", "", name).strip()
    if name in STATE_CODES:
        return STATE_CODES[name]
    if name in DC_NAMES:
        return "DC"
    m = re.match(r"^(ME|NE|Maine|Nebraska)(?:-|'s )(\d|1st|2nd|3rd)$", name)
    if m:
        st = {"Maine": "ME", "Nebraska": "NE"}.get(m[1], m[1])
        return f"{st}-{ORDINALS.get(m[2], m[2])}"
    return None


def sub_kind(label: str) -> str | None:
    label = label.replace("\xad", "").lower()
    if label in ("#", "vote", "votes"):
        return "votes"
    if label in ("electoral votes", "ev", "e"):
        return "ev"
    return None


def parse_year(year: int) -> pd.DataFrame:
    df = results_table(page(year))
    dem, rep = NOMINEES[year]
    # Collapse 3-level headers (1992) to (group, sub-column)
    cols = [(c[-2], c[-1]) for c in df.columns]

    def surname_matches(group: str, full_name: str) -> bool:
        return full_name.split()[-1] in group.split("/")[0]

    groups = {}          # group label -> {"votes": col index, "ev": col index}
    state_ev_col = total_col = None
    for i, (group, sub) in enumerate(cols):
        g = str(group)
        if i == 1 and sub_kind(str(sub)) == "ev":
            state_ev_col = i
        elif "total" in g.lower() and total_col is None:
            total_col = i
        elif not g.startswith("Unnamed") and not re.search(r"(?i)margin|swing|^state|^source", g):
            kind = sub_kind(str(sub))
            if kind:
                groups.setdefault(g, {})[kind] = i

    dem_g = next(g for g in groups if surname_matches(g, dem))
    rep_g = next(g for g in groups if surname_matches(g, rep))
    others = [g for g in groups if g not in (dem_g, rep_g)]

    rows = []
    for _, r in df.iterrows():
        unit = unit_code(r.iloc[0])
        if unit is None:
            continue

        def get(g, kind):
            return num(r.iloc[groups[g][kind]]) if kind in groups[g] else 0.0

        d, rp = get(dem_g, "votes"), get(rep_g, "votes")
        other_votes = {g: get(g, "votes") for g in others}
        cand_total = d + rp + sum(other_votes.values())
        total = num(r.iloc[total_col]) if total_col is not None else 0.0
        if total < cand_total:          # missing or a non-vote column; fall back
            total = cand_total

        ev_d, ev_r = get(dem_g, "ev"), get(rep_g, "ev")
        if state_ev_col is not None:
            ev_total = num(r.iloc[state_ev_col])
            ev_o = ev_total - ev_d - ev_r
        else:
            ev_o = sum(get(g, "ev") for g in others)
            ev_total = ev_d + ev_r + ev_o

        top_other = max(other_votes, key=other_votes.get) if other_votes else ""
        rows.append({
            "year": year, "unit": unit, "state": unit[:2], "is_district": "-" in unit,
            "dem_votes": int(d), "rep_votes": int(rp),
            "other_votes": int(total - d - rp), "total_votes": int(total),
            "ev_total": int(ev_total), "ev_dem": int(ev_d), "ev_rep": int(ev_r), "ev_other": int(ev_o),
            "top_other": clean_group(top_other), "top_other_votes": int(other_votes.get(top_other, 0)),
            "dem_candidate": dem, "rep_candidate": rep,
        })
    return pd.DataFrame(rows)


def clean_group(g: str) -> str:
    """'George Wallace American Independent' -> 'George Wallace (American Independent)'."""
    for party in ["American Independent", "Unpledged Democratic", "Independent / Reform",
                  "Independent", "Libertarian", "Green", "Reform", "Constitution",
                  "Socialist Labor", "New Alliance"]:
        if g.endswith(party):
            return f"{g[:-len(party)].strip()} ({party})"
    return g


# ---------------------------------------------------------------------------
def check(res: pd.DataFrame) -> None:
    problems = []
    for year, (d, r, o) in OFFICIAL_EV.items():
        y = res[res["year"] == year]
        got = (y["ev_dem"].sum(), y["ev_rep"].sum(), y["ev_other"].sum())
        if got != (d, r, o):
            problems.append(f"{year}: parsed EVs {got}, official {(d, r, o)}")
        states = set(y.loc[~y["is_district"], "state"])
        expected = 51 if year >= 1964 else 50     # DC first voted in 1964
        if len(states) != expected:
            problems.append(f"{year}: {len(states)} states parsed, expected {expected}")
    if problems:
        raise SystemExit("Validation failed:\n  " + "\n  ".join(problems))


if __name__ == "__main__":
    res = pd.concat([parse_year(y) for y in NOMINEES], ignore_index=True)
    check(res)
    res.to_csv(OUT, index=False)
    print(f"Wrote {OUT}: {len(res)} rows, {res['year'].nunique()} elections, EV totals match official counts")
