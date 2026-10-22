"""
validate_polls.py — sanity checks for data/polls_2028.csv.

Run before building the site, especially after adding polls automatically:
    python validate_polls.py

Prints every problem found and exits with status 1 if there are any, so a
script (or the scheduled poll-finding agent) can refuse to commit bad rows.
"""

import sys

import pandas as pd

from polling_2028 import STATE_NAMES, today as eastern_today

PATH = "data/polls_2028.csv"
COLUMNS = ["state", "pollster", "start_date", "end_date", "sample_size", "voter_type",
           "dem_candidate", "rep_candidate", "dem", "rep", "source"]
VOTER_TYPES = {"LV", "RV", "A"}


def problems(df: pd.DataFrame) -> list[str]:
    if list(df.columns) != COLUMNS:
        return [f"columns are {list(df.columns)}, expected {COLUMNS}"]

    out = []
    start = pd.to_datetime(df["start_date"], format="%Y-%m-%d", errors="coerce")
    end = pd.to_datetime(df["end_date"], format="%Y-%m-%d", errors="coerce")
    today = eastern_today()

    for i, r in df.iterrows():
        row = f"row {i + 2} ({r['pollster']}, {r['end_date']})"   # +2: header line, 1-based
        if r["state"] != "US" and r["state"] not in STATE_NAMES:
            out.append(f"{row}: state '{r['state']}' is not a postal code or US")
        if pd.isna(end[i]):
            out.append(f"{row}: end_date must be YYYY-MM-DD")
        elif end[i] > today:
            out.append(f"{row}: end_date is in the future")
        if pd.notna(r["start_date"]) and pd.isna(start[i]):
            out.append(f"{row}: start_date must be YYYY-MM-DD or blank")
        elif pd.notna(start[i]) and pd.notna(end[i]) and start[i] > end[i]:
            out.append(f"{row}: start_date is after end_date")
        if pd.notna(r["sample_size"]) and not (50 <= float(r["sample_size"]) <= 100_000):
            out.append(f"{row}: sample_size {r['sample_size']} looks wrong")
        if pd.notna(r["voter_type"]) and r["voter_type"] not in VOTER_TYPES:
            out.append(f"{row}: voter_type must be LV, RV, A, or blank")
        for col in ("dem_candidate", "rep_candidate", "pollster"):
            if pd.isna(r[col]) or not str(r[col]).strip():
                out.append(f"{row}: {col} is blank")
        for col in ("dem", "rep"):
            if pd.isna(r[col]) or not (0 < float(r[col]) < 100):
                out.append(f"{row}: {col} must be a percentage between 0 and 100")
        if pd.notna(r["dem"]) and pd.notna(r["rep"]) and float(r["dem"]) + float(r["rep"]) > 100:
            out.append(f"{row}: dem + rep is over 100")
        if pd.isna(r["source"]) or not str(r["source"]).startswith("http"):
            out.append(f"{row}: source must be a URL")

    key = ["state", "pollster", "end_date", "dem_candidate", "rep_candidate"]
    for _, d in df[df.duplicated(key, keep=False)].groupby(key):
        lines = ", ".join(str(i + 2) for i in d.index)
        out.append(f"rows {lines}: same pollster, date, state and matchup (duplicate poll?)")
    return out


if __name__ == "__main__":
    found = problems(pd.read_csv(PATH))
    if found:
        print(f"{PATH}: {len(found)} problem(s)")
        print("\n".join(f"  - {p}" for p in found))
        sys.exit(1)
    print(f"{PATH}: OK ({len(pd.read_csv(PATH))} polls)")
