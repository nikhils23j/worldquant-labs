import streamlit as st
import plotly.graph_objects as go
from exchangelab.experiments import simulate

st.set_page_config(page_title="ExchangeLab", layout="wide")
st.title("ExchangeLab")
st.caption("Explore price-time queues and delayed information in a synthetic market.")
seed = st.sidebar.number_input("Random seed", 1, 10000, 1)
delay = st.sidebar.select_slider("One-way delay (ms)", [0, 1, 10, 50], value=0)
@st.cache_data
def run(seed, delay):
    return simulate(seed, delay, duration_ms=300, trace=True)
r = run(seed, delay)
a,b,c = st.columns(3)
a.metric("Quantity filled", f"{100*r['fill_rate']:.1f}%" if r['fill_rate'] is not None else "No orders")
b.metric("Execution cost", f"{r['execution_cost_bps']:.2f} bps" if r['execution_cost_bps'] is not None else "No fills")
c.metric("Orders without fills", r['unfilled_orders'])
st.caption("Cost is side-adjusted versus the midpoint observed at the decision. Unfilled orders are excluded from cost.")
i = st.slider("Step through exchange events", 0, len(r["history"])-1, 0)
e = r["history"][i]
fig = go.Figure()
for levels, name, color in zip(e['depth'], ['Bids','Asks'], ['#15803d','#dc2626']):
    fig.add_bar(x=[p/100 for p,q in levels], y=[q for p,q in levels], name=name, marker_color=color)
fig.update_layout(xaxis_title="Price (100 ticks per displayed unit)", yaxis_title="Resting quantity", barmode="group")
st.plotly_chart(fig, width="stretch")
st.write(f"Simulated time: {e['time_us']/1000:.1f} ms")
st.json(e)
st.subheader("Probe decisions and fills")
st.dataframe(r['decisions'])
st.download_button("Download reproducible run", __import__('json').dumps(r, indent=2), "exchange-demo.json")
