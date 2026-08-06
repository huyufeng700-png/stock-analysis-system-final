#!/usr/bin/env python3
"""
🩺 小胡瓜量化系统每日诊断器 v1.0
=====================================
零依赖 · 一键诊断: 数据源 / Cron 静默失败 / 日报缺失 / 缓存健康
不修任何东西,只发现问题 + 给出 P0/P1 优先级建议

用法:
    python3 daily_diagnostic.py
    python3 daily_diagnostic.py --json      # JSON 输出供脚本消费
    python3 daily_diagnostic.py --md x.md    # 写 Markdown 报告

设计原则 (2026-06-08):
- 不引入新依赖 (只用 stdlib)
- 不修改任何业务脚本,只读不写
- 检测 3 个关键故障模式:
    1. Cron last_status: ok 但磁盘无产物 (静默失败) - 06-06/06-07 实测
    2. 数据源 K线 0/N 失败 (架构性单点) - 06-07 0/3 实测
    3. 日报日期缺口 (业务中断) - 06-06/07 缺报告
"""
from __future__ import annotations
import json
import os
import sys
import glob
import subprocess
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

# ============== 路径常量 ==============
# v4.18 (2026-08-05 凌晨 P0 修复): 桌面已迁到 ~/Documents/股票分析知识库/
# BASE 优先指向新路径, 兜底旧 Desktop 路径 (向后兼容未迁移数据)
HOME = Path.home()
KB_ROOT = HOME / "Documents" / "股票分析知识库"   # 知识库根 (v4.18 新)
BASE = KB_ROOT                                     # 主路径: 知识库根
LEGACY_BASE = HOME / "Desktop" / "蜘蛛网计划"      # 兜底: 旧桌面路径
SCRIPTS = KB_ROOT / "scripts"
CRON_JOBS = HOME / ".hermes" / "cron" / "jobs.json"

# 数据源端点 (与 fallback_pool.py 对齐)
ENDPOINTS = [
    ("东方财富 push2 (实时)",   "https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=3"),
    ("腾讯 qt.gtimg (实时)",     "https://qt.gtimg.cn/q=sh000001"),
    ("新浪 K线 (历史)",          "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKlineData?symbol=sz300059&scale=240&datalen=5"),
]

# ============== 1. 数据源健康度 ==============
def probe_endpoint(name: str, url: str, timeout: int = 8) -> Dict:
    """短 UA 探测一个端点,返回 http_code + body 字节数 + 延迟"""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        t0 = datetime.now()
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            dt = (datetime.now() - t0).total_seconds()
            return {
                "name": name,
                "url": url,
                "http": resp.status,
                "bytes": len(body),
                "latency_s": round(dt, 2),
                "ok": resp.status == 200 and len(body) > 10 and body.strip() not in (b"null", b""),
            }
    except urllib.error.HTTPError as e:
        return {"name": name, "url": url, "http": e.code, "bytes": 0, "latency_s": 0, "ok": False, "err": str(e)}
    except Exception as e:
        return {"name": name, "url": url, "http": 0, "bytes": 0, "latency_s": 0, "ok": False, "err": type(e).__name__}


def diagnose_data_sources() -> Tuple[List[Dict], int, int]:
    """并行 (串行可接受) 探测所有数据源,返回 (results, ok_count, total)"""
    results = [probe_endpoint(n, u) for n, u in ENDPOINTS]
    ok = sum(1 for r in results if r["ok"])
    return results, ok, len(results)

