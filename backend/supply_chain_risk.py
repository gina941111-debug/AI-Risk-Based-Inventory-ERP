"""
backend/supply_chain_risk.py
供應鏈與風險 — 後端邏輯
職責：供應鏈地圖資料、風險事件與交期、風險係數管理、風險報告產出
"""

import sqlite3
from datetime import datetime, timedelta
from backend.database import DB_FILE, run_query


# ── 供應鏈地圖 ────────────────────────────────────────────────────────
# 經緯度僅在後端使用（地圖繪圖），前端不呈現；若 DB 為空則依國家代碼由後端帶入預設座標。

_COUNTRY_DEFAULT_COORDS = {
    "台灣": (25.0330, 121.5654),
    "日本": (35.6895, 139.6917),
    "美國": (37.7749, -122.4194),
    "德國": (48.1351, 11.5820),
    "越南": (21.0285, 105.8542),
    "墨西哥": (23.6345, -102.5528),
    "中國": (39.9042, 116.4074),
    "南韓": (37.5665, 126.9780),
    "泰國": (13.7563, 100.5018),
    "新加坡": (1.3521, 103.8198),
}


def _fill_coords_from_country(df, country_col="country", lat_col="latitude", lon_col="longitude"):
    """若經緯度為空但有國家，由後端依國家帶入預設座標（僅後端使用，前端不顯示經緯度欄位）。"""
    if df is None or df.empty or country_col not in df.columns:
        return df
    import pandas as pd
    df = df.copy()
    if lat_col not in df.columns:
        df[lat_col] = pd.NA
    if lon_col not in df.columns:
        df[lon_col] = pd.NA
    for idx, row in df.iterrows():
        if pd.isna(row.get(lat_col)) or pd.isna(row.get(lon_col)):
            country = (row.get(country_col) or "").strip()
            if country and country in _COUNTRY_DEFAULT_COORDS:
                lat, lon = _COUNTRY_DEFAULT_COORDS[country]
                df.at[idx, lat_col], df.at[idx, lon_col] = lat, lon
    return df


def get_suppliers_for_map():
    """取得供應商清單（含經緯度、國家、地區、風險等級），供地圖與清單使用。經緯度僅後端使用。"""
    conn = sqlite3.connect(DB_FILE)
    df = __pd_read("SELECT supplier_id, name, country, region, latitude, longitude, risk_level FROM suppliers", conn)
    conn.close()
    return _fill_coords_from_country(df)


def get_customers_for_map():
    """取得客戶清單（含經緯度、國家、地區、風險等級），供地圖與清單使用。經緯度僅後端使用。"""
    conn = sqlite3.connect(DB_FILE)
    try:
        df = __pd_read("SELECT customer_id, name, country, region, latitude, longitude, risk_level FROM customers", conn)
    except Exception:
        df = __empty_df()
    conn.close()
    return _fill_coords_from_country(df)


def get_recent_events_for_delay(limit=50):
    """取得近期供應鏈事件，供地圖判定出貨延遲狀況。"""
    conn = sqlite3.connect(DB_FILE)
    df = __pd_read(
        "SELECT event_type, region, country, impact_days FROM supply_chain_events ORDER BY id DESC LIMIT ?",
        conn,
        params=(limit,),
    )
    conn.close()
    return df


def get_region_procurement_share():
    """依地區彙總採購金額，計算各地區採購佔比（該地區供應商之採購額 / 全公司採購額）。
    回傳 list of dict: region_key, display_name, procurement_ratio (0~1), total_amount, supplier_count。
    用於初始熱圖：採購佔比愈高，集中度風險愈高，可對應風險低/中/高。"""
    conn = sqlite3.connect(DB_FILE)
    total = __pd_read(
        "SELECT COALESCE(SUM(total_amount), 0) as tot FROM purchase_orders WHERE total_amount IS NOT NULL AND total_amount > 0",
        conn,
    )
    global_sum = float(total["tot"].iloc[0]) if total is not None and not total.empty else 0
    if global_sum <= 0:
        conn.close()
        return {}
    df = __pd_read(
        """SELECT s.country, s.region, SUM(COALESCE(p.total_amount, 0)) as amt, COUNT(DISTINCT s.supplier_id) as cnt
           FROM suppliers s
           LEFT JOIN purchase_orders p ON s.supplier_id = p.supplier_id AND p.total_amount IS NOT NULL AND p.total_amount > 0
           WHERE (s.country IS NOT NULL AND s.country != '') OR (s.region IS NOT NULL AND s.region != '')
           GROUP BY s.country, s.region""",
        conn,
    )
    conn.close()
    if df is None or df.empty:
        return {}
    out = {}
    for _, r in df.iterrows():
        country = (r.get("country") or "").strip() or "未填"
        region = (r.get("region") or "").strip() or country
        key = f"{country}|{region}"
        amt = float(r.get("amt") or 0)
        ratio = amt / global_sum
        out[key] = {
            "region_key": key,
            "display_name": f"{country} {region}".strip(),
            "procurement_ratio": round(ratio, 4),
            "total_amount": amt,
            "supplier_count": int(r.get("cnt") or 0),
        }
    return out


