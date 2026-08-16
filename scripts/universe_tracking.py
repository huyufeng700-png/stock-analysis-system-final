#!/usr/bin/env python3
"""
universe_tracking.py — 新老股票池追踪对比 (2026-08-16 起)
每个蜘蛛网选股 run 之后, 统计:
  - 老池(111) vs 新池(230) 各自: 出票数 / 入选率 / 平均评分
  - 结合 backtest 结果: 各池票的平均胜率 / 平均收益
产出: runtime/tracking/universe_tracking.md (追加) + universe_tracking.json
"""
import json, os, re, glob, sys
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PICKS_DIR = os.path.join(BASE, "runtime", "picks")
BT_DIR = os.path.join(PICKS_DIR, "backtest")
TRACK_DIR = os.path.join(BASE, "runtime", "tracking")
MD_FILE = os.path.join(TRACK_DIR, "universe_tracking.md")
JSON_FILE = os.path.join(TRACK_DIR, "universe_tracking.json")

# 老池 111 只 (2026-08-16 扩充前, 来自 git efc0a9d)
OLD_CODES = {
    "600036","601318","600519","601166","600030","601328","600887","601288","600276","601668",
    "600309","601899","600031","601012","600585","601601","600588","601888","600050","601989",
    "600547","601398","600028","601857","600809","601628","600016","601225","600436","601088",
    "600900","601688","600570","601186","600000","601818","600104","601939","600048","601211",
    "600837","601336","600690","601006","600340","601111","600111","601800","600489","601658",
    "600025","601985","600516","601238","300059","300750","300124","300015","300408","300142",
    "300760","300033","300450","300274","300496","300661","300782","300347","300136","300003",
    "300413","300595","300676","300832","300896","300999","300979","300919","000538","000878",
    "000975","002039","002116","002267","002428","002501","600497","600995","601107","603027",
    "688981","688256","688012","688041","688111","688169","688223","688396","002230","002236",
    "002415","000977","000063","000034","002049","002153","300474","300223","300115","300308","300502",
}

def current_universe() -> set:
    src = open(os.path.join(BASE, "02_选股系统", "蜘蛛网4.3_量化选股.py"), encoding="utf-8").read()
    m = re.search(r"def get_stock_list\(\):\n    return \[(.*?)\n    \]", src, re.S)
    return set(re.findall(r"\('(?:sh|sz)', '(\d{6})'\)", m.group(1)))

def load_picks(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
        return d.get("stocks") or d.get("picks") or []
    except Exception:
        return []

def load_bt(path):
    try:
        d = json.load(open(path, encoding="utf-8"))
        res = d.get("results") or d.get("backtests") or {}
        return res
    except Exception:
        return {}

def main():
    os.makedirs(TRACK_DIR, exist_ok=True)
    uni = current_universe()
    new_codes = uni - OLD_CODES
    pick_files = sorted(glob.glob(os.path.join(PICKS_DIR, "蜘蛛网v4.3_*.json")))
    bt_files = {os.path.basename(p): p for p in glob.glob(os.path.join(BT_DIR, "backtest_*.json"))}
    history = json.load(open(JSON_FILE)) if os.path.exists(JSON_FILE) else []
    seen = {h["pick_file"] for h in history}

    rows = []
    for pf in pick_files:
        bname = os.path.basename(pf)
        if bname in seen:
            continue
        picks = load_picks(pf)
        if not picks:
            continue
        ts = re.search(r"_(\d{8})_(\d{4})", bname)
        run_time = f"{ts.group(1)}_{ts.group(2)}" if ts else bname
        # 匹配同时间戳的 backtest 文件 (backtest_YYYYMMDD_HHMM.json)
        bt = {}
        for bt_name, bt_path in bt_files.items():
            if run_time in bt_name.replace("backtest_", "").replace(".json", ""):
                bt = load_bt(bt_path); break
        old_picks = [p for p in picks if p.get("code") in OLD_CODES]
        new_picks = [p for p in picks if p.get("code") in new_codes]
        def stats(ps, pool_size):
            if not ps:
                return {"count": 0, "rate": 0, "avg_score": 0, "avg_wr": None, "avg_profit": None}
            avg_score = sum(p.get("score", 0) for p in ps) / len(ps)
            wrs, pros = [], []
            for p in ps:
                r = bt.get(p.get("code")) or {}
                if isinstance(r, dict) and r.get("win_rate") is not None:
                    wrs.append(r["win_rate"]); pros.append(r.get("avg_profit", 0) or 0)
            return {
                "count": len(ps),
                "rate": round(len(ps) / pool_size * 100, 1),
                "avg_score": round(avg_score, 1),
                "avg_wr": round(sum(wrs)/len(wrs), 1) if wrs else None,
                "avg_profit": round(sum(pros)/len(pros), 2) if pros else None,
            }
        row = {
            "pick_file": bname, "run_time": run_time,
            "old_pool_size": len(OLD_CODES), "new_pool_size": len(uni),
            "old": stats(old_picks, len(OLD_CODES)),
            "new": stats(new_picks, len(new_codes)),
            "total_picks": len(picks),
        }
        history.append(row); seen.add(bname); rows.append(row)

    # 写 JSON
    json.dump(history, open(JSON_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 写 MD (重建, 含全部历史)
    lines = ["# 新老股票池追踪对比 (2026-08-16 起)", "",
             "> 老池 111 只(8/16 前) vs 新池 230 只(扩充后)。入选率 = 出票数/池大小。avg_wr/avg_profit 来自同轮 backtest。", "",
             "| 运行时间 | 总出票 | 老池出票(率) | 老池均分 | 老池胜率 | 老池均益 | 新池出票(率) | 新池均分 | 新池胜率 | 新池均益 |", "|---|---|---|---|---|---|---|---|---|---|"]
    for r in history:
        o, n = r["old"], r["new"]
        lines.append(
            f"| {r['run_time']} | {r['total_picks']} "
            f"| {o['count']}({o['rate']}%) | {o['avg_score']} | {o['avg_wr'] if o['avg_wr'] is not None else '-'} | {o['avg_profit'] if o['avg_profit'] is not None else '-'} "
            f"| {n['count']}({n['rate']}%) | {n['avg_score']} | {n['avg_wr'] if n['avg_wr'] is not None else '-'} | {n['avg_profit'] if n['avg_profit'] is not None else '-'} |")
    open(MD_FILE, "w", encoding="utf-8").write("\n".join(lines) + "\n")

    for r in rows:
        o, n = r["old"], r["new"]
        print(f"{r['run_time']}: 总{r['total_picks']}只 | 老池 {o['count']}({o['rate']}%) 均分{o['avg_score']} "
              f"| 新池 {n['count']}({n['rate']}%) 均分{n['avg_score']}")

if __name__ == "__main__":
    main()
