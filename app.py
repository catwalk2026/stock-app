import streamlit as st
import yfinance as yf
import matplotlib.pyplot as plt

st.title("株式分析ツール")

ticker = st.text_input("銘柄コードを入力", value="7203.T")

if ticker:
    stock = yf.Ticker(ticker)
    df = stock.history(period="1y")

    df['MA25'] = df['Close'].rolling(25).mean()
    df['MA75'] = df['Close'].rolling(75).mean()

    fig, ax = plt.subplots(figsize=(12, 5))
    ax.plot(df.index, df['Close'], color='#00e5a0', lw=1.5, label='Close')
    ax.plot(df.index, df['MA25'], color='#00b8ff', lw=1, label='MA25')
    ax.plot(df.index, df['MA75'], color='#ffd166', lw=1, label='MA75')
    ax.legend()
    ax.grid(alpha=0.2)
    st.pyplot(fig)

    latest = df['Close'].iloc[-1]
    change = df['Close'].iloc[-1] - df['Close'].iloc[-2]
    pct = change / df['Close'].iloc[-2] * 100

    col1, col2, col3 = st.columns(3)
    col1.metric("現在値", f"{latest:.0f}")
    col2.metric("前日比", f"{change:.0f}")
    col3.metric("騰落率", f"{pct:.2f}%")
