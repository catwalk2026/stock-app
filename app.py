import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time

st.set_page_config(page_title="株式分析ツール", page_icon="📈", layout="wide")
st.markdown("""
<style>
.stApp { background-color: #0d1117; color: #e6edf3; }
.stMarkdown { color: #e6edf3; }
section[data-testid="stSidebar"] { background-color: #0d1117; }
section[data-testid="stSidebar"] * { color: #e6edf3 !important; }
section[data-testid="stSidebar"] .stButton button {
    background-color: #161b22;
    border: 1px solid #30363d;
    border-radius: 8px;
    text-align: left;
}
section[data-testid="stSidebar"] .stButton button:hover {
    border-color: #00e5a0;
    background-color: #1f2d3d;
}
[data-testid="metric-container"] {
    background: #161b22;
    border: 1px solid #1f2d3d;
    border-radius: 10px;
    padding: 12px !important;
}
[data-testid="metric-container"] label { color: #6b7280 !important; }
[data-testid="metric-container"] [data-testid="metric-value"] { color: #e6edf3 !important; }
</style>
""", unsafe_allow_html=True)
st.markdown("""
<style>
section[data-testid="stSidebar"] {
    background-color: #0d1117;
    color: #e6edf3;
}
section[data-testid="stSidebar"] * {
    color: #e6edf3 !important;
}
section[data-testid="stSidebar"] .stButton button {
    background-color: #161b22;
    color: #e6edf3 !important;
    border: 1px solid #30363d;
    text-align: left;
    border-radius: 8px;
}
section[data-testid="stSidebar"] .stButton button:hover {
    background-color: #1f2d3d;
    border-color: #00e5a0;
}
section[data-testid="stSidebar"] .stSelectbox div {
    background-color: #161b22 !important;
    color: #e6edf3 !important;
}
</style>
""", unsafe_allow_html=True)
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

def calc_bb(series, period=20):
    ma = series.rolling(period).mean()
    std = series.rolling(period).std()
    return ma + 2*std, ma, ma - 2*std

def ai_signal(rsi, macd, macd_signal, ma25, ma75, close):
    score = 0
    reasons = []
    if rsi < 30:
        score += 2
        reasons.append("RSIが売られすぎゾーン")
    elif rsi > 70:
        score -= 2
        reasons.append("RSIが買われすぎゾーン")
    if macd > macd_signal:
        score += 1
        reasons.append("MACDがシグナル線を上回っている")
    else:
        score -= 1
        reasons.append("MACDがシグナル線を下回っている")
    if close > ma25:
        score += 1
        reasons.append("株価がMA25を上回っている")
    else:
        score -= 1
        reasons.append("株価がMA25を下回っている")
    if ma25 > ma75:
        score += 1
        reasons.append("MA25がMA75を上回っている（上昇トレンド）")
    else:
        score -= 1
        reasons.append("MA25がMA75を下回っている（下降トレンド）")
    if score >= 2:
        return "🟢 買いシグナル", score, reasons
    elif score <= -2:
        return "🔴 売りシグナル", score, reasons
    else:
        return "🟡 中立", score, reasons

def get_currency(ticker):
    try:
        info = yf.Ticker(ticker).info
        currency = info.get('currency', 'JPY')
        if currency == 'JPY':
            return '¥', 0
        elif currency == 'USD':
            return '$', 2
        else:
            return currency + ' ', 2
    except Exception:
        return '¥', 0

# ===== サイドバー =====
with st.sidebar:
    st.markdown("## 📈 株式分析ツール")
    st.markdown("---")

    st.markdown("### 🔍 銘柄検索")
    main_ticker = st.text_input("銘柄コード", value="7203.T", label_visibility="collapsed")

    st.markdown("### ⭐ ウォッチリスト")
    watchlist = [
        ("7203.T", "トヨタ"),
        ("6758.T", "ソニー"),
        ("9984.T", "ソフトバンクG"),
        ("AAPL", "Apple"),
        ("NVDA", "NVIDIA"),
        ("TSLA", "Tesla"),
    ]

    for symbol, name in watchlist:
        try:
            d = yf.Ticker(symbol).history(period="2d")
            if not d.empty:
                price = d['Close'].iloc[-1]
                chg = (d['Close'].iloc[-1] - d['Close'].iloc[-2]) / d['Close'].iloc[-2] * 100
                color = "🟢" if chg >= 0 else "🔴"
                sign = "+" if chg >= 0 else ""
                if st.button(f"{color} **{name}**\n{price:.0f}  {sign}{chg:.2f}%", key=symbol, use_container_width=True):
                    main_ticker = symbol
        except Exception:
            pass

    st.markdown("---")
    st.markdown("### 📅 期間・足")
    period_label = st.selectbox("期間", ["1週間","1ヶ月","3ヶ月","6ヶ月","1年","2年","5年"], index=4)
    interval_label = st.selectbox("足の種類", ["日足","週足","月足"], index=0)

    st.markdown("---")
    st.markdown("### 📐 インジケーター")
    show_ma25 = st.checkbox("MA25", value=True)
    show_ma75 = st.checkbox("MA75", value=True)
    show_bb   = st.checkbox("ボリンジャーバンド", value=False)
    show_rsi  = st.checkbox("RSI", value=True)
    show_macd = st.checkbox("MACD", value=True)
    show_vol  = st.checkbox("出来高", value=True)

period_map = {"1週間":"5d","1ヶ月":"1mo","3ヶ月":"3mo","6ヶ月":"6mo","1年":"1y","2年":"2y","5年":"5y"}
interval_map = {"日足":"1d","週足":"1wk","月足":"1mo"}
period = period_map[period_label]
interval = interval_map[interval_label]

