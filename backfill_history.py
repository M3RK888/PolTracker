"""
backfill_history.py — rebuild the 2026 forecast for past days, for the tracker charts.

The daily build records one point a day from the day the forecast launched
(October 22, 2026). This script fills in the days before that, back to START,
by running the same model on the data as it stood on each day:

  Senate polls         polls whose fieldwork had ended by that day
  generic ballot       Wikipedia's aggregator table as it read that day
                       (from the article's revision history)
  approval             Wikipedia's approval average as it read that day
  market prices        each race's Polymarket / Kalshi price at the end of that day
                       (from the markets' public price-history data)

Two things are today's, not the past's: the list of races, candidates and
partisan leans, and a poll counts from the day its fieldwork ended even if it
was published a few days later. These rows are marked "reconstructed".

Run once (takes several minutes; Wikipedia pages are cached in data/.cache):
    python backfill_history.py
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

import fetch_markets as fmk
import fetch_midterms as fmt
import forecast_model as model
import midterms

START = pd.Timestamp("2026-08-01")
AGENT = fmk.AGENT
CACHE = Path("data/.cache/revisions")
WIKI_API = "https://en.wikipedia.org/w/api.php"


def get_json(url: str, params: dict) -> dict:
    """Like fetch_markets.get_json, but backs off for longer when a site says
    it's getting too many requests (Wikipedia does, for old page versions)."""
    for attempt in range(6):
        try:
            return fmk.get_json(url, params)
        except urllib.error.HTTPError as e:
            if e.code != 429 or attempt == 5:
                raise
            time.sleep(int(e.headers.get("Retry-After") or 0) or 15 * (attempt + 1))


# ---------------------------------------------------------------------------
# Wikipedia: the page as it read at the end of each day
# ---------------------------------------------------------------------------
def revisions(title: str, since: pd.Timestamp) -> list[tuple[pd.Timestamp, int]]:
    out, cont = [], {}
    while True:
        d = get_json(WIKI_API, {"action": "query", "prop": "revisions", "titles": title, "rvprop": "ids|timestamp",
                                "rvlimit": 500, "rvend": since.strftime("%Y-%m-%dT00:00:00Z"), "format": "json",
                                "formatversion": 2, **cont})
        out += [(pd.Timestamp(r["timestamp"]).tz_localize(None), r["revid"]) for r in d["query"]["pages"][0]["revisions"]]
        if "continue" not in d:
            return sorted(out)
        cont = d["continue"]


def revision_text(revid: int) -> str:
    path = CACHE / f"{revid}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8")
    d = get_json(WIKI_API, {"action": "query", "prop": "revisions", "revids": revid, "rvprop": "content",
                            "rvslots": "main", "format": "json", "formatversion": 2})
    text = d["query"]["pages"][0]["revisions"][0]["slots"]["main"]["content"]
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    time.sleep(1)                                 # be gentle with Wikipedia
    return text


def as_of_each_day(title: str, days: list[pd.Timestamp], parse) -> dict:
    """{day: parse(page text as of the end of that day)}; a day whose version can't be
    parsed borrows the nearest earlier day's result."""
    revs = revisions(title, days[0] - pd.Timedelta(days=30))
    pick = {}
    for day in days:
        before = [rid for ts, rid in revs if ts < day + pd.Timedelta(days=1)]
        if before:
            pick[day] = before[-1]
    texts = {rid: revision_text(rid) for rid in sorted(set(pick.values()))}
    out, last = {}, None
    for day in days:
        try:
            last = parse(texts[pick[day]])
        except Exception as e:                    # an odd old version: keep the previous day's numbers
            print(f"  {title} on {day.date()}: couldn't read ({type(e).__name__}); using the day before")
        if last is not None:
            out[day] = last
    return out


def generic_from_text(text: str) -> list[dict]:
    g = fmt.generic_ballot(text.split("\n"))
    if not g:
        raise ValueError("no generic ballot table")
    return g


