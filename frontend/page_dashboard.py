"""
frontend/page_dashboard.py
📊 營運分析看板
"""

import sqlite3
import streamlit as st
import pandas as pd
from backend import DB_FILE, run_query


def render():
    st.markdown("<div class='premium-title'>📊 營運分析看板</div>", unsafe_allow_html=True)
    st.markdown("<p style='color: #64748b; font-size: 1.1rem; margin-bottom: 2rem;'>快速瀏覽企業當前營運狀況與核心指標。</p>", unsafe_allow_html=True)

    # 頂部四大指標 Metrics
    col1, col2, col3, col4 = st.columns(4)

    # 庫存總價值（以成本計算）
    inv_val = run_query("SELECT SUM(stock * COALESCE(cost, 0)) FROM inventory")
    inv_val = (inv_val[0][0] or 0) if inv_val else 0

    # 本月營收與訂單數（排除已取消訂單）
    this_month_revenue_row = run_query(
        "SELECT COALESCE(SUM(total_amount), 0) FROM orders "
        "WHERE status != '已取消' AND strftime('%Y-%m', order_date) = strftime('%Y-%m','now')"
    )
    this_month_revenue = this_month_revenue_row[0][0] if this_month_revenue_row else 0

    this_month_orders_row = run_query(
        "SELECT COUNT(*) FROM orders "
        "WHERE status != '已取消' AND strftime('%Y-%m', order_date) = strftime('%Y-%m','now')"
    )
    this_month_orders = this_month_orders_row[0][0] if this_month_orders_row else 0

    # 低庫存商品數（庫存小於等於安全庫存）
    low_stock_row = run_query(
        "SELECT COUNT(*) FROM inventory WHERE reorder_point IS NOT NULL AND reorder_point > 0 AND stock <= reorder_point"
    )
    low_stock_count = low_stock_row[0][0] if low_stock_row else 0

    col1.metric("💰 本月營收 (NTD)", f"${this_month_revenue:,.0f}")
    col2.metric("🧾 本月訂單數", f"{this_month_orders} 筆")
    col3.metric("📦 庫存總價值 (NTD)", f"${inv_val:,.0f}")
    col4.metric("⚠️ 低庫存商品數", f"{low_stock_count} 品項")

    st.markdown("---")
    col_chart1, col_chart2 = st.columns(2)

    # 圖表 1: 庫存健康度
    with col_chart1:
        st.markdown("### 📦 庫存水位 vs 安全庫存")
        try:
            df_inv = pd.read_sql_query("SELECT name, stock, reorder_point FROM inventory", sqlite3.connect(DB_FILE))
            if not df_inv.empty:
                df_inv = df_inv.set_index('name')
                st.bar_chart(df_inv[['stock', 'reorder_point']], color=["#4CAF50", "#FF5252"])
        except Exception as e:
            st.error(f"無法載入圖表: {e}")

    # 圖表 2: 訂單狀態
    with col_chart2:
        st.markdown("### 🧾 訂單執行狀態分佈")
        try:
            df_ord = pd.read_sql_query("SELECT status, COUNT(*) as count FROM orders GROUP BY status", sqlite3.connect(DB_FILE))
            if not df_ord.empty:
                # 把 status 當作 index 繪製長條圖
                st.bar_chart(df_ord.set_index('status'), color="#3B82F6")
        except Exception as e:
            st.error(f"無法載入圖表: {e}")

    st.markdown("---")

    # 圖表 3 & 4: 商品銷售排行 / 每月營收趨勢
    col_chart3, col_chart4 = st.columns(2)

    # 圖表 3: 商品銷售排行（依銷售金額）
    with col_chart3:
        st.markdown("### 🏆 商品銷售排行（依銷售金額）")
        try:
            conn = sqlite3.connect(DB_FILE)
            df_rank = pd.read_sql_query(
                """
                SELECT i.name AS 商品, 
                       SUM(o.quantity) AS 銷售數量, 
                       SUM(o.total_amount) AS 銷售金額
                FROM orders o
                LEFT JOIN inventory i ON o.product_id = i.product_id
                WHERE o.status != '已取消'
                GROUP BY o.product_id, i.name
                ORDER BY 銷售金額 DESC
                LIMIT 10
                """,
                conn,
            )
            conn.close()
            if not df_rank.empty:
                st.bar_chart(df_rank.set_index("商品")[["銷售金額"]], color="#10B981")
            else:
                st.info("目前還沒有銷售資料。")
        except Exception as e:
            st.error(f"無法載入圖表: {e}")

    # 圖表 4: 每月營收趨勢圖
    with col_chart4:
        st.markdown("### 📈 每月營收趨勢")
        try:
            conn = sqlite3.connect(DB_FILE)
            df_rev = pd.read_sql_query(
                """
                SELECT strftime('%Y-%m', order_date) AS 月份,
                       SUM(total_amount) AS 營收
                FROM orders
                WHERE status != '已取消'
                GROUP BY strftime('%Y-%m', order_date)
                ORDER BY 月份
                """,
                conn,
            )
            conn.close()
            if not df_rev.empty:
                df_rev = df_rev.set_index("月份")
                st.line_chart(df_rev["營收"], color="#6366F1")
            else:
                st.info("目前還沒有營收資料。")
        except Exception as e:
            st.error(f"無法載入圖表: {e}")
