"""Sector-relative scoring: the thing the absolute score structurally cannot see.

`financial_health.py` marks every company against fixed, published thresholds. That is the
right way to ask "is this a good business" and the wrong way to ask "is this a good business
for its industry". The difference is not academic, and the 20-company ranking already exposes
it: Walmart's net margin is about 3%, which scores near zero against a threshold calibrated on
absolute profitability and is entirely ordinary for grocery retail. The absolute score ranked
it 18th of 20 and called it Weak.

This module scores the same metrics, with the same pillars and the same weights, by percentile
against sector peers instead of against thresholds. Neither reading replaces the other. A
company can rank well inside a structurally poor sector, and a company in a rich sector can
score well absolutely while lagging every peer it actually competes with. The gap between the
two numbers is the finding, which is why `compare()` returns both rather than one blended
score that would hide the disagreement.

Peers are the 20-company universe in `universe.py`, whose cached statements live in `data/`
and are checked into the repository, so peer scoring needs no network call. A company is
always ranked against peers *other than itself*, so an in-universe company and a typed-in
ticker are scored the same way rather than one of them quietly getting a free rank.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from fsi.data_cleaning import clean_financials
from fsi.data_loader import load_financials
from fsi.financial_health import PILLARS, MetricSpec, build_metric_inputs, rating_band
from fsi.ratios import calculate_ratios
from fsi.universe import UNIVERSE

DATA_DIR = Path(__file__).resolve().parents[1] / "data"

# A percentile needs enough peers to mean anything. Module 2's comparables uses MIN_PEERS = 2,
# which is sound there: a median of two multiples is still a central estimate. A *rank* over
# two peers is not, because it can only land on a handful of values and one unusual peer
# decides the answer outright. Three is where a percentile starts carrying information, and
# sectors below it are reported as un-rankable rather than ranked badly.
MIN_PEERS = 3

# yfinance uses its own sector vocabulary; the universe uses GICS-style names. Only the two
# that actually differ are mapped, and anything unrecognised resolves to None rather than
# being forced into the nearest-looking bucket.
_SECTOR_ALIASES = {
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
}


def higher_is_better(spec: MetricSpec) -> bool:
    """Direction is read off the existing threshold spec rather than a second table that could
    drift out of step with it: `score_metric` already encodes "lower is better" as a floor
    above the ceiling, so debt-to-equity needs no special case anywhere in this module."""
    return spec.ceiling > spec.floor


@dataclass
class PeerGroup:
    """The companies a target is ranked against, and their metric values."""

    sector: str
    tickers: tuple[str, ...]
    metrics: pd.DataFrame

    @property
    def size(self) -> int:
        return len(self.tickers)

    @property
    def usable(self) -> bool:
        return self.size >= MIN_PEERS


def load_universe_metrics(data_dir: Path | None = None) -> pd.DataFrame:
    """Every universe company's scored metric inputs, computed by exactly the same chain the
    absolute score uses (clean, ratios, `build_metric_inputs`), so the two readings can never
    disagree about the underlying numbers, only about how to judge them.

    A company whose cached file is missing or unusable is skipped rather than filled in, and
    simply does not appear as a peer.
    """
    directory = Path(data_dir) if data_dir is not None else DATA_DIR
    rows = []

    for ticker, (name, sector) in UNIVERSE.items():
        path = directory / f"{ticker}_financials.csv"
        try:
            ratios = calculate_ratios(clean_financials(load_financials(path)))
            inputs = build_metric_inputs(ratios)
        except Exception:  # noqa: BLE001 - a bad peer must not stop the comparison
            continue
        rows.append({"ticker": ticker, "name": name, "sector": sector, **inputs})

    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).set_index("ticker")


def resolve_sector(ticker: str, allow_network: bool = True) -> str | None:
    """The universe's hand-checked label first, then yfinance, then nothing.

    The hand label wins because it was verified against the filings when the universe was
    built; a provider's classification is a fallback, not an override. An unrecognised sector
    returns None, which downstream reads as "no peer score", never as a guess.
    """
    upper = ticker.upper()
    if upper in UNIVERSE:
        return UNIVERSE[upper][1]

    if not allow_network:
        return None

    try:
        import yfinance as yf

        raw = yf.Ticker(upper).info.get("sector")
    except Exception:  # noqa: BLE001 - no sector is a valid outcome, not an error
        return None

    if not raw:
        return None
    return _SECTOR_ALIASES.get(raw, raw)


def peer_group(sector: str, exclude: str | None = None,
              universe_metrics: pd.DataFrame | None = None,
              data_dir: Path | None = None) -> PeerGroup:
    """The sector's companies, minus the target itself."""
    frame = universe_metrics if universe_metrics is not None else load_universe_metrics(data_dir)
    if frame.empty:
        return PeerGroup(sector=sector, tickers=(), metrics=frame)

    members = frame[frame["sector"] == sector]
    if exclude:
        members = members.drop(index=exclude.upper(), errors="ignore")

    return PeerGroup(sector=sector, tickers=tuple(members.index), metrics=members)


