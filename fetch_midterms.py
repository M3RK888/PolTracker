"""
fetch_midterms.py — 2026 Senate and House data from Wikipedia.

Reads the exact wikitext (?action=raw) of:
  "2026 United States Senate elections"                 seats up, incumbents, Cook PVI
  "2026 United States Senate (special) election in X"   each race's general-election polls
  "2026 United States House of Representatives elections"
                                                        every district's 2026 Cook PVI,
                                                        incumbent and candidates, plus the
                                                        generic-ballot aggregates

and writes:
  data/senate_2026_races.csv      one row per Senate race
  data/senate_2026_polls.csv      general-election polls, same columns as polls_2028.csv
  data/house_2026_districts.csv   one row per House district (435)
  data/generic_ballot_2026.csv    generic congressional ballot averages by aggregator

Cook's 2026 PVI is computed for the districts actually used in 2026, so states
that redrew their maps mid-decade are handled without 2024 results for the new lines.

Run (refetches every page; takes about a minute):
    python fetch_midterms.py
"""

import csv
import re
import sys
import time
from collections import Counter
from pathlib import Path

import wikitables as w

SENATE_ARTICLE = "2026 United States Senate elections"
HOUSE_ARTICLE = "2026 United States House of Representatives elections"
STATE_CODES = {
    "Alabama": "AL", "Alaska": "AK", "Arizona": "AZ", "Arkansas": "AR", "California": "CA",
    "Colorado": "CO", "Connecticut": "CT", "Delaware": "DE", "Florida": "FL", "Georgia": "GA",
    "Hawaii": "HI", "Idaho": "ID", "Illinois": "IL", "Indiana": "IN", "Iowa": "IA", "Kansas": "KS",
    "Kentucky": "KY", "Louisiana": "LA", "Maine": "ME", "Maryland": "MD", "Massachusetts": "MA",
    "Michigan": "MI", "Minnesota": "MN", "Mississippi": "MS", "Missouri": "MO", "Montana": "MT",
    "Nebraska": "NE", "Nevada": "NV", "New Hampshire": "NH", "New Jersey": "NJ", "New Mexico": "NM",
    "New York": "NY", "North Carolina": "NC", "North Dakota": "ND", "Ohio": "OH", "Oklahoma": "OK",
    "Oregon": "OR", "Pennsylvania": "PA", "Rhode Island": "RI", "South Carolina": "SC",
    "South Dakota": "SD", "Tennessee": "TN", "Texas": "TX", "Utah": "UT", "Vermont": "VT",
    "Virginia": "VA", "Washington": "WA", "West Virginia": "WV", "Wisconsin": "WI", "Wyoming": "WY",
}
POLL_COLUMNS = ["state", "pollster", "start_date", "end_date", "sample_size", "voter_type",
                "dem_candidate", "rep_candidate", "dem", "rep", "source"]
VOTER_RANK = {"LV": 0, "RV": 1, "V": 2, "A": 3, None: 4}


def pvi(cell: str) -> float | None:
    """'{{Shading PVI|R|15}}' -> -15 (Democratic lean positive); EVEN -> 0."""
    m = re.search(r"PVI\|\s*(EVEN|[RD])\s*(?:[|+]\s*(?:value\s*=\s*)?(\d+(?:\.\d+)?))?", cell, re.I)
    if not m:
        return None
    if m.group(1).upper() == "EVEN":
        return 0.0
    if m.group(2) is None:                        # malformed cell, e.g. '{{Shading PVI|R}}'
        return None
    return float(m.group(2)) * (1 if m.group(1).upper() == "D" else -1)


def candidates(cell: str) -> list[tuple[str, str]]:
    """Plainlist of '*{{Party stripe|...}}[[Name]] (Republican)' -> [('Name', 'R'), ...]."""
    return [(name, code) for name, code, _, _ in candidate_results(cell)]


