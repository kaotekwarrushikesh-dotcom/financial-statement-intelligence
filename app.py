"""Financial Statement Intelligence, as an app.

Type a company name, get its financial health. Works for any listed company with published
statements, across the US, Europe, India and elsewhere.

The page is three sections and nothing else: the verdict, the evidence for it, and the
detail behind the evidence. An earlier version laid nine equally-weighted sections out in
one flat scroll, which meant the reader had to read all of it to find out which part was
the answer.
"""

import math
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).parent))

from fsi import peers
from fsi.company import analyse
from fsi.financial_health import PILLARS, build_metric_inputs, rating_band, score_metric
from fsi.providers import resolve as resolver
from fsi.providers import yahoo

st.set_page_config(page_title="Financial Statement Intelligence", page_icon="📊", layout="wide")

BAND_COLOUR = {
    "Strong": "#1e7a4b", "Healthy": "#4a8f3c", "Adequate": "#b8860b",
    "Weak": "#c1621f", "Distressed": "#a32020",
}

BAND_PHRASING = {
    "Strong": "is in strong financial condition",
    "Healthy": "is in healthy financial condition",
    "Adequate": "is in adequate financial condition",
    "Weak": "shows a weak financial position",
    "Distressed": "shows signs of financial distress",
}

# A MetricSpec carries a floor and a ceiling but no display information, because the scoring
# engine has no business knowing about the UI. The label and the unit live here instead.
METRIC_DISPLAY = {
    "net_margin": ("Net margin", "pct"),
    "ebitda_margin": ("EBITDA margin", "pct"),
    "roe": ("Return on equity", "pct"),
    "roa": ("Return on assets", "pct"),
    "fcf_margin": ("Free cash flow margin", "pct"),
    "revenue_cagr": ("Revenue CAGR", "pct"),
    "net_income_cagr": ("Net income CAGR", "pct"),
    "debt_to_equity": ("Debt to equity", "times"),
    "interest_coverage": ("Interest coverage", "times"),
    "current_ratio": ("Current ratio", "times"),
    "cfo_to_net_income": ("Cash conversion, CFO over net income", "times"),
    "asset_turnover": ("Asset turnover", "times"),
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


def unit_fmt(value, unit):
    """Format a scored metric in its own unit, so a margin never prints as a multiple."""
    if value is None or pd.isna(value):
        return "n/a"
    return pct(value) if unit == "pct" else times(value)


def metric_detail(ratios: pd.DataFrame) -> dict[str, list[dict]]:
    """The per-metric working behind each pillar score.

    score_pillars returns only the pillar aggregates, so this recomputes the metric level
    from the same public helpers rather than duplicating the arithmetic. The page needs it
    twice: to name the metric holding a weak pillar back, and to show every value against
    the threshold that scored it. The thresholds were previously documented in the README
    and invisible in the product, which left every number on the page without a reference
    point.
    """
    inputs = build_metric_inputs(ratios)
    detail: dict[str, list[dict]] = {}
    for pillar in PILLARS:
        rows = []
        for spec in pillar.metrics:
            value = inputs.get(spec.name, float("nan"))
            label, unit = METRIC_DISPLAY.get(spec.name, (spec.name, "times"))
            rows.append({
                "metric": spec.name, "label": label, "unit": unit, "value": value,
                "score": score_metric(value, spec), "floor": spec.floor,
                "ceiling": spec.ceiling, "weight": spec.weight,
            })
        detail[pillar.name] = rows
    return detail


def dupont_terms(row) -> tuple[float, float, float] | None:
    """The three DuPont terms for one year, or None where the identity cannot be formed."""
    nm, at = row.get("net_margin", float("nan")), row.get("asset_turnover", float("nan"))
    em = row.get("equity_multiplier", float("nan"))
    if any(v is None or pd.isna(v) for v in (nm, at, em)):
        return None
    return nm, at, em


def dupont_drivers(ratios: pd.DataFrame) -> dict | None:
    """Which of the three terms actually moved ROE over the period on file.

    A multiplicative identity decomposes cleanly in logs: the log change in ROE is exactly
    the sum of the log changes in its three terms, so each term's share of the move is well
    defined rather than a rough attribution. Logs need every value strictly positive, which
    a loss-making year and negative book equity both break, so both are checked instead of
    assumed.
    """
    if len(ratios) < 2:
        return None
    first, last = ratios.iloc[0], ratios.iloc[-1]
    start, end = dupont_terms(first), dupont_terms(last)
    if start is None or end is None or any(v <= 0 for v in start + end):
        return None

    deltas = [math.log(e / s0) for s0, e in zip(start, end)]
    total = sum(deltas)
    if abs(total) < 1e-9:
        return None
    return {
        "shares": list(zip(("net margin", "asset turnover", "equity multiplier"), 
                           (d / total for d in deltas))),
        "roe_start": first["roe"], "roe_end": last["roe"],
        "fy_start": int(first["fiscal_year"]), "fy_end": int(last["fiscal_year"]),
    }


def verdict_lines(a, detail) -> list[str]:
    """The conclusion, in prose, ahead of any of the evidence for it.

    The score and its band were always computed and then left for the reader to interpret
    out of a coloured number. Saying it in a sentence is the entire point of the page.
    """
    health = a.health
    scored = sorted(
        ((name, s) for name, s in health["pillars"].items() if not pd.isna(s)),
        key=lambda t: t[1],
    )
    lead = (
        f"**{a.name} {BAND_PHRASING[health['rating']]}, scoring "
        f"{health['overall']:.1f} out of 100** on {a.years} years of filed statements."
    )
    if len(scored) < 2:
        return [lead]

    (weak_name, weak_score), (strong_name, strong_score) = scored[0], scored[-1]
    second = (f"{strong_name} is its strongest pillar at {strong_score:.0f}, and "
              f"{weak_name} its weakest at {weak_score:.0f}")

    # Name the metric actually holding the weak pillar back. "Leverage is weak" invites the
    # obvious question, and answering it in the same breath is the difference between
    # reporting a score and giving a read.
    available = [r for r in detail[weak_name] if not pd.isna(r["score"])]
    if available:
        worst = min(available, key=lambda r: r["score"])
        second += (
            f", held back by {worst['label'].lower()} of "
            f"{unit_fmt(worst['value'], worst['unit'])} against the "
            f"{unit_fmt(worst['ceiling'], worst['unit'])} that would score full marks"
        )
    return [lead, second + "."]


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


st.title("Financial Statement Intelligence")
st.caption(
    "Reads a company's filed statements, scores its financial health against published "
    "thresholds, and says what the result means. Method and limitations are in the last tab."
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

detail = metric_detail(a.ratios)
by_metric = {r["metric"]: r for rows in detail.values() for r in rows}
latest = a.ratios.iloc[-1]
prior = a.ratios.iloc[-2] if len(a.ratios) > 1 else None
colour = BAND_COLOUR.get(a.health["rating"], "#6b6b6b")


def banded(metric_key: str) -> str:
    """What the scoring engine makes of one metric, as words.

    The 80/65/50/35 bands are defined on the overall score, but every metric score is on
    that same 0-100 scale by construction, so the same bands read correctly here. A bare
    percentage is not a finding; a percentage with a verdict attached is.
    """
    row = by_metric.get(metric_key)
    if row is None or pd.isna(row["score"]):
        return "not scored"
    return f"{rating_band(row['score'])}, scores {row['score']:.0f}"


# ---------------------------------------------------------------- 1. the verdict

st.markdown(f"## {a.name}  ·  `{a.ticker}`")

head_left, head_right = st.columns([3, 2])

with head_left:
    # The prose is kept in a column rather than spanning the wide layout, because a verdict
    # set across the full page width is a measure no one reads comfortably.
    for line in verdict_lines(a, detail):
        st.markdown(line)
    st.caption(
        f"{a.source}  ·  FY{a.first_year} to FY{a.last_year}  ·  "
        f"figures in {a.currency} millions  ·  {a.region}"
    )

with head_right:
    st.markdown(
        f"<div style='font-size:3rem;font-weight:700;color:{colour};line-height:1'>"
        f"{a.health['overall']:.1f}<span style='font-size:1.1rem;color:#888'>/100</span></div>"
        f"<div style='color:{colour};font-weight:600;font-size:1.1rem'>"
        f"{a.health['rating']}</div>"
        f"<div style='color:#888;font-size:0.8rem'>Strong 80 · Healthy 65 · Adequate 50 · "
        f"Weak 35</div>",
        unsafe_allow_html=True,
    )

if a.notes:
    # Expanded, unlike every other disclosure on the page. These notes are the ones that
    # change how a specific number should be read, so hiding them defeats their purpose.
    with st.expander("Data notes worth reading", expanded=True):
        for n in a.notes:
            st.markdown(f"- {n}")

st.divider()

# ---------------------------------------------------------------- 2. the evidence

st.subheader("The evidence")

g = st.columns(3)
revenue_growth = (
    latest["revenue"] / prior["revenue"] - 1
    if prior is not None and prior["revenue"] else float("nan")
)
g[0].metric("Market capitalisation", scaled_raw(a.market_cap, a.currency))
g[1].metric(f"Revenue FY{int(latest['fiscal_year'])}", scaled(latest["revenue"], a.currency),
            "n/a" if pd.isna(revenue_growth) else f"{revenue_growth:+.1%} YoY")
g[2].metric("Free cash flow", scaled(latest["fcf"], a.currency),
            "n/a" if pd.isna(latest["fcf_margin"]) else f"{latest['fcf_margin']:.1%} of revenue")

g2 = st.columns(4)
for col, key in zip(g2, ["ebitda_margin", "net_margin", "roe", "debt_to_equity"]):
    row = by_metric[key]
    col.metric(row["label"], unit_fmt(row["value"], row["unit"]), banded(key), delta_color="off")

# ROE taken apart, directly under the ROE the row above reports. ROE is the product of
# three things this scorecard already measures separately, so a high one says nothing on
# its own about which of the three produced it: Apple and Home Depot land within 40 points
# of each other on ROE while earning 31% and 13% on their assets. Without this line the
# page cannot tell those two apart.
terms = dupont_terms(latest)
if terms is not None:
    nm, at, em = terms
    st.markdown(
        f"**Return on equity {pct(latest['roe'])}**  =  net margin {pct(nm)}  ×  "
        f"asset turnover {times(at)}  ×  equity multiplier {times(em)}"
    )
    st.caption(
        f"The business earns {pct(latest['roa'])} on its assets. Everything above that is "
        f"capital structure: the equity multiplier of {times(em)} says assets are "
        f"{em:.2f} times book equity, so the same operating performance is being spread "
        f"over a smaller equity base."
    )
elif pd.isna(latest.get("roe", float("nan"))):
    st.markdown("**Return on equity is not reported for this company.**")
    st.caption(
        f"Book equity is not positive, which sustained buybacks can cause, and both ROE "
        f"and the equity multiplier flip sign and stop meaning anything when it happens. "
        f"Return on assets, at {pct(latest['roa'])}, is the reading that survives, and it "
        f"is the one the Efficiency pillar scores."
    )

drivers = dupont_drivers(a.ratios)
if drivers:
    # Shares are shares of the move, and the move can be downward. Printing a bare "+67%"
    # against a falling ROE reads as a term that helped when it is in fact most of the
    # fall, so the direction is named and the signs are turned into words.
    rose = drivers["roe_end"] >= drivers["roe_start"]
    drove = [(lab, sh) for lab, sh in drivers["shares"] if sh >= 0]
    opposed = [lab for lab, sh in drivers["shares"] if sh < 0]
    ranked = ", ".join(f"{lab} {sh:.0%}"
                       for lab, sh in sorted(drove, key=lambda t: -t[1]))
    note = (f"Between FY{drivers['fy_start']} and FY{drivers['fy_end']} ROE "
            f"{'rose' if rose else 'fell'} from {pct(drivers['roe_start'])} to "
            f"{pct(drivers['roe_end'])}. Share of that "
            f"{'rise' if rose else 'fall'}: {ranked}.")
    if opposed:
        note += (f" {' and '.join(opposed).capitalize()} moved the other way and "
                 f"partly offset it.")
    st.caption(note)

st.write("")

bars, trends = st.columns([1, 1])

with bars:
    st.markdown("**How the score decomposes**")
    for p in PILLARS:
        s = a.health["pillars"][p.name]
        if pd.isna(s):
            st.caption(f"{p.name}: not scored, no metric in this pillar is reported")
            continue
        st.progress(
            min(max(s / 100, 0.0), 1.0),
            text=f"{p.name}  ·  {s:.0f}  ·  {rating_band(s)}  ·  {p.weight:.0%} of the score",
        )

    if a.health["unavailable_metrics"]:
        # Labelled, not raw. The engine keys metrics by column name, and letting
        # "interest_coverage" through to the page undoes the point of disclosing it.
        missing = sorted({
            METRIC_DISPLAY.get(m, (m, ""))[0] for m in a.health["unavailable_metrics"]
        })
        st.caption(
            "Not scored, because the company does not report them. The remaining weights in "
            "that pillar are renormalized rather than the gap being scored as zero or full "
            "marks: " + ", ".join(missing) + "."
        )

    with st.expander("The thresholds behind each pillar"):
        st.caption(
            "Every metric is scored by linear interpolation between a level that scores 0 "
            "and a level that scores 100. Both ends are shown, so a score can be argued "
            "with rather than taken on faith."
        )
        for p in PILLARS:
            st.markdown(f"**{p.name}**  ·  {p.weight:.0%} of the overall score")
            for row in detail[p.name]:
                if pd.isna(row["score"]):
                    st.caption(f"{row['label']}: not reported, excluded from the pillar")
                    continue
                st.progress(
                    min(max(row["score"] / 100, 0.0), 1.0),
                    text=(f"{row['label']}  ·  {unit_fmt(row['value'], row['unit'])}  ·  "
                          f"scores {row['score']:.0f}  "
                          f"(0 at {unit_fmt(row['floor'], row['unit'])}, "
                          f"100 at {unit_fmt(row['ceiling'], row['unit'])})"),
                )

with trends:
    st.markdown("**What the trends say**")
    for line in a.trends:
        st.markdown(f"- {line}")

st.divider()

# ---------------------------------------------------------------- 3. the detail

st.subheader("The detail")

tab_sector, tab_price, tab_history, tab_method = st.tabs(
    ["Against its sector", "Share price", "Ratio history", "Method and limits"]
)

with tab_sector:
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

        # A bare "-29.2" told the reader nothing. What matters is the direction and what it
        # means about the company, so the gap is stated as a sentence rather than a number.
        direction = "lower" if gap < 0 else "higher"
        st.markdown(
            f"**{a.name} scores {abs(gap):.1f} points {direction} against its "
            f"{rel['sector']} peers than against fixed thresholds.**"
        )

        if gap <= -15:
            st.markdown(
                f"The headline score is partly rewarding {a.name} for operating in a sector "
                f"with strong economics rather than for how the company is run. Measured "
                f"against the companies it actually competes with, it is weaker than the "
                f"headline suggests."
            )
        elif gap >= 15:
            st.markdown(
                f"The headline score is penalising {a.name} for its sector's economics "
                f"rather than for how the company is run. Against the companies it actually "
                f"competes with, it holds up better than the headline suggests."
            )
        else:
            st.markdown(
                "The two readings broadly agree, so the headline score is not being "
                "distorted by sector economics in either direction here."
            )

        pillar_rows = []
        for p in PILLARS:
            abs_s = a.health["pillars"][p.name]
            rel_s = rel["pillars"].get(p.name, float("nan"))
            pillar_rows.append({
                "Pillar": p.name,
                "Absolute": None if pd.isna(abs_s) else round(abs_s, 1),
                "Vs peers": None if pd.isna(rel_s) else round(rel_s, 1),
                "Gap": None if (pd.isna(abs_s) or pd.isna(rel_s)) else round(rel_s - abs_s, 1),
            })
        st.dataframe(
            pd.DataFrame(pillar_rows), hide_index=True, use_container_width=True,
            column_config={
                "Absolute": st.column_config.NumberColumn(format="%.1f"),
                "Vs peers": st.column_config.NumberColumn(format="%.1f"),
                "Gap": st.column_config.NumberColumn(format="%+.1f"),
            },
        )
        st.caption(
            f"Peer group: {', '.join(rel['peers'])}. The first score asks \"is this a good "
            "business\". The second asks \"is this a good business for its industry\". "
            "Percentile scores are zero-sum inside a peer group and average to the middle "
            "by construction, so this reading can never say a whole sector is excellent."
        )

with tab_price:
    st.caption(
        "The statements describe the business. This describes what the market has paid for "
        "it over the same period, which is a different question and often a different story."
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
        # A value plus a sub-line rather than "low to high" in one string, which was wide
        # enough to be truncated to "15.56 to 3..." in a four-column row.
        pm[2].metric(f"{price_period} low", f"{low:,.2f}", f"high {high:,.2f}",
                     delta_color="off")
        pm[3].metric("Below period high", pct(off_high))

        st.line_chart(closes.rename("Close"), height=320)

        for note in price_notes:
            st.caption(note)
        st.caption(
            f"Daily closes from Yahoo Finance, {len(closes):,} trading days. Not adjusted "
            "for dividends, so this is the price paid rather than the total return earned."
        )

with tab_history:
    r = a.ratios
    # Numeric columns rather than pre-formatted strings. The old table held twelve columns
    # of text, which meant Streamlit could neither sort nor plot any of it, and which did
    # not fit a laptop. Formatting now happens in column_config, where the values stay
    # numbers.
    hist = pd.DataFrame({
        "FY": r["fiscal_year"].astype(int),
        "Revenue (mn)": r["revenue"],
        "Gross": r["gross_margin"] * 100,
        "EBITDA": r["ebitda_margin"] * 100,
        "Net": r["net_margin"] * 100,
        "ROE": r["roe"] * 100,
        "ROA": r["roa"] * 100,
        "FCF margin": r["fcf_margin"] * 100,
        "Asset TO": r["asset_turnover"],
        "D/E": r["debt_to_equity"],
        "Int cov": r["interest_coverage"],
        "Current": r["current_ratio"],
    })
    headline_cols = ["FY", "Revenue (mn)", "EBITDA", "Net", "ROE", "FCF margin", "D/E"]
    show_all = st.checkbox("Show every ratio", value=False)

    pct_fmt = st.column_config.NumberColumn(format="%.1f%%")
    times_fmt = st.column_config.NumberColumn(format="%.2fx")
    st.dataframe(
        hist if show_all else hist[headline_cols],
        hide_index=True, use_container_width=True,
        column_config={
            "FY": st.column_config.NumberColumn(format="%d"),
            "Revenue (mn)": st.column_config.NumberColumn(format="%.0f"),
            "Gross": pct_fmt, "EBITDA": pct_fmt, "Net": pct_fmt, "ROE": pct_fmt,
            "ROA": pct_fmt, "FCF margin": pct_fmt,
            "Asset TO": times_fmt, "D/E": times_fmt, "Int cov": times_fmt,
            "Current": times_fmt,
        },
    )

    st.markdown("**ROE decomposed**")
    du = pd.DataFrame({
        "FY": r["fiscal_year"].astype(int),
        "Net margin": r["net_margin"] * 100,
        "Asset turnover": r["asset_turnover"],
        "Equity multiplier": r.get("equity_multiplier"),
        "ROE": r["roe"] * 100,
        "ROA": r["roa"] * 100,
    })
    st.dataframe(
        du, hide_index=True, use_container_width=True,
        column_config={
            "FY": st.column_config.NumberColumn(format="%d"),
            "Net margin": st.column_config.NumberColumn(format="%.1f%%"),
            "Asset turnover": st.column_config.NumberColumn(format="%.2fx"),
            "Equity multiplier": st.column_config.NumberColumn(format="%.2fx"),
            "ROE": st.column_config.NumberColumn(format="%.1f%%"),
            "ROA": st.column_config.NumberColumn(format="%.1f%%"),
        },
    )

    # The three terms live on different scales, so plotting them raw puts a 0.27 margin and
    # a 4.87 multiplier on one axis and hides the thing worth seeing. Indexing each to its
    # own starting value puts them on a common footing, and the question the chart answers
    # is which line pulled away from the others.
    comp = du[["Net margin", "Asset turnover", "Equity multiplier"]]
    base = comp.iloc[0]
    if base.notna().all() and (base > 0).all():
        indexed = comp.divide(base) * 100
        # A fiscal year is a label, not a quantity: as an integer index Streamlit
        # formats it with a thousands separator and the axis reads "2,016".
        indexed.index = du["FY"].astype(str)
        st.line_chart(indexed, height=260)
        st.caption(
            "Each term indexed to 100 in the first year on file. The line that pulls away "
            "is the one carrying ROE, and an equity multiplier doing the pulling means the "
            "return improved because the equity base shrank rather than because the "
            "business did."
        )
    else:
        st.caption(
            "The indexed chart needs all three terms positive in the first year on file, "
            "which is not the case here."
        )

    st.markdown("**Trends**")
    fy_labels = r["fiscal_year"].astype(int).astype(str)
    chart_cols = st.columns(3)
    for col, (label, series) in zip(chart_cols, [
        ("Revenue", r.set_index(fy_labels)["revenue"]),
        ("EBITDA margin", r.set_index(fy_labels)["ebitda_margin"]),
        ("FCF margin", r.set_index(fy_labels)["fcf_margin"]),
    ]):
        col.caption(label)
        col.line_chart(series, height=180)

    st.download_button(
        "Download ratios as CSV",
        r.to_csv(index=False).encode(),
        file_name=f"{a.ticker}_ratios.csv",
        mime="text/csv",
    )

with tab_method:
    # Everything that used to sit as grey text between the sections. Nine caption blocks
    # spread down the page read as a disclaimer wrapped around a product, and the eye
    # learns to skip grey, which took the load-bearing caveats down with the rest.
    st.markdown(
        "**Where the numbers come from.** US companies are read from SEC EDGAR filings. "
        "Everywhere else uses Yahoo Finance. Figures are as filed, in the reporting "
        "currency, in millions."
    )
    st.markdown(
        "**How the score is built.** Five weighted pillars, each the weighted average of "
        "its metric scores, each metric interpolated linearly between a floor that scores "
        "0 and a ceiling that scores 100. Every threshold is listed under *The thresholds "
        "behind each pillar* above and in the repository README, so the score can be "
        "challenged and recalibrated rather than taken on faith."
    )
    st.markdown(
        "**Missing data.** A metric the company does not report is dropped and the "
        "remaining weights inside that pillar are renormalized. Scoring a gap as either 0 "
        "or 100 would be a fabricated result. Dropped metrics are always disclosed."
    )
    st.markdown(
        "**Why there are two scores.** The headline score uses absolute thresholds, so "
        "capital-intensive businesses score lower than asset-light ones almost by "
        "construction. The sector comparison is the check on that, and it only reaches "
        "sectors with at least three peers in the 20-company universe."
    )
    st.markdown(
        "**Where the sector check is still coarse.** GICS Consumer Staples holds both "
        "grocery retail and branded consumer goods, whose margins differ roughly nine-fold, "
        "so Walmart is not rescued by it."
    )
    st.markdown(
        "**What this is not.** It measures reported financial condition, not whether the "
        "shares are worth buying. There is no valuation in it and no recommendation."
    )
