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

# --- 定期実行（パトロール）用ライブラリ ---
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

LINE_CHANNEL_ACCESS_TOKEN = "rlJ1YRFK3hCEYnrfCe5k9kO2gjyX3YkqhfdAvnT28lWoC/9Q6NTtPdBNvGU6jVWunuf7k6NPAg/d2r39X+IxD4mlNjs2bH4krV2B7zWilto5IHSvo7QXkKbIxa0GNvVN2SK9b2AH03Rs/M6VrJBIlwdB04t89/1O/w1cDnyilFU="
LINE_CHANNEL_SECRET = "c8caf38acc62174908dcff1f782621f6"

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = FastAPI()

DATABASE_URL = os.environ.get("DATABASE_URL")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()

# ==========================================
# 🌟 JPX（日本取引所グループ）全上場銘柄データの読み込み
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
            next(reader, None) # ヘッダーをスキップ
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

def get_db_connection():
    if not DATABASE_URL:
        raise Exception("DATABASE_URLが設定されていません。")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn

def get_ai_summary(title: str) -> str:
    if not GEMINI_API_KEY:
        return "GEMINI_API_KEYがRenderに設定されていません。"
    
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    
    prompt = f"""以下の金融ニュース見出しの文章・テキストに含まれる事実のみに基づいて、投資初心者向けに2〜3行で分かりやすく要約し、最後に相場への一般的な影響(ポジティブ/ネガティブ/中立など)を判定してください。

【厳重注意事項】
ニュース見出しのテキストに書かれていない独自の情報（企業の過去の履歴や上場・未上場などのステータス）は絶対に推測したり言及したりしないでください。テキストに書かれている事実のみを解説してください。

ニュース見出し: {title}"""

    payload = {
        "contents": [{
            "parts": [{"text": prompt}]
        }]
    }
    
    try:
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.status_code == 200:
            data = res.json()
            return data['candidates'][0]['content']['parts'][0]['text'].strip()
        else:
            return f"AI解説の取得失敗 (エラーコード: {res.status_code})"
    except Exception as e:
        print("Gemini API Error Detail:", e)
        return "AIの解説取得に一時的に失敗しました。"

class TradeCreate(BaseModel):
    ticker: str = ""
    name: str
    trade_type: str
    asset_type: str
    trade_date: str
    quantity: float
    price: float
    reason: str = ""

class FundRuleCreate(BaseModel):
    ticker: str
    name: str
    frequency: str
    monthly_day: int = 1
    amount: float
    avg_price: float = 10000.0
    start_date: str

class PriceUpdate(BaseModel):
    ticker: str
    current_price: float

class WatchlistCreate(BaseModel):
    ticker: str
    name: str