def candidate_results(cell: str) -> list[tuple[str, str, float | None, bool]]:
    """Like candidates(), plus each one's vote share and whether they won. Before an
    election the share is None; after, lines read '{{Aye}} [[Name]] (Republican) 50.5%'
    (the check mark is on the winner)."""
    out = []
    lines = cell.split("\n")
    bulleted = any(l.strip().startswith("*") for l in lines)
    for line in lines:
        # Usually one '*' line per candidate; an unopposed winner is a single line without one
        if not (line.strip().startswith("*") or (not bulleted and "party stripe" in line.lower())):
            continue
        won = bool(re.search(r"\{\{\s*(Aye|Yes|Tick|Check mark)", line, re.I))
        m = re.search(r"^(.*)\(([^()]*)\)\s*(?:([\d.]+)\s*%)?\s*$", w.plain(line.lstrip("* ")))
        if not m:
            continue
        party = m.group(2)
        code = ("D" if re.search(r"Democrat|DFL", party) else "R" if "Republican" in party
                else "I" if re.search(r"Independent|Nonpartisan|Forward", party) else "O")
        out.append((m.group(1).strip(), code, float(m.group(3)) if m.group(3) else None, won))
    return out


def result_cell(row: list[str]) -> str:
    """The candidates cell, rejoined: a collapsible "Others" list inside it has lines
    starting with '|', which the table reader takes for extra cells."""
    i = next((i for i, c in enumerate(row) if re.search(r"plainlist|party stripe", c, re.I)), len(row) - 1)
    return "\n".join(row[i:])


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def surname(name: str) -> str:
    """Display key used in poll rows, e.g. 'Roy Cooper' -> 'Cooper'; keeps
    initials when needed to tell same-surname candidates apart (Alaska's two Dan Sullivans)."""
    return name.split()[-1]


# ---------------------------------------------------------------------------
# Senate
# ---------------------------------------------------------------------------
def senate(year: int = 2026, refresh: bool = True, with_polls: bool = True) -> tuple[list[dict], list[dict]]:
    """Races and polls for one year's Senate elections. Past years (for backtest.py)
    work too: their articles have the same layout, plus results once decided.
    refresh=False reuses cached copies of the articles; with_polls=False skips each
    race's own article (one page instead of ~35, enough for results)."""
    lines = w.raw_page(SENATE_ARTICLE.replace("2026", str(year)), refresh).split("\n")
    races = []
    # Regular races, plus the Florida and Ohio specials, which have their own table
    spans = [w.section(lines, r"^Special elections during the preceding Congress$"),
             w.section(lines, r"^Elections leading to the next Congress$")]
    for t in (t for a, b in spans for t in w.tables(lines[a:b])):
        for row in t["rows"]:
            title = re.search(rf"\[\[({year} United States Senate (?:special )?election in ([^|\]]+))", row[0])
            if not title:
                continue
            state = title.group(2).strip()
            party = w.plain(w.cell_parts(row[3])[1])
            races.append({
                "state": STATE_CODES[state], "state_name": state, "article": title.group(1),
                "special": "special" in title.group(1),
                "pvi": pvi(row[1]), "incumbent": w.plain(row[2]),
                "incumbent_party": "R" if "Republican" in party else "D" if re.search("Democrat|DFL", party) else "I",
                "listed": candidates(row[-1]), "results": candidate_results(result_cell(row)),
            })

    polls = []
    for race in races if with_polls else []:
        time.sleep(0.3)
        lines = w.raw_page(race["article"], refresh).split("\n")
        g0, g1 = w.section(lines, r"^General election$", 2)
        p0, p1 = w.section(lines, r"^Polling$", 3, g0) if g0 >= 0 else (-1, -1)
        rows = []
        if 0 <= p0 < g1:
            seg = lines[p0:min(p1, g1)]
            # Stop at hypothetical matchups (other possible nominees)
            hyp = next((i for i, l in enumerate(seg) if re.search(r"hypothetical", l, re.I)), len(seg))
            for t in w.tables(seg[:hyp]):
                rows += w.poll_rows(t)

        # The main matchup: strongest Republican vs strongest non-Republican across all polls
        share = Counter()
        party_of = {}
        for r in rows:
            for name, v in r["values"].items():
                if v is not None:
                    share[name] += v
                    party_of[name] = r["parties"][name]
        reps = [n for n in share if party_of[n] == "R"]
        opps = [n for n in share if party_of[n] != "R" and party_of[n] in ("D", "I")]
        if reps and opps:
            rep = max(reps, key=share.get)
            opp = max(opps, key=share.get)
            opp_party = party_of[opp]
        else:                                     # no polls: use the listed nominees
            listed = race["listed"]
            rep = next((n for n, p in listed if p == "R"), "")
            opp, opp_party = next(((n, p) for n, p in listed if p == "D"),
                                  next(((n, p) for n, p in listed if p == "I"), ("", "D")))
        race.update(rep_candidate=rep, opp_candidate=opp, opp_party=opp_party)

        # One row per poll: likely voters first, then the first row listed (for
        # ranked-choice polls in Alaska and Maine that's the first-choice round)
        best = {}
        for r in rows:
            d, rv = r["values"].get(opp), r["values"].get(rep)
            if d is None or rv is None:
                continue
            key = (r["pollster"], r["end"])
            if key not in best or VOTER_RANK[r["voter_type"]] < VOTER_RANK[best[key]["voter_type"]]:
                best[key] = {**r, "dem": d, "rep": rv}
        for r in best.values():
            polls.append({
                "state": race["state"], "race": race["state"] + ("-special" if race["special"] else ""),
                "pollster": r["pollster"], "start_date": r["start"] or "",
                "end_date": r["end"], "sample_size": r["sample_size"] or "",
                "voter_type": r["voter_type"] if r["voter_type"] in ("LV", "RV", "A") else "",
                "dem_candidate": surname(opp), "rep_candidate": surname(rep),
                "dem": r["dem"], "rep": r["rep"],
                "source": r["source"] or "https://en.wikipedia.org/wiki/" + race["article"].replace(" ", "_"),
            })
    polls.sort(key=lambda p: (p["state"], p["end_date"]), reverse=True)
    return races, polls


