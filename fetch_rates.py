#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""香港保险公司汇率 -- 云端每日抓取(GitHub Actions 用)
读取同目录 rates.json,抓取 8 家可程序化来源,更新 cur/date,写回。
宏利 Manulife、中国太平(香港) 由 fetch_browser_rates.py(Playwright)补抓。
2026-10 修复:AXA 生效日解析、全量时效校验、校验失败 exit 1(不再静默)。
"""
import json, re, subprocess, sys, os, datetime

BASE = os.path.dirname(os.path.abspath(__file__))
RATES = os.path.join(BASE, "rates.json")
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124 Safari/537.36"
MAX_AGE_DAYS = 3


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
    # 2026-10 修复:解析页面生效日,避免 date 与 lastChecked 矛盾
    dt = ""
    m = re.search(r"(20\d{2}-\d{2}-\d{2})", h)
    if m:
        dt = m.group(1)
    else:
        m = re.search(r"(\d{1,2})\s+([A-Za-z]{3})\s+(20\d{2})", h)
        if m:
            dt = _iso("%s %s %s" % (m.group(1), m.group(2), m.group(3)), "%d %b %Y")
    return out, dt


def get_fwd():
    j = json.loads(curl("https://oneweb.fwd.com/api/currency/rate",
                        data={"currencies": ["AUD", "CAD", "EUR", "GBP", "RMB", "SGD", "USD"], "country": "hk"}))
    r = j["result"]
    return {x["fromCurrency"].replace("RMB", "CNY"): fl(x["rate"]) for x in r["rates"]}, r.get("date", "")


def get_sunlife():
    j = json.loads(curl("https://www.sunlife.com.hk/webservice/getmpforsofunddata",
                        data={"domainAndUserName": "hkportal", "sqlKey": "EXCHANGE_RATE",
                              "sqlParams": [datetime.date.today().isoformat()]}))
    out, dates = {}, []
    for x in j:
        code = {"US": "USD", "CA": "CAD", "GB": "GBP", "AU": "AUD", "CN": "CNY", "CH": "CHF",
                "EU": "EUR", "SP": "SGD", "UA": "AED"}.get(x.get("CURRENCY"), x.get("CURRENCY_EN"))
        out[code] = fl(x.get("BID_RATE"))
        pub = (x.get("PUBLISH_DATE", "") or "")[:10]
        if pub:
            dates.append(pub)
    return out, (max(dates) if dates else "")


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
    vu = j["data"].get("validUntil", "") or ""
    try:
        dt = datetime.datetime.strptime(vu, "%m/%d/%Y").date().isoformat()
    except Exception:
        dt = ""
    return out, dt


SOURCES = [
    ("友邦 AIA", get_aia), ("保诚 Prudential", get_prudential), ("富卫 FWD", get_fwd),
    ("永明 Sun Life", get_sunlife), ("周大福人寿 CTF Life", get_ctf),
    ("中国人寿(海外) China Life", get_chinalife), ("安盛 AXA", get_axa), ("万通 YF Life", get_yflife),
]
MANUAL = ["宏利 Manulife", "中国太平(香港) China Taiping"]


def validate(book, max_age_days=MAX_AGE_DAYS, names=None):
    """全量校验:每家公司日期可解析、未过期、币种数量合理。返回问题列表。"""
    today = datetime.date.today()
    problems = []
    scope = names or list(book.get("companies", {}).keys())
    for name in scope:
        row = (book.get("companies", {}) or {}).get(name) or {}
        row = row or {}
        dt = row.get("date") or ""
        try:
            age = (today - datetime.date.fromisoformat(dt)).days
        except Exception:
            problems.append("%s: 日期无效(%s)" % (name, dt))
            continue
        if age > max_age_days:
            problems.append("%s: date=%s 已 %d 天" % (name, dt, age))
        if len(row.get("cur") or {}) < 2:
            problems.append("%s: 币种不足" % name)
    return problems


def main():
    book = json.load(open(RATES, encoding="utf-8"))
    changed = 0
    for name, fn in SOURCES:
        try:
            fresh, dt = fn()
        except Exception as e:
            print(f"[ERR ] {name}: {e}")
            continue
        if not fresh:
            print(f"[ERR ] {name}: 解析到 0 个币种")
            continue
        if "RMB" in fresh and "CNY" not in fresh:
            fresh["CNY"] = fresh.pop("RMB")
        old = book["companies"].get(name, {}).get("cur", {})
        diffs = [f"{c}:{old.get(c)}->{v}" for c, v in fresh.items()
                 if old.get(c) is None or abs(old.get(c) - v) > max(1e-4, abs(old.get(c) or 1) * 2e-5)]
        row = book["companies"][name]
        if dt:
            row["date"] = dt
            row["dateSource"] = "official_effective"
        else:
            row["date"] = datetime.date.today().isoformat()
            row["dateSource"] = "scraped"
        cur = {"HKD": 1}
        cur.update({k: v for k, v in fresh.items() if v is not None})
        row["cur"] = cur
        row["lastChecked"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        print(f"[OK  ] {name}" + (f"  变化: {'; '.join(diffs)}" if diffs else "  无变化"))
        changed += 1 if diffs else 0
    for m in MANUAL:
        print(f"[SKIP] {m}  (浏览器来源脚本补齐)")

    book["generatedAt"] = datetime.date.today().isoformat()
    problems = validate(book, names=[n for n, _ in SOURCES])
    json.dump(book, open(RATES, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"完成:更新 {len(SOURCES)} 家,其中 {changed} 家有变化")
    if problems:
        print("[FAIL] 数据校验未通过: " + "; ".join(problems))
        sys.exit(1)


if __name__ == "__main__":
    main()
