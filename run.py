"""Leo Capital: price alerts + paper portfolio + NEWS/price-move watch + Telegram app data (docs/data.json).
Runs free on GitHub Actions twice each weekday: morning (~9:45 ET, after the open) and evening (~4:30 ET, after the close).
No AI needed: prices from Yahoo Finance, headlines from Google News. Paper only - no real orders."""
import os, re, json, hashlib, datetime as dt, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
import yfinance as yf

H = os.path.dirname(os.path.abspath(__file__))
D = lambda f: os.path.join(H, "data", f)
BL = json.load(open(D("buylist.json")))
NEWS = {r[0]: r for r in json.load(open(D("news.json")))}
DEEP = {d["tk"]: d for d in json.load(open(D("deep.json")))}
st = json.load(open(D("price_alert_state.json"))) if os.path.exists(D("price_alert_state.json")) else {}
seen = json.load(open(D("news_seen.json"))) if os.path.exists(D("news_seen.json")) else {}
book = json.load(open(D("leo_paper.json")))
now = dt.datetime.now(dt.timezone.utc); today = now.date()
EVENING = now.hour >= 21  # after-close run (21:30 UTC); weekly summary only then
VN = {"G": "GREAT - wait for price", "W": "WATCH", "A": "AVOID"}
NOISE = re.compile(r"stock price, news, quote|seasonal returns|stock traders daily|historical prices|price target|shares (sold|bought|acquired) by|"
                   r"position in|stake in .* by|holdings in|law firm|investor alert|pomerantz|rosen law|levi & korsinsky|halper sadeh|bragar|faruqi|class action (reminder|deadline)|\bmarketbeat\b|insider (buying|selling) .* shares|stock (forum|chart)|why did .* (fall|rise|gain|drop) \d", re.I)
FLAG = re.compile(r"resign|steps? down|ousted|fired|investigat|probe|lawsuit|sued|fraud|\bsec\b|subpoena|cuts? (guidance|forecast|outlook)|"
                  r"(lowers|slashes|withdraws) (guidance|forecast|outlook)|profit warning|downgrad|bankrupt|default|recall|takeover|merger|buyout|"
                  r"to be (acquired|bought)|agrees to (buy|acquire)|dividend (cut|suspend)|suspends|restat|short seller|plunge|tumble|crash|halt|delist|sanction", re.I)


def send(text):
    tok, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print(text); return
    for i in range(0, len(text), 3900):
        data = urllib.parse.urlencode({"chat_id": chat, "text": text[i:i + 3900], "disable_web_page_preview": "true"}).encode()
        print("telegram:", urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data, timeout=30).status)


def quote(t):
    fi = yf.Ticker(t).fast_info
    return float(fi["lastPrice"]), float(fi.get("previousClose") or fi["lastPrice"])


def headlines(q):
    url = "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": f'"{q}" when:2d', "hl": "en-CA", "gl": "CA", "ceid": "CA:en"})
    try:
        x = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=20).read()
        out = []
        for it in ET.fromstring(x).iter("item"):
            title = it.findtext("title") or ""; src = it.find("source")
            if NOISE.search(title):
                continue
            try:
                when = parsedate_to_datetime(it.findtext("pubDate")).astimezone(dt.timezone.utc)
            except Exception:
                when = now
            out.append(dict(title=re.sub(r"\s+-\s+[^-]+$", "", title), source=src.text if src is not None else "", link=it.findtext("link") or "",
                            when=when.strftime("%Y-%m-%d %H:%M"), ts=when.timestamp(), flag=bool(FLAG.search(title))))
        out.sort(key=lambda h: -h["ts"])
        return out[:8]
    except Exception as e:
        print("news fail", q, e); return []


fx = quote("USDCAD=X")[0]
held = {p["tk"] for p in book["pos"]}
watch, alerts, moves, news_alerts, news_out = [], [], [], [], {}
for s in BL:
    try:
        px, prev = quote(s["tk"])
    except Exception as e:
        print("price fail", s["tk"], e); continue
    chg = px / prev - 1 if prev else 0
    # big price move
    lim = 0.03 if s["tk"] in held else 0.05
    if abs(chg) >= lim and st.get("mv_" + s["tk"]) != str(today):
        moves.append(f"{'📈' if chg > 0 else '📉'} {s['name']} ({s['tk']}) {chg:+.1%} today → {s['cur']}{px:,.2f}"); st["mv_" + s["tk"]] = str(today)
    # buy-price alert (+ paper buy)
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
    # news
    hs = headlines(s.get("q", s["name"]))
    news_out[s["tk"]] = hs
    for h in hs:
        hid = hashlib.md5(h["title"].encode()).hexdigest()[:12]
        if hid in seen.get(s["tk"], []):
            continue
        seen.setdefault(s["tk"], []).append(hid)
        if (s["tk"] in held or h["flag"]) and now.timestamp() - h["ts"] < 36 * 3600:
            news_alerts.append(f"{'🔴' if h['flag'] else '📰'} {s['name']}: {h['title']} ({h['source']})")
    seen[s["tk"]] = seen.get(s["tk"], [])[-60:]
    n = NEWS.get(s["tk"]); dp = DEEP.get(s["tk"])
    verdict = dp["verdict"] if dp else ("BUY" if s["tk"] == "FFH.TO" else VN.get(n[1], "") if n else "")
    watch.append(dict(tk=s["tk"], name=dp["name"] if dp else s["name"], cur=s["cur"], px=round(px, 2), chg=round(chg, 4), buy=round(trigger, 2),
                      gap=round(trigger / px - 1, 4), verdict=verdict, what=n[2] if n else "", story=n[3] if n else s.get("why", ""),
                      src=n[4] if n else "", deep=dp, news=hs[:5]))