# ---------------------------------------------------------------------------
# House
# ---------------------------------------------------------------------------
def house(year: int = 2026, refresh: bool = True) -> tuple[list[dict], list[dict]]:
    lines = w.raw_page(HOUSE_ARTICLE.replace("2026", str(year)), refresh).split("\n")
    districts = {}
    for name, code in STATE_CODES.items():
        a, b = w.section(lines, rf"^{re.escape(name)}$", 2)
        if a < 0:
            raise SystemExit(f"House article: no section for {name}")
        for t in w.tables(lines[a:b]):
            for row in t["rows"]:
                m = re.search(r"\{\{ushr\|([A-Z]{2})\|(\d+|AL)\|", row[0])
                if not m or m.group(1) != code:
                    continue
                num = 0 if m.group(2) == "AL" else int(m.group(2))
                did = f"{code}-{'AL' if num == 0 else f'{num:02d}'}"
                if did in districts:
                    continue                      # second incumbent row in a merged district
                cands = candidates(row[-1])
                party_cell = next((w.plain(w.cell_parts(c)[1]) for c in row[2:6]
                                   if re.fullmatch(r"(Democratic|Republican|DFL|Independent)",
                                                   w.plain(w.cell_parts(c)[1]))), "")
                incumbent = w.plain(w.cell_parts(row[2])[1])
                new_seat = "new seat" in incumbent.lower() or incumbent.startswith("None")
                districts[did] = {
                    "district": did, "state": code, "number": num, "pvi": pvi(row[1]),
                    "incumbent": "" if new_seat else incumbent,
                    "incumbent_party": "" if new_seat else ("D" if re.match("Democrat|DFL", party_cell)
                                                            else "R" if party_cell == "Republican" else party_cell[:1]),
                    "dem_candidate": next((n for n, p in cands if p == "D"), ""),
                    "rep_candidate": next((n for n, p in cands if p == "R"), ""),
                    "other_candidates": "; ".join(f"{n} ({p})" for n, p in cands if p not in ("D", "R")),
                    "results": candidate_results(result_cell(row)),
                }

    return sorted(districts.values(), key=lambda d: (d["state"], d["number"])), generic_ballot(lines)


