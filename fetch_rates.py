#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""香港保险公司汇率 —— 云端每日抓取(GitHub Actions 用)
读取同目录 rates.json,抓取 8 家可程序化来源,更新 cur/date,写回。
宏利 Manulife、中国太平(香港) 因反爬/JS 挑战,保留原值(可由本地浏览器流程补录)。
"""
import json, re, subprocess, sys, os, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
RATES = os.path.join(BASE, "rates.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"


def curl(url, data=None, form=None, timeout=45):
    cmd = ["curl", "-s", "-L", "-m", str(timeout), "-A", UA]
    if form is not None:
        cmd += ["-X", "POST"]
        for k, v in form.items():
            cmd += ["--data-urlencode", f"{k}={v}"]
    elif data is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", json.dumps(data)]
    cmd.append(url)
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def fl(x):
    try:
        return float(x)
    except Exception:
        return None


def _iso(s, fmt):
    if not s:
        return ""
    try:
        return datetime.datetime.strptime(s, fmt).date().isoformat()
    except Exception:
        return ""


def get_aia():
    j = json.loads(curl("https://www1.aia.com.hk/CorpWS/Investment/Get/ExchangeRate2"))
    m = {"usd": "USD", "aus": "AUD", "rmb": "CNY", "can": "CAD", "chf": "CHF", "pound": "GBP",
         "peso": "PHP", "mop": "MOP", "nt": "TWD", "sing": "SGD", "nzd": "NZD", "euro": "EUR", "yen": "JPY"}
    out = {m[e["type"]]: fl(e["value"]) for e in j if isinstance(e, dict) and e.get("type") in m}
    dt = next((e["value"] for e in j if isinstance(e, dict) and e.get("type") == "date"), "")
    return out, _iso(dt, "%m/%d/%Y")


def get_prudential():
    h = curl("https://www.prudential.com.hk/scws/pages/en/plan-for-life/investment/exchange-rates-hk/index.html?iframe=1")
    pairs = re.findall(r">\s*(USD|RMB|AUD|CAD|GBP)\s*</td>\s*<td[^>]*>\s*([0-9.]+)\s*</td>", h)
    d = re.search(r"Effective Date\(dd/mm/yyyy\):\s*([0-9/]+)", h)
    return {c: fl(v) for c, v in pairs}, _iso(d.group(1) if d else "", "%d/%m/%Y")


def get_ctf():
    h = curl("https://www.ctflife.com.hk/en/support/exchange-rate")
    pairs = re.findall(r"<td>\s*1\s*([A-Z]{3})\s*</td>\s*<td>\s*([0-9.]+)\s*</td>", h)
    d = re.search(r"(\d{2} [A-Za-z]{3} \d{4})", h)
    return {c: fl(v) for c, v in pairs}, _iso(d.group(1) if d else "", "%d %b %Y")


def get_axa():
    h = curl("https://www.axa.com.hk/en/exchange-rate")
    pairs = re.findall(r"([A-Z]{3})\s*1\s*=\s*HKD\s*([0-9.]+)", h)
    out = {}
    for c, v in pairs:
        out.setdefault(c, fl(v))
    return out, ""


def get_fwd():
    j = json.loads(curl("https://oneweb.fwd.com/api/currency/rate",
                        data={"currencies": ["AUD", "CAD", "EUR", "GBP", "RMB", "SGD", "USD"], "country": "hk"}))
    r = j["result"]
    return {x["fromCurrency"].replace("RMB", "CNY"): fl(x["rate"]) for x in r["rates"]}, r.get("date", "")


def get_sunlife():
    j = json.loads(curl("https://www.sunlife.com.hk/webservice/getmpforsofunddata",
                        data={"domainAndUserName": "hkportal", "sqlKey": "EXCHANGE_RATE",
                              "sqlParams": [datetime.date.today().isoformat()]}))
    out, dt = {}, ""
    for x in j:
        code = {"US": "USD", "CA": "CAD", "GB": "GBP", "AU": "AUD", "CN": "CNY", "CH": "CHF",
                "EU": "EUR", "SP": "SGD", "UA": "AED"}.get(x.get("CURRENCY"), x.get("CURRENCY_EN"))
        out[code] = fl(x.get("BID_RATE"))
        dt = dt or (x.get("PUBLISH_DATE", "") or "")[:10]
    return out, dt


CL_CODES = {"02": "USD", "03": "CNY", "05": "GBP", "06": "EUR", "10": "SGD", "11": "AUD", "12": "CAD", "13": "NZD"}


def get_chinalife():
    out, dt = {}, ""
    for code, cur in CL_CODES.items():
        j = json.loads(curl("https://www.e-chinalife.com.hk/insurance/exratesvc", form={"r": code, "t": "01"}))
        lst = j.get("ExchangeRateList") or []
        t01 = next((x["Rate"] for x in lst if x.get("Type") == "01"), None)
        if t01:
            out[cur] = fl(t01)
        dt = dt or j.get("Date", "")
    return out, dt


def get_yflife():
    j = json.loads(curl("https://www.yflife.com/aisite-applyapi/fund/common/getExchangeRate", data={}))
    m = j["data"]["exchangeRateMap"]
    code = {"US$": "USD", "RMB": "CNY", "EUR": "EUR", "GBP": "GBP", "AUD": "AUD",
            "CAD": "CAD", "SGD": "SGD", "CHF": "CHF", "MOP": "MOP"}
    out = {}
    for k, v in code.items():
        row = m.get(k) or {}
        if "HK$" in row:
            out[v] = fl(row["HK$"])
    return out, (j["data"].get("validUntil", "").replace("/", "-") or "")


SOURCES = [
    ("友邦 AIA", get_aia), ("保诚 Prudential", get_prudential), ("富卫 FWD", get_fwd),
    ("永明 Sun Life", get_sunlife), ("周大福人寿 CTF Life", get_ctf),
    ("中国人寿(海外) China Life", get_chinalife), ("安盛 AXA", get_axa), ("万通 YF Life", get_yflife),
]
MANUAL = ["宏利 Manulife", "中国太平(香港) China Taiping"]


def main():
    book = json.load(open(RATES, encoding="utf-8"))
    changed = 0
    for name, fn in SOURCES:
        try:
            fresh, dt = fn()
        except Exception as e:
            print(f"[ERR ] {name}: {e}"); continue
        if not fresh:
            print(f"[ERR ] {name}: 解析到 0 个币种"); continue
        if "RMB" in fresh and "CNY" not in fresh:
            fresh["CNY"] = fresh.pop("RMB")
        old = book["companies"].get(name, {}).get("cur", {})
        diffs = [f"{c}:{old.get(c)}->{v}" for c, v in fresh.items()
                 if old.get(c) is None or abs(old.get(c) - v) > max(1e-4, abs(old.get(c) or 1) * 2e-5)]
        if dt:
            book["companies"][name]["date"] = dt
        cur = {"HKD": 1}; cur.update({k: v for k, v in fresh.items() if v is not None})
        book["companies"][name]["cur"] = cur
        book["companies"][name]["lastChecked"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        print(f"[OK  ] {name}" + (f"  变化: {'; '.join(diffs)}" if diffs else "  无变化"))
        changed += 1 if diffs else 0
    for m in MANUAL:
        print(f"[SKIP] {m}  (反爬/JS,保留原值)")
    book["generatedAt"] = datetime.date.today().isoformat()
    json.dump(book, open(RATES, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"完成:更新 {len(SOURCES)-0} 家,其中 {changed} 家有变化")


if __name__ == "__main__":
    main()
