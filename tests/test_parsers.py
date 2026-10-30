"""
Tests for the readers that turn Wikipedia pages into data. They run on saved
excerpts of real pages (tests/fixtures), so they work offline and catch it when
a format quirk breaks a reader. Run all tests with:

    python -m pytest
"""

from pathlib import Path

import fetch_markets
import fetch_midterms as fm
import scoring
import wikitables as w

FIX = Path(__file__).parent / "fixtures"


def lines(name: str) -> list[str]:
    return (FIX / name).read_text(encoding="utf-8").split("\n")


# --- Partisan-lean (Cook PVI) cells --------------------------------------------
def test_pvi_formats():
    assert fm.pvi("{{Shading PVI|R|15}}") == -15
    assert fm.pvi("{{shading PVI|D|2}}") == 2
    assert fm.pvi("{{Shading PVI|EVEN}}") == 0
    assert fm.pvi("{{Shading PVI|R|2.9}}") == -2.9          # decimals (2024 article)
    assert fm.pvi("{{Shading PVI|D|value=1}}") == 1         # value= form
    assert fm.pvi("{{Shading PVI|R}}") is None              # malformed: no number
    assert fm.pvi("no lean here") is None


# --- Candidate cells, before and after an election ------------------------------
def test_candidates_before_election():
    cell = "{{Plainlist}}\n* {{Party stripe|Republican Party (US)}}[[Ken Paxton]] (Republican)\n" \
           "* {{Party stripe|Democratic Party (US)}}[[James Talarico]] (Democratic)\n{{endplainlist}}"
    assert fm.candidates(cell) == [("Ken Paxton", "R"), ("James Talarico", "D")]
    assert all(share is None for _, _, share, _ in fm.candidate_results(cell))


def test_house_results_2024():
    rows = {}
    for t in w.tables(lines("house_results_2024_AL_AK_CO.wiki")):
        for row in t["rows"]:
            for key in ("AL|3|", "AK|AL|", "CO|1|"):
                if "{{ushr|" + key in row[0]:
                    rows[key] = fm.candidate_results(fm.result_cell(row))
    # Unopposed: one line, no bullet
    assert [(p, s, won) for _, p, s, won in rows["AL|3|"]] == [("R", 100.0, True)]
    # A collapsible "Others" list inside the cell mustn't hide the main candidates
    co = rows["CO|1|"]
    assert co[0][:4] == ("Diana DeGette", "D", 76.6, True) and co[1][1] == "R"
    # Ranked choice: both rounds listed; scoring uses the last one
    out = scoring.race_outcome(rows["AK|AL|"])
    assert out == {"dem_won": False, "margin": -2.4, "two_way": True}


# --- Poll tables ------------------------------------------------------------------
def test_senate_poll_table():
    polls = [p for t in w.tables(lines("senate_polls_texas_2026.wiki")) for p in w.poll_rows(t)]
    assert len(polls) >= 3                                  # the aggregator summary table is skipped
    p = polls[0]
    assert p["pollster"] and p["end"] and p["end"].startswith("2026-")
    assert p["values"]["James Talarico"] is not None and p["values"]["Ken Paxton"] is not None
    assert p["parties"]["James Talarico"] == "D" and p["parties"]["Ken Paxton"] == "R"


def test_duplicate_polling_heading():
    # Oct 28, 2026: an empty duplicate "Polling" heading sat above the real one on the
    # Texas page, and the polls under the second heading were missed.
    page = ["== General election ==", "=== Polling ===", "=== Polling ===",
            "{| class=\"wikitable\"", "! Poll source", "! Date(s)<br />administered", "! Sample<br />size",
            "! Margin<br />of error", "! Ken<br />Paxton (R)", "! James<br />Talarico (D)", "|-",
            "| YouGov", "| October 20–23, 2026", "| 1,000 (LV)", "| ± 3%", "| 45%", "| 49%", "|}",
            "=== Results ==="]
    rows = fm.general_election_poll_rows(page)
    assert len(rows) == 1 and rows[0]["values"] == {"Ken Paxton": 45.0, "James Talarico": 49.0}


