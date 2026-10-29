from dataclasses import replace
import pytest
from exchangelab import Command, Exchange
from exchangelab.reference import ReferenceExchange
from exchangelab.events import EventLoop
from exchangelab.experiments import workload, simulate, differential

def c(oid,side='B',qty=10,price=100,owner=None,kind='limit',symbol=0,time=0):
    return Command(kind,oid,oid if owner is None else owner,symbol,side,qty,price,time)

@pytest.mark.parametrize('factory',[Exchange,ReferenceExchange])
def test_fifo_partial_and_resting_price(factory):
    e=factory(); e.process(c(1,'S',5,100));e.process(c(2,'S',7,100))
    out=e.process(c(3,'B',8,110))
    assert [(t.maker,t.qty,t.price) for t in out.trades]==[(1,5,100),(2,3,100)]
    assert e.state()[0][0]==2 and e.state()[0][5]==4
    assert e.process(c(2,kind='cancel')).accepted
    assert not e.state()

@pytest.mark.parametrize('factory',[Exchange,ReferenceExchange])
def test_price_priority_and_market_residual(factory):
    e=factory();e.process(c(1,'S',2,105));e.process(c(2,'S',2,100))
    r=e.process(c(3,'B',10,kind='market'))
    assert [t.maker for t in r.trades]==[2,1]
    assert r.unfilled==6 and not e.state()
    assert not e.process(c(3)).accepted

@pytest.mark.parametrize('factory',[Exchange,ReferenceExchange])
def test_cancel_ownership_symbol_and_reuse(factory):
    e=factory();e.process(c(1,owner=4))
    assert not e.process(c(1,kind='cancel',owner=5)).accepted
    assert not e.process(c(1,kind='cancel',owner=4,symbol=1)).accepted
    assert e.process(c(1,kind='cancel',owner=4)).accepted
    assert not e.process(c(1,owner=4)).accepted

@pytest.mark.parametrize('factory',[Exchange,ReferenceExchange])
def test_invalid_time_capacity_and_empty(factory):
    e=factory(max_live=1)
    assert not e.process(c(1,qty=0)).accepted
    assert not e.process(c(1,price=0)).accepted
    assert not e.process(c(1,side='X')).accepted
    assert not e.process(c(1,qty=True)).accepted
    assert e.process(c(1,time=10)).accepted
    assert not e.process(c(2,time=9)).accepted
    assert e.process(c(2,time=10)).reason=='capacity'
    assert e.process(c(1,time=10,kind='cancel')).accepted
    assert e.process(c(2,time=10)).accepted

@pytest.mark.parametrize('seed',[0,17,99])
def test_randomized_oracle(seed):
    assert differential(3000,seed)['mismatches']==0

def test_heap_churn_and_conservation():
    e=Exchange()
    for i in range(500):
        e.process(c(i,price=100+i))
        e.process(c(i,kind='cancel'))
        e.check()
    assert len(e.books[0][0].heap)<=33
    e.process(c(1000,'S',3));e.process(c(1001,'B',3))
    e.check();assert e.cash[1000]==300 and e.positions[1001,0]==3

def test_equal_time_sequence_and_no_past_events():
    loop=EventLoop();events=[]
    loop.schedule(5,events.append,'first');loop.schedule(5,events.append,'second')
    loop.run();assert events==['first','second']
    with pytest.raises(ValueError):loop.schedule(4,events.append,'past')

def test_cancel_race_event_order():
    for cancel_first in (True,False):
        loop=EventLoop();e=Exchange();e.process(c(1,'S',2))
        commands=[c(1,kind='cancel',time=10),c(2,'B',2,kind='market',time=10)]
        if not cancel_first:commands.reverse()
        out=[]
        for cmd in commands:loop.schedule(10,lambda cmd=cmd:out.append(e.process(cmd)))
        loop.run();assert sum(len(x.trades) for x in out)==(0 if cancel_first else 1)

@pytest.mark.parametrize('delay',[0,1,10,50])
def test_latency_causality_reproducibility(delay):
    a=simulate(2,delay,duration_ms=300,trace=True)
    b=simulate(2,delay,duration_ms=300,trace=True)
    assert a['sha256']==b['sha256']
    assert a['decisions']
    for d in a['decisions']:
        assert d['observed_exchange_time']<=d['observation_delivery_time']<=d['time']
        assert d['observation_delivery_time']-d['observed_exchange_time']==delay*1000
        assert 0<=d['filled_qty']<=d['qty']
    assert 0<=a['fill_rate']<=1

def test_symbols_isolated():
    e=Exchange();e.process(c(1,'S',symbol=0));e.process(c(2,'B',kind='market',symbol=1))
    assert len(e.orders)==1
