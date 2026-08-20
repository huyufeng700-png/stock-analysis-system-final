#!/usr/bin/env python3
"""
backtest_delta.py — 蜘蛛网回测结果漂移守护 (v1.0, 2026-08-03)

根因: 07-31 沉淀,backtest 落盘后从不主动对比 delta → P0 STALE 假阳性
职责: 复用蜘蛛网 backtest(),对 runtime/picks/backtest/ 最近一份结果重跑,
      对比 win_rate/avg_profit 漂移,超阈值时推飞书 + 落 delta json

依赖: 直接 import 蜘蛛网 backtest 引擎,避免双维护
形态: --once 守护跑(cron/oncall 30min tick),无 --once 时单次
边界: 不动蜘蛛网本体,不替换内嵌 backtest,纯外挂
"""
import sys
import os
import json
import argparse
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional

# ===== 路径: 复用蜘蛛网模块,避免双维护 =====
KB_ROOT = Path("/Users/huyufeng/Documents/股票分析知识库")
SPIDER_PATH = KB_ROOT / "02_选股系统" / "蜘蛛网4.3_量化选股.py"
BT_DIR = KB_ROOT / "runtime" / "picks" / "backtest"
BT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(SPIDER_PATH.parent))
import importlib.util
spec = importlib.util.spec_from_file_location("spider43", SPIDER_PATH)
if spec is None or spec.loader is None:
    raise RuntimeError(f"无法加载蜘蛛网模块: {SPIDER_PATH}")
_spider = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_spider)
backtest = _spider.backtest  # 单股回测函数 (90天, ATR止损止盈)

# ===== 飞书 (抄蜘蛛网 send_to_feishu 模式) =====
FEISHU_CHAT = "oc_ef684ee04be46f9c15054770be144b82"  # 蜘蛛网 cron 群,同源不漂


