"""
定時監測：讀 data/watchlist.txt + 持倉代號，計算燈號並推播。
用法：
  python scripts/daily_notify.py
  python scripts/daily_notify.py --force   # 忽略狀態去重，強制推播
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import quant_core as qc


def _read_secrets_lines():
    secrets = ROOT / ".streamlit" / "secrets.toml"
    if not secrets.exists():
        return []
    text = secrets.read_text(encoding="utf-8-sig")
    return text.splitlines()


def load_token():
    token = os.environ.get("FINMIND_TOKEN", "")
    if token:
        return token
    for line in _read_secrets_lines():
        line = line.strip()
        if line.startswith("FINMIND_TOKEN"):
            parts = line.split("=", 1)
            if len(parts) == 2:
                return parts[1].strip().strip('"').strip("'")
    return ""


def load_push_config():
    cfg = {
        "telegram_token": os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "telegram_chat": os.environ.get("TELEGRAM_CHAT_ID", ""),
        "discord": os.environ.get("DISCORD_WEBHOOK_URL", ""),
        "gmail_user": os.environ.get("GMAIL_USER", ""),
        "gmail_pass": os.environ.get("GMAIL_APP_PASSWORD", ""),
        "gmail_to": os.environ.get("GMAIL_TO", ""),
        "app_url": os.environ.get("APP_URL", ""),
    }
    for line in _read_secrets_lines():
        line = line.strip()
        if "=" not in line or line.startswith("#"):
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k == "TELEGRAM_BOT_TOKEN":
            cfg["telegram_token"] = v
        elif k == "TELEGRAM_CHAT_ID":
            cfg["telegram_chat"] = v
        elif k == "DISCORD_WEBHOOK_URL":
            cfg["discord"] = v
        elif k == "GMAIL_USER":
            cfg["gmail_user"] = v
        elif k == "GMAIL_APP_PASSWORD":
            cfg["gmail_pass"] = v
        elif k == "GMAIL_TO":
            cfg["gmail_to"] = v
        elif k == "APP_URL":
            cfg["app_url"] = v
    if not cfg["gmail_to"] and cfg["gmail_user"]:
        cfg["gmail_to"] = cfg["gmail_user"]
    return cfg


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true", help="強制推播（略過狀態去重）")
    parser.add_argument("--years", type=int, default=1)
    args = parser.parse_args()

    token = load_token()
    if not token:
        print("ERROR: 找不到 FINMIND_TOKEN")
        sys.exit(1)

    push = load_push_config()
    start, end = qc.date_range(args.years)

    codes = qc.load_watchlist_file()
    for p in qc.load_positions():
        if p.get("status", "open") == "open":
            sid = p.get("stock_id")
            if sid and sid not in codes:
                codes.append(sid)

    if not codes:
        print("ERROR: watchlist 與持倉皆空")
        sys.exit(1)

    mkt = qc.load_stock_bundle("0050", start, end, token)
    market_weak = False
    if mkt is not None and len(mkt):
        ml = mkt.iloc[-1]
        market_weak = bool(ml["close"] < ml["20MA"])

    alerts = []
    for sid in codes:
        u = qc.load_stock_bundle(sid, start, end, token)
        if u is None:
            alerts.append(
                {
                    "stock_id": sid,
                    "date": end,
                    "close": None,
                    "entry": "資料失敗",
                    "exit": "-",
                    "pass_count": 0,
                    "x_pass": 0,
                    "brewing": False,
                    "ma_gap_pct": None,
                    "vol_ratio": None,
                    "disp_active": False,
                    "market_weak": market_weak,
                }
            )
            continue
        latest = u.iloc[-1]
        disp = qc.disposition_status(sid, start, end, token, False)
        alerts.append(qc.summarize_alert(sid, latest, market_weak, disp["active"]))

    # Gmail：純文字也不貼長網址，只提示看 HTML 連結文字
    msg_gmail = qc.format_alert_message(alerts, app_url="")
    if push.get("app_url"):
        msg_gmail = msg_gmail.rstrip() + "\n\n觀看完整數據（請點信件中的連結）"
    msg = qc.format_alert_message(alerts, app_url=push.get("app_url", ""))
    html = qc.format_alert_html(alerts, app_url=push.get("app_url", ""))

    # dedupe: only push when fingerprint changes (unless --force)
    state = qc.load_notify_state()
    fingerprint = "|".join(
        f"{a['stock_id']}:{a['entry']}:{a['exit']}:{a.get('brewing')}:{a.get('disp_active')}"
        for a in alerts
    )
    if not args.force and state.get("fingerprint") == fingerprint:
        print("NOCHANGE: 燈號未變，略過推播")
        print(msg)
        return

    ok_any = False
    results = []
    if push["telegram_token"] and push["telegram_chat"]:
        ok, info = qc.send_telegram(push["telegram_token"], push["telegram_chat"], msg)
        results.append(info)
        ok_any = ok_any or ok
    if push["discord"]:
        ok, info = qc.send_discord(push["discord"], msg)
        results.append(info)
        ok_any = ok_any or ok
    if push["gmail_user"] and push["gmail_pass"]:
        ok, info = qc.send_gmail(
            push["gmail_user"],
            push["gmail_pass"],
            push["gmail_to"],
            msg_gmail,
            html=html,
        )
        results.append(info)
        ok_any = ok_any or ok

    has_channel = bool(
        (push["telegram_token"] and push["telegram_chat"])
        or push["discord"]
        or (push["gmail_user"] and push["gmail_pass"])
    )
    if not has_channel:
        print("WARN: 未設定 Telegram／Discord／Gmail，僅列印：")
        print(msg)
        sys.exit(0)

    print(msg)
    print("PUSH:", results)
    if ok_any:
        state["fingerprint"] = fingerprint
        state["last_push"] = msg
        qc.save_notify_state(state)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()
