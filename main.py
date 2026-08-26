from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yfinance as yf
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta, timezone
import requests
import jpholiday
import re
import math
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import csv
import time
import random

# --- LINE連携用ライブラリ ---
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

LINE_CHANNEL_ACCESS_TOKEN = "rlJ1YRFK3hCEYnrfCe5k9kO2gjyX3YkqhfdAvnT28lWoC/9Q6NTtPdBNvGU6jVWunuf7k6NPAg/d2r39X+IxD4mlNjs2bH4krV2B7zWilto5IHSvo7QXkKbIxa0GNvVN2SK9b2AH03Rs/M6VrJBIlwdB04t89/1O/w1cDnyilFU="
LINE_CHANNEL_SECRET = "c8caf38acc62174908dcff1f782621f6"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = FastAPI()

DATABASE2_URL = os.environ.get("DATABASE2_URL") or os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

JPX_STOCKS = []
def load_jpx_stocks():
    global JPX_STOCKS
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.csv"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=10)
        if res.status_code == 200:
            res.encoding = 'shift_jis'
            reader = csv.reader(res.text.splitlines())
            next(reader, None)
            stocks = []
            for row in reader:
                if len(row) >= 3 and row[1].strip() and row[2].strip():
                    stocks.append({"code": row[1].strip(), "name": row[2].strip(), "ticker": f"{row[1].strip()}.T"})
            JPX_STOCKS = stocks
    except: pass

load_jpx_stocks()

POPULAR_FUNDS = [
    {"ticker": "0331418A", "name": "eMAXIS Slim 全世界株式(オール・カントリー)", "keywords": ["オルカン", "emaxis", "slim", "all", "全世界", "カントリー"]},
    {"ticker": "03311187", "name": "eMAXIS Slim 米国株式(S&P500)", "keywords": ["emaxis", "slim", "s&p500", "sp500", "米国"]},
    {"ticker": "89311199", "name": "SBI・V・S&P500インデックス・ファンド", "keywords": ["sbi", "v", "s&p", "sp500"]},
    {"ticker": "89311216", "name": "SBI・V・全米株式インデックス・ファンド", "keywords": ["sbi", "v", "全米", "vti"]},
    {"ticker": "9I312179", "name": "楽天・全米株式インデックス・ファンド(楽天・VTI)", "keywords": ["楽天", "全米", "vti"]},
    {"ticker": "9I311179", "name": "楽天・全世界株式インデックス・ファンド(楽天・VT)", "keywords": ["楽天", "全世界", "vt", "オルカン"]},
    {"ticker": "4731B15C", "name": "たわらノーロード 先進国株式", "keywords": ["たわら", "ノーロード", "先進国"]},
    {"ticker": "47312197", "name": "たわらノーロード 全世界株式", "keywords": ["たわら", "ノーロード", "全世界", "オルカン"]},
    {"ticker": "2931113C", "name": "ニッセイ外国株式インデックスファンド", "keywords": ["ニッセイ", "外国", "インデックス"]},
    {"ticker": "29311041", "name": "ニッセイ日経225インデックスファンド", "keywords": ["ニッセイ", "日経"]},
    {"ticker": "9C311125", "name": "ひふみプラス", "keywords": ["ひふみ", "プラス", "レオス"]},
    {"ticker": "03319172", "name": "eMAXIS Slim 先進国株式インデックス", "keywords": ["emaxis", "slim", "先進国"]},
    {"ticker": "03317172", "name": "eMAXIS Slim 国内株式(TOPIX)", "keywords": ["emaxis", "slim", "国内", "topix"]},
    {"ticker": "03311182", "name": "eMAXIS Slim 国内株式(日経平均)", "keywords": ["emaxis", "slim", "国内", "日経"]},
    {"ticker": "03312175", "name": "eMAXIS Slim バランス(8資産均等型)", "keywords": ["emaxis", "slim", "バランス", "8資産"]},
]

def get_db_connection():
    if not DATABASE2_URL: raise Exception("DB_URL is missing")
    conn = psycopg2.connect(DATABASE2_URL)
    conn.autocommit = True
    return conn

_gemini_model_cache = {"name": None, "checked_at": None}

def get_working_gemini_model():
    now = datetime.now()
    if _gemini_model_cache["name"] and _gemini_model_cache["checked_at"] and (now - _gemini_model_cache["checked_at"]).seconds < 3600:
        return _gemini_model_cache["name"]
    try:
        res = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}", timeout=10)
        if res.status_code == 200:
            candidates = [
                m["name"].replace("models/", "") for m in res.json().get("models", [])
                if "generateContent" in m.get("supportedGenerationMethods", [])
                and "flash" in m["name"] and "lite" not in m["name"]
                and "image" not in m["name"] and "preview" not in m["name"]
            ]
            candidates.sort(reverse=True)
            if candidates:
                _gemini_model_cache["name"] = candidates[0]
                _gemini_model_cache["checked_at"] = now
                return candidates[0]
    except: pass
    return "gemini-3.7-flash"

def get_ai_summary(title: str) -> str:
    if not GEMINI_API_KEY: return "AI機能が未設定です"
    model = get_working_gemini_model()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": f"以下のニュースタイトルから、個人投資家向けの影響を2〜3行で簡潔に要約してください。\nニュースタイトル: {title}"}]}]}
    for attempt in range(3):
        try:
            res = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=10)
            if res.status_code == 200: return res.json().get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "").strip()
            time.sleep(2 ** (attempt + 1))
        except: time.sleep(2 ** (attempt + 1))
    return "要約失敗"

class TradeCreate(BaseModel): ticker: str = ""; name: str; trade_type: str; asset_type: str; trade_date: str; quantity: float; price: float; reason: str = ""
class FundRuleCreate(BaseModel): ticker: str; name: str; frequency: str; monthly_day: int = 1; amount: float; avg_price: float = 10000.0; start_date: str
class PriceUpdate(BaseModel): ticker: str; current_price: float
class WatchlistCreate(BaseModel): ticker: str; name: str
class TargetAllocation(BaseModel): jp_stock: float; us_stock: float; fund: float

