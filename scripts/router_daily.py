#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
router_daily.py — 小胡瓜策略路由器每日打分 (2026-08-08 P1 重建)
========================================================================

被 9am / 14:55 cron 调用, 跑 3 维打分:
  趋势强度 (40%) + 资金面 (35%) + 风险偏好 (25%)
输出 router_9am.json / router_1455.json + 状态 + 仓位权重

设计:
  - 零新依赖 (只 import fallback_pool + 标准库)
  - 任一源失败 → 该维度 0 分兜底 (不抛异常)
  - 输出 schema 跟 cron prompt 对齐, 可直接 push

用法:
  python3 router_daily.py --mode 9am --date 2026-08-08
  python3 router_daily.py --mode 1455 --date 2026-08-08
  # 默认 date=today, mode=9am
"""
import argparse
import json
import os
import sys
from datetime import datetime, date
from pathlib import Path

# 路径 (适配 cron workdir)
SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))
from fallback_pool import FallbackPool  # noqa: E402

# 输出目录
KB_ROOT = Path(os.path.expanduser("~/Documents/股票分析知识库"))
ROUTER_OUT = KB_ROOT / "runtime" / "router"
ROUTER_OUT.mkdir(parents=True, exist_ok=True)

# 仓位硬上限 (v1.81 已沉淀)
CAP_LIMIT = {
    "🟢 主升": 100,
    "🟡 震荡": 90,
    "⚪ 弱震荡": 90,
    "🔴 下跌": 80,
}

# 状态判定阈值
THRESHOLDS = [
    (60,  "🟢 主升"),
    (20,  "🟡 震荡"),
    (-20, "⚪ 弱震荡"),
    (None, "🔴 下跌"),
]


def _score_trend(pool: FallbackPool) -> dict:
    """指标① 趋势强度 (权重 40%): 上证 vs 5日均线 + 斜率"""
    try:
        kline = pool.get_kline("sh000001", scale=240, datalen=10)
        if not kline or len(kline) < 6:
            return {"score": 0, "note": "K线不足, 0 分兜底"}
        # 上证现价 (kline 最新条 close)
        last = kline[-1]
        close = float(last.get("close", 0))
        # 5 日均线 = 最近 5 条 close 均值
        closes = [float(k.get("close", 0)) for k in kline[-5:]]
        ma5 = sum(closes) / len(closes) if closes else close
        # 5日斜率 (首尾差)
        slope = closes[-1] - closes[0] if len(closes) >= 2 else 0

        if close > ma5 and slope > 0:
            score, note = 40, f"站上 MA5 + 斜率向上 +40 ({close:.2f} > {ma5:.2f}, slope={slope:.2f})"
        elif close > ma5 and slope == 0:
            score, note = 20, f"站上 MA5 + 走平 +20 ({close:.2f} > {ma5:.2f})"
        elif close > ma5 and slope < 0:
            score, note = 0, f"站上 MA5 + 向下 0 ({close:.2f} > {ma5:.2f}, slope={slope:.2f})"
        else:
            score, note = -40, f"跌破 MA5 -40 ({close:.2f} < {ma5:.2f})"
        return {"score": score, "note": note}
    except Exception as e:
        return {"score": 0, "note": f"趋势异常 {type(e).__name__}, 0 分兜底"}


def _score_capital(pool: FallbackPool) -> dict:
    """指标② 资金面 (权重 35%): 北向资金 + 主力"""
    try:
        hsgt = pool.get_hsgt()
        if not hsgt:
            return {"score": 0, "note": "北向资金获取失败, 0 分兜底"}
        total = float(hsgt.get("total", 0))  # 单位: 亿
        # >50亿 +35, 0~50 +15, -50~0 -15, <-50 -35
        if total > 50:
            score, note = 35, f"北向净买 >50亿 +35 ({total:.2f}亿)"
        elif total > 0:
            score, note = 15, f"北向净买 0~50亿 +15 ({total:.2f}亿)"
        elif total > -50:
            score, note = -15, f"北向净卖出 0~50亿 -15 ({total:.2f}亿)"
        else:
            score, note = -35, f"北向净卖出 >50亿 -35 ({total:.2f}亿)"
        return {"score": score, "note": note}
    except Exception as e:
        return {"score": 0, "note": f"资金异常 {type(e).__name__}, 0 分兜底"}


def _score_sentiment(pool: FallbackPool) -> dict:
    """指标③ 风险偏好 (权重 25%): 涨停 vs 跌停家数 + 连板"""
    try:
        # 简化: 用涨停跌停家数比值; 不依赖单独接口
        # 真实现需要 akshare / 通达信接口, 这里走东财实时行情
        url = "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&po=1&np=1&fltt=2&invt=2&fid=f3&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2&fields=f12,f14,f3"
        ok, raw, lat = pool._fetch(url, timeout=12)
        if not ok:
            return {"score": 0, "note": "情绪接口拉取失败, 0 分兜底"}
        d = json.loads(raw)
        rows = d.get("data", {}).get("diff", []) or []
        limit_up = sum(1 for r in rows if (r.get("f3") or 0) >= 9.7)   # 涨幅>=9.7% 视为涨停
        limit_dn = sum(1 for r in rows if (r.get("f3") or 0) <= -9.7)  # 跌幅<=-9.7% 视为跌停

        ratio = limit_up / max(limit_dn, 1)
        if ratio >= 5:
            base_score, note = 25, f"涨停{limit_up}/跌停{limit_dn} (>5x) +25"
        elif ratio >= 2:
            base_score, note = 10, f"涨停{limit_up}/跌停{limit_dn} (2x) +10"
        elif limit_up == limit_dn:
            base_score, note = 0, f"涨停=跌停 ({limit_up}={limit_dn}) 0"
        else:
            base_score, note = -25, f"跌停>涨停 2x ({limit_up}/{limit_dn}) -25"

        return {"score": base_score, "note": note}
    except Exception as e:
        return {"score": 0, "note": f"情绪异常 {type(e).__name__}, 0 分兜底"}


def run_router(mode: str = "9am", target_date: str = None) -> dict:
    """跑一次 router 跑分, 返回 dict 可直接 json.dumps"""
    pool = FallbackPool()
    target_date = target_date or date.today().isoformat()

    trend = _score_trend(pool)
    capital = _score_capital(pool)
    sentiment = _score_sentiment(pool)

    # 加权总分 (按权重)
    total = (
        trend["score"] * 0.40
        + capital["score"] * 0.35
        + sentiment["score"] * 0.25
    )
    total = round(total, 1)

    # 状态判定
    state = "🔴 下跌"
    for thr, s in THRESHOLDS:
        if thr is None or total >= thr:
            state = s
            break

    cap = CAP_LIMIT.get(state, 80)
    weights = {
        "🟢 主升":  "主力30% + 次力20% + 卫星50%",
        "🟡 震荡":  "主力50% + 次力40% + 卫星10%",
        "⚪ 弱震荡": "主力60% + 次力30% + 卫星10%",
        "🔴 下跌":  "主力60% + 次力30% + 卫星0%",
    }.get(state, "主力60% + 次力30% + 卫星0%")

    return {
        "date": target_date,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "trend": trend,
        "capital": capital,
        "sentiment": sentiment,
        "total": total,
        "state": state,
        "weights": weights,
        "cap_limit": f"{cap}%",
        "notes": "router_daily.py v1.0 (2026-08-08 P1 重建)",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["9am", "1455"], default="9am")
    parser.add_argument("--date", default=None, help="YYYY-MM-DD (默认今天)")
    args = parser.parse_args()

    result = run_router(mode=args.mode, target_date=args.date)

    # 落盘
    suffix = "9am" if args.mode == "9am" else "1455"
    out_path = ROUTER_OUT / f"router_{suffix}.json"
    out_path.write_text(json.dumps([result], ensure_ascii=False, indent=2))
    print(f"✅ {out_path} ({out_path.stat().st_size} bytes)")
    print(f"  总分 {result['total']} → {result['state']} | 仓位硬上限 {result['cap_limit']}")
    print(f"  趋势 {result['trend']['score']} | 资金 {result['capital']['score']} | 情绪 {result['sentiment']['score']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())