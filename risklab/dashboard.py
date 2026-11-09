from dataclasses import replace
import streamlit as st
import plotly.graph_objects as go
import numpy as np
from risklab.contracts import Contract, Model
from risklab.estimators import price
from risklab.greeks import sensitivities

st.set_page_config(page_title='RiskLab',layout='wide')
st.title('RiskLab')
st.caption('Option prices, numerical uncertainty and sensitivities under a constant-volatility model.')
spot=st.sidebar.slider('Spot',60.,140.,100.)
strike=st.sidebar.slider('Strike',60.,140.,100.)
vol=st.sidebar.slider('Annual volatility',.05,.60,.30,.01)
fixings=st.sidebar.select_slider('Future observations',options=[12,64,252],value=64)
kind=st.sidebar.selectbox('Payoff',['asian','european','geometric'])
put=st.sidebar.checkbox('Put option')
c,m=Contract(kind,strike,1.,fixings,not put),Model(spot,vol)
@st.cache_data
def evaluate(c,m):
    methods=['plain','antithetic']+(['control','antithetic_control'] if c.kind=='asian' else [])
    results=[]
    for total in [10000,30000,100000]:
        for method in methods:
            pilot=1000
            n=total-pilot if 'control' in method else total
            r=price(c,m,method,n,42,pilot_paths=pilot)
            r['budget']=total
            results.append(r)
    return results,sensitivities(c,m,30000)
with st.spinner('Running seeded simulations…'):
    rows,greeks=evaluate(c,m)
fig=go.Figure()
for method in sorted({r['method'] for r in rows}):
    data=[r for r in rows if r['method']==method]
    fig.add_scatter(x=[r['budget'] for r in data],y=[r['estimate'] for r in data],
                    error_y=dict(type='data',array=[r['half_width'] for r in data]),mode='lines+markers',name=method)
fig.update_layout(xaxis_title='Total generated paths (including pilot)',yaxis_title='Option value; 95% Monte Carlo intervals')
st.plotly_chart(fig,width='stretch')
a,b=st.columns(2)
a.metric('Delta',f"{greeks['delta']['estimate']:.4f}")
b.metric('Vega / volatility percentage point',f"{greeks['vega_per_vol_point']['estimate']:.4f}")
st.caption('Intervals describe simulation uncertainty, not model accuracy. Control estimation uses an independent pilot.')
st.dataframe([{'method':r['method'],'paths':r['budget'],'value':r['estimate'],'half_width':r['half_width'],'seconds':r['seconds']} for r in rows])
if st.button('Calculate spot/volatility surface'):
    spots=np.linspace(spot*.8,spot*1.2,7);vols=np.linspace(max(.03,vol*.6),vol*1.4,7)
    z=[[price(c,replace(m,spot=float(s),volatility=float(v)),'control' if kind=='asian' else 'antithetic',10000,42,pilot_paths=1000)['estimate'] for s in spots] for v in vols]
    surface=go.Figure(go.Surface(x=spots,y=vols,z=z))
    surface.update_layout(scene=dict(xaxis_title='Spot',yaxis_title='Volatility',zaxis_title='Option value'))
    st.plotly_chart(surface,width='stretch')
st.download_button('Download estimates',__import__('json').dumps(rows,indent=2),'risk-estimates.json')
