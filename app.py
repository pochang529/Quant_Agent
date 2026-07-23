import streamlit as st
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

st.set_page_config(page_title="台股客觀數據審核面板", layout="wide")

# --- 側邊欄 ---
st.sidebar.header("系統參數設定")
stock_id = st.sidebar.text_input("股票代號", value="6217").strip()
watchlist_raw = st.sidebar.text_area(
    "自選清單（多檔，每行一個代號）",
    value="6217",
    height=100,
    help="最多計算 10 檔；用於總表掃描",
)
lookback_years = st.sidebar.selectbox("歷史驗證回溯年數", options=[1, 2, 3], index=0)
fwd_days = st.sidebar.selectbox("綠燈／離場後觀察天數", options=[5, 10, 20], index=1)
brew_window = st.sidebar.selectbox("醞釀→綠燈觀察窗（交易日）", options=[5, 10, 20], index=1)
manual_disposition = st.sidebar.checkbox(
    "手動標記：目前為處置／注意／分盤",
    value=False,
    help="FinMind 處置表需付費方案；抓不到時請手動勾選",
)
risk_pct = st.sidebar.number_input("風險提示：單筆風險佔資金％", min_value=0.5, max_value=5.0, value=1.0, step=0.5)

_default_token = ""
try:
    _default_token = st.secrets.get("FINMIND_TOKEN", "") or ""
except Exception:
    _default_token = ""
api_token = st.sidebar.text_input(
    "FinMind API Token",
    value=_default_token,
    type="password",
    help="優先讀取 .streamlit/secrets.toml；亦可手動貼上",
)
st.sidebar.markdown("---")
st.sidebar.info("資料更新時間：每日 15:30 後 (依證交所公告為準)")
st.sidebar.caption("進場綠燈規則不變。處置期僅觀察；離場為獨立三關。")


@st.cache_data(ttl=3600)
def fetch_data(dataset, data_id, start, end, token):
    if not token:
        return pd.DataFrame()
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {
        "dataset": dataset,
        "data_id": data_id,
        "start_date": start,
        "end_date": end,
        "token": token,
    }
    try:
        resp = requests.get(url, params=params, timeout=60).json()
    except Exception:
        return pd.DataFrame()
    if resp.get("msg") == "success":
        return pd.DataFrame(resp.get("data", []))
    return pd.DataFrame()


@st.cache_data(ttl=3600)
def fetch_disposition(stock, start, end, token):
    """付費方案才有；失敗回傳空表。"""
    return fetch_data("TaiwanStockDispositionSecuritiesPeriod", stock, start, end, token)


def fmt_num(x, digits=2):
    if pd.isna(x):
        return "—"
    return f"{x:,.{digits}f}"


def pct(n, d):
    if d == 0:
        return "—"
    return f"{n / d * 100:.1f}%"


def conversion_rate(flags: pd.Series, target: pd.Series, window: int) -> tuple[int, int]:
    idx = np.flatnonzero(flags.to_numpy() == 1)
    arr = target.to_numpy()
    hits = 0
    for i in idx:
        end = min(i + window, len(arr) - 1)
        if arr[i + 1 : end + 1].any():
            hits += 1
    return hits, len(idx)


