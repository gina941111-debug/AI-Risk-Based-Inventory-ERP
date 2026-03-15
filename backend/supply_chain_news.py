"""
backend/supply_chain_news.py
依國家取得可能影響銷售或出貨的即時新聞，供供應鏈地圖頁面使用。
支援 GNews API（需 API Key）與 Google News RSS 備援（免 Key）。
"""

import os
import re
import sqlite3
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote_plus

# 國家名稱 → 英文搜尋用 / 雙碼（給 GNews API 用）
COUNTRY_MAP = {
    "台灣": ("Taiwan", "TW"),
    "日本": ("Japan", "JP"),
    "美國": ("United States", "US"),
    "越南": ("Vietnam", "VN"),
    "德國": ("Germany", "DE"),
    "中國": ("China", "CN"),
    "南韓": ("South Korea", "KR"),
    "新加坡": ("Singapore", "SG"),
    "泰國": ("Thailand", "TH"),
    "馬來西亞": ("Malaysia", "MY"),
    "印尼": ("Indonesia", "ID"),
    "菲律賓": ("Philippines", "PH"),
    "印度": ("India", "IN"),
    "英國": ("United Kingdom", "GB"),
    "法國": ("France", "FR"),
    "荷蘭": ("Netherlands", "NL"),
    "澳洲": ("Australia", "AU"),
    "加拿大": ("Canada", "CA"),
    "墨西哥": ("Mexico", "MX"),
}


def _get_db():
    from .database import DB_FILE
    return DB_FILE


def _get_gnews_api_key() -> Optional[str]:
    """從環境變數或 Streamlit secrets 取得 GNews API Key（選填）。"""
    key = os.environ.get("GNEWS_API_KEY", "").strip()
    if key:
        return key
    try:
        import streamlit as st
        if hasattr(st, "secrets") and st.secrets.get("gnews", {}).get("api_key"):
            return st.secrets["gnews"]["api_key"]
        if hasattr(st, "secrets") and st.secrets.get("news", {}).get("api_key"):
            return st.secrets["news"]["api_key"]
    except Exception:
        pass
    return None


def _fetch_via_gnews_api(country_name: str, api_key: str, max_results: int = 10) -> List[dict]:
    """使用 GNews API v4 取得新聞（需 API Key）。"""
    try:
        import requests
    except ImportError:
        return []
    name_en, code = COUNTRY_MAP.get(country_name, (country_name, None))
    # 關鍵字：可能影響銷售、出貨、供應鏈、物流、關稅、罷工等
    query = f"{name_en} supply chain OR logistics OR shipping OR export OR tariff OR strike OR port OR pandemic"
    url = "https://gnews.io/api/v4/search"
    params = {
        "q": query,
        "max": max_results,
        "apikey": api_key,
        "lang": "en",
    }
    if code:
        params["country"] = code
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
        articles = data.get("articles") or []
        out = []
        for a in articles:
            out.append({
                "country": country_name,
                "region": None,
                "title": (a.get("title") or "").strip(),
                "summary": (a.get("description") or a.get("content") or "").strip()[:500],
                "url": (a.get("url") or "").strip(),
                "source": (a.get("source", {}).get("name") or "").strip(),
                "published_at": (a.get("publishedAt") or "").strip(),
                "relevance_tag": "supply_chain",
            })
        return out
    except Exception:
        return []


