import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime

import quant_core as qc

st.set_page_config(page_title="台股客觀數據審核面板", layout="wide")

# --- 側邊欄 ---
st.sidebar.header("系統參數設定")
stock_id = st.sidebar.text_input("股票代號", value="6217").strip()
watchlist_raw = st.sidebar.text_area(
    "自選清單（多檔，每行一個代號）",
    value="6217",
    height=80,
    help="最多 10 檔；並會寫入 data/watchlist.txt 供定時推播",
)
lookback_years = st.sidebar.selectbox("歷史驗證回溯年數", options=[1, 2, 3], index=0)
fwd_days = st.sidebar.selectbox("綠燈／離場／對照觀察天數", options=[5, 10, 20], index=1)
brew_window = st.sidebar.selectbox("醞釀→綠燈觀察窗（交易日）", options=[5, 10, 20], index=1)
peer_horizon = st.sidebar.selectbox("歷史路徑天數", options=[10, 20, 40], index=1)
gap_tol = st.sidebar.slider("同條件乖離容差（％點）", min_value=3.0, max_value=15.0, value=8.0, step=1.0)
manual_disposition = st.sidebar.checkbox("手動標記：目前為處置／注意／分盤", value=False)
risk_pct = st.sidebar.number_input("風險提示：單筆風險佔資金％", min_value=0.5, max_value=5.0, value=1.0, step=0.5)

_default_token = ""
try:
    _default_token = st.secrets.get("FINMIND_TOKEN", "") or ""
except Exception:
    _default_token = ""
api_token = st.sidebar.text_input("FinMind API Token", value=_default_token, type="password")
st.sidebar.markdown("---")
st.sidebar.info("資料更新時間：每日 15:30 後")
st.sidebar.caption("進場綠燈不變｜實際組可對照同條件歷史｜定時推播見 scripts/")


@st.cache_data(ttl=3600)
def cached_bundle(sid, start, end, token):
    return qc.load_stock_bundle(sid, start, end, token)


@st.cache_data(ttl=3600)
def cached_disp(sid, start, end, token, manual):
    return qc.disposition_status(sid, start, end, token, manual)


st.title(f"📊 {stock_id} 客觀交易訊號審核")
if not api_token:
    st.warning("👈 請先輸入 FinMind API Token")
    st.stop()

start_date, end_date = qc.date_range(lookback_years)

# persist watchlist for notifier
try:
    codes_save = []
    for line in watchlist_raw.replace(",", "\n").splitlines():
        c = line.strip()
        if c and c not in codes_save:
            codes_save.append(c)
    qc.DATA_DIR.mkdir(parents=True, exist_ok=True)
    qc.WATCHLIST_PATH.write_text("\n".join(codes_save[:20]) + "\n", encoding="utf-8")
except Exception:
    pass

with st.spinner("數據抓取與客觀運算中…"):
    usable = cached_bundle(stock_id, start_date, end_date, api_token)
    if usable is None:
        st.error("獲取資料失敗。請確認 Token／代號。")
        st.stop()
    disp = cached_disp(stock_id, start_date, end_date, api_token, manual_disposition)
    mkt = cached_bundle("0050", start_date, end_date, api_token)
    market_weak = False
    market_note = "0050 資料不足"
    if mkt is not None and len(mkt):
        m_latest = mkt.iloc[-1]
        market_weak = bool(m_latest["close"] < m_latest["20MA"])
        market_note = (
            f"0050 {qc.fmt_num(m_latest['close'])}/{qc.fmt_num(m_latest['20MA'])}"
            + (" → 偏弱" if market_weak else " → 未弱")
        )

latest = usable.iloc[-1]
latest_3 = usable.tail(3)
gate1 = bool(latest["gate1"] == 1)
gate2 = bool(latest["gate2"] == 1)
gate3 = bool(latest["gate3"] == 1)
price_ok = bool(latest["price_ok"] == 1)
vol_ok = bool(latest["vol_ok"] == 1)
x1 = bool(latest["xgate1"] == 1)
x2 = bool(latest["xgate2"] == 1)
x3 = bool(latest["xgate3"] == 1)
close_v = float(latest["close"])
ma_v = float(latest["20MA"])
ma_gap = float(latest["ma_gap_pct"])
vol_v = float(latest["Volume_張"])
thr_v = float(latest["vol_threshold"])
vol_ratio = float(latest["vol_ratio"]) if pd.notna(latest["vol_ratio"]) else np.nan

st.subheader(f"基準日：{latest['date'].strftime('%Y-%m-%d')}")
b1, b2 = st.columns(2)
with b1:
    if disp["active"]:
        st.error(f"🚫 處置／注意：**生效中**（{disp['detail']}）→ 綠燈降為僅觀察")
    else:
        st.success(f"✅ 處置／注意：未生效（{disp['detail']}）")