# ── 即時風險熱圖 (Risk Heatmap) ─────────────────────────────────────────
# 各國／各地區風險% 來源（優先順序）：
# 1. risk_heatmap 表：若已有資料則直接使用（可被「產生即時風險摘要」的 AI 更新）
# 2. 初始熱圖推算：依「供應商據點」+「風險事件」+「風險係數(region)」計算（見下方）
# 3. 供應商／客戶的 risk_level（高/中/低）為手動欄位，在採購／銷售管理維護
#
# 【初始熱圖風險值統計方式】
# - 基礎值：每個據點預設 20%。
# - 風險事件加權：若「風險事件與交期」中有登錄事件，且事件的地區/國家涵蓋該據點，則 +40%（上限 100%）。
# - 地區係數：若「風險係數管理」有設定類型=地區(region)的係數（如東亞 60、日本 40、越南 55），
#   則該據點的風險% = max(上述計算值, 該地區係數)，即取「事件加權後」與「地區係數」較高者。
# - 熱點來源：僅從「供應商」的國家/地區去重後產生，每個 (國家|地區) 一筆，經緯度取自該區任一台供應商。
# - 採購佔比：系統自動彙總該地區所有供應商的採購金額佔比；佔比愈高視為集中度風險愈高，對應風險低/中/高（見 get_region_procurement_share）。
#
# 【AI 摘要 UPDATE: 地區|%】廣域地區對應：當 AI 回傳「UPDATE: 亞洲|70%」時，需將「亞洲」對應到所有亞洲國家之熱點一併更新。
REGION_COUNTRY_MAP = {
    "亞洲": ["台灣", "日本", "中國", "南韓", "北韓", "越南", "泰國", "新加坡", "馬來西亞", "印尼", "菲律賓", "印度", "香港", "澳門"],
    "東亞": ["台灣", "日本", "中國", "南韓", "北韓", "香港", "澳門"],
    "東南亞": ["越南", "泰國", "新加坡", "馬來西亞", "印尼", "菲律賓", "緬甸", "柬埔寨", "寮國"],
    "歐洲": ["德國", "法國", "英國", "義大利", "西班牙", "荷蘭", "波蘭", "比利時", "奧地利", "瑞士"],
    "北美": ["美國", "加拿大", "墨西哥"],
    "中東": ["以色列", "沙烏地阿拉伯", "阿拉伯聯合大公國", "伊朗", "伊拉克", "土耳其", "約旦", "黎巴嫩"],
}

def get_risk_heatmap_data():
    """
    取得熱圖資料：永遠以「供應商據點」為基礎產出完整熱點清單，再以 risk_heatmap 表覆寫風險%與摘要。
    如此手動或 AI 更新單一熱點時，其他未調節的熱點仍會保留在地圖上。
    """
    # 1. 永遠先依供應商據點算出「預設」熱點清單
    suppliers = get_suppliers_for_map()
    if suppliers is None or suppliers.empty:
        return []
    events = get_recent_events_for_delay(20)
    region_scores = get_region_risk_scores()
    procurement_by_region = get_region_procurement_share()
    default_risk = 20.0
    seen = set()
    default_rows = []
    for _, s in suppliers.iterrows():
        country = (s.get("country") or "").strip() or "未填"
        region = (s.get("region") or "").strip() or country
        key = f"{country}|{region}"
        if key in seen:
            continue
        seen.add(key)
        risk = default_risk
        if events is not None and not events.empty:
            for _, ev in events.iterrows():
                if (ev.get("country") and ev["country"] in country) or (ev.get("region") and ev["region"] in region):
                    risk = min(100, risk + 40)
                    break
        for k, v in region_scores.items():
            if k in region or k in country:
                risk = max(risk, min(100, v))
                break
        if key in procurement_by_region:
            ratio = procurement_by_region[key]["procurement_ratio"]
            if ratio >= 0.35:
                risk = max(risk, 70)
            elif ratio >= 0.15:
                risk = max(risk, 45)
            else:
                risk = max(risk, min(35, 20 + ratio * 100))
        lat, lon = s.get("latitude"), s.get("longitude")
        if lat is None or lon is None:
            continue
        default_rows.append({
            "region_key": key,
            "display_name": f"{country} {region}".strip(),
            "latitude": float(lat),
            "longitude": float(lon),
            "risk_pct": round(risk, 1),
            "ai_summary": None,
            "updated_at": None,
        })
    # 2. 讀取 DB 中手動/AI 覆寫的風險%與摘要，依 region_key 覆蓋到預設清單
    conn = sqlite3.connect(DB_FILE)
    df = __pd_read(
        "SELECT region_key, display_name, latitude, longitude, risk_pct, ai_summary, updated_at FROM risk_heatmap",
        conn,
    )
    conn.close()
    overrides = {}
    if df is not None and not df.empty:
        for _, r in df.iterrows():
            k = (r.get("region_key") or "").strip()
            if k:
                overrides[k] = {
                    "risk_pct": r.get("risk_pct"),
                    "ai_summary": r.get("ai_summary"),
                    "updated_at": r.get("updated_at"),
                    "latitude": r.get("latitude"),
                    "longitude": r.get("longitude"),
                }
    # 3. 合併：預設熱點 + 有覆寫則用覆寫的 risk_pct / ai_summary
    out = []
    for row in default_rows:
        rk = row["region_key"]
        if rk in overrides:
            o = overrides[rk]
            out.append({
                "region_key": rk,
                "display_name": row["display_name"],
                "latitude": o.get("latitude") if o.get("latitude") is not None else row["latitude"],
                "longitude": o.get("longitude") if o.get("longitude") is not None else row["longitude"],
                "risk_pct": o.get("risk_pct") if o.get("risk_pct") is not None else row["risk_pct"],
                "ai_summary": o.get("ai_summary"),
                "updated_at": o.get("updated_at"),
            })
        else:
            out.append(row)
    return out


