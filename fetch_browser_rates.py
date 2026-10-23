#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""抓取「宏利」与「中国太平(香港)」的当日汇率并写回 rates.json。

2026-10 修复(数据源被 WAF 拦截):
  * 两家官网对数据/机房 IP 做了 WAF(宏利 Akamai 403、太平 JS 挑战空页),
    Playwright 也无法绕过 -> 改用 r.jina.ai 服务端渲染代理取文本(可跨 IP 封锁);
  * 仍保留 Playwright 浏览器直连作为兜底(本机/白名单网络可用时);
  * 严格模式:抓取/解析失败、必填币种缺失、生效日超过 MAX_AGE_DAYS -> 退出码 1。
"""
import datetime
import json
import os
import re
import subprocess
import sys
import time

RATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rates.json")
MAX_AGE_DAYS = 3            # 周末/假期沿用上一交易日;超过 3 个自然日视为过期
RETRIES = 3

CNY_MAP = {"美元": "USD", "人民幣": "CNY", "人民币": "CNY", "加元": "CAD", "英鎊": "GBP",
           "英镑": "GBP", "澳元": "AUD", "新加坡元": "SGD", "歐元": "EUR", "欧元": "EUR",
           "瑞士法郎": "CHF", "港幣": "HKD", "港元": "HKD"}

# 各官网实际公布的币种(太平官网只公布美元/人民币)
REQUIRED = {
    "宏利 Manulife": {"USD", "CNY", "CAD", "GBP", "AUD", "SGD"},
    "中国太平(香港) China Taiping": {"USD", "CNY"},
}


def fetch_via_jina(url, timeout=90):
    """通过 r.jina.ai 渲染代理取页面文本(可绕过 WAF/机房 IP 封锁)。"""
    cmd = ["curl", "-s", "-L", "-m", str(timeout),
           "-H", "X-Return-Format: text",
           "https://r.jina.ai/" + url]
    out = subprocess.run(cmd, capture_output=True, text=True).stdout or ""
    if len(out) < 500:
        raise RuntimeError("Jina 返回内容过短 (%d 字节)" % len(out))
    return out


def fetch_via_browser(url, attempts=2):
    """兜底:Playwright 真实浏览器直连(本机或未被封的网络)。"""
    from playwright.sync_api import sync_playwright

    last_err = None
    for i in range(attempts):
        try:
            with sync_playwright() as p:
                launch_kwargs = dict(headless=True, args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ])
                if os.environ.get("PW_CHANNEL"):
                    launch_kwargs["channel"] = os.environ["PW_CHANNEL"]
                if os.environ.get("PW_PROXY"):
                    launch_kwargs["proxy"] = {"server": os.environ["PW_PROXY"]}
                browser = p.chromium.launch(**launch_kwargs)
                ctx = browser.new_context(
                    user_agent=("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                "AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/131.0.0.0 Safari/537.36"),
                    locale="zh-HK",
                    viewport={"width": 1440, "height": 900},
                )
                page = ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(6000)
                text = page.evaluate("() => document.body ? document.body.innerText : ''")
                browser.close()
                if len(text) < 200:
                    raise RuntimeError("页面正文过短 (%d 字节)" % len(text))
                return text
        except Exception as exc:
            last_err = exc
            time.sleep(2 + i * 2)
    raise RuntimeError("浏览器直连失败: %s" % last_err)


def parse_date(text):
    """尽量从页面文本里提取生效/更新日期;没有则返回空串。"""
    m = re.search(r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
    m = re.search(r"(\d{1,2})/(\d{1,2})/(20\d{2})", text)
    if m:
        return "%s-%02d-%02d" % (m.group(3), int(m.group(2)), int(m.group(1)))
    return ""


def parse_manulife(text):
    out = {}
    for zh, code in CNY_MAP.items():
        m = re.search(r"1\s*" + zh + r"\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*港元", text)
        if m and code not in out:
            out[code] = float(m.group(1))
    m = re.search(r"1\s*港元\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*澳門", text)
    if m:
        try:
            out["MOP"] = round(1.0 / float(m.group(1)), 4)
        except Exception:
            pass
    out.pop("HKD", None)
    return out


def parse_taiping(text):
    out = {}
    for zh, code in CNY_MAP.items():
        m = re.search(r"1\s*" + zh + r"\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*港幣", text)
        if m and code not in out:
            out[code] = float(m.group(1))
    out.pop("HKD", None)
    return out


TARGETS = [
    ("宏利 Manulife",
     "https://www.manulife.com.hk/zh-hk/individual/services/premium-exchange-rate.html",
     parse_manulife),
    ("中国太平(香港) China Taiping", "https://tplhk.cntaiping.com/customer-dhl/", parse_taiping),
]


def main():
    with open(RATES, encoding="utf-8") as fh:
        book = json.load(fh)
    today = datetime.date.today()
    failures = []

    for name, url, parser in TARGETS:
        text = ""
        channel = ""
        try:
            text = fetch_via_jina(url)
            channel = "jina-reader"
        except Exception as exc:
            print("[WARN] %s: Jina 通道失败(%s),尝试浏览器直连" % (name, exc))
            try:
                text = fetch_via_browser(url)
                channel = "browser"
            except Exception as exc2:
                failures.append("%s: 抓取失败 %s" % (name, exc2))
                print("[ERR ] %s: %s" % (name, exc2))
                continue

        rates = parser(text)
        if not rates:
            failures.append("%s: 未解析到任何汇率" % name)
            print("[ERR ] %s: 未解析到任何汇率" % name)
            continue

        missing = REQUIRED.get(name, set()) - set(rates)
        if missing:
            failures.append("%s: 缺少币种 %s" % (name, ",".join(sorted(missing))))
            print("[ERR ] %s: 缺少币种 %s" % (name, ",".join(sorted(missing))))
            continue

        page_date = parse_date(text)
        effective = page_date or today.isoformat()
        age = (today - datetime.date.fromisoformat(effective)).days
        if age > MAX_AGE_DAYS:
            failures.append("%s: 生效日 %s 已过期 %d 天" % (name, effective, age))
            print("[ERR ] %s: 生效日 %s 已过期 %d 天" % (name, effective, age))
            continue

        row = book["companies"][name]
        row["cur"].update({k: v for k, v in rates.items() if v is not None})
        row["cur"]["HKD"] = 1
        row["date"] = effective
        row["dateSource"] = "official_effective" if page_date else "scraped"
        row["lastChecked"] = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
        row["source"] = url
        row["fetchChannel"] = channel
        print("[OK  ] %s: %d 币种, date=%s (%s, %s)" %
              (name, len(rates), effective, row["dateSource"], channel))

    book["generatedAt"] = today.isoformat()
    with open(RATES, "w", encoding="utf-8") as fh:
        json.dump(book, fh, ensure_ascii=False, indent=2)

    if failures:
        print("[FAIL] " + "; ".join(failures))
        sys.exit(1)
    print("两家手工来源处理完成")


if __name__ == "__main__":
    main()
