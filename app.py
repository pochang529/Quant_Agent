import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(page_title="台股客觀數據審核面板", layout="wide")

# --- 側邊欄設定 ---
st.sidebar.header("系統參數設定")
stock_id = st.sidebar.text_input("股票代號", value="6217")
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

# --- 核心抓取函式 ---
@st.cache_data(ttl=3600)
def fetch_data(dataset, data_id, start, end, token):
    if not token: return pd.DataFrame()
    url = "https://api.finmindtrade.com/api/v4/data"
    params = {"dataset": dataset, "data_id": data_id, "start_date": start, "end_date": end, "token": token}
    resp = requests.get(url, params=params).json()
    return pd.DataFrame(resp.get('data', [])) if resp.get('msg') == 'success' else pd.DataFrame()

st.title(f"📊 {stock_id} 客觀交易訊號審核")

if not api_token:
    st.warning("👈 請先於左側欄位輸入您的 FinMind API Token 以啟動系統。")
    st.stop()

with st.spinner("數據抓取與客觀運算中，請稍候..."):
    # 抓取近40天確保均線可計算
    end_date = datetime.today().strftime('%Y-%m-%d')
    start_date = (datetime.today() - timedelta(days=40)).strftime('%Y-%m-%d')
    
    df_price = fetch_data("TaiwanStockPrice", stock_id, start_date, end_date, api_token)
    df_inst = fetch_data("TaiwanStockInstitutionalInvestorsBuySell", stock_id, start_date, end_date, api_token)
    df_margin = fetch_data("TaiwanStockMarginPurchaseShortSale", stock_id, start_date, end_date, api_token)

    if df_price.empty or df_inst.empty or df_margin.empty:
        st.error("獲取資料失敗。請確認 Token 是否正確，或該標的今日無交易。")
        st.stop()

    # --- 數據處理 ---
    df_price['date'] = pd.to_datetime(df_price['date'])
    df_price['20MA'] = df_price['close'].rolling(20).mean()
    df_price['Volume_張'] = df_price['Trading_Volume'] / 1000
    df_price['5_Vol_MA'] = df_price['Volume_張'].rolling(5).mean()
    
    df_inst['date'] = pd.to_datetime(df_inst['date'])
    df_foreign = df_inst[df_inst['name'] == 'Foreign_Investor'].copy()
    df_foreign['net_buy'] = df_foreign['buy'] - df_foreign['sell']
    df_trust = df_inst[df_inst['name'] == 'Investment_Trust'].copy()
    df_trust['net_buy'] = df_trust['buy'] - df_trust['sell']
    
    df_margin['date'] = pd.to_datetime(df_margin['date'])
    df_margin['margin_change'] = df_margin['MarginPurchaseBuy'] - df_margin['MarginPurchaseSell']
    
    # --- 審核邏輯 (取近3日) ---
    latest_3 = df_price['date'].tail(3).tolist()
    
    margin_3 = df_margin[df_margin['date'].isin(latest_3)]['margin_change'].tolist()
    gate1 = (len(margin_3) == 3 and all(x < 0 for x in margin_3))
    
    foreign_3 = df_foreign[df_foreign['date'].isin(latest_3)]['net_buy'].tolist()
    trust_3 = df_trust[df_trust['date'].isin(latest_3)]['net_buy'].tolist()
    gate2 = (len(foreign_3) == 3 and all(x > 0 for x in foreign_3)) or (len(trust_3) == 3 and all(x > 0 for x in trust_3))
    
    latest = df_price.iloc[-1]
    gate3 = (latest['close'] > latest['20MA']) and (latest['Volume_張'] > latest['5_Vol_MA'] * 1.5)
    
    # --- 介面呈現 ---
    st.subheader(f"基準日：{latest['date'].strftime('%Y-%m-%d')}")
    
    col1, col2, col3 = st.columns(3)
    col1.metric("第一關：籌碼沉澱", "✅ 達標" if gate1 else "❌ 未達標", "融資連三日減少" if gate1 else "融資未連減")
    col2.metric("第二關：法人進場", "✅ 達標" if gate2 else "❌ 未達標", "外資或投信連三日買超" if gate2 else "法人未連買")
    col3.metric("第三關：技術確認", "✅ 達標" if gate3 else "❌ 未達標", "帶量站上月線" if gate3 else "量能或均線未達標")
    
    st.markdown("---")
    pass_count = sum([gate1, gate2, gate3])
    if pass_count == 3:
        st.success("🟢 **綠燈結論：三關全數通過，客觀趨勢確立，可評估計畫性操作。**")
    elif pass_count == 2:
        st.warning("🟡 **黃燈結論：滿足兩項條件，訊號醞釀中，建議嚴格控管部位觀望。**")
    else:
        st.error("🔴 **紅燈結論：條件未齊備，客觀證據不足，嚴禁放大槓桿或進場攤平。**")