if alerts:
    send("🛒 LEO CAPITAL - BUY-PRICE ALERT\n\n" + "\n\n".join(alerts) + "\n\n👉 Ask Hermes for a fresh news check before any real buy.")
first_run = not os.path.exists(D("news_seen.json"))
if (moves or news_alerts) and not first_run:
    parts = []
    if moves:
        parts.append("BIG MOVES\n" + "\n".join(moves))
    if news_alerts:
        parts.append("NEWS (🔴 = important: CEO, lawsuit, deal, guidance, earnings…)\n" + "\n".join(news_alerts[:15]))
    send(f"🗞️ LEO CAPITAL - {'after-close' if EVENING else ('morning' if now.hour < 15 else 'intraday')} check\n\n" + "\n\n".join(parts) +
         "\n\n👉 Big news on something you care about? Ask Hermes: \"news check <company>\".")

val = book["cash_cad"]; pos = []
for p in book["pos"]:
    try:
        px = quote(p["tk"])[0]
    except Exception:
        px = p["px"]
    v = p["units"] * px * (fx * 0.985 if p["cur"] == "USD" else 1)
    val += v
    pos.append(dict(tk=p["tk"], cost=p["cost_cad"], value=round(v, 2), pct=round(v / p["cost_cad"] - 1, 4), px=round(px, 2), buy_px=p["px"], cur=p["cur"], date=p["date"]))
bench = book["bench"]["units"] * quote(book["bench"]["tk"])[0]
port = dict(start=book["start"], start_cash=book["start_cash_cad"], value=round(val, 2), pct=round(val / book["start_cash_cad"] - 1, 4),
            bench=round(bench, 2), bench_pct=round(bench / book["start_cash_cad"] - 1, 4), cash=round(book["cash_cad"], 2), pos=pos, log=book["log"])
if today.weekday() == 0 and EVENING:
    send(f"📊 LEO CAPITAL weekly: paper C${val:,.0f} ({port['pct']:+.1%}) vs XEQT C${bench:,.0f} ({port['bench_pct']:+.1%}). Tap 📊 Leo Capital for details.")

# crash meter: buy-the-dip rules (written in advance): -20% -> put C$5k into the index, -30% -> another C$5k
crash = {}
for t in ("SPY", "XEQT.TO", "QQQ"):
    try:
        h = yf.Ticker(t).history(period="1y")["Close"]
        crash[t] = round(float(h.iloc[-1] / h.max() - 1), 4)
    except Exception as e:
        print("crash fail", t, e)
dd = crash.get("SPY", 0)
for lvl, txt in ((-0.30, "−30%"), (-0.20, "−20%")):
    if dd <= lvl and not st.get(f"crash{txt}"):
        st[f"crash{txt}"] = str(today)
        send(f"🚨 CRASH RULE TRIGGERED: the US market (S&P 500) is {dd:.0%} from its high (rule: {txt}).\n"
             f"Your plan, written in calm times: move C$5,000 from Savings into your index (TFSA). Buffett: be greedy when others are fearful.\n"
             f"Also check the Leo Capital app - great companies may be at their buy prices.")
    if dd > lvl + 0.10 and st.get(f"crash{txt}"):
        st.pop(f"crash{txt}")  # reset once the market recovers
watch.sort(key=lambda w: (0 if "BUY" in w["verdict"] else 1, -w["gap"]))
json.dump(dict(updated=now.strftime("%Y-%m-%d %H:%M UTC"), usdcad=round(fx, 4), portfolio=port, watch=watch, crash=crash),
          open(os.path.join(H, "docs", "data.json"), "w"), indent=1)
json.dump(st, open(D("price_alert_state.json"), "w"), indent=1)
json.dump(seen, open(D("news_seen.json"), "w"), indent=1)
json.dump(book, open(D("leo_paper.json"), "w"), indent=1)
print("ok", len(watch), "alerts", len(alerts), "moves", len(moves), "news", len(news_alerts), "value", round(val))
