#!/usr/bin/env python3
"""
kline_fallback_health.py - K线 Fallback 池独立健康监控

背景 (P0-008, 2026-08-06 沉淀):
  - daily_diagnostic.py 只测实时源 (push2 / qt.gtimg / 新浪 K线)
  - K线 fallback 三源 (新浪 → 腾讯 web.ifzq → 东财 push2his) 健康度无人盯
  - 三源同时挂 = 完全无法回测 = 当日复盘卡死

用法:
  python3 kline_fallback_health.py                   # 默认测 sz300059 日 K 60 根
  python3 kline_fallback_health.py --code sh600519  # 指定标的
  python3 kline_fallback_health.py --json           # JSON 输出
  python3 kline_fallback_health.py --stress 5       # 连测 5 次取最差

退出码:
  0 = 全部 OK
  1 = 1 个源挂
  2 = 2 个源挂 (架构告警)
  3 = 全部挂 (系统瘫痪)
"""
import sys
import os
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

# 复用 fallback_pool.py 里的逻辑 (零新依赖)
sys.path.insert(0, str(Path(__file__).parent))
from fallback_pool import FallbackPool, HealthStatus  # noqa: E402

# 关注的 3 个 K线源 URL (与 fallback_pool.kline_order 对齐)
KLINE_PROBES = [
    ("sina", "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKlineData?symbol={code}&scale=240&datalen=5"),
    ("tencent_kline", "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,5,qfq"),
    ("eastmoney_push2his", "https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={market}{code}&fields1=f1,f2&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1&end=20500101&lmt=5"),
]

MARKET_MAP = {"6": "1", "0": "1", "3": "0", "2": "0"}  # sh6xx/sh5xx=1, sz0xx/sz3xx=0


def probe_one(name: str, url: str, timeout: float = 8.0):
    """单源探测,返回 (ok, latency_s, bytes, err)"""
    pool = FallbackPool()
    ok, raw, lat = pool._fetch(url, timeout=timeout)
    if not ok:
        return False, lat, 0, "fetch_fail"
    if not raw or len(raw) < 50:
        return False, lat, len(raw) if raw else 0, "empty_payload"
    # 简单有效性检查: 不应全是 null/空
    try:
        snippet = raw.decode("utf-8", errors="replace")[:300]
    except Exception:
        snippet = str(raw[:300])
    if snippet.strip() in ("null", "[]", "", "None"):
        return False, lat, len(raw), "null_response"
    return True, lat, len(raw), None


def normalize_code(code: str) -> tuple:
    """code = '300059' → ('sz300059', '0', '0')"""
    code = code.strip().lower().replace("sz", "").replace("sh", "")
    market = MARKET_MAP.get(code[0], "0")
    full = ("sz" if market == "0" else "sh") + code
    return full, market, code


def run_check(code: str = "300059", stress: int = 1):
    full, market, raw_code = normalize_code(code)
    print(f"🩺 K线 Fallback 池健康检查 - {full}")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    results = []
    for round_n in range(stress):
        if stress > 1:
            print(f"\n--- 第 {round_n+1}/{stress} 轮 ---")
        for name, url_tpl in KLINE_PROBES:
            # 东财需要 secid 拼接 market
            if name == "eastmoney_push2his":
                url = url_tpl.format(market=f"{market}.", code=raw_code)
            else:
                url = url_tpl.format(code=full)
            ok, lat, size, err = probe_one(name, url)
            results.append({"src": name, "round": round_n + 1, "ok": ok, "latency_s": lat, "bytes": size, "err": err})
            icon = "✅" if ok else "🔴"
            err_str = f" [{err}]" if err else ""
            print(f"  {icon} {name:<22} http={200 if ok else 0:>3} bytes={size:>5} t={lat:.2f}s{err_str}")
        if round_n < stress - 1:
            time.sleep(2)

    # 汇总
    print("\n" + "=" * 60)
    latest = [r for r in results if r["round"] == stress]
    ok_count = sum(1 for r in latest if r["ok"])
    total = len(latest)
    if ok_count == total:
        verdict = "✅ 全部健康"
        exit_code = 0
    elif ok_count >= 2:
        verdict = "🟡 单点降级 (可降级跑)"
        exit_code = 1
    elif ok_count == 1:
        verdict = "🔴 架构告警 (只剩 1 源)"
        exit_code = 2
    else:
        verdict = "💀 系统瘫痪 (0 源可用)"
        exit_code = 3

    print(f"📊 总结: {ok_count}/{total} OK → {verdict}")
    print(f"🚪 退出码: {exit_code}")

    return {"code": full, "verdict": verdict, "ok": ok_count, "total": total, "results": results}, exit_code


def main():
    ap = argparse.ArgumentParser(description="K线 Fallback 健康监控")
    ap.add_argument("--code", default="300059", help="股票代码 (默认 300059)")
    ap.add_argument("--stress", type=int, default=1, help="连测轮数")
    ap.add_argument("--json", action="store_true", help="仅输出 JSON")
    args = ap.parse_args()

    summary, exit_code = run_check(args.code, args.stress)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
