"""Statistical guardrails for backtest reports, not parameter optimisation."""
from dataclasses import asdict, dataclass
from typing import Iterable

import numpy as np


@dataclass(frozen=True)
class BootstrapSummary:
    trade_count: int
    paths: int
    seed: int
    mean_total_r: float
    p05_total_r: float
    probability_positive_r: float
    sufficient_sample: bool


def bootstrap_trade_outcomes(
    outcomes_r: Iterable[float], paths: int = 5_000, seed: int = 26081901,
    min_trades: int = 100,
) -> BootstrapSummary:
    """Resample realised R outcomes to expose sample-size fragility.

    This does not prove independence or invent an edge. It simply reports the
    dispersion implied by the observed trade list and refuses to call fewer
    than ``min_trades`` statistically sufficient.
    """
    values = np.asarray(list(outcomes_r), dtype=float)
    if len(values) == 0:
        return BootstrapSummary(0, paths, seed, 0.0, 0.0, 0.0, False)
    rng = np.random.default_rng(seed)
    sampled = rng.choice(values, size=(paths, len(values)), replace=True).sum(axis=1)
    return BootstrapSummary(
        trade_count=int(len(values)), paths=paths, seed=seed,
        mean_total_r=round(float(sampled.mean()), 4),
        p05_total_r=round(float(np.quantile(sampled, 0.05)), 4),
        probability_positive_r=round(float((sampled > 0).mean()), 4),
        sufficient_sample=len(values) >= min_trades,
    )
