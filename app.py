import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time

st.set_page_config(page_title="株式分析ツール", page_icon="📈", layout="wide")

st.title("📈 株式分析ツール")

t1 = st.text_input("銘柄1", value="7203.T")

if t1:
    with st.spinner("取得中..."):
        time.sleep(1)
        df = yf.Ticker(t1).history(period="1y", interval="1d")
    if not df.empty:
        st.write("データ取得成功！")
        st.write(df.tail(3))
    else:
        st.error("データが取得できませんでした")
