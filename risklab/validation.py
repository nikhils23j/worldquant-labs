"""Independent QuantLib reference, with matching Actual/365 observation dates."""
from dataclasses import replace
import math
from .analytics import analytic_price
from .estimators import price

def quantlib_reference(c, m, paths=200000, seed=71):
    import QuantLib as ql
    # Calendar dates cannot express all intended equal model-time steps;
    # rescale the model below to preserve the exact GBM law at every fixing.
    if c.maturity not in (.5, 1.0) or c.fixings != 252:
        raise ValueError("Reference wrapper supports the recorded .5/1-year, 252-fixing grid")
    today = ql.Date(1, 1, 2026)
    old = ql.Settings.instance().evaluationDate
    ql.Settings.instance().evaluationDate = today
    try:
        # Actual365Fixed with a rescaled horizon/model makes exact GBM times:
        # fixing dates every 1 or 2 days, effective calendar T=252/365 or 504/365.
        step = 1 if c.maturity == .5 else 2
        cal_t = c.fixings*step/365
        scale = c.maturity/cal_t
        dc = ql.Actual365Fixed()
        spot = ql.QuoteHandle(ql.SimpleQuote(m.spot))
        rate = ql.YieldTermStructureHandle(ql.FlatForward(today, m.rate*scale, dc))
        div = ql.YieldTermStructureHandle(ql.FlatForward(today, m.dividend*scale, dc))
        vol = ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today, ql.NullCalendar(), m.volatility*math.sqrt(scale), dc))
        process = ql.BlackScholesMertonProcess(spot, div, rate, vol)
        dates = [today+i*step for i in range(1, c.fixings+1)]
        payoff = ql.PlainVanillaPayoff(ql.Option.Call if c.call else ql.Option.Put, c.strike)
        exercise = ql.EuropeanExercise(dates[-1])
        if c.kind == "european":
            option = ql.VanillaOption(payoff, exercise)
            engine = ql.AnalyticEuropeanEngine(process)
        else:
            average = ql.Average.Geometric if c.kind == "geometric" else ql.Average.Arithmetic
            option = ql.DiscreteAveragingAsianOption(average, 1.0 if c.kind == "geometric" else 0.0, 0, dates, payoff, exercise)
            engine = (ql.AnalyticDiscreteGeometricAveragePriceAsianEngine(process) if c.kind == "geometric"
                      else ql.MCDiscreteArithmeticAPEngine(process, "pseudorandom", requiredSamples=paths, seed=seed, controlVariate=True))
        option.setPricingEngine(engine)
        value = option.NPV()
        se = option.errorEstimate() if c.kind == "asian" else 0.
        return {"estimate": value, "standard_error": se, "QuantLib_version": ql.__version__,
                "calendar_time": cal_t, "rate_time_scale": scale,
                "convention": "Exact equivalent GBM via rescaled r,q,sigma on equally spaced calendar dates"}
    finally:
        ql.Settings.instance().evaluationDate = old


def _ql_setup(maturity, rate, dividend):
    import QuantLib as ql
    days = maturity * 365
    if abs(days - round(days)) > 1e-12:
        raise ValueError("maturity must be a whole number of days on Actual/365 Fixed")
    today = ql.Date(1, 1, 2026)
    dc = ql.Actual365Fixed()
    expiry = today + int(round(days))
    rate_h = ql.YieldTermStructureHandle(ql.FlatForward(today, rate, dc))
    div_h = ql.YieldTermStructureHandle(ql.FlatForward(today, dividend, dc))
    return ql, today, dc, expiry, rate_h, div_h


def _with_date(today, fn):
    import QuantLib as ql
    old = ql.Settings.instance().evaluationDate
    ql.Settings.instance().evaluationDate = today
    try:
        return fn()
    finally:
        ql.Settings.instance().evaluationDate = old


def quantlib_european_greeks(c, m):
    """QuantLib analytic European price, delta and vega (vega per 1 vol point)."""
    ql, today, dc, expiry, r, q = _ql_setup(c.maturity, m.rate, m.dividend)

    def run():
        vol = ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today, ql.NullCalendar(), m.volatility, dc))
        proc = ql.BlackScholesMertonProcess(ql.QuoteHandle(ql.SimpleQuote(m.spot)), q, r, vol)
        opt = ql.VanillaOption(ql.PlainVanillaPayoff(ql.Option.Call if c.call else ql.Option.Put, c.strike),
                               ql.EuropeanExercise(expiry))
        opt.setPricingEngine(ql.AnalyticEuropeanEngine(proc))
        return {"price": opt.NPV(), "delta": opt.delta(), "vega_per_vol_point": opt.vega() / 100,
                "QuantLib_version": ql.__version__}
    return _with_date(today, run)


def quantlib_barrier(spot, strike, barrier, maturity, rate, vol, direction="down", call=True, dividend=0.0):
    """QuantLib AnalyticBarrierEngine: continuously monitored knock-out, zero rebate."""
    ql, today, dc, expiry, r, q = _ql_setup(maturity, rate, dividend)

    def run():
        v = ql.BlackVolTermStructureHandle(ql.BlackConstantVol(today, ql.NullCalendar(), vol, dc))
        proc = ql.BlackScholesMertonProcess(ql.QuoteHandle(ql.SimpleQuote(spot)), q, r, v)
        kind = ql.Barrier.DownOut if direction == "down" else ql.Barrier.UpOut
        opt = ql.BarrierOption(kind, barrier, 0.0,
                               ql.PlainVanillaPayoff(ql.Option.Call if call else ql.Option.Put, strike),
                               ql.EuropeanExercise(expiry))
        opt.setPricingEngine(ql.AnalyticBarrierEngine(proc))
        return opt.NPV()
    return _with_date(today, run)


def quantlib_heston(spot, strike, maturity, rate, p, dividend=0.0, call=True):
    """QuantLib AnalyticHestonEngine (semi-analytic Fourier integral) European price."""
    ql, today, dc, expiry, r, q = _ql_setup(maturity, rate, dividend)

    def run():
        proc = ql.HestonProcess(r, q, ql.QuoteHandle(ql.SimpleQuote(spot)), p.v0, p.kappa, p.theta, p.xi, p.rho)
        opt = ql.VanillaOption(ql.PlainVanillaPayoff(ql.Option.Call if call else ql.Option.Put, strike),
                               ql.EuropeanExercise(expiry))
        opt.setPricingEngine(ql.AnalyticHestonEngine(ql.HestonModel(proc)))
        return opt.NPV()
    return _with_date(today, run)