def upsert_risk_heatmap(region_key, display_name, latitude, longitude, risk_pct, ai_summary=None):
    """新增或更新一筆熱圖熱點。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = sqlite3.connect(DB_FILE)
    conn.execute(
        """INSERT INTO risk_heatmap (region_key, display_name, latitude, longitude, risk_pct, ai_summary, updated_at)
           VALUES (?,?,?,?,?,?,?) ON CONFLICT(region_key) DO UPDATE SET
           display_name=excluded.display_name, latitude=excluded.latitude, longitude=excluded.longitude,
           risk_pct=excluded.risk_pct, ai_summary=excluded.ai_summary, updated_at=excluded.updated_at""",
        (region_key, display_name, latitude, longitude, risk_pct, ai_summary, now),
    )
    conn.commit()
    conn.close()


def reset_risk_heatmap_to_initial():
    """清空 risk_heatmap 表，使熱圖還原為依供應商據點與風險事件計算的初始狀態。"""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM risk_heatmap")
    conn.commit()
    conn.close()


def get_heatmap_ai_summary(api_key, news_context="", reference_date: str = "2026-03-13", model: str | None = None):
    """依供應商名單與新聞／事件，由 AI 產出熱圖摘要。news_context 應為最近最新新聞（依時間排序）。model 為 Gemini 模型 ID，未指定時使用 gemini-2.5-flash。"""
    suppliers = get_suppliers_for_map()
    events = get_risk_events_list(10)
    heatmap = get_risk_heatmap_data()
    if suppliers is None or suppliers.empty:
        supplier_text = "目前無供應商據點資料。"
    else:
        supplier_text = suppliers[["name", "country", "region"]].to_string(index=False)
    events_text = ""
    if events is not None and not events.empty:
        events_text = events.to_string(index=False)
    else:
        events_text = "目前無登錄的風險事件。"
    date_label = reference_date.replace("-", "年", 1).replace("-", "月", 1) + "日" if reference_date else "今日"
    prompt = f"""你是供應鏈風險分析師。**參考日期：{date_label}**。請比對以下「最近最新新聞／事件」與「我司供應商名單與地區」，產出即時風險摘要，並**務必為每個受影響地區給出風險建議值（0～100%）**。

【風險建議值 % 數】
- 新聞若已寫出某地區的供應鏈風險%數，請直接採用並在摘要中寫出。
- 新聞若未寫出具體%數，請依風險嚴重程度（影響範圍、不確定性、替代難度等）**推估並寫明**各相關地區的「風險建議值」，例如：「台灣供應鏈風險建議值約 75%、南韓 65%、墨西哥 45%」。
- 摘要正文中必須明確列出每個提及地區的建議%數，讓使用者一眼看到 AI 的風險建議值。

【我司供應商名單與地區】
{supplier_text}

【近期風險事件】
{events_text}

【最近最新新聞】
{news_context or "目前尚無快取新聞，請先於頁面「更新即時新聞」取得最近新聞後再產生摘要。"}

請產出「即時風險摘要」：
1. 正文：1～3 句繁體中文，描述風險並**明確寫出各相關地區的風險建議值 %**（例：我司在台灣、南韓、墨西哥的供應鏈風險建議值分別為 75%、65%、45%）。
2. 結尾另起一行，依序寫「UPDATE: 地區名稱|風險%」（多地區多行），供系統更新地圖。此 UPDATE 行不會顯示給使用者。
回覆請簡潔、可直接顯示在儀表板。"""
    if not api_key or not api_key.strip():
        return "請在側欄設定 Gemini API Key 後重新整理，以產生 AI 即時風險摘要。", []
    model_id = (model or "").strip() or "gemini-2.5-flash"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key.strip())
        resp = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2),
        )
        text = (resp.text or "").strip()
        updates = []
        display_lines = []
        for line in text.split("\n"):
            raw = line.strip()
            if raw.upper().startswith("UPDATE:"):
                part = raw[7:].strip()
                if "|" in part:
                    name, pct = part.split("|", 1)
                    try:
                        updates.append({"display_name": name.strip(), "risk_pct": float(pct.strip())})
                    except ValueError:
                        pass
            else:
                display_lines.append(line.rstrip())
        # 回傳給前端的摘要不包含 UPDATE 行，僅供後台更新地圖用
        display_text = "\n".join(display_lines).strip()
        return display_text, updates
    except Exception as e:
        return f"AI 摘要暫時無法產生（{e}）。請確認 API Key 與網路。", []


def apply_heatmap_updates(updates, ai_summary=None):
    """
    將 AI 回傳的 UPDATE 清單套用到熱圖。
    - 若 update 的 display_name 為廣域地區（如「亞洲」），則將該地區內所有熱點都更新為對應 risk_pct。
    - 否則依「display_name 包含於熱點 display_name」匹配單一熱點後更新。
    """
    if not updates:
        return
    heatmap_rows = get_risk_heatmap_data()
    if not heatmap_rows:
        return
    summary_snippet = (ai_summary or "")[:500]
    for u in updates:
        name = (u.get("display_name") or "").strip()
        risk_pct = u.get("risk_pct")
        if not name or risk_pct is None:
            continue
        if name in REGION_COUNTRY_MAP:
            countries = REGION_COUNTRY_MAP[name]
            for r in heatmap_rows:
                country = (r.get("region_key") or "").split("|")[0].strip()
                if country in countries:
                    upsert_risk_heatmap(
                        r["region_key"], r["display_name"], r["latitude"], r["longitude"],
                        risk_pct, summary_snippet,
                    )
        else:
            # 單一國家/地區（如「台灣」）：須更新「所有」符合的熱點，不可只更新第一筆
            for r in heatmap_rows:
                if r.get("display_name") and name in r["display_name"]:
                    upsert_risk_heatmap(
                        r["region_key"], r["display_name"], r["latitude"], r["longitude"],
                        risk_pct, summary_snippet,
                    )


def translate_to_chinese_traditional(api_key: str, text: str, model: str | None = None) -> str:
    """使用 Gemini 將文字翻譯為繁體中文；若無 api_key、空字串或失敗則回傳原文。"""
    if not (api_key and api_key.strip()) or not (text and str(text).strip()):
        return (text or "").strip()
    model_id = (model or "").strip() or "gemini-2.5-flash"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key.strip())
        prompt = f"""將以下新聞標題或摘要翻譯成繁體中文，只輸出翻譯結果、不要加說明或標點以外的內容。若已是中文則略為潤飾成通順繁體即可。

