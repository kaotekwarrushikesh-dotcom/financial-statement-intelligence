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
from fsi.providers import yahoo

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


@st.cache_data(ttl=900, show_spinner=False)
def cached_prices(ticker: str, period: str):
    """Shorter TTL than the statements: filings are stable for months, a share price is not."""
    return yahoo.price_history(ticker, period)


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
st.subheader("At a glance")

latest = a.ratios.iloc[-1]
prior = a.ratios.iloc[-2] if len(a.ratios) > 1 else None


def scaled(value, currency):
    """Statement figures arrive in millions. Market capitalisation does not, so the two are
    never passed through the same formatter: doing so is how a company ends up displayed a
    million times too large."""
    if value is None or pd.isna(value):
        return "n/a"
    for cutoff, suffix in ((1_000_000, "tn"), (1_000, "bn")):
        if abs(value) >= cutoff:
            return f"{currency} {value / cutoff:,.1f}{suffix}"
    return f"{currency} {value:,.0f}mn"


def scaled_raw(value, currency):
    """For figures already in whole currency units, such as market capitalisation."""
    if not value or pd.isna(value):
        return "n/a"
    return scaled(value / 1_000_000, currency)


revenue_growth = (
    latest["revenue"] / prior["revenue"] - 1
    if prior is not None and prior["revenue"] else float("nan")
)

g = st.columns(3)
g[0].metric("Market capitalisation", scaled_raw(a.market_cap, a.currency))
g[1].metric(f"Revenue FY{int(latest['fiscal_year'])}", scaled(latest["revenue"], a.currency),
            "n/a" if pd.isna(revenue_growth) else f"{revenue_growth:+.1%} YoY")
g[2].metric("Free cash flow", scaled(latest["fcf"], a.currency),
            "n/a" if pd.isna(latest["fcf_margin"]) else f"{latest['fcf_margin']:.1%} of revenue")

g2 = st.columns(4)
g2[0].metric("EBITDA margin", pct(latest["ebitda_margin"]))
g2[1].metric("Net margin", pct(latest["net_margin"]))
g2[2].metric("Return on equity", pct(latest["roe"]))
g2[3].metric("Debt to equity", times(latest["debt_to_equity"]))

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
    gap = comparison.gap

    c1, c2 = st.columns(2)
    c1.metric("Scored against fixed thresholds", f"{a.health['overall']:.1f}/100",
              a.health["rating"], delta_color="off")
    c2.metric(f"Scored against {rel['peer_count']} {rel['sector']} peers",
              f"{rel['overall']:.1f}/100", rel["rating"], delta_color="off")

    # A bare "-29.2" told the reader nothing. What matters is the direction and what it means
    # about the company, so the gap is stated as a sentence rather than left as a number.
    direction = "lower" if gap < 0 else "higher"
    headline = (f"**{a.name} scores {abs(gap):.1f} points {direction} against its "
                f"{rel['sector']} peers than against fixed thresholds.**")

    if gap <= -15:
        meaning = (
            f"The headline score is partly rewarding {a.name} for operating in a sector with "
            f"strong economics rather than for how the company is run. Measured against the "
            f"companies it actually competes with, it is weaker than the headline suggests."
        )
    elif gap >= 15:
        meaning = (
            f"The headline score is penalising {a.name} for its sector's economics rather "
            f"than for how the company is run. Against the companies it actually competes "
            f"with, it holds up better than the headline suggests."
        )
    else:
        meaning = (
            "The two readings broadly agree, so the headline score is not being distorted by "
            "sector economics in either direction here."
        )

    st.markdown(headline)
    st.markdown(meaning)
    st.caption(
        f"Peer group: {', '.join(rel['peers'])}. These are the other {rel['sector']} companies "
        "in the 20-company universe; a company is never counted as its own peer. The first "
        "score asks \"is this a good business\". The second asks \"is this a good business "
        "for its industry\"."
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
st.subheader("Share price")
st.caption(
    "The statements above describe the business. This describes what the market has paid for "
    "it over the same period, which is a different question and often tells a different story."
)

price_period = st.radio("Period", ["1y", "3y", "5y", "10y"], index=2, horizontal=True,
                        key="price_period")

try:
    with st.spinner("Loading price history..."):
        closes, price_currency, price_notes = cached_prices(a.ticker, price_period)
except Exception as exc:  # noqa: BLE001 - the rest of the page is still worth showing
    st.info(f"Price history is unavailable for {a.ticker}: {exc}")
else:
    first, last = float(closes.iloc[0]), float(closes.iloc[-1])
    high, low = float(closes.max()), float(closes.min())
    total_return = last / first - 1 if first else float("nan")
    off_high = last / high - 1 if high else float("nan")

    pm = st.columns(4)
    pm[0].metric("Latest close", f"{price_currency} {last:,.2f}")
    pm[1].metric(f"Return over {price_period}", f"{total_return:+.1%}")
    # Shown as a value plus a sub-line rather than "low to high" in one string, which was
    # wide enough to be truncated to "15.56 to 3..." in a four-column row.
    pm[2].metric(f"{price_period} low", f"{low:,.2f}", f"high {high:,.2f}", delta_color="off")
    pm[3].metric("Below period high", pct(off_high))

    st.line_chart(closes.rename("Close"), height=320)

    for note in price_notes:
        st.caption(note)
    st.caption(
        f"Daily closes from Yahoo Finance, {len(closes):,} trading days. Not adjusted for "
        "dividends, so this is the price paid rather than the total return earned."
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
