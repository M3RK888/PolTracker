"""
wikitables.py — read tables and poll rows out of Wikipedia wikitext.

Wikipedia's raw page source ("?action=raw") is exact text, unlike a rendered
or summarized page, so numbers taken from it can be trusted to match what the
page shows. These helpers turn its table markup into plain Python lists.

Used by fetch_midterms.py.
"""

import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CACHE = Path("data/.cache/wiki")
MONTHS = {m: i for i, m in enumerate(
    "January February March April May June July August September October November December".split(), 1)}
MONTH_RE = "|".join(MONTHS) + "|" + "|".join(m[:3] for m in MONTHS) + r"|Sept"


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------
def raw_page(title: str, refresh: bool = True) -> str:
    """Raw wikitext of an article, cached under data/.cache/wiki."""
    path = CACHE / (title.replace(" ", "_").replace("/", "_") + ".wiki")
    if refresh or not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        url = ("https://en.wikipedia.org/w/index.php?" +
               urllib.parse.urlencode({"title": title, "action": "raw", "redirect": "yes"}))
        path.write_bytes(fetch(url))
    return path.read_text(encoding="utf-8")


# Wikimedia asks automated tools to say who they are, with a way to get in touch;
# requests from anonymous-looking tools are the first to be throttled.
USER_AGENT = "pollmap-build/1.0 (https://github.com/M3RK888/pollmap)"


def fetch(url: str) -> bytes:
    """GET a URL, waiting and retrying (for up to about 4 minutes) when the site says
    it's busy (HTTP 429) or has a temporary error (5xx)."""
    for attempt in range(6):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if (e.code != 429 and e.code < 500) or attempt == 5:
                raise
            wait = e.headers.get("Retry-After", "")
            time.sleep(int(wait) if wait.isdigit() else 15 * (attempt + 1))
        except OSError:
            if attempt == 5:
                raise
            time.sleep(5 * (attempt + 1))


# ---------------------------------------------------------------------------
# Cleaning cell text
# ---------------------------------------------------------------------------
def strip_refs(s: str) -> str:
    s = re.sub(r"<ref[^>/]*/>", "", s)
    return re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)


def first_url(s: str) -> str | None:
    m = re.search(r"https?://[^\s|\]}<]+", s)
    return m.group(0) if m else None


def split_top(s: str, sep: str) -> list[str]:
    """Split on `sep` only where it's outside [[...]] and {{...}}."""
    out, depth, buf, i = [], 0, "", 0
    while i < len(s):
        two = s[i:i + 2]
        if two in ("[[", "{{"):
            depth += 1; buf += two; i += 2; continue
        if two in ("]]", "}}"):
            depth = max(0, depth - 1); buf += two; i += 2; continue
        if depth == 0 and s.startswith(sep, i):
            out.append(buf); buf = ""; i += len(sep); continue
        buf += s[i]; i += 1
    out.append(buf)
    return out


def cell_parts(cell: str) -> tuple[str, str]:
    """'rowspan=2 style=".." | text' -> (attributes, text). Templates like
    {{party shading/Democratic}} before the pipe count as attributes."""
    parts = split_top(cell, "|")
    if len(parts) == 1:
        return "", parts[0].strip()
    return "|".join(parts[:-1]).strip(), parts[-1].strip()


def plain(s: str) -> str:
    """Wikitext -> readable text: links to their labels, templates and markup removed."""
    s = strip_refs(s)
    s = re.sub(r"\[\[(?:[^\]|]*\|)?([^\]]*)\]\]", r"\1", s)          # [[a|b]] -> b
    s = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", s)              # [url text] -> text
    s = re.sub(r"\{\{(?:sortname|Sortname)\|([^|}]*)\|([^|}]*)[^}]*\}\}", r"\1 \2", s)
    # Remaining templates ({{small|...}}, {{efn|...}}, ...) are dropped, innermost
    # first, so nested ones like {{small|from {{ushr|CA|7}}}} disappear completely
    while True:
        t = re.sub(r"\{\{[^{}]*\}\}", "", s)
        if t == s:
            break
        s = t
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("'''", "").replace("''", "").replace("&nbsp;", " ").replace("&ndash;", "–")
    return re.sub(r"\s+", " ", s).strip()


