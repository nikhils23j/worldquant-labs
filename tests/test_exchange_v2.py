import pytest
from exchangelab import Command, Exchange
from exchangelab.book_v0 import ExchangeV0
from exchangelab.reference import ReferenceExchange
from exchangelab.experiments import workload, workload_v2, differential, replay
from exchangelab.market import Market, MarketConfig, run_market, PROBE
from exchangelab.latency_study import ci95, curve, skew_study


def c(oid, side='B', qty=10, price=100, owner=None, kind='limit', time=0, tif='GTC', post_only=False):
    return Command(kind, oid, oid if owner is None else owner, 0, side, qty, price, time, tif, post_only)


ENGINES = [Exchange, ReferenceExchange]


@pytest.mark.parametrize('factory', ENGINES)
def test_ioc_residual_never_rests(factory):
    e = factory()
    e.process(c(1, 'S', 3, 100))
    out = e.process(c(2, 'B', 10, 101, tif='IOC'))
    assert [t.qty for t in out.trades] == [3] and out.unfilled == 7
    assert e.state() == []
    out = e.process(c(3, 'B', 5, 99, tif='IOC'))  # nothing to hit
    assert out.accepted and out.unfilled == 5 and not out.trades and e.state() == []


@pytest.mark.parametrize('factory', ENGINES)
def test_post_only(factory):
    e = factory()
    e.process(c(1, 'S', 3, 100))
    r = e.process(c(2, 'B', 1, 100, post_only=True))
    assert not r.accepted and r.reason == 'post_only_would_cross'
    assert e.process(c(2, 'B', 1, 99)).reason == 'duplicate_id'  # a rejected post-only still consumes its id
    assert e.process(c(3, 'B', 1, 99, post_only=True)).accepted
    assert len(e.state()) == 2
    assert not e.process(c(4, 'B', 1, 99, tif='IOC', post_only=True)).accepted  # contradictory


@pytest.mark.parametrize('factory', ENGINES)
def test_self_trade_prevention_modes(factory):
    # owner 7 rests two asks; another owner rests behind them; owner 7 then buys.
    def setup(stp):
        e = factory(stp=stp)
        e.process(c(1, 'S', 2, 100, owner=7))
        e.process(c(2, 'S', 2, 100, owner=8))
        return e
    e = setup('none')
    out = e.process(c(3, 'B', 3, 100, owner=7))
    assert [(t.maker, t.buyer, t.seller) for t in out.trades][0] == (1, 7, 7)  # self-trade allowed
    e = setup('cancel_resting')
    out = e.process(c(3, 'B', 3, 100, owner=7))
    assert out.stp_cancelled == (1,) and [t.maker for t in out.trades] == [2]
    assert [s[0] for s in e.state()] == [3]  # residual 1 rests
    e = setup('cancel_taker')
    out = e.process(c(3, 'B', 3, 100, owner=7))
    assert out.stp_cancelled == (3,) and not out.trades and out.unfilled == 3
    assert [s[0] for s in e.state()] == [1, 2]
    with pytest.raises(ValueError):
        factory(stp='bogus')


def test_lazy_cancel_keeps_fifo_and_queue_position():
    e, ref = Exchange(), ReferenceExchange()
    for x in (e, ref):
        for i in range(1, 6):
            x.process(c(i, 'S', 2, 100, time=i))
        x.process(c(3, kind='cancel', time=6))
    assert e.queue_ahead(5) == ref.queue_ahead(5) == 6
    assert e.queue_ahead(1) == ref.queue_ahead(1) == 0
    assert e.queue_ahead(3) is None
    out = e.process(c(9, 'B', 5, 100, time=7))
    assert [t.maker for t in out.trades] == [1, 2, 4]
    e.check()


def test_queue_ahead_matches_reference_on_random_flow():
    e, ref = Exchange(), ReferenceExchange()
    for i, cmd in enumerate(workload_v2(4000, seed=3)):
        assert e.process(cmd) == ref.process(cmd)
        if i % 97 == 0:
            for oid in list(e.orders)[:20]:
                assert e.queue_ahead(oid) == ref.queue_ahead(oid)


