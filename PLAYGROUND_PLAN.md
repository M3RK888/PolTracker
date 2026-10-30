# Playground plan: "what if" simulations in the browser

Handoff for building PolTracker's playground in a fresh Claude Code session. Written
2026-10-30. Read this and `FORECAST_PLAN.md` (how the forecast works) before starting.

## The request (Mark's words)

> Can you also start work on a playground page where it runs 20,000 simulations and creates a map
> on the press of a button? The user can preselect states that they think a party will win and see
> how it effects the odds. Do this for 2028 presidential and 2026 house and senate maps.

**Deadline:** the 2026 part is only interesting before Election Day, **Tuesday, November 3, 2026**.
Ship Senate first (smallest), then House, then 2028. Each can go live on its own.

## Key decision: condition, don't override

When a user locks "Democrats win Texas", keep only the simulations in which Democrats win Texas
(rejection sampling), and compute everything from those. Errors are correlated (national + regional
+ race), so locking a state shifts the others: that correlation is the point of the feature. Don't
just force Texas's result and leave the rest unchanged.

Unlikely scenarios leave few matching simulations. Run in batches of 20,000 until at least ~2,000
match or ~400,000 have been tried, then report "based on N matching simulations of M". If fewer than
~200 match, say the scenario is very unlikely (show its probability: matches / tried) and don't
present odds as precise. Never fake it.

## The model to reproduce in JavaScript

Exactly what `forecast_model.py` does (`draw_errors`, `forecast_races`, `seat_summary`). Everything
needed is already in the built data:

| | Source |
|---|---|
| Each race's central estimate | `blended` (Democratic margin, points) on every race/state in `site/data/forecast_2026.js` (`senate.races`, `house.races`) and `forecast_2028.js` (`matchups[].states`) |
| Error sizes | `senate.sigma`, `house.sigma`, `matchups[].sigma`: `national`, `region`, `race` (standard deviations, points) |
| Error shape | Student-t, `settings.t_df` (5), rescaled so its SD is the sigma: `t * sd / sqrt(df / (df - 2))` |
| Regions | `REGIONS` / `REGION_OF` in `forecast_model.py` (Census regions). **Not exported yet**: add them to the built data (e.g. `settings.regions`) rather than duplicating the list in JS. Senate race keys like `FL-special` map to their state; House districts to their state. |
| Simulated margin | `blended + national (one per simulation) + regional (one per region per simulation) + race (one per race per simulation)`; Democrats win where margin > 0 |
| Control | Senate: Democratic seats = seats not up (34) + races won; D control at >= 51, R at >= 50 (VP breaks ties) — see `SENATE_THRESHOLD`, `SENATE_NOT_UP` in midterms.py. House: 218. 2028: electoral votes (`ev` per state) >= 270. |

Student-t sampling in JS: `t = z / sqrt(chi2 / df)` with `z` standard normal and `chi2` the sum of
`df` squared standard normals (Box-Muller for normals). Use a seedable PRNG (e.g. mulberry32) so a
"Run" with the same picks gives the same result; a "Run again" can reseed.

**Verify before shipping:** with nothing locked, the playground's odds must match the published
forecast within about 1 point (control odds and each race's win chance). Node isn't installed
locally, so check in headless Edge (`--dump-dom`) or with a small `?selftest` page mode that prints
the comparison. Also check a locked run moves things in the expected direction (lock Texas D ->
Democratic control odds go up; neighbors like Oklahoma shift D).

Performance: Senate is 35 races x 20,000; 2028 is 51 x 20,000; House is 435 x 20,000 = 8.7M draws
per batch. Use typed arrays, don't store all margins (tally as you go), and run the House in chunks
(or a Web Worker) so the page stays responsive; show progress.

## Page

New `site/playground.html` (nav link "Playground" on every page), reusing `common.js`
(`renderMap`, `renderEvBar`, the House hex map code from house.html, tooltip, table helpers) and
style.css. Tabs or a selector: **Senate 2026 / House 2026 / President 2028** (+ matchup picker for
2028, default the main matchup). Keep the choice and the picks in the URL hash so a scenario can
be shared as a link.

- **Pick:** click a state (or hex district) to cycle: no pick -> Democrat -> Republican -> no pick.
  Picked ones get a clear marker (outline/hatching/label), not just a color. Also a list of picks
  with remove buttons, and "Clear picks". On phones, tapping must work the same way.
- **Run 20,000 simulations** button. Disable while running; show progress for the House.
- **Results:** control odds for both parties (and the change vs. the published forecast, e.g.
  "Democrats 72% -> 81%"), the seat / electoral-vote distribution chart with the control line, the
  map colored by each unpicked race's win chance in this scenario, and a sortable table of races
  that moved most. The bar from the main pages (probability bands) also works here.
- **How it works:** a short plain-language explanation of conditioning ("we keep only the
  simulations where your picks come true, so states that tend to move together shift too") and the
  matching-simulations count. Reuse the forecast's honesty caveat (unvalidated hobby model).

Match the existing look in light and dark mode; check phone width (390px iframes; headless Edge
won't go below ~496px).

## Owner and process notes

- Mark is new to coding: explain in plain terms, show screenshots/numbers as proof.
- Pushing to `main` publishes to https://m3rk888.github.io/PolTracker/ after tests,
  `validate_polls.py` and `check_site.py` pass (pages.yml). Add the new page's data checks to
  `check_site.py` if it needs new exported fields. Don't push a half-working page live.
- The daily refresh (refresh.yml, 11:00 UTC) and the Claude routine (12:00 UTC) commit to `main`
  too: `git pull` before committing, and if a pull conflicts in data files, resolve it properly
  (don't commit merge markers; check_site.py now rejects them).
- Don't send Mark's email address anywhere.
- After Nov 3 the 2026 tabs should switch to "the race is over" (results) or be hidden; mention it,
  don't build it unless asked.
