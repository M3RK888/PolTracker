"""
fetch_markets.py — prediction-market prices and presidential approval for the forecast.

Reads current prices from two public prediction markets (no account or key needed):

  Polymarket  https://gamma-api.polymarket.com/events
  Kalshi      https://api.elections.kalshi.com/trade-api/v2/events

the presidential approval average from Wikipedia's "Opinion polling on the
second Trump presidency" (exact page source, ?action=raw), and the national
unemployment rate from the St. Louis Fed (FRED series UNRATE, plain CSV).

Writes:
  data/markets.csv    one row per race per market:
                      race, source, probability_dem, volume, label, fetched_at
  data/approval.csv   date, approve, disapprove, net, source
  data/economy.csv    date, unemployment, year_ago, change, source

Race keys:
  control:senate, control:house, control:president   who controls the chamber / wins in 2028
  senate:TX                                          a 2026 Senate race
  house:TX-09                                        a 2026 House district (at-large: AK-AL)
  pres:PA                                            a state in the 2028 presidential race

probability_dem is the market's chance that the Democratic side wins. For Senate
races that includes independents running against the Republican (Nebraska, ...),
so it is computed as 1 minus the Republican's price. A market price of 0.67
means traders put the chance at about 67%.

Each price is the midpoint between the best bid and best ask (what buyers and
sellers are offering), or the last trade when the gap between them is wide.

Run:
    python fetch_markets.py
"""

import csv
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import wikitables
from fetch_midterms import STATE_CODES

AGENT = {"User-Agent": "pollmap-build/1.0 (https://github.com/M3RK888/pollmap)"}
POLYMARKET = "https://gamma-api.polymarket.com/events"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2/events"
APPROVAL_ARTICLE = "Opinion polling on the second Trump presidency"
STATE_NAME_RE = "|".join(sorted(STATE_CODES, key=len, reverse=True))   # longest first: "West Virginia" before "Virginia"


def get_json(url: str, params: dict) -> dict | list:
    full = url + "?" + urllib.parse.urlencode(params)
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(full, headers=AGENT), timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code < 500 or attempt == 3:      # a refused request won't succeed on retry
                raise
            time.sleep(2 + 3 * attempt)
        except OSError:
            if attempt == 3:
                raise
            time.sleep(2 + 3 * attempt)


def mid_price(bid, ask, last) -> float | None:
    """Midpoint of bid and ask when the spread is tight; otherwise the last trade."""
    bid, ask, last = (None if v in (None, "") else float(v) for v in (bid, ask, last))
    if bid is not None and ask is not None and 0 < ask and ask - bid <= 0.10:
        return (bid + ask) / 2
    return last if last else None


def district_key(state: str, num: str) -> str:
    return f"house:{state.upper()}-{'AL' if num.upper() in ('AL', '00') else num.zfill(2)}"


# ---------------------------------------------------------------------------
# Polymarket: events have one Yes/No market per outcome ("Will the Republicans win ...?")
# ---------------------------------------------------------------------------
def polymarket_events(tag: str) -> list[dict]:
    out, offset = [], 0
    while True:
        page = get_json(POLYMARKET, {"tag_slug": tag, "closed": "false", "limit": 100, "offset": offset})
        out += page
        if len(page) < 100:
            return out
        offset += 100


def polymarket_yes(m: dict) -> float | None:
    if not m.get("active") or m.get("closed"):
        return None
    prices = json.loads(m.get("outcomePrices") or "null")
    fallback = float(prices[0]) if prices else None
    return mid_price(m.get("bestBid"), m.get("bestAsk"), m.get("lastTradePrice") or fallback)