def percentile_rank(value: float, peer_values, higher_is_better_metric: bool = True) -> float:
    """Where `value` sits within `peer_values`, on 0 to 100.

    Ties are mid-ranked rather than resolved to the optimistic or pessimistic end: a company
    level with two of four peers should read as the middle of that cluster, not the top of it.
    For a metric where lower is better the scale is inverted, so 100 always means "best in
    the peer group" regardless of the metric's direction.
    """
    if pd.isna(value):
        return float("nan")

    clean = [float(v) for v in peer_values if pd.notna(v)]
    if not clean:
        return float("nan")

    below = sum(1 for v in clean if v < value)
    equal = sum(1 for v in clean if v == value)
    pct = (below + 0.5 * equal) / len(clean) * 100.0
    return pct if higher_is_better_metric else 100.0 - pct


def score_pillars_relative(metrics: dict[str, float],
                          group: PeerGroup) -> tuple[dict[str, float], list[str]]:
    """The same pillars and the same weights as the absolute score, with percentile ranks in
    place of threshold scores.

    A metric is dropped when the target cannot report it, or when fewer than `MIN_PEERS` peers
    report it, and the remaining weights inside that pillar are renormalized. That is the same
    discipline `score_pillars` already applies to missing data: a gap is disclosed, never
    scored as a zero or a hundred.
    """
    pillar_scores: dict[str, float] = {}
    unavailable: list[str] = []

    for pillar in PILLARS:
        scored: list[tuple[float, float]] = []

        for spec in pillar.metrics:
            value = metrics.get(spec.name, float("nan"))
            peer_values = (group.metrics[spec.name].tolist()
                          if spec.name in group.metrics.columns else [])
            usable_peers = [v for v in peer_values if pd.notna(v)]

            if pd.isna(value) or len(usable_peers) < MIN_PEERS:
                unavailable.append(spec.name)
                continue

            rank = percentile_rank(value, usable_peers, higher_is_better(spec))
            if pd.isna(rank):
                unavailable.append(spec.name)
            else:
                scored.append((rank, spec.weight))

        if not scored:
            pillar_scores[pillar.name] = float("nan")
            continue

        total_weight = sum(w for _, w in scored)
        pillar_scores[pillar.name] = round(sum(s * w for s, w in scored) / total_weight, 1)

    return pillar_scores, unavailable


def peer_relative_score(ratios_df: pd.DataFrame, sector: str | None,
                       exclude_ticker: str | None = None,
                       universe_metrics: pd.DataFrame | None = None,
                       data_dir: Path | None = None) -> dict:
    """Score one company against its sector. Same output shape as `financial_health_score`,
    plus the peer group used and why, so a reader can see who the company was measured against
    rather than taking a percentile on trust.

    Returns `usable: False` with a stated reason, not a number, when the sector is unknown or
    too thin to rank in.
    """
    if not sector:
        return {"usable": False, "overall": float("nan"), "rating": None, "pillars": {},
                "unavailable_metrics": [], "sector": None, "peers": (), "peer_count": 0,
                "note": "No sector could be resolved for this company, so there is no peer "
                        "group to rank it against."}

    group = peer_group(sector, exclude=exclude_ticker,
                      universe_metrics=universe_metrics, data_dir=data_dir)

    if not group.usable:
        return {"usable": False, "overall": float("nan"), "rating": None, "pillars": {},
                "unavailable_metrics": [], "sector": sector, "peers": group.tickers,
                "peer_count": group.size,
                "note": f"{sector} has {group.size} peer(s) in the universe and a percentile "
                        f"needs at least {MIN_PEERS}. Ranking against this few would be noise "
                        "presented as a score, so no peer reading is given."}

    metrics = build_metric_inputs(ratios_df)
    pillars, unavailable = score_pillars_relative(metrics, group)

    available = [p for p in PILLARS if not pd.isna(pillars[p.name])]
    if not available:
        return {"usable": False, "overall": float("nan"), "rating": None, "pillars": pillars,
                "unavailable_metrics": unavailable, "sector": sector, "peers": group.tickers,
                "peer_count": group.size,
                "note": "No pillar had enough comparable data to rank."}

    total_weight = sum(p.weight for p in available)
    overall = sum(pillars[p.name] * p.weight for p in available) / total_weight

    return {"usable": True, "overall": round(overall, 1), "rating": rating_band(overall),
            "pillars": pillars, "unavailable_metrics": unavailable, "sector": sector,
            "peers": group.tickers, "peer_count": group.size, "note": ""}


