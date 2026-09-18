#!/usr/bin/env python3
"""
emit_unified_signals.py — 股票系统 → 六爻统一信号协议（桥接层）

作用
----
读取蜘蛛网最新 picks，转换成统一的 `liuyao.signal.v1` 信号并落盘。
让 A 股信号与加密信号进入**同一套契约**，下游（回测/监控/看板）可以统一消费。

为什么要桥接而不是改源脚本
--------------------------
蜘蛛网4.3 是生产选股脚本（800+ 行，含回测/黑名单/板块逻辑）。桥接层是
**只读 → 转换 → 落盘**，不动生产逻辑，零风险，且幂等可重跑。

用法
----
    python3 emit_unified_signals.py                # 处理最新一份 picks
    python3 emit_unified_signals.py --file <path>  # 指定文件
"""

import argparse
import glob
import json
import os
import sys

# 统一协议模块（六爻引擎侧，单一来源）
WORKSPACE = "/Users/huyufeng/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

try:
    import liuyao_signal as ls
except ImportError as e:
    print(f"❌ 无法导入 liuyao_signal（{e}）")
    print(f"   期望位置: {WORKSPACE}/liuyao_signal.py")
    sys.exit(1)

KB = "/Users/huyufeng/Documents/股票分析知识库"
PICKS_DIR = os.path.join(KB, "runtime", "picks")
OUT_DIR = os.path.join(KB, "runtime", "signals")


def latest_picks():
    files = sorted(glob.glob(os.path.join(PICKS_DIR, "蜘蛛网v4.3_*.json")))
    return files[-1] if files else None


def convert(path, dry_run=False):
    with open(path) as f:
        data = json.load(f)

    stocks = data.get("stocks") or []
    src_ts = data.get("time")

    signals = []
    for pick in stocks:
        sig = ls.from_pick(pick, horizon="1D")
        sig["meta"]["source_file"] = os.path.basename(path)
        sig["meta"]["source_time"] = src_ts
        errs = ls.validate(sig)
        if errs:
            print(f"  ⚠️ {sig.get('symbol')} 校验失败: {errs}")
            continue
        signals.append(sig)

    return signals, src_ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="指定 picks 文件（默认取最新）")
    ap.add_argument("--dry-run", action="store_true", help="只打印，不落盘")
    args = ap.parse_args()

    path = args.file or latest_picks()
    if not path or not os.path.exists(path):
        print("❌ 找不到 picks 文件")
        return 1

    print(f"📥 读取: {os.path.basename(path)}")
    signals, src_ts = convert(path, args.dry_run)
    if not signals:
        print("⚠️ 没有可用信号")
        return 0

    # 汇总
    tradeable = [s for s in signals if s["tradeable"]]
    longs = [s for s in signals if s["action"] == ls.ACTION_LONG]
    print(f"   信号数: {len(signals)}  可交易: {len(tradeable)}  看多: {len(longs)}")

    if args.dry_run:
        print("\n--- dry-run 样本（前 3 条）---")
        for s in signals[:3]:
            p = s["probabilities"]
            print(f"  {s['symbol']:<12} {s['action']:<5} "
                  f"P(多)={p['long']:.2f} P(空)={p['short']:.2f} P(平)={p['flat']:.2f}  "
                  f"边际={s['edge_bps']:.0f} 成本={s['cost_bps']:.0f} "
                  f"净={s['net_edge_bps']:+.0f}bps  {'✅' if s['tradeable'] else '🚫'}")
        return 0

    os.makedirs(OUT_DIR, exist_ok=True)
    stream = os.path.join(OUT_DIR, "equity_signals.jsonl")
    latest = os.path.join(OUT_DIR, "last_equity_signal.json")
    for s in signals:
        ls.append_jsonl(s, stream)
    ls.write_latest({"ts": src_ts, "count": len(signals), "signals": signals}, latest)

    print(f"💾 已落盘: {stream}")
    print(f"💾 已落盘: {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