# ---------------------------------------------------------------------------
# Markets: closing price each day
# ---------------------------------------------------------------------------
def price_history(row: dict, start: pd.Timestamp, end: pd.Timestamp) -> list[tuple[int, float]]:
    """[(unix time, probability_dem)] for one market row from fetch_markets."""
    ref, t0, t1 = row["ref"], int(start.timestamp()), int(end.timestamp())
    if row["source"] == "Polymarket":
        d = get_json("https://clob.polymarket.com/prices-history",
                     {"market": ref["token"], "startTs": t0, "fidelity": 1440})   # (it refuses long startTs-endTs ranges)
        pts = [(p["t"], float(p["p"])) for p in d.get("history", [])]
    else:
        d = get_json(f"https://api.elections.kalshi.com/trade-api/v2/series/{ref['series']}/markets/{ref['ticker']}/candlesticks",
                     {"start_ts": t0, "end_ts": t1, "period_interval": 1440})
        pts = []
        for c in d.get("candlesticks", []):
            close = c.get("price", {}).get("close_dollars")
            bid, ask = c.get("yes_bid", {}).get("close_dollars"), c.get("yes_ask", {}).get("close_dollars")
            p = float(close) if close else fmk.mid_price(bid, ask, None)
            if p is not None:
                pts.append((c["end_period_ts"], p))
    return [(t, 1 - p if ref["invert"] else p) for t, p in pts]


def markets_by_day(days: list[pd.Timestamp]) -> dict[pd.Timestamp, pd.DataFrame]:
    print("Reading today's market list from Polymarket and Kalshi...")
    rows = fmk.polymarket_rows() + fmk.kalshi_rows()
    best = {}
    for r in rows:
        if r["race"].startswith(("senate:", "house:", "control:senate", "control:house")):
            k = (r["race"], r["source"])
            if k not in best or r["volume"] > best[k]["volume"]:
                best[k] = r
    rows = [r for r in best.values() if r["volume"] >= model.MIN_VOLUME or r["race"].startswith("control:")]
    start, end = days[0] - pd.Timedelta(days=3), days[-1] + pd.Timedelta(days=2)
    print(f"Fetching price history for {len(rows)} markets...")

    done = []

    def one(r):
        try:
            h = price_history(r, start, end)
        except OSError as e:
            print(f"  no history for {r['race']} ({r['source']}): {e}", flush=True)
            h = []
        done.append(1)
        if len(done) % 100 == 0:
            print(f"  {len(done)} of {len(rows)}", flush=True)
        return h
    with ThreadPoolExecutor(4) as ex:
        hists = list(ex.map(one, rows))

    out = {}
    for day in days:
        # A day's price is the last one recorded by the end of that day in US Eastern time (04:00 UTC next day)
        cutoff = (day + pd.Timedelta(days=1, hours=5)).timestamp()
        recs = []
        for r, h in zip(rows, hists):
            pts = [p for t, p in h if t <= cutoff]
            if pts:
                recs.append({"race": r["race"], "source": r["source"], "probability_dem": round(pts[-1], 4),
                             "volume": r["volume"], "label": r["label"], "fetched_at": f"{day.date()}"})
        out[day] = pd.DataFrame(recs)
    return out


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    today = midterms.AS_OF
    days = list(pd.date_range(START, today - pd.Timedelta(days=1)))
    print(f"Rebuilding {len(days)} days, {days[0].date()} to {days[-1].date()}")
    print("Reading old versions of the Wikipedia articles...")
    generic = as_of_each_day(fmt.HOUSE_ARTICLE, days, generic_from_text)
    approval = as_of_each_day(fmk.APPROVAL_ARTICLE, days, fmk.approval_from_text)
    markets = markets_by_day(days)

    rows = []
    for day in days:
        midterms.AS_OF = model.AS_OF = day
        f = model.forecast_2026(markets[day], approval[day], generic[day])
        rows += model.history_rows(f26=f, kind="reconstructed")
        s, h = f["senate"]["summary"], f["house"]["summary"]
        print(f"  {day.date()}: environment {f['environment']['polls']:+.1f}, approval {approval[day]['net']:+.1f}, "
              f"markets {len(markets[day])} -> Senate D {s['p_dem_control']:.0%}, House D {h['p_dem_control']:.0%}")
    model.save_history(rows)
    print(f"Saved {len(rows)} rows to {model.HISTORY_CSV}")
