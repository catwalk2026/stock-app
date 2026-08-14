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
import unicodedata

# --- LINE連携用ライブラリ ---
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent

# --- 定期実行（パトロール）用ライブラリ ---
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

LINE_CHANNEL_ACCESS_TOKEN = "rlJ1YRFK3hCEYnrfCe5k9kO2gjyX3YkqhfdAvnT28lWoC/9Q6NTtPdBNvGU6jVWunuf7k6NPAg/d2r39X+IxD4mlNjs2bH4krV2B7zWilto5IHSvo7QXkKbIxa0GNvVN2SK9b2AH03Rs/M6VrJBIlwdB04t89/1O/w1cDnyilFU="
LINE_CHANNEL_SECRET = "c8caf38acc62174908dcff1f782621f6"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = FastAPI()

DATABASE2_URL = os.environ.get("DATABASE2_URL") or os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# ==========================================
# 🌟 JPX（日本株）と 人気投信（王道ファンド）の正確なキャッシュデータ
# ==========================================
JPX_STOCKS = []

def load_jpx_stocks():
    global JPX_STOCKS
    url = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.csv"
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            res.encoding = 'shift_jis'
            lines = res.text.splitlines()
            reader = csv.reader(lines)
            next(reader, None)
            stocks = []
            for row in reader:
                if len(row) >= 3:
                    code = row[1].strip()
                    name = row[2].strip()
                    if code and name:
                        stocks.append({"code": code, "name": name, "ticker": f"{code}.T"})
            JPX_STOCKS = stocks
            print(f"✅ JPX全銘柄データの読み込み完了 ({len(JPX_STOCKS)}件)")
    except Exception as e:
        print("❌ JPX銘柄データの読み込み失敗:", e)

load_jpx_stocks()

POPULAR_FUNDS = [
    {"ticker": "0331418A", "name": "eMAXIS Slim 全世界株式(オール・カントリー)", "keywords": ["オルカン", "emaxis", "slim", "all", "全世界", "カントリー", "三菱ufj"]},
    {"ticker": "03311187", "name": "eMAXIS Slim 米国株式(S&P500)", "keywords": ["emaxis", "slim", "s&p500", "sp500", "米国", "アメリカ"]},
    {"ticker": "89311199", "name": "SBI・V・S&P500インデックス・ファンド", "keywords": ["sbi", "v", "s&p", "sp500", "バンガード"]},
    {"ticker": "89311216", "name": "SBI・V・全米株式インデックス・ファンド", "keywords": ["sbi", "v", "全米", "vti", "バンガード"]},
    {"ticker": "9I312179", "name": "楽天・全米株式インデックス・ファンド(楽天・VTI)", "keywords": ["楽天", "全米", "vti", "バンガード"]},
    {"ticker": "9I311179", "name": "楽天・全世界株式インデックス・ファンド(楽天・VT)", "keywords": ["楽天", "全世界", "vt", "オルカン", "バンガード"]},
    {"ticker": "4731B15C", "name": "たわらノーロード 先進国株式", "keywords": ["たわら", "ノーロード", "先進国"]},
    {"ticker": "47312197", "name": "たわらノーロード 全世界株式", "keywords": ["たわら", "ノーロード", "全世界", "オルカン"]},
    {"ticker": "2931113C", "name": "ニッセイ外国株式インデックスファンド", "keywords": ["ニッセイ", "外国", "インデックス"]},
    {"ticker": "29311041", "name": "ニッセイ日経225インデックスファンド", "keywords": ["ニッセイ", "日経"]},
    {"ticker": "9C311125", "name": "ひふみプラス", "keywords": ["ひふみ", "プラス", "レオス"]},
    {"ticker": "03319172", "name": "eMAXIS Slim 先進国株式インデックス", "keywords": ["emaxis", "slim", "先進国"]},
    {"ticker": "03317172", "name": "eMAXIS Slim 国内株式(TOPIX)", "keywords": ["emaxis", "slim", "国内", "topix", "トピックス"]},
    {"ticker": "03311182", "name": "eMAXIS Slim 国内株式(日経平均)", "keywords": ["emaxis", "slim", "国内", "日経"]},
    {"ticker": "03312175", "name": "eMAXIS Slim バランス(8資産均等型)", "keywords": ["emaxis", "slim", "バランス", "8資産"]},
]