def send_to_feishu(text: str) -> bool:
    try:
        data = {
            "receive_id": FEISHU_CHAT,
            "msg_type": "text",
            "content": json.dumps({"text": text}, ensure_ascii=False),
        }
        result = subprocess.run(
            ["lark-cli", "api", "POST", "/open-apis/im/v1/messages",
             "--params", json.dumps({"receive_id_type": "chat_id"}),
             "--data", json.dumps(data, ensure_ascii=False)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            resp = json.loads(result.stdout)
            return resp.get("code") == 0
        return False
    except Exception as e:
        print(f"⚠️ 飞书推送异常: {e}")
        return False


def find_latest_bt() -> Optional[Path]:
    """找 backtest_*.json 最新一份(顶层优先,fallback 下钻 archive/)"""
    files = sorted(BT_DIR.glob("backtest_*.json"))
    if not files:
        files = sorted((BT_DIR / "archive").glob("backtest_*.json"))
    return files[-1] if files else None


def load_bt_results(path: Path) -> tuple[dict, dict]:
    """读落盘结果,返回 (meta, {code: bt_result})"""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {"time": data.get("time"), "span_days": data.get("span_days")}, data.get("results", {})


def run_delta_check(threshold: float = 5.0, top_n: int = 5) -> dict:
    """
    主流程: 重跑 top_n 票,对比 win_rate/avg_profit 漂移
    threshold: win_rate 漂移超过该百分点报警
    """
    latest = find_latest_bt()
    if not latest:
        return {"status": "NO_BASELINE", "msg": f"backtest 目录无基线: {BT_DIR}"}

    meta, prev_results = load_bt_results(latest)
    if not prev_results:
        return {"status": "EMPTY", "msg": f"基线无 results: {latest.name}"}

    # 只对比前 N 只(跟蜘蛛网 main 保持一致)
    codes = list(prev_results.keys())[:top_n]
    print(f"📊 对比基线: {latest.name} ({meta.get('time', '?')}), 重跑 {len(codes)} 只")

    deltas = []
    new_results = {}
    for code in codes:
        prev = prev_results[code]
        curr = backtest(code, 90)
        if curr is None:
            deltas.append({"code": code, "status": "FAIL_REPLAY", "prev": prev})
            continue
        new_results[code] = curr
        wr_delta = round(curr["win_rate"] - prev["win_rate"], 2)
        ap_delta = round(curr["avg_profit"] - prev["avg_profit"], 2)
        deltas.append({
            "code": code,
            "status": "OK" if abs(wr_delta) < threshold else "DRIFT",
            "prev_win_rate": prev["win_rate"],
            "curr_win_rate": curr["win_rate"],
            "wr_delta": wr_delta,
            "prev_avg_profit": prev["avg_profit"],
            "curr_avg_profit": curr["avg_profit"],
            "ap_delta": ap_delta,
        })
        flag = "🚨" if abs(wr_delta) >= threshold else "✅"
        print(f"  {flag} {code}: wr {prev['win_rate']}% → {curr['win_rate']}% (Δ{wr_delta:+.1f})  "
              f"avg {prev['avg_profit']:+.2f}% → {curr['avg_profit']:+.2f}% (Δ{ap_delta:+.2f})")

    drift_count = sum(1 for d in deltas if d.get("status") == "DRIFT")
    fail_count = sum(1 for d in deltas if d.get("status") == "FAIL_REPLAY")
    return {
        "status": "OK" if drift_count == 0 else "DRIFT",
        "baseline_file": latest.name,
        "baseline_time": meta.get("time"),
        "drift_count": drift_count,
        "fail_count": fail_count,
        "deltas": deltas,
        "new_results": new_results,
    }


PARAM_GRID = [
    {"atr_sl_mult": 2.0, "atr_tp_mult": 3.0, "rsi_min": 30, "rsi_max": 70, "require_vol_ratio": True, "use_trailing_stop": False},
    {"atr_sl_mult": 2.0, "atr_tp_mult": 3.0, "rsi_min": 30, "rsi_max": 70, "require_vol_ratio": True, "use_trailing_stop": True},
    {"atr_sl_mult": 2.0, "atr_tp_mult": 3.5, "rsi_min": 30, "rsi_max": 70, "require_vol_ratio": True, "use_trailing_stop": True},
    {"atr_sl_mult": 2.5, "atr_tp_mult": 3.0, "rsi_min": 30, "rsi_max": 70, "require_vol_ratio": True, "use_trailing_stop": True},
    {"atr_sl_mult": 2.0, "atr_tp_mult": 3.0, "rsi_min": 35, "rsi_max": 65, "require_vol_ratio": True, "use_trailing_stop": True},
]


def run_param_search(top_n: int = 5) -> dict:
    """
    参数搜索: 用最新基线 top_n 只,扫不同策略参数组合,
    选出平均胜率/平均收益最优的配置。
    """
    latest = find_latest_bt()
    if not latest:
        return {"status": "NO_BASELINE", "msg": f"backtest 目录无基线: {BT_DIR}"}

    meta, prev_results = load_bt_results(latest)
    codes = list(prev_results.keys())[:top_n]
    print(f"🧪 参数搜索基线: {latest.name} ({meta.get('time', '?')}), 标的 {len(codes)} 只, 组合 {len(PARAM_GRID)} 组")

    best = None
    best_avg_wr = -1e9
    best_avg_profit = -1e9
    rows = []
    for idx, params in enumerate(PARAM_GRID, start=1):
        wr_sum = 0.0
        profit_sum = 0.0
        n = 0
        detail = {}
        for code in codes:
            curr = backtest(code, 90, **params)
            if curr is None:
                continue
            wr_sum += curr["win_rate"]
            profit_sum += curr["avg_profit"]
            n += 1
            detail[code] = curr
        if n == 0:
            continue
        avg_wr = wr_sum / n
        avg_profit = profit_sum / n
        rows.append({
            "idx": idx,
            "params": params,
            "avg_win_rate": round(avg_wr, 2),
            "avg_profit": round(avg_profit, 2),
            "tested": n,
            "detail": detail,
        })
        if avg_wr > best_avg_wr or (avg_wr == best_avg_wr and avg_profit > best_avg_profit):
            best_avg_wr = avg_wr
            best_avg_profit = avg_profit
            best = rows[-1]

    print(f"✅ 最优组合: #{best['idx']} 平均胜率 {best['avg_win_rate']}% 平均收益 {best['avg_profit']}%")
    return {"status": "OK", "baseline_file": latest.name, "best": best, "rows": rows}


def format_report(result: dict) -> str:
    """人类可读报告"""
    if result["status"] == "NO_BASELINE":
        return f"🕷️ backtest_delta: 无基线 — {result['msg']}"
    if result["status"] == "EMPTY":
        return f"🕷️ backtest_delta: 基线空 — {result['msg']}"

    icon = "🟢" if result["status"] == "OK" else "🚨"
    lines = [f"{icon} backtest_delta 漂移检查 (vs {result['baseline_file']})",
             f"   基线时间: {result['baseline_time']}",
             f"   漂移: {result['drift_count']}  重跑失败: {result['fail_count']}"]
    for d in result["deltas"]:
        if d.get("status") == "FAIL_REPLAY":
            lines.append(f"   ⚠️ {d['code']}: 重跑失败 (上次 wr={d['prev']['win_rate']}%)")
        else:
            lines.append(f"   {d['code']}: wr {d['prev_win_rate']}%→{d['curr_win_rate']}% "
                         f"(Δ{d['wr_delta']:+.1f})  avg {d['prev_avg_profit']:+.2f}%→{d['curr_avg_profit']:+.2f}%")
    return "\n".join(lines)


def update_index(bt_dir: Path) -> None:
    """更新知识库回测索引：JSON + Markdown"""
    files = sorted(bt_dir.glob("*.json"))
    index_json = KB_ROOT / "10_配置" / "backtest-index.json"
    index_md = KB_ROOT / "10_配置" / "backtest-index.md"

    entries = []
    md_lines = ["# Backtest 索引", ""]
    md_lines.append(f"- 更新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    md_lines.append(f"- 产物总数：{len(files)}")
    md_lines.append("")

    for f in files:
        try:
            data = json.load(open(f, "r", encoding="utf-8"))
        except Exception:
            continue
        name = f.name
        mtime = datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
        entry = {
            "file": name,
            "path": str(f),
            "mtime": mtime,
            "type": "delta" if name.startswith("delta_") else "backtest",
        }
        if entry["type"] == "backtest":
            results = data.get("results", {})
            n = len(results)
            wrs = [v.get("win_rate", 0) for v in results.values() if isinstance(v, dict)]
            avg_wr = round(sum(wrs) / len(wrs), 2) if wrs else None
            profits = [v.get("avg_profit", 0) for v in results.values() if isinstance(v, dict)]
            avg_profit = round(sum(profits) / len(profits), 2) if profits else None
            ranked = sorted(results.items(), key=lambda kv: kv[1].get("avg_profit", 0), reverse=True)
            top = ranked[:3]
            bottom = ranked[-3:]
            entry.update(
                {
                    "tested_count": n,
                    "avg_win_rate": avg_wr,
                    "avg_profit": avg_profit,
                    "p0_filtered_count": data.get("p0_filtered_count"),
                    "p0_dropped_count": data.get("p0_dropped_count"),
                    "top": [
                        {"code": k, "win_rate": v.get("win_rate"), "avg_profit": v.get("avg_profit")}
                        for k, v in top
                    ],
                    "bottom": [
                        {"code": k, "win_rate": v.get("win_rate"), "avg_profit": v.get("avg_profit")}
                        for k, v in bottom
                    ],
                }
            )
            md_lines.append(f"## {name}")
            md_lines.append(f"- 时间：{data.get('time')}")
            md_lines.append(f"- 标的：{n}，P0 过滤：{data.get('p0_filtered_count')}，P0 丢弃：{data.get('p0_dropped_count')}")
            if avg_wr is not None:
                md_lines.append(f"- 平均胜率：{avg_wr}%")
            if avg_profit is not None:
                md_lines.append(f"- 平均收益：{avg_profit}%")
            md_lines.append("- 前三：")
            for k, v in top:
                md_lines.append(f"  - {k} 胜率 {v.get('win_rate')}% 收益 {v.get('avg_profit')}%")
            md_lines.append("- 后三：")
            for k, v in bottom:
                md_lines.append(f"  - {k} 胜率 {v.get('win_rate')}% 收益 {v.get('avg_profit')}%")
            md_lines.append("")
        else:
            entry.update(
                {
                    "baseline_file": data.get("baseline_file"),
                    "baseline_time": data.get("baseline_time"),
                    "status": data.get("status"),
                    "drift_count": data.get("drift_count", 0),
                    "fail_count": data.get("fail_count", 0),
                    "tested_count": len(data.get("deltas", [])),
                    "drifted": [
                        {"code": d.get("code"), "wr_delta": d.get("wr_delta"), "ap_delta": d.get("ap_delta")}
                        for d in data.get("deltas", [])
                        if d.get("status") == "DRIFT"
                    ],
                }
            )
        entries.append(entry)

    md_lines.append("## delta")
    for entry in entries:
        if entry.get("type") != "delta":
            continue
        md_lines.append(
            f"- {entry['file']} baseline={entry.get('baseline_file')} 状态={entry.get('status')} 漂移={entry.get('drift_count')} 失败={entry.get('fail_count')}"
        )

    index = {
        "schema_version": "1.0",
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "count": len(entries),
        "latest": entries[-5:],
        "all": entries,
    }
    with open(index_json, "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    with open(index_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines) + "\n")
    print(f"🧭 索引已更新: {index_json.name}, {index_md.name}")


def main():
    parser = argparse.ArgumentParser(description="蜘蛛网 backtest 漂移守护 / 参数搜索")
    parser.add_argument("--once", action="store_true", help="单次跑(默认, 兼容 cron)")
    parser.add_argument("--threshold", type=float, default=5.0, help="win_rate 漂移报警阈值(百分点)")
    parser.add_argument("--top-n", type=int, default=5, help="重跑前 N 只")
    parser.add_argument("--no-push", action="store_true", help="不推飞书(只落盘+stdout)")
    parser.add_argument("--param-search", action="store_true", help="参数搜索模式：扫描策略参数组合并输出最优配置")
    args = parser.parse_args()

    if args.param_search:
        search = run_param_search(top_n=args.top_n)
        print(json.dumps(search, ensure_ascii=False, indent=2))
        return

    result = run_delta_check(threshold=args.threshold, top_n=args.top_n)
    report = format_report(result)
    print(report)

    # 落 delta (无论是否漂移都落,供历史分析)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    delta_path = BT_DIR / f"delta_{ts}.json"
    # 剥离 new_results 里的不可序列化(其实都是 dict,这里保险一下)
    save_obj = {k: v for k, v in result.items() if k != "new_results"}
    with open(delta_path, "w", encoding="utf-8") as f:
        json.dump(save_obj, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n💾 delta 落盘: {delta_path.name}")

    # 更新知识库索引
    try:
        update_index(BT_DIR)
    except Exception as e:
        print(f"⚠️ 更新索引失败: {e}")

    # 推送策略: 仅漂移或失败时推 OK 不推(借鉴 oncall 三态 P0-601658)
    if not args.no_push and result["status"] in ("DRIFT",):
        if send_to_feishu(report):
            print("📨 飞书已推 (DRIFT)")
        else:
            print("⚠️ 飞书推送失败")


if __name__ == "__main__":
    main()