def init_db():
    if not DATABASE2_URL: return
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolio (user_id TEXT, ticker TEXT, name TEXT, quantity REAL, average_price REAL, manual_price REAL, PRIMARY KEY (user_id, ticker));
            CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, user_id TEXT, ticker TEXT, type TEXT, trade_date TEXT, quantity REAL, price REAL, reason TEXT);
            CREATE TABLE IF NOT EXISTS fund_rules (id SERIAL PRIMARY KEY, user_id TEXT, ticker TEXT, name TEXT, frequency TEXT, monthly_day INTEGER, amount REAL, start_date TEXT);
            CREATE TABLE IF NOT EXISTS watchlist (user_id TEXT, ticker TEXT, name TEXT, added_date TEXT, PRIMARY KEY (user_id, ticker));
            CREATE TABLE IF NOT EXISTS line_users (line_user_id TEXT PRIMARY KEY, app_user_id TEXT, is_news_active BOOLEAN DEFAULT TRUE);
            CREATE TABLE IF NOT EXISTS sent_news (line_user_id TEXT, news_link TEXT, PRIMARY KEY (line_user_id, news_link));
            CREATE TABLE IF NOT EXISTS asset_cache (ticker TEXT PRIMARY KEY, price REAL, div_yield REAL, last_updated TEXT, earnings_date TEXT);
            CREATE TABLE IF NOT EXISTS sent_alerts (line_user_id TEXT, ticker TEXT, alert_date TEXT, PRIMARY KEY (line_user_id, ticker, alert_date));
            CREATE TABLE IF NOT EXISTS sent_calendar_alerts (line_user_id TEXT, event_id TEXT, PRIMARY KEY (line_user_id, event_id));
            CREATE TABLE IF NOT EXISTS sent_earnings_alerts (line_user_id TEXT, ticker TEXT, earnings_date TEXT, PRIMARY KEY (line_user_id, ticker, earnings_date));
            CREATE TABLE IF NOT EXISTS target_allocations (user_id TEXT PRIMARY KEY, jp_stock REAL, us_stock REAL, fund REAL);
        ''')
        try: cursor.execute("ALTER TABLE asset_cache ADD COLUMN IF NOT EXISTS earnings_date TEXT")
        except: pass
        cursor.close(); conn.close()
    except: pass

init_db()

def get_usdjpy_rate():
    today_str = datetime.now().strftime("%Y-%m-%d")
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT price FROM asset_cache WHERE ticker = 'USDJPY' AND last_updated = %s", (today_str,))
        row = cursor.fetchone()
        if row and row["price"] > 100:
            cursor.close(); conn.close(); return {"rate": row["price"], "time": f"{today_str} (取得済)"}
    except: pass
        
    rate = 155.0; fetch_time = "前回取得値"
    try:
        res = requests.get("https://open.er-api.com/v6/latest/USD", timeout=3)
        if res.status_code == 200 and res.json().get("rates", {}).get("JPY", 0) > 100:
            rate = float(res.json()["rates"]["JPY"]); fetch_time = datetime.now().strftime("%Y/%m/%d %H:%M")
    except: pass
        
    try:
        cursor.execute("INSERT INTO asset_cache (ticker, price, div_yield, last_updated) VALUES ('USDJPY', %s, 0.0, %s) ON CONFLICT (ticker) DO UPDATE SET price = EXCLUDED.price, last_updated = EXCLUDED.last_updated", (rate, today_str))
        cursor.close(); conn.close()
    except: pass
    return {"rate": rate, "time": fetch_time}

def get_asset_data(ticker: str, is_jpy: bool, is_fund: bool):
    ticker = ticker.strip().upper()
    today_str = datetime.now().strftime("%Y-%m-%d-v18")
    price = 0.0; div_yield = 0.0; row = None; conn = None; cursor = None
    old_div_yield = 0.0 
    
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT price, div_yield, last_updated FROM asset_cache WHERE ticker = %s", (ticker,))
        row = cursor.fetchone()
        if row:
            if row["div_yield"] > 0: old_div_yield = row["div_yield"]
            if row["last_updated"] == today_str and row["price"] > 0:
                cursor.close(); conn.close(); return row["price"], row["div_yield"]
    except: pass
        
    if not is_fund:
        try:
            stock_ticker = ticker if not is_jpy else (f"{ticker}.T" if (len(ticker)==4 and ticker.isalnum()) else ticker)
            stock = yf.Ticker(stock_ticker)
            hist = stock.history(period="5d")
            if not hist.empty:
                last_valid_price = hist['Close'].dropna()
                if not last_valid_price.empty: price = float(last_valid_price.iloc[-1])

            if price > 0:
                try:
                    divs = stock.dividends
                    if not divs.empty:
                        divs.index = divs.index.tz_localize(None)
                        recent_divs = divs[divs.index >= (datetime.now() - timedelta(days=365))]
                        if not recent_divs.empty and float(recent_divs.sum()) > 0:
                            div_yield = (float(recent_divs.sum()) / price) * 100.0
                except: pass

            if div_yield <= 0.0:
                try:
                    info = stock.info
                    if info:
                        if info.get("dividendYield") is not None:
                            raw_y = float(info["dividendYield"])
                            div_yield = raw_y * 100.0 if raw_y < 1.0 else raw_y
                        elif info.get("trailingAnnualDividendYield") is not None:
                            raw_y = float(info["trailingAnnualDividendYield"])
                            div_yield = raw_y * 100.0 if raw_y < 1.0 else raw_y
                except: pass
        except: pass

    if (math.isnan(div_yield) or div_yield <= 0.0 or div_yield > 20.0) and old_div_yield > 0.0: 
        div_yield = old_div_yield
    elif math.isnan(div_yield) or div_yield > 20.0:
        div_yield = 0.0

    if price == 0.0 and row: price = row["price"] 

    if price > 0:
        try:
            if conn and cursor:
                cursor.execute("INSERT INTO asset_cache (ticker, price, div_yield, last_updated) VALUES (%s, %s, %s, %s) ON CONFLICT (ticker) DO UPDATE SET price = EXCLUDED.price, div_yield = EXCLUDED.div_yield, last_updated = EXCLUDED.last_updated", (ticker, price, div_yield, today_str))
                cursor.close(); conn.close()
        except: pass
    return price, div_yield

def get_earnings_date(ticker: str, is_jpy: bool):
    try:
        stock = yf.Ticker(ticker if not is_jpy else f"{ticker.replace('.T', '')}.T")
        cal = stock.calendar
        if cal and "Earnings Date" in cal and len(cal["Earnings Date"]) > 0:
            e_date = cal["Earnings Date"][0]
            if pd.notnull(e_date): return e_date.strftime("%Y/%m/%d")
    except: pass
    return None

def fetch_economic_events():
    target_urls = [
        "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
        "https://nfs.faireconomy.media/ff_calendar_nextweek.json"
    ]
    all_events = []
    headers = {"User-Agent": "Mozilla/5.0"}
    
    for u in target_urls:
        try_routes = [u, f"https://api.allorigins.win/raw?url={urllib.parse.quote(u)}"]
        for route in try_routes:
            try:
                res = requests.get(route, headers=headers, timeout=5)
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, list):
                        all_events.extend(data)
                        break 
            except: pass
    return all_events

# ==========================================
# 定期実行タスク群
# ==========================================
def check_and_send_news():
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM line_users WHERE is_news_active = TRUE")
        users = cursor.fetchall()
        for user in users:
            line_user_id = user["line_user_id"]; app_user_id = user["app_user_id"]
            if not app_user_id: continue
            news_data = get_jp_news(app_user_id)
            if not news_data: continue
            
            cursor.execute("SELECT news_link FROM sent_news WHERE line_user_id = %s", (line_user_id,))
            sent_links = {row["news_link"] for row in cursor.fetchall()}
            
            new_articles = []; market_count = 0; stock_count = 0
            for n in news_data:
                if n["link"] not in sent_links:
                    if n["stock_name"] == "主要市況":
                        if market_count < 1: new_articles.append(n); market_count += 1
                    else:
                        if stock_count < 4: new_articles.append(n); stock_count += 1
                    if len(new_articles) >= 5: break
                        
            if new_articles:
                msg = "🔔 【定期配信】最新ニュースが届きました！\n\n"
                for n in new_articles:
                    icon = "🌍" if n["stock_name"] == "主要市況" else "📰"
                    msg += f"{icon} 【{n['stock_name']}】\n{n['title']}\n{n['link']}\n\n"
                msg += f"👇 AI要約はダッシュボードから✨\nhttps://stock-app-xyif.onrender.com/{app_user_id}"
                try:
                    line_bot_api.push_message(line_user_id, TextSendMessage(text=msg))
                    for n in new_articles: cursor.execute("INSERT INTO sent_news (line_user_id, news_link) VALUES (%s, %s) ON CONFLICT DO NOTHING", (line_user_id, n["link"]))
                except: pass
        cursor.close(); conn.close()
    except: pass

def check_and_send_price_alerts():
    try:
        today_str = datetime.now(timezone(timedelta(hours=9))).strftime("%Y-%m-%d")
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM line_users WHERE is_news_active = TRUE")
        users = cursor.fetchall()
        price_change_cache = {}

        for user in users:
            line_user_id = user["line_user_id"]; app_user_id = user["app_user_id"]
            if not app_user_id: continue

            cursor.execute("SELECT ticker, name FROM portfolio WHERE user_id = %s", (app_user_id,))
            p_items = cursor.fetchall()
            cursor.execute("SELECT ticker, name FROM watchlist WHERE user_id = %s", (app_user_id,))
            w_items = cursor.fetchall()
            target_items = {item["ticker"]: item["name"] for item in (p_items + w_items)}
            alerts_to_send = []
            
            for ticker, name in target_items.items():
                if len(ticker) == 8 and ticker.isalnum(): continue
                if ticker not in price_change_cache:
                    stock_ticker = f"{ticker}.T" if (len(ticker)==4 and ticker.isalnum() and not ticker.endswith(".T")) else ticker
                    try:
                        hist = yf.Ticker(stock_ticker).history(period="5d")
                        if len(hist) >= 2:
                            prev_close = float(hist['Close'].iloc[-2]); curr_price = float(hist['Close'].iloc[-1])
                            price_change_cache[ticker] = {"curr": curr_price, "pct": ((curr_price - prev_close) / prev_close) * 100}
                        else: price_change_cache[ticker] = None
                    except: price_change_cache[ticker] = None

                change_data = price_change_cache[ticker]
                if change_data and abs(change_data["pct"]) >= 5.0:
                    cursor.execute("SELECT 1 FROM sent_alerts WHERE line_user_id = %s AND ticker = %s AND alert_date = %s", (line_user_id, ticker, today_str))
                    if not cursor.fetchone(): alerts_to_send.append({"ticker": ticker, "name": name, "curr": change_data["curr"], "pct": change_data["pct"]})
            
            if alerts_to_send:
                msg = "⚠️ 【急変動アラート】\n登録銘柄に大きな動きがありました！\n\n"
                for a in alerts_to_send:
                    icon = "📈 急騰" if a["pct"] > 0 else "📉 急落"
                    msg += f"{icon}: {a['name']}\n変動: {'+' if a['pct'] > 0 else ''}{a['pct']:.1f}%\n現在値: {a['curr']:,.1f}\n\n"
                msg += f"👇 ダッシュボードで確認✨\nhttps://stock-app-xyif.onrender.com/{app_user_id}"
                try:
                    line_bot_api.push_message(line_user_id, TextSendMessage(text=msg))
                    for a in alerts_to_send: cursor.execute("INSERT INTO sent_alerts (line_user_id, ticker, alert_date) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (line_user_id, a["ticker"], today_str))
                except: pass
        cursor.close(); conn.close()
    except: pass

def check_and_send_economic_alerts():
    try:
        events = fetch_economic_events()
        if not events: return
        now_jst = datetime.now(timezone(timedelta(hours=9)))
        country_flags = {"USD": "🇺🇸 米国", "JPY": "🇯🇵 日本"}

        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT line_user_id, app_user_id FROM line_users WHERE is_news_active = TRUE")
        users = cursor.fetchall()

        for ev in events:
            if ev.get("country") not in ["USD", "JPY"] or ev.get("impact") != "High" or not ev.get("date"): continue
            try: dt_jst = datetime.fromisoformat(ev.get("date")).astimezone(timezone(timedelta(hours=9)))
            except: continue

            diff_minutes = (dt_jst - now_jst).total_seconds() / 60
            if 10 <= diff_minutes <= 70:
                event_id = f"{ev.get('country')}_{ev.get('title')}_{dt_jst.strftime('%Y%m%d%H%M')}"
                title_jp = translate_title(ev.get("title", ""))
                flag = country_flags.get(ev.get("country"), "🌐")
                for u in users:
                    cursor.execute("SELECT 1 FROM sent_calendar_alerts WHERE line_user_id = %s AND event_id = %s", (u["line_user_id"], event_id))
                    if not cursor.fetchone():
                        msg = f"🚨 【重要指標アラート】\nまもなく重要指標が発表されます！\n\n📊 指標: {flag} {title_jp}\n⏰ 時刻: {dt_jst.strftime('%H:%M')}\n\n👇 ダッシュボードで確認✨\nhttps://stock-app-xyif.onrender.com/{u['app_user_id']}"
                        try:
                            line_bot_api.push_message(u["line_user_id"], TextSendMessage(text=msg))
                            cursor.execute("INSERT INTO sent_calendar_alerts (line_user_id, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (u["line_user_id"], event_id))
                        except: pass
        cursor.close(); conn.close()
    except: pass

def check_and_send_earnings_alerts():
    try:
        now_jst = datetime.now(timezone(timedelta(hours=9)))
        today_date = now_jst.date()
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM line_users WHERE is_news_active = TRUE")
        users = cursor.fetchall()
        earnings_cache = {}

        for user in users:
            line_user_id = user["line_user_id"]; app_user_id = user["app_user_id"]
            if not app_user_id: continue

            cursor.execute("SELECT ticker, name FROM portfolio WHERE user_id = %s", (app_user_id,))
            p_items = cursor.fetchall()
            target_items = {item["ticker"]: item["name"] for item in p_items}
            alerts_to_send = []
            
            for ticker, name in target_items.items():
                is_fund = (len(ticker) == 8 and ticker.isalnum()) or "投信" in name
                if is_fund: continue
                is_jpy = ticker.endswith(".T") or (len(ticker)==4 and ticker.isalnum())
                
                if ticker not in earnings_cache:
                    earnings_cache[ticker] = get_earnings_date(ticker, is_jpy)

                e_date_str = earnings_cache[ticker]
                if e_date_str:
                    try:
                        e_date = datetime.strptime(e_date_str, "%Y/%m/%d").date()
                        diff_days = (e_date - today_date).days
                        if 0 <= diff_days <= 3:
                            cursor.execute("SELECT 1 FROM sent_earnings_alerts WHERE line_user_id = %s AND ticker = %s AND earnings_date = %s", (line_user_id, ticker, e_date_str))
                            if not cursor.fetchone():
                                alerts_to_send.append({"name": name, "date": e_date_str, "days": diff_days})
                                cursor.execute("INSERT INTO sent_earnings_alerts (line_user_id, ticker, earnings_date) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (line_user_id, ticker, e_date_str))
                    except: pass

            if alerts_to_send:
                msg = "📅 【決算発表アラート】\n保有銘柄の決算発表が迫っています！\n\n"
                for a in alerts_to_send:
                    timing = "本日！" if a["days"] == 0 else f"あと{a['days']}日"
                    msg += f"🏢 {a['name']}\n📆 発表予定: {a['date']} ({timing})\n\n"
                msg += f"決算前後の株価変動にご注意ください。\nhttps://stock-app-xyif.onrender.com/{app_user_id}"
                try: line_bot_api.push_message(line_user_id, TextSendMessage(text=msg))
                except: pass
        cursor.close(); conn.close()
    except: pass

scheduler = BackgroundScheduler()
scheduler.add_job(check_and_send_news, 'interval', minutes=60)
scheduler.add_job(check_and_send_price_alerts, 'interval', minutes=60)
scheduler.add_job(check_and_send_economic_alerts, 'interval', minutes=60) 
scheduler.add_job(check_and_send_earnings_alerts, 'cron', hour=8, minute=0)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    try: handler.handle(body.decode("utf-8"), x_line_signature)
    except: raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    line_user_id = event.source.user_id
    if "会員連携" in text:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ABCashのアプリで使っている【6桁の会員ID（英数字）】をそのままメッセージで送信してください！🔑"))
    elif "最新ニュース" in text:
        try:
            conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT app_user_id FROM line_users WHERE line_user_id = %s", (line_user_id,))
            row = cursor.fetchone(); cursor.close(); conn.close()
            if row:
                user_id = row['app_user_id']; news_data = get_jp_news(user_id)
                if news_data:
                    msg = "🚀 保有銘柄と市況の最新ニュースです！\n\n"; new_articles = []; market_count = 0; stock_count = 0
                    for n in news_data:
                        if n["stock_name"] == "主要市況":
                            if market_count < 1: new_articles.append(n); market_count += 1
                        else:
                            if stock_count < 4: new_articles.append(n); stock_count += 1
                        if len(new_articles) >= 5: break
                    for n in new_articles: msg += f"{'🌍' if n['stock_name'] == '主要市況' else '📰'} 【{n['stock_name']}】\n{n['title']}\n{n['link']}\n\n"
                    msg += f"👇 さらに詳しいニュースやAI要約はダッシュボードから✨\nhttps://stock-app-xyif.onrender.com/{user_id}"
                else: msg = f"現在、新しいニュースはありません。\n\n👇 ダッシュボードはこちら✨\nhttps://stock-app-xyif.onrender.com/{user_id}"
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
            else: line_bot_api.reply_message(event.reply_token, TextSendMessage(text="ニュースをお届けするために、まずは「🔑 会員連携」からIDを登録してくださいね！"))
        except: pass
    elif "通知" in text:
        try:
            conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT is_news_active FROM line_users WHERE line_user_id = %s", (line_user_id,))
            row = cursor.fetchone()
            if row:
                new_status = not row["is_news_active"]
                cursor.execute("UPDATE line_users SET is_news_active = %s WHERE line_user_id = %s", (new_status, line_user_id))
                msg = "🔔 【通知：ON】\n最新ニュース、急変動、重要経済指標、決算アラートをお知らせします！✨" if new_status else "🔕 【通知：OFF】\n定期通知をストップしました。またいつでもONにできます！"
            else: msg = "通知を受け取るために、まずは「🔑 会員連携」からIDを登録してくださいね！"
            cursor.close(); conn.close()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except: pass
    elif "ダッシュボード" in text:
        try:
            conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT app_user_id FROM line_users WHERE line_user_id = %s", (line_user_id,))
            row = cursor.fetchone(); cursor.close(); conn.close()
            msg = f"おかえりなさい！📈✨\n👇こちらから最新の資産状況をチェックできます。\n\nhttps://stock-app-xyif.onrender.com/{row['app_user_id']}" if row else "まずは「🔑 会員連携」からIDを登録してくださいね！"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=msg))
        except: pass
    elif re.match(r"^[a-zA-Z0-9]{6}$", text):
        try:
            conn = get_db_connection(); cursor = conn.cursor()
            cursor.execute("INSERT INTO line_users (line_user_id, app_user_id, is_news_active) VALUES (%s, %s, TRUE) ON CONFLICT (line_user_id) DO UPDATE SET app_user_id = EXCLUDED.app_user_id", (line_user_id, text))
            cursor.close(); conn.close()
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"🎉 連携完了！\n\n会員ID【{text}】で連携しました！\nさっそく「ダッシュボード」を開いてみましょう✨"))
        except: pass

@app.get("/api/ai_summary")
def api_ai_summary(title: str): return {"summary": get_ai_summary(title)}

@app.get("/api/{user_id}/news")
def get_jp_news(user_id: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT ticker, name FROM portfolio WHERE user_id = %s AND quantity > 0", (user_id,))
        p_items = cursor.fetchall()
        cursor.execute("SELECT ticker, name FROM watchlist WHERE user_id = %s", (user_id,))
        w_items = cursor.fetchall()
        cursor.close(); conn.close()
        
        target_items = {item["ticker"]: item["name"] for item in (p_items + w_items)}
        news_list = []
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        for ticker, name in target_items.items():
            if len(ticker) == 8 and ticker.isalnum(): continue
            search_term = urllib.parse.quote(f"{name} 株")
            base_url = f"https://news.google.com/rss/search?q={search_term}&hl=ja&gl=JP&ceid=JP:ja"
            
            try_urls = [base_url, f"https://api.allorigins.win/raw?url={urllib.parse.quote(base_url)}"]
            
            for u in try_urls:
                try:
                    res = requests.get(u, headers=headers, timeout=5)
                    if res.status_code == 200 and "<?xml" in res.text:
                        root = ET.fromstring(res.text)
                        for item in root.findall('.//item')[:3]:
                            try:
                                dt_utc = parsedate_to_datetime(item.find('pubDate').text)
                                dt_jst = dt_utc.astimezone(timezone(timedelta(hours=9))) if dt_utc.tzinfo else dt_utc.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=9)))
                                clean_link = item.find('link').text.replace("news.google.com/rss/articles/", "news.google.com/articles/")
                                news_list.append({"stock_name": name, "title": item.find('title').text, "link": clean_link, "pub_time": dt_jst.strftime("%Y/%m/%d %H:%M"), "timestamp": dt_jst.timestamp()})
                            except: pass
                        break 
                except: pass

        market_term = urllib.parse.quote("日経平均")
        market_url = f"https://news.google.com/rss/search?q={market_term}&hl=ja&gl=JP&ceid=JP:ja"
        market_try_urls = [market_url, f"https://api.allorigins.win/raw?url={urllib.parse.quote(market_url)}"]
        
        for u in market_try_urls:
            try:
                res = requests.get(u, headers=headers, timeout=5)
                if res.status_code == 200 and "<?xml" in res.text:
                    root = ET.fromstring(res.text)
                    for item in root.findall('.//item')[:5]:
                        try:
                            dt_utc = parsedate_to_datetime(item.find('pubDate').text)
                            dt_jst = dt_utc.astimezone(timezone(timedelta(hours=9))) if dt_utc.tzinfo else dt_utc.replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=9)))
                            clean_link = item.find('link').text.replace("news.google.com/rss/articles/", "news.google.com/articles/")
                            news_list.append({"stock_name": "主要市況", "title": item.find('title').text, "link": clean_link, "pub_time": dt_jst.strftime("%Y/%m/%d %H:%M"), "timestamp": dt_jst.timestamp()})
                        except: pass
                    break
            except: pass
            
        news_list.sort(key=lambda x: x["timestamp"], reverse=True)
        return news_list[:30]
    except: return []

TRANS_DICT = {"Prelim GDP Price Index": "GDPデフレーター(速報)", "Prelim GDP": "GDP(速報値)", "Revised Industrial Production": "鉱工業生産(改定値)", "Tertiary Industry Activity": "第3次産業活動指数", "Core Machinery Orders": "コア機械受注", "Machinery Orders": "機械受注", "National Core CPI": "全国コアCPI", "National CPI": "全国CPI", "Flash Manufacturing PMI": "製造業PMI(速報値)", "Flash Services PMI": "サービス業PMI(速報値)", "FOMC Meeting Minutes": "FOMC議事録", "FOMC Member": "FRB高官", "Philly Fed Manufacturing Index": "フィラデルフィア連銀製造業景気指数", "Philly FRB Manufacturing Index": "フィラデルフィア連銀製造業景気指数", "Unemployment Claims": "新規失業保険申請件数", "Trade Balance": "貿易収支", "Current Account": "経常収支", "Retail Sales": "小売売上高", "Industrial Production": "鉱工業生産", "Consumer Price Index": "消費者物価指数", "Bank Lending": "銀行融資", "Economy Watchers Sentiment": "景気ウォッチャー調査", "Non-Farm Employment Change": "非農業部門雇用者数", "Unemployment Rate": "失業率", "Core CPI": "コアCPI", "CPI": "消費者物価指数(CPI)", "PPI": "生産者物価指数(PPI)", "PMI": "購買担当者景気指数(PMI)", "GDP": "GDP", "Fed": "FRB", "BOJ": "日銀", "Policy Rate": "政策金利発表"}
SUFFIX_TRANS = [(r"\by/y\b", "（前年比）"), (r"\bq/q\b", "（前期比）"), (r"\bm/m\b", "（前月比）"), (r"\bw/w\b", "（前週比）")]

def translate_title(title: str) -> str:
    for eng, jp in sorted(TRANS_DICT.items(), key=lambda x: -len(x[0])): title = re.sub(re.escape(eng), jp, title, flags=re.IGNORECASE)
    for pattern, jp in SUFFIX_TRANS: title = re.sub(pattern, jp, title, flags=re.IGNORECASE)
    return title.strip()

# 🌟 カレンダーキャッシュを復活
_calendar_cache = {"data": None, "checked_at": None}

def get_economic_calendar():
    global _calendar_cache
    now = datetime.now()
    if _calendar_cache["data"] is not None and _calendar_cache["checked_at"] and (now - _calendar_cache["checked_at"]).seconds < 3600:
        return _calendar_cache["data"]

    days_jp = ["月", "火", "水", "木", "金", "土", "日"]
    country_flags = {"USD": "🇺🇸 米", "JPY": "🇯🇵 日"}
    
    try:
        raw_events = fetch_economic_events()
        if not raw_events: 
            return _calendar_cache["data"] or []

        calendar_dict = {}
        now_jst = datetime.now(timezone(timedelta(hours=9)))

        for ev in raw_events:
            country = ev.get("country", "")
            if country not in ["USD", "JPY"]: continue
            impact = ev.get("impact", "")
            if impact == "Holiday" or (country == "USD" and impact not in ["High", "Medium"]) or (country == "JPY" and impact not in ["High", "Medium", "Low"]): continue

            try: dt_jst = datetime.fromisoformat(ev.get("date")).astimezone(timezone(timedelta(hours=9)))
            except: continue

            if dt_jst.date() < (now_jst - timedelta(days=6)).date(): continue

            title = translate_title(ev.get("title", ""))
            d_key = dt_jst.strftime("%m/%d").lstrip("0").replace("/0", "/")
            day_str = days_jp[dt_jst.weekday()]
            sort_key = dt_jst.strftime("%Y%m%d")

            if d_key not in calendar_dict:
                bg_color, text_color = "bg-[#F8F6ED]", "text-[#2F3842]"
                if dt_jst.weekday() == 5: text_color = "text-[#4984BD]"
                elif dt_jst.weekday() == 6: bg_color, text_color = "bg-[#F77261]", "text-white"
                calendar_dict[d_key] = {"date": d_key, "day": day_str, "bg": bg_color, "text": text_color, "events": [], "sort_key": sort_key}

            calendar_dict[d_key]["events"].append({"flag": country_flags.get(country, "🌐"), "title": f"{title} ({dt_jst.strftime('%H:%M')})", "isRed": (impact == "High"), "isHtml": False, "isEarnings": False})

        sorted_vals = sorted(calendar_dict.values(), key=lambda x: x["sort_key"])
        _calendar_cache["data"] = sorted_vals
        _calendar_cache["checked_at"] = now
        return _calendar_cache["data"]
    except: 
        return _calendar_cache["data"] or []

@app.get("/api/{user_id}/calendar")
def get_user_calendar(user_id: str):
    eco_data = get_economic_calendar()
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT ticker, name FROM portfolio WHERE user_id = %s", (user_id,))
    p_items = cursor.fetchall()
    cursor.execute("SELECT ticker, name FROM watchlist WHERE user_id = %s", (user_id,))
    w_items = cursor.fetchall()
    
    target_items = {item["ticker"]: item["name"] for item in (p_items + w_items)}
    earnings_events = []
    now_jst = datetime.now(timezone(timedelta(hours=9)))
    
    for ticker, name in target_items.items():
        is_fund = (len(ticker) == 8 and ticker.isalnum()) or "投信" in name
        if is_fund: continue
        is_jpy = ticker.endswith(".T") or (len(ticker)==4 and ticker.isalnum())
        
        try:
            cursor.execute("SELECT earnings_date FROM asset_cache WHERE ticker = %s", (ticker,))
            row = cursor.fetchone()
            e_date_str = row["earnings_date"] if row and "earnings_date" in row else None
        except: e_date_str = None
            
        if not e_date_str:
            e_date_str = get_earnings_date(ticker, is_jpy)
            if e_date_str:
                try: cursor.execute("UPDATE asset_cache SET earnings_date = %s WHERE ticker = %s", (e_date_str, ticker))
                except: pass
        
        if e_date_str:
            try:
                e_date = datetime.strptime(e_date_str, "%Y/%m/%d").date()
                if (now_jst.date() - timedelta(days=6)) <= e_date <= (now_jst.date() + timedelta(days=30)):
                    d_key = e_date.strftime("%m/%d").lstrip("0").replace("/0", "/")
                    sort_key = e_date.strftime("%Y%m%d")
                    earnings_events.append({
                        "date_key": d_key, "sort_key": sort_key,
                        "event": {"flag": "🏢", "title": f"【決算発表】{name}", "isRed": False, "isHtml": False, "isEarnings": True}
                    })
            except: pass
    cursor.close(); conn.close()
    
    merged_dict = {d["sort_key"]: d for d in eco_data}
    days_jp = ["月", "火", "水", "木", "金", "土", "日"]
    
    for ee in earnings_events:
        s_key = ee["sort_key"]
        if s_key not in merged_dict:
            e_date = datetime.strptime(s_key, "%Y%m%d")
            d_key = e_date.strftime("%m/%d").lstrip("0").replace("/0", "/")
            day_str = days_jp[e_date.weekday()]
            bg_color, text_color = "bg-[#F8F6ED]", "text-[#2F3842]"
            if e_date.weekday() == 5: text_color = "text-[#4984BD]"
            elif e_date.weekday() == 6: bg_color, text_color = "bg-[#F77261]", "text-white"
            merged_dict[s_key] = {"date": d_key, "day": day_str, "bg": bg_color, "text": text_color, "events": [], "sort_key": s_key}
            
        merged_dict[s_key]["events"].insert(0, ee["event"])
        
    return sorted(merged_dict.values(), key=lambda x: x["sort_key"])

@app.get("/")
def read_root(): return FileResponse("index.html")

@app.get("/admin")
def read_admin(): return FileResponse("admin.html")

@app.get("/{user_id}")
def read_user_dashboard(user_id: str):
    if re.match(r"^[a-zA-Z0-9]{6}$", user_id): return FileResponse("index.html")
    raise HTTPException(status_code=404, detail="Error")

@app.get("/api/search_stock")
def search_stock(q: str, asset_type: str = "ALL"):
    if not q: return []
    results = []; q_str = q.strip().lower()
    headers = {"User-Agent": "Mozilla/5.0"}
    if asset_type in ["JP", "ALL"]:
        for item in JPX_STOCKS:
            if q_str in item["code"].lower() or q_str in item["name"].lower():
                results.append({"ticker": item["ticker"], "name": item["name"]})
                if len(results) >= 8: break
        if len(results) < 8:
            try:
                res = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=8&country=JP", headers=headers, timeout=3)
                if res.status_code == 200:
                    for quote in res.json().get("quotes", []):
                        code = quote.get("symbol", "")
                        if code.endswith(".T") and not any(r["ticker"] == code for r in results): results.append({"ticker": code, "name": quote.get("shortname", quote.get("longname", code))})
            except: pass
        if results and asset_type == "JP": return results[:8]

    if asset_type in ["US", "ALL"] and len(results) < 8:
        try:
            res = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=8&country=US", headers=headers, timeout=3)
            if res.status_code == 200:
                for quote in res.json().get("quotes", []):
                    ticker = quote.get("symbol", "")
                    if not ticker.endswith(".T") and not any(r["ticker"] == ticker for r in results): results.append({"ticker": ticker, "name": quote.get("shortname", quote.get("longname", ticker))})
        except: pass

    if asset_type in ["FUND", "ALL"] and len(results) < 8:
        search_terms = q_str.replace(" ", " ").split()
        for fund in POPULAR_FUNDS:
            if all(t in (fund["name"].lower() + " " + " ".join(fund["keywords"])) for t in search_terms):
                if not any(r["ticker"] == fund["ticker"] for r in results): results.append({"ticker": fund["ticker"], "name": fund["name"]})
                if len(results) >= 8: break
    return results[:8]

@app.post("/api/{user_id}/update_price")
def update_price(user_id: str, data: PriceUpdate):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("UPDATE portfolio SET manual_price = %s WHERE user_id = %s AND ticker = %s", (data.current_price, user_id, data.ticker))
        cursor.close(); conn.close()
    except: pass
    return {"message": "Success"}

@app.post("/api/{user_id}/trade")
def record_trade(user_id: str, trade: TradeCreate):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        ticker = trade.ticker.strip() or trade.name.strip()
        if trade.asset_type == "JP" and not ticker.endswith(".T"): ticker = f"{ticker}.T"
        elif trade.asset_type == "US": ticker = ticker.upper()
        
        cursor.execute('INSERT INTO transactions (user_id, ticker, type, trade_date, quantity, price, reason) VALUES (%s, %s, %s, %s, %s, %s, %s)', (user_id, ticker, trade.trade_type, trade.trade_date, trade.quantity, trade.price, trade.reason))
        cursor.execute("SELECT * FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        current = cursor.fetchone()
        
        if "BUY" in trade.trade_type:
            if current:
                new_qty = current["quantity"] + trade.quantity
                cursor.execute("UPDATE portfolio SET quantity = %s, average_price = %s WHERE user_id = %s AND ticker = %s", (new_qty, ((current["quantity"] * current["average_price"]) + (trade.quantity * trade.price)) / new_qty, user_id, ticker))
            else: cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (%s, %s, %s, %s, %s, %s)", (user_id, ticker, trade.name.strip() or ticker, trade.quantity, trade.price, trade.price))
        elif trade.trade_type == "SELL" and current:
            new_qty = current["quantity"] - trade.quantity
            if new_qty <= 0: cursor.execute("DELETE FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker))
            else: cursor.execute("UPDATE portfolio SET quantity = %s WHERE user_id = %s AND ticker = %s", (new_qty, user_id, ticker))
        cursor.close(); conn.close()
    except: pass
    return {"message": "Success"}

@app.get("/api/{user_id}/portfolio")
def get_portfolio(user_id: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM portfolio WHERE user_id = %s", (user_id,))
        rows = cursor.fetchall(); cursor.close(); conn.close()
    except: return {"total_assets": 0, "usdjpy_rate": 155.0, "category_totals": {"日本株":{"current":0,"book":0}, "米国株":{"current":0,"book":0}, "投資信託":{"current":0,"book":0}}, "portfolio": [], "est_dividend_jpy": 0}
    
    usdjpy_info = get_usdjpy_rate()
    portfolio_data = []; cat_totals = {"日本株": {"current": 0.0, "book": 0.0}, "米国株": {"current": 0.0, "book": 0.0}, "投資信託": {"current": 0.0, "book": 0.0}}
    total_assets = 0.0; total_book = 0.0; total_est_dividend_jpy = 0.0 

    for row in rows:
        item = dict(row)
        is_jpy = item["ticker"].endswith(".T")
        is_fund = (len(item["ticker"]) == 8 and item["ticker"].isalnum()) or "投信" in item["name"] or "ファンド" in item["name"] or "スリム" in item["name"]
        fx_rate = 1.0 if is_jpy or is_fund else usdjpy_info["rate"]
        
        current_price, div_yield = get_asset_data(item["ticker"], is_jpy, is_fund)
        if current_price == 0: current_price = item.get("manual_price") or item["average_price"]
            
        if is_fund: cv = (item["quantity"] * current_price) / 10000.0; bv = (item["quantity"] * item["average_price"]) / 10000.0; cat = "投資信託"
        elif is_jpy: cv = (current_price * item["quantity"]); bv = (item["average_price"] * item["quantity"]); cat = "日本株"; total_est_dividend_jpy += cv * (div_yield / 100.0)
        else: cv = (current_price * item["quantity"]) * fx_rate; bv = (item["average_price"] * item["quantity"]) * fx_rate; cat = "米国株"; total_est_dividend_jpy += cv * (div_yield / 100.0)
            
        item.update({"category": cat, "is_fund": is_fund, "current_price": current_price, "currency": "JPY" if is_jpy or is_fund else "USD", "current_value_jpy": cv, "profit_loss_jpy": cv - bv, "dividend_yield": div_yield})
        cat_totals[cat]["current"] += cv; cat_totals[cat]["book"] += bv; total_assets += cv; total_book += bv; portfolio_data.append(item)

    return {"total_assets": total_assets, "total_book": total_book, "usdjpy_rate": usdjpy_info["rate"], "usdjpy_time": usdjpy_info["time"], "category_totals": cat_totals, "portfolio": portfolio_data, "est_dividend_jpy": total_est_dividend_jpy}

@app.get("/api/{user_id}/target_allocation")
def get_target_allocation(user_id: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM target_allocations WHERE user_id = %s", (user_id,))
        row = cursor.fetchone(); cursor.close(); conn.close()
        if row: return {"jp_stock": row["jp_stock"], "us_stock": row["us_stock"], "fund": row["fund"]}
    except: pass
    return {"jp_stock": 33.3, "us_stock": 33.3, "fund": 33.4} 

@app.post("/api/{user_id}/target_allocation")
def set_target_allocation(user_id: str, data: TargetAllocation):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO target_allocations (user_id, jp_stock, us_stock, fund) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id) DO UPDATE SET jp_stock = EXCLUDED.jp_stock, us_stock = EXCLUDED.us_stock, fund = EXCLUDED.fund", (user_id, data.jp_stock, data.us_stock, data.fund))
        cursor.close(); conn.close()
        return {"message": "Success"}
    except: raise HTTPException(status_code=500, detail="DB Error")

@app.get("/api/fund_info/{ticker}")
def get_fund_info(ticker: str):
    price, _ = get_asset_data(ticker, False, True)
    return {"ticker": ticker, "price": price}

@app.post("/api/{user_id}/fund_rule")
def add_fund_rule(user_id: str, rule: FundRuleCreate):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute('INSERT INTO fund_rules (user_id, ticker, name, frequency, monthly_day, amount, start_date) VALUES (%s, %s, %s, %s, %s, %s, %s)', (user_id, rule.ticker, rule.name, rule.frequency, rule.monthly_day, rule.amount, rule.start_date))
        curr = datetime.strptime(rule.start_date, "%Y-%m-%d"); today = datetime.now()
        fetched_price, _ = get_asset_data(rule.ticker, False, True)
        base_price = rule.avg_price if rule.avg_price > 0 else (fetched_price or 10000.0)

        while curr <= today:
            actual_date = curr if rule.frequency == "DAILY" and is_business_day(curr) else (get_next_business_day(curr) if rule.frequency == "MONTHLY" and curr.day == rule.monthly_day else None)
            if actual_date and actual_date <= today:
                cursor.execute('INSERT INTO transactions (user_id, ticker, type, trade_date, quantity, price, reason) VALUES (%s, %s, %s, %s, %s, %s, %s)', (user_id, rule.ticker, 'BUY_AUTO', actual_date.strftime("%Y-%m-%d"), (rule.amount / base_price) * 10000.0, base_price, "自動積立"))
            curr += timedelta(days=1)

        cursor.execute("SELECT SUM(quantity) as total_qty FROM transactions WHERE user_id = %s AND ticker = %s", (user_id, rule.ticker))
        cursor.execute("UPDATE portfolio SET quantity = %s, average_price = %s WHERE user_id = %s AND ticker = %s", (cursor.fetchone()["total_qty"] or 0.0, base_price, user_id, rule.ticker)) if cursor.rowcount > 0 else cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (%s, %s, %s, %s, %s, %s)", (user_id, rule.ticker, rule.name, cursor.fetchone()["total_qty"] or 0.0, base_price, base_price))
        cursor.close(); conn.close()
    except: pass
    return {"message": "Success"}

@app.get("/api/{user_id}/transactions/{category}")
def get_transactions_by_category(user_id: str, category: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT t.*, p.name FROM transactions t LEFT JOIN portfolio p ON t.ticker = p.ticker AND p.user_id = t.user_id WHERE t.user_id = %s ORDER BY t.trade_date DESC", (user_id,))
        rows = cursor.fetchall(); cursor.close(); conn.close()
        result = []
        for r in rows:
            item_cat = "FUND" if ((len(r["ticker"]) == 8 and r["ticker"].isalnum()) or "投信" in (r["name"] or r["ticker"])) else ("JP" if r["ticker"].endswith(".T") else "US")
            if category.upper() == "ALL" or category.upper() == item_cat: result.append(dict(r, name=r["name"] or r["ticker"]))
        return result
    except: return []

@app.delete("/api/{user_id}/transaction/{tx_id}")
def delete_transaction(user_id: str, tx_id: int):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("DELETE FROM transactions WHERE id = %s AND user_id = %s", (tx_id, user_id))
        cursor.close(); conn.close()
    except: pass
    return {"message": "Success"}

@app.delete("/api/{user_id}/delete_stock/{ticker}")
def delete_stock_api(user_id: str, ticker: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("DELETE FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker)); cursor.execute("DELETE FROM transactions WHERE user_id = %s AND ticker = %s", (user_id, ticker)); cursor.execute("DELETE FROM fund_rules WHERE user_id = %s AND ticker = %s", (user_id, ticker)); cursor.execute("DELETE FROM watchlist WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        cursor.close(); conn.close()
    except: pass
    return {"message": "Deleted"}

@app.get("/api/{user_id}/history")
def get_history(user_id: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM transactions WHERE user_id = %s ORDER BY trade_date ASC", (user_id,))
        trades = cursor.fetchall()
        cursor.execute("SELECT ticker, manual_price, average_price FROM portfolio WHERE user_id = %s", (user_id,))
        portfolio_prices = {r["ticker"]: r["manual_price"] or r["average_price"] for r in cursor.fetchall()}
        cursor.close(); conn.close()
        if not trades: return []
    except: return []
    
    usdjpy = get_usdjpy_rate()["rate"]; tickers = list(set(t["ticker"] for t in trades)); prices_by_date = {}; start_date_obj = datetime.strptime(trades[0]["trade_date"], "%Y-%m-%d")
    min_start = datetime.now() - timedelta(days=365*5)
    if start_date_obj < min_start: start_date_obj = min_start
    all_dates_set = set()

    for ticker in tickers:
        if len(ticker) == 8 and ticker.isalnum(): continue
        try:
            df = yf.Ticker(ticker if ticker.endswith(".T") else (f"{ticker}.T" if (len(ticker)==4 and ticker.isalnum()) else ticker)).history(start=start_date_obj.strftime("%Y-%m-%d"))
            for idx, row in df.iterrows():
                if not math.isnan(row["Close"]):
                    d_str = idx.strftime("%Y-%m-%d"); all_dates_set.add(d_str) 
                    if d_str not in prices_by_date: prices_by_date[d_str] = {}
                    prices_by_date[d_str][ticker] = float(row["Close"])
        except: pass

    for t in trades: all_dates_set.add(t["trade_date"])
    today_str = datetime.now().strftime("%Y-%m-%d"); all_dates_set.add(today_str); all_dates = sorted(list(all_dates_set))
    current_holdings = {t: 0.0 for t in tickers}; last_known_price = {t: 0.0 for t in tickers}
    for t in trades:
        if last_known_price[t["ticker"]] == 0: last_known_price[t["ticker"]] = t["price"]

    trade_index = 0; result = []
    for date_str in all_dates:
        while trade_index < len(trades) and trades[trade_index]["trade_date"] <= date_str:
            tr = trades[trade_index]
            if "BUY" in tr["type"]: current_holdings[tr["ticker"]] += tr["quantity"]
            elif tr["type"] == "SELL": current_holdings[tr["ticker"]] -= tr["quantity"]
            last_known_price[tr["ticker"]] = tr["price"]; trade_index += 1
        
        if date_str in prices_by_date:
            for t, p in prices_by_date[date_str].items(): last_known_price[t] = p
        if date_str == today_str:
            for t in tickers:
                if t in portfolio_prices: last_known_price[t] = portfolio_prices[t]

        day_total = sum((qty * last_known_price.get(t, 0.0) / (10000.0 if len(t) == 8 and t.isalnum() else 1.0)) * (1.0 if t.endswith(".T") or (len(t) == 8 and t.isalnum()) else usdjpy) for t, qty in current_holdings.items() if qty > 0)
        if day_total > 0: result.append({"date": date_str, "total_assets": round(day_total, 2)})
            
    return result

@app.get("/api/{user_id}/watchlist")
def get_watchlist(user_id: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM watchlist WHERE user_id = %s ORDER BY added_date DESC", (user_id,))
        rows = cursor.fetchall(); cursor.close(); conn.close()
        result = []
        for r in rows:
            item = dict(r); price, _ = get_asset_data(item["ticker"], item["ticker"].endswith(".T") or (len(item["ticker"])==4 and item["ticker"].isalnum()), False)
            item.update({"current_price": price, "currency": "¥" if item["ticker"].endswith(".T") or (len(item["ticker"])==4 and item["ticker"].isalnum()) else "$"})
            result.append(item)
        return result
    except: return []

@app.post("/api/{user_id}/watchlist")
def add_watchlist(user_id: str, item: WatchlistCreate):
    try:
        ticker = item.ticker.strip()
        ticker = f"{ticker}.T" if len(ticker) == 4 and ticker.isalnum() and not ticker.endswith(".T") else ticker.upper()
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute('INSERT INTO watchlist (user_id, ticker, name, added_date) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, ticker) DO NOTHING', (user_id, ticker, item.name, datetime.now().strftime("%Y-%m-%d")))
        cursor.close(); conn.close()
    except: pass
    return {"message": "Success"}

@app.delete("/api/{user_id}/watchlist/{ticker}")
def delete_watchlist(user_id: str, ticker: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("DELETE FROM watchlist WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        cursor.close(); conn.close()
    except: pass
    return {"message": "Success"}

@app.get("/api/admin/users")
def get_all_users():
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT p.user_id, COUNT(DISTINCT p.ticker) as portfolio_count, (SELECT COUNT(*) FROM transactions t WHERE t.user_id = p.user_id) as transaction_count FROM portfolio p GROUP BY p.user_id")
        rows = cursor.fetchall(); cursor.close(); conn.close()
        return rows
    except: raise HTTPException(status_code=500, detail="Database Error")

@app.delete("/api/admin/user/{user_id}")
def delete_all_user_data(user_id: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        for table in ["portfolio", "transactions", "fund_rules", "watchlist"]: cursor.execute(f"DELETE FROM {table} WHERE user_id = %s", (user_id,))
        cursor.close(); conn.close()
        return {"message": f"User {user_id} deleted"}
    except: raise HTTPException(status_code=500, detail="Database Error")
