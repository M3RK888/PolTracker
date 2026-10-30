"""
check_site.py — make sure the built site is sound before it's published.

The publish workflow (.github/workflows/pages.yml) runs this, the tests and
validate_polls.py on every push, and only publishes if all of them pass. If
something's wrong, the live site keeps its last good version.

Checks:
  - every site/data/*.js file is readable, with the parts the pages need
  - probabilities are between 0 and 1; seat and race counts are right
  - every local file the pages link to (scripts, styles, images) exists

    python check_site.py
"""

import json
import re
import sys
from pathlib import Path

SITE = Path("site")
problems = []


def load(name: str) -> dict:
    text = (SITE / "data" / f"{name}.js").read_text(encoding="utf-8")
    m = re.fullmatch(r"window\.\w+ = (.*);\s*", text, re.S)
    if not m:
        raise ValueError(f"{name}.js isn't in the form 'window.X = {{...}};'")
    return json.loads(m.group(1))


def check(ok: bool, message: str) -> None:
    if not ok:
        problems.append(message)


def is_prob(x) -> bool:
    return isinstance(x, (int, float)) and 0 <= x <= 1


data = {}
for name in ("forecast", "polls", "history", "senate", "house", "forecast_2026", "forecast_2028", "accuracy"):
    try:
        data[name] = load(name)
    except Exception as e:                               # missing file, broken JSON, ...
        problems.append(f"site/data/{name}.js: {e}")

if "senate" in data:
    s = data["senate"]
    check(len(s["races"]) == 35, f"senate.js: {len(s['races'])} races, expected 35")
    check(all("estimate" in r for r in s["races"]), "senate.js: a race has no estimate")
if "house" in data:
    h = data["house"]
    check(len(h["districts"]) == 435, f"house.js: {len(h['districts'])} districts, expected 435")
if "forecast_2026" in data:
    f = data["forecast_2026"]
    for chamber, n in (("senate", 35), ("house", 435)):
        c = f[chamber]
        check(is_prob(c["summary"]["p_dem_control"]), f"forecast_2026.js: {chamber} control odds out of range")
        check(len(c["races"]) == n, f"forecast_2026.js: {chamber} has {len(c['races'])} races, expected {n}")
        check(all(is_prob(r["p_dem"]) for r in c["races"]), f"forecast_2026.js: a {chamber} race's chance is out of range")
        check(len(f["history"][chamber]) > 0, f"forecast_2026.js: no {chamber} history for the tracker")
        dates = [p["date"] for p in f["history"][chamber]]
        # one point per day (two copies of a day means a botched merge of the history file)
        check(len(dates) == len(set(dates)), f"forecast_2026.js: {chamber} history has repeated days")
if "forecast_2028" in data:
    f = data["forecast_2028"]
    check(len(f["matchups"]) > 0, "forecast_2028.js: no matchups")
    for mu in f["matchups"]:
        check(is_prob(mu["summary"]["p_dem"]), f"forecast_2028.js: {mu['id']} chance out of range")
        check(len(mu["states"]) == 51, f"forecast_2028.js: {mu['id']} has {len(mu['states'])} states, expected 51")
if "forecast" in data:
    check(any(m["available"] for m in data["forecast"]["matchups"]), "forecast.js: no matchup has a map")

# No data file may contain unresolved git merge-conflict markers
# (on Oct 30, 2026 a conflicted data/forecast_history.csv was committed and published)
for path in list(Path("data").glob("*.csv")) + list((SITE / "data").glob("*.js")):
    text = path.read_text(encoding="utf-8")
    if re.search(r"^(<<<<<<<|>>>>>>>) ", text, re.M):
        problems.append(f"{path}: unresolved merge conflict")

# Every local file a page links to must exist
for page in SITE.glob("*.html"):
    html = page.read_text(encoding="utf-8")
    for ref in re.findall(r'(?:src|href)="([^"#:]+?)(?:\?[^"]*)?"', html):
        if "${" not in ref and not (SITE / ref).exists():         # ${...}: built by the page's script
            problems.append(f"{page.name}: links to missing file {ref}")

if problems:
    print("check_site.py: problems found\n  " + "\n  ".join(problems))
    sys.exit(1)
print(f"check_site.py: OK ({len(data)} data files, {len(list(SITE.glob('*.html')))} pages)")