def get_db_connection():
    if not DATABASE2_URL:
        raise Exception("DATABASE2_URLまたはDATABASE_URLが設定されていません。")
    conn = psycopg2.connect(DATABASE2_URL)
    conn.autocommit = True
    return conn

def get_ai_summary(title: str) -> str:
    if not GEMINI_API_KEY:
        return "GEMINI_API_KEYがRenderに設定されていません。"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = f"""以下の金融ニュース見出しの文章・テキストに含まれる事実のみに基づいて、投資初心者向けに2〜3行で分かりやすく要約し、最後に相場への一般的な影響(ポジティブ/ネガティブ/中立など)を判定してください。
【厳重注意事項】独自の情報は推測せず、テキストに書かれている事実のみを解説してください。
ニュース見出し: {title}"""

    payload = {"contents": [{"parts": [{"text": prompt}]}]}
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            return res.json()['candidates'][0]['content']['parts'][0]['text'].strip()
        else: return f"AI解説の取得失敗 (エラー: {res.status_code})"
    except Exception: return "AIの解説取得に一時的に失敗しました。"

class TradeCreate(BaseModel):
    ticker: str = ""; name: str; trade_type: str; asset_type: str; trade_date: str; quantity: float; price: float; reason: str = ""

class FundRuleCreate(BaseModel):
    ticker: str; name: str; frequency: str; monthly_day: int = 1; amount: float; avg_price: float = 10000.0; start_date: str

class PriceUpdate(BaseModel):
    ticker: str; current_price: float

class WatchlistCreate(BaseModel):
    ticker: str; name: str

