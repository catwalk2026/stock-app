import streamlit as st
import yfinance as yf
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import time

st.set_page_config(page_title="株式分析ツール", page_icon="📈", layout="wide")

st.title("📈 株式分析ツール")

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

def show_fundamentals(ticker):
    try:
        info = yf.Ticker(ticker).info
        symbol, decimals = get_currency(ticker)
        st.markdown("#### 📊 ファンダメンタル情報")
        c1, c2, c3, c4 = st.columns(4)
        per = info.get('trailingPE', None)
        pbr = info.get('priceToBook', None)
        div = info.get('dividendYield', None)
        roe = info.get('returnOnEquity', None)
        mktcap = info.get('marketCap', None)
        c1.metric("PER", f"{per:.1f}x" if per else "N/A")
        c2.metric("PBR", f"{pbr:.2f}x" if pbr else "N/A")
        c3.metric("配当利回り", f"{div:.2f}%" if div else "N/A")
        c4.metric("ROE", f"{roe*100:.1f}%" if roe else "N/A")
        if mktcap:
            if mktcap >= 1_000_000_000_000:
                cap_str = f"{symbol}{mktcap/1_000_000_000_000:.1f}兆" if symbol == '¥' else f"{symbol}{mktcap/1_000_000_000_000:.2f}T"
            elif mktcap >= 100_000_000:
                cap_str = f"{symbol}{mktcap/100_000_000:.0f}億" if symbol == '¥' else f"{symbol}{mktcap/1_000_000_000:.1f}B"
            else:
                cap_str = f"{symbol}{mktcap:,.0f}"
            st.caption(f"時価総額: {cap_str}")
    except Exception:
        st.caption("ファンダメンタル情報を取得できませんでした")

def show_news(ticker):
    try:
        st.markdown("#### 📰 最新ニュース＆センチメント")
        news = yf.Ticker(ticker).news
        if not news:
            st.caption("ニュースが見つかりませんでした")
            return
        positive = 0
        negative = 0
        neutral = 0
        for item in news[:8]:
            title = item.get('content', {}).get('title', '')
            url = item.get('content', {}).get('canonicalUrl', {}).get('url', '#')
            source = item.get('content', {}).get('provider', {}).get('displayName', '')
            pub_date = item.get('content', {}).get('pubDate', '')[:10]
            pos_words = ['上昇','増益','好調','最高値','買い','上方修正','beat','growth','record','rise','gain','up','high']
            neg_words = ['下落','減益','不振','最安値','売り','下方修正','miss','decline','fall','drop','down','loss','low']
            t_lower = title.lower()
            if any(w in t_lower for w in pos_words):
                sentiment = "🟢 ポジティブ"
                positive += 1
            elif any(w in t_lower for w in neg_words):
                sentiment = "🔴 ネガティブ"
                negative += 1
            else:
                sentiment = "🟡 中立"
                neutral += 1
            st.markdown(f"**{sentiment}** [{title}]({url})")
            st.caption(f"{source} · {pub_date}")
        total = positive + negative + neutral
        if total > 0:
            st.markdown("---")
            st.markdown("**センチメントサマリー**")
            c1, c2, c3 = st.columns(3)
            c1.metric("🟢 ポジティブ", f"{positive}/{total}")
            c2.metric("🟡 中立", f"{neutral}/{total}")
            c3.metric("🔴 ネガティブ", f"{negative}/{total}")
            bar_html = f"""
            <div style='height:8px;border-radius:5px;overflow:hidden;display:flex;margin-top:8px'>
                <div style='width:{positive/total*100:.0f}%;background:#00e5a0'></div>
                <div style='width:{neutral/total*100:.0f}%;background:#ffd166'></div>
                <div style='width:{negative/total*100:.0f}%;background:#ff4d6d'></div>
            </div>
            """
            st.markdown(bar_html, unsafe_allow_html=True)
    except Exception:
        st.caption("ニュースを取得できませんでした")

