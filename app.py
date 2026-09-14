"""Financial Statement Intelligence, as an app.

Type a company name, get its financial health. Works for any listed company with published
statements, across the US, Europe, India and elsewhere.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from fsi import peers
from fsi.company import analyse
from fsi.financial_health import PILLARS
from fsi.providers import resolve as resolver

st.set_page_config(page_title="Financial Statement Intelligence", page_icon="📊", layout="wide")

BAND_COLOUR = {
    "Strong": "#1e7a4b", "Healthy": "#4a8f3c", "Adequate": "#b8860b",
    "Weak": "#c1621f", "Distressed": "#a32020",
}


@st.cache_data(ttl=3600, show_spinner=False)
def cached_search(query: str):
    return [(m.ticker, m.name, m.region) for m in resolver.search(query, limit=8)]


@st.cache_data(ttl=3600, show_spinner=False)
def cached_analyse(ticker: str):
    return analyse(ticker)


@st.cache_data(ttl=3600, show_spinner=False)
def cached_universe_metrics():
    """The peer universe's statements are cached CSVs in the repo, so this needs no network."""
    return peers.load_universe_metrics()


@st.cache_data(ttl=3600, show_spinner=False)
def cached_sector(ticker: str):
    return peers.resolve_sector(ticker)


def pct(v, dp=1):
    return "n/a" if v is None or pd.isna(v) else f"{v:.{dp}%}"


def times(v):
    return "n/a" if v is None or pd.isna(v) else f"{v:.2f}x"


st.title("Financial Statement Intelligence")
st.caption(
    "Takes a company's filed statements, computes profitability, efficiency, leverage and "
    "cash-flow ratios, reads the trends, and scores financial health against published "
    "thresholds. US companies use SEC EDGAR filings; everywhere else uses Yahoo Finance."
)

query = st.text_input(
    "Company name or ticker",
    value="",
    placeholder="Apple, Reliance Industries, SAP, Nestle, ASML, 7203.T",
)

if not query:
    st.info(
        "Enter a company to begin. Names work as well as tickers, and listings outside the "
        "US are found by their exchange suffix (RELIANCE.NS, SAP.DE, SHEL.L, NESN.SW)."
    )
    st.stop()

with st.spinner("Finding the company..."):
    matches = cached_search(query)

if not matches:
    st.error(f"No listed company found for '{query}'. Try the ticker instead of the name.")
    st.stop()

# The same company often trades in several places, in different currencies and at different
# prices, so the listing is the user's choice rather than a silent default.
labels = [f"{t}  ·  {n}  ·  {r}" for t, n, r in matches]
choice = st.selectbox("Listing", labels, index=0)
ticker = matches[labels.index(choice)][0]

try:
    with st.spinner(f"Loading statements for {ticker}..."):
        a = cached_analyse(ticker)
except Exception as exc:  # noqa: BLE001 - surface the reason rather than a stack trace
    st.error(f"Could not analyse {ticker}: {exc}")
    st.stop()

colour = BAND_COLOUR.get(a.health["rating"], "#6b6b6b")

st.markdown(f"### {a.name}  ·  `{a.ticker}`")
top = st.columns([2, 1, 1, 1, 1])
top[0].markdown(
    f"<div style='font-size:2.4rem;font-weight:700;color:{colour};line-height:1.1'>"
    f"{a.health['overall']:.1f}<span style='font-size:1.1rem;color:#888'>/100</span></div>"
    f"<div style='color:{colour};font-weight:600'>{a.health['rating']}</div>",
    unsafe_allow_html=True,
)
top[1].metric("Region", a.region)
top[2].metric("Currency", a.currency)
top[3].metric("History", f"{a.years} years")
top[4].metric("Share price", f"{a.share_price:,.2f}" if a.share_price else "n/a")

st.caption(
    f"Source: {a.source}  ·  FY{a.first_year} to FY{a.last_year}  ·  figures in {a.currency} millions"
)

if a.notes:
    with st.expander("Data notes worth reading", expanded=False):
        for n in a.notes:
            st.markdown(f"- {n}")

st.divider()

left, right = st.columns([1, 1])

with left:
    st.subheader("Score decomposition")
    rows = []
    for p in PILLARS:
        s = a.health["pillars"][p.name]
        rows.append({"Pillar": p.name, "Weight": f"{p.weight:.0%}",
                     "Score": "n/a" if pd.isna(s) else f"{s:.1f}"})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    for p in PILLARS:
        s = a.health["pillars"][p.name]
        if not pd.isna(s):
            st.progress(min(max(s / 100, 0.0), 1.0), text=f"{p.name}  {s:.1f}")

    if a.health["unavailable_metrics"]:
        st.caption(
            "Not scored, because the company does not report them. The remaining weights in "
            "that pillar are renormalized rather than the gap being scored as zero or full "
            "marks: " + ", ".join(sorted(set(a.health["unavailable_metrics"]))) + "."
        )

