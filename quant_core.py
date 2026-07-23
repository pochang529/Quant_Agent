"""Shared Quant_Agent logic: data, gates, peers, positions, alerts."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
POSITIONS_PATH = DATA_DIR / "positions.json"
STATE_PATH = DATA_DIR / "notify_state.json"
WATCHLIST_PATH = DATA_DIR / "watchlist.txt"


def fmt_num(x, digits=2):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    try:
        if pd.isna(x):
            return "—"
    except Exception:
        pass
    return f"{float(x):,.{digits}f}"


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


def fetch_disposition(stock, start, end, token):
    return fetch_data("TaiwanStockDispositionSecuritiesPeriod", stock, start, end, token)


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
    return usable if not usable.empty else None


def disposition_status(sid, start_date, end_date, token, manual_flag=False):
    df = fetch_disposition(sid, start_date, end_date, token)
    api_hit = False
    period_end = None
    detail = ""
    ended = pd.DataFrame()
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


def date_range(lookback_years: int):
    end_date = datetime.today().strftime("%Y-%m-%d")
    start_date = (datetime.today() - timedelta(days=lookback_years * 365 + 60)).strftime("%Y-%m-%d")
    return start_date, end_date


# ----- positions -----
def _ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_positions() -> list[dict]:
    _ensure_data_dir()
    if not POSITIONS_PATH.exists():
        return []
    try:
        raw = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        return raw.get("positions", [])
    except Exception:
        return []


def save_positions(positions: list[dict]):
    _ensure_data_dir()
    POSITIONS_PATH.write_text(
        json.dumps({"positions": positions}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def add_position(record: dict) -> dict:
    positions = load_positions()
    record = dict(record)
    record.setdefault("id", str(uuid.uuid4())[:8])
    record.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    record.setdefault("status", "open")
    positions.append(record)
    save_positions(positions)
    return record


def close_position(pos_id: str, exit_price: float | None = None, note: str = ""):
    positions = load_positions()
    for p in positions:
        if p.get("id") == pos_id:
            p["status"] = "closed"
            p["closed_at"] = datetime.now().isoformat(timespec="seconds")
            if exit_price is not None:
                p["exit_price"] = exit_price
                ep = float(p.get("entry_price") or 0)
                if ep:
                    p["realized_ret_pct"] = (exit_price / ep - 1) * 100
            if note:
                p["close_note"] = note
    save_positions(positions)


def snapshot_from_row(stock_id: str, row: pd.Series, entry_price: float, lots: float, notes: str, factors: list[str]):
    return {
        "stock_id": stock_id,
        "entry_date": pd.Timestamp(row["date"]).strftime("%Y-%m-%d"),
        "entry_price": float(entry_price),
        "lots": float(lots),
        "shares": float(lots) * 1000,
        "notes": notes,
        "factors": factors,
        "entry_gates": {
            "g1": bool(row["gate1"] == 1),
            "g2": bool(row["gate2"] == 1),
            "g3": bool(row["gate3"] == 1),
            "pass_count": int(row["pass_count"]),
            "brewing": bool(row["brewing"] == 1),
            "x_pass": int(row["x_pass"]),
        },
        "ma_gap_pct": float(row["ma_gap_pct"]) if pd.notna(row["ma_gap_pct"]) else None,
        "vol_ratio": float(row["vol_ratio"]) if pd.notna(row["vol_ratio"]) else None,
    }


# ----- peer historical paths -----
def find_peer_indices(
    hist: pd.DataFrame,
    pass_count: int,
    ma_gap_pct: float,
    gap_tol: float = 8.0,
    exclude_date=None,
) -> np.ndarray:
    """Same pass_count + ma_gap within tolerance; need room for forward days."""
    mask = hist["pass_count"] == pass_count
    if ma_gap_pct is not None and not (isinstance(ma_gap_pct, float) and np.isnan(ma_gap_pct)):
        mask &= (hist["ma_gap_pct"] - ma_gap_pct).abs() <= gap_tol
    if exclude_date is not None:
        mask &= hist["date"] != pd.Timestamp(exclude_date)
    idx = np.flatnonzero(mask.to_numpy())
    return idx


def peer_forward_paths(hist: pd.DataFrame, peer_idx: np.ndarray, horizon: int = 20) -> pd.DataFrame:
    """Return long dataframe: day_offset, peer_id, ret_pct (from peer entry close)."""
    closes = hist["close"].to_numpy()
    rows = []
    for j, i in enumerate(peer_idx):
        if i + horizon >= len(closes):
            # still take available
            max_h = len(closes) - 1 - i
            if max_h < 1:
                continue
            h = min(horizon, max_h)
        else:
            h = horizon
        base = closes[i]
        if base == 0 or np.isnan(base):
            continue
        for d in range(0, h + 1):
            rows.append(
                {
                    "peer_id": j,
                    "day": d,
                    "ret_pct": (closes[i + d] / base - 1) * 100,
                }
            )
    return pd.DataFrame(rows)


def summarize_peer_paths(path_df: pd.DataFrame) -> pd.DataFrame:
    if path_df.empty:
        return pd.DataFrame(columns=["day", "p25", "median", "p75", "count"])
    g = path_df.groupby("day")["ret_pct"]
    out = pd.DataFrame(
        {
            "day": g.median().index,
            "median": g.median().values,
            "p25": g.quantile(0.25).values,
            "p75": g.quantile(0.75).values,
            "count": g.count().values,
        }
    )
    return out


def actual_path_from_entry(hist: pd.DataFrame, entry_date: str, entry_price: float, horizon: int = 20) -> pd.DataFrame:
    """Mark-to-market path of an actual position vs entry_price."""
    ed = pd.Timestamp(entry_date)
    sub = hist[hist["date"] >= ed].head(horizon + 1).copy()
    if sub.empty:
        return pd.DataFrame(columns=["day", "ret_pct"])
    sub = sub.reset_index(drop=True)
    sub["day"] = np.arange(len(sub))
    sub["ret_pct"] = (sub["close"] / float(entry_price) - 1) * 100
    return sub[["day", "ret_pct", "date", "close"]]


def peer_stats_at_horizon(path_df: pd.DataFrame, horizon: int) -> dict:
    if path_df.empty:
        return {"n": 0}
    # last available ret per peer at or before horizon
    last = (
        path_df[path_df["day"] <= horizon]
        .sort_values(["peer_id", "day"])
        .groupby("peer_id")
        .tail(1)
    )
    rets = last["ret_pct"]
    return {
        "n": int(len(rets)),
        "up_rate": float((rets > 0).mean()) if len(rets) else None,
        "avg": float(rets.mean()) if len(rets) else None,
        "median": float(rets.median()) if len(rets) else None,
    }


# ----- alerts / notify -----
def summarize_alert(sid: str, latest: pd.Series, market_weak: bool, disp_active: bool) -> dict:
    pc = int(latest["pass_count"])
    xp = int(latest["x_pass"])
    raw_green = pc == 3
    observe = raw_green and (disp_active or market_weak)
    if observe:
        entry = "僅觀察"
    elif pc == 3:
        entry = "綠燈"
    elif pc == 2:
        entry = "黃燈"
    else:
        entry = "紅燈"
    if xp == 3:
        exit_l = "離場紅"
    elif xp == 2:
        exit_l = "離場黃"
    else:
        exit_l = "離場OK"
    return {
        "stock_id": sid,
        "date": pd.Timestamp(latest["date"]).strftime("%Y-%m-%d"),
        "close": float(latest["close"]),
        "entry": entry,
        "exit": exit_l,
        "pass_count": pc,
        "x_pass": xp,
        "brewing": bool(latest["brewing"] == 1),
        "ma_gap_pct": float(latest["ma_gap_pct"]) if pd.notna(latest["ma_gap_pct"]) else None,
        "vol_ratio": float(latest["vol_ratio"]) if pd.notna(latest["vol_ratio"]) else None,
        "disp_active": disp_active,
        "market_weak": market_weak,
    }


def format_alert_message(items: list[dict], title: str = "Quant_Agent 定時監測") -> str:
    lines = [f"【{title}】{datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    for a in items:
        brew = "｜醞釀" if a.get("brewing") else ""
        disp = "｜處置中" if a.get("disp_active") else ""
        mkt = "｜大盤弱" if a.get("market_weak") else ""
        lines.append(
            f"{a['stock_id']} 收{fmt_num(a['close'])}｜進場{a['entry']}({a['pass_count']}/3)"
            f"｜{a['exit']}({a['x_pass']}/3)｜乖離{fmt_num(a.get('ma_gap_pct'))}%"
            f"｜量比{fmt_num(a.get('vol_ratio'))}{brew}{disp}{mkt}"
        )
    return "\n".join(lines)


def send_telegram(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    if not token or not chat_id:
        return False, "缺少 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID"
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": chat_id, "text": text}, timeout=30)
        if r.ok:
            return True, "telegram ok"
        return False, r.text[:300]
    except Exception as e:
        return False, str(e)


def send_discord(webhook: str, text: str) -> tuple[bool, str]:
    if not webhook:
        return False, "缺少 DISCORD_WEBHOOK_URL"
    try:
        r = requests.post(webhook, json={"content": text[:1900]}, timeout=30)
        if r.ok or r.status_code == 204:
            return True, "discord ok"
        return False, r.text[:300]
    except Exception as e:
        return False, str(e)


def load_notify_state() -> dict:
    _ensure_data_dir()
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_notify_state(state: dict):
    _ensure_data_dir()
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def load_watchlist_file() -> list[str]:
    _ensure_data_dir()
    if not WATCHLIST_PATH.exists():
        WATCHLIST_PATH.write_text("6217\n", encoding="utf-8")
    codes = []
    for line in WATCHLIST_PATH.read_text(encoding="utf-8").splitlines():
        c = line.strip()
        if c and not c.startswith("#") and c not in codes:
            codes.append(c)
    return codes[:20]