def polymarket_rows() -> list[dict]:
    events = {e["id"]: e for tag in ("midterms", "us-presidential-election") for e in polymarket_events(tag)}
    with open("data/senate_2026_races.csv", encoding="utf-8") as f:
        challengers = {r["state"]: r["opp_candidate"].split()[-1] for r in csv.DictReader(f)}
    rows = []
    for e in events.values():
        title, slug = e.get("title", "").strip(), e.get("slug", "")
        markets = e.get("markets", [])
        volume = sum(float(m.get("volume") or 0) for m in markets)

        used = {}                       # the market a price came from, for looking up its history

        def find(pattern):
            for m in markets:
                text = f"{m.get('question', '')} {m.get('groupItemTitle', '')}"
                if re.search(pattern, text, re.I):
                    p = polymarket_yes(m)
                    if p is not None:
                        used.update(token=json.loads(m["clobTokenIds"])[0], p=p)
                        return p
            return None

        race, p_dem = None, None
        if slug == "which-party-will-win-the-senate-in-2026":
            race, p_dem = "control:senate", find(r"Democratic Party control")
        elif slug == "which-party-will-win-the-house-in-2026":
            race, p_dem = "control:house", find(r"Democratic Party control")
        elif slug == "presidential-election-winner-2028-party" or re.fullmatch(r"which party wins 2028 us presidential election\??", title, re.I):
            race, p_dem = "control:president", find(r"Democrat")
        elif m := re.fullmatch(rf"({STATE_NAME_RE}) Senate Election Winner", title, re.I):
            state = STATE_CODES[m.group(1).title()]
            p_rep = find(r"Republicans? win|\(R\)")
            race, p_dem = f"senate:{state}", None if p_rep is None else 1 - p_rep
            if p_dem is None and state in challengers:
                # Some races (Alaska) are listed by candidate, not party: use the main challenger's market
                p_dem = find(rf"^Will .*\b{re.escape(challengers[state])} win")
        elif m := re.fullmatch(r"([A-Z]{2})-(\d{1,2}|AL) House Election Winner", title, re.I):
            p_rep = find(r"Republican Party win|Republicans? win|\(R\)")
            race, p_dem = district_key(m.group(1), m.group(2)), None if p_rep is None else 1 - p_rep
        elif m := re.fullmatch(rf"({STATE_NAME_RE}) Presidential Election Winner", title, re.I):
            p_rep = find(r"Republican")
            race, p_dem = f"pres:{STATE_CODES[m.group(1).title()]}", None if p_rep is None else 1 - p_rep
        if race and p_dem is not None and (e.get("endDate") or "")[:4] in ("2026", "2027", "2028", "2029"):
            rows.append({"race": race, "source": "Polymarket", "probability_dem": p_dem,
                         "volume": volume, "label": title,
                         "ref": {"token": used["token"], "invert": abs(p_dem - used["p"]) > 1e-9}})
    return rows


# ---------------------------------------------------------------------------
# Kalshi: events hold markets ending -D / -R (plus named independents)
# ---------------------------------------------------------------------------
def kalshi_events() -> list[dict]:
    out, cursor = [], ""
    while True:
        params = {"status": "open", "limit": 200, "with_nested_markets": "true"}
        if cursor:
            params["cursor"] = cursor
        page = get_json(KALSHI, params)
        out += page["events"]
        cursor = page.get("cursor")
        if not cursor or not page["events"]:
            return out


def kalshi_rows() -> list[dict]:
    rows = []
    for e in kalshi_events():
        ticker, title = e["event_ticker"], e.get("title", "").strip()
        markets = {m["ticker"].rsplit("-", 1)[-1]: m for m in e.get("markets", []) if m.get("status") == "active"}
        volume = sum(float(m.get("volume_fp") or 0) for m in e.get("markets", []))

        used = {}

        def price(side):
            m = markets.get(side)
            if m is None:
                return None
            used.update(series=e["series_ticker"], ticker=m["ticker"], invert=side == "R")
            return mid_price(m.get("yes_bid_dollars"), m.get("yes_ask_dollars"), m.get("last_price_dollars"))

        race, p_dem = None, None
        if ticker == "CONTROLS-2026":
            race, p_dem = "control:senate", price("D")
        elif ticker == "CONTROLH-2026":
            race, p_dem = "control:house", price("D")
        elif ticker == "KXPRESPARTY-2028":
            race, p_dem = "control:president", price("D")
        elif (m := re.fullmatch(rf"({STATE_NAME_RE}) Senate winner\?", title, re.I)) and re.search(r"-26", ticker):
            # Use the title for the state: some tickers don't match it (SENATELA-26 is Kentucky)
            p_rep = price("R")
            race, p_dem = f"senate:{STATE_CODES[m.group(1).title()]}", None if p_rep is None else 1 - p_rep
        elif e["series_ticker"] == "KXHOUSERACE" and (m := re.match(r"([A-Z]{2})-(\d{1,2}|AL) House winner", title)):
            p_rep = price("R")
            race, p_dem = district_key(m.group(1), m.group(2)), None if p_rep is None else 1 - p_rep
        elif (m := re.fullmatch(rf"({STATE_NAME_RE}) presidential (?:election )?winner\??(?: \(2028\))?", title, re.I)) \
                and "28" in ticker:
            p_rep = price("R")
            race, p_dem = f"pres:{STATE_CODES[m.group(1).title()]}", None if p_rep is None else 1 - p_rep
        if race and p_dem is not None:
            rows.append({"race": race, "source": "Kalshi", "probability_dem": p_dem, "volume": volume, "label": title,
                         "ref": dict(used)})
    return rows


# ---------------------------------------------------------------------------
# Presidential approval: the "Average" row of Wikipedia's aggregate table
# ---------------------------------------------------------------------------
def approval() -> dict:
    url = "https://en.wikipedia.org/w/index.php?" + urllib.parse.urlencode({"title": APPROVAL_ARTICLE, "action": "raw"})
    return approval_from_text(wikitables.fetch(url).decode("utf-8"))


def approval_from_text(text: str) -> dict:
    """The aggregate table's "Average" row, from any version of the article."""
    section = text.split("==== Approval ====", 1)[1].split("|}", 1)[0]
    row = section.split("|'''Average'''", 1)[1]
    cells = [c.split("|")[-1].strip() for c in row.split("\n|")[1:]]
    nums = [float(c.replace("%", "").replace("−", "-")) for c in cells[1:5]]
    approve, disapprove, _, net = nums
    date = datetime.strptime(cells[0], "%B %d, %Y").strftime("%Y-%m-%d")
    return {"date": date, "approve": approve, "disapprove": disapprove, "net": net,
            "source": "https://en.wikipedia.org/wiki/" + APPROVAL_ARTICLE.replace(" ", "_")}


