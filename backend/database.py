"""
backend/database.py
資料庫連線、路徑設定、初始化、通用查詢函式
"""

import sqlite3
import os
from datetime import datetime, timedelta


# ==========================================
# 資料庫路徑設定（與程式碼分離，企業可自訂或匯入）
# ==========================================
# 優先順序：環境變數 ERP_DB_PATH > .streamlit/secrets.toml [database] path > 預設 data/erp.db
def _get_db_path():
    path = os.environ.get("ERP_DB_PATH", "").strip()
    if path:
        return path
    try:
        import streamlit as st
        # 企業可於 .streamlit/secrets.toml 設定： [database] path = "D:/company_data/erp.db"
        if hasattr(st, "secrets") and st.secrets.get("database", {}).get("path"):
            return st.secrets["database"]["path"]
    except Exception:
        pass
    return "data/erp.db"


DB_FILE = _get_db_path()


def _ensure_db_dir():
    """確保資料庫所在目錄存在，方便企業指定任意路徑或匯入既有 .db"""
    d = os.path.dirname(DB_FILE)
    if d and not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)


def init_db():
    _ensure_db_dir()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()

    # 使用者與權限
    c.execute('''CREATE TABLE IF NOT EXISTS users (username TEXT PRIMARY KEY, password TEXT, role TEXT, name TEXT)''')
    # 進銷存：商品、倉庫
    c.execute('''CREATE TABLE IF NOT EXISTS warehouses (warehouse_id TEXT PRIMARY KEY, name TEXT, address TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS inventory (product_id TEXT PRIMARY KEY, name TEXT, stock INTEGER, price INTEGER, cost REAL, reorder_point INTEGER, daily_sales INTEGER, barcode TEXT, warehouse_id TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS stock_moves (move_id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, warehouse_id TEXT, qty INTEGER, move_type TEXT, ref_no TEXT, move_date TEXT, note TEXT)''')
    # 採購：供應商、採購單
    c.execute('''CREATE TABLE IF NOT EXISTS suppliers (supplier_id TEXT PRIMARY KEY, name TEXT, contact TEXT, phone TEXT, email TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_orders (po_id TEXT PRIMARY KEY, supplier_id TEXT, order_date TEXT, status TEXT, total_amount REAL, note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS purchase_order_items (id INTEGER PRIMARY KEY AUTOINCREMENT, po_id TEXT, product_id TEXT, qty INTEGER, unit_price REAL)''')
    # 銷售：客戶、報價單、銷售單、收款
    c.execute('''CREATE TABLE IF NOT EXISTS customers (customer_id TEXT PRIMARY KEY, name TEXT, contact TEXT, phone TEXT, email TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS quotations (quote_id TEXT PRIMARY KEY, customer_id TEXT, quote_date TEXT, status TEXT, total_amount REAL, valid_until TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS quotation_items (id INTEGER PRIMARY KEY AUTOINCREMENT, quote_id TEXT, product_id TEXT, qty INTEGER, unit_price REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS orders (order_id TEXT PRIMARY KEY, customer_id TEXT, product_id TEXT, quantity INTEGER, status TEXT, order_date TEXT, total_amount REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS payments (payment_id INTEGER PRIMARY KEY AUTOINCREMENT, ref_type TEXT, ref_id TEXT, amount REAL, payment_date TEXT, note TEXT)''')
    # 財務：應收應付、總帳
    c.execute('''CREATE TABLE IF NOT EXISTS receivables (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT, ref_id TEXT, amount REAL, paid REAL, due_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS payables (id INTEGER PRIMARY KEY AUTOINCREMENT, supplier_id TEXT, ref_id TEXT, amount REAL, paid REAL, due_date TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS general_ledger (id INTEGER PRIMARY KEY AUTOINCREMENT, ledger_date TEXT, account TEXT, debit REAL, credit REAL, description TEXT)''')
    # 生產：BOM、製造工單
    c.execute('''CREATE TABLE IF NOT EXISTS bom (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, component_id TEXT, qty_per REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS work_orders (wo_id TEXT PRIMARY KEY, product_id TEXT, qty_plan INTEGER, qty_done INTEGER DEFAULT 0, status TEXT, start_date TEXT, end_date TEXT)''')
    # 人資
    c.execute('''CREATE TABLE IF NOT EXISTS hr (employee_id TEXT PRIMARY KEY, name TEXT, department TEXT, role TEXT, salary REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS payroll (id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT, period TEXT, base_salary REAL, bonus REAL, deduction REAL)''')
    c.execute('''CREATE TABLE IF NOT EXISTS attendance (id INTEGER PRIMARY KEY AUTOINCREMENT, employee_id TEXT, work_date TEXT, check_in TEXT, check_out TEXT, status TEXT)''')
    # 永續 ESG：碳係數、供應鏈事件
    c.execute('''CREATE TABLE IF NOT EXISTS carbon_factors (id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT, scope INTEGER, kg_co2_per_unit REAL, note TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS supply_chain_events (id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, region TEXT, country TEXT, impact_days INTEGER, description TEXT, created_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS esg_targets (id INTEGER PRIMARY KEY AUTOINCREMENT, target_year INTEGER, scope INTEGER, baseline_kg_co2 REAL, target_kg_co2 REAL, note TEXT)''')
    # 永續 ESG：風險管理係數（地區/事件類型/供應商類別 → 風險分數 0–100、權重）
    c.execute('''CREATE TABLE IF NOT EXISTS esg_risk_factors (id INTEGER PRIMARY KEY AUTOINCREMENT, risk_type TEXT, risk_key TEXT, risk_score REAL, weight REAL, note TEXT, updated_at TEXT, UNIQUE(risk_type, risk_key))''')
    # 客戶關係管理 (CRM)：通訊紀錄
    c.execute('''CREATE TABLE IF NOT EXISTS crm_communications (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id TEXT, comm_type TEXT, subject TEXT, content TEXT, comm_date TEXT, created_by TEXT, created_at TEXT)''')

    # 供應商擴充：地理位置與風險（供應鏈地圖、ESG）
    try:
        c.execute("SELECT country FROM suppliers LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE suppliers ADD COLUMN country TEXT")
        c.execute("ALTER TABLE suppliers ADD COLUMN region TEXT")
        c.execute("ALTER TABLE suppliers ADD COLUMN latitude REAL")
        c.execute("ALTER TABLE suppliers ADD COLUMN longitude REAL")
        c.execute("ALTER TABLE suppliers ADD COLUMN risk_level TEXT")

    try:
        c.execute("SELECT customer_id FROM orders LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE orders ADD COLUMN customer_id TEXT")
        c.execute("ALTER TABLE orders ADD COLUMN total_amount REAL")
    try:
        c.execute("SELECT cost, barcode, warehouse_id FROM inventory LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE inventory ADD COLUMN cost REAL")
        c.execute("ALTER TABLE inventory ADD COLUMN barcode TEXT")
        c.execute("ALTER TABLE inventory ADD COLUMN warehouse_id TEXT")
    # 若已新增欄位，回填既有資料的空值（避免商品管理出現空白欄位）
    try:
        c.execute("UPDATE inventory SET barcode = product_id WHERE barcode IS NULL OR TRIM(barcode) = ''")
        c.execute("UPDATE inventory SET warehouse_id = 'WH01' WHERE warehouse_id IS NULL OR TRIM(warehouse_id) = ''")
    except Exception:
        pass
    try:
        c.execute("SELECT salary FROM hr LIMIT 1")
    except sqlite3.OperationalError:
        c.execute("ALTER TABLE hr ADD COLUMN salary REAL")

    # 永續 ESG：若有新欄位則為既有供應商寫入示範經緯度（台灣）
    try:
        c.execute("SELECT latitude FROM suppliers LIMIT 1")
        c.execute("UPDATE suppliers SET country='台灣', region='北區', latitude=25.0330, longitude=121.5654, risk_level='低' WHERE supplier_id='SUP01'")
        c.execute("UPDATE suppliers SET country='台灣', region='北區', latitude=25.0479, longitude=121.5318, risk_level='低' WHERE supplier_id='SUP02'")
    except sqlite3.OperationalError:
        pass

    try:
        c.execute("PRAGMA table_info(customers)")
        columns = [col[1] for col in c.fetchall()]
        if 'contact' not in columns:
            c.execute("ALTER TABLE customers ADD COLUMN contact TEXT")
        if 'phone' not in columns:
            c.execute("ALTER TABLE customers ADD COLUMN phone TEXT")
        if 'email' not in columns:
            c.execute("ALTER TABLE customers ADD COLUMN email TEXT")
    except sqlite3.OperationalError:
        pass

    # Insert Mock Data if empty
    c.execute("SELECT COUNT(*) FROM users")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO users VALUES ('admin', 'admin', 'admin', '系統管理員')")
        c.execute("INSERT INTO users VALUES ('hr1', 'hr1', 'hr', '人資主管')")
        c.execute("INSERT INTO users VALUES ('wh1', 'wh1', 'warehouse', '倉管人員')")
        c.execute("INSERT INTO users VALUES ('sales1', 'sales1', 'sales', '業務代表')")

        c.execute("INSERT INTO warehouses VALUES ('WH01', '主倉庫', '新北市板橋區')")
        c.execute("INSERT INTO warehouses VALUES ('WH02', '二倉', '桃園市')")

        inventory_data = [
            ("P001", "高階筆記型電腦", 150, 45000, 38000, 50, 5, "6901234567890", "WH01"),
            ("P002", "無線滑鼠", 500, 800, 450, 100, 20, "6901234567891", "WH01"),
            ("P003", "機械鍵盤", 120, 2500, 1800, 50, 5, "6901234567892", "WH01"),
            ("P004", "螢幕顯示器", 30, 6000, 4800, 50, 10, "6901234567893", "WH01"),
        ]
        for row in inventory_data:
            c.execute("INSERT OR IGNORE INTO inventory (product_id, name, stock, price, cost, reorder_point, daily_sales, barcode, warehouse_id) VALUES (?,?,?,?,?,?,?,?,?)",
                      (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[8]))

        c.execute("INSERT OR IGNORE INTO suppliers (supplier_id, name, contact, phone, email) VALUES ('SUP01', '鍵鼠供應商', '張先生', '02-12345678', 'sup@example.com')")
        c.execute("INSERT OR IGNORE INTO suppliers (supplier_id, name, contact, phone, email) VALUES ('SUP02', '螢幕原廠', '李小姐', '03-87654321', 'lcd@example.com')")
        c.execute("INSERT OR IGNORE INTO customers VALUES ('C001', '科技公司A', '王經理', '02-11112222', 'a@example.com')")
        c.execute("INSERT OR IGNORE INTO customers VALUES ('C002', '零售通路B', '陳主任', '02-33334444', 'b@example.com')")

        hr_data = [
            ("E001", "王小明", "業務部", "資深業務", 45000),
            ("E002", "李美鳳", "人資部", "HR 經理", 55000),
        ]
        for row in hr_data:
            c.execute("INSERT OR IGNORE INTO hr (employee_id, name, department, role, salary) VALUES (?,?,?,?,?)", row)

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        old_str = (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S")
        c.execute("INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", ("ORD-20231026-001", "C001", "P001", 3, "處理中", old_str, 135000))
        c.execute("INSERT OR IGNORE INTO orders VALUES (?, ?, ?, ?, ?, ?, ?)", ("ORD-20231025-002", "C002", "P002", 15, "已出貨", now_str, 12000))

    conn.commit()
    conn.close()


def run_query(query, params=(), fetch=True):
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute(query, params)
    if fetch:
        res = c.fetchall()
    else:
        conn.commit()
        res = c.lastrowid
    conn.close()
    return res