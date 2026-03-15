"""
backend/__init__.py
彙整所有後端模組，對外匯出 tools_mapping 與 ALL_TOOLS 供 AI Agent 使用
"""

from .database import DB_FILE, init_db, run_query
from .auth import check_login, check_permission

# --- AI 工具函式（供 Function Calling 使用）---
from .inventory import (
    check_inventory,
    get_all_inventory,
    update_inventory,
    get_inventory_total_value,
    get_cost_analysis,
    calculate_smart_restocking,
)
from .orders import (
    get_recent_orders,
    create_order,
    get_receivables,
    get_customers_list,
    get_quotations_summary,
)
from .procurement import (
    get_payables,
    get_suppliers_list,
    get_purchase_orders_summary,
)
from .finance import (
    get_ledger_summary,
    get_financial_overview,
    calculate,
)
from .hr import (
    get_employee_info,
    get_payroll_summary,
    get_attendance_summary,
)
from .manufacturing import (
    get_bom_list,
    get_work_orders_status,
)

# 名稱 → 函式的對應表（供 AI Agent 呼叫 function_calls 時使用）
tools_mapping = {
    "check_inventory": check_inventory,
    "get_all_inventory": get_all_inventory,
    "update_inventory": update_inventory,
    "get_employee_info": get_employee_info,
    "get_recent_orders": get_recent_orders,
    "create_order": create_order,
    "get_receivables": get_receivables,
    "get_payables": get_payables,
    "get_customers_list": get_customers_list,
    "get_suppliers_list": get_suppliers_list,
    "get_quotations_summary": get_quotations_summary,
    "get_purchase_orders_summary": get_purchase_orders_summary,
    "get_ledger_summary": get_ledger_summary,
    "get_cost_analysis": get_cost_analysis,
    "get_bom_list": get_bom_list,
    "get_work_orders_status": get_work_orders_status,
    "get_payroll_summary": get_payroll_summary,
    "get_attendance_summary": get_attendance_summary,
    "get_financial_overview": get_financial_overview,
    "get_inventory_total_value": get_inventory_total_value,
    "calculate_smart_restocking": calculate_smart_restocking,
    "calculate": calculate,
}

# 供 AI 多輪呼叫的完整工具列表（含公式計算）
ALL_TOOLS = [
    check_inventory, get_all_inventory, update_inventory, get_employee_info,
    get_recent_orders, create_order, get_receivables, get_payables,
    get_customers_list, get_suppliers_list, get_quotations_summary,
    get_purchase_orders_summary, get_ledger_summary, get_cost_analysis,
    get_bom_list, get_work_orders_status, get_payroll_summary,
    get_attendance_summary, get_financial_overview, get_inventory_total_value,
    calculate_smart_restocking, calculate,
]

__all__ = [
    "DB_FILE", "init_db", "run_query",
    "check_login", "check_permission",
    "tools_mapping", "ALL_TOOLS",
]
