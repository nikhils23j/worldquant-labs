from dataclasses import replace
import math
import numpy as np
import pytest
from risklab import Contract, Model, price
from risklab.analytics import analytic_price, analytic_greeks
from risklab.qmc import bridge_plan, brownian, NormalSource, prefix_estimates
from risklab.greeks import greek_estimators, digital_delta
from risklab.barrier import barrier_mc, down_and_out_call
from risklab.heston import HestonParams, heston_call_mc
from risklab.validation import quantlib_european_greeks, quantlib_barrier, quantlib_heston


@pytest.mark.parametrize('steps', [1, 2, 3, 12, 64, 252])
def test_bridge_plan_fills_every_date_once(steps):
    idx = [p[0] for p in bridge_plan(steps)]
    assert sorted(idx + [steps - 1]) == list(range(steps))
    for mid, left, right, wl, wr, sd in bridge_plan(steps):
        assert left < mid < right and wl + wr == pytest.approx(1) and sd > 0


@pytest.mark.parametrize('bridge', [False, True])
def test_brownian_covariance(bridge):
    z = np.random.default_rng(0).standard_normal((200000, 12))
    w = brownian(z, 2.0, bridge)
    t = 2.0 * np.arange(1, 13) / 12
    np.testing.assert_allclose(np.cov(w.T), np.minimum.outer(t, t), atol=0.03)


def test_bridge_first_coordinate_is_terminal_value():
    z = np.random.default_rng(1).standard_normal((5, 16))
    w = brownian(z, 4.0, True)
    np.testing.assert_allclose(w[:, -1], 2.0 * z[:, 0])


def test_sobol_requires_power_of_two_and_is_sequential():
    src = NormalSource('sobol', 8, 3)
    with pytest.raises(ValueError):
        src.draw(100)
    a = NormalSource('sobol', 8, 3); b = NormalSource('sobol', 8, 3)
    np.testing.assert_allclose(np.vstack([a.draw(64), a.draw(64)]), b.draw(128))


def test_rqmc_bridge_is_accurate_on_geometric_asian():
    c, m = Contract('geometric', fixings=64), Model()
    truth = analytic_price(c, m)
    errs = [prefix_estimates(c, m, 'sobol', True, s, [12])[4096] - truth for s in range(6)]
    plain = [prefix_estimates(c, m, 'prng', False, s, [12])[4096] - truth for s in range(6)]
    assert np.sqrt(np.mean(np.square(errs))) < 0.05
    assert np.sqrt(np.mean(np.square(errs))) < np.sqrt(np.mean(np.square(plain)))


def test_antithetic_control_estimator():
    c, m = Contract(fixings=32), Model()
    plain = price(c, m, 'plain', 40000, 4)
    both = price(c, m, 'antithetic_control', 30000, 4, pilot_paths=10000)
    assert both['independent_units'] == 15000 and both['total_generated_paths'] == 40000
    assert both['standard_error'] < plain['standard_error'] / 5
    assert abs(both['estimate'] - plain['estimate']) < 5 * math.hypot(both['standard_error'], plain['standard_error'])
    with pytest.raises(ValueError):
        price(c, m, 'antithetic_control', 1000, pilot_paths=101)
    with pytest.raises(ValueError):
        price(Contract('european'), m, 'antithetic_control', 1000)


def test_pathwise_and_lr_greeks_match_analytic_and_quantlib():
    c, m = Contract('european'), Model()
    truth = analytic_greeks(c, m)
    ql = quantlib_european_greeks(c, m)
    assert ql['delta'] == pytest.approx(truth['delta'], abs=1e-12)
    assert ql['vega_per_vol_point'] == pytest.approx(truth['vega_per_vol_point'], abs=1e-12)
    assert ql['price'] == pytest.approx(analytic_price(c, m), abs=1e-10)
    r = greek_estimators(c, m, 100000, 3)
    for est in ('pathwise', 'lr', 'crn_bump'):
        assert abs(r[f'{est}_delta']['estimate'] - truth['delta']) < 5 * r[f'{est}_delta']['standard_error']
        assert abs(r[f'{est}_vega']['estimate'] - truth['vega_per_vol_point']) < 5 * r[f'{est}_vega']['standard_error'] + 1e-3
    assert r['lr_delta']['standard_error'] > r['pathwise_delta']['standard_error']


def test_lr_vega_variance_grows_with_steps():
    m = Model()
    one = greek_estimators(Contract('asian', fixings=4), m, 20000, 1)
    many = greek_estimators(Contract('asian', fixings=128), m, 20000, 1)
    ratio = lambda r: r['lr_vega']['standard_error'] / r['pathwise_vega']['standard_error']
    assert ratio(many) > 2 * ratio(one)


def test_digital_pathwise_fails_lr_works():
    d = digital_delta(Model(), paths=100000, seed=2)
    assert d['pathwise']['estimate'] == 0.0
    assert abs(d['likelihood_ratio']['estimate'] - d['analytic']) < 5 * d['likelihood_ratio']['standard_error']


@pytest.mark.parametrize('spot,strike,barrier,vol', [(100, 100, 90, .3), (100, 90, 80, .2), (105, 110, 95, .4)])
def test_down_and_out_closed_form_matches_quantlib(spot, strike, barrier, vol):
    assert down_and_out_call(spot, strike, barrier, 1.0, .03, vol) == pytest.approx(
        quantlib_barrier(spot, strike, barrier, 1.0, .03, vol), abs=1e-10)


def test_barrier_bridge_correction_removes_monitoring_bias():
    truth = quantlib_barrier(100, 100, 90, 1.0, .03, .3)
    fixed = barrier_mc(100, 100, 90, 1.0, .03, .3, steps=12, paths=60000, seed=5, correction=True)
    naive = barrier_mc(100, 100, 90, 1.0, .03, .3, steps=12, paths=60000, seed=5, correction=False)
    assert abs(fixed['estimate'] - truth) < 5 * fixed['standard_error']
    assert naive['estimate'] - truth > 10 * naive['standard_error']
    up = barrier_mc(100, 100, 130, 1.0, .03, .3, 'up', steps=12, paths=60000, seed=6)
    assert abs(up['estimate'] - quantlib_barrier(100, 100, 130, 1.0, .03, .3, 'up')) < 5 * up['standard_error']
    with pytest.raises(ValueError):
        barrier_mc(100, 100, 110, 1.0, .03, .3, 'down')


def test_heston_matches_quantlib_and_reduces_to_black_scholes():
    p = HestonParams()
    r = heston_call_mc(100, 100, 1.0, .03, p, steps=52, paths=40000, seed=1)
    assert abs(r['estimate'] - quantlib_heston(100, 100, 1.0, .03, p)) < 5 * r['standard_error']
    flat = HestonParams(v0=.09, kappa=1.0, theta=.09, xi=1e-8, rho=0.0)
    bs = analytic_price(Contract('european'), Model(volatility=.3))
    assert quantlib_heston(100, 100, 1.0, .03, flat) == pytest.approx(bs, abs=1e-4)
    r = heston_call_mc(100, 100, 1.0, .03, flat, steps=4, paths=40000, seed=2)
    assert abs(r['estimate'] - bs) < 5 * r['standard_error']
    assert not HestonParams(kappa=1.0, xi=.9).feller and HestonParams().feller
