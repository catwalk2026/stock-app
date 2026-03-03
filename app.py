import streamlit as st
import yfinance as yf
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import time

st.set_page_config(page_title="株式分析ツール", page_icon="📈", layout="wide")

st.markdown("""
<style>
.stApp { background-color: #0d1117; color: #e6edf3; }
h1 { font-size: 2rem !important; color: #00e5a0 !important; }
.stTextInput input { background-color: #161b22 !important; color: #e6edf3 !important; border: 1px solid #30363d !important; }
</style>
""", unsafe_allow_html=True)

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

def show_fundamentals(ticker):
    try:
        info = yf.Ticker(ticker).info
        st.markdown("#### 📊 ファンダメンタル情報")
        c1, c2, c3, c4 = st.columns(4)
        per = info.get('trailingPE', None)
        pbr = info.get('priceToBook', None)
        div = info.get('dividendYield', None)
        roe = info.get('returnOnEquity', None)
        mktcap = info.get('marketCap', None)
        currency = info.get('currency', 'JPY')
        symbol = '¥' if currency == 'JPY' else '$'
        c1.metric("PER", f"{per:.1f}x" if per else "N/A")
        c2.metric("PBR", f"{pbr:.2f}x" if pbr else "N/A")
        c3.metric("配当利回り", f"{div:.2f}%" if div else "N/A")
        c4.metric("ROE", f"{roe*100:.1f}%" if roe else "N/A")
        if mktcap:
            st.caption(f"時価総額: {symbol}{mktcap:,.0f}")
    except:
        st.caption("ファンダメンタル情報を取得できませんでした")

def show_news(ticker):
    try:
        st.markdown("#### 📰 最新ニュース＆センチメント")
        stock = yf.Ticker(ticker)
        news = stock.news
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

            # シンプルなキーワードベースのセンチメント判定
            positive_words = ['上昇', '増益', '好調', '最高値', '買い', '上方修正',
                             'beat', 'growth', 'record', 'rise', 'gain', 'up', 'high']
            negative_words = ['下落', '減益', '不振', '最安値', '売り', '下方修正',
                             'miss', 'decline', 'fall', 'drop', 'down', 'loss', 'low']

            title_lower = title.lower()
            if any(w in title_lower for w in positive_words):
                sentiment = "🟢 ポジティブ"
                positive += 1
            elif any(w in title_lower for w in negative_words):
                sentiment = "🔴 ネガティブ"
                negative += 1
            else:
                sentiment = "🟡 中立"
                neutral += 1

            st.markdown(f"**{sentiment}** [{title}]({url})")
            st.caption(f"{source} · {pub_date}")

        # センチメントサマリー
        total = positive + negative + neutral
        if total > 0:
            st.markdown("---")
            st.markdown("**センチメントサマリー**")
            c1, c2, c3 = st.columns(3)
            c1.metric("🟢 ポジティブ", f"{positive}/{total}")
            c2.metric("🟡 中立", f"{neutral}/{total}")
            c3.metric("🔴 ネガティブ", f"{negative}/{total}")

            bull_pct = positive / total
            bear_pct = negative / total
            neut_pct = neutral / total
            bar_html = f"""
            <div style='height:10px;border-radius:5px;overflow:hidden;display:flex;margin-top:8px'>
                <div style='width:{bull_pct*100:.0f}%;background:#00e5a0'></div>
                <div style='width:{neut_pct*100:.0f}%;background:#ffd166'></div>
                <div style='width:{bear_pct*100:.0f}%;background:#ff4d6d'></div>
            </div>
            """
            st.markdown(bar_html, unsafe_allow_html=True)

    except Exception as e:
        st.caption("ニュースを取得できませんでした")

# 銘柄入力
st.subheader("銘柄を入力（最大3つ）")
col1, col2, col3 = st.columns(3)
with col1:
    t1 = st.text_input("銘柄1", value="7203.T")
with col2:
    t2 = st.text_input("銘柄2", value="6758.T")
with col3:
    t3 = st.text_input("銘柄3", value="")

tickers = [t for t in [t1, t2, t3] if t.strip()]

