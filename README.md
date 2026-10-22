# PolTracker

Poll maps and forecasts for the 2026 U.S. Senate and House elections and the 2028 presidential race.

**Live site:** https://m3rk888.github.io/PolTracker/

Each page has a **Polls** view (where the polls say each race stands) and a **Forecast** view (each
side's chance of winning, blending polls, prediction markets and historical fundamentals). After
Election Day 2026 the Senate and House pages add a **Results** view. The forecast is an unvalidated
hobby model; the [Accuracy page](https://m3rk888.github.io/PolTracker/accuracy.html) shows how it has
done on past elections.

## How it works

```
Wikipedia, Polymarket, Kalshi, FRED     fetch_*.py      data/*.csv
data/*.csv                              build_site.py   site/data/*.js   (uses midterms.py, polling_2028.py, forecast_model.py)
site/data/*.js + site/*.html            GitHub Pages    the live site
```

The pages in `site/` are plain HTML and JavaScript, written by hand. Python scripts produce the data
they draw (`site/data/*.js`). Nothing needs a server: you can open `site/index.html` straight from disk.

### Automatic updates

| When (UTC) | What | Where |
|---|---|---|
| 11:00 daily | Refresh Senate/House polls, market prices, approval, unemployment; after Nov 3, 2026, election results and the forecast's score. Rebuild, test, commit, publish. | GitHub job: `.github/workflows/refresh.yml` |
| 12:00 daily | Look for new 2028 presidential polls, add them, rebuild, commit. | Claude routine, following `agents/find_polls.md` |
| every push | Run the tests and site checks; publish only if they pass. | GitHub job: `.github/workflows/pages.yml` |

## Files

**Fetching data**

| File | Does |
|---|---|
| `fetch_midterms.py` | 2026 Senate races and polls, House districts, generic ballot (Wikipedia). Works for past years too. |
| `fetch_markets.py` | Polymarket and Kalshi prices, presidential approval (Wikipedia), unemployment (FRED). |
| `fetch_history.py` | Presidential results by state, 1960–2024 (run once). |
| `wikitables.py` | Reads Wikipedia tables from page source. |

**Models**

| File | Does |
|---|---|
| `midterms.py` | 2026 poll estimates: generic ballot + partisan lean, blended with each race's polls. |
| `polling_2028.py` | 2028 poll estimates: national polls applied to 2024 results, blended with state polls. |
| `forecast_model.py` | Win probabilities: blends polls, markets and fundamentals, then simulates 20,000 elections. |

**Building and checking**

| File | Does |
|---|---|
| `build_site.py` | Writes `site/data/*.js` and adds today's points to the tracker history. |
| `validate_polls.py` | Checks the hand-collected `data/polls_2028.csv`. |
| `check_site.py` | Checks the built site data and links before publishing. |
| `tests/` | Tests for the readers (on saved excerpts of real pages) and the model. |

**Accuracy**

| File | Does |
|---|---|
| `backtest.py` | Reruns the Senate model on 2018–2024 and grades it. |
| `score_forecast.py` | Grades the 2026 Election Day forecast against the results (after Nov 3). |
| `scoring.py` | The grading measures (Brier score, calibration, …), explained in plain language. |
| `backfill_history.py` | Rebuilt the tracker's Aug 1 – Oct 21, 2026 history (one-off). |

**Data you might edit by hand:** only `data/polls_2028.csv`. Every other CSV is written by a script.

## Running it yourself

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt     # on Mac/Linux: .venv/bin/python
.venv/Scripts/python fetch_midterms.py
.venv/Scripts/python fetch_markets.py
.venv/Scripts/python build_site.py
.venv/Scripts/python -m pytest
```

Then open `site/index.html` in a browser.

## After the 2026 election

The daily job handles it: from November 4 it stops fetching polls, reads results as races are called,
grades the forecast (`score_forecast.py`), and the Senate and House pages switch to the Results view.
The 2026 forecast itself stays frozen at its Election Day version.
