from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yfinance as yf
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
import jpholiday
import re
import math
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import csv

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
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
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

def get_ai_summary(title: str) -> str:
    if not GEMINI_API_KEY: return "AI機能が未設定です"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
    payload = {"contents": [{"parts": [{"text": f"事実のみに基づいて初心者向けに2行で要約し影響を判定: {title}"}]}]}
    try:
        res = requests.post(url, headers={'Content-Type': 'application/json'}, json=payload, timeout=10)
        return res.json()['candidates'][0]['content']['parts'][0]['text'].strip() if res.status_code == 200 else "要約失敗"
    except: return "AI要約エラー"

class TradeCreate(BaseModel): ticker: str = ""; name: str; trade_type: str; asset_type: str; trade_date: str; quantity: float; price: float; reason: str = ""
class FundRuleCreate(BaseModel): ticker: str; name: str; frequency: str; monthly_day: int = 1; amount: float; avg_price: float = 10000.0; start_date: str
class PriceUpdate(BaseModel): ticker: str; current_price: float
class WatchlistCreate(BaseModel): ticker: str; name: str

def init_db():
    if not DATABASE2_URL: return
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute('''CREATE TABLE IF NOT EXISTS portfolio (user_id TEXT, ticker TEXT, name TEXT, quantity REAL, average_price REAL, manual_price REAL, PRIMARY KEY (user_id, ticker)); CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, user_id TEXT, ticker TEXT, type TEXT, trade_date TEXT, quantity REAL, price REAL, reason TEXT); CREATE TABLE IF NOT EXISTS fund_rules (id SERIAL PRIMARY KEY, user_id TEXT, ticker TEXT, name TEXT, frequency TEXT, monthly_day INTEGER, amount REAL, start_date TEXT); CREATE TABLE IF NOT EXISTS watchlist (user_id TEXT, ticker TEXT, name TEXT, added_date TEXT, PRIMARY KEY (user_id, ticker)); CREATE TABLE IF NOT EXISTS line_users (line_user_id TEXT PRIMARY KEY, app_user_id TEXT); CREATE TABLE IF NOT EXISTS sent_news (line_user_id TEXT, news_link TEXT, PRIMARY KEY (line_user_id, news_link)); CREATE TABLE IF NOT EXISTS asset_cache (ticker TEXT PRIMARY KEY, price REAL, div_yield REAL, last_updated TEXT);''')
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
        
    rate = 0.0; fetch_time = datetime.now().strftime("%Y/%m/%d %H:%M")
    try:
        res = requests.get("https://open.er-api.com/v6/latest/USD", timeout=3)
        if res.status_code == 200 and res.json().get("rates", {}).get("JPY", 0) > 100: rate = float(res.json()["rates"]["JPY"])
    except: pass
    if rate == 0.0:
        try:
            hist = yf.Ticker("JPY=X").history(period="1d")
            if not hist.empty and not math.isnan(hist['Close'].iloc[-1]) and hist['Close'].iloc[-1] > 100: rate = float(hist['Close'].iloc[-1])
        except: pass
    if rate == 0.0:
        try:
            cursor.execute("SELECT price FROM asset_cache WHERE ticker = 'USDJPY'")
            old = cursor.fetchone()
            rate = old["price"] if old else 155.0; fetch_time = "前回取得値"
        except: rate = 155.0
        
    try:
        cursor.execute("INSERT INTO asset_cache (ticker, price, div_yield, last_updated) VALUES ('USDJPY', %s, 0.0, %s) ON CONFLICT (ticker) DO UPDATE SET price = EXCLUDED.price, last_updated = EXCLUDED.last_updated", (rate, today_str))
        cursor.close(); conn.close()
    except: pass
    return {"rate": rate, "time": fetch_time}