# ---------------------------------------------------------------------------
# Unemployment: latest month and the same month a year earlier
# ---------------------------------------------------------------------------
def economy() -> dict:
    url = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=UNRATE"
    lines = wikitables.fetch(url).decode("utf-8").strip().splitlines()[1:]
    rate = {d: float(v) for d, v in (l.split(",") for l in lines) if v.strip()}
    latest = max(rate)
    year_ago = f"{int(latest[:4]) - 1}{latest[4:]}"
    return {"date": latest, "unemployment": rate[latest], "year_ago": rate.get(year_ago),
            "change": None if year_ago not in rate else round(rate[latest] - rate[year_ago], 2),
            "source": "https://fred.stlouisfed.org/series/UNRATE"}


# ---------------------------------------------------------------------------
def check_markets(rows: list[dict]) -> list[str]:
    problems = []
    by = lambda src, prefix: {r["race"] for r in rows if r["source"] == src and r["race"].startswith(prefix)}
    for src in ("Polymarket", "Kalshi"):
        for race in ("control:senate", "control:house"):
            if race not in by(src, race):
                problems.append(f"{src}: no {race} market")
        if len(by(src, "senate:")) < 25:
            problems.append(f"{src}: only {len(by(src, 'senate:'))} Senate races matched (expected ~35)")
    if len(by("Polymarket", "house:") | by("Kalshi", "house:")) < 100:
        problems.append("fewer than 100 House districts matched across both markets")
    bad = [r for r in rows if not 0 <= r["probability_dem"] <= 1]
    if bad:
        problems.append(f"probabilities outside 0-1: {bad[:3]}")
    return problems


def check_approval(appr: dict) -> list[str]:
    ok = 20 <= appr["approve"] <= 80 and abs(appr["approve"] - appr["disapprove"] - appr["net"]) < 0.6
    return [] if ok else [f"approval row looks wrong: {appr}"]


def check_economy(econ: dict) -> list[str]:
    ok = 2 <= econ["unemployment"] <= 15 and econ["change"] is not None
    return [] if ok else [f"unemployment row looks wrong: {econ}"]


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def market_rows() -> list[dict]:
    fetched = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    rows = polymarket_rows() + kalshi_rows()
    # One row per race per source: if a market is listed twice, keep the busier one
    best = {}
    for r in rows:
        k = (r["race"], r["source"])
        if k not in best or r["volume"] > best[k]["volume"]:
            best[k] = r
    rows = sorted(best.values(), key=lambda r: (r["race"], r["source"]))
    for r in rows:
        r["probability_dem"] = round(r["probability_dem"], 4)
        r["volume"] = round(r["volume"])
        r["fetched_at"] = fetched
    return rows


if __name__ == "__main__":
    # The three parts are independent: if one site is down or a check fails, that
    # part's file keeps its previous contents and the others are still updated.
    parts = [
        ("markets", market_rows, check_markets, "data/markets.csv", ["race", "source", "probability_dem", "volume", "label", "fetched_at"]),
        ("approval", approval, check_approval, "data/approval.csv", ["date", "approve", "disapprove", "net", "source"]),
        ("unemployment", economy, check_economy, "data/economy.csv", ["date", "unemployment", "year_ago", "change", "source"]),
    ]
    got, failed = {}, []
    for name, fetch, check, path, columns in parts:
        try:
            data = fetch()
            problems = check(data)
        except Exception as e:                       # network trouble or a page that changed format
            problems = [f"couldn't fetch: {type(e).__name__}: {e}"]
        if problems:
            failed.append(name)
            print(f"WARNING {name}: kept the previous {path}\n  " + "\n  ".join(problems))
            continue
        write_csv(path, data if name == "markets" else [data], columns)
        got[name] = data

    if "markets" in got:
        rows = got["markets"]
        count = lambda src, prefix: sum(r["source"] == src and r["race"].startswith(prefix) for r in rows)
        for src in ("Polymarket", "Kalshi"):
            ctrl = {r["race"].split(":")[1]: r["probability_dem"] for r in rows if r["source"] == src and r["race"].startswith("control:")}
            print(f"{src}: control {ctrl}; Senate races {count(src, 'senate:')}, House districts {count(src, 'house:')}, "
                  f"2028 states {count(src, 'pres:')}")
    if "unemployment" in got:
        e = got["unemployment"]
        print(f"Unemployment ({e['date']}): {e['unemployment']}%, {e['change']:+} over the year")
    if "approval" in got:
        a = got["approval"]
        print(f"Approval ({a['date']}): {a['approve']}% approve, {a['disapprove']}% disapprove, net {a['net']:+}")
    if len(failed) == len(parts):
        raise SystemExit("fetch_markets.py: nothing could be updated")