with right:
    st.subheader("What the trends say")
    for line in a.trends:
        st.markdown(f"- {line}")

st.divider()
st.subheader("Against its sector")
st.caption(
    "The score above marks this company against fixed thresholds. The same pillars and "
    "weights are applied again here, scored by percentile against sector peers instead. "
    "Neither reading replaces the other, and where they disagree, the disagreement is the "
    "point."
)

sector = cached_sector(a.ticker)
comparison = peers.compare(a.ratios, a.health, sector, exclude_ticker=a.ticker,
                           universe_metrics=cached_universe_metrics())

if not comparison.usable:
    st.info(comparison.relative["note"])
else:
    rel = comparison.relative
    rel_colour = BAND_COLOUR.get(rel["rating"], "#6b6b6b")
    c1, c2, c3 = st.columns(3)
    c1.metric("Against fixed thresholds", f"{a.health['overall']:.1f}", a.health["rating"],
              delta_color="off")
    c2.metric(f"Against {rel['peer_count']} {rel['sector']} peers", f"{rel['overall']:.1f}",
              rel["rating"], delta_color="off")
    c3.metric("Gap", f"{comparison.gap:+.1f}",
              "sector economics" if abs(comparison.gap) >= 15 else "broadly agree",
              delta_color="off")

    st.markdown(
        f"Ranked against **{', '.join(rel['peers'])}**, the other {rel['sector']} companies "
        "in the 20-company universe. A company is never counted as its own peer."
    )

    pillar_rows = []
    for p in PILLARS:
        abs_s = a.health["pillars"][p.name]
        rel_s = rel["pillars"].get(p.name, float("nan"))
        pillar_rows.append({
            "Pillar": p.name,
            "Absolute": "n/a" if pd.isna(abs_s) else f"{abs_s:.1f}",
            "Vs peers": "n/a" if pd.isna(rel_s) else f"{rel_s:.1f}",
            "Gap": "n/a" if (pd.isna(abs_s) or pd.isna(rel_s)) else f"{rel_s - abs_s:+.1f}",
        })
    st.dataframe(pd.DataFrame(pillar_rows), hide_index=True, use_container_width=True)

    st.caption(
        "Percentile scores are zero-sum inside a peer group: they average to the middle by "
        "construction, so this reading can never say a whole sector is excellent. Only the "
        "absolute score can make a cross-sector statement, which is why both are kept."
    )

st.divider()
st.subheader("Ratio history")

r = a.ratios
view = pd.DataFrame({
    "FY": r["fiscal_year"].astype(int).astype(str),
    "Revenue": r["revenue"].map(lambda v: f"{v:,.0f}"),
    "Gross": r["gross_margin"].map(pct),
    "EBITDA": r["ebitda_margin"].map(pct),
    "Net": r["net_margin"].map(pct),
    "ROE": r["roe"].map(pct),
    "ROA": r["roa"].map(pct),
    "Asset TO": r["asset_turnover"].map(times),
    "D/E": r["debt_to_equity"].map(times),
    "Int cov": r["interest_coverage"].map(times),
    "Current": r["current_ratio"].map(times),
    "FCF margin": r["fcf_margin"].map(pct),
})
st.dataframe(view, hide_index=True, use_container_width=True)

chart_cols = st.columns(3)
for col, (label, series) in zip(chart_cols, [
    ("Revenue", r.set_index(r["fiscal_year"].astype(int))["revenue"]),
    ("EBITDA margin", r.set_index(r["fiscal_year"].astype(int))["ebitda_margin"]),
    ("FCF margin", r.set_index(r["fiscal_year"].astype(int))["fcf_margin"]),
]):
    col.caption(label)
    col.line_chart(series, height=180)

st.download_button(
    "Download ratios as CSV",
    r.to_csv(index=False).encode(),
    file_name=f"{a.ticker}_ratios.csv",
    mime="text/csv",
)

st.divider()
st.caption(
    "The headline score uses absolute thresholds, so capital-intensive businesses score lower "
    "than asset-light ones almost by construction; the sector comparison above is the check on "
    "that, and it only reaches sectors with at least three peers in the universe. Sector-level "
    "peers are still coarse: GICS Consumer Staples holds both grocery retail and branded "
    "consumer goods, whose margins differ roughly nine-fold, so Walmart is not rescued by it. "
    "This measures reported financial condition, not whether the shares are worth buying. Full "
    "methodology and limitations are in the repository README."
)