原文：
{text[:3000]}"""
        resp = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2),
        )
        out = (resp.text or "").strip()
        return out if out else (text or "").strip()
    except Exception:
        return (text or "").strip()


def infer_affected_region_from_news(api_key: str, news_text: str, model: str | None = None) -> dict:
    """
    依新聞內容由 AI 判斷受影響的國家、地區與事件類型。
    回傳 {"country": str, "region": str, "event_type": str}，若失敗或無 api_key 則回傳空字串。
    """
    out = {"country": "", "region": "", "event_type": ""}
    if not (api_key and api_key.strip()) or not (news_text and str(news_text).strip()):
        return out
    model_id = (model or "").strip() or "gemini-2.5-flash"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key.strip())
        prompt = f"""你正在分析一則新聞，要填寫「受影響的國家」「受影響的地區」「事件類型」三項。

【嚴格規則，務必遵守】
1. 國家：只填新聞「正文內有直接寫出」的國家名（如台灣、美國、中國）。未提及則填「不明」。
2. 地區：只填新聞「正文內有直接寫出」的區域名、地理區或城市名（如中東、關東、加州）。  
   **禁止**根據國家自行推測境內城市或地區：例如新聞只寫「台灣」而沒寫新竹、台北、高雄等，地區欄必須填「不明」，不可填新竹、北區等任何新聞未寫的地名。
3. 若新聞只提到多國而無具體地區，地區填「不明」或該區域統稱（如「中東」僅當新聞有寫中東時才填）。
4. 事件類型只能選一個：地震、天候、政治、疫情、罷工、其他。

請「只輸出」三行，格式如下（不要其他說明）：
國家：<新聞有寫的國家名，無則填「不明」>
地區：<新聞有寫的地區／城市名，無則填「不明」>
事件類型：<上述其一>

