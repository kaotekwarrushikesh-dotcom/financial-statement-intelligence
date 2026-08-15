"""Financial Statement Intelligence, as an app.

Type a company name, get its financial health. Works for any listed company with published
statements, across the US, Europe, India and elsewhere.
"""

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from src.company import analyse
from src.financial_health import PILLARS
from src.providers import resolve as resolver

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
    "The thresholds behind the score are absolute rather than sector-relative, so "
    "capital-intensive businesses score lower than asset-light ones almost by construction. "
    "This measures reported financial condition, not whether the shares are worth buying. "
    "Full methodology and limitations are in the repository README."
)