with b2:
    if market_weak:
        st.warning(f"📉 大盤：{market_note} → 綠燈建議僅觀察")
    else:
        st.info(f"📈 大盤：{market_note}")

# ===== 進場 =====
st.markdown("## 進場監測")
c1, c2, c3 = st.columns(3)
c1.metric("第一關：籌碼沉澱", "✅ 達標" if gate1 else "❌ 未達標", "融資連三日減少" if gate1 else "融資未連減")
c2.metric("第二關：法人進場", "✅ 達標" if gate2 else "❌ 未達標", "外資或投信連三日買超" if gate2 else "法人未連買")
c3.metric("第三關：技術確認", "✅ 達標" if gate3 else "❌ 未達標", "帶量站上月線" if gate3 else "量能或均線未達標")

pass_count = int(latest["pass_count"])
raw_green = pass_count == 3
observe_only = raw_green and (disp["active"] or market_weak)
if raw_green and not observe_only:
    st.success("🟢 **綠燈：三關全過，可評估計畫性操作。**")
elif observe_only:
    st.warning("🟡 **結構三關過，但處置或大盤弱 → 僅觀察。**")
elif pass_count == 2:
    st.warning("🟡 **黃燈：兩關滿足，醞釀中。**")
else:
    st.error("🔴 **紅燈：條件未齊。**")
if int(latest["brewing"]) == 1 and not gate3:
    st.info("⏳ 達標前預警：第三關醞釀中（門檻未放寬）")

st.markdown("### 進場｜數據對映")
g1, g2, g3 = st.columns(3)
with g1:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "日期": r["date"].strftime("%Y-%m-%d"),
                    "融資變化": qc.fmt_num(r["margin_change"], 0),
                    "當日": "✅ <0" if r["margin_change"] < 0 else "❌ ≥0",
                }
                for _, r in latest_3.iterrows()
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
with g2:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "日期": r["date"].strftime("%Y-%m-%d"),
                    "外資": qc.fmt_num(r["foreign_net"], 0),
                    "投信": qc.fmt_num(r["trust_net"], 0),
                }
                for _, r in latest_3.iterrows()
            ]
        ),
        hide_index=True,
        use_container_width=True,
    )
with g3:
    st.write(f"收盤 {qc.fmt_num(close_v)}／20MA {qc.fmt_num(ma_v)}／乖離 {qc.fmt_num(ma_gap)}%")
    if price_ok:
        st.success(f"均線已過｜+{qc.fmt_num(ma_gap)}%")
    else:
        st.error(f"均線未過｜仍差約 {qc.fmt_num(abs(ma_gap))}%（約 {qc.fmt_num(ma_v - close_v)} 元）")
    st.write(f"量 {qc.fmt_num(vol_v, 0)}／門檻 {qc.fmt_num(thr_v, 0)}／量比 {qc.fmt_num(vol_ratio)}")
    if vol_ok:
        st.success(f"量能已過｜{qc.fmt_num(vol_ratio)}")
    else:
        st.error(f"量能未過｜量比仍差 {qc.fmt_num(1.5 - vol_ratio)}")

# ===== 離場 =====
st.markdown("---")
st.markdown("## 離場監測")
e1, e2, e3 = st.columns(3)
e1.metric("離場① 融資連增", "✅" if x1 else "—", "觸發" if x1 else "未觸發")
e2.metric("離場② 法人連賣", "✅" if x2 else "—", "觸發" if x2 else "未觸發")
e3.metric("離場③ 跌破月線", "✅" if x3 else "—", "觸發" if x3 else "未觸發")
xp = int(latest["x_pass"])
if xp == 3:
    st.error("🔴 離場紅燈：可評估出清／大減")
elif xp == 2:
    st.warning("🟡 離場黃燈：建議減碼或收緊停損")
else:
    st.success("🟢 離場結構未明顯破壞（非進場訊號）")

atr = float(latest["ATR14"]) if pd.notna(latest["ATR14"]) else np.nan
st.markdown("### 部位風控提示")
if not np.isnan(atr) and atr > 0:
    stop_dist = atr * 1.5
    risk_budget = 1_000_000 * (risk_pct / 100)
    shares = risk_budget / stop_dist
    st.caption(
        f"ATR≈{qc.fmt_num(atr)}｜1.5×ATR停損距≈{qc.fmt_num(stop_dist)}｜"
        f"示意停損價≈{qc.fmt_num(close_v - stop_dist)}｜"
        f"資金100萬風險{risk_pct}%≈{qc.fmt_num(shares, 0)}股（{qc.fmt_num(shares/1000, 2)}張）"
    )