def show_chart(df, ticker, symbol, decimals):
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.6, 0.2, 0.2],
        vertical_spacing=0.03,
        subplot_titles=(f"{ticker} 株価", "RSI", "MACD")
    )
    fig.add_trace(go.Candlestick(
        x=df.index,
        open=df['Open'], high=df['High'],
        low=df['Low'], close=df['Close'],
        name='ローソク足',
        increasing_line_color='#00e5a0',
        decreasing_line_color='#ff4d6d',
    ), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA25'], name='MA25', line=dict(color='#00b8ff', width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA75'], name='MA75', line=dict(color='#ffd166', width=1.2)), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], name='RSI', line=dict(color='#ff4d6d', width=1.2)), row=2, col=1)
    fig.add_hline(y=70, line_dash="dash", line_color="white", opacity=0.3, row=2, col=1)
    fig.add_hline(y=30, line_dash="dash", line_color="white", opacity=0.3, row=2, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MACD'], name='MACD', line=dict(color='#00b8ff', width=1.2)), row=3, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['Signal'], name='Signal', line=dict(color='#ffd166', width=1.2)), row=3, col=1)
    hist = df['MACD'] - df['Signal']
    fig.add_trace(go.Bar(
        x=df.index, y=hist, name='Histogram',
        marker_color=['#00e5a0' if v >= 0 else '#ff4d6d' for v in hist],
        opacity=0.6
    ), row=3, col=1)
    fig.update_yaxes(tickprefix=symbol, tickformat=f",.{decimals}f", row=1, col=1)
    fig.update_layout(
        template='plotly_dark',
        paper_bgcolor='#0d1117',
        plot_bgcolor='#0d1117',
        height=700,
        hovermode='x unified',
        xaxis_rangeslider_visible=False,
        showlegend=True,
        legend=dict(orientation='h', y=1.02, font=dict(size=11)),
    )
    fig.update_yaxes(gridcolor='rgba(255,255,255,0.04)')
    fig.update_xaxes(gridcolor='rgba(255,255,255,0.04)')
    st.plotly_chart(fig, use_container_width=True)

# 銘柄入力
st.subheader("銘柄を入力（最大3つ）")
col1, col2, col3 = st.columns(3)
with col1:
    t1 = st.text_input("銘柄1", value="7203.T")
with col2:
    t2 = st.text_input("銘柄2", value="6758.T")
with col3:
    t3 = st.text_input("銘柄3", value="")

st.subheader("📅 期間・足の設定")
col_a, col_b = st.columns(2)
with col_a:
    period_label = st.selectbox("期間", ["1週間","1ヶ月","3ヶ月","6ヶ月","1年","2年","5年"], index=4)
with col_b:
    interval_label = st.selectbox("足の種類", ["日足","週足","月足"], index=0)

period_map = {"1週間":"5d","1ヶ月":"1mo","3ヶ月":"3mo","6ヶ月":"6mo","1年":"1y","2年":"2y","5年":"5y"}
interval_map = {"日足":"1d","週足":"1wk","月足":"1mo"}
period = period_map[period_label]
interval = interval_map[interval_label]

tickers = [t for t in [t1, t2, t3] if t.strip()]

if tickers:
    try:
        with st.spinner("データ取得中..."):
            time.sleep(1)
            data = {}
            currencies = {}
            for t in tickers:
                df = yf.Ticker(t).history(period=period, interval=interval)
                if not df.empty:
                    df['MA25'] = df['Close'].rolling(25).mean()
                    df['MA75'] = df['Close'].rolling(75).mean()
                    df['RSI'] = calc_rsi(df['Close'])
                    df['MACD'], df['Signal'] = calc_macd(df['Close'])
                    data[t] = df
                    currencies[t] = get_currency(t)

        if not data:
            st.error("データが取得できませんでした。")
        else:
            if len(data) > 1:
                st.subheader("📊 パフォーマンス比較")
                fig_cmp = go.Figure()
                colors = ['#00e5a0', '#00b8ff', '#ffd166']
                for (t, df), color in zip(data.items(), colors):
                    norm = df['Close'] / df['Close'].iloc[0] * 100
                    fig_cmp.add_trace(go.Scatter(x=df.index, y=norm, name=t, line=dict(color=color, width=1.5)))
                fig_cmp.add_hline(y=100, line_dash="dash", line_color="white", opacity=0.2)
                fig_cmp.update_layout(
                    template='plotly_dark',
                    paper_bgcolor='#0d1117',
                    plot_bgcolor='#0d1117',
                    height=300,
                    hovermode='x unified',
                    yaxis_title='相対パフォーマンス（開始=100）',
                )
                st.plotly_chart(fig_cmp, use_container_width=True)

            for t, df in data.items():
                symbol, decimals = currencies[t]
                st.markdown("---")
                st.subheader(f"📈 {t} / {period_label} / {interval_label}")

                latest = df['Close'].iloc[-1]
                change = df['Close'].iloc[-1] - df['Close'].iloc[-2]
                pct = change / df['Close'].iloc[-2] * 100
                rsi_now = df['RSI'].iloc[-1]
                macd_now = df['MACD'].iloc[-1]
                sig_now = df['Signal'].iloc[-1]
                ma25_now = df['MA25'].iloc[-1]
                ma75_now = df['MA75'].iloc[-1]

                signal, score, reasons = ai_signal(rsi_now, macd_now, sig_now, ma25_now, ma75_now, latest)
                st.markdown(f"### AIシグナル: {signal} (スコア: {score:+d})")
                for r in reasons:
                    st.markdown(f"- {r}")

                fmt = f"{{:.{decimals}f}}"
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("現在値", symbol + fmt.format(latest))
                c2.metric(f"{interval_label}の変化", symbol + fmt.format(change))
                c3.metric("騰落率", f"{pct:.2f}%")
                c4.metric("RSI", f"{rsi_now:.1f}")

                show_fundamentals(t)
                show_news(t)
                show_chart(df, t, symbol, decimals)

    except Exception as e:
        st.error("データ取得に失敗しました。少し待ってから再試行してください。")
        if st.button("再試行"):
            st.rerun()
