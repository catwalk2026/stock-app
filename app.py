import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time

st.set_page_config(page_title="株式分析ツール", page_icon="📈", layout="wide")

st.markdown("""
<style>
.stApp, .main, .block-container {
    background-color: #0d1117 !important;
    color: #c9d1d9 !important;
}
[data-testid="stSidebar"] {
    background-color: #161b22 !important;
}
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] span,
[data-testid="stSidebar"] div {
    color: #c9d1d9 !important;
}
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
[data-testid="metric-container"] {
    background-color: #161b22 !important;
    border: 1px solid #30363d !important;
    border-radius: 8px !important;
    padding: 12px !important;
}
[data-testid="stMetricLabel"] { color: #8b949e !important; font-size: 11px !important; }
[data-testid="stMetricValue"] { color: #c9d1d9 !important; font-size: 20px !important; font-weight: 700 !important; }
h1, h2, h3, h4, p, span, div { color: #c9d1d9 !important; }
h1 { color: #00e5a0 !important; }
hr { border-color: #30363d !important; }
[data-testid="stCheckbox"] label { color: #c9d1d9 !important; }
input { background-color: #21262d !important; color: #c9d1d9 !important; border-color: #30363d !important; }
.stTabs [data-baseweb="tab-list"] { background-color: #161b22; border-radius: 8px; padding: 4px; }
.stTabs [data-baseweb="tab"] { color: #8b949e !important; border-radius: 6px; }
.stTabs [aria-selected="true"] { background-color: #21262d !important; color: #c9d1d9 !important; }
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

def show_fundamentals(ticker, symbol):
    try:
        info = yf.Ticker(ticker).info
        st.subheader("📊 バリュエーション")
        c1, c2, c3, c4 = st.columns(4)
        per   = info.get('trailingPE', None)
        pbr   = info.get('priceToBook', None)
        div   = info.get('dividendYield', None)
        roe   = info.get('returnOnEquity', None)
        mktcap = info.get('marketCap', None)
        eps   = info.get('trailingEps', None)
        rev   = info.get('totalRevenue', None)
        margin = info.get('profitMargins', None)
        c1.metric("PER", f"{per:.1f}x" if per else "N/A")
        c2.metric("PBR", f"{pbr:.2f}x" if pbr else "N/A")
        c3.metric("配当利回り", f"{div:.2f}%" if div else "N/A")
        c4.metric("ROE", f"{roe*100:.1f}%" if roe else "N/A")

        st.divider()
        st.subheader("📈 業績")
        c5, c6, c7, c8 = st.columns(4)
        if mktcap:
            if mktcap >= 1_000_000_000_000:
                cap_str = f"{symbol}{mktcap/1_000_000_000_000:.1f}兆" if symbol == '¥' else f"{symbol}{mktcap/1_000_000_000_000:.2f}T"
            elif mktcap >= 100_000_000:
                cap_str = f"{symbol}{mktcap/100_000_000:.0f}億" if symbol == '¥' else f"{symbol}{mktcap/1_000_000_000:.1f}B"
            else:
                cap_str = f"{symbol}{mktcap:,.0f}"
        else:
            cap_str = "N/A"
        c5.metric("時価総額", cap_str)
        c6.metric("EPS", f"{symbol}{eps:.2f}" if eps else "N/A")
        if rev:
            rev_str = f"{symbol}{rev/1_000_000_000_000:.1f}兆" if symbol == '¥' else f"{symbol}{rev/1_000_000_000:.1f}B"
        else:
            rev_str = "N/A"
        c7.metric("売上高", rev_str)
        c8.metric("利益率", f"{margin*100:.1f}%" if margin else "N/A")
    except Exception:
        st.caption("ファンダメンタル情報を取得できませんでした")

def show_news(ticker):
    try:
        news = yf.Ticker(ticker).news
        if not news:
            st.caption("ニュースが見つかりませんでした")
            return
        positive = 0
        negative = 0
        neutral  = 0
        items = []
        for item in news[:10]:
            title    = item.get('content', {}).get('title', '')
            url      = item.get('content', {}).get('canonicalUrl', {}).get('url', '#')
            source   = item.get('content', {}).get('provider', {}).get('displayName', '')
            pub_date = item.get('content', {}).get('pubDate', '')[:10]
            pos_words = ['上昇','増益','好調','最高値','買い','上方修正','beat','growth','record','rise','gain','up','high','rally','surge']
            neg_words = ['下落','減益','不振','最安値','売り','下方修正','miss','decline','fall','drop','down','loss','low','plunge','crash']
            t_lower = title.lower()
            if any(w in t_lower for w in pos_words):
                sentiment = "🟢"
                label = "ポジティブ"
                color = "#3fb950"
                positive += 1
            elif any(w in t_lower for w in neg_words):
                sentiment = "🔴"
                label = "ネガティブ"
                color = "#f85149"
                negative += 1
            else:
                sentiment = "🟡"
                label = "中立"
                color = "#d29922"
                neutral += 1
            items.append((sentiment, label, color, title, url, source, pub_date))

        # センチメントサマリー
        total = positive + negative + neutral
        if total > 0:
            c1, c2, c3 = st.columns(3)
            c1.metric("🟢 ポジティブ", f"{positive}/{total}")
            c2.metric("🟡 中立", f"{neutral}/{total}")
            c3.metric("🔴 ネガティブ", f"{negative}/{total}")
            bar_html = f"""
            <div style='height:8px;border-radius:5px;overflow:hidden;display:flex;margin:8px 0 20px'>
                <div style='width:{positive/total*100:.0f}%;background:#3fb950'></div>
                <div style='width:{neutral/total*100:.0f}%;background:#d29922'></div>
                <div style='width:{negative/total*100:.0f}%;background:#f85149'></div>
            </div>
            """
            st.markdown(bar_html, unsafe_allow_html=True)

        st.divider()
        for sentiment, label, color, title, url, source, pub_date in items:
            st.markdown(f"""
            <div style='padding:12px;margin-bottom:8px;border-left:3px solid {color};background:#161b22;border-radius:0 6px 6px 0'>
                <div style='font-size:11px;color:{color};font-weight:700;margin-bottom:4px'>{sentiment} {label}</div>
                <a href='{url}' target='_blank' style='color:#c9d1d9;text-decoration:none;font-size:13px;font-weight:500'>{title}</a>
                <div style='font-size:11px;color:#8b949e;margin-top:4px'>{source} · {pub_date}</div>
            </div>
            """, unsafe_allow_html=True)
    except Exception:
        st.caption("ニュースを取得できませんでした")

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
                color = "#3fb950" if chg >= 0 else "#f85149"
                st.markdown(f"""
                <div style='background:#21262d;border:1px solid #30363d;border-radius:6px;
                padding:8px 12px;margin-bottom:6px'>
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

            latest   = df['Close'].iloc[-1]
            change   = df['Close'].iloc[-1] - df['Close'].iloc[-2]
            pct      = change / df['Close'].iloc[-2] * 100
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

            signal, score, reasons = ai_signal(rsi_now, macd_now, sig_now, ma25_now, ma75_now, latest)
            reason_text = "　".join([f"• {r}" for r in reasons])
            st.markdown(f"**AIシグナル: {signal}** (スコア: {score:+d})　{reason_text}")
            st.divider()

            # タブ
            tab1, tab2, tab3 = st.tabs(["📈 チャート", "📊 ファンダメンタル", "📰 ニュース"])

            with tab1:
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
                    legend=dict(orientation='h', y=1.02, font=dict(size=11, color='#c9d1d9'), bgcolor='rgba(0,0,0,0)'),
                    margin=dict(l=0, r=0, t=30, b=0),
                    font=dict(color='#c9d1d9'),
                )
                fig.update_yaxes(gridcolor='rgba(255,255,255,0.05)', tickfont=dict(color='#8b949e'), zerolinecolor='rgba(255,255,255,0.1)')
                fig.update_xaxes(gridcolor='rgba(255,255,255,0.05)', tickfont=dict(color='#8b949e'))
                st.plotly_chart(fig, use_container_width=True)

            with tab2:
                show_fundamentals(main_ticker, symbol)

            with tab3:
                show_news(main_ticker)

    except Exception as e:
        st.error("データ取得に失敗しました。少し待ってから再試行してください。")
        if st.button("再試行"):
            st.rerun()
        
