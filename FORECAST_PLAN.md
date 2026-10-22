# Forecast plan: win probabilities for 2026 and 2028

Handoff document for building PolTracker's forecast model in a fresh Claude Code session.
Written 2026-10-21 at the end of the session that built the site. Read this whole file
before starting; the decisions in it were made with the owner and shouldn't be reopened.

## The request (owner's words)

> I want a prediction map for 2026 midterm and 2028 presidential elections that calculates the
> overall chance a party takes control and the chance of carrying each state. This model can be
> based on polls, prediction market data, and fundamentals, but skew towards polling the closer
> it gets to the election.

## Decisions already made

| Question | Decision |
|---|---|
| Where forecasts appear | A **"Polls / Forecast" toggle** on the existing 2028 (index.html), Senate and House pages. Same map and bar components, showing win chances instead of margins. No new top-level page. |
| Prediction markets | **Both Polymarket and Kalshi**, averaged where both list a race, whichever exists otherwise. Verify their public data first; if one has no usable coverage, use the other and say so. |
| Order | **2026 first** (Senate, House): it's only useful before Election Day, **November 3, 2026**. Ship 2026 forecasts live as early as possible, then do 2028. |
| Honesty | The page must say plainly that this is an unvalidated hobby model: standard method, judgment-based parameters, not calibrated the way professional forecasts are. |

## About the owner

- Mark is new to coding (this is his first website). Explain what you did in plain terms, avoid
  jargon or define it, and point him to the relevant files. He likes to understand how things work.
- He reviews outcomes, not diffs. Show screenshots or numbers that prove things work.
- He chose to publish: anything pushed to `main` under `site/` goes live on
  https://m3rk888.github.io/pollmap/ within about a minute (GitHub Pages workflow). Don't push
  broken or half-done pages. Work on a branch or keep the toggle hidden until a forecast is done.
- Don't send his email address anywhere (e.g. in HTTP User-Agent headers). Use
  `poltracker-build/1.0` style agents.
- His usage is limited. Be efficient: batch work, avoid re-reading large files, don't over-screenshot.

## How the site works today

Python builds static data files; hand-written pages draw them. Repo: https://github.com/M3RK888/pollmap

```
data/*.csv  ->  build_site.py  ->  site/data/*.js  ->  site/*.html (+ site/assets/common.js, style.css)
```

| File | Role |
|---|---|
| `polling_2028.py` | 2028 model: national poll average -> uniform swing from 2024 results -> blend with state polls (trend-adjusted). `RATING_CUTOFFS`, `RATING_COLORS`, `rate()`, `ELECTORAL_VOTES`, `STATE_NAMES` live here. |
| `midterms.py` | 2026 model: environment = mean of generic-ballot aggregator margins; baseline = environment + 2 x Cook PVI; Senate races blended with their polls (21-day half-life). House = baseline only. Also the House hex-map layout (`hex_layout`). |
| `fetch_midterms.py` + `wikitables.py` | Pull 2026 Senate races/polls, House districts (2026 Cook PVI, candidates) and generic-ballot aggregates from Wikipedia **raw wikitext** (`?action=raw`), with self-validation. |
| `build_site.py` | Writes `site/data/forecast.js` (2028 per matchup), `polls.js`, `history.js` (1960-2024 results), `senate.js`, `house.js`. Data is written as `window.X = {...}` JS so pages open from disk without a server. |
| `site/assets/common.js` | `PM.renderEvBar` (segmented bar with configurable threshold), `PM.renderMap` (Plotly choropleth; rows with `color` drawn flat), `PM.sortableTable`, tooltip, formatters. |
| `validate_polls.py` | Checks `data/polls_2028.csv`. |
| `agents/find_polls.md` | Instructions for the **daily cloud agent** (routine `trig_01UPvnM2ieRLKysU5eDYJtih`, 12:00 UTC) that adds 2028 polls, runs `fetch_midterms.py`, rebuilds and pushes. On the cloud machine use `python3.13`. |

Conventions: margins are **Democratic minus Republican, in points** (positive = D). Ratings cutoffs
are 15 / 5 / 2 points (Almost certain / Likely / Lean / Tossup). In the Senate, the main
non-Republican candidate may be an independent (NE, ID, SD, MT); wins count on the Democratic side.
Senate: 65 seats not up (34 D-caucus, 31 R); Democrats need 51, Republicans 50 (VP Vance breaks ties).
House: 218 for control. 2028: 270 electoral votes; Maine/Nebraska district splits are not modeled
(statewide winner takes all), which is acceptable but should be noted on the page.

Local dev: Windows, venv at `.venv` (`.venv/Scripts/python.exe`), Node is not installed.
Screenshots: headless Edge (`msedge --headless=new --screenshot`); it clamps width to ~496px, so
check phone layouts by loading pages in 390px iframes.

## The model

### 1. Three estimates per race (state, Senate race, House district)

All on the margin scale (D minus R, points):

- **Polls**: the existing estimate (2028: `polling_2028.state_estimates`; Senate:
  `midterms.senate()` estimate; House: environment + 2 x PVI, since districts are rarely polled).
- **Fundamentals**: a national environment from structural factors, plus each race's partisan lean.
  Keep it simple and transparent; document every coefficient on the page.
  - 2026: president's party historically loses ground in midterms; tie the national environment to
    presidential net approval (fit a one-variable regression on past midterms if you can source the
    data reliably, e.g. Wikipedia's lists of House popular vote by year and Gallup approval; otherwise
    use a documented rule of thumb). Add an incumbency bonus for House/Senate incumbents running
    (roughly 2-3 points of margin), since the PVI baseline ignores candidates.
  - 2028: open seat (Trump is term-limited). Start from a near-even national environment adjusted by
    presidential approval and the economy (e.g. real GDP growth / unemployment), Abramowitz
    "time for change" style. Two years out this should be weak and close to 50/50 nationally.