def init_db():
    if not DATABASE_URL: return
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolio (user_id TEXT, ticker TEXT, name TEXT, quantity REAL, average_price REAL, manual_price REAL, PRIMARY KEY (user_id, ticker));
            CREATE TABLE IF NOT EXISTS transactions (id SERIAL PRIMARY KEY, user_id TEXT, ticker TEXT, type TEXT, trade_date TEXT, quantity REAL, price REAL, reason TEXT);
            CREATE TABLE IF NOT EXISTS fund_rules (id SERIAL PRIMARY KEY, user_id TEXT, ticker TEXT, name TEXT, frequency TEXT, monthly_day INTEGER, amount REAL, start_date TEXT);
            CREATE TABLE IF NOT EXISTS watchlist (user_id TEXT, ticker TEXT, name TEXT, added_date TEXT, PRIMARY KEY (user_id, ticker));
            CREATE TABLE IF NOT EXISTS line_users (line_user_id TEXT PRIMARY KEY, app_user_id TEXT);
            CREATE TABLE IF NOT EXISTS sent_news (line_user_id TEXT, news_link TEXT, PRIMARY KEY (line_user_id, news_link));
            
            -- 🌟 新規追加: 価格と配当利回りを1日1回だけ保存するキャッシュテーブル
            CREATE TABLE IF NOT EXISTS asset_cache (ticker TEXT PRIMARY KEY, price REAL, div_yield REAL, last_updated TEXT);
        ''')
        cursor.close()
        conn.close()
    except Exception as e:
        print("DB Init Error:", e)

init_db()

# 🌟 為替レートもキャッシュ化（通信エラー防止）
def get_usdjpy_rate():
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT price FROM asset_cache WHERE ticker = 'USDJPY' AND last_updated = %s", (today_str,))
    row = cursor.fetchone()
    
    if row and row["price"] > 100:
        cursor.close(); conn.close()
        return {"rate": row["price"], "time": f"{today_str} (取得済)"}
        
    fetch_time = datetime.now().strftime("%Y/%m/%d %H:%M")
    rate = 0.0
    try:
        res = requests.get("https://open.er-api.com/v6/latest/USD", timeout=3)
        if res.status_code == 200:
            val = res.json().get("rates", {}).get("JPY")
            if val and val > 100: rate = float(val)
    except: pass
    
    if rate == 0.0:
        try:
            usdjpy = yf.Ticker("JPY=X")
            hist = usdjpy.history(period="1d")
            if not hist.empty:
                val = float(hist['Close'].iloc[-1])
                if not math.isnan(val) and val > 100: rate = val
        except: pass
        
    if rate == 0.0:
        cursor.execute("SELECT price FROM asset_cache WHERE ticker = 'USDJPY'")
        old_row = cursor.fetchone()
        rate = old_row["price"] if old_row else 155.0
        fetch_time = "前回取得値"
        
    cursor.execute('''
        INSERT INTO asset_cache (ticker, price, div_yield, last_updated) 
        VALUES ('USDJPY', %s, 0.0, %s) 
        ON CONFLICT (ticker) DO UPDATE SET price = EXCLUDED.price, last_updated = EXCLUDED.last_updated
    ''', (rate, today_str))
    cursor.close(); conn.close()
    
    return {"rate": rate, "time": fetch_time}

# ==========================================
# 🌟 資産データのオンデマンド・キャッシュ取得（新設）
# ==========================================
def get_asset_data(ticker: str, is_jpy: bool, is_fund: bool):
    ticker = ticker.strip().upper()
    today_str = datetime.now().strftime("%Y-%m-%d")
    
    conn = get_db_connection()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
    # 1. DBから今日のキャッシュを確認
    cursor.execute("SELECT price, div_yield, last_updated FROM asset_cache WHERE ticker = %s", (ticker,))
    row = cursor.fetchone()
    
    if row and row["last_updated"] == today_str and row["price"] > 0:
        cursor.close(); conn.close()
        return row["price"], row["div_yield"]
        
    # 2. キャッシュがない or 古い場合は外部から取得
    price = 0.0
    div_yield = 0.0
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    if is_fund and len(ticker) == 8 and ticker.isalnum():
        # 投信: みんかぶ → Yahoo → 日経 の3段スクレイピング
        try:
            m_url = f"https://itf.minkabu.jp/fund/{ticker}"
            res = requests.get(m_url, headers=headers, timeout=5)
            if res.status_code == 200:
                html = res.text
                idx = html.find("基準価額")
                if idx != -1:
                    text_only = re.sub(r'<[^>]+>', '', html[idx:idx+300])
                    nums = re.findall(r'([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})\s*円', text_only)
                    if nums: price = float(nums[0].replace(',', ''))
        except: pass

        if price == 0.0:
            try:
                y_url = f"https://finance.yahoo.co.jp/quote/{ticker}"
                res = requests.get(y_url, headers=headers, timeout=5)
                if res.status_code == 200:
                    text_only = re.sub(r'<[^>]+>', '', res.text)
                    match = re.search(r'基準価額[^0-9]*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})', text_only)
                    if match: price = float(match.group(1).replace(',', ''))
            except: pass

        if price == 0.0:
            try:
                n_url = f"https://www.nikkei.com/nkd/fund/?fcode={ticker}"
                res = requests.get(n_url, headers=headers, timeout=5)
                if res.status_code == 200:
                    text_only = re.sub(r'<[^>]+>', '', res.text)
                    match = re.search(r'基準価額[^0-9]*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{4,})', text_only)
                    if match: price = float(match.group(1).replace(',', ''))
            except: pass
            
        div_yield = 0.0 # 投信は基本再投資のため0

    else:
        # 株: yfinanceからの取得
        try:
            stock_ticker = ticker if not is_jpy else (f"{ticker}.T" if (len(ticker)==4 and ticker.isalnum()) else ticker)
            stock = yf.Ticker(stock_ticker)
            hist = stock.history(period="1d")
            if not hist.empty:
                val = float(hist['Close'].iloc[-1])
                if not math.isnan(val): price = val
            
            info = stock.info
            if info and info.get("dividendRate") and price > 0:
                div_yield = (float(info.get("dividendRate")) / price) * 100.0
            elif info and info.get("dividendYield"):
                div_yield = float(info["dividendYield"]) * 100.0
            
            if div_yield == 0.0:
                divs = stock.dividends
                if not divs.empty:
                    one_year_ago = datetime.now(divs.index.tzinfo) - timedelta(days=365)
                    recent_divs = divs[divs.index >= one_year_ago]
                    total_div = float(recent_divs.sum())
                    if total_div > 0 and price > 0:
                        div_yield = (total_div / price) * 100.0
        except: pass

    # 取得失敗時のフォールバック処理
    if div_yield == 0.0 or math.isnan(div_yield):
        if is_jpy: div_yield = 2.5
        elif not is_fund: div_yield = 1.5
        else: div_yield = 0.0
        
    if price == 0.0 and row:
        price = row["price"] # 取得失敗時は前日のキャッシュを使い回す

    # 3. 取得した結果をDBに保存（キャッシュ更新）
    if price > 0:
        cursor.execute('''
            INSERT INTO asset_cache (ticker, price, div_yield, last_updated) 
            VALUES (%s, %s, %s, %s) 
            ON CONFLICT (ticker) DO UPDATE SET price = EXCLUDED.price, div_yield = EXCLUDED.div_yield, last_updated = EXCLUDED.last_updated
        ''', (ticker, price, div_yield, today_str))
        
    cursor.close(); conn.close()
    return price, div_yield

# ==========================================
# ニュースのパトロールとPush送信機能
# ==========================================
def check_and_send_news():
    if not DATABASE_URL: return
    try:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT * FROM line_users")
        users = cursor.fetchall()
        
        headers = {"User-Agent": "Mozilla/5.0"}
        yesterday = datetime.now() - timedelta(days=1)
        market_targets = ["日経平均", "S&P500", "為替 ドル円"]

        for u in users:
            line_user_id = u["line_user_id"]
            app_user_id = u["app_user_id"]
            
            cursor.execute("SELECT name FROM portfolio WHERE user_id = %s AND ticker LIKE '%%.T' AND quantity > 0", (app_user_id,))
            p_names = [r["name"] for r in cursor.fetchall()]
            cursor.execute("SELECT name FROM watchlist WHERE user_id = %s AND ticker LIKE '%%.T'", (app_user_id,))
            w_names = [r["name"] for r in cursor.fetchall()]
            
            target_names = list(set(p_names + w_names + market_targets))
            new_messages = []
            
            for company_name in target_names:
                query = urllib.parse.quote(f"{company_name} 株")
                url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
                try:
                    res = requests.get(url, headers=headers, timeout=5)
                    if res.status_code == 200:
                        root = ET.fromstring(res.text)
                        items = root.findall('.//item')
                        for item in items[:5]:
                            link = item.find('link').text if item.find('link') is not None else ""
                            title = item.find('title').text if item.find('title') is not None else ""
                            pub_date_str = item.find('pubDate').text if item.find('pubDate') is not None else ""
                            try:
                                dt = parsedate_to_datetime(pub_date_str)
                                if dt.timestamp() < yesterday.timestamp(): continue
                            except: continue
                            
                            cursor.execute("SELECT 1 FROM sent_news WHERE line_user_id = %s AND news_link = %s", (line_user_id, link))
                            if not cursor.fetchone():
                                ai_summary = get_ai_summary(title)
                                msg_text = f"📰 【{company_name}】の最新ニュース\n\n{title}\n\n💡 AI解説:\n{ai_summary}\n\n{link}"
                                new_messages.append((msg_text, link))
                                if len(new_messages) >= 3: break
                except Exception: pass
                if len(new_messages) >= 3: break
            
            if new_messages:
                try:
                    send_data = [TextSendMessage(text=m[0]) for m in new_messages]
                    line_bot_api.push_message(line_user_id, send_data)
                    for m in new_messages:
                        cursor.execute("INSERT INTO sent_news (line_user_id, news_link) VALUES (%s, %s) ON CONFLICT DO NOTHING", (line_user_id, m[1]))
                except Exception: pass
        cursor.close()
        conn.close()
    except Exception: pass

scheduler = BackgroundScheduler()
scheduler.add_job(check_and_send_news, 'interval', minutes=60)
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ==========================================
# LINE Bot 用のWebhook受け取り口
# ==========================================
@app.post("/callback")
async def callback(request: Request, x_line_signature: str = Header(None)):
    body = await request.body()
    try:
        handler.handle(body.decode("utf-8"), x_line_signature)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")
    return "OK"

@handler.add(FollowEvent)
def handle_follow(event):
    line_bot_api.reply_message(event.reply_token, TextSendMessage(text="友だち追加ありがとうございます！🎉\n\nまずは、メニューの「会員連携」をタップして、ポートフォリオ用の会員番号（6桁の英数字）を登録してください📉✨"))

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    line_user_id = event.source.user_id

    if "会員連携" in text:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="📝 ダッシュボードに表示されている「会員番号（6桁の英数字）」をそのまま送信してください。\n例: AB1234"))
        
    elif "ダッシュボード" in text:
        conn = get_db_connection()
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute("SELECT app_user_id FROM line_users WHERE line_user_id = %s", (line_user_id,))
        row = cursor.fetchone()
        cursor.close(); conn.close()

        if row:
            app_url = f"https://stock-app-xyif.onrender.com/{row['app_user_id']}"
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"📊 あなたのダッシュボードはこちらです！\n{app_url}"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ まだ会員連携が完了していません。\n「会員連携」ボタンをタップして、会員番号を登録してください！"))

    elif re.match(r"^[a-zA-Z0-9]{6}$", text):
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO line_users (line_user_id, app_user_id) VALUES (%s, %s) ON CONFLICT (line_user_id) DO UPDATE SET app_user_id = EXCLUDED.app_user_id", (line_user_id, text))
        cursor.close(); conn.close()
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 会員番号「{text}」との紐付けが完了しました！\n今後、保有銘柄や市況の新しいニュースが出たらAIの解説付きでお知らせします📉✨"))
        
    elif text == "ニューステスト":
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="🚀 ニューステスト隊が出動しました！最新ニュースをAIが要約してきます...🤖💭"))
        try:
            conn = get_db_connection()
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute("SELECT app_user_id FROM line_users WHERE line_user_id = %s", (line_user_id,))
            row = cursor.fetchone()
            
            if not row:
                line_bot_api.push_message(line_user_id, TextSendMessage(text="⚠️ 先に会員連携を済ませてください！"))
                cursor.close(); conn.close(); return

            app_user_id = row["app_user_id"]
            
            cursor.execute("SELECT name FROM portfolio WHERE user_id = %s AND ticker LIKE '%%.T'", (app_user_id,))
            p_names = [r["name"] for r in cursor.fetchall()]
            
            cursor.execute("SELECT name FROM watchlist WHERE user_id = %s AND ticker LIKE '%%.T'", (app_user_id,))
            w_names = [r["name"] for r in cursor.fetchall()]
            cursor.close(); conn.close()

            market_targets = ["日経平均", "S&P500", "為替 ドル円"]
            target_names = list(set(p_names + w_names + market_targets))

            headers = {"User-Agent": "Mozilla/5.0"}
            new_messages = []
            for company_name in target_names:
                query = urllib.parse.quote(f"{company_name} 株")
                url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
                res = requests.get(url, headers=headers, timeout=5)
                if res.status_code == 200:
                    root = ET.fromstring(res.text)
                    items = root.findall('.//item')
                    if items:
                        item = items[0] 
                        title = item.find('title').text
                        link = item.find('link').text
                        ai_summary = get_ai_summary(title)
                        new_messages.append(f"📰 【テスト配信: {company_name}】\n\n{title}\n\n💡 AI解説:\n{ai_summary}\n\n{link}")
                        if len(new_messages) >= 2: break 

            if new_messages:
                send_data = [TextSendMessage(text=m) for m in new_messages]
                line_bot_api.push_message(line_user_id, send_data)
            else:
                line_bot_api.push_message(line_user_id, TextSendMessage(text="😢 ニュースが見つかりませんでした。"))
        except Exception as e:
            print("テストエラー:", e)

    else:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="💡 メニューから操作を選ぶか、連携したい会員番号（6桁の英数字）を送信してください！"))

# ==========================================
# API・コア機能
# ==========================================
@app.get("/api/ai_summary")
def api_ai_summary(title: str):
    summary = get_ai_summary(title)
    return {"summary": summary}

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

@app.get("/api/search_stock")
def search_stock(q: str, asset_type: str = "ALL"):
    if not q: return []
    results = []
    q_str = q.strip().lower()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/115.0.0.0 Safari/537.36",
        "Referer": "https://finance.yahoo.co.jp/"
    }

    if asset_type in ["JP", "ALL"]:
        for item in JPX_STOCKS:
            if q_str in item["code"].lower() or q_str in item["name"].lower():
                results.append({"ticker": item["ticker"], "name": item["name"]})
                if len(results) >= 8: break
        if results and asset_type == "JP": return results[:8]

    if asset_type in ["US", "ALL"] and len(results) < 8:
        try:
            res = requests.get(f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=8&country=US", headers=headers, timeout=3)
            if res.status_code == 200:
                for quote in res.json().get("quotes", []):
                    ticker = quote.get("symbol", "")
                    name = quote.get("shortname", quote.get("longname", ticker))
                    if asset_type == "US" and ticker.endswith(".T"): continue
                    if not any(r["ticker"] == ticker for r in results):
                        results.append({"ticker": ticker, "name": name})
        except Exception: pass

    if asset_type == "FUND" or (asset_type == "ALL" and len(results) < 8):
        if len(q_str) == 8 and q_str.isalnum():
            try:
                fund_url = f"https://itf.minkabu.jp/fund/{q_str.upper()}"
                res = requests.get(fund_url, headers=headers, timeout=3)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, 'html.parser')
                    name = ""
                    h1 = soup.find('h1')
                    if h1: name = h1.get_text(strip=True)
                    if not name:
                        title_elem = soup.find('title')
                        if title_elem: name = title_elem.text.split('|')[0].split('-')[0].strip()
                    if name:
                        results.append({"ticker": q_str.upper(), "name": name})
            except Exception: pass

        if not results:
            try:
                yj_url = f"https://finance.yahoo.co.jp/api/v1/finance/suggest/realtime?query={urllib.parse.quote(q)}"
                res = requests.get(yj_url, headers=headers, timeout=3)
                if res.status_code == 200:
                    for item in res.json().get("results", []):
                        code = item.get("code", "")
                        name = item.get("name", "")
                        if code and name and len(code) == 8 and code.isalnum():
                            if not any(r["ticker"] == code for r in results):
                                results.append({"ticker": code, "name": name})
            except Exception: pass

        if not results:
            try:
                minkabu_url = f"https://minkabu.jp/search?query={urllib.parse.quote(q)}"
                res = requests.get(minkabu_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=3)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, 'html.parser')
                    for a in soup.find_all('a', href=re.compile(r'/fund/[0-9A-Za-z]{8}')):
                        code_match = re.search(r'/fund/([0-9A-Za-z]{8})', a['href'])
                        if code_match:
                            code = code_match.group(1).upper()
                            name = a.text.strip()
                            if code and name and len(name) < 40 and not any(r["ticker"] == code for r in results):
                                results.append({"ticker": code, "name": name})
                        if len(results) >= 8: break
            except Exception: pass

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
            cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (%s, %s, %s, %s, %s, %s)", (user_id, ticker, name, trade.quantity, trade.price, trade.price))
    elif trade.trade_type == "SELL" and current:
        new_qty = current["quantity"] - trade.quantity
        if new_qty <= 0: cursor.execute("DELETE FROM portfolio WHERE user_id = %s AND ticker = %s", (user_id, ticker))
        else: cursor.execute("UPDATE portfolio SET quantity = %s WHERE user_id = %s AND ticker = %s", (new_qty, user_id, ticker))
    cursor.close(); conn.close(); return {"message": "Success"}

# 🌟 大幅修正: キャッシュを使った爆速ポートフォリオ取得！
@app.get("/api/{user_id}/portfolio")
def get_portfolio(user_id: str):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT * FROM portfolio WHERE user_id = %s", (user_id,))
    rows = cursor.fetchall(); cursor.close(); conn.close()
    
    usdjpy_info = get_usdjpy_rate()
    portfolio_data = []; cat_totals = {"日本株": {"current": 0.0, "book": 0.0}, "米国株": {"current": 0.0, "book": 0.0}, "投資信託": {"current": 0.0, "book": 0.0}}
    total_assets = 0.0; total_book = 0.0
    total_est_dividend_jpy = 0.0 

    for row in rows:
        item = dict(row); ticker = item["ticker"]; quantity = item["quantity"]; average_price = item["average_price"]
        manual_price = item.get("manual_price") or average_price
        is_jpy = ticker.endswith(".T")
        is_fund = (len(ticker) == 8 and ticker.isalnum()) or "投信" in item["name"] or "ファンド" in item["name"] or "スリム" in item["name"]
        fx_rate = 1.0 if is_jpy or is_fund else usdjpy_info["rate"]
        
        # ★ キャッシュから価格と配当利回りを取得
        fetched_price, div_yield = get_asset_data(ticker, is_jpy, is_fund)
        
        # マニュアル価格が設定されていなければ、取得した価格を反映
        current_price = manual_price
        if item.get("manual_price") is None and fetched_price > 0:
            current_price = fetched_price
            
        if is_fund: 
            current_value_jpy = (quantity * current_price) / 10000.0
            book_value_jpy = (quantity * average_price) / 10000.0
            category = "投資信託"
        elif is_jpy: 
            current_value_jpy = (current_price * quantity)
            book_value_jpy = (average_price * quantity)
            category = "日本株"
            total_est_dividend_jpy += current_value_jpy * (div_yield / 100.0)
        else: 
            current_value_jpy = (current_price * quantity) * fx_rate
            book_value_jpy = (average_price * quantity) * fx_rate
            category = "米国株"
            total_est_dividend_jpy += current_value_jpy * (div_yield / 100.0)
            
        item.update({
            "category": category, 
            "is_fund": is_fund, 
            "current_price": current_price, 
            "currency": "JPY" if is_jpy or is_fund else "USD", 
            "current_value_jpy": current_value_jpy, 
            "profit_loss_jpy": current_value_jpy - book_value_jpy,
            "dividend_yield": div_yield
        })
        cat_totals[category]["current"] += current_value_jpy; cat_totals[category]["book"] += book_value_jpy
        total_assets += current_value_jpy; total_book += book_value_jpy
        portfolio_data.append(item)

    return {
        "total_assets": total_assets, 
        "total_book": total_book, 
        "usdjpy_rate": usdjpy_info["rate"], 
        "usdjpy_time": usdjpy_info["time"], 
        "category_totals": cat_totals, 
        "portfolio": portfolio_data,
        "est_dividend_jpy": total_est_dividend_jpy 
    }

@app.get("/api/{user_id}/news")
def get_jp_news(user_id: str):
    conn = get_db_connection(); cursor = conn.cursor(cursor_factory=RealDictCursor)
    cursor.execute("SELECT name FROM portfolio WHERE user_id = %s AND ticker LIKE '%%.T' AND quantity > 0", (user_id,))
    p_names = [r["name"] for r in cursor.fetchall()]
    cursor.execute("SELECT name FROM watchlist WHERE user_id = %s AND ticker LIKE '%%.T'", (user_id,))
    w_names = [r["name"] for r in cursor.fetchall()]
    cursor.close(); conn.close()

    market_targets = ["主要市況: 日経平均", "主要市況: S&P500", "主要市況: 為替 ドル円"]
    target_names = list(set(p_names + w_names)) + market_targets

    news_list = []
    headers = {"User-Agent": "Mozilla/5.0"}
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
    else: cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (%s, %s, %s, %s, %s, %s)", (user_id, rule.ticker, rule.name, total_qty, base_price, base_price))
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

# 🌟 検索から選ばれた直後の自動価格取得もキャッシュを利用
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
