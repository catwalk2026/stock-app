from fastapi import FastAPI, HTTPException, Request, Header
from fastapi.responses import FileResponse
from pydantic import BaseModel
import yfinance as yf
import sqlite3
from datetime import datetime, timedelta
import requests
from bs4 import BeautifulSoup
import jpholiday
import re
import math
import urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

# --- LINE連携用ライブラリ ---
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage, FollowEvent

# --- 定期実行（パトロール）用ライブラリ ---
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

# ▼▼▼ ここに取得した2つの鍵を貼り付けてください ▼▼▼
LINE_CHANNEL_ACCESS_TOKEN = """ここにチャネルアクセストークンを貼り付ける"""
LINE_CHANNEL_SECRET = """c8caf38acc62174908dcff1f782621f6"""
# ▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲▲

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = FastAPI()
DB_PATH = "portfolio.db"

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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS portfolio (
            user_id TEXT,
            ticker TEXT,
            name TEXT,
            quantity REAL,
            average_price REAL,
            manual_price REAL,
            PRIMARY KEY (user_id, ticker)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            ticker TEXT,
            type TEXT,
            trade_date TEXT,
            quantity REAL,
            price REAL,
            reason TEXT
        )
    ''')
    try:
        cursor.execute("ALTER TABLE transactions ADD COLUMN reason TEXT")
    except sqlite3.OperationalError:
        pass

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS fund_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT,
            ticker TEXT,
            name TEXT,
            frequency TEXT,
            monthly_day INTEGER,
            amount REAL,
            start_date TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id TEXT,
            ticker TEXT,
            name TEXT,
            added_date TEXT,
            PRIMARY KEY (user_id, ticker)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS line_users (
            line_user_id TEXT PRIMARY KEY,
            app_user_id TEXT
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sent_news (
            line_user_id TEXT,
            news_link TEXT,
            PRIMARY KEY (line_user_id, news_link)
        )
    ''')
    conn.commit()
    conn.close()

init_db()

# ==========================================
# ニュースのパトロールとPush送信機能
# ==========================================
def check_and_send_news():
    print("ニュースのパトロールを開始します...")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM line_users")
    users = cursor.fetchall()
    
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    yesterday = datetime.now() - timedelta(days=1)

    for u in users:
        line_user_id = u["line_user_id"]
        app_user_id = u["app_user_id"]
        
        cursor.execute("SELECT name FROM portfolio WHERE user_id = ? AND ticker LIKE '%.T' AND quantity > 0", (app_user_id,))
        p_names = [r["name"] for r in cursor.fetchall()]
        cursor.execute("SELECT name FROM watchlist WHERE user_id = ? AND ticker LIKE '%.T'", (app_user_id,))
        w_names = [r["name"] for r in cursor.fetchall()]
        target_names = list(set(p_names + w_names))
        
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
                            if dt.timestamp() < yesterday.timestamp():
                                continue
                        except:
                            continue
                        
                        cursor.execute("SELECT 1 FROM sent_news WHERE line_user_id = ? AND news_link = ?", (line_user_id, link))
                        if not cursor.fetchone():
                            msg_text = f"📰 【{company_name}】の最新ニュース\n\n{title}\n{link}"
                            new_messages.append((msg_text, link))
                            if len(new_messages) >= 5:
                                break
            except Exception as e:
                print("パトロール中のエラー:", e)
            
            if len(new_messages) >= 5:
                break
        
        if new_messages:
            try:
                send_data = [TextSendMessage(text=m[0]) for m in new_messages]
                line_bot_api.push_message(line_user_id, send_data)
                
                for m in new_messages:
                    cursor.execute("INSERT INTO sent_news (line_user_id, news_link) VALUES (?, ?)", (line_user_id, m[1]))
                conn.commit()
                print(f"{app_user_id}に {len(new_messages)}件 の新着ニュースを送りました！")
            except Exception as e:
                print(f"LINE送信エラー ({app_user_id}):", e)

    conn.close()

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
    welcome_msg = (
        "友だち追加ありがとうございます！🎉\n\n"
        "まずは、メニューの「会員連携」をタップして、ポートフォリオ用の会員番号（6桁の英数字）を登録してください📉✨"
    )
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text=welcome_msg)
    )

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    text = event.message.text.strip()
    line_user_id = event.source.user_id

    if "会員連携" in text:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="📝 ダッシュボードに表示されている「会員番号（6桁の英数字）」をそのまま送信してください。\n例: AB1234")
        )
        
    elif "ダッシュボード" in text:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT app_user_id FROM line_users WHERE line_user_id = ?", (line_user_id,))
        row = cursor.fetchone()
        conn.close()

        if row:
            app_user_id = row["app_user_id"]
            # ▼▼▼ ここを実際のアプリのURLに変えてください ▼▼▼
            app_url = f"https://stock-app-xyif.onrender.com/{app_user_id}"
            
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text=f"📊 あなたのダッシュボードはこちらです！\n{app_url}")
            )
        else:
            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(text="⚠️ まだ会員連携が完了していません。\n「会員連携」ボタンをタップして、会員番号を登録してください！")
            )

    elif re.match(r"^[a-zA-Z0-9]{6}$", text):
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("INSERT OR REPLACE INTO line_users (line_user_id, app_user_id) VALUES (?, ?)", (line_user_id, text))
        conn.commit()
        conn.close()
        
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text=f"✅ 会員番号「{text}」との紐付けが完了しました！\n今後、保有銘柄や気になる銘柄の新しいニュースが出たら、いち早くお知らせします📉✨")
        )
        
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="💡 メニューから操作を選ぶか、連携したい会員番号（6桁の英数字）を送信してください！")
        )

