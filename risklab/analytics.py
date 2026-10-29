import math
from scipy.special import ndtr

def lognormal_payoff(log_mean, log_variance, strike, discount, call=True):
    if log_variance <= 0:
        value = math.exp(log_mean)
        return discount * max(value-strike if call else strike-value, 0.0)
    sigma = math.sqrt(log_variance)
    d2 = (log_mean - math.log(strike)) / sigma
    d1 = d2 + sigma
    mean = math.exp(log_mean + .5 * log_variance)
    if call:
        return discount * (mean * ndtr(d1) - strike * ndtr(d2))
    return discount * (strike * ndtr(-d2) - mean * ndtr(-d1))

def analytic_price(c, m):
    t = c.maturity
    if c.kind == "asian" and m.volatility > 0 and t > 0:
        raise ValueError("No arithmetic-Asian closed form in this implementation")
    if c.kind == "asian":
        values = [m.spot * math.exp((m.rate-m.dividend) * t * i / c.fixings)
                  for i in range(1, c.fixings+1)]
        avg = sum(values) / len(values)
        return math.exp(-m.rate*t) * max(avg-c.strike if c.call else c.strike-avg, 0)
    if c.kind == "european":
        mean = math.log(m.spot) + (m.rate-m.dividend-.5*m.volatility**2)*t
        variance = m.volatility**2*t
    else:
        n = c.fixings
        # Equal future times i*T/n, i=1..n. No observation at t=0.
        mean = math.log(m.spot) + (m.rate-m.dividend-.5*m.volatility**2)*t*(n+1)/(2*n)
        variance = m.volatility**2*t*(n+1)*(2*n+1)/(6*n*n)
    return float(lognormal_payoff(mean, variance, c.strike, math.exp(-m.rate*t), c.call))

def analytic_greeks(c, m):
    if c.kind != "european":
        raise ValueError("Analytic Greeks only implemented for European contracts")
    t = c.maturity
    if t == 0 or m.volatility == 0:
        raise ValueError("Degenerate sensitivities require a convention at the payoff kink")
    d1 = (math.log(m.spot/c.strike)+(m.rate-m.dividend+.5*m.volatility**2)*t)/(m.volatility*math.sqrt(t))
    delta = math.exp(-m.dividend*t)*(ndtr(d1) if c.call else ndtr(d1)-1)
    vega = m.spot*math.exp(-m.dividend*t)*math.exp(-.5*d1*d1)/math.sqrt(2*math.pi)*math.sqrt(t)/100
    return {"delta": float(delta), "vega_per_vol_point": float(vega)}
