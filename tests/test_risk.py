from dataclasses import replace
import math
import numpy as np
import pytest
from risklab import Contract,Model,price
from risklab.analytics import analytic_price,analytic_greeks
from risklab.paths import scalar_summaries,summaries_from_shocks,payoff_batches
from risklab.greeks import sensitivities
from risklab.validation import quantlib_reference

@pytest.mark.parametrize('kwargs',[{'spot':0},{'volatility':-1},{'rate':float('nan')}])
def test_invalid_model(kwargs):
    with pytest.raises(ValueError):Model(**kwargs)

@pytest.mark.parametrize('kwargs',[{'strike':0},{'maturity':-1},{'fixings':0},{'kind':'unknown'}])
def test_invalid_contract(kwargs):
    with pytest.raises(ValueError):Contract(**kwargs)

@pytest.mark.parametrize('call',[True,False])
@pytest.mark.parametrize('kind',['european','geometric'])
def test_analytic_quantlib(call,kind):
    c=Contract(kind,call=call);m=Model()
    assert analytic_price(c,m)==pytest.approx(quantlib_reference(c,m)['estimate'],abs=1e-9)

@pytest.mark.parametrize('kind',['european','geometric','asian'])
@pytest.mark.parametrize('call',[True,False])
def test_zero_volatility_and_maturity(kind,call):
    c=Contract(kind,fixings=12,call=call);m=Model(volatility=0)
    r=price(c,m,'plain',1000)
    assert r['estimate']==pytest.approx(analytic_price(c,m),abs=1e-10)
    r=price(replace(c,maturity=0),Model(spot=110),'plain',1000)
    assert r['estimate']==pytest.approx(10 if call else 0,abs=1e-10)

def test_scalar_vector_same_shocks():
    z=np.random.default_rng(1).standard_normal((200,20));m=Model()
    for a,b in zip(scalar_summaries(z,m,.5),summaries_from_shocks(z,m,.5)):
        np.testing.assert_allclose(a,b,rtol=1e-13,atol=1e-10)

def test_seed_and_antithetic_units():
    c,m=Contract(fixings=12),Model()
    a=price(c,m,'antithetic',2000,5);b=price(c,m,'antithetic',2000,5)
    assert a['estimate']==b['estimate'] and a['independent_units']==1000
    seed=np.random.SeedSequence(5).spawn(2)[1]
    y=np.concatenate([v for v,_ in payoff_batches(c,m,2000,seed,antithetic=True)])
    assert a['standard_error']==pytest.approx(np.std(y,ddof=1)/np.sqrt(1000))

def test_control_independent_pilot_and_precision():
    c,m=Contract(fixings=64),Model()
    plain=price(c,m,'plain',30000,3)
    r=price(c,m,'control',20000,3,pilot_paths=10000)
    assert r['total_generated_paths']==plain['total_generated_paths']==30000
    assert r['standard_error']<plain['standard_error']
    assert abs(r['estimate']-plain['estimate'])<5*math.hypot(r['standard_error'],plain['standard_error'])
    assert r['independent_units']==20000

@pytest.mark.parametrize('method,paths',[('antithetic',3),('antithetic',2),('plain',1)])
def test_invalid_path_count(method,paths):
    with pytest.raises(ValueError):price(Contract(),Model(),method,paths)

def test_invalid_control_contract():
    with pytest.raises(ValueError):price(Contract('european'),Model(),'control',100)

def test_european_parity_and_monte_carlo():
    c,m=Contract('european'),Model()
    call=analytic_price(c,m);put=analytic_price(replace(c,call=False),m)
    assert call-put==pytest.approx(m.spot-math.exp(-m.rate)*c.strike)
    r=price(c,m,'antithetic',100000,42)
    assert abs(r['estimate']-call)<5*r['standard_error']

def test_common_random_greeks():
    c,m=Contract('european'),Model()
    g=sensitivities(c,m,100000,7);truth=analytic_greeks(c,m)
    for key in truth:
        assert abs(g[key]['estimate']-truth[key])<5*g[key]['standard_error']+.001
    with pytest.raises(ValueError):sensitivities(c,m,spot_bump=200)

def test_asian_quantlib_independent_mc():
    c,m=Contract(),Model()
    r=price(c,m,'control',20000,11);q=quantlib_reference(c,m,20000,13)
    assert abs(r['estimate']-q['estimate'])<5*math.hypot(r['standard_error'],q['standard_error'])