def span(attrs: str, kind: str) -> int:
    m = re.search(kind + r'\s*=\s*"?(\d+)', attrs)
    return int(m.group(1)) if m else 1


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------
def tables(lines: list[str]) -> list[dict]:
    """Every top-level {| ... |} table in `lines`, as
    {"start": line index, "header": [header cells], "rows": [[raw cells]]}.
    Header and data cells keep their wikitext; rowspans are filled in so each
    row has every column."""
    # Footnotes often span several lines, and their continuation lines can
    # start with "|" (e.g. "|access-date=..."), which would read as new cells.
    text = re.sub(r"<ref[^>/]*>.*?</ref>", lambda m: m.group(0).replace("\n", " "), "\n".join(lines), flags=re.S)
    lines = text.split("\n")
    found, i = [], 0
    while i < len(lines):
        if not lines[i].lstrip().startswith("{|"):
            i += 1
            continue
        start, depth, j = i, 0, i
        body = []
        while j < len(lines):
            t = lines[j].lstrip()
            if t.startswith("{|"):
                depth += 1
            if depth == 1 and j > start:
                body.append(lines[j])
            if t.startswith("|}"):
                depth -= 1
                if depth == 0:
                    break
            j += 1
        found.append(_parse_table(body, start))
        i = j + 1
    return found


def _parse_table(body: list[str], start: int) -> dict:
    header, rows, cur, pending = [], [], None, {}   # pending: col -> [cell, rows left]

    def finish():
        if cur is None:
            return
        row, col, k = [], 0, 0
        while k < len(cur) or col in pending:
            if col in pending:
                cell, left = pending[col]
                row.append(cell)
                pending[col][1] -= 1
                if pending[col][1] == 0:
                    del pending[col]
                col += 1
                continue
            raw = cur[k]; k += 1
            attrs, _ = cell_parts(raw)
            rs, cs = span(attrs, "rowspan"), span(attrs, "colspan")
            for _ in range(cs):
                if rs > 1:
                    pending[col] = [raw, rs - 1]
                row.append(raw)
                col += 1
        rows.append(row)

    text_cells = []
    for line in body:
        t = line.strip()
        if t.startswith("|-"):
            if cur is not None:
                finish()
            cur = []
            continue
        if t.startswith("|}") or t.startswith("|+"):
            continue
        if t.startswith("!"):
            cells = split_top(t[1:], "!!")
            if cur is None or not rows and not cur:
                header.extend(cells)
            else:
                cur = cur if cur is not None else []
                cur.extend(cells)          # row headers (e.g. "! State") count as cells
            continue
        if t.startswith("|"):
            if cur is None:
                cur = []
            cur.extend(split_top(t[1:], "||"))
        elif cur and t:
            cur[-1] += "\n" + line         # continuation of a multi-line cell
    if cur:
        finish()
    return {"start": start, "header": header, "rows": [r for r in rows if r]}


# ---------------------------------------------------------------------------
# Poll rows
# ---------------------------------------------------------------------------
def parse_dates(s: str) -> tuple[str | None, str | None]:
    """'September 30 – October 2, 2026' -> ('2026-09-30', '2026-10-02')."""
    s = plain(s).replace("–", "-").replace("—", "-")
    years = re.findall(r"\b(20\d\d)\b", s)
    if not years:
        return None, None
    parts = [p.strip() for p in re.split(r"\s+-\s+|-(?=\s*(?:" + MONTH_RE + r")\b)|(?<=\d)-(?=\d)", s)]
    dates, month, year = [], None, int(years[-1])
    for p in parts:
        m = re.search(r"(" + MONTH_RE + r")\.?\s*(\d{1,2})?(?:,?\s*(\d{4}))?", p)
        d = re.search(r"^(\d{1,2})(?:,?\s*(\d{4}))?$", p.strip())
        if m:
            month = MONTHS[next(k for k in MONTHS if k.startswith(m.group(1)[:3]))]
            day = int(m.group(2)) if m.group(2) else None
            y = int(m.group(3)) if m.group(3) else None
        elif d and month:
            day, y = int(d.group(1)), (int(d.group(2)) if d.group(2) else None)
        else:
            continue
        dates.append([y, month, day])
    if not dates or any(x[2] is None for x in dates):
        return None, None
    for x in reversed(dates):                     # fill missing years from the right
        x[0] = x[0] or year
        year = x[0]
    if len(dates) == 2 and dates[0][1] > dates[1][1]:
        dates[0][0] = dates[1][0] - 1             # Dec 28 - Jan 3 spans a new year
    fmt = lambda x: f"{x[0]:04d}-{x[1]:02d}-{x[2]:02d}"
    return fmt(dates[0]), fmt(dates[-1])


