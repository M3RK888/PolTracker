"""
poll_map.py — Step 1 of the polling-map project.

Reads a CSV of state-level poll margins and renders an interactive
US choropleth (hover, zoom, pan) to a standalone HTML file.

Run:
    pip install pandas plotly
    python poll_map.py

NOTE: sample_state_polls.csv contains ILLUSTRATIVE numbers, not real polls.
Swap in real data once you pick a source.
"""

import pandas as pd
import plotly.express as px

CSV_PATH = "sample_state_polls.csv"
OUT_PATH = "poll_map.html"


def load_polls(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    # margin > 0 = Democratic lead, < 0 = Republican lead (percentage points)
    df["leader"] = df["margin"].apply(lambda m: "D" if m > 0 else ("R" if m < 0 else "Tie"))
    df["label"] = df.apply(
        lambda r: f"{r['leader']}+{abs(r['margin']):.1f}" if r["leader"] != "Tie" else "Tied",
        axis=1,
    )
    return df


def make_map(df: pd.DataFrame):
    fig = px.choropleth(
        df,
        locations="state",            # two-letter postal codes
        locationmode="USA-states",    # built into Plotly, no shapefile needed
        scope="usa",
        color="margin",
        color_continuous_scale="RdBu",  # red (R) -> blue (D)
        range_color=(-25, 25),
        hover_name="state_name",
        hover_data={"label": True, "margin": False, "state": False, "n_polls": True},
        labels={"label": "Polling avg", "n_polls": "Polls"},
        title="2028 Presidential Polling Average by State (ILLUSTRATIVE DATA)",
    )
    fig.update_layout(
        coloraxis_colorbar=dict(
            title="Margin",
            tickvals=[-20, -10, 0, 10, 20],
            ticktext=["R+20", "R+10", "Even", "D+10", "D+20"],
        ),
        margin=dict(l=0, r=0, t=50, b=0),
    )
    return fig


if __name__ == "__main__":
    polls = load_polls(CSV_PATH)
    fig = make_map(polls)
    fig.write_html(OUT_PATH, include_plotlyjs="cdn")
    print(f"Wrote {OUT_PATH} ({len(polls)} states)")