def generic_ballot(lines: list[str]) -> list[dict]:
    """The generic-ballot aggregator table. Older versions of the article keep it under
    "Opinion polling", so fall back to the section markers around the table."""
    a, b = w.section(lines, r"^Generic congressional ballot aggregate polls$")
    if a < 0:
        a = next((i for i, l in enumerate(lines) if 'section begin="GenericBallotAgg"' in l), -1)
        b = next((i for i, l in enumerate(lines) if 'section end="GenericBallotAgg"' in l), len(lines)) + 1
    generic = []
    for t in w.tables(lines[a:b]):
        header = [w.plain(w.cell_parts(h)[1]).lower() for h in t["header"]]
        i_r = next(i for i, h in enumerate(header) if h.startswith("republican"))
        i_d = next(i for i, h in enumerate(header) if h.startswith("democrat"))
        for row in t["rows"]:
            if len(row) != len(header) or "average" in w.plain(row[0]).lower():
                continue
            start, end = w.parse_dates(w.cell_parts(row[1])[1])
            _, updated = w.parse_dates(w.cell_parts(row[2])[1])
            d, r = w.pct(w.cell_parts(row[i_d])[1]), w.pct(w.cell_parts(row[i_r])[1])
            if d is None or r is None:
                continue
            generic.append({"source": w.plain(w.cell_parts(row[0])[1]), "start_date": start or "",
                            "end_date": end or "", "updated": updated or end or "",
                            "dem": d, "rep": r, "url": w.first_url(row[0]) or ""})
    return generic


# ---------------------------------------------------------------------------
def check(races, polls, districts, generic) -> None:
    problems = []
    if len(races) != 35:
        problems.append(f"{len(races)} Senate races, expected 35")
    if Counter(r["incumbent_party"] for r in races) != Counter({"R": 22, "D": 13}):
        problems.append(f"Senate seats up by party: {Counter(r['incumbent_party'] for r in races)}")
    if len(districts) != 435:
        problems.append(f"{len(districts)} House districts, expected 435")
    missing_pvi = [d["district"] for d in districts if d["pvi"] is None] + [r["state"] for r in races if r["pvi"] is None]
    if missing_pvi:
        problems.append(f"missing PVI: {missing_pvi}")
    if not generic:
        problems.append("no generic ballot averages found")
    if len(polls) < 100:
        problems.append(f"only {len(polls)} Senate polls parsed")
    if problems:
        raise SystemExit("fetch_midterms.py: validation failed\n  " + "\n  ".join(problems))


if __name__ == "__main__":
    races, polls = senate()
    districts, generic = house()
    check(races, polls, districts, generic)
    write_csv("data/senate_2026_races.csv", races,
              ["state", "state_name", "special", "pvi", "incumbent", "incumbent_party",
               "rep_candidate", "opp_candidate", "opp_party", "article"])
    write_csv("data/senate_2026_polls.csv", polls, POLL_COLUMNS)
    write_csv("data/house_2026_districts.csv", districts,
              ["district", "state", "number", "pvi", "incumbent", "incumbent_party",
               "dem_candidate", "rep_candidate", "other_candidates"])
    write_csv("data/generic_ballot_2026.csv", generic,
              ["source", "start_date", "end_date", "updated", "dem", "rep", "url"])
    no_polls = [r["state"] for r in races if not any(p["state"] == r["state"] for p in polls)]
    print(f"Senate: {len(races)} races, {len(polls)} polls (no polls: {', '.join(no_polls) or 'none'})")
    print(f"House: {len(districts)} districts; generic ballot from {len(generic)} aggregators")