@pytest.mark.parametrize('stp', ['none', 'cancel_resting', 'cancel_taker'])
@pytest.mark.parametrize('seed', [1, 2])
def test_randomized_oracle_v2(stp, seed):
    r = differential(4000, seed, mix='v2', stp=stp, state_every=100)
    assert r['mismatches'] == 0
    assert r['command_mix'].get('IOC') and r['command_mix'].get('post_only')
    if stp != 'none':
        assert r['stp_cancellations'] > 0


def test_v0_and_fast_engine_agree():
    cmds = list(workload(20000, seed=5))
    a, b = replay(cmds, engine='v0'), replay(cmds, engine='fast')
    assert a['state_sha256'] == b['state_sha256'] and a['trades'] == b['trades'] and a['volume'] == b['volume']


def test_v0_baseline_is_self_contained():
    import inspect
    import exchangelab.book_v0 as v0
    src = inspect.getsource(v0)
    assert 'from .orders' not in src and 'ExchangeV0' in src
    e = ExchangeV0()
    e.process(c(1, 'S', 2, 100)); out = e.process(c(2, 'B', 2, 100))
    assert out.trades[0].qty == 2


# ---------------------------------------------------------------- agent market --------------
def test_market_deterministic_and_conserving():
    a = run_market(3, duration_us=500_000, probe_latency_us=40)
    b = run_market(3, duration_us=500_000, probe_latency_us=40)
    assert a == b
    assert a['probe']['filled_qty'] <= a['probe']['submitted_qty']


def test_common_random_numbers_across_latencies():
    m1 = Market(MarketConfig(duration_us=500_000, probe_latency_us=0), 4); m1.run()
    m2 = Market(MarketConfig(duration_us=500_000, probe_latency_us=900), 4); m2.run()
    assert m1.jump_times == m2.jump_times and m1.values == m2.values


@pytest.mark.parametrize('mode', ['taker', 'passive'])
@pytest.mark.parametrize('latency', [0, 75, 600])
def test_probe_causality(mode, latency):
    m = Market(MarketConfig(duration_us=500_000, probe_latency_us=latency, probe_mode=mode), 2)
    m.run()
    infos = [i for i in m.order_info.values() if 'arrival_us' in i]
    assert infos
    for i in infos:  # an order can never reach the exchange sooner than one-way latency
        assert i['arrival_us'] - i['decision_us'] >= latency
    for t in m.trades:
        if PROBE in (m.owner_of.get(t.maker), m.owner_of.get(t.taker)):
            oid = t.maker if m.owner_of.get(t.maker) == PROBE else t.taker
            assert t.time >= m.order_info[oid]['decision_us'] + latency


def test_probe_off_has_no_fills():
    r = run_market(1, duration_us=300_000, probe_mode='off')
    assert r['probe']['filled_qty'] == 0 and r['probe']['orders'] == 0


def test_speed_beats_slowness_for_stale_quote_races():
    r = curve('taker', (0, 2000), (1, 2, 3), duration_us=1_000_000)
    fast, slow = r['summary']
    assert fast['filled_qty']['mean'] > slow['filled_qty']['mean']
    assert fast['pnl_ticks_1000us']['mean'] > slow['pnl_ticks_1000us']['mean']
    assert slow['pnl_ticks_1000us_paired_diff_vs_fastest']['mean'] < 0


def test_inventory_skew_reduces_inventory():
    no, yes = skew_study((1, 2, 3, 4))
    assert yes['mean_abs_inventory']['mean'] < no['mean_abs_inventory']['mean']


def test_ci95():
    r = ci95([1.0, 2.0, 3.0])
    assert r['mean'] == 2.0 and r['half_width'] == pytest.approx(4.302652729911275 * 1 / 3 ** .5)
    assert ci95([None])['n'] == 0