def get_asset_data(ticker: str, is_jpy: bool, is_fund: bool):
    ticker = ticker.strip().upper()
    today_str = datetime.now().strftime("%Y-%m-%d-v11")
    price = 0.0; div_yield = 0.0; row = None; conn = None; cursor = None
    
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT price, div_yield, last_updated FROM asset_cache WHERE ticker = %s", (ticker,))
        row = cursor.fetchone()
        if row and row["last_updated"] == today_str and row["price"] > 0:
            cursor.close(); conn.close(); return row["price"], row["div_yield"]
    except: pass
        
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    }
    
    if is_fund and len(ticker) == 8 and ticker.isalnum():
        urls_to_try = [f"https://itf.minkabu.jp/fund/{ticker}", f"https://finance.yahoo.co.jp/quote/{ticker}", f"https://www.nikkei.com/nkd/fund/?fcode={ticker}"]
        for url in urls_to_try:
            if price > 0.0: break
            try:
                res = requests.get(url, headers=headers, timeout=5)
                if res.status_code == 200:
                    text_only = re.sub(r'<[^>]+>', '', res.text)
                    idx = text_only.find("基準価額")
                    if idx != -1:
                        snippet = text_only[idx:idx+150]
                        match = re.search(r'([1-9][0-9]{0,2}(?:,[0-9]{3})+)', snippet)
                        if match: price = float(match.group(1).replace(',', ''))
            except: pass
    else:
        try:
            stock_ticker = ticker if not is_jpy else (f"{ticker}.T" if (len(ticker)==4 and ticker.isalnum()) else ticker)
            stock = yf.Ticker(stock_ticker); hist = stock.history(period="1d")
            if not hist.empty and not math.isnan(hist['Close'].iloc[-1]): price = float(hist['Close'].iloc[-1])
            info = stock.info
            if info and info.get("dividendYield"): div_yield = float(info["dividendYield"]) * 100.0
            elif info and info.get("dividendRate") and price > 0: div_yield = (float(info.get("dividendRate")) / price) * 100.0
            if div_yield == 0.0:
                recent_divs = stock.dividends[stock.dividends.index >= (datetime.now(stock.dividends.index.tzinfo) - timedelta(days=365))]
                if float(recent_divs.sum()) > 0 and price > 0: div_yield = (float(recent_divs.sum()) / price) * 100.0
        except: pass

    if div_yield == 0.0 or math.isnan(div_yield): div_yield = 2.5 if is_jpy else (1.5 if not is_fund else 0.0)
    if price == 0.0 and row: price = row["price"] 

    if price > 0:
        try:
            if conn and cursor:
                cursor.execute("INSERT INTO asset_cache (ticker, price, div_yield, last_updated) VALUES (%s, %s, %s, %s) ON CONFLICT (ticker) DO UPDATE SET price = EXCLUDED.price, div_yield = EXCLUDED.div_yield, last_updated = EXCLUDED.last_updated", (ticker, price, div_yield, today_str))
                cursor.close(); conn.close()
        except: pass
        
    return price, div_yield

def check_and_send_news():
    pass

scheduler = BackgroundScheduler(); scheduler.add_job(check_and_send_news, 'interval', minutes=60); scheduler.start()
atexit.register(lambda: scheduler.shutdown())

@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    try: handler.handle(body.decode("utf-8"), x_line_signature)
    except: raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    pass

@app.get("/api/ai_summary")
def api_ai_summary(title: str): return {"summary": get_ai_summary(title)}