新聞內容：
{news_text[:2500]}"""
        resp = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.1),
        )
        text = (resp.text or "").strip()
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("國家：") or line.startswith("國家:"):
                out["country"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif line.startswith("地區：") or line.startswith("地區:"):
                out["region"] = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            elif line.startswith("事件類型：") or line.startswith("事件類型:"):
                et = line.split("：", 1)[-1].split(":", 1)[-1].strip()
                if et in ("地震", "天候", "政治", "疫情", "罷工", "其他"):
                    out["event_type"] = et
                else:
                    out["event_type"] = "其他"
        return out
    except Exception:
        return out


# ── 受災採購清單 (Impacted PO List) ────────────────────────────────────

def get_impacted_pos(region_key=None, country=None, supplier_id=None):
    """依熱點（地區/國家）或供應商 ID 篩選未結案採購單，回傳：採購單號、供應商、關鍵物料、預計延遲、替代建議。"""
    conn = sqlite3.connect(DB_FILE)
    where, params = ["(p.status IS NULL OR p.status NOT IN ('已完成','已取消'))"], []
    if supplier_id:
        where.append("p.supplier_id = ?")
        params.append(supplier_id)
    if region_key:
        if "|" in region_key:
            _c, _r = region_key.split("|", 1)
            where.append("(s.country LIKE ? OR s.region LIKE ?)")
            params.extend([f"%{_c}%", f"%{_r}%"])
        else:
            where.append("(s.country LIKE ? OR s.region LIKE ?)")
            params.extend([f"%{region_key}%", f"%{region_key}%"])
    if country:
        where.append("(s.country LIKE ? OR s.region LIKE ?)")
        params.extend([f"%{country}%", f"%{country}%"])
    q = """
    SELECT p.po_id, p.supplier_id, s.name as supplier_name, s.country, s.region,
           p.estimated_delay_days, p.alternative_suggestion
    FROM purchase_orders p
    JOIN suppliers s ON p.supplier_id = s.supplier_id
    WHERE """ + " AND ".join(where)
    pos = __pd_read(q, conn, params=tuple(params))
    conn.close()
    if pos is None or pos.empty:
        return []
    out = []
    conn = sqlite3.connect(DB_FILE)
    for _, row in pos.iterrows():
        items = __pd_read(
            "SELECT product_id FROM purchase_order_items WHERE po_id = ?", conn, params=(row["po_id"],)
        )
        names = []
        if items is not None and not items.empty:
            for _, it in items.iterrows():
                inv = __pd_read("SELECT name FROM inventory WHERE product_id = ?", conn, params=(it["product_id"],))
                if inv is not None and not inv.empty:
                    names.append(inv["name"].iloc[0])
        key_materials = "、".join(names) if names else "—"
        delay = row.get("estimated_delay_days")
        # 處理 NaN（pandas 從 DB 讀出空值時可能為 NaN，NaN != NaN）
        try:
            delay_str = f"+{int(delay)} 天" if delay is not None and delay == delay else "—"
        except (TypeError, ValueError):
            delay_str = "—"
        alt = (row.get("alternative_suggestion") or "").strip() or "—"
        out.append({
            "po_id": row["po_id"],
            "supplier_name": row["supplier_name"],
            "key_materials": key_materials,
            "estimated_delay": delay_str,
            "alternative_suggestion": alt,
        })
    conn.close()
    return out


def update_po_impact(po_id, estimated_delay_days=None, alternative_suggestion=None):
    """更新採購單的預計延遲天數與替代建議。"""
    conn = sqlite3.connect(DB_FILE)
    if estimated_delay_days is not None:
        conn.execute("UPDATE purchase_orders SET estimated_delay_days = ? WHERE po_id = ?", (estimated_delay_days, po_id))
    if alternative_suggestion is not None:
        conn.execute("UPDATE purchase_orders SET alternative_suggestion = ? WHERE po_id = ?", (alternative_suggestion, po_id))
    conn.commit()
    conn.close()


def get_ai_alternative_suggestions(api_key, impacted_list, hotspot_name, model: str | None = None):
    """由 AI 依熱點、供應商與關鍵物料分析，為每張採購單產生替代建議，回傳 [{"po_id": ..., "alternative_suggestion": ...}, ...]。model 為 Gemini 模型 ID。"""
    if not api_key or not api_key.strip() or not impacted_list:
        return []
    rows_text = "\n".join(
        f"- PO: {x['po_id']} | 供應商: {x['supplier_name']} | 關鍵物料: {x['key_materials']} | 預計延遲: {x['estimated_delay']}"
        for x in impacted_list
    )
    # 取得我司其他供應商據點（國家/地區），供 AI 明確建議「從哪裡調貨」
    other_regions_text = ""
    try:
        suppliers = get_suppliers_for_map()
        if suppliers is not None and not suppliers.empty:
            # 當前熱點可能為「墨西哥 中北部」或「台灣 北區」，用關鍵字排除
            hotspot_parts = [p.strip() for p in (hotspot_name or "").replace(" ", " ").split() if p.strip()]
            seen = set()
            parts = []
            for _, s in suppliers.iterrows():
                country = (s.get("country") or "").strip()
                region = (s.get("region") or "").strip() or country
                if not country:
                    continue
                # 若該據點屬於當前熱點（國家或地區名重合）則跳過
                if any(p in country or p in region for p in hotspot_parts):
                    continue
                key = f"{country} {region}".strip()
                if key not in seen:
                    seen.add(key)
                    parts.append(key)
            if parts:
                other_regions_text = "、".join(parts)
    except Exception:
        pass
    if not other_regions_text:
        other_regions_text = "（系統內暫無其他地區供應商，可依產業常識建議具體國家，例如：越南、泰國、中國華南、美國）"

    prompt = f"""你是供應鏈風險分析師。以下為「{hotspot_name}」熱點下、我司受影響的採購單清單。請為「每一張」採購單各給出一條具體的「替代建議」。

【重要】替代建議必須「明確寫出從哪個國家或地區」調貨或尋找替代，不可使用「其他地區」「其他區域」「其他供應商」等含糊用語。
- 可調貨／可尋替代的我司據點或常見產地：{other_regions_text}
- 每條建議請直接寫出地名，例如：「調用越南庫存」「從中國華南尋找替代供應商」「改由美國線出貨」「評估泰國或印尼備援產能」。
- 若建議空運／改航線，也請一併寫明可從哪一地區出貨（如「改由新加坡倉出貨空運」）。

【受災採購清單】
{rows_text}

請依序回覆，格式嚴格如下（每張 PO 一行，共 {len(impacted_list)} 行）：
PO_ID: 替代建議內容（必須含具體國家/地區名）
例如：
PO-20251127-0037: 調用越南庫存或從泰國尋找同規格供應商
PO-20251213-0042: 從美國或中國華南尋找替代供應商，或改由新加坡中轉
"""
    model_id = (model or "").strip() or "gemini-2.5-flash"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key.strip())
        resp = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.3),
        )
        text = (resp.text or "").strip()
        result = []
        po_ids = [x["po_id"] for x in impacted_list]
        for line in text.split("\n"):
            line = line.strip()
            if not line or ":" not in line:
                continue
            head, _, suggestion = line.partition(":")
            po_id = head.strip()
            suggestion = suggestion.strip()
            if po_id in po_ids and suggestion:
                result.append({"po_id": po_id, "alternative_suggestion": suggestion})
        return result
    except Exception:
        return []


# ── 模擬情境分析 (What-If Simulation) ──────────────────────────────────

def what_if_simulation(api_key, user_question, model: str | None = None):
    """依使用者情境問題，結合 ERP 供應商、未結案採購單、庫存安全天數，由 AI 回覆影響與建議。model 為 Gemini 模型 ID。"""
    conn = sqlite3.connect(DB_FILE)
    suppliers = __pd_read("SELECT supplier_id, name, country, region FROM suppliers", conn)
    pos = __pd_read(
        """SELECT p.po_id, p.supplier_id, s.name, s.country, s.region, p.estimated_delay_days, p.alternative_suggestion
           FROM purchase_orders p JOIN suppliers s ON p.supplier_id = s.supplier_id
           WHERE p.status NOT IN ('已完成','已取消') OR p.status IS NULL""",
        conn,
    )
    inv = __pd_read(
        "SELECT product_id, name, stock, reorder_point, daily_sales FROM inventory WHERE daily_sales > 0 OR reorder_point > 0",
        conn,
    )
    conn.close()
    supplier_text = suppliers.to_string(index=False) if suppliers is not None and not suppliers.empty else "無"
    po_text = pos.to_string(index=False) if pos is not None and not pos.empty else "無進行中採購單"
    inv_text = inv.to_string(index=False) if inv is not None and not inv.empty else "無庫存資料"
    system = """你是一個供應鏈風險分析師。請根據以下 ERP 資料（供應商名單與地區、未結案採購單、庫存與安全庫存設定），回答使用者的「如果…會怎樣」情境問題。