# ==========================================
# API・コア機能
# ==========================================
def get_usdjpy_rate():
    fetch_time = datetime.now().strftime("%Y/%m/%d %H:%M")
    try:
        usdjpy = yf.Ticker("JPY=X")
        hist = usdjpy.history(period="1d")
        if not hist.empty:
            val = float(hist['Close'].iloc[-1])
            if not math.isnan(val):
                return {"rate": val, "time": fetch_time}
        return {"rate": 155.0, "time": fetch_time + " (取得エラー)"}
    except Exception:
        return {"rate": 155.0, "time": fetch_time + " (取得エラー)"}

def is_business_day(dt: datetime) -> bool:
    return dt.weekday() not in (5, 6) and not jpholiday.is_holiday(dt)

def get_next_business_day(dt: datetime) -> datetime:
    curr = dt
    while not is_business_day(curr):
        curr += timedelta(days=1)
    return curr

def fetch_latest_fund_price(ticker: str) -> float:
    ticker = ticker.strip()
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        url_minkabu = f"https://itf.minkabu.jp/fund/{ticker}"
        res = requests.get(url_minkabu, headers=headers, timeout=3)
        if res.status_code == 200:
            soup = BeautifulSoup(res.text, 'html.parser')
            price_elem = soup.find('div', class_='stock_price')
            if price_elem:
                num = re.sub(r'[^\d.]', '', price_elem.text)
                if num and float(num) > 100:
                    return float(num)
    except Exception:
        pass
    return 0.0

@app.get("/")
def read_root():
    return FileResponse("index.html")

@app.get("/admin")
def read_admin():
    return FileResponse("admin.html")

@app.get("/{user_id}")
def read_user_dashboard(user_id: str):
    if re.match(r"^[a-zA-Z0-9]{6}$", user_id):
        return FileResponse("index.html")
    raise HTTPException(status_code=404, detail="会員番号は6桁の英数字である必要があります")

# 🌟 新機能：国別・日本語対応のスマート検索 API
@app.get("/api/search_stock")
def search_stock(q: str, asset_type: str = "ALL"):
    if not q: return []
    results = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

    # 1. もし「4桁の数字」なら、日本のサイト（みんかぶ等）から正しい日本語名を取得（ETF対応）
    if q.isdigit() and len(q) == 4 and asset_type in ["JP", "ALL"]:
        try:
            res = requests.get(f"https://minkabu.jp/stock/{q}", headers=headers, timeout=3)
            if res.status_code == 200:
                soup = BeautifulSoup(res.text, 'html.parser')
                title = soup.find('title').text
                # タイトル例: "トヨタ自動車 (7203) : 株価..." -> 最初の部分を取得
                name = title.split('(')[0].strip()
                if name:
                    results.append({"ticker": f"{q}.T", "name": name})
                    return results # 正確な情報が取れたらここで返す
        except Exception:
            pass

    # 2. Yahoo Finance US APIで総合検索（US株や、名称からの検索用）
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(q)}&quotesCount=8&country=JP"
    try:
        res = requests.get(url, headers=headers, timeout=5)
        data = res.json()
        for quote in data.get("quotes", []):
            ticker = quote.get("symbol", "")
            name = quote.get("shortname", quote.get("longname", ticker))
            
            is_jp = ticker.endswith(".T")
            
            # 引数(asset_type)に合わせて結果を振り分け
            if asset_type == "JP" and not is_jp: continue
            if asset_type == "US" and is_jp: continue
            
            if not any(r["ticker"] == ticker for r in results):
                results.append({"ticker": ticker, "name": name})
    except Exception:
        pass
        
    return results