# ===== 實際組 × 歷史對照 =====
st.markdown("---")
st.markdown("## 實際組 × 同條件歷史對照")
st.caption(
    f"同條件定義：進場關數相同（目前 {pass_count}/3）且乖離差在 ±{gap_tol}% 點內。"
    "實際持倉走勢疊加在歷史中位數／四分位路徑上，供盈虧後調參。"
)

# simulate today as entry
sim_pc = pass_count
sim_gap = ma_gap
peer_idx = qc.find_peer_indices(usable, sim_pc, sim_gap, gap_tol=gap_tol, exclude_date=latest["date"])
# need forward room
peer_idx = np.array([i for i in peer_idx if i + 1 < len(usable)])
path_df = qc.peer_forward_paths(usable, peer_idx, horizon=peer_horizon)
summary = qc.summarize_peer_paths(path_df)
stats = qc.peer_stats_at_horizon(path_df, fwd_days)

s1, s2, s3, s4 = st.columns(4)
s1.metric("同條件歷史樣本", f"{stats.get('n', 0)} 組")
s2.metric(
    f"歷史後{fwd_days}日上漲率",
    f"{stats['up_rate'] * 100:.1f}%" if stats.get("up_rate") is not None else "—",
)
s3.metric(f"歷史後{fwd_days}日平均%", qc.fmt_num(stats.get("avg")))
s4.metric(f"歷史後{fwd_days}日中位%", qc.fmt_num(stats.get("median")))

# chart: peers band + optional actuals
chart_df = None
if not summary.empty:
    chart_df = summary.set_index("day")[["p25", "median", "p75"]]
    chart_df.columns = ["歷史P25%", "歷史中位%", "歷史P75%"]

# open positions for this stock
positions = qc.load_positions()
open_pos = [p for p in positions if p.get("stock_id") == stock_id and p.get("status", "open") == "open"]

overlay_cols = {}
for p in open_pos:
    ap = qc.actual_path_from_entry(usable, p["entry_date"], float(p["entry_price"]), horizon=peer_horizon)
    if not ap.empty:
        overlay_cols[f"實際 {p['id']}({p['entry_date']})"] = ap.set_index("day")["ret_pct"]

# also "若今日進場" mark day0=0
if chart_df is not None:
    plot = chart_df.copy()
    for name, ser in overlay_cols.items():
        plot = plot.join(ser.rename(name), how="outer")
    st.line_chart(plot)
    st.caption("縱軸：相對進場價報酬％｜橫軸：進場後第 N 個交易日")
else:
    st.info("同條件歷史樣本不足，無法畫路徑。可放寬乖離容差或拉長回溯年數。")

# register actual position
st.markdown("### 登記實際進場（寫入本機 data/positions.json，不上傳 Git）")
with st.form("add_pos"):
    fc1, fc2, fc3 = st.columns(3)
    entry_price = fc1.number_input("進場均價", min_value=0.01, value=float(close_v), step=0.5)
    lots = fc2.number_input("張數", min_value=0.0, value=10.0, step=1.0)
    entry_date = fc3.date_input("進場日", value=pd.Timestamp(latest["date"]).date())
    notes = st.text_input("備註（例：對照朋友單／自有資金）", value="")
    factors = st.multiselect(
        "當下記錄的候選因子（盈虧後調參用）",
        options=[
            "三關黃燈",
            "三關綠燈",
            "第三關醞釀",
            "深跌乖離",
            "量縮進場",
            "融資買進",
            "出關預期",
            "消息驅動對照",
            "大盤偏弱仍進",
            "處置／注意期",
        ],
        default=(["三關黃燈", "第三關醞釀"] if pass_count == 2 else [])
        + (["深跌乖離"] if ma_gap < -10 else [])
        + (["量縮進場"] if pd.notna(vol_ratio) and vol_ratio < 1 else []),
    )
    submitted = st.form_submit_button("登記實際組")
    if submitted:
        # use nearest row on/after entry_date for snapshot; price from form
        ed = pd.Timestamp(entry_date)
        rows = usable[usable["date"] >= ed]
        row = rows.iloc[0] if not rows.empty else latest
        rec = qc.snapshot_from_row(stock_id, row, entry_price, lots, notes, factors)
        rec["entry_date"] = ed.strftime("%Y-%m-%d")
        qc.add_position(rec)
        st.success(f"已登記 {rec['id']}｜{stock_id} {lots}張 @ {entry_price}")
        st.rerun()

