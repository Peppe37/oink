"""Backtests and Monte Carlo simulations, computed here (never by the AI).

- `backtest`: what a plan (initial capital + a contribution every month) would have done on
  real monthly prices.
- `monte_carlo`: thousands of possible futures. Monthly returns are drawn from the asset's
  own history in blocks of 12 months (block bootstrap: keeps crashes and rallies together),
  or from a normal distribution with the historical mean and volatility (`gbm`). Results
  are percentiles per year, the chance of ending below what was put in, and of reaching a
  target. Works for a single asset, a weighted portfolio, a PAC or a lump sum.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any

Series = list[tuple[date, Decimal]]


class SimulationError(ValueError):
    pass


def monthly_returns(series: Series) -> list[float]:
    prices = [float(p) for _, p in series]
    return [b / a - 1 for a, b in itertools.pairwise(prices) if a > 0]


def portfolio_returns(histories: list[Series], weights: list[float]) -> list[float]:
    """Monthly returns of a portfolio rebalanced monthly, on the months all assets share."""
    if not histories or len(histories) != len(weights):
        raise SimulationError("weights_mismatch")
    total = sum(weights)
    if total <= 0:
        raise SimulationError("weights_mismatch")
    maps = [dict(h) for h in histories]
    months = sorted(set.intersection(*(set(m) for m in maps)))
    if len(months) < 13:
        raise SimulationError("history_too_short")
    out = []
    for a, b in itertools.pairwise(months):
        r = sum(
            w / total * (float(m[b]) / float(m[a]) - 1)
            for m, w in zip(maps, weights, strict=True)
            if float(m[a]) > 0
        )
        out.append(r)
    return out


def backtest(series: Series, monthly: float, initial: float = 0.0) -> dict[str, Any]:
    if len(series) < 2:
        raise SimulationError("history_too_short")
    units, invested, peak, worst = 0.0, 0.0, 0.0, 0.0
    points: list[dict[str, Any]] = []
    for i, (day, price) in enumerate(series):
        p = float(price)
        add = (initial if i == 0 else 0.0) + monthly
        if p > 0 and add:
            units += add / p
            invested += add
        value = units * p
        peak = max(peak, value)
        if peak:
            worst = min(worst, value / peak - 1)
        points.append(
            {"day": day.isoformat(), "invested": round(invested, 2), "value": round(value, 2)}
        )
    final = float(points[-1]["value"])
    years = max((series[-1][0] - series[0][0]).days / 365.25, 1 / 12)
    return {
        "points": points,
        "invested": round(invested, 2),
        "value": round(final, 2),
        "gain": round(final - invested, 2),
        "return": round(final / invested - 1, 4) if invested else None,
        "asset_cagr": round((float(series[-1][1]) / float(series[0][1])) ** (1 / years) - 1, 4),
        "max_drawdown": round(worst, 4),
        "years": round(years, 1),
    }


@dataclass
class MonteCarlo:
    returns: list[float]
    years: int
    initial: float = 0.0
    monthly: float = 0.0
    paths: int = 2000
    method: str = "bootstrap"  # bootstrap | gbm
    inflation: float = 0.0  # yearly, to show results in today's money
    target: float | None = None
    seed: int | None = 7

    def run(self) -> dict[str, Any]:
        if len(self.returns) < 24:
            raise SimulationError("history_too_short")
        if not 1 <= self.years <= 60 or not 100 <= self.paths <= 20000:
            raise SimulationError("bad_parameters")
        rng = random.Random(self.seed)
        months = self.years * 12
        mu, sigma = mean(self.returns), pstdev(self.returns)
        log_mu = math.log1p(mu) - sigma**2 / 2
        monthly_infl = (1 + self.inflation) ** (1 / 12) - 1
        by_year: list[list[float]] = [[] for _ in range(self.years + 1)]
        finals = []
        block = 12
        n = len(self.returns)
        for _ in range(self.paths):
            value = self.initial
            by_year[0].append(value)
            m = 0
            while m < months:
                if self.method == "gbm":
                    draws = [math.expm1(rng.gauss(log_mu, sigma))]
                else:
                    start = rng.randrange(0, max(1, n - block))
                    draws = self.returns[start : start + block]
                for r in draws:
                    if m >= months:
                        break
                    value = value * (1 + r) + self.monthly
                    m += 1
                    if m % 12 == 0:
                        real = value / ((1 + monthly_infl) ** m)
                        by_year[m // 12].append(real)
            finals.append(by_year[self.years][-1])
        invested = self.initial + self.monthly * months
        pct = (5, 25, 50, 75, 95)
        bands = []
        for year, values in enumerate(by_year):
            values.sort()
            bands.append(
                {
                    "year": year,
                    **{
                        f"p{p}": round(values[min(len(values) - 1, int(len(values) * p / 100))], 2)
                        for p in pct
                    },
                }
            )
        finals.sort()
        out: dict[str, Any] = {
            "bands": bands,
            "invested": round(invested, 2),
            "median": bands[-1]["p50"],
            "p5": bands[-1]["p5"],
            "p95": bands[-1]["p95"],
            "prob_loss": round(sum(1 for v in finals if v < invested) / len(finals), 3),
            "monthly_mean": round(mu, 5),
            "monthly_vol": round(sigma, 5),
            "yearly_vol": round(sigma * math.sqrt(12), 4),
            "paths": self.paths,
            "method": self.method,
        }
        if self.target:
            out["prob_target"] = round(sum(1 for v in finals if v >= self.target) / len(finals), 3)
        return out