重點：指出哪些訂單/物料會斷貨、影響比例，並給出具體建議（例如：將 X 物料的安全庫存從 30 天提高到 60 天）。回覆用繁體中文、條列清晰。"""
    prompt = f"""【供應商名單與地區】
{supplier_text}

【未結案採購單與供應商】
{po_text}

【庫存與安全庫存（reorder_point, daily_sales 可推算安全天數）】
{inv_text}

【使用者情境問題】
{user_question}

請針對「對我司的影響」回答：受影響比例、可能斷貨的訂單或物料、具體因應建議（含安全庫存天數建議）。"""
    if not api_key or not api_key.strip():
        return "請在側欄設定 Gemini API Key 後再執行模擬。"
    model_id = (model or "").strip() or "gemini-2.5-flash"
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key.strip())
        resp = client.models.generate_content(
            model=model_id,
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.2),
        )
        return (resp.text or "").strip()
    except Exception as e:
        return f"模擬分析暫時無法產生（{e}）。請確認 API Key 與網路。"


# ── 風險事件與交期 ────────────────────────────────────────────────────

def get_risk_events_list(limit=20):
    """取得風險事件列表（id, event_type, region, country, impact_days, description, created_at）。"""
    conn = sqlite3.connect(DB_FILE)
    df = __pd_read(
        "SELECT id, event_type, region, country, impact_days, description, created_at FROM supply_chain_events ORDER BY id DESC LIMIT ?",
        conn,
        params=(limit,),
    )
    conn.close()
    return df


def add_risk_event(event_type, region, country, impact_days, description):
    """新增一筆風險事件。"""
    run_query(
        "INSERT INTO supply_chain_events (event_type, region, country, impact_days, description, created_at) VALUES (?,?,?,?,?,?)",
        (event_type, region or None, country or None, impact_days, description or None, datetime.now().strftime("%Y-%m-%d %H:%M")),
        fetch=False,
    )


def delete_risk_event(event_id):
    """刪除一筆風險事件。"""
    run_query("DELETE FROM supply_chain_events WHERE id = ?", (event_id,), fetch=False)


def get_affected_suppliers_by_event(region: str, country: str = None):
    """依地區與國家篩選受影響的供應商。"""
    conn = sqlite3.connect(DB_FILE)
    where, params = [], []
    if region:
        where.append("(region LIKE ? OR country LIKE ?)")
        params.extend([f"%{region}%", f"%{region}%"])
    if country:
        where.append("(country LIKE ? OR region LIKE ?)")
        params.extend([f"%{country}%", f"%{country}%"])
    if not where:
        conn.close()
        return []

    q = "SELECT supplier_id, name, country, region FROM suppliers WHERE " + " AND ".join(where)
    df = __pd_read(q, conn, params=tuple(params))
    conn.close()
    if df is None or df.empty:
        return []
    return df.values.tolist()

def get_affected_sales_orders_by_event(region: str, country: str, impact_days: int):
    """
    Find sales orders impacted by a regional risk event.
    Trace: Suppliers (Region) -> Purchase Orders (Pending) -> Products -> Sales Orders (Pending).
    Returns list of dicts with order details.
    """
    conn = sqlite3.connect(DB_FILE)
    
    where, params = [], []
    if region:
        where.append("(s.region LIKE ? OR s.country LIKE ?)")
        params.extend([f"%{region}%", f"%{region}%"])
    if country:
        where.append("(s.country LIKE ? OR s.region LIKE ?)")
        params.extend([f"%{country}%", f"%{country}%"])
    if not where:
        conn.close()
        return []
        
    query = f"""
    SELECT DISTINCT 
        o.order_id, 
        c.name as customer_name, 
        inv.name as product_name,
        o.order_date
    FROM suppliers s
    JOIN purchase_orders po ON s.supplier_id = po.supplier_id
    JOIN purchase_order_items poi ON po.po_id = poi.po_id
    JOIN inventory inv ON poi.product_id = inv.product_id
    JOIN orders o ON o.product_id = inv.product_id
    LEFT JOIN customers c ON o.customer_id = c.customer_id
    WHERE {" AND ".join(where)}
      AND (po.status IS NULL OR po.status NOT IN ('已完成', '已取消', '已入庫'))
      AND (o.status IS NULL OR o.status NOT IN ('已完成', '已取消', '已出貨'))
    """
    
    df = __pd_read(query, conn, params=tuple(params))
    conn.close()
    
    if df is None or df.empty:
        return []
        
    results = []
    for _, row in df.iterrows():
        order_date_str = row['order_date']
        try:
            # Assuming original delivery is order_date + 7 days
            dt = datetime.strptime(order_date_str.split(" ")[0], "%Y-%m-%d")
            orig_delivery = dt + timedelta(days=7)
            new_delivery = orig_delivery + timedelta(days=impact_days)
            orig_str = orig_delivery.strftime("%Y-%m-%d")
            new_str = new_delivery.strftime("%Y-%m-%d")
        except Exception:
            orig_str = "未定"
            new_str = f"未定 (+{impact_days}天)"
            
        results.append({
            "order_id": row['order_id'],
            "customer_name": row['customer_name'] or "Unknown",
            "product_name": row['product_name'] or "Unknown",
            "original_delivery": orig_str,
            "new_delivery": new_str
        })
        
    return results

def get_event_risk_scores():
    """取得事件類型對應的風險分數（event_type -> score）。"""
    conn = sqlite3.connect(DB_FILE)
    df = __pd_read(
        "SELECT risk_type, risk_key, risk_score, weight FROM esg_risk_factors WHERE risk_type = 'event_type'", conn)
    conn.close()
    if df is None or df.empty:
        return {}
    return dict(zip(df["risk_key"], df["risk_score"] * df["weight"]))


def get_region_risk_scores():
    """取得地區對應的風險分數（region key -> score）。"""
    conn = sqlite3.connect(DB_FILE)
    df = __pd_read("SELECT risk_type, risk_key, risk_score, weight FROM esg_risk_factors WHERE risk_type = 'region'", conn)
    conn.close()
    if df is None or df.empty:
        return {}
    return dict(zip(df["risk_key"], df["risk_score"] * df["weight"]))


# ── 風險係數管理 ────────────────────────────────────────────────────────

def get_risk_factors():
    """取得所有風險係數（id, 類型, 代碼, 風險分數, 權重, 備註, 更新時間）。"""
    conn = sqlite3.connect(DB_FILE)
    df = __pd_read(
        "SELECT id, risk_type as 類型, risk_key as 代碼, risk_score as 風險分數, weight as 權重, note as 備註, updated_at as 更新時間 FROM esg_risk_factors ORDER BY risk_type, risk_key",
        conn,
    )
    conn.close()
    return df


def get_risk_factors_raw():
    """取得原始欄位名的風險係數（供加權計算、預覽用）。"""
    conn = sqlite3.connect(DB_FILE)
    df = __pd_read("SELECT risk_type, risk_key, risk_score, weight FROM esg_risk_factors", conn)
    conn.close()
    return df


def save_risk_factor(risk_type, risk_key, risk_score, weight, note=None):
    """新增或更新一筆風險係數。"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    run_query(
        "INSERT OR REPLACE INTO esg_risk_factors (risk_type, risk_key, risk_score, weight, note, updated_at) VALUES (?,?,?,?,?,?)",
        (risk_type, risk_key.strip(), float(risk_score), float(weight), note or None, now),
        fetch=False,
    )


