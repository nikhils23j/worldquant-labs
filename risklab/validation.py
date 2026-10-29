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