if positions:
    st.markdown("### 持倉清單")
    show = []
    for p in positions:
        mt = None
        if p.get("status", "open") == "open" and p.get("stock_id") == stock_id:
            mt = (close_v / float(p["entry_price"]) - 1) * 100
        show.append(
            {
                "id": p.get("id"),
                "代號": p.get("stock_id"),
                "進場日": p.get("entry_date"),
                "均價": p.get("entry_price"),
                "張": p.get("lots"),
                "關數": p.get("entry_gates", {}).get("pass_count"),
                "因子": ",".join(p.get("factors") or []),
                "狀態": p.get("status", "open"),
                "現況報酬%": round(mt, 2) if mt is not None else p.get("realized_ret_pct"),
                "備註": p.get("notes", ""),
            }
        )
    st.dataframe(pd.DataFrame(show), hide_index=True, use_container_width=True)

    close_id = st.selectbox("平倉／結案（寫入結果供調參）", options=[""] + [p["id"] for p in positions if p.get("status") == "open"])
    if close_id:
        cx1, cx2 = st.columns(2)
        exit_px = cx1.number_input("平倉價", min_value=0.01, value=float(close_v), key="exit_px")
        if cx2.button("確認平倉"):
            qc.close_position(close_id, exit_price=exit_px)
            st.success("已結案")
            st.rerun()

# ===== 歷史驗證（總覽）=====
st.markdown("---")
st.markdown("## 歷史驗證（規則統計）")
hist = usable.copy()
hist["fwd_ret"] = (hist["close"].shift(-fwd_days) / hist["close"] - 1) * 100
green_days = hist[hist["green"] == 1]
exit_days = hist[hist["exit_red"] == 1]
y2g_hits, y2g_n = qc.conversion_rate(hist["yellow"], hist["green"], brew_window)
b2g_hits, b2g_n = qc.conversion_rate(hist["brewing"], hist["green"], brew_window)
green_fwd = green_days.dropna(subset=["fwd_ret"])
exit_fwd = exit_days.dropna(subset=["fwd_ret"])
m1, m2, m3, m4 = st.columns(4)
m1.metric("綠燈出現率", qc.pct(len(green_days), len(hist)))
m2.metric(f"黃→綠({brew_window}日)", qc.pct(y2g_hits, y2g_n))
m3.metric(f"醞釀→綠({brew_window}日)", qc.pct(b2g_hits, b2g_n))
m4.metric(
    f"綠燈後{fwd_days}日上漲率",
    qc.pct(int((green_fwd["fwd_ret"] > 0).sum()), len(green_fwd)),
    f"平均 {qc.fmt_num(green_fwd['fwd_ret'].mean() if len(green_fwd) else np.nan)}%",
)
n1, n2 = st.columns(2)
n1.metric("離場紅出現率", qc.pct(len(exit_days), len(hist)))
n2.metric(
    f"離場紅後{fwd_days}日下跌率",
    qc.pct(int((exit_fwd["fwd_ret"] < 0).sum()), len(exit_fwd)),
    f"平均 {qc.fmt_num(exit_fwd['fwd_ret'].mean() if len(exit_fwd) else np.nan)}%",
)

# ===== 自選掃描 =====
st.markdown("---")
st.markdown("## 自選清單掃描")
codes = []
for line in watchlist_raw.replace(",", "\n").splitlines():
    c = line.strip()
    if c and c not in codes:
        codes.append(c)
codes = codes[:10]
scan_rows = []
with st.spinner("掃描自選…"):
    for code in codes:
        u = cached_bundle(code, start_date, end_date, api_token)
        if u is None:
            scan_rows.append({"代號": code, "狀態": "失敗"})
            continue
        L = u.iloc[-1]
        pc = int(L["pass_count"])
        xp = int(L["x_pass"])
        dstat = cached_disp(code, start_date, end_date, api_token, manual_disposition if code == stock_id else False)
        entry_label = "綠" if pc == 3 else ("黃" if pc == 2 else "紅")
        if pc == 3 and (dstat["active"] or market_weak):
            entry_label = "僅觀察"
        scan_rows.append(
            {
                "代號": code,
                "收盤": round(float(L["close"]), 2),
                "進場燈": entry_label,
                "進場關": pc,
                "醞釀": "Y" if int(L["brewing"]) == 1 else "",
                "離場燈": "離場紅" if xp == 3 else ("離場黃" if xp == 2 else "OK"),
                "離場關": xp,
                "乖離%": round(float(L["ma_gap_pct"]), 2),
                "量比": round(float(L["vol_ratio"]), 2) if pd.notna(L["vol_ratio"]) else None,
                "處置": "Y" if dstat["active"] else "",
            }
        )
st.dataframe(pd.DataFrame(scan_rows), hide_index=True, use_container_width=True)

st.markdown("---")
st.markdown("## 定時監測與推播")
st.markdown(
    """
1. 在 `.streamlit/secrets.toml` 或環境變數加入（擇一通道即可）：
   - `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
   - 或 `DISCORD_WEBHOOK_URL`
2. 本機執行一次測試：`python scripts/daily_notify.py`
3. 固定時間：以系統管理員身分執行  
   `powershell -ExecutionPolicy Bypass -File scripts/register_windows_task.ps1`  
   預設每個交易日概念用 **每日 15:40**（可改腳本內時間）。
"""
)
