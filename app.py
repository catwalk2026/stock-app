import streamlit as st
import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import time

st.title("株式分析ツール")

ticker = st.text_input("銘柄コードを入力", value="7203.T")

def calc_rsi(series, period=14):
    delta = series.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = -delta.clip(upper=0).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def calc_macd(series):
    ema12 = series.ewm(span=12).mean()
    ema26 = series.ewm(span=26).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9).mean()
    return macd, signal

if ticker:
    try:
        with st.spinner("データ取得中..."):
            time.sleep(1)
            stock = yf.Ticker(ticker)
            df = stock.history(period="1y")

        if df.empty:
            st.error("データが取得できませんでした。")
        else:
            df['MA25'] = df['Close'].rolling(25).mean()
            df['MA75'] = df['Close'].rolling(75).mean()
            df['RSI'] = calc_rsi(df['Close'])
            df['MACD'], df['Signal'] = calc_macd(df['Close'])

            fig = plt.figure(figsize=(12, 10))
            gs = gridspec.GridSpec(3, 1, height_ratios=[3, 1, 1], hspace=0.1)

            # 株価チャート
            ax1 = fig.add_subplot(gs[0])
            ax1.plot(df.index, df['Close'], color='#00e5a0', lw=1.5, label='Close')
            ax1.plot(df.index, df['MA25'], color='#00b8ff', lw=1, label='MA25')
            ax1.plot(df.index, df['MA75'], color='#ffd166', lw=1, label='MA75')
            ax1.legend(loc='upper left', fontsize=8)
            ax1.grid(alpha=0.2)
            ax1.set_ylabel('Price')
            ax1.set_xticklabels([])

            # RSI
            ax2 = fig.add_subplot(gs[1])
            ax2.plot(df.index, df['RSI'], color='#ff4d6d', lw=1.2)
            ax2.axhline(70, color='white', alpha=0.3, linestyle='--', lw=0.8)
            ax2.axhline(30, color='white', alpha=0.3, linestyle='--', lw=0.8)
            ax2.fill_between(df.index, df['RSI'], 70,
                where=(df['RSI'] >= 70), alpha=0.2, color='#ff4d6d')
            ax2.fill_between(df.index, df['RSI'], 30,
                where=(df['RSI'] <= 30), alpha=0.2, color='#00e5a0')
            ax2.set_ylabel('RSI')
            ax2.set_ylim(0, 100)
            ax2.grid(alpha=0.2)
            ax2.set_xticklabels([])

            # MACD
            ax3 = fig.add_subplot(gs[2])
            ax3.plot(df.index, df['MACD'], color='#00b8ff', lw=1.2, label='MACD')
            ax3.plot(df.index, df['Signal'], color='#ffd166', lw=1.2, label='Signal')
            ax3.bar(df.index, df['MACD'] - df['Signal'],
                color=['#00e5a0' if v >= 0 else '#ff4d6d'
                       for v in (df['MACD'] - df['Signal'])],
                alpha=0.5, width=1)
            ax3.axhline(0, color='white', alpha=0.2)
            ax3.set_ylabel('MACD')
            ax3.legend(loc='upper left', fontsize=8)
            ax3.grid(alpha=0.2)

            plt.style.use('dark_background')
            st.pyplot(fig)

            latest = df['Close'].iloc[-1]
            change = df['Close'].iloc[-1] - df['Close'].iloc[-2]
            pct = change / df['Close'].iloc[-2] * 100
            rsi_now = df['RSI'].iloc[-1]

            col1, col2, col3, col4 = st.columns(4)
            col1.metric("現在値", f"{latest:.0f}")
            col2.metric("前日比", f"{change:.0f}")
            col3.metric("騰落率", f"{pct:.2f}%")
            col4.metric("RSI", f"{rsi_now:.1f}",
                delta="過熱" if rsi_now > 70 else "売られすぎ" if rsi_now < 30 else "中立")

    except Exception as e:
        st.error("データ取得に失敗しました。少し待ってから再試行してください。")
        if st.button("再試行"):
            st.rerun()
