import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time

st.set_page_config(page_title="株式分析ツール", page_icon="📈", layout="wide")

st.markdown("""
<style>
/* 全体背景 */
.stApp, .main, .block-container {
    background-color: #0d1117 !important;
    color: #c9d1d9 !important;
}
/* サイドバー */
[data-testid="stSidebar"] {
    background-color: #161b22 !important;
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] div {
    color: #c9d1d9 !important;
}
/* ボタン */
[data-testid="stSidebar"] button {
    background-color: #21262d !important;
    color: #c9d1d9 !important;
    border: 1px solid #30363d !important;
    border-radius: 6px !important;
}
[data-testid="stSidebar"] button:hover {
    border-color: #00e5a0 !important;
    color: #00e5a0 !important;
}
/* セレクトボックス */
[data-testid="stSidebar"] select,
[data-testid="stSidebar"] [data-baseweb="select"] {
    background-color: #21262d !important;
    color: #c9d1d9 !important;
}
/* メトリクス */
[data-testid="metric-container"] {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    padding: 12px !important;
}
[data-testid="stMetricLabel"] { color: #8b949e !important; font-size: 11px !important; }
[data-testid="stMetricValue"] { color: #c9d1d9 !important; font-size: 20px !important; font-weight: 700 !important; }
/* テキスト全般 */
h1, h2, h3, h4, p, span, div { color: #c9d1d9 !important; }
h1 { color: #00e5a0 !important; }
hr { border-color: #30363d !important; }
/* チェックボックス */
[data-testid="stCheckbox"] label { color: #c9d1d9 !important; }
/* テキスト入力 */
input { background-color: #21262d !important; color: #c9d1d9 !important; border-color: #30363d !important; }
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
    st.title("📈 株式分析")
    st.divider()

    main_ticker = st.text_input("🔍 銘柄コード", value="7203.T")

    st.divider()
    st.subheader("⭐ ウォッチリスト")

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
            if not d.empty and len(d) >= 2:
                price = d['Close'].iloc[-1]
                chg = (d['Close'].iloc[-1] - d['Close'].iloc[-2]) / d['Close'].iloc[-2] * 100
                arrow = "▲" if chg >= 0 else "▼"
                color = "#00e5a0" if chg >= 0 else "#ff4d6d"
                st.markdown(f"""
                <div style='background:#21262d;border:1px solid #30363d;border-radius:6px;
                padding:8px 12px;margin-bottom:6px;cursor:pointer'>
                    <div style='font-weight:600;font-size:13px;color:#c9d1d9'>{name}</div>
                    <div style='display:flex;justify-content:space-between;margin-top:2px'>
                        <span style='font-size:12px;color:#8b949e'>{symbol}</span>
                        <span style='font-size:12px;color:{color}'>{arrow} {chg:+.2f}%</span>
                    </div>
                </div>
                """, unsafe_allow_html=True)
        except Exception:
            pass

    st.divider()
    st.subheader("📅 期間・足")
    period_label = st.selectbox("期間", ["1週間","1ヶ月","3ヶ月","6ヶ月","1年","2年","5年"], index=4)
    interval_label = st.selectbox("足の種類", ["日足","週足","月足"], index=0)

    st.divider()
    st.subheader("📐 インジケーター")
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

            latest  = df['Close'].iloc[-1]
            change  = df['Close'].iloc[-1] - df['Close'].iloc[-2]
            pct     = change / df['Close'].iloc[-2] * 100
            rsi_now  = df['RSI'].iloc[-1]
            macd_now = df['MACD'].iloc[-1]
            sig_now  = df['Signal'].iloc[-1]
            ma25_now = df['MA25'].iloc[-1]
            ma75_now = df['MA75'].iloc[-1]

            fmt = f"{{:.{decimals}f}}"

            # ヘッダー
            st.markdown(f"# {main_ticker}")
            st.caption(f"{period_label} / {interval_label}")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("現在値", symbol + fmt.format(latest))
            c2.metric("変化", symbol + fmt.format(change))
            c3.metric("騰落率", f"{pct:.2f}%")
            c4.metric("RSI", f"{rsi_now:.1f}")

            # AIシグナル
            signal, score, reasons = ai_signal(rsi_now, macd_now, sig_now, ma25_now, ma75_now, latest)
            reason_text = "　".join([f"• {r}" for r in reasons])
            st.markdown(f"**AIシグナル: {signal}** (スコア: {score:+d})　{reason_text}")
            st.divider()

            # チャート構成
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
                increasing_line_color='#3fb950',
                decreasing_line_color='#f85149',
            ), row=1, col=1)

            if show_ma25:
                fig.add_trace(go.Scatter(x=df.index, y=df['MA25'], name='MA25',
                    line=dict(color='#58a6ff', width=1.2)), row=1, col=1)
            if show_ma75:
                fig.add_trace(go.Scatter(x=df.index, y=df['MA75'], name='MA75',
                    line=dict(color='#f0883e', width=1.2)), row=1, col=1)
            if show_bb:
                fig.add_trace(go.Scatter(x=df.index, y=df['BB_upper'], name='BB上限',
                    line=dict(color='rgba(188,140,255,0.7)', width=1, dash='dot')), row=1, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df['BB_lower'], name='BB下限',
                    line=dict(color='rgba(188,140,255,0.7)', width=1, dash='dot'),
                    fill='tonexty', fillcolor='rgba(188,140,255,0.05)'), row=1, col=1)

            current_row = 2

            if show_vol:
                vol_colors = ['#3fb950' if c >= o else '#f85149'
                              for c, o in zip(df['Close'], df['Open'])]
                fig.add_trace(go.Bar(x=df.index, y=df['Volume'], name='出来高',
                    marker_color=vol_colors, opacity=0.7), row=current_row, col=1)
                current_row += 1

            if show_rsi:
                fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], name='RSI',
                    line=dict(color='#f85149', width=1.2)), row=current_row, col=1)
                fig.add_hline(y=70, line_dash="dash", line_color="#8b949e", opacity=0.5, row=current_row, col=1)
                fig.add_hline(y=30, line_dash="dash", line_color="#8b949e", opacity=0.5, row=current_row, col=1)
                fig.update_yaxes(range=[0, 100], row=current_row, col=1)
                current_row += 1

            if show_macd:
                fig.add_trace(go.Scatter(x=df.index, y=df['MACD'], name='MACD',
                    line=dict(color='#58a6ff', width=1.2)), row=current_row, col=1)
                fig.add_trace(go.Scatter(x=df.index, y=df['Signal'], name='Signal',
                    line=dict(color='#f0883e', width=1.2)), row=current_row, col=1)
                hist = df['MACD'] - df['Signal']
                fig.add_trace(go.Bar(x=df.index, y=hist, name='Histogram',
                    marker_color=['#3fb950' if v >= 0 else '#f85149' for v in hist],
                    opacity=0.6), row=current_row, col=1)

            fig.update_yaxes(tickprefix=symbol, tickformat=f",.{decimals}f", row=1, col=1)
            fig.update_layout(
                paper_bgcolor='#0d1117',
                plot_bgcolor='#0d1117',
                height=750,
                hovermode='x unified',
                xaxis_rangeslider_visible=False,
                showlegend=True,
                legend=dict(
                    orientation='h', y=1.02,
                    font=dict(size=11, color='#c9d1d9'),
                    bgcolor='rgba(0,0,0,0)',
                ),
                margin=dict(l=0, r=0, t=30, b=0),
                font=dict(color='#c9d1d9'),
            )
            fig.update_yaxes(
                gridcolor='rgba(255,255,255,0.05)',
                tickfont=dict(color='#8b949e'),
                zerolinecolor='rgba(255,255,255,0.1)',
            )
            fig.update_xaxes(
                gridcolor='rgba(255,255,255,0.05)',
                tickfont=dict(color='#8b949e'),
            )
            st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error("データ取得に失敗しました。少し待ってから再試行してください。")
        if st.button("再試行"):
            st.rerun()