def parse_sample(s: str) -> tuple[int | None, str | None]:
    """'1,200 (LV)' -> (1200, 'LV')."""
    t = plain(s)
    n = re.search(r"(\d[\d,]*)", t)
    v = re.search(r"\b(LV|RV|A|V)\b", t)
    return (int(n.group(1).replace(",", "")) if n else None), (v.group(1) if v else None)


def pct(s: str) -> float | None:
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", plain(s))
    return float(m.group(1)) if m else None


def candidate_columns(header: list[str]) -> list[tuple[int, str, str]]:
    """Header cells like 'Roy<br />Cooper (D)' -> [(column, 'Roy Cooper', 'D'), ...]."""
    out = []
    for i, h in enumerate(header):
        text = plain(cell_parts(h)[1])
        m = re.match(r"(.+?)\s*\((R|D|I|L|G|IP|DFL|NPA|C|F|U)\)$", text)
        if m:
            party = {"DFL": "D", "NPA": "I", "IP": "I", "U": "I", "C": "O", "L": "O", "G": "O", "F": "O"}.get(m.group(2), m.group(2))
            out.append((i, m.group(1).strip(), party))
    return out


def poll_rows(table: dict) -> list[dict]:
    """Rows of an individual-poll table: pollster, dates, sample, and each candidate's %."""
    header = [plain(cell_parts(h)[1]).lower() for h in table["header"]]
    if not header or "aggregation" in header[0]:
        return []
    cands = candidate_columns(table["header"])
    try:
        i_src = next(i for i, h in enumerate(header) if "source" in h or "poll" in h)
        i_date = next(i for i, h in enumerate(header) if "date" in h)
        i_n = next(i for i, h in enumerate(header) if "sample" in h)
    except StopIteration:
        return []
    out = []
    for row in table["rows"]:
        if len(row) != len(table["header"]):
            continue                              # notes, colspan separators, malformed rows
        start, end = parse_dates(cell_parts(row[i_date])[1])
        values = {name: pct(cell_parts(row[i])[1]) for i, name, _ in cands}
        if not end or sum(v is not None for v in values.values()) < 2:
            continue
        n, vt = parse_sample(cell_parts(row[i_n])[1])
        src_cell = row[i_src]
        out.append({
            "pollster": plain(cell_parts(src_cell)[1]) or plain(src_cell),
            "start": start, "end": end, "sample_size": n, "voter_type": vt,
            "source": first_url(src_cell), "values": values,
            "parties": {name: p for _, name, p in cands},
        })
    return out


def section(lines: list[str], title_re: str, level: int | None = None, start: int = 0) -> tuple[int, int]:
    """(first, last) line indexes of the section whose heading matches title_re."""
    for i in range(start, len(lines)):
        m = re.match(r"^(=+)\s*(.*?)\s*=+\s*$", lines[i])
        if m and re.search(title_re, m.group(2), re.I) and (level is None or len(m.group(1)) == level):
            lvl = len(m.group(1))
            for j in range(i + 1, len(lines)):
                n = re.match(r"^(=+)[^=]", lines[j])
                if n and len(n.group(1)) <= lvl:
                    return i, j
            return i, len(lines)
    return -1, -1