@dataclass
class ScoreComparison:
    """Both readings side by side. Deliberately not averaged: a 45 that is really a 78 for its
    sector is a different statement from a 61, and averaging them would destroy exactly the
    information this comparison exists to surface."""

    absolute: dict
    relative: dict

    @property
    def usable(self) -> bool:
        return bool(self.relative.get("usable"))

    @property
    def gap(self) -> float:
        if not self.usable:
            return float("nan")
        return round(self.relative["overall"] - self.absolute["overall"], 1)

    def read(self) -> str:
        """What the two scores together say, in the language an analyst would use."""
        absolute_line = (f"{self.absolute['overall']:.1f}/100 ({self.absolute['rating']}) "
                        "against fixed thresholds")

        if not self.usable:
            return f"{absolute_line}. {self.relative.get('note', '')}".strip()

        peers = ", ".join(self.relative["peers"])
        relative_line = (f"{self.relative['overall']:.1f}/100 ({self.relative['rating']}) "
                        f"against {self.relative['peer_count']} {self.relative['sector']} "
                        f"peers ({peers})")
        gap = self.gap

        if gap >= 15:
            verdict = ("The absolute score is largely measuring the sector's economics rather "
                       "than the company: it holds up well against the businesses it actually "
                       "competes with.")
        elif gap <= -15:
            verdict = ("It sits in a sector whose economics flatter the absolute score, and "
                       "lags the companies it actually competes with.")
        else:
            verdict = ("Both readings broadly agree, so the absolute score is not being "
                       "driven by sector economics here.")

        return f"{absolute_line}, {relative_line}. Gap: {gap:+.1f} points. {verdict}"


def compare(ratios_df: pd.DataFrame, absolute: dict, sector: str | None,
           exclude_ticker: str | None = None,
           universe_metrics: pd.DataFrame | None = None,
           data_dir: Path | None = None) -> ScoreComparison:
    """The absolute score already computed, alongside the peer score for the same company."""
    relative = peer_relative_score(ratios_df, sector, exclude_ticker=exclude_ticker,
                                  universe_metrics=universe_metrics, data_dir=data_dir)
    return ScoreComparison(absolute=absolute, relative=relative)


def rank_universe(universe_metrics: pd.DataFrame | None = None,
                 data_dir: Path | None = None) -> pd.DataFrame:
    """Every universe company under both readings, for the comparison table in the report.

    Each company is ranked against its sector peers excluding itself, which is what makes the
    two columns comparable across companies rather than each one grading its own homework.
    """
    frame = universe_metrics if universe_metrics is not None else load_universe_metrics(data_dir)
    if frame.empty:
        return pd.DataFrame()

    directory = Path(data_dir) if data_dir is not None else DATA_DIR
    rows = []

    for ticker, row in frame.iterrows():
        try:
            ratios = calculate_ratios(
                clean_financials(load_financials(directory / f"{ticker}_financials.csv")))
        except Exception:  # noqa: BLE001
            continue

        from fsi.financial_health import financial_health_score

        absolute = financial_health_score(ratios)
        relative = peer_relative_score(ratios, row["sector"], exclude_ticker=ticker,
                                      universe_metrics=frame, data_dir=directory)
        rows.append({
            "ticker": ticker, "name": row["name"], "sector": row["sector"],
            "absolute": absolute["overall"], "absolute_rating": absolute["rating"],
            "relative": relative["overall"] if relative["usable"] else float("nan"),
            "relative_rating": relative["rating"] if relative["usable"] else None,
            "gap": (round(relative["overall"] - absolute["overall"], 1)
                   if relative["usable"] else float("nan")),
            "peer_count": relative["peer_count"],
        })

    return pd.DataFrame(rows).sort_values("absolute", ascending=False).reset_index(drop=True)
