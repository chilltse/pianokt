import torch
import pytest
from pianokt_backend.experiments.benchmark import Benchmark, seed, bayesian_control

class Fake:
    seq_len=4
    def eval(self): return self
    def requires_grad_(self, flag): return self
    def predict_next(self,state,attrs,pid,**kw): return torch.tensor(.5)
    def predict_next_with_mu_override(self,state,attrs,pid,mu,**kw): return torch.tensor(.2+.1*state.next_position)
    def observe(self,state,attrs,pid,response,event_id,**kw):
        return State(state.next_position+1),torch.tensor(.5)
class State:
    def __init__(self,n=0): self.next_position=n

def runner():
    return Benchmark(Fake(),[dict(attrs=[1],pid=1,mu_override=m) for m in [-.5,-.25,0,.25,.5]],{})

def test_full_trajectory_independence_and_one_step():
    r=runner();s=State();c=dict(segment_id='a',events=[dict(attrs=[1],pid=1)]*3,duration_seconds=6)
    a=r.mc(s,c,'student','selection',4)
    b=r.mc(s,c,'student','selection',4)
    assert a['macro_gain']==pytest.approx(.3)
    assert a['gain']==b['gain']
    assert s.next_position==0
    one=r.one_step(s,c)
    assert one['mean']==pytest.approx(.1)
    assert one['sum']==pytest.approx(.3)
    assert one['per_second']==pytest.approx(.05)

def test_seed_and_negative_control():
    assert seed(1,'selection') != seed(1,'evaluation')
    assert seed(1,'selection') == seed(1,'selection')
    result=bayesian_control()
    assert abs(result['mean_gain']) < 4*result['sem']