def delete_risk_factor(factor_id):
    """刪除一筆風險係數。"""
    run_query("DELETE FROM esg_risk_factors WHERE id = ?", (factor_id,), fetch=False)


def clear_all_risk_factors():
    """清空全部風險係數（供重新實作或重置使用）。"""
    conn = sqlite3.connect(DB_FILE)
    conn.execute("DELETE FROM esg_risk_factors")
    conn.commit()
    conn.close()


def get_geographic_risk_display():
    """地理風險圖用：回傳 list of dict {name, score, level, emoji}。level 為 高/中/低，emoji 為 🔴/🟡/🟢。"""
    region_scores = get_region_risk_scores()
    default_regions = ["中國", "越南", "台灣", "日本", "美國", "南韓", "墨西哥", "歐洲", "東南亞"]
    default_fallback = {"中國": 75, "越南": 55, "台灣": 35, "日本": 45, "美國": 50, "南韓": 50, "墨西哥": 45, "歐洲": 45, "東南亞": 55}
    seen = set()
    out = []
    for name in default_regions:
        if name in seen:
            continue
        seen.add(name)
        score = 0
        for rk, rs in region_scores.items():
            if rk in name or name in rk:
                score = max(score, min(100, rs))
                break
        if score == 0 and name in default_fallback:
            score = default_fallback[name]
        if score >= 70:
            level, emoji = "高", "🔴"
        elif score >= 40:
            level, emoji = "中", "🟡"
        else:
            level, emoji = "低", "🟢"
        out.append({"name": name, "score": score, "level": level, "emoji": emoji})
    for rk, rs in region_scores.items():
        if rk in seen:
            continue
        seen.add(rk)
        score = min(100, rs)
        if score >= 70:
            level, emoji = "高", "🔴"
        elif score >= 40:
            level, emoji = "中", "🟡"
        else:
            level, emoji = "低", "🟢"
        out.append({"name": rk, "score": score, "level": level, "emoji": emoji})
    return sorted(out, key=lambda x: -x["score"])


def get_risk_ai_suggestions(api_key: str, news_context: str, region_summary: str, model: str = "gemini-2.5-flash") -> str:
    """依地理風險與新聞由 AI 產出建議，考量政治風險、物流風險、匯率。"""
    if not (api_key and api_key.strip()):
        return "請在側欄設定 Gemini API Key 以取得 AI 建議。"
    prompt = f"""你是供應鏈風險分析師。請根據以下「地理風險」與「近期新聞」，針對 **政治風險、物流風險、匯率** 三方面，給我司簡要的供應鏈風險建議（每項 1～2 句，繁體中文）。

【地理風險】
{region_summary}

【近期新聞】
{news_context or "（尚無新聞，請先於「風險事件與交期」頁按「更新即時新聞」）"}

請依序回覆：
1. 政治風險建議
2. 物流風險建議
3. 匯率建議
簡潔、可直接供決策參考。"""
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=api_key.strip())
        resp = client.models.generate_content(
            model=(model or "gemini-2.5-flash").strip(),
            contents=prompt,
            config=types.GenerateContentConfig(temperature=0.3),
        )
        return (resp.text or "").strip()
    except Exception as e:
        return f"AI 建議暫時無法產生（{e}）。請確認 API Key 與網路。"