@app.post("/api/{user_id}/update_price")
def update_price(user_id: str, data: PriceUpdate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE portfolio SET manual_price = ? WHERE user_id = ? AND ticker = ?", (data.current_price, user_id, data.ticker))
    conn.commit()
    conn.close()
    return {"message": "Success"}

@app.post("/api/{user_id}/trade")
def record_trade(user_id: str, trade: TradeCreate):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    ticker = trade.ticker.strip() if trade.ticker.strip() else trade.name.strip()
    if trade.asset_type == "JP" and not ticker.endswith(".T"):
        ticker = f"{ticker}.T"
    elif trade.asset_type == "US":
        ticker = ticker.upper()

    name = trade.name.strip() if trade.name.strip() else ticker
    
    cursor.execute('''
        INSERT INTO transactions (user_id, ticker, type, trade_date, quantity, price, reason)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, ticker, trade.trade_type, trade.trade_date, trade.quantity, trade.price, trade.reason))
    
    cursor.execute("SELECT * FROM portfolio WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    current = cursor.fetchone()
    
    if "BUY" in trade.trade_type:
        if current:
            old_qty = current["quantity"]
            old_price = current["average_price"]
            new_qty = old_qty + trade.quantity
            new_price = ((old_qty * old_price) + (trade.quantity * trade.price)) / new_qty
            cursor.execute("UPDATE portfolio SET quantity = ?, average_price = ? WHERE user_id = ? AND ticker = ?", (new_qty, new_price, user_id, ticker))
        else:
            cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (?, ?, ?, ?, ?, ?)",
                           (user_id, ticker, name, trade.quantity, trade.price, trade.price))
    
    elif trade.trade_type == "SELL":
        if current:
            new_qty = current["quantity"] - trade.quantity
            if new_qty <= 0:
                cursor.execute("DELETE FROM portfolio WHERE user_id = ? AND ticker = ?", (user_id, ticker))
            else:
                cursor.execute("UPDATE portfolio SET quantity = ? WHERE user_id = ? AND ticker = ?", (new_qty, user_id, ticker))

    conn.commit()
    conn.close()
    return {"message": "Success"}

@app.get("/api/{user_id}/portfolio")
def get_portfolio(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row 
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM portfolio WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    
    usdjpy_info = get_usdjpy_rate()
    usdjpy = usdjpy_info["rate"]
    usdjpy_time = usdjpy_info["time"]
    
    portfolio_data = []
    cat_totals = {
        "日本株": {"current": 0.0, "book": 0.0}, 
        "米国株": {"current": 0.0, "book": 0.0}, 
        "投資信託": {"current": 0.0, "book": 0.0}
    }
    total_assets = 0.0
    total_book = 0.0
    
    for row in rows:
        item = dict(row)
        ticker = item["ticker"]
        quantity = item["quantity"]
        average_price = item["average_price"]
        manual_price = item.get("manual_price") or average_price
        
        is_jpy = ticker.endswith(".T")
        is_fund = (len(ticker) == 8 and ticker.isalnum()) or "投信" in item["name"] or "ファンド" in item["name"] or "スリム" in item["name"] or "たわら" in item["name"]
        fx_rate = 1.0 if is_jpy or is_fund else usdjpy
        current_price = manual_price
        
        if is_fund and len(ticker) == 8 and ticker.isalnum():
            scraped = fetch_latest_fund_price(ticker)
            if scraped > 0: current_price = scraped
        else:
            try:
                search_target = ticker if ticker.endswith(".T") else (f"{ticker}.T" if ticker.isdigit() or (len(ticker)==4 and ticker[:-1].isdigit()) else ticker)
                stock = yf.Ticker(search_target)
                hist = stock.history(period="1d")
                if not hist.empty:
                    val = float(hist['Close'].iloc[-1])
                    if not math.isnan(val): current_price = val
            except:
                pass
            
        if is_fund:
            current_value_jpy = (quantity * current_price) / 10000.0
            book_value_jpy = (quantity * average_price) / 10000.0
            category = "投資信託"
        elif is_jpy or ticker.endswith(".T"):
            current_value_jpy = (current_price * quantity)
            book_value_jpy = (average_price * quantity)
            category = "日本株"
        else:
            current_value_jpy = (current_price * quantity) * fx_rate
            book_value_jpy = (average_price * quantity) * fx_rate
            category = "米国株"
            
        profit_loss_jpy = current_value_jpy - book_value_jpy
        
        item["category"] = category
        item["is_fund"] = is_fund
        item["current_price"] = current_price
        item["currency"] = "JPY" if is_jpy or is_fund else "USD"
        item["current_value_jpy"] = current_value_jpy
        item["profit_loss_jpy"] = profit_loss_jpy
        
        cat_totals[category]["current"] += current_value_jpy
        cat_totals[category]["book"] += book_value_jpy
        total_assets += current_value_jpy
        total_book += book_value_jpy
        portfolio_data.append(item)

    conn.close()
    return {
        "total_assets": total_assets, 
        "total_book": total_book,
        "usdjpy_rate": usdjpy, 
        "usdjpy_time": usdjpy_time, 
        "category_totals": cat_totals, 
        "portfolio": portfolio_data
    }

# 🌟 新機能：保有株＋気になるリストの両方からニュースを取得
@app.get("/api/{user_id}/news")
def get_jp_news(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # 保有銘柄
    cursor.execute("SELECT name FROM portfolio WHERE user_id = ? AND ticker LIKE '%.T' AND quantity > 0", (user_id,))
    p_names = [r["name"] for r in cursor.fetchall()]
    
    # 気になるリスト
    cursor.execute("SELECT name FROM watchlist WHERE user_id = ? AND ticker LIKE '%.T'", (user_id,))
    w_names = [r["name"] for r in cursor.fetchall()]
    conn.close()

    target_names = list(set(p_names + w_names))

    if not target_names:
        return []

    news_list = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    
    for company_name in target_names:
        query = urllib.parse.quote(f"{company_name} 株")
        url = f"https://news.google.com/rss/search?q={query}&hl=ja&gl=JP&ceid=JP:ja"
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                root = ET.fromstring(res.text)
                items = root.findall('.//item')
                for item in items:
                    title_elem = item.find('title')
                    link_elem = item.find('link')
                    pubdate_elem = item.find('pubDate')
                    
                    title = title_elem.text if title_elem is not None else ""
                    link = link_elem.text if link_elem is not None else ""
                    pub_date = pubdate_elem.text if pubdate_elem is not None else ""
                    
                    try:
                        dt = parsedate_to_datetime(pub_date)
                        ts = dt.timestamp()
                        date_str = dt.strftime("%Y/%m/%d %H:%M")
                        iso_date = dt.strftime("%Y-%m-%d")
                    except Exception:
                        ts = 0
                        date_str = pub_date
                        iso_date = ""

                    news_list.append({
                        "stock_name": company_name,
                        "title": title,
                        "link": link,
                        "pub_time": date_str,
                        "iso_date": iso_date,
                        "timestamp": ts
                    })
        except Exception as e:
            pass

    news_list.sort(key=lambda x: x["timestamp"], reverse=True)
    return news_list[:300]

@app.post("/api/{user_id}/fund_rule")
def add_fund_rule(user_id: str, rule: FundRuleCreate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO fund_rules (user_id, ticker, name, frequency, monthly_day, amount, start_date)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (user_id, rule.ticker, rule.name, rule.frequency, rule.monthly_day, rule.amount, rule.start_date))
    conn.commit()
    
    start_dt = datetime.strptime(rule.start_date, "%Y-%m-%d")
    today = datetime.now()
    base_price = rule.avg_price if rule.avg_price > 0 else fetch_latest_fund_price(rule.ticker)
    if base_price == 0: base_price = 10000.0

    curr = start_dt
    while curr <= today:
        should_buy = False
        actual_date = curr
        if rule.frequency == "DAILY":
            if is_business_day(curr):
                should_buy = True; actual_date = curr
        else:
            if curr.day == rule.monthly_day:
                should_buy = True; actual_date = get_next_business_day(curr)

        if should_buy and actual_date <= today:
            trade_date_str = actual_date.strftime("%Y-%m-%d")
            quantity = (rule.amount / base_price) * 10000.0
            cursor.execute('''
                INSERT INTO transactions (user_id, ticker, type, trade_date, quantity, price, reason)
                VALUES (?, ?, 'BUY_AUTO', ?, ?, ?, ?)
            ''', (user_id, rule.ticker, trade_date_str, quantity, base_price, "自動積立"))
        curr += timedelta(days=1)

    cursor.execute("SELECT SUM(quantity) FROM transactions WHERE user_id = ? AND ticker = ?", (user_id, rule.ticker))
    total_qty = cursor.fetchone()[0] or 0.0

    cursor.execute("SELECT * FROM portfolio WHERE user_id = ? AND ticker = ?", (user_id, rule.ticker))
    if cursor.fetchone():
        cursor.execute("UPDATE portfolio SET quantity = ?, average_price = ? WHERE user_id = ? AND ticker = ?", (total_qty, base_price, user_id, rule.ticker))
    else:
        cursor.execute("INSERT INTO portfolio (user_id, ticker, name, quantity, average_price, manual_price) VALUES (?, ?, ?, ?, ?, ?)",
                       (user_id, rule.ticker, rule.name, total_qty, base_price, base_price))

    conn.commit()
    conn.close()
    return {"message": "Success"}

@app.get("/api/{user_id}/transactions/{category}")
def get_transactions_by_category(user_id: str, category: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT t.*, p.name FROM transactions t LEFT JOIN portfolio p ON t.ticker = p.ticker AND p.user_id = t.user_id WHERE t.user_id = ? ORDER BY t.trade_date DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for r in rows:
        item = dict(r)
        ticker = item["ticker"]
        name = item["name"] or ticker
        is_jpy = ticker.endswith(".T")
        is_fund = (len(ticker) == 8 and ticker.isalnum()) or "投信" in name or "ファンド" in name or "スリム" in name or "たわら" in name
        
        item_cat = "FUND" if is_fund else ("JP" if is_jpy else "US")
        if category.upper() == item_cat:
            item["name"] = name
            result.append(item)
    return result

@app.delete("/api/{user_id}/transaction/{tx_id}")
def delete_transaction(user_id: str, tx_id: int):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM transactions WHERE id = ? AND user_id = ?", (tx_id, user_id))
    conn.commit()
    conn.close()
    return {"message": "Success"}

@app.delete("/api/{user_id}/delete_stock/{ticker}")
def delete_stock_api(user_id: str, ticker: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM portfolio WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    cursor.execute("DELETE FROM transactions WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    cursor.execute("DELETE FROM fund_rules WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    cursor.execute("DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    conn.commit()
    conn.close()
    return {"message": "Deleted"}

@app.get("/api/{user_id}/history")
def get_history(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM transactions WHERE user_id = ? ORDER BY trade_date ASC", (user_id,))
    trades = cursor.fetchall()
    conn.close()
    
    if not trades: return []
    
    first_date_str = trades[0]["trade_date"]
    unique_tickers = list(set([t["ticker"] for t in trades]))
    usdjpy_info = get_usdjpy_rate()
    usdjpy = usdjpy_info["rate"]
    
    price_histories = {}
    for ticker in unique_tickers:
        price_histories[ticker] = {}
        try:
            search_target = ticker if ticker.endswith(".T") else (f"{ticker}.T" if ticker.isdigit() or (len(ticker)==4 and ticker[:-1].isdigit()) else ticker)
            stock = yf.Ticker(search_target)
            df = stock.history(start=first_date_str)
            if not df.empty:
                for idx, row in df.iterrows():
                    val = float(row["Close"])
                    if not math.isnan(val):
                        price_histories[ticker][idx.strftime("%Y-%m-%d")] = val
        except Exception:
            pass

    all_dates_set = set([d for hist in price_histories.values() for d in hist.keys()])
    all_dates_set.update([t["trade_date"] for t in trades])
    all_dates_set.add(datetime.now().strftime("%Y-%m-%d"))
    
    all_dates = sorted(list(all_dates_set))
    if not all_dates: return []

    current_holdings = {t: 0.0 for t in unique_tickers}
    last_known_price = {t: 0.0 for t in unique_tickers}
    trade_index = 0
    num_trades = len(trades)
    
    result = []
    
    for date_str in all_dates:
        while trade_index < num_trades and trades[trade_index]["trade_date"] <= date_str:
            tr = trades[trade_index]
            t = tr["ticker"]
            if "BUY" in tr["type"]: current_holdings[t] += tr["quantity"]
            elif tr["type"] == "SELL": current_holdings[t] -= tr["quantity"]
            last_known_price[t] = tr["price"]
            trade_index += 1
            
        day_total = 0.0
        for t, qty in current_holdings.items():
            if qty > 0:
                price = price_histories.get(t, {}).get(date_str)
                if price is None or math.isnan(price):
                    prices = [p for d, p in price_histories.get(t, {}).items() if d <= date_str]
                    price = prices[-1] if prices else last_known_price.get(t, 0.0)
                
                is_jpy = t.endswith(".T")
                is_fund = len(t) == 8 and t.isalnum()
                fx = 1.0 if is_jpy or is_fund else usdjpy
                
                if is_fund: day_total += (qty * price) / 10000.0
                elif is_jpy: day_total += (qty * price)
                else: day_total += (qty * price) * fx
                    
        if day_total > 0 or date_str == all_dates[-1]:
            result.append({"date": date_str, "total_assets": round(day_total, 2)})
            
    return result

@app.get("/api/fund_info/{ticker}")
def get_fund_info(ticker: str):
    price = fetch_latest_fund_price(ticker)
    return {"ticker": ticker, "price": price}

@app.get("/api/{user_id}/watchlist")
def get_watchlist(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM watchlist WHERE user_id = ? ORDER BY added_date DESC", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    results = []
    usdjpy = get_usdjpy_rate()["rate"]

    for r in rows:
        item = dict(r)
        ticker = item["ticker"]
        price = 0.0
        try:
            search_target = ticker if ticker.endswith(".T") else (f"{ticker}.T" if ticker.isdigit() or (len(ticker)==4 and ticker[:-1].isdigit()) else ticker)
            stock = yf.Ticker(search_target)
            hist = stock.history(period="1d")
            if not hist.empty:
                val = float(hist['Close'].iloc[-1])
                if not math.isnan(val):
                    price = val
        except:
            pass
        
        item["current_price"] = price
        item["currency"] = "¥" if ticker.endswith(".T") or ticker.isdigit() else "$"
        results.append(item)
    return results

@app.post("/api/{user_id}/watchlist")
def add_watchlist(user_id: str, item: WatchlistCreate):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    added_date = datetime.now().strftime("%Y-%m-%d")
    
    ticker = item.ticker.strip()
    if ticker.isdigit() or (len(ticker) == 4 and ticker[:-1].isdigit()):
        if not ticker.endswith(".T"): ticker += ".T"
    else:
        ticker = ticker.upper()

    try:
        cursor.execute("INSERT INTO watchlist (user_id, ticker, name, added_date) VALUES (?, ?, ?, ?)",
                       (user_id, ticker, item.name, added_date))
        conn.commit()
    except sqlite3.IntegrityError:
        pass 
    finally:
        conn.close()
    return {"message": "Added to watchlist"}

@app.delete("/api/{user_id}/watchlist/{ticker}")
def delete_watchlist(user_id: str, ticker: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker))
    conn.commit()
    conn.close()
    return {"message": "Success"}

@app.get("/api/admin/users")
def admin_get_users():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    cursor.execute("SELECT DISTINCT user_id FROM portfolio")
    p_users = [r["user_id"] for r in cursor.fetchall()]
    cursor.execute("SELECT DISTINCT user_id FROM transactions")
    t_users = [r["user_id"] for r in cursor.fetchall()]
    cursor.execute("SELECT DISTINCT user_id FROM fund_rules")
    f_users = [r["user_id"] for r in cursor.fetchall()]
    
    all_users = list(set(p_users + t_users + f_users))
    
    user_data = []
    for uid in all_users:
        cursor.execute("SELECT COUNT(*) as c FROM portfolio WHERE user_id=?", (uid,))
        p_count = cursor.fetchone()["c"]
        cursor.execute("SELECT COUNT(*) as c FROM transactions WHERE user_id=?", (uid,))
        t_count = cursor.fetchone()["c"]
        user_data.append({"user_id": uid, "portfolio_count": p_count, "tx_count": t_count})
        
    conn.close()
    return user_data

@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: str):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM portfolio WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM transactions WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM fund_rules WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM watchlist WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()
    return {"message": "Success"}
