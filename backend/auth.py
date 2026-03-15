"""
backend/auth.py
使用者驗證與角色型存取控制 (RBAC)
"""

import streamlit as st
from .database import run_query


def check_login(username: str, password: str) -> dict | None:
    """驗證帳號密碼，成功回傳 {role, name}，失敗回傳 None"""
    user_res = run_query(
        "SELECT role, name FROM users WHERE username=? AND password=?",
        (username, password),
    )
    if user_res:
        return {"role": user_res[0][0], "name": user_res[0][1]}
    return None


def check_permission(allowed_roles: list) -> bool:
    """依目前 session 角色判斷是否有權限；admin 永遠通過"""
    current_role = st.session_state.get("role", "")
    if current_role == "admin":
        return True
    return current_role in allowed_roles
