"""
Sanity tests for the forecast model and scoring: things that must stay true
however the settings are tuned.
"""

import math

import numpy as np
import pandas as pd

import forecast_model as m
import scoring


def chance(polls, fund=None, market=None, t=12):
    race = [{"key": "X", "region_state": "PA", "polls": polls, "fundamentals": fund,
             "market": None if market is None else {"p": market, "sources": {}}}]
    return m.forecast_races(race, t, 4.0)[0][0]["p_dem"]


def test_even_race_is_a_coin_flip():
    assert abs(chance(0.0) - 0.5) < 0.02


def test_bigger_lead_means_better_chance():
    ps = [chance(x) for x in (-6, -3, 0, 3, 6)]
    assert all(a < b for a, b in zip(ps, ps[1:]))


def test_each_ingredient_pushes_the_right_way():
    assert chance(0.0, market=0.8) > chance(0.0, market=0.5) > chance(0.0, market=0.2)
    assert chance(0.0, fund=5.0) > chance(0.0)


def test_weights_add_up_and_shift_toward_polls():
    for t in (0, 12, 100, 750):
        assert math.isclose(sum(m.base_weights(t).values()), 1.0)
    assert m.base_weights(12)["polls"] > m.base_weights(100)["polls"] > m.base_weights(750)["polls"]
    # A missing ingredient's weight goes to the others
    w = m.weights_for(12, {"polls": True, "markets": False, "fundamentals": True})
    assert w["markets"] == 0 and math.isclose(w["polls"] + w["fundamentals"], 1.0)


def test_uncertainty_grows_with_time():
    assert m.total_sigma(m.sigmas(0, 4.0)) < m.total_sigma(m.sigmas(100, 4.0)) < m.total_sigma(m.sigmas(700, 4.0))


def test_market_conversion_is_symmetric():
    assert math.isclose(m.market_margin(0.7, 6.0), -m.market_margin(0.3, 6.0))
    assert m.market_margin(0.5, 6.0) == 0
    assert m.market_margin(0.9999, 6.0) == m.market_margin(m.P_CLAMP[1], 6.0)    # extremes are clamped


def test_simulation_is_reproducible():
    assert chance(1.5) == chance(1.5)


def test_scoring_basics():
    perfect = pd.DataFrame({"p_dem": [1.0, 0.0], "dem_won": [True, False]})
    assert scoring.score(perfect)["brier"] == 0
    coin = pd.DataFrame({"p_dem": [0.5, 0.5], "dem_won": [True, False]})
    assert scoring.score(coin)["brier"] == 0.25
    # The error-shape density integrates to about 1
    x = np.linspace(-60, 60, 20001)
    assert abs(np.exp(scoring.t_logpdf(x, 5.0)).sum() * (x[1] - x[0]) - 1) < 1e-3
