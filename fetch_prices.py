#!/usr/bin/env python3
"""Hydra price fetcher.

Runs inside GitHub Actions (see .github/workflows/update-prices.yml).
Reads symbols.txt and writes data/prices.json, which index.html reads.

Each symbol is tried against these sources in order, stopping at the
first that answers:

  1. Yahoo Finance through the yfinance library  (price + 1 year of history)
  2. Yahoo Finance chart service, called directly (price + 1 year of history)
  3. Stooq          — stocks, ETFs, futures       (price only, no key)
  4. Finnhub        — US stocks and ETFs          (price only, needs the
                      free FINNHUB_API_KEY repository secret)

A price-only backup keeps the history already saved for that symbol, so
indicators keep working. A symbol that fails everywhere keeps its previous
entry, marked "stale", so the site shows that the number is old.
"""
import csv
import datetime as dt
import io
import json
import math
import os
import pathlib
import sys
import time
import urllib.parse
import urllib.request

try:
    import yfinance as yf
except Exception as e:  # the backups below still work without it
    yf = None
    print(f"yfinance unavailable ({e}); using backup sources only")

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "data" / "prices.json"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"}


def read_symbols():
    symbols, with_options = [], []
    for line in (ROOT / "symbols.txt").read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        parts = line.split()
        if parts[0] not in symbols:
            symbols.append(parts[0])
        if len(parts) > 1 and parts[1].lower() == "options":
            with_options.append(parts[0])
    return symbols, with_options


def num(x, digits=8):
    """Float rounded to significant digits, or None for NaN/inf/garbage."""
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return float(f"{x:.{digits}g}")


def http_get(url, timeout=15):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def full_entry(dates, closes, price, now, src):
    pairs = [(d, c) for d, c in zip(dates, closes) if c is not None and c > 0]
    if not pairs:
        raise ValueError("no usable closes")
    dates, closes = [p[0] for p in pairs], [p[1] for p in pairs]
    return {
        "price": price if price and price > 0 else closes[-1],
        "prev": closes[-2] if len(closes) > 1 else None,
        "time": now,
        "asof": dates[-1],
        "src": src,
        "dates": dates,
        "closes": closes,
    }


# ---- source 1: yfinance -------------------------------------------------
def from_yfinance(sym, now):
    if yf is None:
        raise ValueError("yfinance not installed")
    ticker = yf.Ticker(sym)
    df = ticker.history(period="1y", interval="1d", auto_adjust=True)
    df = df.dropna(subset=["Close"])
    if df.empty:
        raise ValueError("no data returned")
    last = None
    try:  # a fresher last trade when Yahoo offers one
        last = num(ticker.fast_info["lastPrice"])
    except Exception:
        pass
    return full_entry([d.strftime("%Y-%m-%d") for d in df.index],
                      [num(c) for c in df["Close"].tolist()], last, now, "Yahoo Finance")