# ============== 2. Cron 静默失败检测 ==============
def diagnose_cron_silent_failures() -> List[Dict]:
    """
    关键检查 (2026-06-08 验证 P0 模式):
    Cron last_status=ok 但磁盘上找不到产物 = 静默失败
    触发条件: cron job 是 'script: xxx.py' 模式 + no_agent=true
    """
    if not CRON_JOBS.exists():
        return [{"err": f"cron jobs.json 不存在: {CRON_JOBS}"}]

    issues = []
    try:
        d = json.loads(CRON_JOBS.read_text())
    except Exception as e:
        return [{"err": f"jobs.json 解析失败: {e}"}]

    for j in d.get("jobs", []):
        script = j.get("script")
        if not script:
            continue
        workdir = j.get("workdir") or str(HOME)
        full = Path(workdir) / script
        exists_primary = full.is_file()
        # 智能降级:如果默认 workdir(家目录)找不到,递归搜全盘 (skill §2a)
        exists = exists_primary
        search_hint = ""
        if not exists_primary:
            try:
                # 用 timeout 防止 find 卡死
                r = subprocess.run(
                    ["find", str(BASE), "-name", script, "-type", "f"],
                    capture_output=True, text=True, timeout=5,
                )
                hits = [l for l in r.stdout.strip().split("\n") if l]
                if hits:
                    exists = True
                    search_hint = f" (在 {hits[0]} 找到,但 workdir={workdir} 错)"
            except Exception:
                pass
        last = j.get("last_run_at", "?")

        # 额外: 看 last_run 与磁盘上的产物文件是否对得上
        product_mtime_warning = ""
        if exists and last and last != "None":
            try:
                last_dt = datetime.fromisoformat(last.replace("Z", "+00:00").replace("+08:00", ""))
                script_mtime = datetime.fromtimestamp(full.stat().st_mtime)
                gap_days = (last_dt.replace(tzinfo=None) - script_mtime).days
                if gap_days < 0 and abs(gap_days) > 0:
                    # 脚本 mtime 比 last_run 还新,可能近期改过,正常
                    pass
            except Exception:
                pass

        issues.append({
            "id": j.get("id", "?")[:8],
            "name": j.get("name", "?"),
            "script": script,
            "exists": exists,
            "search_hint": search_hint,
            "workdir": workdir,
            "last_status": j.get("last_status", "?"),
            "last_run": last,
            "flag": "✅" if exists else "🔴 MISSING",
        })
    return issues

# ============== 3. 日报日期缺口 ==============
def diagnose_report_gaps(days: int = 5) -> Dict:
    """检查最近 N 天 蜘蛛网v4.3 + 舆情报告 是否有缺口"""
    today = datetime.now().date()
    expected = [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]

    spider_pattern = str(BASE / "蜘蛛网v4.3_*_*.json")
    yq_pattern = str(BASE / "舆情报告_*.json")

    spider_dates = set()
    for f in glob.glob(spider_pattern):
        # 文件名形如 蜘蛛网v4.3_20260605_1501.json
        try:
            parts = Path(f).stem.split("_")
            spider_dates.add(parts[1])  # 20260605
        except (IndexError, ValueError):
            continue

    yq_dates = set()
    for f in glob.glob(yq_pattern):
        # 文件名形如 舆情报告_20260605.json
        try:
            stem = Path(f).stem
            # 取最后 8 位数字
            tail = stem.split("_")[-1]
            if len(tail) == 8 and tail.isdigit():
                yq_dates.add(tail)
        except (IndexError, ValueError):
            continue

    missing_spider = [d for d in expected if d not in spider_dates]
    missing_yq = [d for d in expected if d not in yq_dates]

    # 最新产物
    spider_files = sorted(glob.glob(spider_pattern), key=os.path.getmtime, reverse=True)
    yq_files = sorted(glob.glob(yq_pattern), key=os.path.getmtime, reverse=True)

    return {
        "checked_days": days,
        "expected_dates": expected,
        "spider_missing": missing_spider,
        "yq_missing": missing_yq,
        "spider_latest": Path(spider_files[0]).name if spider_files else "无",
        "spider_latest_mtime": datetime.fromtimestamp(os.path.getmtime(spider_files[0])).strftime("%Y-%m-%d %H:%M") if spider_files else "?",
        "yq_latest": Path(yq_files[0]).name if yq_files else "无",
        "yq_latest_mtime": datetime.fromtimestamp(os.path.getmtime(yq_files[0])).strftime("%Y-%m-%d %H:%M") if yq_files else "?",
    }

# ============== 4. 磁盘使用 ==============
def du_dir(p: Path) -> int:
    """递归算目录总字节数 (兼容无 du 命令)"""
    total = 0
    try:
        for root, _, files in os.walk(p):
            for f in files:
                fp = Path(root) / f
                try:
                    total += fp.stat().st_size
                except OSError:
                    pass
    except OSError:
        return 0
    return total


