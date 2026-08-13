"""Financial Health Score: five weighted pillars, each scored 0-100 on published thresholds.

Every metric is scored by linear interpolation between a `floor` (scores 0) and a
`ceiling` (scores 100). Thresholds are documented in README.md so the score can be
challenged and re-calibrated rather than taken on faith.
"""

from dataclasses import dataclass, field

import pandas as pd

from src.trends import cagr


@dataclass(frozen=True)
class MetricSpec:
    """One scored metric: where the value comes from and how it maps onto 0-100."""

    name: str
    floor: float
    ceiling: float
    weight: float


@dataclass(frozen=True)
class PillarSpec:
    name: str
    weight: float
    metrics: list[MetricSpec] = field(default_factory=list)


PILLARS = [
    PillarSpec(
        "Profitability",
        0.25,
        [
            MetricSpec("net_margin", 0.00, 0.25, 0.40),
            MetricSpec("ebitda_margin", 0.05, 0.35, 0.30),
            MetricSpec("roe", 0.05, 0.40, 0.30),
        ],
    ),
    PillarSpec(
        "Growth",
        0.20,
        [
            # EPS growth is deliberately excluded. EDGAR restates only the ~3 years each
            # 10-K covers, so a 10-year EPS series mixes pre- and post-split bases and its
            # CAGR is an artefact of share splits rather than performance. Revenue and net
            # income are split-immune.
            MetricSpec("revenue_cagr", -0.05, 0.15, 0.50),
            MetricSpec("net_income_cagr", -0.05, 0.20, 0.50),
        ],
    ),
    PillarSpec(
        "Leverage",
        0.20,
        [
            # Inverted: lower debt-to-equity scores higher.
            MetricSpec("debt_to_equity", 3.00, 0.20, 0.40),
            MetricSpec("interest_coverage", 3.00, 30.00, 0.35),
            MetricSpec("current_ratio", 0.70, 2.00, 0.25),
        ],
    ),
    PillarSpec(
        "Cash generation",
        0.20,
        [
            MetricSpec("fcf_margin", 0.00, 0.25, 0.50),
            MetricSpec("cfo_to_net_income", 0.80, 1.50, 0.50),
        ],
    ),
    PillarSpec(
        "Efficiency",
        0.15,
        [
            MetricSpec("asset_turnover", 0.30, 1.20, 0.50),
            MetricSpec("roa", 0.02, 0.20, 0.50),
        ],
    ),
]


def score_metric(value: float, spec: MetricSpec) -> float:
    """Map a raw value onto 0-100. A floor above the ceiling means lower is better."""
    if pd.isna(value):
        return float("nan")

    span = spec.ceiling - spec.floor
    raw = (value - spec.floor) / span
    return max(0.0, min(1.0, raw)) * 100


def build_metric_inputs(ratios_df: pd.DataFrame) -> dict[str, float]:
    """Assemble the scored inputs: latest-year levels plus full-period growth rates."""
    latest = ratios_df.iloc[-1]
    n_years = int(ratios_df["fiscal_year"].iloc[-1] - ratios_df["fiscal_year"].iloc[0])

    inputs = {
        "net_margin": latest["net_margin"],
        "ebitda_margin": latest["ebitda_margin"],
        "roe": latest["roe"],
        "debt_to_equity": latest["debt_to_equity"],
        "interest_coverage": latest["interest_coverage"],
        "current_ratio": latest["current_ratio"],
        "fcf_margin": latest["fcf_margin"],
        "cfo_to_net_income": latest["cfo_to_net_income"],
        "asset_turnover": latest["asset_turnover"],
        "roa": latest["roa"],
    }

    for metric, column in [
        ("revenue_cagr", "revenue"),
        ("net_income_cagr", "net_income"),
    ]:
        inputs[metric] = cagr(
            ratios_df[column].iloc[0], ratios_df[column].iloc[-1], n_years
        )

    return inputs


def score_pillars(ratios_df: pd.DataFrame) -> tuple[dict[str, float], list[str]]:
    """Score each pillar 0-100 as the weighted average of its available metric scores.

    Metrics that cannot be computed (an unreported line item, or a ratio whose denominator
    is missing) are dropped and the remaining weights within that pillar are renormalized.
    Scoring a data gap as either 0 or 100 would be a fabricated result; the dropped metrics
    are returned so the report can disclose them.
    """
    inputs = build_metric_inputs(ratios_df)
    pillar_scores: dict[str, float] = {}
    unavailable: list[str] = []

    for pillar in PILLARS:
        scored = []
        for metric in pillar.metrics:
            value = inputs.get(metric.name, float("nan"))
            score = score_metric(value, metric)
            if pd.isna(score):
                unavailable.append(metric.name)
            else:
                scored.append((score, metric.weight))

        if not scored:
            pillar_scores[pillar.name] = float("nan")
            continue

        total_weight = sum(w for _, w in scored)
        pillar_scores[pillar.name] = round(
            sum(s * w for s, w in scored) / total_weight, 1
        )

    return pillar_scores, unavailable


def rating_band(score: float) -> str:
    if score >= 80:
        return "Strong"
    if score >= 65:
        return "Healthy"
    if score >= 50:
        return "Adequate"
    if score >= 35:
        return "Weak"
    return "Distressed"


def financial_health_score(ratios_df: pd.DataFrame) -> dict:
    """Produce the overall score, its band, and the pillar decomposition behind it."""
    pillar_scores, unavailable = score_pillars(ratios_df)

    available = [p for p in PILLARS if not pd.isna(pillar_scores[p.name])]
    total_weight = sum(p.weight for p in available)
    overall = sum(pillar_scores[p.name] * p.weight for p in available) / total_weight

    return {
        "overall": round(overall, 1),
        "rating": rating_band(overall),
        "pillars": pillar_scores,
        "unavailable_metrics": unavailable,
    }
