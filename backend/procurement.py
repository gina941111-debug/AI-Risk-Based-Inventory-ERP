"""
backend/procurement.py
採購管理 AI 工具函式（供應商、採購單、應付帳款）
"""

from .database import run_query
from .auth import check_permission


def get_payables() -> str:
    """查詢應付帳款：採購單待入庫或未結清金額總和與摘要。"""
    if not check_permission(["admin", "warehouse"]):
        return "權限不足：僅店長或倉管可查詢應付帳款。"
    res = run_query("SELECT COALESCE(SUM(total_amount),0) FROM purchase_orders WHERE status IN ('待入庫','已入庫')")
    total = res[0][0] if res else 0
    detail = run_query(
        "SELECT po_id, supplier_id, total_amount, status FROM purchase_orders ORDER BY order_date DESC LIMIT 20"
    )
    out = f"📊 應付帳款（採購單）：總計 **{total:,.0f}** 元\n\n近期採購單：\n"
    for r in detail or []:
        out += f"- 採購單 {r[0]} | 供應商 {r[1]} | 金額 {r[2]:,.0f} | 狀態 {r[3]}\n"
    return out


def get_suppliers_list() -> str:
    """查詢供應商列表：代號、名稱、聯絡人、電話。"""
    if not check_permission(["admin", "warehouse"]):
        return "權限不足：僅店長或倉管可查詢供應商資料。"
    res = run_query("SELECT supplier_id, name, contact, phone FROM suppliers")
    if not res:
        return "目前尚無供應商資料。"
    out = "📋 供應商列表：\n"
    for r in res:
        out += f"- {r[0]} | {r[1]} | 聯絡人 {r[2] or '-'} | {r[3] or '-'}\n"
    return out


def get_purchase_orders_summary() -> str:
    """查詢採購單摘要：單號、供應商、金額、狀態、筆數。"""
    if not check_permission(["admin", "warehouse"]):
        return "權限不足：僅店長或倉管可查詢採購單。"
    res = run_query("SELECT COUNT(*), COALESCE(SUM(total_amount),0) FROM purchase_orders")
    cnt, total = (res[0][0], res[0][1]) if res else (0, 0)
    rows = run_query(
        "SELECT po_id, supplier_id, total_amount, status, order_date FROM purchase_orders ORDER BY order_date DESC LIMIT 15"
    )
    out = f"📋 採購單摘要：共 **{cnt}** 筆，總金額 **{total:,.0f}** 元\n\n最近採購：\n"
    for r in rows or []:
        out += f"- {r[0]} | 供應商 {r[1]} | {r[2]:,.0f} 元 | {r[3]} | {r[4]}\n"
    return out