def diagnose_disk() -> List[Dict]:
    targets = [
        ("scripts/",        SCRIPTS),
        ("02_选股系统/",    BASE / "02_选股系统"),
        ("03_持仓管理/",    BASE / "03_持仓管理"),
        ("04_数据采集/",    BASE / "04_数据采集"),
        ("05_工具脚本/",    BASE / "05_工具脚本"),
        ("__pycache__ 总",  None),  # 特殊
    ]
    out = []
    pyc_total = 0
    for root, dirs, _ in os.walk(BASE):
        if "__pycache__" in dirs:
            pyc_total += du_dir(Path(root) / "__pycache__")

    for name, p in targets:
        if p is None:
            out.append({"name": name, "size_mb": round(pyc_total / 1024 / 1024, 2)})
            continue
        if p.exists():
            out.append({"name": name, "size_mb": round(du_dir(p) / 1024 / 1024, 2)})
    return out

# ============== 主诊断流程 ==============
def run_full_diagnostic() -> Dict:
    print("🩺 小胡瓜量化系统每日诊断 v1.0")
    print(f"⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    # 1. 数据源
    print("\n[1/4] 🌐 数据源实测 (短 UA) ...")
    src, ok, total = diagnose_data_sources()
    for r in src:
        flag = "✅" if r["ok"] else "🔴"
        err = f" [{r.get('err','')}]" if not r["ok"] else ""
        print(f"  {flag} {r['name']:25s} http={r['http']} bytes={r['bytes']:>5} t={r['latency_s']}s{err}")
    print(f"  小结: {ok}/{total} 可用")
    if ok < total:
        print(f"  🔴 触发 P0: K线 fallback 池 {ok}/{total} 失败 → 架构性单点 (P0-008)")

    # 2. Cron
    print("\n[2/4] ⏰ Cron 静默失败检测 ...")
    cron = diagnose_cron_silent_failures()
    missing = [c for c in cron if c.get("exists") is False]
    workdir_misconfig = [c for c in cron if c.get("search_hint")]
    for c in cron[:8]:  # 只显示前 8 个
        hint = c.get("search_hint", "")
        print(f"  {c.get('flag','?')} {c.get('id','?'):8s} {c.get('name','?')[:30]:30s} {c.get('last_status','?'):6s} {c.get('last_run','?')[:16]}{hint}")
    if len(cron) > 8:
        print(f"  ... (共 {len(cron)} 个 cron 任务)")
    if missing:
        print(f"  🔴 触发 P0: {len(missing)} 个 cron 引用 script 真缺失")
    if workdir_misconfig:
        print(f"  🔴 触发 P0-009: {len(workdir_misconfig)} 个 cron 缺 workdir → 默认 ~ → 路径错 (比 script 丢失更隐蔽!)")

    # 3. 日报缺口
    print("\n[3/4] 📅 日报日期缺口 (最近 5 天) ...")
    gap = diagnose_report_gaps(days=5)
    print(f"  蜘蛛网v4.3 最新: {gap['spider_latest']} ({gap['spider_latest_mtime']})")
    if gap["spider_missing"]:
        print(f"  🔴 缺失: {', '.join(gap['spider_missing'])}")
    print(f"  舆情报告  最新: {gap['yq_latest']} ({gap['yq_latest_mtime']})")
    if gap["yq_missing"]:
        print(f"  🔴 缺失: {', '.join(gap['yq_missing'])}")
    if not gap["spider_missing"] and not gap["yq_missing"]:
        print("  ✅ 近 5 天日报完整")

    # 4. 磁盘
    print("\n[4/4] 💿 磁盘使用 ...")
    disk = diagnose_disk()
    for d in disk:
        flag = "🟡" if d["size_mb"] > 1.0 else "✅"
        print(f"  {flag} {d['name']:18s} {d['size_mb']:>8.2f} MB")

    print("\n" + "=" * 60)
    print("✅ 诊断完成")

    return {
        "timestamp": datetime.now().isoformat(),
        "data_sources": src,
        "data_source_ok": ok,
        "data_source_total": total,
        "cron_jobs": cron,
        "cron_missing": len(missing),
        "report_gaps": gap,
        "disk": disk,
    }

# ============== Markdown 渲染 ==============
def render_md(d: Dict) -> str:
    lines = [
        f"# 🩺 量化系统每日诊断报告",
        f"",
        f"**生成时间**: {d['timestamp']}",
        f"**诊断器**: `scripts/daily_diagnostic.py` v1.0",
        f"",
        f"---",
        f"",
        f"## 1. 数据源健康度",
        f"",
        f"| 端点 | HTTP | 字节 | 延迟 | 状态 |",
        f"|---|---|---|---|---|",
    ]
    for r in d["data_sources"]:
        flag = "✅" if r["ok"] else "🔴"
        err = f" ({r.get('err','')})" if not r["ok"] else ""
        lines.append(f"| {r['name']} | {r['http']} | {r['bytes']} | {r['latency_s']}s | {flag}{err} |")
    lines += [
        f"",
        f"**汇总**: {d['data_source_ok']}/{d['data_source_total']} 可用",
        f"",
        f"## 2. Cron 静默失败检测",
        f"",
        f"| ID | 名称 | Script | 存在 | last_status | last_run |",
        f"|---|---|---|---|---|---|",
    ]
    for c in d["cron_jobs"]:
        flag = "✅" if c.get("exists") else "🔴 MISSING"
        lines.append(f"| {c.get('id','?')} | {c.get('name','?')[:25]} | `{c.get('script','-')[:30]}` | {flag} | {c.get('last_status','?')} | {str(c.get('last_run','?'))[:16]} |")
    lines += [
        f"",
        f"**缺失 script 数**: {d['cron_missing']} / {len(d['cron_jobs'])}",
        f"",
        f"## 3. 日报日期缺口 (近 5 天)",
        f"",
        f"- 蜘蛛网v4.3 最新: **{d['report_gaps']['spider_latest']}** ({d['report_gaps']['spider_latest_mtime']})",
        f"- 缺失: {', '.join(d['report_gaps']['spider_missing']) if d['report_gaps']['spider_missing'] else '无'}",
        f"- 舆情报告 最新: **{d['report_gaps']['yq_latest']}** ({d['report_gaps']['yq_latest_mtime']})",
        f"- 缺失: {', '.join(d['report_gaps']['yq_missing']) if d['report_gaps']['yq_missing'] else '无'}",
        f"",
        f"## 4. 磁盘使用",
        f"",
        f"| 目录 | 大小 |",
        f"|---|---|",
    ]
    for x in d["disk"]:
        lines.append(f"| {x['name']} | {x['size_mb']} MB |")

    return "\n".join(lines)

# ============== CLI ==============
def main():
    args = sys.argv[1:]
    out_json = "--json" in args
    md_path = None
    if "--md" in args:
        i = args.index("--md")
        md_path = args[i + 1] if i + 1 < len(args) else None

    diag = run_full_diagnostic()
    if out_json:
        print("\n--- JSON ---")
        # 去掉 cron_jobs 列表 (太长) 只保留计数
        slim = {k: v for k, v in diag.items() if k != "cron_jobs"}
        slim["cron_jobs_count"] = len(diag["cron_jobs"])
        json_text = json.dumps(slim, ensure_ascii=False, indent=2)
        print(json_text)
        # 07-31 重构: 落 runtime/logs/(替代 /tmp/)
        import os
        _log_dir = os.path.expanduser("~/Documents/股票分析知识库/runtime/logs")
        os.makedirs(_log_dir, exist_ok=True)
        _json_path = os.path.join(_log_dir, "daily_diagnostic_latest.json")
        with open(_json_path, "w", encoding="utf-8") as _f:
            _f.write(json_text)
        print(f"\n💾 JSON 已存: {_json_path}")
    if md_path:
        Path(md_path).write_text(render_md(diag), encoding="utf-8")
        print(f"\n📄 Markdown 已写: {md_path}")


if __name__ == "__main__":
    main()