def load_preset_risk_factors():
    """載入預設風險係數範本（地區、事件類型、供應商類別）。"""
    conn = sqlite3.connect(DB_FILE)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    presets = [
        ("region", "東亞", 60, 1.0, "預設範本"),
        ("region", "日本", 40, 1.0, "預設範本"),
        ("region", "美國", 50, 1.0, "預設範本"),
        ("region", "越南", 55, 1.0, "預設範本"),
        ("region", "歐洲", 45, 1.0, "預設範本"),
        ("event_type", "地震", 85, 1.0, "預設範本"),
        ("event_type", "天候", 65, 1.0, "預設範本"),
        ("event_type", "政治", 75, 1.0, "預設範本"),
        ("event_type", "疫情", 70, 1.0, "預設範本"),
        ("event_type", "罷工", 60, 1.0, "預設範本"),
        ("event_type", "其他", 50, 1.0, "預設範本"),
        ("supplier_category", "高", 80, 1.0, "預設範本"),
        ("supplier_category", "中", 50, 1.0, "預設範本"),
        ("supplier_category", "低", 20, 1.0, "預設範本"),
    ]
    for rt, rk, rs, w, nt in presets:
        try:
            conn.execute(
                "INSERT OR REPLACE INTO esg_risk_factors (risk_type, risk_key, risk_score, weight, note, updated_at) VALUES (?,?,?,?,?,?)",
                (rt, rk, rs, w, nt, now),
            )
        except Exception:
            pass
    conn.commit()
    conn.close()


def get_procurement_by_region_with_risk():
    """依地區彙總採購金額並帶出該地區風險係數（ERP 供應鏈連動）。回傳 list of dict: display_name, total_amount, supplier_count, risk_score。"""
    proc = get_region_procurement_share()
    if not proc:
        return []
    region_scores = get_region_risk_scores()
    out = []
    for key, v in proc.items():
        display_name = v.get("display_name") or key
        total_amount = float(v.get("total_amount") or 0)
        supplier_count = int(v.get("supplier_count") or 0)
        risk_score = 0
        for rk, rs in region_scores.items():
            if rk in display_name or rk in key:
                risk_score = max(risk_score, min(100, rs))
        out.append({
            "display_name": display_name,
            "total_amount": round(total_amount, 0),
            "supplier_count": supplier_count,
            "risk_score": round(risk_score, 1),
        })
    return sorted(out, key=lambda x: -x["total_amount"])


def get_aggregated_risk_preview():
    """綜合風險預覽：據點 × 地區係數 × 供應商類別係數，回傳 list of dict。"""
    conn = sqlite3.connect(DB_FILE)
    factors = __pd_read("SELECT risk_type, risk_key, risk_score, weight FROM esg_risk_factors", conn)
    sup = __pd_read(
        "SELECT supplier_id as id, name, country, region, risk_level FROM suppliers WHERE (country IS NOT NULL AND country != '') OR (region IS NOT NULL AND region != '')",
        conn,
    )
    if sup is None:
        sup = __empty_df()
    sup["據點類型"] = "供應商"
    try:
        cust = __pd_read(
            "SELECT customer_id as id, name, country, region, risk_level FROM customers WHERE (country IS NOT NULL AND country != '') OR (region IS NOT NULL AND region != '')",
            conn,
        )
        cust["據點類型"] = "客戶"
        partners = __pd_concat(sup, cust)
    except Exception:
        partners = sup
    conn.close()

    if factors.empty or partners.empty:
        return []

    region_df = factors[factors["risk_type"] == "region"]
    region_map = dict(zip(region_df["risk_key"], region_df["risk_score"] * region_df["weight"])) if not region_df.empty else {}
    cat_df = factors[factors["risk_type"] == "supplier_category"]
    cat_map = dict(zip(cat_df["risk_key"], cat_df["risk_score"])) if not cat_df.empty else {}

    rows = []
    for _, p in partners.iterrows():
        region_score = None
        for k, v in region_map.items():
            if k in str(p.get("region") or "") or k in str(p.get("country") or ""):
                region_score = v
                break
        cat_score = cat_map.get(str(p.get("risk_level") or "").strip())
        if region_score is not None or cat_score is not None:
            r = (region_score or 0) + (cat_score or 0)
            level = "高" if r >= 100 else ("中" if r >= 50 else "低")
            rows.append({
                "據點": p["name"],
                "類型": p["據點類型"],
                "國家/地區": p.get("country") or p.get("region") or "-",
                "地區係數": region_score if region_score is not None else "-",
                "類別係數": cat_score if cat_score is not None else "-",
                "綜合關注": f"{r:.0f} ({level})",
            })
    return rows


# ── 內部輔助 ────────────────────────────────────────────────────────────

def __pd_read(query, conn, params=()):
    try:
        import pandas as pd
        out = pd.read_sql_query(query, conn, params=params if params else ())
        return out if out is not None else __empty_df()
    except Exception:
        return __empty_df()


def __empty_df():
    import pandas as pd
    return pd.DataFrame()


def __pd_concat(a, b):
    import pandas as pd
    return pd.concat([a, b], ignore_index=True)