# ===== メインエリア =====
if main_ticker:
    try:
        with st.spinner("データ取得中..."):
            time.sleep(0.5)
            df = yf.Ticker(main_ticker).history(period=period, interval=interval)
            symbol, decimals = get_currency(main_ticker)

        if df.empty:
            st.error("データが取得できませんでした。")
        else:
            df['MA25'] = df['Close'].rolling(25).mean()
            df['MA75'] = df['Close'].rolling(75).mean()
            df['RSI']  = calc_rsi(df['Close'])
            df['MACD'], df['Signal'] = calc_macd(df['Close'])
            df['BB_upper'], df['BB_mid'], df['BB_lower'] = calc_bb(df['Close'])

            latest = df['Close'].iloc[-1]
            change = df['Close'].iloc[-1] - df['Close'].iloc[-2]
            pct    = change / df['Close'].iloc[-2] * 100
            rsi_now  = df['RSI'].iloc[-1]
            macd_now = df['MACD'].iloc[-1]
            sig_now  = df['Signal'].iloc[-1]
            ma25_now = df['MA25'].iloc[-1]
            ma75_now = df['MA75'].iloc[-1]

            # ヘッダー
            col_a, col_b, col_c, col_d, col_e = st.columns([3,2,2,2,2])
            fmt = f"{{:.{decimals}f}}"
            col_a.markdown(f"## {main_ticker}　`{period_label} / {interval_label}`")
            col_b.metric("現在値", symbol + fmt.format(latest))
            col_c.metric("変化", symbol + fmt.format(change))
            col_d.metric("騰落率", f"{pct:.2f}%")
            col_e.metric("RSI", f"{rsi_now:.1f}")

            # AIシグナル
            signal, score, reasons = ai_signal(rsi_now, macd_now, sig_now, ma25_now, ma75_now, latest)
            st.markdown(f"**AIシグナル: {signal}** (スコア: {score:+d})　" + "　".join([f"・{r}" for r in reasons]))
            st.markdown("---")

            # サブプロット構成
            rows = 1
            row_heights = [0.6]
            if show_vol:
                rows += 1
                row_heights.append(0.1)
            if show_rsi:
                rows += 1
                row_heights.append(0.15)
            if show_macd:
                rows += 1
                row_heights.append(0.15)

            fig = make_subplots(
                rows=rows, cols=1,
                shared_xaxes=True,
                row_heights=row_heights,
                vertical_spacing=0.02,
            )

            # ローソク足
            fig.add_trace(go.Candlestick(
                x=df.index,
                open=df['Open'], high=df['High'],
                low=df['Low'], close=df['Close'],
                name='ローソク足',
                increasing_line_color='#00e5a0',
                decreasing_line_color='#ff4d6d',
            ), row=1, col=1)

            if show_ma25:
                fig.add_trace(go.Scatter(x=df.index, y=df['MA25'], name='MA25', line=dict(color='#00b8ff', width=1.2)), row=1, col=1)
            if show_ma75:
                fig.add_trace(go.Scatter(x=df.index, y=df['MA75'], name='MA75', line=dict(color='#ffd166', width=1.2)), row=1, col=1)
            if show_bb:
                fig.add_trace(go.Scatter(x=df.index, y=df['BB_upper'], name='BB上限', line=dict(color='rgba(180,100,255,0.6)', width=1, dash='dot')), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df['BB_lower'], name='BB下限', line=dict(color='rgba(180,100,255,0.6)', width=1, dash='dot'), fill='tonexty', fillcolor='rgba(180,100,255,0.05)'), row=1, col=1)

            current_row = 2

            if show_vol:
                colors = ['#00e5a0' if c >= o else '#ff4d6d' for c, o in zip(df['Close'], df['Open'])]
                fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='出来高', marker_color=colors, opacity=0.6), row=current_row, col=1)
                current_row += 1

            if show_rsi:
                fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], name='RSI', line=dict(color='#ff4d6d', width=1.2)), row=current_row, col=1)
                fig.add_hline(y=70, line_dash="dash", line_color="white", opacity=0.3, row=current_row, col=1)
                fig.add_hline(y=30, line_dash="dash", line_color="white", opacity=0.3, row=current_row, col=1)
                fig.update_yaxes(range=[0, 100], row=current_row, col=1)
                current_row += 1

            if show_macd:
                fig.add_trace(go.Scatter(x=df.index, y=df['MACD'], name='MACD', line=dict(color='#00b8ff', width=1.2)), row=current_row, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df['Signal'], name='Signal', line=dict(color='#ffd166', width=1.2)), row=current_row, col=1)
                hist = df['MACD'] - df['Signal']
                fig.add_trace(go.Bar(x=df.index, y=hist, name='Histogram',
                    marker_color=['#00e5a0' if v >= 0 else '#ff4d6d' for v in hist], opacity=0.6), row=current_row, col=1)

            fig.update_yaxes(tickprefix=symbol, tickformat=f",.{decimals}f", row=1, col=1)
            fig.update_layout(
                template='plotly_dark',
                paper_bgcolor='#0d1117',
                plot_bgcolor='#0d1117',
                height=750,
                hovermode='x unified',
                xaxis_rangeslider_visible=False,
                showlegend=True,
                legend=dict(orientation='h', y=1.02, font=dict(size=11)),
                margin=dict(l=0, r=0, t=30, b=0),
            )
            fig.update_yaxes(gridcolor='rgba(255,255,255,0.04)')
            fig.update_xaxes(gridcolor='rgba(255,255,255,0.04)')
            st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error("データ取得に失敗しました。少し待ってから再試行してください。")
        if st.button("再試行"):
            st.rerun()
