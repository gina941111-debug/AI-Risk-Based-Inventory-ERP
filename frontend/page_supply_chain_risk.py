"""
frontend/page_supply_chain_risk.py
🌱 供應鏈與風險（前端 UI）
細項：供應鏈地圖、風險事件與交期
所有資料與邏輯由 backend/supply_chain_risk 提供
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
from backend import DB_FILE
from backend.supply_chain_news import get_news_from_db, refresh_news_for_countries
from backend.supply_chain_risk import (
    translate_to_chinese_traditional,
    infer_affected_region_from_news,
    get_suppliers_for_map,
    get_customers_for_map,
    get_recent_events_for_delay,
    get_risk_events_list,
    add_risk_event,
    delete_risk_event,
    get_affected_suppliers_by_event,
    get_affected_sales_orders_by_event,
    get_event_risk_scores,
    get_region_risk_scores,
    get_risk_factors,
    load_preset_risk_factors,
    clear_all_risk_factors,
    save_risk_factor,
    delete_risk_factor,
    get_aggregated_risk_preview,
    get_procurement_by_region_with_risk,
    get_risk_heatmap_data,
    get_heatmap_ai_summary,
    apply_heatmap_updates,
    upsert_risk_heatmap,
    reset_risk_heatmap_to_initial,
    get_impacted_pos,
    update_po_impact,
    get_ai_alternative_suggestions,
    what_if_simulation,
)

def render(sub_menu: str, api_key: str, gnews_api_key: str = "", gemini_model: str = "gemini-2.5-flash"):
    if sub_menu == "供應鏈地圖":
        _render_supply_chain_map(api_key, gnews_api_key, gemini_model)
    elif sub_menu == "風險事件與交期":
        _render_risk_events_delivery(api_key=api_key, gnews_api_key=gnews_api_key, gemini_model=gemini_model)
    else:
        st.info("請從左側選單選擇：供應鏈地圖、風險事件與交期。")


def _render_supply_chain_map(api_key: str, gnews_api_key: str, gemini_model: str = "gemini-2.5-flash"):
    """供應鏈地圖：第一層即時風險熱圖 + AI 摘要，第二層受災採購清單，第三層 What-If 模擬。"""
    st.subheader("🌍 供應鏈地圖 — 即時風險熱圖 · 受災採購清單 · 情境模擬")
    st.caption("熱圖顏色深度代表對「我司」的影響程度；點選熱點可檢視受災採購單；下方可進行 What-If 情境分析。")

    # ── 第一層：即時風險熱圖 (Risk Heatmap) ─────────────────────────────────
    st.markdown("#### 第一層：即時風險熱圖 (Risk Heatmap)")
    st.caption("在地圖上標示當前衝突／風險熱點，顏色深度代表對「你公司」的影響程度（0–100%）。**初始熱圖**會依各地區供應商的**採購佔比**自動對應風險高/中/低（佔比愈高集中度風險愈高）。")

    heatmap_rows = get_risk_heatmap_data()
    if heatmap_rows:
        df_heat = pd.DataFrame(heatmap_rows)
        df_heat["risk_pct"] = df_heat["risk_pct"].fillna(20)
        df_heat["hover_info"] = df_heat.apply(
            lambda r: f"<b>{r['display_name']}</b><br>對您公司的影響：{r['risk_pct']:.0f}%<br>{r['ai_summary'] or ''}",
            axis=1,
        )
        fig = px.scatter_geo(
            df_heat,
            lat="latitude",
            lon="longitude",
            color="risk_pct",
            color_continuous_scale="Reds",
            range_color=[0, 100],
            hover_name="display_name",
            custom_data=["hover_info"],
            size=[15] * len(df_heat),
            title="全球風險熱圖 — 顏色越深表示對您公司供應鏈影響越大",
        )
        fig.update_traces(
            hovertemplate="%{customdata[0]}<extra></extra>",
            marker=dict(line=dict(width=1, color="darkred")),
        )
        fig.update_layout(
            geo=dict(
                showland=True, landcolor="lightgray",
                showocean=True, oceancolor="aliceblue",
                showcountries=True, countrycolor="white",
                projection_type="natural earth",
            ),
            margin=dict(l=0, r=0, t=40, b=0),
            coloraxis_colorbar=dict(title="影響 %"),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("尚無熱圖資料。請由管理員於資料庫或後端維護供應商國家、地區與風險等級；經緯度由系統依國家於後端帶入，前端不呈現。")

    # AI 摘要（使用最近最新新聞，參考日期 2026-03-13）
    st.markdown("**AI 摘要**")
    news_context = ""
    try:
        # 取近一個月內、最近最新新聞（依發布/取得時間排序，最多 15 筆）
        news_list = get_news_from_db(limit=15, order_by_latest=True, within_days=30)
        if news_list:
            news_context = "\n".join([
                (n.get("title") or "") + " " + (n.get("summary") or "")[:200] + " [" + (n.get("published_at") or n.get("fetched_at") or "") + "]"
                for n in news_list
            ])
    except Exception:
        pass
    ref_date = "2026-03-13"  # 參考日期（可改為 datetime.now().strftime("%Y-%m-%d") 使用當日）
    col_ai_btn, col_reset = st.columns(2)
    with col_ai_btn:
        if st.button("🔄 產生／更新即時風險摘要", key="heatmap_ai_btn"):
            summary_text, updates = get_heatmap_ai_summary(api_key, news_context, reference_date=ref_date, model=gemini_model)
            apply_heatmap_updates(updates, summary_text)
            st.session_state["heatmap_ai_summary"] = summary_text
            st.rerun()
    with col_reset:
        if st.button("🔄 重置為初始熱圖", key="reset_heatmap_btn", help="清空已儲存的熱圖資料，還原為依供應商據點與風險事件計算的初始狀態"):
            reset_risk_heatmap_to_initial()
            if "heatmap_ai_summary" in st.session_state:
                del st.session_state["heatmap_ai_summary"]
            st.success("已重置為初始熱圖，請重整頁面。")
            st.rerun()
    if "heatmap_ai_summary" in st.session_state:
        st.info(st.session_state["heatmap_ai_summary"])
    else:
        st.caption("摘要依據：**近一個月內**快取新聞。請先至「風險事件與交期」頁按「📡 更新即時新聞」取得最近新聞，再按「產生／更新即時風險摘要」並設定 Gemini API Key。")

    # ── 手動調節熱圖風險% ─────────────────────────────────────────────────
    if heatmap_rows:
        with st.expander("✏️ 手動調節熱圖風險%", expanded=False):
            st.caption("若 AI 摘要與地圖未同步，可在此直接指定某熱點的影響%（0–100），套用後地圖會立即更新。")
            manual_options = [r["display_name"] for r in heatmap_rows]
            manual_idx = st.selectbox(
                "選擇要調節的熱點",
                range(len(manual_options)),
                format_func=lambda i: f"{manual_options[i]}（目前 {heatmap_rows[i].get('risk_pct', 20):.0f}%）",
                key="manual_heatmap_region",
            )
            new_pct = st.slider("影響 %", 0, 100, value=int(heatmap_rows[manual_idx].get("risk_pct") or 20), key="manual_risk_pct")
            if st.button("套用並更新地圖", key="manual_heatmap_apply"):
                r = heatmap_rows[manual_idx]
                upsert_risk_heatmap(
                    r["region_key"],
                    r["display_name"],
                    r["latitude"],
                    r["longitude"],
                    float(new_pct),
                    (r.get("ai_summary") or "")[:500],
                )
                st.success(f"已將「{r['display_name']}」的影響% 更新為 {new_pct}%，地圖已重新整理。")
                st.rerun()

    # ── 第二層：受災採購清單 (Impacted PO List) ───────────────────────────
    st.markdown("---")
    st.markdown("#### 第二層：受災採購清單 (Impacted PO List)")
    st.caption("選擇熱點（地區／國家）後，系統自動過濾並列出 ERP 內相關採購單與預計延遲、替代建議。")

    options_region = [r["display_name"] for r in heatmap_rows] if heatmap_rows else []
    options_region.insert(0, "— 請選擇熱點 —")
    selected_hotspot = st.selectbox("選擇熱點（地區／國家）", options_region, key="impacted_po_hotspot")
    region_key_for_po = None
    if selected_hotspot and selected_hotspot != "— 請選擇熱點 —" and heatmap_rows:
        for r in heatmap_rows:
            if r["display_name"] == selected_hotspot:
                region_key_for_po = r.get("region_key")
                break
        if not region_key_for_po:
            region_key_for_po = selected_hotspot

    if region_key_for_po:
        impacted = get_impacted_pos(region_key=region_key_for_po)
        if impacted:
            df_impacted = pd.DataFrame(impacted)
            col_btn, _ = st.columns([1, 3])
            with col_btn:
                if st.button("🤖 由 AI 分析並填寫替代建議", key="ai_alt_btn"):
                    with st.spinner("AI 正在分析並產生替代建議…"):
                        suggestions = get_ai_alternative_suggestions(api_key, impacted, selected_hotspot, model=gemini_model)
                    if suggestions:
                        for s in suggestions:
                            update_po_impact(s["po_id"], None, s["alternative_suggestion"])
                        st.success(f"已為 {len(suggestions)} 張採購單填寫替代建議，請見下方表格。")
                        st.rerun()
                    else:
                        if not (api_key and api_key.strip()):
                            st.warning("請在左側邊欄設定 Gemini API Key。")
                        else:
                            st.warning("AI 暫未回傳建議，請稍後再試或手動編輯。")
            st.dataframe(df_impacted.rename(columns={"po_id": "採購單 (PO#)", "supplier_name": "供應商", "key_materials": "關鍵物料", "estimated_delay": "預計延遲", "alternative_suggestion": "替代建議"}), use_container_width=True, hide_index=True)
            with st.expander("✏️ 編輯預計延遲與替代建議"):
                po_ids = [x["po_id"] for x in impacted]
                sel_po = st.selectbox("選擇採購單", po_ids, key="edit_po_impact")
                d = st.number_input("預計延遲天數", min_value=0, value=0, key="edit_delay")
                alt = st.text_area("替代建議", key="edit_alt", placeholder="例：轉由空運或調用越南庫存、尋找土耳其替代供應商")
                if st.button("儲存"):
                    update_po_impact(sel_po, d if d else None, alt.strip() or None)
                    st.success("已更新")
                    st.rerun()
        else:
            st.info("該熱點下目前無未結案採購單，或尚無供應商位於此地區。可至「採購管理」建立採購單並指定供應商。")
    else:
        st.info("請先於上方選擇一處熱點，系統將列出該地區的受災採購清單。")

    # ── 第三層：模擬情境分析 (What-If Simulation) ───────────────────────────
    st.markdown("---")
    st.markdown("#### 第三層：模擬情境分析 (What-If Simulation)")
    st.caption("主動詢問 AI：例如「如果南海發生衝突導致航線中斷 1 個月，哪些訂單會斷貨？」AI 將依 ERP 資料回覆影響與建議。")

    user_question = st.text_area(
        "輸入情境問題",
        value="如果南海發生衝突導致航線中斷 1 個月，哪些訂單會斷貨？",
        height=80,
        key="whatif_question",
        placeholder="例：如果紅海航線中斷 2 週，我司哪些採購單會受影響？",
    )
    if st.button("執行 What-If 模擬分析", key="whatif_btn"):
        with st.spinner("AI 正在依供應商、採購單與庫存資料分析情境…"):
            answer = what_if_simulation(api_key, user_question, model=gemini_model)
        st.markdown("**AI 回覆**")
        st.info(answer)
        st.caption("範例回覆：「這將影響您 40% 的原材料供應。建議現在就將 X 物料的安全庫存從 30 天提高到 60 天。」")




def _render_risk_events_delivery(api_key: str = "", gnews_api_key: str = "", gemini_model: str = "gemini-2.5-flash"):
    """風險事件與交期：主從式架構 (Master-Detail) 展示，包含 KPI 儀表板、事件選擇、及各事件的分類衝擊分頁。"""
    st.subheader("風險事件與交期影響")
    st.caption("透過建立數位孿生模型，即時分析突發風險對供應商及下游客戶訂單的蝴蝶效應。")

    # 更新即時新聞：依供應商國家從 GNews/RSS 抓取並寫入 DB
    _suppliers = get_suppliers_for_map()
    _countries = []
    if _suppliers is not None and not _suppliers.empty and "country" in _suppliers.columns:
        _countries = _suppliers["country"].dropna().unique().tolist()
        _countries = [str(c).strip() for c in _countries if str(c).strip()]
    if not _countries:
        _countries = ["台灣", "日本", "美國", "南韓", "中國", "越南", "墨西哥"]
    col_btn, col_help = st.columns([1, 3])
    with col_btn:
        if st.button("📡 更新即時新聞", key="refresh_news_btn", help="依供應商國家抓取最新新聞並寫入快取，供本頁「從最新新聞加入風險事件」與供應鏈地圖 AI 摘要使用。"):
            with st.spinner("正在抓取即時新聞…"):
                _res = refresh_news_for_countries(_countries, api_key=gnews_api_key or None, max_per_country=8)
            st.success(f"已更新 {_res.get('updated', 0)} 筆新聞。")
            st.rerun()
    with col_help:
        st.caption("**GNews API Key**（左側欄）為**選填**：未填寫時會改用 **Google News RSS** 抓取，免 Key 即可使用。若有 Key 可填寫以使用 GNews API 取得更多結果。")
    st.markdown("---")

    events = get_risk_events_list(20)
    has_events = events is not None and not events.empty
    
    # --- KPI Dashboard ---
    if has_events:
        total_events = len(events)
        # Calculate rough impacted orders and suppliers for currently active events
        total_impacted_suppliers = 0
        total_impacted_orders = 0
        for _, row in events.iterrows():
            affected_sup = get_affected_suppliers_by_event(row.get("region") or "", row.get("country") or "")
            if affected_sup:
                total_impacted_suppliers += len(affected_sup)
                impact_days_int = int(row.get("impact_days") or 7) if str(row.get("impact_days") or "7").isdigit() else 7
                affected_ord = get_affected_sales_orders_by_event(row.get("region") or "", row.get("country") or "", impact_days_int)
                total_impacted_orders += len(affected_ord) if affected_ord else 0
                
        metrics_cols = st.columns(3)
        with metrics_cols[0]:
            st.metric("🚨 最新風險事件", f"{total_events} 宗", delta="-近期頻發" if total_events > 3 else "穩定", delta_color="inverse")
        with metrics_cols[1]:
            st.metric("🏭 受波及供應商", f"{total_impacted_suppliers} 家")
        with metrics_cols[2]:
            st.metric("⚠️ 潛在受延遲訂單", f"{total_impacted_orders} 筆")
    st.markdown("---")

    # --- Master-Detail Layout ---
    col_master, col_detail = st.columns([1, 2])
    
    with col_master:
        st.markdown("#### 📜 風險事件清單")
        st.caption("清單**非寫死**：由您從「最新新聞」加入或手動新增。請先按上方「📡 更新即時新聞」從外部抓取新聞，**無固定排程**。")
        selected_event_index = 0
        if has_events:
            # Dropdown for selecting the active event
            event_labels = [f"{row['event_type']} - {row['region'] or row['country']}" for _, row in events.iterrows()]
            selected_event_index = st.radio("選擇要分析的事件", range(len(event_labels)), format_func=lambda i: event_labels[i], key="active_event_sel")
        else:
            st.info("尚無登錄風險事件。")

        st.markdown("<br>", unsafe_allow_html=True)

        # 從最新新聞下拉選單加入風險事件
        with st.expander("📰 從最新新聞加入風險事件"):
            try:
                raw_news = get_news_from_db(limit=50, order_by_latest=True, within_days=30)
                # 依標題+連結去重，保留每則新聞只出現一次（先出現的保留）
                seen = set()
                news_list = []
                for n in raw_news:
                    key = ((n.get("title") or "").strip()[:200], (n.get("url") or "").strip())
                    if key in seen or (key[0] == "" and key[1] == ""):
                        continue
                    seen.add(key)
                    news_list.append(n)
            except Exception:
                news_list = []
            if not news_list:
                st.info("尚無最新新聞。請先按本頁上方「📡 更新即時新聞」取得最近新聞後，再由此加入為風險事件。")
            else:
                news_options = [
                    f"{n.get('title') or '（無標題）'} — {n.get('country') or ''} / {n.get('region') or ''} ({n.get('published_at') or n.get('fetched_at') or ''})"
                    for n in news_list
                ]
                news_sel_idx = st.selectbox(
                    "選擇一則新聞加入為風險事件",
                    range(len(news_list)),
                    format_func=lambda i: (news_options[i][:80] + "…" if len(news_options[i]) > 80 else news_options[i]),
                    key="news_to_risk_sel",
                )
                if news_sel_idx is not None:
                    chosen = news_list[news_sel_idx]
                    raw_intro = "\n\n".join(
                        p for p in [
                            (chosen.get("title") or "").strip(),
                            (chosen.get("summary") or "").strip(),
                        ] if p
                    ).strip() or "（無簡介）"
                    # 一律顯示中文簡介：有 API Key 時用 Gemini 翻譯，無則顯示原文並提示
                    cache_key = f"news_cn_{chosen.get('id', news_sel_idx)}"
                    if cache_key not in st.session_state and api_key and api_key.strip():
                        with st.spinner("正在產生中文簡介…"):
                            st.session_state[cache_key] = translate_to_chinese_traditional(
                                api_key, raw_intro, gemini_model
                            )
                    intro_cn = st.session_state.get(cache_key)
                    if intro_cn is None:
                        intro_cn = raw_intro
                        if not (api_key and api_key.strip()):
                            st.caption("請在左側邊欄設定 Gemini API Key，即可顯示中文簡介。")
                    st.markdown("**中文簡介**")
                    st.caption("以下為該則新聞的繁體中文摘要，加入風險事件時會一併存入說明。")
                    st.info((intro_cn or raw_intro)[:500] + ("…" if len(intro_cn or raw_intro) > 500 else ""))
                    news_url = (chosen.get("url") or "").strip()
                    if news_url:
                        st.markdown(f"**報導連結：** [{news_url[:60]}…]({news_url})" if len(news_url) > 60 else f"**報導連結：** [{news_url}]({news_url})")
                    else:
                        st.caption("（此則新聞無連結）")
                    st.markdown("---")
                    # AI 判斷受影響區域（有 API Key 時呼叫並快取；版本號使更新判斷邏輯後舊快取自動失效）
                    _INFER_CACHE_VERSION = "3"
                    infer_key = f"news_infer_{chosen.get('id', news_sel_idx)}_v{_INFER_CACHE_VERSION}"
                    if infer_key not in st.session_state and api_key and api_key.strip():
                        with st.spinner("AI 正在判斷受影響區域…"):
                            st.session_state[infer_key] = infer_affected_region_from_news(
                                api_key, (intro_cn or raw_intro or "").strip()[:2500], gemini_model
                            )
                    inferred = st.session_state.get(infer_key) or {}
                    # 提供「重新判斷」按鈕，清除快取後重新呼叫 AI，不需重整頁面
                    if api_key and api_key.strip() and infer_key in st.session_state:
                        if st.button("🔄 重新由 AI 判斷受影響區域", key="reinfer_region_btn", help="清除目前快取，依最新規則重新判斷此則新聞的國家／地區與事件類型"):
                            if infer_key in st.session_state:
                                del st.session_state[infer_key]
                            st.rerun()
                    ai_country = (inferred.get("country") or "").strip() or (chosen.get("country") or "").strip()
                    ai_region = (inferred.get("region") or "").strip() or (chosen.get("region") or "").strip()
                    if ai_country == "不明":
                        ai_country = ""
                    if ai_region == "不明":
                        ai_region = ""
                    ai_etype = (inferred.get("event_type") or "").strip() or "其他"
                    if ai_country or ai_region or ai_etype:
                        st.markdown("**🤖 AI 判斷受影響區域**（可自行修改）")
                        st.caption("以下由 AI 依新聞內容判斷，公司可修正後再填入預估延期天數並加入。")
                    col_a, col_b, col_c = st.columns(3)
                    with col_a:
                        region_news = st.text_input("受影響地區", value=ai_region or "", key="news_region", placeholder="例：中東、關東")
                    with col_b:
                        country_news = st.text_input("受影響國家", value=ai_country or "", key="news_country", placeholder="例：以色列、日本")
                    with col_c:
                        etype_news = st.selectbox(
                            "事件類型",
                            ["地震", "天候", "政治", "疫情", "罷工", "其他"],
                            index=["地震", "天候", "政治", "疫情", "罷工", "其他"].index(ai_etype) if ai_etype in ["地震", "天候", "政治", "疫情", "罷工", "其他"] else 5,
                            key="news_event_type",
                        )
                    st.markdown("**預估交期延遲天數**（請由公司自行填寫）")
                    impact_days_news = st.number_input("延遲天數", min_value=0, value=7, key="news_impact_days", label_visibility="collapsed")
                    # 存入風險事件：一律使用中文簡介 + 報導連結
                    desc_base = (intro_cn or raw_intro or "").strip()
                    if news_url:
                        desc_base += "\n\n報導連結：" + news_url
                    desc_news = desc_base[:2000]
                    if st.button("加入為風險事件", key="add_risk_from_news_btn"):
                        add_risk_event(
                            etype_news,
                            region_news.strip() or "",
                            country_news.strip() or "",
                            impact_days_news,
                            desc_news,
                        )
                        st.success(f"已從新聞加入風險事件：{chosen.get('title', '')[:50]}…")
                        st.rerun()

        # Expenders for Management Actions (Add/Delete)
        with st.expander("➕ 新增風險事件（手動填寫）"):
            region_options = [
                "北區", "中區", "南區", "關東", "關西", "首爾", "西岸", "東岸",
                "巴伐利亞", "柏林", "北部", "南部", "曼谷", "吉隆坡", "雅加達", "新德里", "中北部", "其他"
            ]
            with st.form("event_form"):
                etype = st.selectbox("事件類型", ["地震", "天候", "政治", "疫情", "罷工", "其他"])
                sel_region = st.selectbox("影響地區", region_options)
                if sel_region == "其他":
                    sel_region = st.text_input("輸入其他影響地區")
                impact_days = st.number_input("預估交期延遲天數", min_value=0, value=7)
                desc = st.text_area("說明", placeholder="例如：暴雨導致港口淹水...")
                if st.form_submit_button("新增"):
                    add_risk_event(etype, sel_region, "", impact_days, desc)
                    st.success(f"已登錄事件：{etype} 於 {sel_region}")
                    st.rerun()

        if has_events:
            with st.expander("🗑️ 刪除歷史事件"):
                event_ids_del = events["id"].tolist()
                del_choice = st.selectbox("選擇要刪除的事件", range(len(event_ids_del)), format_func=lambda i: event_labels[i], key="del_event_sel")
                if st.button("確認刪除", key="del_event_btn") and del_choice is not None:
                    delete_risk_event(event_ids_del[del_choice])
                    st.success("已刪除")
                    st.rerun()

    with col_detail:
        if has_events:
            active_event = events.iloc[selected_event_index]
            etype = active_event.get("event_type") or ""
            region = active_event.get("region") or ""
            country = active_event.get("country") or ""
            impact = active_event.get("impact_days") or 0
            desc = active_event.get("description") or "無詳細說明"
            created_at = active_event.get("created_at") or ""
            
            # Retrieve risk scores
            event_score = get_event_risk_scores().get(etype, 50)
            region_str = (region or country or "").strip()
            region_score = 0
            for k, v in get_region_risk_scores().items():
                if k in region_str:
                    region_score = v
                    break
            comp = event_score + region_score
            level = "🔴 高" if comp >= 100 else ("🟡 中" if comp >= 50 else "🟢 低")
            
            # Detail header
            st.markdown(f"### 目前分析：【{etype} - {region or country}】")
            st.markdown(f"**延遲天數：** `+{impact} 天` &nbsp;&nbsp;|&nbsp;&nbsp; **關注等級：** {level} (風險分數 {comp:.0f}) &nbsp;&nbsp;|&nbsp;&nbsp; <span style='font-size:0.8em;color:gray;'>{created_at}</span>", unsafe_allow_html=True)
            if desc and str(desc).strip() != 'nan':
                 st.info(f"📝 說明：{desc}")
                 
            # Impact Data fetching
            affected_sup = get_affected_suppliers_by_event(region, country)
            impact_days_int = int(impact) if str(impact).isdigit() else 7
            affected_ord = get_affected_sales_orders_by_event(region, country, impact_days_int)
            
            # Tabs for deep-dive
            tab1, tab2 = st.tabs(["⚠️ 需聯絡客戶清單 (銷售單)", "🏭 斷鏈供應商清單 (採購端)"])
            
            with tab1:
                st.markdown("針對此事件，系統已自動比對供應商之待出貨採購單，並追溯即將受影響的下游銷售單。")
                if affected_ord:
                    df_orders = pd.DataFrame(affected_ord)
                    df_orders = df_orders.rename(columns={
                        "order_id": "訂單編號",
                        "customer_name": "客戶名稱",
                        "product_name": "受影響產品",
                        "original_delivery": "原定交期",
                        "new_delivery": "預計新交期"
                    })
                    st.dataframe(df_orders, use_container_width=True, hide_index=True)
                else:
                    st.success("目前無受波及且處理中的客戶銷售訂單，您的出貨暫時安全。")
                    
            with tab2:
                st.markdown("在此區域的活躍供應商列表，可能面臨出貨延遲或中斷。")
                if affected_sup:
                    df_sup = pd.DataFrame(affected_sup, columns=["供應商代碼", "供應商名稱", "國家", "地區"])
                    st.dataframe(df_sup, use_container_width=True, hide_index=True)
                else:
                    st.info("此地區目前尚無記錄中之供應商資料。")
        else:
            st.info("請從左側新增一個風險事件以開始分析。")