- **Markets**: Polymarket and Kalshi prices. Convert a win probability p to an implied margin with
  the same uncertainty the model uses: `margin = sigma_total * Phi^-1(p)`. Ignore thin markets
  (set a minimum volume/liquidity) and clamp p to [0.02, 0.98].

### 2. Blend by time to the election

Let `t` = days until Election Day (2026-11-03 for midterms, 2028-11-07 for presidential).
Starting point (tune if results look wrong, and document):

- Poll weight `w_p = 1 / (1 + t / 60)`: about 0.82 at 13 days out, 0.07 at two years out.
- The rest split between markets and fundamentals, e.g. markets 60%, fundamentals 40%.
- If a race has no polls, no market or no fundamentals, renormalize the remaining weights.
- Show each race's three ingredients and their weights on the page (the owner explicitly wants to
  see what drives the number).

### 3. Uncertainty

Errors on the final margin, as standard deviations that grow with time:

- National (shared by every race): `sigma_nat(t) = sqrt(3^2 + 0.06 * t)` -> ~3.1 at 13 days, ~7.3 two years out.
- Race-specific: `sigma_race(t) = sqrt(s0^2 + 0.05 * t)` with s0 = 4 (states, Senate) and 6 (House districts).
- Optionally a regional shared error (Census regions, SD ~2) so neighboring states move together.
- Use a Student-t with ~5 degrees of freedom instead of a normal for fat tails.

These are judgment calls informed by typical polling misses (2016/2020 national misses of 2-4
points; state misses larger). If time allows, sanity-check against history: the repo's git history
has 2016 state polls (`git show d5a3089:data/polls_2016_raw.csv`) and `data/results_1960_2024.csv`.

### 4. Simulation

Monte Carlo, about 20,000 draws, fixed random seed so builds are reproducible:

1. Draw one national error, (optional) regional errors, and per-race errors.
2. Simulated margin = blended estimate + national + regional + race error.
3. Tally winners: per-race win probability; seats/electoral votes per draw; control = share of draws
   reaching the threshold (Senate: D >= 51 or R >= 50 with seats not up added; House 218; 2028 270).
4. Output per race: win probability, median margin, 80% interval. Overall: control probability,
   seat/EV distribution (histogram), median seats.

Do it in Python at build time (numpy, already installed). Write only summaries to the site data,
not raw draws.

### 5. Display

On each page, a **Polls / Forecast** toggle near the title (remember the choice in the URL hash).
Forecast view:

- Headline: "Democrats win the Senate **63%**" style, for both parties.
- Map colored by win probability: diverging, 50% = neutral, 95%+ = deep color. Reuse
  `PM.renderMap` with a probability scale; the House hex map takes probability colors the same way.
- The bar: keep the seat bar, but segment by probability bands (e.g. >95%, 75-95%, 60-75%, toss-up
  40-60%) instead of margin ratings.
- A distribution chart of simulated seats/electoral votes, with the control line marked.
- Per-race table: polls estimate, fundamentals estimate, market probability, weights, blended margin,
  win probability. Sortable, closest first.
- A "How the forecast works" section in plain language, including the honesty caveat above and the
  current weights (e.g. "13 days out: polls 82%, markets 11%, fundamentals 7%").

Match the existing look (style.css tokens, light/dark). The `dataviz` skill has guidance for the
distribution chart and probability color scale.

### 6. Data pipeline

- New `fetch_markets.py`: pull current prices from both markets, map each market to a race key
  (state code / district id / "senate-control" / "house-control" / "president-2028"), write
  `data/markets.csv` (race, source, probability_dem, volume, fetched_at). Include market control
  odds in the page as a comparison point, even though the model computes its own.
- Public endpoints to verify first (no keys should be needed for reading):
  Polymarket `https://gamma-api.polymarket.com/events?...` / `/markets?...`;
  Kalshi `https://api.elections.kalshi.com/trade-api/v2/markets?...` and `/events?...`.
  Market slugs and tickers change; search them, don't hard-code guesses. Read numbers from the JSON,
  never from summaries.
- New `forecast_model.py` (or similar) for blending + simulation; called from `build_site.py`, which
  writes `site/data/forecast_2026.js` and `forecast_2028.js` (or adds a `forecast` key to existing files).
- Update `agents/find_polls.md` and the routine prompt so the daily agent also runs `fetch_markets.py`
  (with a validation step like `fetch_midterms.py` has), then rebuilds. Update the routine with the
  RemoteTrigger tool (load via ToolSearch); keep its existing rules.
- Add any new dependency (e.g. scipy, if used) to `requirements.txt`. numpy alone can do the
  normal/t quantiles if you prefer not to add scipy.

## Suggested order

1. Check market data coverage for 2026 Senate races, House control, and 2028 (both sources). Report
   to Mark briefly what's available.
2. Build the model for **2026 Senate + House**, with tests or sanity checks (probabilities move the
   right way when you nudge inputs; a race at 0 margin is ~50%; control odds roughly agree with
   markets).
3. Forecast view on senate.html and house.html; screenshot desktop + phone, light + dark.
4. Push (goes live), update the daily agent. Tell Mark it's live.
5. Then 2028: fundamentals, markets, forecast view on index.html.

## After Election Day 2026

The Senate/House pages should switch to results (like history.html), and the agent should stop
running `fetch_midterms.py`. Not part of this task unless Mark asks.