def is_business_day(dt: datetime) -> bool: return dt.weekday() not in (5, 6) and not jpholiday.is_holiday(dt)
def get_next_business_day(dt: datetime) -> datetime:
    curr = dt
    while not is_business_day(curr): curr += timedelta(days=1)
    return curr

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
                        name = quote.get("shortname", quote.get("longname", code))
                        if code.endswith(".T") and not any(r["ticker"] == code for r in results):
                            results.append({"ticker": code, "name": name})
            except: pass
        if results and asset_type == "JP": return results[:8]

    if asset_type in ["US", "ALL"] and len(results) < 8:
        try:
            res = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=8&country=US", headers=headers, timeout=3)
            if res.status_code == 200:
                for quote in res.json().get("quotes", []):
                    ticker = quote.get("symbol", ""); name = quote.get("shortname", quote.get("longname", ticker))
                    if not ticker.endswith(".T") and not any(r["ticker"] == ticker for r in results): results.append({"ticker": ticker, "name": name})
        except: pass

    if asset_type in ["FUND", "ALL"] and len(results) < 8:
        search_terms = q_str.replace(" ", " ").split()
        for fund in POPULAR_FUNDS:
            if all(t in (fund["name"].lower() + " " + " ".join(fund["keywords"])) for t in search_terms):
                if not any(r["ticker"] == fund["ticker"] for r in results): results.append({"ticker": fund["ticker"], "name": fund["name"]})
                if len(results) >= 8: break
        if len(results) < 8 and len(q_str) == 8 and q_str.isalnum():
            try:
                res = requests.get(f"https://itf.minkabu.jp/fund/{q_str.upper()}", headers=headers, timeout=3)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, 'html.parser')
                    name = soup.find('h1').get_text(strip=True) if soup.find('h1') else (soup.find('title').text.split('|')[0].split('-')[0].strip() if soup.find('title') else "")
                    if name: results.append({"ticker": q_str.upper(), "name": name})
            except: pass
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
        name = trade.name.strip() or ticker
        
        cursor.execute('INSERT INTO transactions (user_id, ticker, type, trade_date, quantity, price, reason) VALUES (%s, %s, %s, %s, %s, %s, %s)', (user_id, ticker, trade.trade_type, trade.trade_date, trade.quantity, trade.price, trade.reason))
        cursor.execute("SELECT * FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        current = cursor.fetchone()
        
        if "BUY" in trade.trade_type:
            if current:
                new_qty = current["quantity"] + trade.quantity
                new_price = ((current["quantity"] * current["average_price"]) + (trade.quantity * trade.price)) / new_qty
                cursor.execute("UPDATE portfolio SET quantity = %s, average_price = %s WHERE user_id = %s AND ticker = %s", (new_qty, new_price, user_id, ticker))
            else:
                cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (%s, %s, %s, %s, %s, %s)", (user_id, ticker, name, trade.quantity, trade.price, trade.price))
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
    except: return {"total_assets": 0, "total_book": 0, "usdjpy_rate": 155.0, "usdjpy_time": "エラー", "category_totals": {"日本株":{"current":0,"book":0}, "米国株":{"current":0,"book":0}, "投資信託":{"current":0,"book":0}}, "portfolio": [], "est_dividend_jpy": 0}
    
    usdjpy_info = get_usdjpy_rate()
    portfolio_data = []; cat_totals = {"日本株": {"current": 0.0, "book": 0.0}, "米国株": {"current": 0.0, "book": 0.0}, "投資信託": {"current": 0.0, "book": 0.0}}
    total_assets = 0.0; total_book = 0.0; total_est_dividend_jpy = 0.0 

    for row in rows:
        item = dict(row); ticker = item["ticker"]; quantity = item["quantity"]; average_price = item["average_price"]
        manual_price = item.get("manual_price") or average_price
        is_jpy = ticker.endswith(".T")
        is_fund = (len(ticker) == 8 and ticker.isalnum()) or "投信" in item["name"] or "ファンド" in item["name"] or "スリム" in item["name"]
        fx_rate = 1.0 if is_jpy or is_fund else usdjpy_info["rate"]
        
        fetched_price, div_yield = get_asset_data(ticker, is_jpy, is_fund)
        current_price = fetched_price if fetched_price > 0 else manual_price
            
        if is_fund: current_value_jpy = (quantity * current_price) / 10000.0; book_value_jpy = (quantity * average_price) / 10000.0; category = "投資信託"
        elif is_jpy: current_value_jpy = (current_price * quantity); book_value_jpy = (average_price * quantity); category = "日本株"; total_est_dividend_jpy += current_value_jpy * (div_yield / 100.0)
        else: current_value_jpy = (current_price * quantity) * fx_rate; book_value_jpy = (average_price * quantity) * fx_rate; category = "米国株"; total_est_dividend_jpy += current_value_jpy * (div_yield / 100.0)
            
        item.update({"category": category, "is_fund": is_fund, "current_price": current_price, "currency": "JPY" if is_jpy or is_fund else "USD", "current_value_jpy": current_value_jpy, "profit_loss_jpy": current_value_jpy - book_value_jpy, "dividend_yield": div_yield})
        cat_totals[category]["current"] += current_value_jpy; cat_totals[category]["book"] += book_value_jpy
        total_assets += current_value_jpy; total_book += book_value_jpy; portfolio_data.append(item)

    return {"total_assets": total_assets, "total_book": total_book, "usdjpy_rate": usdjpy_info["rate"], "usdjpy_time": usdjpy_info["time"], "category_totals": cat_totals, "portfolio": portfolio_data, "est_dividend_jpy": total_est_dividend_jpy}

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
        total_qty = cursor.fetchone()["total_qty"] or 0.0
        cursor.execute("SELECT * FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, rule.ticker))
        if cursor.fetchone(): cursor.execute("UPDATE portfolio SET quantity = %s, average_price = %s WHERE user_id = %s AND ticker = %s", (total_qty, base_price, user_id, rule.ticker))
        else: cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (%s, %s, %s, %s, %s, %s)", (user_id, rule.ticker, rule.name, total_qty, base_price, base_price))
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
            item_cat = "FUND" if ((len(r["ticker"]) == 8 and r["ticker"].isalnum()) or "投信" in (r["name"] or r["ticker"]) or "ファンド" in (r["name"] or r["ticker"])) else ("JP" if r["ticker"].endswith(".T") else "US")
            if category.upper() == "ALL" or category.upper() == item_cat:
                result.append(dict(r, name=r["name"] or r["ticker"]))
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
        cursor.execute("DELETE FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        cursor.execute("DELETE FROM transactions WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        cursor.execute("DELETE FROM fund_rules WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        cursor.execute("DELETE FROM watchlist WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        cursor.close(); conn.close()
    except: pass
    return {"message": "Deleted"}

# ==========================================
# 🌟 【完全版】Googleファイナンス風「リアルな変動グラフ」の生成ロジック
# ==========================================
@app.get("/api/{user_id}/history")
def get_history(user_id: str):
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM transactions WHERE user_id = %s ORDER BY trade_date ASC", (user_id,))
        trades = cursor.fetchall()
        
        # 最終日のDB価格（手入力価格やスクレイピング価格）を取得
        cursor.execute("SELECT ticker, manual_price, average_price FROM portfolio WHERE user_id = %s", (user_id,))
        portfolio_prices = {r["ticker"]: r["manual_price"] or r["average_price"] for r in cursor.fetchall()}
        cursor.close()
        conn.close()
        
        if not trades: return []
    except Exception as e:
        print("History DB Error:", e)
        return []
    
    usdjpy = get_usdjpy_rate()["rate"]
    tickers = list(set(t["ticker"] for t in trades))
    
    # 1. すべての銘柄の「過去の毎日の株価」を辞書にまとめる
    prices_by_date = {}
    
    # 全期間をカバーするため、3年前から今日までの日付リストを作成
    start_date = datetime.strptime(trades[0]["trade_date"], "%Y-%m-%d")
    today = datetime.now()
    
    # yfinanceから株価履歴を一括ダウンロード（超高速）
    for ticker in tickers:
        is_fund = len(ticker) == 8 and ticker.isalnum()
        if is_fund: continue # 投資信託はyfinanceにないのでスキップ
        
        try:
            yf_ticker = ticker if ticker.endswith(".T") else (f"{ticker}.T" if (len(ticker)==4 and ticker.isalnum()) else ticker)
            df = yf.Ticker(yf_ticker).history(start=start_date.strftime("%Y-%m-%d"))
            for idx, row in df.iterrows():
                if not math.isnan(row["Close"]):
                    d_str = idx.strftime("%Y-%m-%d")
                    if d_str not in prices_by_date: prices_by_date[d_str] = {}
                    prices_by_date[d_str][ticker] = float(row["Close"])
        except: pass

    # 2. 過去から今日まで「1日ずつ」進めながら、リアルな資産額を計算する
    all_dates = []
    current_date = start_date
    while current_date <= today:
        all_dates.append(current_date.strftime("%Y-%m-%d"))
        current_date += timedelta(days=1)

    current_holdings = {t: 0.0 for t in tickers}
    last_known_price = {t: 0.0 for t in tickers}
    
    # 初日の価格をセット
    for t in trades:
        if last_known_price[t["ticker"]] == 0: last_known_price[t["ticker"]] = t["price"]

    trade_index = 0
    result = []
    
    for date_str in all_dates:
        # その日に取引があったら、保有数量と価格を更新
        while trade_index < len(trades) and trades[trade_index]["trade_date"] <= date_str:
            tr = trades[trade_index]
            t = tr["ticker"]
            if "BUY" in tr["type"]: current_holdings[t] += tr["quantity"]
            elif tr["type"] == "SELL": current_holdings[t] -= tr["quantity"]
            last_known_price[t] = tr["price"]
            trade_index += 1
        
        # もしその日の「実際の株価」があれば、価格を更新（波打つグラフの源泉！）
        if date_str in prices_by_date:
            for t, p in prices_by_date[date_str].items():
                last_known_price[t] = p
                
        # 最終日（今日）だけは、ポートフォリオ画面と全く同じ価格になるようにDBデータで上書き
        if date_str == today.strftime("%Y-%m-%d"):
            for t in tickers:
                if t in portfolio_prices: last_known_price[t] = portfolio_prices[t]

        # その日の総資産を計算
        day_total = 0.0
        for t, qty in current_holdings.items():
            if qty > 0:
                price = last_known_price.get(t, 0.0)
                is_fund = len(t) == 8 and t.isalnum()
                is_jpy = t.endswith(".T") or is_fund
                val = (qty * price) / (10000.0 if is_fund else 1.0)
                day_total += val * (1.0 if is_jpy else usdjpy)
                
        # 資産が1円以上ある日だけグラフにプロット
        if day_total > 0:
            result.append({"date": date_str, "total_assets": round(day_total, 2)})
            
    # スマホが重くならないようにデータ間引き（最大100件に圧縮）
    if len(result) > 100:
        step = len(result) // 100
        result = result[::step] + [result[-1]] if result[-1] != result[::step][-1] else result[::step]
        
    return result

@app.get("/api/{user_id}/news")
def get_jp_news(user_id: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT name FROM portfolio WHERE user_id = %s AND ticker LIKE '%%.T' AND quantity > 0", (user_id,))
        p_names = [r["name"] for r in cursor.fetchall() if r["name"]]
        cursor.execute("SELECT name FROM watchlist WHERE user_id = %s AND ticker LIKE '%%.T'", (user_id,))
        w_names = [r["name"] for r in cursor.fetchall() if r["name"]]
        cursor.close(); conn.close()
        
        market_targets = ["主要市況: 日経平均", "主要市況: S&P500", "主要市況: 為替 ドル円"]
        target_names = list(set(p_names + w_names)) + market_targets

        news_list = []
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        for company_name in target_names:
            search_term = company_name.replace("主要市況: ", "") + " 株"
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(search_term)}&hl=ja&gl=JP&ceid=JP:ja"
            try:
                res = requests.get(url, headers=headers, timeout=5)
                if res.status_code == 200:
                    root = ET.fromstring(res.text)
                    for item in root.findall('.//item')[:3]:
                        try:
                            dt = parsedate_to_datetime(item.find('pubDate').text)
                            news_list.append({"stock_name": company_name, "title": item.find('title').text, "link": item.find('link').text, "pub_time": dt.strftime("%Y/%m/%d %H:%M"), "timestamp": dt.timestamp()})
                        except: pass
            except: pass
        news_list.sort(key=lambda x: x["timestamp"], reverse=True)
        return news_list[:300]
    except Exception as e: return []

@app.get("/api/{user_id}/watchlist")
def get_watchlist(user_id: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM watchlist WHERE user_id = %s ORDER BY added_date DESC", (user_id,))
        rows = cursor.fetchall(); cursor.close(); conn.close()
        for r in rows:
            price, _ = get_asset_data(r["ticker"], r["ticker"].endswith(".T") or (len(r["ticker"])==4 and r["ticker"].isalnum()), False)
            r["current_price"] = price; r["currency"] = "¥" if r["ticker"].endswith(".T") or (len(r["ticker"])==4 and r["ticker"].isalnum()) else "$"
        return rows
    except: return []

# ==========================================
# 🌟 Admin用API群（完全復旧）
# ==========================================
@app.get("/api/admin/users")
def get_all_users():
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT p.user_id, COUNT(DISTINCT p.ticker) as portfolio_count, (SELECT COUNT(*) FROM transactions t WHERE t.user_id = p.user_id) as transaction_count FROM portfolio p GROUP BY p.user_id")
        rows = cursor.fetchall(); cursor.close(); conn.close()
        return rows
    except Exception as e:
        print("Admin Users Error:", e)
        raise HTTPException(status_code=500, detail="Database Error")

@app.delete("/api/admin/user/{user_id}")
def delete_all_user_data(user_id: str):
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("DELETE FROM portfolio WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM transactions WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM fund_rules WHERE user_id = %s", (user_id,))
        cursor.execute("DELETE FROM watchlist WHERE user_id = %s", (user_id,))
        cursor.close(); conn.close()
        return {"message": f"User {user_id} deleted"}
    except Exception as e:
        print("Admin Delete Error:", e)
        raise HTTPException(status_code=500, detail="Database Error")
