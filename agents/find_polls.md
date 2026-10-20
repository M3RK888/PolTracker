# Finding new 2028 polls

Instructions for the scheduled agent that keeps `data/polls_2028.csv` up to date.
Edit this file to change how the agent behaves; the routine reads it every run.

## Goal

Find 2028 **general-election presidential** polls that are not yet in
`data/polls_2028.csv`, add them, rebuild the site data, and commit.

## Where to look

Search the web, then confirm every number on a page you actually opened. Good starting points:

- Wikipedia: the "Opinion polling > General election" section of "2028 United States presidential
  election", which has one table per matchup (national, then statewide). Read its exact wikitext with
  `curl -sL -A "pollmap-agent/1.0" "https://en.wikipedia.org/w/index.php?title=2028_United_States_presidential_election&action=raw"`.
  If a separate "Nationwide/Statewide opinion polling for the 2028 ..." article exists by the time you
  run, use that instead (same `action=raw` trick).
- 270toWin 2028 presidential polls
- RealClearPolling 2028 general election matchups
- Pollster releases: Emerson College, YouGov (Yahoo, Economist), Morning Consult, Echelon Insights,
  Quinnipiac, Marist, Siena / New York Times, Fox News, CNN, Harris / HarrisX, AtlasIntel,
  Cygnal, co/efficient, and others

Prefer the pollster's own release as the `source` URL. An aggregator URL is fine when the pollster
page isn't available, as long as the numbers match.

## Reading numbers exactly

WebFetch answers through a summarizing model, which can misread table columns. Use it to *find*
polls, but take the percentages from exact page text: `curl -sL <url>` piped to `grep`/`sed`
(or Python), or Wikipedia wikitext via `action=raw` as above. In Wikipedia's matchup tables the
column order follows the header (check which party's column comes first), and the leader's number is
wrapped in `'''bold'''` with a `{{Party shading/...}}` cell.

One exact source is enough. You don't need a second site to agree, but if two sources disagree,
skip the poll and mention it in your report. Many pollster releases only show toplines in chart
images; in that case the Wikipedia row (which cites the release) is the source to read.

## What counts

Include:
- Head-to-head matchups between one Democrat and one Republican, nationally or in one state.
- Every matchup a poll tested: one row per matchup. A poll testing Newsom–Vance and
  Buttigieg–Vance gives two rows.

Skip:
- Primary polls, favorability, "generic Democrat vs generic Republican", and approval ratings.
- Matchups with three or more named candidates, unless the poll also reports the two-way
  version (then use the two-way numbers).
- Anything where you can't find the actual percentages.

When a poll reports more than one population, use **likely voters (LV)** if available, otherwise
**registered voters (RV)**, otherwise all adults (A). Use only one per matchup.

## Row format

Append rows to `data/polls_2028.csv`. Columns, in order:

| column | format |
|---|---|
| state | `US` for national, otherwise the two-letter postal code (`PA`, `DC`) |
| pollster | Name as used in existing rows when the pollster already appears (e.g. `Emerson College`, `Yahoo/YouGov`). For a new partnership use `Sponsor/Pollster`. |
| start_date | `YYYY-MM-DD`, blank if not reported |
| end_date | `YYYY-MM-DD` (last day of fieldwork; required) |
| sample_size | whole number, blank if not reported |
| voter_type | `LV`, `RV`, or `A` |
| dem_candidate | surname only, spelled like existing rows (`Newsom`, `Buttigieg`, `Ocasio-Cortez`) |
| rep_candidate | surname only (`Vance`) |
| dem, rep | the percentages as numbers, e.g. `47` or `46.5` — not margins |
| source | URL of the page you verified the numbers on |

Put quotes around any field containing a comma. Don't reorder, edit or delete existing rows,
except to fix one you added yourself in this same run.

## Avoid duplicates

Before adding a poll, check the CSV for the same pollster, state, matchup and end date
(within a day or two, since aggregators sometimes list slightly different dates). If it's there, skip it.
The same poll is often reported by several sites, so search results will repeat.

## After adding

Use the same interpreter for installing and running. On the cloud machine `python3` is 3.11, which
is too old for the pinned packages, so use `python3.13` (if that doesn't exist, use the newest
`python3.x` available):

1. `python3.13 -m pip install -q -r requirements.txt`
2. `python3.13 validate_polls.py` and fix any problem it reports in the rows you added
3. `python3.13 build_site.py`
4. Commit `data/polls_2028.csv` and `site/data/` with a message like:

   ```
   Add 3 polls: Emerson (US, Oct 15), Quinnipiac (PA, Oct 12), ...
   ```

   Then push to `main`.

If you found nothing new, don't commit. Just report that no new polls were found.

## Final report

End with a short summary listing each poll added (pollster, where, matchup, dem–rep, source URL),
plus any polls you found but skipped and why.