def _fetch_via_rss(country_name: str, max_results: int = 10) -> List[dict]:
    """使用 Google News RSS 取得新聞（免 API Key）。"""
    name_en = COUNTRY_MAP.get(country_name, (country_name,))[0]
    query = f"{name_en} supply chain logistics shipping export"
    q_enc = quote_plus(query)
    url = f"https://news.google.com/rss/search?q={q_enc}&hl=en-US&gl=US&ceid=US:en"
    out = []
    try:
        import xml.etree.ElementTree as ET
        from urllib.request import urlopen, Request
        req = Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; ERP-Bot/1.0)"})
        with urlopen(req, timeout=15) as resp:
            tree = ET.parse(resp)
        root = tree.getroot()
        channel = root.find("channel")
        if channel is None:
            return []
        for item in list(channel.findall("item"))[:max_results]:
            title = (item.find("title") or item).text or ""
            link = (item.find("link") or item).text or ""
            desc_el = item.find("description")
            summary = (desc_el.text or "") if desc_el is not None else ""
            if summary:
                summary = re.sub(r"<[^>]+>", "", summary)[:500]
            pub = (item.find("pubDate") or item).text or ""
            out.append({
                "country": country_name,
                "region": None,
                "title": title.strip(),
                "summary": summary.strip(),
                "url": link.strip(),
                "source": "Google News",
                "published_at": pub,
                "relevance_tag": "supply_chain",
            })
        return out
    except Exception:
        return []


def fetch_country_news(country_name: str, api_key: Optional[str] = None, max_results: int = 10) -> List[dict]:
    """
    取得指定國家可能影響銷售或出貨的即時新聞。
    若有 GNews API Key 則優先使用 API，否則使用 Google News RSS。
    """
    if api_key:
        items = _fetch_via_gnews_api(country_name, api_key, max_results)
        if items:
            return items
    return _fetch_via_rss(country_name, max_results)


def save_news_to_db(items: List[dict]) -> int:
    """將新聞寫入 supply_chain_news 表。"""
    if not items:
        return 0
    db = _get_db()
    conn = sqlite3.connect(db)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    n = 0
    for it in items:
        try:
            conn.execute(
                """INSERT INTO supply_chain_news (country, region, title, summary, url, source, published_at, relevance_tag, fetched_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    it.get("country") or "",
                    it.get("region"),
                    (it.get("title") or "")[:500],
                    (it.get("summary") or "")[:1000],
                    (it.get("url") or "")[:500],
                    (it.get("source") or "")[:100],
                    it.get("published_at"),
                    it.get("relevance_tag"),
                    now,
                ),
            )
            n += 1
        except Exception:
            continue
    conn.commit()
    conn.close()
    return n


def get_news_from_db(
    country: Optional[str] = None,
    limit: int = 50,
    order_by_latest: bool = True,
    within_days: Optional[int] = None,
) -> List[dict]:
    """從資料庫讀取已快取的新聞。order_by_latest=True 依發布/取得時間取最近最新；within_days=30 僅取近 N 天內。"""
    db = _get_db()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    order = "ORDER BY COALESCE(published_at, fetched_at) DESC, id DESC LIMIT ?"
    date_filter = ""
    params_where = []
    if within_days is not None and within_days > 0:
        date_filter = " AND date(COALESCE(published_at, fetched_at)) >= date('now', ?) "
        params_where.append(f"-{int(within_days)} days")
    if country:
        params = [country] + params_where + [limit]
        rows = conn.execute(
            f"""SELECT id, country, region, title, summary, url, source, published_at, relevance_tag, fetched_at
               FROM supply_chain_news WHERE country = ?{date_filter}{order}""",
            params,
        ).fetchall()
    else:
        params = params_where + [limit]
        rows = conn.execute(
            f"""SELECT id, country, region, title, summary, url, source, published_at, relevance_tag, fetched_at
               FROM supply_chain_news WHERE 1=1{date_filter}{order}""",
            params,
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def refresh_news_for_countries(countries: List[str], api_key: Optional[str] = None, max_per_country: int = 8) -> dict:
    """
    為多個國家重新抓取新聞並寫入 DB。
    回傳 {"updated": 筆數, "by_country": {國家: 筆數}, "used_api": bool}
    """
    used_api = bool(api_key)
    key = api_key or _get_gnews_api_key()
    by_country = {}
    total = 0
    for c in countries:
        items = fetch_country_news(c, api_key=key, max_results=max_per_country)
        n = save_news_to_db(items)
        by_country[c] = n
        total += n
    return {"updated": total, "by_country": by_country, "used_api": used_api or bool(key)}