def test_hypothetical_footnote_does_not_end_the_polls():
    # Alaska, Oct 2026: a footnote on a real poll says "Hypothetical scenario...". Only the
    # "Hypothetical polling" box below the real polls should end them.
    head = ["== General election ==", "=== Polling ===",
            "{| class=\"wikitable\"", "! Poll source", "! Date(s)<br />administered", "! Sample<br />size",
            "! Margin<br />of error", "! Dan S.<br />Sullivan (R)", "! Mary<br />Peltola (D)"]
    poll = lambda who, n, dates, s, p: ["|-", f"| {who}", f"| {dates}", f"| {n} (LV)", "| ± 3%", f"| {s}%", f"| {p}%"]
    page = (head + poll("Data for Progress (D)", 694, "October 8–16, 2026", 44, 47)
            + ["|-", '| Data for Progress (D){{efn|name="Sullivan Hypothetical"|Hypothetical scenario where voters '
               'who ranked Dan J. Sullivan are instead distributed to Dan S. Sullivan}}',
               "| October 8–16, 2026", "| 660 (LV)", "| ± 4%", "| 50%", "| 50%"]
            + poll("InsiderAdvantage (R)", 1200, "October 9–10, 2026", 43, 48) + ["|}",
            "{{hidden begin|titlestyle=background:#cff|title=Hypothetical polling}}"]
            + head[2:] + poll("Someone", 500, "June 1–2, 2026", 40, 40) + ["|}", "=== Results ==="])
    pollsters = [r["pollster"] for r in fm.general_election_poll_rows(page)]
    assert "InsiderAdvantage (R)" in pollsters                 # below the footnote: still read
    assert "Someone" not in pollsters                          # inside the hypothetical box: not read


def test_keep_previous_polls_when_a_race_vanishes():
    old = [{"state": "TX", "pollster": f"P{i}"} for i in range(6)] + [{"state": "IA", "pollster": "Q"}]
    new = [{"state": "IA", "pollster": "Q"}, {"state": "IA", "pollster": "R"}]
    polls, kept = fm.keep_previous_polls(new, old)
    assert kept == ["TX"]                                    # Texas lost all 6: restored
    assert sum(p["state"] == "TX" for p in polls) == 6
    assert sum(p["state"] == "IA" for p in polls) == 2       # Iowa's fresh polls untouched
    assert fm.keep_previous_polls(new, old[:3] + old[6:])[1] == []   # too few before to judge


def test_generic_ballot_table():
    g = fm.generic_ballot(lines("generic_ballot_2026-08-01.wiki"))
    assert len(g) == 6
    assert {"Decision Desk HQ", "RealClearPolitics"} <= {x["source"] for x in g}
    for x in g:
        assert 35 < x["dem"] < 60 and 35 < x["rep"] < 60 and x["dem"] > x["rep"]


def test_approval_average():
    a = fetch_markets.approval_from_text("\n".join(lines("approval_2026-10-21.wiki")))
    assert a["date"] == "2026-10-21"
    assert (a["approve"], a["disapprove"], a["net"]) == (36.1, 61.4, -25.2)
    assert fetch_markets.check_approval(a) == []


# --- Market prices -----------------------------------------------------------------
def test_mid_price():
    assert fetch_markets.mid_price("0.66", "0.67", "0.60") == 0.665     # tight spread: midpoint
    assert fetch_markets.mid_price("0.10", "0.40", "0.30") == 0.30      # wide spread: last trade
    assert fetch_markets.mid_price(None, None, None) is None


# --- Race outcomes ----------------------------------------------------------------
def test_race_outcome_rules():
    # An independent running against a Republican counts on the Democratic side
    assert scoring.race_outcome([("Osborn", "I", 52.0, True), ("Ricketts", "R", 48.0, False)]) == \
        {"dem_won": True, "margin": 4.0, "two_way": True}
    # Two Republicans at the top (Alaska 2022): winner known, margin not comparable
    out = scoring.race_outcome([("Murkowski", "R", 43.4, True), ("Tshibaka", "R", 42.6, False), ("Chesbro", "D", 10.4, False)])
    assert out["dem_won"] is False and out["margin"] is None and not out["two_way"]
    # No results yet
    assert scoring.race_outcome([("A", "D", None, False), ("B", "R", None, False)]) is None
