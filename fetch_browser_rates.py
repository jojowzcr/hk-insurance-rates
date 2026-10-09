#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端用无头浏览器抓取「宏利」与「中国太平(香港)」的当日汇率并写回 rates.json。
用于 GitHub Actions(需先 pip install playwright 并安装 chromium)。
失败时不抛错(保留原值),以免影响整体流程。
"""
import json, os, re, datetime, sys

RATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rates.json")

CNY_MAP = {"美元": "USD", "人民幣": "CNY", "人民币": "CNY", "加元": "CAD", "英鎊": "GBP",
           "英镑": "GBP", "澳元": "AUD", "新加坡元": "SGD", "歐元": "EUR", "欧元": "EUR",
           "瑞士法郎": "CHF", "港幣": "HKD", "港元": "HKD"}


def grab(url, wait_ms=6000, timeout=60000):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--no-sandbox"])
        pg = b.new_page(user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"))
        pg.goto(url, wait_until="domcontentloaded", timeout=timeout)
        pg.wait_for_timeout(wait_ms)
        try:
            pg.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        txt = pg.evaluate("() => document.body ? document.body.innerText : ''")
        tables = pg.evaluate("() => [...document.querySelectorAll('table')].map(t=>t.innerText).join('\\n')")
        b.close()
        return (tables or "") + "\n" + (txt or "")


def parse_manulife(txt):
    out = {}
    for zh, code in CNY_MAP.items():
        m = re.search(r"1\s*" + zh + r"\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*港元", txt)
        if m and code not in out:
            out[code] = float(m.group(1))
    m = re.search(r"1\s*港元\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*澳門", txt)
    if m:
        try:
            out["MOP"] = round(1.0 / float(m.group(1)), 4)
        except Exception:
            pass
    out.pop("HKD", None)
    return out


def parse_taiping(txt):
    out = {}
    for zh, code in CNY_MAP.items():
        m = re.search(r"1\s*" + zh + r"\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*港幣", txt)
        if m and code not in out:
            out[code] = float(m.group(1))
    out.pop("HKD", None)
    return out


TARGETS = [
    ("宏利 Manulife", "https://www.manulife.com.hk/zh-hk/individual/services/premium-exchange-rate.html", parse_manulife),
    ("中国太平(香港) China Taiping", "https://tplhk.cntaiping.com/customer-dhl/", parse_taiping),
]


def main():
    book = json.load(open(RATES, encoding="utf-8"))
    for name, url, parser in TARGETS:
        try:
            txt = grab(url)
            rates = parser(txt)
        except Exception as e:
            print(f"[ERR ] {name}: {e}")
            continue
        if not rates:
            print(f"[WARN] {name}: 未解析到汇率(站点可能改版/被拦),保留原值")
            continue
        cur = book["companies"][name]["cur"]
        for k, v in rates.items():
            cur[k] = v
        cur["HKD"] = 1
        book["companies"][name]["date"] = datetime.date.today().isoformat()
        book["companies"][name]["lastChecked"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        print(f"[OK  ] {name}: {rates}")
    json.dump(book, open(RATES, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("浏览器来源处理完成")


if __name__ == "__main__":
    main()