def build_panel(df_price, df_inst, df_margin):
    price = df_price.copy()
    price["date"] = pd.to_datetime(price["date"])
    price = price.sort_values("date").drop_duplicates("date")
    price["20MA"] = price["close"].rolling(20).mean()
    price["Volume_張"] = price["Trading_Volume"] / 1000
    price["5_Vol_MA"] = price["Volume_張"].rolling(5).mean()
    price["vol_threshold"] = price["5_Vol_MA"] * 1.5
    price["vol_ratio"] = price["Volume_張"] / price["5_Vol_MA"]
    price["ma_gap_pct"] = (price["close"] - price["20MA"]) / price["20MA"] * 100

    # ATR(14)
    prev_close = price["close"].shift(1)
    tr = pd.concat(
        [
            price["max"] - price["min"],
            (price["max"] - prev_close).abs(),
            (price["min"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    price["ATR14"] = tr.rolling(14).mean()

    inst = df_inst.copy()
    inst["date"] = pd.to_datetime(inst["date"])
    foreign = inst[inst["name"] == "Foreign_Investor"].copy()
    foreign["foreign_net"] = foreign["buy"] - foreign["sell"]
    trust = inst[inst["name"] == "Investment_Trust"].copy()
    trust["trust_net"] = trust["buy"] - trust["sell"]

    margin = df_margin.copy()
    margin["date"] = pd.to_datetime(margin["date"])
    margin["margin_change"] = margin["MarginPurchaseBuy"] - margin["MarginPurchaseSell"]

    panel = price.merge(foreign[["date", "foreign_net"]], on="date", how="left")
    panel = panel.merge(trust[["date", "trust_net"]], on="date", how="left")
    panel = panel.merge(margin[["date", "margin_change"]], on="date", how="left")
    panel = panel.sort_values("date").reset_index(drop=True)

    # 進場三關（不變）
    panel["gate1"] = panel["margin_change"].rolling(3).apply(
        lambda s: float(len(s) == 3 and (s < 0).all()), raw=False
    )
    panel["foreign_3ok"] = panel["foreign_net"].rolling(3).apply(
        lambda s: float(len(s) == 3 and (s > 0).all()), raw=False
    )
    panel["trust_3ok"] = panel["trust_net"].rolling(3).apply(
        lambda s: float(len(s) == 3 and (s > 0).all()), raw=False
    )
    panel["gate2"] = ((panel["foreign_3ok"] == 1) | (panel["trust_3ok"] == 1)).astype(float)
    panel["price_ok"] = (panel["close"] > panel["20MA"]).astype(float)
    panel["vol_ok"] = (panel["Volume_張"] > panel["vol_threshold"]).astype(float)
    panel["gate3"] = ((panel["price_ok"] == 1) & (panel["vol_ok"] == 1)).astype(float)
    panel["pass_count"] = panel[["gate1", "gate2", "gate3"]].sum(axis=1)
    panel["green"] = (panel["pass_count"] == 3).astype(int)
    panel["yellow"] = (panel["pass_count"] == 2).astype(int)

    near_ma = (panel["ma_gap_pct"] >= -2) & (panel["ma_gap_pct"] < 0)
    vol_warming = (panel["vol_ratio"] >= 1.0) & (panel["vol_ratio"] < 1.5)
    panel["brewing"] = (
        ((panel["gate1"] == 1) & (panel["gate2"] == 1) & (panel["gate3"] == 0))
        | ((panel["gate1"] == 1) & (panel["gate2"] == 1) & near_ma & vol_warming)
    ).astype(int)

    # 離場三關（獨立，不降進場門檻）
    panel["xgate1"] = panel["margin_change"].rolling(3).apply(
        lambda s: float(len(s) == 3 and (s > 0).all()), raw=False
    )
    panel["x_foreign_3"] = panel["foreign_net"].rolling(3).apply(
        lambda s: float(len(s) == 3 and (s < 0).all()), raw=False
    )
    panel["x_trust_3"] = panel["trust_net"].rolling(3).apply(
        lambda s: float(len(s) == 3 and (s < 0).all()), raw=False
    )
    panel["xgate2"] = ((panel["x_foreign_3"] == 1) | (panel["x_trust_3"] == 1)).astype(float)
    panel["xgate3"] = (panel["close"] < panel["20MA"]).astype(float)
    panel["x_pass"] = panel[["xgate1", "xgate2", "xgate3"]].sum(axis=1)
    panel["exit_red"] = (panel["x_pass"] == 3).astype(int)
    panel["exit_yellow"] = (panel["x_pass"] == 2).astype(int)

    return panel


def load_stock_bundle(sid, start_date, end_date, token):
    df_price = fetch_data("TaiwanStockPrice", sid, start_date, end_date, token)
    df_inst = fetch_data("TaiwanStockInstitutionalInvestorsBuySell", sid, start_date, end_date, token)
    df_margin = fetch_data("TaiwanStockMarginPurchaseShortSale", sid, start_date, end_date, token)
    if df_price.empty or df_inst.empty or df_margin.empty:
        return None
    panel = build_panel(df_price, df_inst, df_margin)
    usable = panel.dropna(
        subset=["20MA", "5_Vol_MA", "margin_change", "foreign_net", "trust_net"]
    ).copy()
    if usable.empty:
        return None
    return usable


def disposition_status(sid, start_date, end_date, token, manual_flag):
    df = fetch_disposition(sid, start_date, end_date, token)
    api_hit = False
    period_end = None
    detail = ""
    if not df.empty and "period_start" in df.columns:
        df = df.copy()
        df["period_start"] = pd.to_datetime(df["period_start"], errors="coerce")
        df["period_end"] = pd.to_datetime(df["period_end"], errors="coerce")
        today = pd.Timestamp(datetime.today().date())
        active = df[(df["period_start"] <= today) & (df["period_end"] >= today)]
        if not active.empty:
            api_hit = True
            row = active.iloc[-1]
            period_end = row["period_end"]
            detail = f"API：處置中至 {period_end.strftime('%Y-%m-%d')}"
        ended = df.dropna(subset=["period_end"]).sort_values("period_end")
    else:
        ended = pd.DataFrame()

    active_now = bool(manual_flag or api_hit)
    source = []
    if manual_flag:
        source.append("手動標記")
    if api_hit:
        source.append("FinMind處置表")
    if not source:
        source.append("未偵測（免費方案可能無處置API）")
    return {
        "active": active_now,
        "api_hit": api_hit,
        "period_end": period_end,
        "detail": detail or "／".join(source),
        "history": ended,
    }


# --- 主流程 ---
st.title(f"📊 {stock_id} 客觀交易訊號審核")

if not api_token:
    st.warning("👈 請先於左側欄位輸入您的 FinMind API Token 以啟動系統。")
    st.stop()

end_date = datetime.today().strftime("%Y-%m-%d")
start_date = (datetime.today() - timedelta(days=lookback_years * 365 + 60)).strftime("%Y-%m-%d")

with st.spinner("數據抓取與客觀運算中，請稍候..."):
    usable = load_stock_bundle(stock_id, start_date, end_date, api_token)
    if usable is None:
        st.error("獲取資料失敗。請確認 Token／代號是否正確。")
        st.stop()

    disp = disposition_status(stock_id, start_date, end_date, api_token, manual_disposition)

    # 大盤代理：0050
    mkt = load_stock_bundle("0050", start_date, end_date, api_token)
    market_weak = False
    market_note = "0050 資料不足，略過大盤過濾"
    if mkt is not None and len(mkt):
        m_latest = mkt.iloc[-1]
        market_weak = bool(m_latest["close"] < m_latest["20MA"])
        market_note = (
            f"0050 收盤 {fmt_num(m_latest['close'])}／20MA {fmt_num(m_latest['20MA'])}"
            + (" → 大盤偏弱" if market_weak else " → 大盤結構未弱")
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

# ===== 狀態列：處置／大盤 =====
st.subheader(f"基準日：{latest['date'].strftime('%Y-%m-%d')}")
b1, b2 = st.columns(2)
with b1:
    if disp["active"]:
        st.error(f"🚫 處置／注意狀態：**生效中**（{disp['detail']}）→ 進場綠燈降為「僅觀察」")
    else:
        st.success(f"✅ 處置／注意：**未生效**（{disp['detail']}）")
with b2:
    if market_weak:
        st.warning(f"📉 大盤過濾：{market_note} → 個股綠燈建議降為「僅觀察」")
    else:
        st.info(f"📈 大盤過濾：{market_note}")

# ===== 進場三關 =====
st.markdown("## 進場監測")
col1, col2, col3 = st.columns(3)
col1.metric("第一關：籌碼沉澱", "✅ 達標" if gate1 else "❌ 未達標", "融資連三日減少" if gate1 else "融資未連減")
col2.metric("第二關：法人進場", "✅ 達標" if gate2 else "❌ 未達標", "外資或投信連三日買超" if gate2 else "法人未連買")
col3.metric("第三關：技術確認", "✅ 達標" if gate3 else "❌ 未達標", "帶量站上月線" if gate3 else "量能或均線未達標")

pass_count = int(latest["pass_count"])
raw_green = pass_count == 3
observe_only = raw_green and (disp["active"] or market_weak)

st.markdown("---")
if raw_green and not observe_only:
    st.success("🟢 **綠燈結論：三關全數通過，客觀趨勢確立，可評估計畫性操作。**")
elif observe_only:
    st.warning(
        "🟡 **結構上三關通過，但因處置期或大盤偏弱 → 降為僅觀察（不視為可執行綠燈）。**"
    )
elif pass_count == 2:
    st.warning("🟡 **黃燈結論：滿足兩項條件，訊號醞釀中，建議嚴格控管部位觀望。**")
else:
    st.error("🔴 **紅燈結論：條件未齊備，客觀證據不足，嚴禁放大槓桿或進場攤平。**")

if int(latest["brewing"]) == 1 and not gate3:
    st.info("⏳ **達標前預警：第三關醞釀中**（綠燈規則未放寬）。")

# ===== 進場數據對映 =====
st.markdown("### 進場｜三關數據對映")
g1, g2, g3 = st.columns(3)
with g1:
    st.markdown("**第一關｜融資增減**")
    rows = [
        {
            "日期": r["date"].strftime("%Y-%m-%d"),
            "融資變化": fmt_num(r["margin_change"], 0),
            "當日": "✅ <0" if r["margin_change"] < 0 else "❌ ≥0",
        }
        for _, r in latest_3.iterrows()
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if not gate1:
        need = 3 - int((latest_3["margin_change"] < 0).sum())
        st.caption(f"未達標｜仍差 {need} 日融資減少")
with g2:
    st.markdown("**第二關｜外資／投信**")
    rows = [
        {
            "日期": r["date"].strftime("%Y-%m-%d"),
            "外資淨買超": fmt_num(r["foreign_net"], 0),
            "投信淨買超": fmt_num(r["trust_net"], 0),
        }
        for _, r in latest_3.iterrows()
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if not gate2:
        f_ok = int((latest_3["foreign_net"] > 0).sum())
        t_ok = int((latest_3["trust_net"] > 0).sum())
        st.caption(f"未達標｜外資 {f_ok}/3；投信 {t_ok}/3")
with g3:
    st.markdown("**第三關｜價＋量**")
    close_v, ma_v = float(latest["close"]), float(latest["20MA"])
    vol_v, thr_v = float(latest["Volume_張"]), float(latest["vol_threshold"])
    ma_gap = float(latest["ma_gap_pct"])
    vol_ratio = float(latest["vol_ratio"]) if pd.notna(latest["vol_ratio"]) else np.nan
    st.write(f"收盤 {fmt_num(close_v)}／20MA {fmt_num(ma_v)}／乖離 {fmt_num(ma_gap)}%")
    if price_ok:
        st.success(f"均線已過｜高出 {fmt_num(ma_gap)}%")
    else:
        st.error(f"均線未過｜仍差約 {fmt_num(abs(ma_gap))}%（約 {fmt_num(ma_v - close_v)} 元）")
    st.write(f"量 {fmt_num(vol_v, 0)} 張／門檻 {fmt_num(thr_v, 0)}／量比 {fmt_num(vol_ratio)}")
    if vol_ok:
        st.success(f"量能已過｜量比 {fmt_num(vol_ratio)}")
    else:
        st.error(f"量能未過｜量比仍差 {fmt_num(1.5 - vol_ratio)}（約再增 {fmt_num(thr_v - vol_v, 0)} 張）")

# ===== 離場監測 =====
st.markdown("---")
st.markdown("## 離場監測（持有時使用｜獨立三關）")
e1, e2, e3 = st.columns(3)
e1.metric("離場① 籌碼轉熱", "✅ 觸發" if x1 else "— 未觸發", "融資連三日增加" if x1 else "融資未連增")
e2.metric("離場② 法人轉賣", "✅ 觸發" if x2 else "— 未觸發", "外資或投信連三日賣超" if x2 else "法人未連賣")
e3.metric("離場③ 跌破月線", "✅ 觸發" if x3 else "— 未觸發", "收盤 < 20MA" if x3 else "仍在月線上")

x_pass = int(latest["x_pass"])
if x_pass == 3:
    st.error("🔴 **離場紅燈：三關破壞齊備，可評估計畫性出清／大減。**")
elif x_pass == 2:
    st.warning("🟡 **離場黃燈：兩關轉壞，建議減碼或收緊停損。**")
else:
    st.success("🟢 **離場結構未明顯破壞（非進場訊號）。**")

xg1, xg2, xg3 = st.columns(3)
with xg1:
    rows = [
        {
            "日期": r["date"].strftime("%Y-%m-%d"),
            "融資變化": fmt_num(r["margin_change"], 0),
            "當日": "✅ >0" if r["margin_change"] > 0 else "—",
        }
        for _, r in latest_3.iterrows()
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
with xg2:
    rows = [
        {
            "日期": r["date"].strftime("%Y-%m-%d"),
            "外資": fmt_num(r["foreign_net"], 0),
            "投信": fmt_num(r["trust_net"], 0),
        }
        for _, r in latest_3.iterrows()
    ]
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
with xg3:
    if x3:
        st.error(f"收盤 {fmt_num(close_v)} 已低於 20MA {fmt_num(ma_v)}（乖離 {fmt_num(ma_gap)}%）")
    else:
        st.success(f"收盤仍高於月線 {fmt_num(ma_gap)}%")

# ===== 部位／停損提示 =====
st.markdown("### 部位風控提示（非下單指令）")
atr = float(latest["ATR14"]) if pd.notna(latest["ATR14"]) else np.nan
if not np.isnan(atr) and atr > 0:
    stop_dist = atr * 1.5
    stop_price = close_v - stop_dist
    capital_demo = 1_000_000
    risk_budget = capital_demo * (risk_pct / 100)
    shares = risk_budget / stop_dist if stop_dist > 0 else 0
    lots = shares / 1000
    st.write(
        f"ATR(14)≈**{fmt_num(atr)}** 元｜建議停損距離 1.5×ATR≈**{fmt_num(stop_dist)}** 元"
        f"｜示意停損價≈**{fmt_num(stop_price)}**"
    )
    st.caption(
        f"若資金 100 萬、單筆風險 {risk_pct}%（{fmt_num(risk_budget, 0)} 元），"
        f"約可承受 **{fmt_num(shares, 0)} 股（{fmt_num(lots, 2)} 張）**（僅示意）。"
    )
else:
    st.caption("ATR 樣本不足，略過部位提示。")

# ===== 歷史驗證 =====
st.markdown("---")
st.markdown("## 歷史驗證")
st.caption(
    f"樣本：{usable['date'].min().strftime('%Y-%m-%d')} ~ {usable['date'].max().strftime('%Y-%m-%d')}"
    f"｜{len(usable)} 交易日｜進場綠燈規則未放寬"
)

hist = usable.copy()
hist["fwd_close"] = hist["close"].shift(-fwd_days)
hist["fwd_ret"] = (hist["fwd_close"] / hist["close"] - 1) * 100
# 離場紅燈後報酬（預期偏負較佳）
hist["x_fwd_ret"] = hist["fwd_ret"]

green_days = hist[hist["green"] == 1]
exit_days = hist[hist["exit_red"] == 1]
y2g_hits, y2g_n = conversion_rate(hist["yellow"], hist["green"], brew_window)
b2g_hits, b2g_n = conversion_rate(hist["brewing"], hist["green"], brew_window)
green_fwd = green_days.dropna(subset=["fwd_ret"])
exit_fwd = exit_days.dropna(subset=["x_fwd_ret"])
up_n = int((green_fwd["fwd_ret"] > 0).sum())
avg_ret = float(green_fwd["fwd_ret"].mean()) if len(green_fwd) else np.nan
exit_down = int((exit_fwd["x_fwd_ret"] < 0).sum())
exit_avg = float(exit_fwd["x_fwd_ret"].mean()) if len(exit_fwd) else np.nan

m1, m2, m3, m4 = st.columns(4)
m1.metric("綠燈出現率", pct(len(green_days), len(hist)), f"{len(green_days)}/{len(hist)}")
m2.metric(f"黃燈→綠燈（{brew_window}日）", pct(y2g_hits, y2g_n), f"{y2g_hits}/{y2g_n}")
m3.metric(f"醞釀→綠燈（{brew_window}日）", pct(b2g_hits, b2g_n), f"{b2g_hits}/{b2g_n}")
m4.metric(
    f"綠燈後{fwd_days}日上漲率",
    pct(up_n, len(green_fwd)),
    f"平均 {fmt_num(avg_ret)}%" if not np.isnan(avg_ret) else "樣本不足",
)

n1, n2, n3 = st.columns(3)
n1.metric("離場紅燈出現率", pct(len(exit_days), len(hist)), f"{len(exit_days)}/{len(hist)}")
n2.metric(
    f"離場紅燈後{fwd_days}日下跌率",
    pct(exit_down, len(exit_fwd)),
    f"平均 {fmt_num(exit_avg)}%" if not np.isnan(exit_avg) else "樣本不足",
)
n3.metric("歷史第三關達標率", pct(int((hist["gate3"] == 1).sum()), len(hist)))

# 出關後驗證（若有處置結束日）
st.markdown("### 出關後子樣本（處置 period_end 後）")
hist_disp = disp.get("history")
if hist_disp is not None and not hist_disp.empty and "period_end" in hist_disp.columns:
    rows_out = []
    for _, row in hist_disp.tail(8).iterrows():
        pe = row["period_end"]
        if pd.isna(pe):
            continue
        after = hist[hist["date"] > pe].head(fwd_days)
        if after.empty:
            continue
        first = after.iloc[0]
        last = after.iloc[-1]
        ret = (last["close"] / first["close"] - 1) * 100
        greened = int(after["green"].sum())
        rows_out.append(
            {
                "出關日": pe.strftime("%Y-%m-%d"),
                f"後{fwd_days}日報酬%": round(ret, 2),
                "其間綠燈日數": greened,
            }
        )
    if rows_out:
        st.dataframe(pd.DataFrame(rows_out), hide_index=True, use_container_width=True)
    else:
        st.caption("有處置紀錄但尚無足夠出關後交易日可算。")
else:
    st.caption("無處置歷史（付費 API 或該股無紀錄）→ 出關子樣本略過。可改用手動標記僅影響「現況僅觀察」。")

with st.expander("最近綠燈日"):
    show = green_fwd.tail(15)[["date", "close", "fwd_ret", "ma_gap_pct", "vol_ratio"]].copy()
    if show.empty:
        st.write("無樣本")
    else:
        show["date"] = show["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(show, hide_index=True, use_container_width=True)

with st.expander("最近離場紅燈日"):
    show = exit_fwd.tail(15)[["date", "close", "x_fwd_ret", "ma_gap_pct"]].copy()
    if show.empty:
        st.write("無樣本")
    else:
        show["date"] = show["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(show, hide_index=True, use_container_width=True)

# ===== 自選總表 =====
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
        u = load_stock_bundle(code, start_date, end_date, api_token)
        if u is None:
            scan_rows.append({"代號": code, "狀態": "資料失敗"})
            continue
        L = u.iloc[-1]
        pc = int(L["pass_count"])
        xp = int(L["x_pass"])
        dstat = disposition_status(code, start_date, end_date, api_token, manual_disposition if code == stock_id else False)
        entry_label = "綠" if pc == 3 else ("黃" if pc == 2 else "紅")
        if pc == 3 and (dstat["active"] or market_weak):
            entry_label = "僅觀察"
        exit_label = "離場紅" if xp == 3 else ("離場黃" if xp == 2 else "持有結構OK")
        scan_rows.append(
            {
                "代號": code,
                "收盤": round(float(L["close"]), 2),
                "進場燈": entry_label,
                "進場關數": pc,
                "醞釀": "Y" if int(L["brewing"]) == 1 else "",
                "離場燈": exit_label,
                "離場關數": xp,
                "乖離%": round(float(L["ma_gap_pct"]), 2),
                "量比": round(float(L["vol_ratio"]), 2) if pd.notna(L["vol_ratio"]) else None,
                "處置中": "Y" if dstat["active"] else "",
            }
        )

st.dataframe(pd.DataFrame(scan_rows), hide_index=True, use_container_width=True)
st.caption("自選最多 10 檔。大盤偏弱時，所有「綠」在主面板會降為僅觀察；總表對非主代號的處置僅依 API。")