if tickers:
    try:
        with st.spinner("データ取得中..."):
            time.sleep(1)
            data = {}
            for t in tickers:
                stock = yf.Ticker(t)
                df = stock.history(period="1y")
                if not df.empty:
                    df['MA25'] = df['Close'].rolling(25).mean()
                    df['MA75'] = df['Close'].rolling(75).mean()
                    df['RSI'] = calc_rsi(df['Close'])
                    df['MACD'], df['Signal'] = calc_macd(df['Close'])
                    data[t] = df

        if not data:
            st.error("データが取得できませんでした。")
        else:
            if len(data) > 1:
                st.subheader("📊 パフォーマンス比較")
                fig_cmp, ax_cmp = plt.subplots(figsize=(14, 4))
                plt.style.use('dark_background')
                colors = ['#00e5a0', '#00b8ff', '#ffd166']
                for (t, df), color in zip(data.items(), colors):
                    norm = df['Close'] / df['Close'].iloc[0] * 100
                    ax_cmp.plot(df.index, norm, color=color, lw=1.5, label=t)
                ax_cmp.axhline(100, color='white', alpha=0.2, linestyle='--')
                ax_cmp.set_ylabel('相対パフォーマンス（開始=100）')
                ax_cmp.legend()
                ax_cmp.grid(alpha=0.2)
                st.pyplot(fig_cmp)

            for t, df in data.items():
                st.markdown("---")
                st.subheader(f"📈 {t}")

                latest = df['Close'].iloc[-1]
                change = df['Close'].iloc[-1] - df['Close'].iloc[-2]
                pct = change / df['Close'].iloc[-2] * 100
                rsi_now = df['RSI'].iloc[-1]
                macd_now = df['MACD'].iloc[-1]
                sig_now = df['Signal'].iloc[-1]
                ma25_now = df['MA25'].iloc[-1]
                ma75_now = df['MA75'].iloc[-1]

                signal, score, reasons = ai_signal(
                    rsi_now, macd_now, sig_now, ma25_now, ma75_now, latest)

                st.markdown(f"### AIシグナル: {signal}（スコア: {score:+d}）")
                for r in reasons:
                    st.markdown(f"- {r}")

                c1, c2, c3, c4 = st.columns(4)
                c1.metric("現在値", f"{latest:.0f}")
                c2.metric("前日比", f"{change:.0f}")
                c3.metric("騰落率", f"{pct:.2f}%")
                c4.metric("RSI", f"{rsi_now:.1f}")

                show_fundamentals(t)
                show_news(t)

                fig = plt.figure(figsize=(12, 8))
                plt.style.use('dark_background')
                gs = gridspec.GridSpec(3, 1, height_ratios=[3, 1, 1], hspace=0.1)

                ax1 = fig.add_subplot(gs[0])
                ax1.plot(df.index, df['Close'], color='#00e5a0', lw=1.5, label='Close')
                ax1.plot(df.index, df['MA25'], color='#00b8ff', lw=1, label='MA25')
                ax1.plot(df.index, df['MA75'], color='#ffd166', lw=1, label='MA75')
                ax1.legend(loc='upper left', fontsize=8)
                ax1.grid(alpha=0.2)
                ax1.set_ylabel('Price')
                ax1.set_xticklabels([])

                ax2 = fig.add_subplot(gs[1])
                ax2.plot(df.index, df['RSI'], color='#ff4d6d', lw=1.2)
                ax2.axhline(70, color='white', alpha=0.3, linestyle='--', lw=0.8)
                ax2.axhline(30, color='white', alpha=0.3, linestyle='--', lw=0.8)
                ax2.fill_between(df.index, df['RSI'], 70, where=(df['RSI'] >= 70), alpha=0.2, color='#ff4d6d')
                ax2.fill_between(df.index, df['RSI'], 30, where=(df['RSI'] <= 30), alpha=0.2, color='#00e5a0')
                ax2.set_ylabel('RSI')
                ax2.set_ylim(0, 100)
                ax2.grid(alpha=0.2)
                ax2.set_xticklabels([])

                ax3 = fig.add_subplot(gs[2])
                ax3.plot(df.index, df['MACD'], color='#00b8ff', lw=1.2, label='MACD')
                ax3.plot(df.index, df['Signal'], color='#ffd166', lw=1.2, label='Signal')
                ax3.bar(df.index, df['MACD'] - df['Signal'],
                    color=['#00e5a0' if v >= 0 else '#ff4d6d' for v in (df['MACD'] - df['Signal'])],
                    alpha=0.5, width=1)
                ax3.axhline(0, color='white', alpha=0.2)
                ax3.set_ylabel('MACD')
                ax3.legend(loc='upper left', fontsize=8)
                ax3.grid(alpha=0.2)

                st.pyplot(fig)

    except Exception as e:
        st.error("データ取得に失敗しました。少し待ってから再試行してください。")
        if st.button("再試行"):
            st.rerun()