# ---- source 2: Yahoo chart service, no library --------------------------
def from_yahoo_chart(sym, now):
    url = ("https://query2.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(sym) + "?range=1y&interval=1d")
    res = json.loads(http_get(url))["chart"]["result"][0]
    stamps = res["timestamp"]
    closes = res["indicators"]["quote"][0]["close"]
    dates = [dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime("%Y-%m-%d") for t in stamps]
    e = full_entry(dates, [num(c) for c in closes],
                   num(res.get("meta", {}).get("regularMarketPrice")), now,
                   "Yahoo Finance (direct, backup)")
    e["backup"] = True
    return e


# ---- source 3: Stooq (price only) ---------------------------------------
def stooq_symbol(sym):
    s = sym.lower()
    if s.endswith("=f"):
        return s[:-2] + ".f"          # SI=F -> si.f
    if any(ch in s for ch in "=-^"):
        return None                   # currencies, crypto, indices: not handled here
    return s.replace(".", "-") + ".us"  # TSLA -> tsla.us


def from_stooq(sym, now):
    s = stooq_symbol(sym)
    if not s:
        raise ValueError("stocks, ETFs and futures only")
    text = http_get(f"https://stooq.com/q/l/?s={s}&f=sd2t2ohlcv&h&e=csv")
    rows = list(csv.DictReader(io.StringIO(text)))
    close = num(rows[0].get("Close")) if rows else None
    if not close or close <= 0:
        raise ValueError("no quote")
    date = (rows[0].get("Date") or "").strip()
    return {"price": close, "time": now, "src": "Stooq (backup)", "backup": True,
            "asof": date if len(date) == 10 else None}


# ---- source 4: Finnhub (price only, free key) ---------------------------
def from_finnhub(sym, now):
    key = os.environ.get("FINNHUB_API_KEY", "").strip()
    if not key:
        raise ValueError("no FINNHUB_API_KEY secret")
    if any(ch in sym for ch in "=-^"):
        raise ValueError("stocks and ETFs only")
    try:
        text = http_get("https://finnhub.io/api/v1/quote?symbol="
                        + urllib.parse.quote(sym) + "&token=" + urllib.parse.quote(key))
    except Exception as e:           # never let the key leak into the log
        raise ValueError(type(e).__name__) from None
    j = json.loads(text)
    price = num(j.get("c"))
    if not price or price <= 0:
        raise ValueError("no quote")
    asof = None
    if j.get("t"):
        asof = dt.datetime.fromtimestamp(j["t"], dt.timezone.utc).strftime("%Y-%m-%d")
    return {"price": price, "prev": num(j.get("pc")), "time": now,
            "src": "Finnhub (backup)", "backup": True, "asof": asof}


def finnhub_status(now):
    """One test quote per run, so the site can show whether the key works."""
    if not os.environ.get("FINNHUB_API_KEY", "").strip():
        return "missing"
    try:
        from_finnhub("AAPL", now)
        return "ok"
    except Exception as e:
        return f"key not accepted or Finnhub unreachable ({e})"


SOURCES = [("yfinance", from_yfinance), ("yahoo-chart", from_yahoo_chart),
           ("stooq", from_stooq), ("finnhub", from_finnhub)]


def merge_price_only(old, new, today):
    """Attach a price-only quote to the history we already hold."""
    e = dict(new)
    dates, closes = list((old or {}).get("dates") or []), list((old or {}).get("closes") or [])
    if closes and len(dates) == len(closes):
        day = e.get("asof") or today
        if day > dates[-1]:
            dates.append(day)
            closes.append(e["price"])
        else:
            closes[-1] = e["price"]
        e["dates"], e["closes"] = dates[-260:], closes[-260:]
        if e.get("prev") is None and len(closes) > 1:
            e["prev"] = closes[-2]
    e["asof"] = e.get("asof") or today
    return e


def fetch_symbol(sym, now, old):
    errors = []
    for name, fn in SOURCES:
        try:
            e = fn(sym, now)
            if "closes" not in e:
                e = merge_price_only(old, e, now[:10])
            return e, errors
        except Exception as ex:
            errors.append(f"{name}: {ex}")
    raise ValueError("; ".join(errors))


def fetch_options(sym, spot):
    if yf is None:
        raise ValueError("yfinance not installed")
    ticker = yf.Ticker(sym)
    expiries = list(ticker.options or [])
    if not expiries:
        raise ValueError("no option expiries listed")
    today = dt.date.today()
    expiry = next(
        (e for e in expiries if (dt.date.fromisoformat(e) - today).days >= 21),
        expiries[-1],
    )
    chain = ticker.option_chain(expiry)

    def near_money(df):
        df = df.assign(_d=(df["strike"] - spot).abs()).sort_values("_d").head(7)
        rows = []
        for r in df.sort_values("strike").itertuples():
            rows.append({
                "strike": num(r.strike),
                "last": num(r.lastPrice),
                "bid": num(r.bid),
                "ask": num(r.ask),
                "iv": num(r.impliedVolatility, 4),
                "oi": num(getattr(r, "openInterest", None)),
            })
        return rows

    return {
        "expiry": expiry,
        "underlying": spot,
        "calls": near_money(chain.calls),
        "puts": near_money(chain.puts),
    }


def main():
    symbols, with_options = read_symbols()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        old = json.loads(OUT.read_text())
    except Exception:
        old = {}
    old_syms, old_opts = old.get("symbols", {}), old.get("options", {})

    out_syms, out_opts, failed = {}, {}, []
    for sym in symbols:
        try:
            out_syms[sym], errors = fetch_symbol(sym, now, old_syms.get(sym))
            via = out_syms[sym]["src"]
            print(f"ok    {sym:10s} {out_syms[sym]['price']}  via {via}"
                  + (f"   (after: {'; '.join(errors)})" if errors else ""))
        except Exception as e:  # keep going; one bad symbol must not stop the rest
            failed.append(sym)
            print(f"FAIL  {sym:10s} {e}")
            if sym in old_syms:
                out_syms[sym] = dict(old_syms[sym], stale=True)
        time.sleep(0.4)

    for sym in with_options:
        spot = (out_syms.get(sym) or {}).get("price")
        try:
            if not spot:
                raise ValueError("no underlying price")
            out_opts[sym] = fetch_options(sym, spot)
            print(f"ok    {sym:10s} options {out_opts[sym]['expiry']}")
        except Exception as e:
            print(f"FAIL  {sym:10s} options: {e}")
            if sym in old_opts:
                out_opts[sym] = old_opts[sym]

    if not any(not v.get("stale") for v in out_syms.values()):
        print("Nothing fetched successfully; leaving data/prices.json untouched.")
        return 1

    finnhub = finnhub_status(now)
    print(f"Finnhub backup key: {finnhub}")
    on_backup = [s for s, v in out_syms.items() if v.get("backup") and not v.get("stale")]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(
        {"updated": now, "source": "Yahoo Finance, with Stooq and Finnhub as backups",
         "finnhub": finnhub, "failed": failed, "symbols": out_syms, "options": out_opts},
        separators=(",", ":"),
    ))
    print(f"Wrote {OUT} — {len(out_syms)} symbols, {len(on_backup)} on a backup source "
          f"{on_backup}, {len(failed)} failed {failed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
