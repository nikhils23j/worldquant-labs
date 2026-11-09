import json
import subprocess
import sys
from pathlib import Path
import pytest

@pytest.mark.parametrize('module,args',[('exchangelab.cli',['demo']),('exchangelab.cli',['validate','--count','500']),('risklab.cli',['price','--paths','1000','--fixings','12']),
    ('exchangelab.cli',['validate','--count','500','--mix','v2','--stp','cancel_taker']),
    ('exchangelab.cli',['market','--latency','50','--mode','passive','--seed','2']),
    ('risklab.cli',['price','--paths','4000','--fixings','12','--method','antithetic_control']),
    ('risklab.cli',['barrier','--paths','2000','--steps','12']),
    ('risklab.cli',['heston','--paths','2000','--steps','12']),
    ('risklab.cli',['greeks-compare','--kind','european','--paths','2000']),
    ('risklab.cli',['scale-case','--paths','2000','--batch','500']),
    ('risklab.cli',['qmc','--reps','2','--fixings','8','--max-log2','10'])])
def test_cli_artifact(tmp_path,module,args):
    output=tmp_path/'result.json'
    subprocess.run([sys.executable,'-m',module,*args,'--output',str(output)],check=True,capture_output=True,text=True)
    assert json.loads(output.read_text())

@pytest.mark.parametrize('name',['exchangelab','risklab'])
def test_dashboard_load_and_control(name):
    from streamlit.testing.v1 import AppTest
    root=Path(__file__).resolve().parents[1]
    app=AppTest.from_file(str(root/name/'dashboard.py')).run(timeout=60)
    assert not app.exception
    if name=='exchangelab':
        app.select_slider[0].set_value(50).run(timeout=60)
        app.slider[0].set_value(10).run(timeout=60)
    else:
        app.slider[2].set_value(.4).run(timeout=60)
        app.button[0].click().run(timeout=60)
    assert not app.exception

def test_generate_replay_roundtrip(tmp_path):
    workload=tmp_path/'commands.jsonl'; output=tmp_path/'replay.json'
    subprocess.run([sys.executable,'-m','exchangelab.cli','generate','--count','1000','--output',str(workload)],check=True)
    subprocess.run([sys.executable,'-m','exchangelab.cli','replay','--input',str(workload),'--output',str(output)],check=True)
    assert len(workload.read_text().splitlines())==json.loads(output.read_text())['commands']==1000
