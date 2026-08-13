"""Build the cross-company analysis outputs: a PDF report plus machine-readable CSVs."""

from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.data_cleaning import clean_financials
from src.data_loader import load_financials
from src.financial_health import PILLARS, financial_health_score
from src.ratios import calculate_ratios
from src.trends import trend_report
from src.universe import UNIVERSE

INK = colors.HexColor("#1a1a1a")
MUTED = colors.HexColor("#6b6b6b")
RULE = colors.HexColor("#d4d4d4")
BAND = colors.HexColor("#f2f2f2")
ACCENT = colors.HexColor("#1f4e79")


def build_styles() -> dict:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("t", parent=base["Title"], fontSize=22, textColor=INK, spaceAfter=4),
        "subtitle": ParagraphStyle("st", parent=base["Normal"], fontSize=10.5, textColor=MUTED, spaceAfter=16),
        "h1": ParagraphStyle("h1", parent=base["Heading1"], fontSize=15, textColor=ACCENT, spaceBefore=10, spaceAfter=8),
        "h2": ParagraphStyle("h2", parent=base["Heading2"], fontSize=11.5, textColor=INK, spaceBefore=10, spaceAfter=5),
        "body": ParagraphStyle("b", parent=base["Normal"], fontSize=9.5, leading=14, textColor=INK, alignment=TA_LEFT, spaceAfter=6),
        "small": ParagraphStyle("s", parent=base["Normal"], fontSize=8, leading=11, textColor=MUTED, spaceAfter=4),
    }


def analyse_all(data_dir: Path) -> tuple[dict, pd.DataFrame]:
    """Run the pipeline for every company in the universe."""
    results, summary_rows = {}, []

    for ticker, (name, sector) in UNIVERSE.items():
        csv_path = data_dir / f"{ticker}_financials.csv"
        if not csv_path.exists():
            continue

        ratios = calculate_ratios(clean_financials(load_financials(csv_path)))
        health = financial_health_score(ratios)
        results[ticker] = {"name": name, "sector": sector, "ratios": ratios, "health": health}

        latest = ratios.iloc[-1]
        summary_rows.append(
            {
                "ticker": ticker,
                "company": name,
                "sector": sector,
                "first_fy": int(ratios.fiscal_year.iloc[0]),
                "last_fy": int(ratios.fiscal_year.iloc[-1]),
                "health_score": health["overall"],
                "rating": health["rating"],
                **{p.name: health["pillars"][p.name] for p in PILLARS},
                "revenue_latest": latest["revenue"],
                "net_margin": latest["net_margin"],
                "roe": latest["roe"],
                "debt_to_equity": latest["debt_to_equity"],
                "fcf_margin": latest["fcf_margin"],
            }
        )

    summary = pd.DataFrame(summary_rows).sort_values("health_score", ascending=False).reset_index(drop=True)
    summary.insert(0, "rank", range(1, len(summary) + 1))
    return results, summary


def fmt_pct(v: float, dp: int = 1) -> str:
    return "n/a" if pd.isna(v) else f"{v:.{dp}%}"


def fmt_x(v: float) -> str:
    return "n/a" if pd.isna(v) else f"{v:.2f}x"


def fmt_score(v: float) -> str:
    return "n/a" if pd.isna(v) else f"{v:.1f}"


# The PDF base fonts have no block-drawing glyphs, so score bars are drawn as a real
# coloured cell rather than typed with a character that would fall back to a blank box.
SECTOR_SHORT = {
    "Communication Services": "Comm. Svcs",
    "Consumer Discretionary": "Cons. Disc.",
    "Consumer Staples": "Cons. Staples",
    "Technology": "Technology",
    "Healthcare": "Healthcare",
    "Energy": "Energy",
}


def score_colour(score: float) -> colors.Color:
    if pd.isna(score):
        return MUTED
    if score >= 80:
        return colors.HexColor("#1e7a4b")
    if score >= 65:
        return colors.HexColor("#4a8f3c")
    if score >= 50:
        return colors.HexColor("#b8860b")
    if score >= 35:
        return colors.HexColor("#c1621f")
    return colors.HexColor("#a32020")


