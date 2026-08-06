from fastapi import FastAPI, HTTPException
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
    conn.commit()
    conn.close()

init_db()

def get_usdjpy_rate():
    try:
        usdjpy = yf.Ticker("JPY=X")
        hist = usdjpy.history(period="1d")
        if not hist.empty:
            val = float(hist['Close'].iloc[-1])
            if not math.isnan(val):
                return val
        return 155.0
    except Exception:
        return 155.0

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

@app.get("/{user_id}")
def read_user_dashboard(user_id: str):
    if re.match(r"^\d{6}$", user_id):
        return FileResponse("index.html")
    raise HTTPException(status_code=404, detail="会員番号は6桁の数字である必要があります")

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
    # 日本株入力の場合は強制的に.Tを付ける（英字入りティッカー対策）
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
    
    usdjpy = get_usdjpy_rate()
    portfolio_data = []
    
    # 評価額(current)と投資元本(book)を保持する構造に変更
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
        "category_totals": cat_totals, 
        "portfolio": portfolio_data
    }

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
    usdjpy = get_usdjpy_rate()
    
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
