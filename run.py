"""Leo Capital: daily price alerts + paper portfolio + builds the Telegram app data (docs/data.json).
Runs free on GitHub Actions. Paper only - no real orders."""
import os, json, datetime as dt, urllib.parse, urllib.request
import yfinance as yf

H = os.path.dirname(os.path.abspath(__file__))
D = lambda f: os.path.join(H, "data", f)
BL = json.load(open(D("buylist.json")))
NEWS = {r[0]: r for r in json.load(open(D("news.json")))}
DEEP = {d["tk"]: d for d in json.load(open(D("deep.json")))}
st = json.load(open(D("price_alert_state.json"))) if os.path.exists(D("price_alert_state.json")) else {}
book = json.load(open(D("leo_paper.json")))
today = dt.date.today()
VN = {"G": "GREAT - wait for price", "W": "WATCH", "A": "AVOID"}


def send(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print(text); return
    for i in range(0, len(text), 3900):
        data = urllib.parse.urlencode({"chat_id": chat, "text": text[i:i + 3900]}).encode()
        print("telegram:", urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=30).status)


def price(t):
    return float(yf.Ticker(t).fast_info["lastPrice"])


fx = price("USDCAD=X")
held = {p["tk"] for p in book["pos"]}
watch, alerts = [], []
for s in BL:
    try:
        px = price(s["tk"])
    except Exception as e:
        print("price fail", s["tk"], e); continue
    trigger = s.get("alert_below", s["buy"])
    if px <= trigger and (not st.get(s["tk"]) or (today - dt.date.fromisoformat(st[s["tk"]])).days >= 7):
        msg = f"🟢 {s['name']} ({s['tk']}) is ON SALE: {s['cur']}{px:,.2f} ≤ buy-below {s['cur']}{trigger:,.2f}"
        if s.get("paper_cad") and s["tk"] not in held and book["cash_cad"] >= s["paper_cad"]:
            cad = s["paper_cad"]; usd = s["cur"].startswith("US")
            units = (cad / fx * 0.985 if usd else cad) / px
            book["pos"].append({"tk": s["tk"], "cur": "USD" if usd else "CAD", "units": round(units, 4), "px": round(px, 2), "cost_cad": cad, "date": str(today)})
            book["cash_cad"] -= cad; held.add(s["tk"]); book["log"].append(f"{today} BUY {s['tk']} C${cad} @ {px:.2f}")
            msg += f"\n   📒 Paper portfolio bought C${cad}."
        alerts.append(msg); st[s["tk"]] = str(today)
    n = NEWS.get(s["tk"]); dp = DEEP.get(s["tk"])
    verdict = dp["verdict"] if dp else ("BUY" if s["tk"] == "FFH.TO" else VN.get(n[1], "") if n else "")
    watch.append(dict(tk=s["tk"], name=dp["name"] if dp else s["name"].strip().title(), cur=s["cur"], px=round(px, 2), buy=round(trigger, 2),
                      gap=round(trigger / px - 1, 4), verdict=verdict, what=n[2] if n else "", story=n[3] if n else s.get("why", ""),
                      src=n[4] if n else "", deep=dp))
if alerts:
    send("🛒 LEO CAPITAL - BUY-PRICE ALERT\n\n" + "\n\n".join(alerts) + "\n\n👉 Ask Hermes for a fresh news check before any real buy.")

val = book["cash_cad"]; pos = []
for p in book["pos"]:
    try:
        px = price(p["tk"])
    except Exception:
        px = p["px"]
    v = p["units"] * px * (fx * 0.985 if p["cur"] == "USD" else 1)
    val += v
    pos.append(dict(tk=p["tk"], cost=p["cost_cad"], value=round(v, 2), pct=round(v / p["cost_cad"] - 1, 4), px=round(px, 2), buy_px=p["px"], cur=p["cur"], date=p["date"]))
bench = book["bench"]["units"] * price(book["bench"]["tk"])
port = dict(start=book["start"], start_cash=book["start_cash_cad"], value=round(val, 2), pct=round(val / book["start_cash_cad"] - 1, 4),
            bench=round(bench, 2), bench_pct=round(bench / book["start_cash_cad"] - 1, 4), cash=round(book["cash_cad"], 2), pos=pos, log=book["log"])
if today.weekday() == 0:
    send(f"📊 LEO CAPITAL weekly: paper C${val:,.0f} ({port['pct']:+.1%}) vs XEQT C${bench:,.0f} ({port['bench_pct']:+.1%}). Open the 📊 Leo Capital button for details.")

watch.sort(key=lambda w: (0 if w["verdict"].startswith("BUY") or "BUY" in w["verdict"] else 1, -w["gap"]))
json.dump(dict(updated=dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"), usdcad=round(fx, 4), portfolio=port, watch=watch),
          open(os.path.join(H, "docs", "data.json"), "w"), indent=1)
json.dump(st, open(D("price_alert_state.json"), "w"), indent=1)
json.dump(book, open(D("leo_paper.json"), "w"), indent=1)
print("ok", len(watch), "alerts", len(alerts), "value", round(val))