def _bar(score: float, full_width: float):
    """A proportional score bar, drawn as a filled cell."""
    if pd.isna(score):
        return ""
    bar = Table([[""]], colWidths=[max(1.2, full_width * score / 100.0)], rowHeights=[5.5])
    bar.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), score_colour(score))]))
    return bar


def _table(data: list[list], widths: list[float], align_right_from: int = 1) -> Table:
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(
        TableStyle(
            [
                ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 7.8),
                ("FONT", (0, 1), (-1, -1), "Helvetica", 7.8),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
                ("ALIGN", (align_right_from, 0), (-1, -1), "RIGHT"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, BAND]),
                ("GRID", (0, 0), (-1, -1), 0.25, RULE),
                ("TOPPADDING", (0, 0), (-1, -1), 3.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    return t


def league_table(summary: pd.DataFrame, widths: list[float]) -> Table:
    header = ["#", "Company", "Sector", "Score", "Rating", "Prof.", "Growth", "Lev.", "Cash", "Eff."]
    rows = [header]
    for _, r in summary.iterrows():
        rows.append(
            [
                str(r["rank"]),
                f"{r['ticker']}  {r['company'][:22]}",
                SECTOR_SHORT.get(r["sector"], r["sector"]),
                fmt_score(r["health_score"]),
                r["rating"],
                fmt_score(r["Profitability"]),
                fmt_score(r["Growth"]),
                fmt_score(r["Leverage"]),
                fmt_score(r["Cash generation"]),
                fmt_score(r["Efficiency"]),
            ]
        )
    return _table(rows, widths, align_right_from=3)


def company_pages(ticker: str, entry: dict, styles: dict, width: float) -> list:
    ratios, health = entry["ratios"], entry["health"]
    flow = []

    first, last = int(ratios.fiscal_year.iloc[0]), int(ratios.fiscal_year.iloc[-1])
    flow.append(Paragraph(f"{entry['name']} ({ticker})", styles["h1"]))
    flow.append(
        Paragraph(
            f"{entry['sector']}  |  FY{first} to FY{last}  |  "
            f"Financial Health {health['overall']}/100 ({health['rating']})",
            styles["small"],
        )
    )
    flow.append(Spacer(1, 5))

    # Ratio history
    cols = [
        ("fiscal_year", "FY", lambda v: str(int(v))),
        ("revenue", "Revenue $m", lambda v: f"{v:,.0f}"),
        ("gross_margin", "Gross", fmt_pct),
        ("ebitda_margin", "EBITDA", fmt_pct),
        ("net_margin", "Net", fmt_pct),
        ("roe", "ROE", fmt_pct),
        ("roa", "ROA", fmt_pct),
        ("asset_turnover", "Asset TO", lambda v: fmt_x(v)),
        ("debt_to_equity", "D/E", fmt_x),
        ("interest_coverage", "Int cov", fmt_x),
        ("current_ratio", "Current", fmt_x),
        ("fcf_margin", "FCF mgn", fmt_pct),
    ]
    data = [[label for _, label, _ in cols]]
    for _, row in ratios.iterrows():
        data.append([f(row[key]) for key, _, f in cols])

    w = width / len(cols)
    flow.append(_table(data, [w * 0.55] + [w * 1.12] + [w * 1.0] * (len(cols) - 2)))
    flow.append(Spacer(1, 9))

    flow.append(Paragraph("Trend analysis", styles["h2"]))
    for line in trend_report(ratios):
        flow.append(Paragraph(f"&bull; {line}", styles["body"]))

    flow.append(Spacer(1, 4))
    flow.append(Paragraph("Score decomposition", styles["h2"]))

    bar_width = width * 0.54
    score_rows = [["Pillar", "Weight", "Score", ""]]
    for p in PILLARS:
        s = health["pillars"][p.name]
        score_rows.append([p.name, f"{p.weight:.0%}", fmt_score(s), _bar(s, bar_width)])
    score_rows.append(["Overall", "100%", fmt_score(health["overall"]), health["rating"]])

    st = _table(score_rows, [width * 0.22, width * 0.10, width * 0.10, width * 0.58])
    st.setStyle(TableStyle([("ALIGN", (3, 1), (3, len(PILLARS)), "LEFT")]))
    st.setStyle(TableStyle([("FONT", (0, len(score_rows) - 1), (-1, -1), "Helvetica-Bold", 7.8)]))
    flow.append(st)

    if health["unavailable_metrics"]:
        missing = ", ".join(sorted(set(health["unavailable_metrics"])))
        flow.append(Spacer(1, 5))
        flow.append(
            Paragraph(
                f"Not scored (not reported in filings, weights renormalized): {missing}.",
                styles["small"],
            )
        )

    flow.append(PageBreak())
    return flow


def build_pdf(results: dict, summary: pd.DataFrame, out_path: Path) -> None:
    styles = build_styles()
    doc = SimpleDocTemplate(
        str(out_path),
        pagesize=A4,
        leftMargin=14 * mm,
        rightMargin=14 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
        title="Financial Statement Intelligence",
        author="Module 1",
    )
    width = doc.width
    flow = []

    # Cover
    flow.append(Paragraph("Financial Statement Intelligence", styles["title"]))
    flow.append(
        Paragraph(
            f"Module 1 &bull; {len(results)} companies &bull; 10 fiscal years each &bull; "
            f"source: SEC EDGAR XBRL company facts",
            styles["subtitle"],
        )
    )

    flow.append(Paragraph("What this report does", styles["h1"]))
    flow.append(
        Paragraph(
            "Every figure is pulled directly from filed 10-K data through the SEC EDGAR XBRL API, "
            "then cleaned, converted into ratios, described as trends, and scored. Nothing is "
            "hand-entered. The Financial Health Score is a weighted blend of five pillars, each "
            "metric scored 0 to 100 by linear interpolation between a published floor and ceiling. "
            "The full threshold table is in the methodology section at the end, so any score here "
            "can be recalculated or disputed.",
            styles["body"],
        )
    )
    flow.append(
        Paragraph(
            "The ranking below is deliberately not an investment view. It measures reported "
            "financial condition against fixed absolute thresholds, which is why capital-intensive "
            "and cyclical businesses score lower than asset-light software companies almost by "
            "construction. That bias is the single most important thing to understand before "
            "quoting any number in this report.",
            styles["body"],
        )
    )

    flow.append(Paragraph("League table", styles["h1"]))
    lw = [width * x for x in [0.035, 0.235, 0.135, 0.06, 0.095, 0.073, 0.073, 0.065, 0.073, 0.056]]
    flow.append(league_table(summary, lw))
    flow.append(Spacer(1, 8))
    flow.append(
        Paragraph(
            "Prof. = Profitability, Lev. = Leverage, Cash = Cash generation, Eff. = Efficiency. "
            "Pillar scores are 0 to 100. Bands: 80+ Strong, 65-79 Healthy, 50-64 Adequate, "
            "35-49 Weak, below 35 Distressed.",
            styles["small"],
        )
    )

    # Sector view, kept on one page so the table is never split mid-comparison.
    sector = (
        summary.groupby("sector")
        .agg(companies=("ticker", "count"), avg_score=("health_score", "mean"),
             avg_net_margin=("net_margin", "mean"), avg_de=("debt_to_equity", "mean"))
        .sort_values("avg_score", ascending=False)
        .reset_index()
    )
    srows = [["Sector", "Companies", "Avg score", "Avg net margin", "Avg D/E"]]
    for _, r in sector.iterrows():
        srows.append([r["sector"], str(int(r["companies"])), f"{r['avg_score']:.1f}",
                      fmt_pct(r["avg_net_margin"]), fmt_x(r["avg_de"])])
    flow.append(
        KeepTogether(
            [
                Paragraph("Sector averages", styles["h1"]),
                _table(srows, [width * x for x in [0.34, 0.14, 0.16, 0.20, 0.16]]),
                Paragraph(
                    "The spread across sectors is the evidence for the limitation noted above: the "
                    "same absolute thresholds flatter some business models and penalise others.",
                    styles["small"],
                ),
            ]
        )
    )

    flow.append(PageBreak())

    # Per-company detail, best first
    for ticker in summary["ticker"]:
        flow.extend(company_pages(ticker, results[ticker], styles, width))

    # Methodology
    flow.append(Paragraph("Methodology", styles["h1"]))
    flow.append(
        Paragraph(
            "Each metric is scored 0 to 100 by linear interpolation between a floor (scores 0) and "
            "a ceiling (scores 100), clamped at both ends. Where a lower value is better, the floor "
            "is set above the ceiling, which inverts the scale. Metric scores are combined into "
            "pillar scores by metric weight, and pillars into the overall score by pillar weight.",
            styles["body"],
        )
    )
    mrows = [["Pillar", "Pillar weight", "Metric", "Floor", "Ceiling", "Metric weight"]]
    for p in PILLARS:
        for i, m in enumerate(p.metrics):
            is_pct = abs(m.ceiling) <= 1 and abs(m.floor) <= 1
            f = f"{m.floor:.0%}" if is_pct else f"{m.floor:.2f}"
            c = f"{m.ceiling:.0%}" if is_pct else f"{m.ceiling:.2f}"
            mrows.append([p.name if i == 0 else "", f"{p.weight:.0%}" if i == 0 else "",
                          m.name.replace("_", " "), f, c, f"{m.weight:.0%}"])
    flow.append(_table(mrows, [width * x for x in [0.2, 0.13, 0.27, 0.13, 0.13, 0.14]], align_right_from=3))

    flow.append(Paragraph("Known limitations", styles["h2"]))
    for text in [
        "<b>Thresholds are absolute, not sector-relative.</b> A 25% net margin is exceptional for a "
        "grocer and ordinary for enterprise software. Peer-relative scoring is the next module.",
        "<b>ROE is distorted by buybacks.</b> Companies that have bought back stock aggressively show "
        "ROE inflated by a shrunken equity base rather than by better returns. Read ROE next to ROA.",
        "<b>EPS growth is excluded on purpose.</b> EDGAR restates only the roughly three years each "
        "10-K covers, so a ten-year EPS series mixes pre- and post-split bases. Revenue and net "
        "income growth are used instead because they are unaffected by splits.",
        "<b>Four of five pillars use the latest year only.</b> Growth uses the full-period CAGR. A "
        "single unusual year can therefore move the level-based pillars.",
        "<b>Unreported metrics are dropped, not guessed.</b> Where a company does not tag a line item, "
        "that metric is excluded and the remaining weights in its pillar are renormalized. Affected "
        "companies are flagged on their own page.",
        "<b>Financial-sector companies are excluded.</b> Banks and insurers do not report gross profit "
        "and their balance sheets are unclassified, so current ratio and working capital are undefined. "
        "They need a different model rather than a looser one.",
        "<b>No accounting-quality checks.</b> Restatements, one-off items and segment changes are not "
        "detected. The pipeline trusts what was filed.",
    ]:
        flow.append(Paragraph(f"&bull; {text}", styles["body"]))

    doc.build(flow)


def main(project_root: Path) -> None:
    data_dir, reports_dir = project_root / "data", project_root / "reports"
    reports_dir.mkdir(exist_ok=True)

    results, summary = analyse_all(data_dir)

    summary.to_csv(reports_dir / "company_scores.csv", index=False)

    long_rows = []
    for ticker, entry in results.items():
        r = entry["ratios"].copy()
        r.insert(0, "ticker", ticker)
        r.insert(1, "sector", entry["sector"])
        long_rows.append(r)
    pd.concat(long_rows).to_csv(reports_dir / "all_ratios_by_year.csv", index=False)

    pdf_path = reports_dir / "financial_health_report.pdf"
    build_pdf(results, summary, pdf_path)

    print(f"Analysed {len(results)} companies\n")
    print(summary[["rank", "ticker", "sector", "health_score", "rating"]].to_string(index=False))
    print(f"\nWrote:\n  {pdf_path}\n  {reports_dir/'company_scores.csv'}\n  {reports_dir/'all_ratios_by_year.csv'}")