def init_db():
    if not DATABASE2_URL: return
    try:
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolio (user_id TEXT, ticker TEXT, name TEXT, quantity REAL, average_price REAL, manual_price REAL, PRIMARY KEY (user_id, ticker));
            CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, user_id TEXT, ticker TEXT, type TEXT, trade_date TEXT, quantity REAL, price REAL, reason TEXT);
            CREATE TABLE IF NOT EXISTS fund_rules (id SERIAL PRIMARY KEY, user_id TEXT, ticker TEXT, name TEXT, frequency TEXT, monthly_day INTEGER, amount REAL, start_date TEXT);
            CREATE TABLE IF NOT EXISTS watchlist (user_id TEXT, ticker TEXT, name TEXT, added_date TEXT, PRIMARY KEY (user_id, ticker));
            CREATE TABLE IF NOT EXISTS line_users (line_user_id TEXT PRIMARY KEY, app_user_id TEXT);
            CREATE TABLE IF NOT EXISTS sent_news (line_user_id TEXT, news_link TEXT, PRIMARY KEY (line_user_id, news_link));
            CREATE TABLE IF NOT EXISTS asset_cache (ticker TEXT PRIMARY KEY, price REAL, div_yield REAL, last_updated TEXT);
        ''')
        cursor.close(); conn.close()
    except Exception as e: print("DB Init Error:", e)

init_db()

def get_usdjpy_rate():
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT price FROM asset_cache WHERE ticker = 'USDJPY' AND last_updated = %s", (today_str,))
    row = cursor.fetchone()
    if row and row["price"] > 100:
        cursor.close(); conn.close(); return {"rate": row["price"], "time": f"{today_str} (取得済)"}
        
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
        cursor.execute("SELECT price FROM asset_cache WHERE ticker = 'USDJPY'")
        old = cursor.fetchone()
        rate = old["price"] if old else 155.0; fetch_time = "前回取得値"
        
    cursor.execute('''INSERT INTO asset_cache (ticker, price, div_yield, last_updated) VALUES ('USDJPY', %s, 0.0, %s) ON CONFLICT (ticker) DO UPDATE SET price = EXCLUDED.price, div_yield = EXCLUDED.div_yield, last_updated = EXCLUDED.last_updated''', (rate, today_str))
    cursor.close(); conn.close()
    return {"rate": rate, "time": fetch_time}

# ==========================================
# 🌟 価格取得の完全高精度化（ピンポイント抽出）
# ==========================================
def get_asset_data(ticker: str, is_jpy: bool, is_fund: bool):
    ticker = ticker.strip().upper()
    today_str = datetime.now().strftime("%Y-%m-%d-v3")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT price, div_yield, last_updated FROM asset_cache WHERE ticker = %s", (ticker,))
    row = cursor.fetchone()
    
    if row and row["last_updated"] == today_str and row["price"] > 0:
        cursor.close(); conn.close()
        return row["price"], row["div_yield"]
        
    price = 0.0
    div_yield = 0.0
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    if is_fund and len(ticker) == 8 and ticker.isalnum():
        # 1. Yahoo!ファイナンス (メインコンテンツをピンポイント解析)
        try:
            res = requests.get(f"https://finance.yahoo.co.jp/quote/{ticker}", headers=headers, timeout=5)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                main_tag = soup.find('main') or soup.find('div', id='root') or soup
                text = main_tag.get_text()
                idx = text.find("基準価額")
                if idx != -1:
                    sub = text[idx:idx+200]
                    nums = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})', sub)
                    if nums:
                        val = float(nums[0].replace(',', ''))
                        if val > 100: price = val
        except: pass

        # 2. みんかぶ投信 (コンテンツエリア解析)
        if price == 0.0:
            try:
                res = requests.get(f"https://itf.minkabu.jp/fund/{ticker}", headers=headers, timeout=5)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, 'html.parser')
                    content = soup.find('div', id='content') or soup.find('main') or soup
                    text = content.get_text()
                    idx = text.find("基準価額")
                    if idx != -1:
                        sub = text[idx:idx+200]
                        nums = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})', sub)
                        if nums:
                            val = float(nums[0].replace(',', ''))
                            if val > 100: price = val
            except: pass

        # 3. 日経新聞 (構造安定型)
        if price == 0.0:
            try:
                res = requests.get(f"https://www.nikkei.com/nkd/fund/?fcode={ticker}", headers=headers, timeout=5)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, 'html.parser')
                    dd = soup.find('dd', class_=re.compile(r'.*value.*'))
                    if dd:
                        nums = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})', dd.text)
                        if nums:
                            val = float(nums[0].replace(',', ''))
                            if val > 100: price = val
            except: pass

        div_yield = 0.0

    else:
        try:
            stock_ticker = ticker if not is_jpy else (f"{ticker}.T" if (len(ticker)==4 and ticker.isalnum()) else ticker)
            stock = yf.Ticker(stock_ticker); hist = stock.history(period="1d")
            if not hist.empty and not math.isnan(hist['Close'].iloc[-1]): price = float(hist['Close'].iloc[-1])
            info = stock.info
            if info and info.get("dividendRate") and price > 0: div_yield = (float(info.get("dividendRate")) / price) * 100.0
            elif info and info.get("dividendYield"): div_yield = float(info["dividendYield"]) * 100.0
            if div_yield == 0.0 and not stock.dividends.empty:
                recent_divs = stock.dividends[stock.dividends.index >= (datetime.now(stock.dividends.index.tzinfo) - timedelta(days=365))]
                if float(recent_divs.sum()) > 0 and price > 0: div_yield = (float(recent_divs.sum()) / price) * 100.0
        except: pass

    if div_yield == 0.0 or math.isnan(div_yield):
        div_yield = 2.5 if is_jpy else (1.5 if not is_fund else 0.0)
    
    if price == 0.0 and row: price = row["price"] 

    if price > 0:
        cursor.execute('''INSERT INTO asset_cache (ticker, price, div_yield, last_updated) VALUES (%s, %s, %s, %s) ON CONFLICT (ticker) DO UPDATE SET price = EXCLUDED.price, div_yield = EXCLUDED.div_yield, last_updated = EXCLUDED.last_updated''', (ticker, price, div_yield, today_str))
        
    cursor.close(); conn.close()
    return price, div_yield

def check_and_send_news():
    if not DATABASE2_URL: return
    try:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM line_users"); users = cursor.fetchall()
        headers = {"User-Agent": "Mozilla/5.0"}; yesterday = datetime.now() - timedelta(days=1)
        for u in users:
            line_user_id = u["line_user_id"]; app_user_id = u["app_user_id"]
            cursor.execute("SELECT name FROM portfolio WHERE user_id = %s AND ticker LIKE '%%.T' AND quantity > 0", (app_user_id,))
            p_names = [r["name"] for r in cursor.fetchall()]
            cursor.execute("SELECT name FROM watchlist WHERE user_id = %s AND ticker LIKE '%%.T'", (app_user_id,))
            target_names = list(set(p_names + [r["name"] for r in cursor.fetchall()] + ["日経平均", "S&P500", "為替 ドル円"]))
            new_msgs = []
            for company_name in target_names:
                try:
                    res = requests.get(f"https://news.google.com/rss/search?q={urllib.parse.quote(company_name + ' 株')}&hl=ja&gl=JP&ceid=JP:ja", headers=headers, timeout=5)
                    if res.status_code == 200:
                        for item in ET.fromstring(res.text).findall('.//item')[:5]:
                            link = item.find('link').text; title = item.find('title').text
                            if parsedate_to_datetime(item.find('pubDate').text).timestamp() < yesterday.timestamp(): continue
                            cursor.execute("SELECT 1 FROM sent_news WHERE line_user_id = %s AND news_link = %s", (line_user_id, link))
                            if not cursor.fetchone():
                                new_msgs.append((f"📰 【{company_name}】の最新ニュース\n\n{title}\n\n💡 AI解説:\n{get_ai_summary(title)}\n\n{link}", link))
                                if len(new_msgs) >= 3: break
                except: pass
                if len(new_msgs) >= 3: break
            if new_msgs:
                try:
                    line_bot_api.push_message(line_user_id, [TextSendMessage(text=m[0]) for m in new_msgs])
                    for m in new_msgs: cursor.execute("INSERT INTO sent_news (line_user_id, news_link) VALUES (%s, %s) ON CONFLICT DO NOTHING", (line_user_id, m[1]))
                except: pass
        cursor.close(); conn.close()
    except: pass

scheduler = BackgroundScheduler(); scheduler.add_job(check_and_send_news, 'interval', minutes=60); scheduler.start()
atexit.register(lambda: scheduler.shutdown())

@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    try: handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError: raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(FollowEvent)
def handle_follow(event):
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="友だち追加ありがとうございます！🎉\n\nまずは、メニューの「会員連携」をタップして、ポートフォリオ用の会員番号（6桁の英数字）を登録してください📉✨"))

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip(); line_user_id = event.source.user_id
    if "会員連携" in text: line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 ダッシュボードに表示されている「会員番号（6桁の英数字）」をそのまま送信してください。\n例: AB1234"))
    elif "ダッシュボード" in text:
        conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT app_user_id FROM line_users WHERE line_user_id = %s", (line_user_id,))
        row = cursor.fetchone(); cursor.close(); conn.close()
        if row: line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📊 あなたのダッシュボードはこちらです！\nhttps://stock-app-xyif.onrender.com/{row['app_user_id']}"))
        else: line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ まだ会員連携が完了していません。"))
    elif re.match(r"^[a-zA-Z0-9]{6}$", text):
        conn = get_db_connection(); cursor = conn.cursor()
        cursor.execute("INSERT INTO line_users (line_user_id, app_user_id) VALUES (%s, %s) ON CONFLICT (line_user_id) DO UPDATE SET app_user_id = EXCLUDED.app_user_id", (line_user_id, text))
        cursor.close(); conn.close()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 会員番号「{text}」との紐付けが完了しました！"))
    else: line_bot_api.reply_message(event.reply_token, TextSendMessage(text="💡 メニューから操作を選ぶか、連携したい会員番号（6桁の英数字）を送信してください！"))

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
    raise HTTPException(status_code=404, detail="会員番号は6桁の英数字である必要があります")

# ==========================================
# 🌟 日本株サジェストの完全二重化（JPX＋Yahoo）
# ==========================================
@app.get("/api/search_stock")
def search_stock(q: str, asset_type: str = "ALL"):
    if not q: return []
    results = []
    q_str = q.strip().lower()
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/115.0.0.0 Safari/537.36"}

    # 1. 日本株検索 (JPXメモリ ＋ Yahooリアルタイムサジェストの二重化)
    if asset_type in ["JP", "ALL"]:
        # ① JPXリストからの検索
        if JPX_STOCKS:
            for item in JPX_STOCKS:
                if q_str in item["code"].lower() or q_str in item["name"].lower():
                    results.append({"ticker": item["ticker"], "name": item["name"]})
                    if len(results) >= 8: break
        
        # ② JPXが空または不十分な場合、Yahoo Japan サジェスト API を叩く
        if len(results) < 8:
            try:
                yj_url = f"https://finance.yahoo.co.jp/api/v1/finance/suggest/realtime?query={urllib.parse.quote(q)}"
                res = requests.get(yj_url, headers=headers, timeout=3)
                if res.status_code == 200:
                    for item in res.json().get("results", []):
                        code = item.get("code", "")
                        name = item.get("name", "")
                        if code and name and (code.endswith(".T") or (len(code) == 4 and code.isalnum())):
                            ticker = code if code.endswith(".T") else f"{code}.T"
                            if not any(r["ticker"] == ticker for r in results):
                                results.append({"ticker": ticker, "name": name})
                        if len(results) >= 8: break
            except: pass

        if results and asset_type == "JP": return results[:8]

    # 2. 米国株検索
    if asset_type in ["US", "ALL"] and len(results) < 8:
        try:
            res = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=8&country=US", headers=headers, timeout=5)
            if res.status_code == 200:
                for quote in res.json().get("quotes", []):
                    ticker = quote.get("symbol", "")
                    name = quote.get("shortname", quote.get("longname", ticker))
                    if asset_type == "US" and ticker.endswith(".T"): continue
                    if not any(r["ticker"] == ticker for r in results): results.append({"ticker": ticker, "name": name})
        except: pass

    # 3. 投資信託検索
    if asset_type in ["FUND", "ALL"] and len(results) < 8:
        if len(q_str) == 8 and q_str.isalnum():
            try:
                res = requests.get(f"https://itf.minkabu.jp/fund/{q_str.upper()}", headers=headers, timeout=3)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, 'html.parser'); h1 = soup.find('h1')
                    name = h1.get_text(strip=True) if h1 else (soup.find('title').text.split('|')[0].split('-')[0].strip() if soup.find('title') else "")
                    if name: results.append({"ticker": q_str.upper(), "name": unicodedata.normalize('NFKC', name)})
            except: pass

        search_terms = q_str.replace(" ", " ").split()
        for fund in POPULAR_FUNDS:
            fund_text = fund["name"].lower() + " " + " ".join(fund["keywords"])
            if all(term in fund_text for term in search_terms):
                if not any(r["ticker"] == fund["ticker"] for r in results):
                    results.append({"ticker": fund["ticker"], "name": fund["name"]})
                    if len(results) >= 8: break
                    
        if len(results) < 8:
            q_yahoo = q.replace(" ", "").replace(" ", "")
            try:
                res = requests.get(f"https://finance.yahoo.co.jp/api/v1/finance/suggest/realtime?query={urllib.parse.quote(q_yahoo)}", headers=headers, timeout=3)
                if res.status_code == 200:
                    for item in res.json().get("results", []):
                        code = item.get("code", ""); name = item.get("name", "")
                        if code and name and len(code) == 8 and code.isalnum() and not code.endswith(".T"):
                            if not any(r["ticker"] == code for r in results):
                                results.append({"ticker": code, "name": unicodedata.normalize('NFKC', name)})
                        if len(results) >= 8: break
            except: pass

        if len(results) < 8:
            try:
                res = requests.get(f"https://itf.minkabu.jp/search/fund?word={urllib.parse.quote(q)}", headers=headers, timeout=3)
                if res.status_code == 200:
                    for a in BeautifulSoup(res.text, 'html.parser').find_all('a', href=re.compile(r'/fund/[0-9A-Za-z]{8}')):
                        code_match = re.search(r'/fund/([0-9A-Za-z]{8})', a['href'])
                        if code_match:
                            code = code_match.group(1).upper(); name = a.get_text(strip=True)
                            if code and name and len(name) > 3 and not any(r["ticker"] == code for r in results):
                                results.append({"ticker": code, "name": unicodedata.normalize('NFKC', name)})
                        if len(results) >= 8: break
            except: pass

    return results[:8]

@app.post("/api/{user_id}/update_price")
def update_price(user_id: str, data: PriceUpdate):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("UPDATE portfolio SET manual_price = %s WHERE user_id = %s AND ticker = %s", (data.current_price, user_id, data.ticker))
    cursor.close(); conn.close(); return {"message": "Success"}

@app.post("/api/{user_id}/trade")
def record_trade(user_id: str, trade: TradeCreate):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    ticker = trade.ticker.strip() if trade.ticker.strip() else trade.name.strip()
    if trade.asset_type == "JP" and not ticker.endswith(".T"): ticker = f"{ticker}.T"
    elif trade.asset_type == "US": ticker = ticker.upper()
    name = trade.name.strip() if trade.name.strip() else ticker
    
    cursor.execute('INSERT INTO transactions (user_id, ticker, type, trade_date, quantity, price, reason) VALUES (%s, %s, %s, %s, %s, %s, %s)', (user_id, ticker, trade.trade_type, trade.trade_date, trade.quantity, trade.price, trade.reason))
    cursor.execute("SELECT * FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker))
    current = cursor.fetchone()
    
    if "BUY" in trade.trade_type:
        if current:
            new_qty = current["quantity"] + trade.quantity
            new_price = ((current["quantity"] * current["average_price"]) + (trade.quantity * trade.price)) / new_qty
            cursor.execute("UPDATE portfolio SET quantity = %s, average_price = %s WHERE user_id = %s AND ticker = %s", (new_qty, new_price, user_id, ticker))
        else:
            cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (%s, %s, %s, %s, %s, NULL)", (user_id, ticker, name, trade.quantity, trade.price))
    elif trade.trade_type == "SELL" and current:
        new_qty = current["quantity"] - trade.quantity
        if new_qty <= 0: cursor.execute("DELETE FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        else: cursor.execute("UPDATE portfolio SET quantity = %s WHERE user_id = %s AND ticker = %s", (new_qty, user_id, ticker))
    cursor.close(); conn.close(); return {"message": "Success"}

@app.get("/api/{user_id}/portfolio")
def get_portfolio(user_id: str):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM portfolio WHERE user_id = %s", (user_id,))
    rows = cursor.fetchall(); cursor.close(); conn.close()
    
    usdjpy_info = get_usdjpy_rate()
    portfolio_data = []; cat_totals = {"日本株": {"current": 0.0, "book": 0.0}, "米国株": {"current": 0.0, "book": 0.0}, "投資信託": {"current": 0.0, "book": 0.0}}
    total_assets = 0.0; total_book = 0.0; total_est_dividend_jpy = 0.0 

    for row in rows:
        item = dict(row); ticker = item["ticker"]; quantity = item["quantity"]; average_price = item["average_price"]
        is_jpy = ticker.endswith(".T")
        is_fund = (len(ticker) == 8 and ticker.isalnum()) or "投信" in item["name"] or "ファンド" in item["name"] or "スリム" in item["name"]
        fx_rate = 1.0 if is_jpy or is_fund else usdjpy_info["rate"]
        
        fetched_price, div_yield = get_asset_data(ticker, is_jpy, is_fund)
        
        if fetched_price > 0: current_price = fetched_price
        elif item.get("manual_price") is not None and item["manual_price"] > 0: current_price = item["manual_price"]
        else: current_price = average_price
            
        if is_fund: 
            current_value_jpy = (quantity * current_price) / 10000.0
            book_value_jpy = (quantity * average_price) / 10000.0
            category = "投資信託"
        elif is_jpy: 
            current_value_jpy = (current_price * quantity)
            book_value_jpy = (average_price * quantity)
            category = "日本株"; total_est_dividend_jpy += current_value_jpy * (div_yield / 100.0)
        else: 
            current_value_jpy = (current_price * quantity) * fx_rate
            book_value_jpy = (average_price * quantity) * fx_rate
            category = "米国株"; total_est_dividend_jpy += current_value_jpy * (div_yield / 100.0)
            
        item.update({"category": category, "is_fund": is_fund, "current_price": current_price, "currency": "JPY" if is_jpy or is_fund else "USD", "current_value_jpy": current_value_jpy, "profit_loss_jpy": current_value_jpy - book_value_jpy, "dividend_yield": div_yield})
        cat_totals[category]["current"] += current_value_jpy; cat_totals[category]["book"] += book_value_jpy
        total_assets += current_value_jpy; total_book += book_value_jpy; portfolio_data.append(item)

    return {"total_assets": total_assets, "total_book": total_book, "usdjpy_rate": usdjpy_info["rate"], "usdjpy_time": usdjpy_info["time"], "category_totals": cat_totals, "portfolio": portfolio_data, "est_dividend_jpy": total_est_dividend_jpy}

@app.get("/api/{user_id}/news")
def get_jp_news(user_id: str):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT name FROM portfolio WHERE user_id = %s AND ticker LIKE '%%.T' AND quantity > 0", (user_id,))
    p_names = [r["name"] for r in cursor.fetchall()]
    cursor.execute("SELECT name FROM watchlist WHERE user_id = %s AND ticker LIKE '%%.T'", (user_id,))
    w_names = [r["name"] for r in cursor.fetchall()]
    cursor.close(); conn.close()

    target_names = list(set(p_names + w_names)) + ["主要市況: 日経平均", "主要市況: S&P500", "主要市況: 為替 ドル円"]
    news_list = []; headers = {"User-Agent": "Mozilla/5.0"}
    for company_name in target_names:
        try:
            res = requests.get(f"https://news.google.com/rss/search?q={urllib.parse.quote(company_name.replace('主要市況: ', '') + ' 株')}&hl=ja&gl=JP&ceid=JP:ja", headers=headers, timeout=5)
            if res.status_code == 200:
                for item in ET.fromstring(res.text).findall('.//item')[:3]:
                    try:
                        dt = parsedate_to_datetime(item.find('pubDate').text)
                        news_list.append({"stock_name": company_name, "title": item.find('title').text, "link": item.find('link').text, "pub_time": dt.strftime("%Y/%m/%d %H:%M"), "timestamp": dt.timestamp()})
                    except: pass
        except: pass
    news_list.sort(key=lambda x: x["timestamp"], reverse=True)
    return news_list[:300]

@app.post("/api/{user_id}/fund_rule")
def add_fund_rule(user_id: str, rule: FundRuleCreate):
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
    else: cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (%s, %s, %s, %s, %s, NULL)", (user_id, rule.ticker, rule.name, total_qty, base_price))
    cursor.close(); conn.close(); return {"message": "Success"}

@app.get("/api/{user_id}/transactions/{category}")
def get_transactions_by_category(user_id: str, category: str):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT t.*, p.name FROM transactions t LEFT JOIN portfolio p ON t.ticker = p.ticker AND p.user_id = t.user_id WHERE t.user_id = %s ORDER BY t.trade_date DESC", (user_id,))
    rows = cursor.fetchall(); cursor.close(); conn.close()
    return [dict(r, name=r["name"] or r["ticker"]) for r in rows if category.upper() == ("FUND" if ((len(r["ticker"]) == 8 and r["ticker"].isalnum()) or "投信" in (r["name"] or r["ticker"]) or "ファンド" in (r["name"] or r["ticker"])) else ("JP" if r["ticker"].endswith(".T") else "US"))]

@app.delete("/api/{user_id}/transaction/{tx_id}")
def delete_transaction(user_id: str, tx_id: int):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM transactions WHERE id = %s AND user_id = %s", (tx_id, user_id))
    cursor.close(); conn.close(); return {"message": "Success"}

@app.delete("/api/{user_id}/delete_stock/{ticker}")
def delete_stock_api(user_id: str, ticker: str):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker))
    cursor.execute("DELETE FROM transactions WHERE user_id = %s AND ticker = %s", (user_id, ticker))
    cursor.execute("DELETE FROM fund_rules WHERE user_id = %s AND ticker = %s", (user_id, ticker))
    cursor.execute("DELETE FROM watchlist WHERE user_id = %s AND ticker = %s", (user_id, ticker))
    cursor.close(); conn.close(); return {"message": "Deleted"}

@app.get("/api/{user_id}/history")
def get_history(user_id: str):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM transactions WHERE user_id = %s ORDER BY trade_date ASC", (user_id,))
    trades = cursor.fetchall(); cursor.close(); conn.close()
    if not trades: return []
    
    usdjpy = get_usdjpy_rate()["rate"]
    price_histories = {}
    for ticker in list(set([t["ticker"] for t in trades])):
        price_histories[ticker] = {}
        try:
            df = yf.Ticker(ticker if ticker.endswith(".T") else (f"{ticker}.T" if (len(ticker)==4 and ticker.isalnum()) else ticker)).history(start=trades[0]["trade_date"])
            for idx, row in df.iterrows():
                if not math.isnan(row["Close"]): price_histories[ticker][idx.strftime("%Y-%m-%d")] = float(row["Close"])
        except: pass

    all_dates = sorted(list(set([d for h in price_histories.values() for d in h.keys()] + [t["trade_date"] for t in trades] + [datetime.now().strftime("%Y-%m-%d")])))
    current_holdings = {t: 0.0 for t in price_histories.keys()}; last_known_price = {t: 0.0 for t in price_histories.keys()}
    trade_index = 0; result = []
    
    for date_str in all_dates:
        while trade_index < len(trades) and trades[trade_index]["trade_date"] <= date_str:
            tr = trades[trade_index]; t = tr["ticker"]
            if "BUY" in tr["type"]: current_holdings[t] += tr["quantity"]
            elif tr["type"] == "SELL": current_holdings[t] -= tr["quantity"]
            last_known_price[t] = tr["price"]; trade_index += 1
            
        day_total = sum([((qty * (price_histories.get(t, {}).get(date_str) or ([p for d, p in price_histories.get(t, {}).items() if d <= date_str][-1:] or [last_known_price.get(t, 0.0)])[0])) / (10000.0 if len(t) == 8 and t.isalnum() else 1.0)) * (1.0 if t.endswith(".T") or (len(t) == 8 and t.isalnum()) else usdjpy) for t, qty in current_holdings.items() if qty > 0])
        if day_total > 0 or date_str == all_dates[-1]: result.append({"date": date_str, "total_assets": round(day_total, 2)})
            
    return result

@app.get("/api/fund_info/{ticker}")
def get_fund_info(ticker: str):
    price, _ = get_asset_data(ticker, False, True)
    return {"ticker": ticker, "price": price}

@app.get("/api/{user_id}/watchlist")
def get_watchlist(user_id: str):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM watchlist WHERE user_id = %s ORDER BY added_date DESC", (user_id,))
    rows = cursor.fetchall(); cursor.close(); conn.close()
    for r in rows:
        price, _ = get_asset_data(r["ticker"], r["ticker"].endswith(".T") or (len(r["ticker"])==4 and r["ticker"].isalnum()), False)
        r["current_price"] = price
        r["currency"] = "¥" if r["ticker"].endswith(".T") or (len(r["ticker"])==4 and r["ticker"].isalnum()) else "$"
    return rows

@app.post("/api/{user_id}/watchlist")
def add_watchlist(user_id: str, item: WatchlistCreate):
    ticker = item.ticker.strip()
    if len(ticker) == 4 and ticker.isalnum(): ticker = f"{ticker}.T" if not ticker.endswith(".T") else ticker
    else: ticker = ticker.upper()
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute('INSERT INTO watchlist (user_id, ticker, name, added_date) VALUES (%s, %s, %s, %s) ON CONFLICT (user_id, ticker) DO NOTHING', (user_id, ticker, item.name, datetime.now().strftime("%Y-%m-%d")))
    cursor.close(); conn.close(); return {"message": "Success"}

@app.delete("/api/{user_id}/watchlist/{ticker}")
def delete_watchlist(user_id: str, ticker: str):
    conn = get_db_connection(); cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist WHERE user_id = %s AND ticker = %s", (user_id, ticker))
    cursor.close(); conn.close(); return {"message": "Success"}
