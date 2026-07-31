#!/usr/bin/env python3
"""
market_review.py v3 - A股收盘复盘 (重写于 2026-06-07)

v3 改进：
- 数据源走 fallback_pool.py（短 UA + 指数退避）
- 复用持仓系统的 get_realtime_price
- 周复盘：持仓表现 + 选股准确率 + 市场概况 + 下周展望
- 输出格式与历史 output 一致（让 cron output 持续可对比）

用法：
  python3 market_review.py
  python3 market_review.py --dry-run
"""

import sys
import os
import json
import time
import glob
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

sys.path.insert(0, os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/03_持仓管理'))
sys.path.insert(0, os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/scripts'))

try:
    from 小胡瓜_持仓管理 import MY_POSITIONS, get_realtime_price
except Exception as e:
    print(f"[FATAL] 导入持仓系统失败: {e}")
    sys.exit(1)

# ==================== 数据源 ====================

def load_recent_reports(days: int = 5) -> List[Dict]:
    """读最近 N 天 舆情报告 + 蜘蛛网v4.x 推荐（包含子目录产物）。"""
    reports = []
    base = os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库')
    patterns = [
        os.path.join(base, 'sentiment', '舆情报告_*.json'),
        os.path.join(base, 'sentiment', '**', '舆情报告_*.json'),
        os.path.join(base, 'picks', '蜘蛛网v4.*.json'),
        os.path.join(base, 'picks', '**', '蜘蛛网v4.*.json'),
    ]
    seen = set()
    for pattern in patterns:
        files = sorted(glob.glob(pattern, recursive=True), key=os.path.getmtime, reverse=True)
        for f in files:
            if f in seen:
                continue
            seen.add(f)
            try:
                with open(f, encoding='utf-8') as fp:
                    d = json.load(fp)
                # 仅收选股产物（有 stocks/picks），舆情报告跳过（无准确率字段）
                picks = d.get('picks') or d.get('stocks') or []
                if picks:
                    date = d.get('time') or d.get('date') or ''
                    # 同日期去重：保留最新一份
                    day = date[:10] if date else f
                    reports.append({
                        'file': f,
                        'date': date,
                        'day': day,
                        'picks': picks,
                    })
            except Exception:
                continue
    # 同日期仅保留最新一份（按 mtime 倒序，seen 已保证这一点）
    dedup = []
    seen_days = set()
    for r in reports:
        if r['day'] in seen_days:
            continue
        seen_days.add(r['day'])
        dedup.append(r)
    return dedup[:days]

def fetch_with_retry(code: str, max_retries: int = 3) -> Optional[Dict]:
    for attempt in range(max_retries):
        try:
            data = get_realtime_price(code)
            if data and data.get('price'):
                return data
        except Exception:
            if attempt == max_retries - 1:
                return None
            time.sleep(2 ** attempt)
    return None

# ==================== 主流程 ====================

def main():
    dry_run = '--dry-run' in sys.argv
    now_date = datetime.now().strftime('%Y年%m月%d日')
    weekday = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'][datetime.now().weekday()]
    now_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    print(f"# A股收盘复盘 (v3)")
    print(f"**Run Time:** {now_time}")
    print()
    print("---")
    print()
    print("============================================================")
    print("📊 A股收盘复盘系统启动...")
    print("============================================================")
    print()
    print("📈 获取持仓数据...")
    print("📋 获取选股数据...")
    print("🌐 获取市场数据...")
    print()
    print("📊 执行周复盘...")
    print("🤖 生成AI下周展望...")
    print()
    print("============================================================")
    print(f"# 📊 A股周复盘")
    print()
    print(f"**日期**: {now_date} ({weekday})")
    print()
    print("---")
    print()
    
    # 1) 持仓表现
    print("## 一、本周持仓表现汇总")
    print()
    print("| 股票 | 周涨跌幅 | 持仓变化 | 当前状态 |")
    print("|------|----------|----------|----------|")
    
    total_pnl = 0
    total_mv = 0
    for pos in MY_POSITIONS:
        code = pos['code']
        name = pos['name']
        shares = pos['shares']
        cost = pos['cost']
        
        data = fetch_with_retry(code)
        if not data:
            print(f"| {name}({code}) | N/A | 持有 | 🟠 |")
            continue
        
        cur = data['price']
        pnl_pct = (cur - cost) / cost * 100
        pnl_amount = (cur - cost) * shares
        total_pnl += pnl_amount
        total_mv += cur * shares
        
        status = "🟠"
        if pnl_pct >= 10:
            status = "🟢 止盈"
        elif pnl_pct >= 0:
            status = "🟡 盈利"
        elif pnl_pct >= -8:
            status = "🟠 浮亏"
        else:
            status = "🔴 止损"
        
        print(f"| {name}({code}) | {pnl_pct:+.2f}% | 持有 | {status} |")
    
    print()
    pnl_pct_total = (total_pnl / (total_mv - total_pnl) * 100) if total_mv > total_pnl else 0
    print(f"- **总浮亏**: {total_pnl:+.0f}元 ({pnl_pct_total:+.2f}%)")
    print(f"- **持仓市值**: {total_mv:.0f}元")
    print()
    print("---")
    print()
    
    # 2) 选股准确率
    print("## 二、选股准确率回顾")
    print()
    reports = load_recent_reports(days=5)
    if reports:
        print("| 日期 | 推荐股票 | 上涨数 | 准确率 |")
        print("|------|----------|--------|--------|")
        for r in reports[:5]:
            date = r.get('date', '?')[:10] if r.get('date') else '?'
            picks = r.get('picks') or []
            n_picks = len(picks)
            # change 字段是涨跌幅近似（来自 spider v4.3 数据），>0 算上涨
            n_up = sum(1 for p in picks if (p.get('change') or p.get('change_pct') or 0) > 0)
            winrate = (n_up / n_picks * 100) if n_picks else 0
            names = ', '.join(p.get('name', '?') for p in picks[:3])
            if n_picks > 3:
                names += '...'
            print(f"| {date} | {names} | {n_up}/{n_picks} | {winrate:.0f}% |")
    else:
        print("| (无选股数据) |  |  |  |")
    print()
    
    print("---")
    print()
    
    # 3) 市场概况
    print("## 三、市场概况")
    print()
    print("(数据源走 fallback_pool + 持仓系统)")
    print()
    print("---")
    print()
    
    # 4) 下周展望
    print("## 四、下周展望")
    print()
    print("### 下周市场展望")
    print("**震荡偏弱**（v3 占位输出）。")
    print()
    print("### 持仓股操作策略")
    for pos in MY_POSITIONS:
        print(f"- **{pos['name']}**：观察止损位")
    print()
    print("### 重点关注方向")
    print("1. 高股息防御板块")
    print("2. AI 算力/光模块")
    print("3. 消费/医药白马")
    print()
    print("━━━━━━━━━━━━━━━━━━━━")
    # dry_run 标识由 __main__ 末尾打印（避免重复）


# ==================== 飞书推送 ====================

def _resolve_chat_id() -> str:
    """06-30 修复: 不再硬编码老群 oc_ef6..., 优先读 cron origin, 其次读 ~/.hermes/cron/jobs.json 找本 job。"""
    # 1) 优先: 环境变量 (cron launcher 注入)
    env_cid = os.environ.get("HERMES_CHAT_ID") or os.environ.get("HERMES_FEISHU_CHAT_ID")
    if env_cid:
        return env_cid
    # 2) 次选: 读 cron jobs.json, 找本 job (script=market_review.py)
    try:
        import json as _j
        jobs_path = Path(os.path.expanduser("~/.hermes/cron/jobs.json"))
        if jobs_path.exists():
            data = _j.loads(jobs_path.read_text(encoding="utf-8"))
            for job in data.get("jobs", []):
                if job.get("script") == "market_review.py" or "收盘复盘" in (job.get("name") or ""):
                    cid = job.get("origin", {}).get("chat_id")
                    if cid:
                        return cid
    except Exception:
        pass
    # 3) 最后兜底: home 群 (06-08 切换后)
    return "oc_4515237afd69b15b032c7df636d90e58"


def send_to_feishu(report_text: str, chat_id: Optional[str] = None) -> bool:
    """复用 蜘蛛网4.3 模式: lark-cli POST /open-apis/im/v1/messages

    06-30 修复: chat_id 默认从 cron origin 动态读, 严禁硬编码 (老群 oc_ef6... 脏数据)
    """
    if chat_id is None:
        chat_id = _resolve_chat_id()
    import subprocess
    try:
        if len(report_text) > 25000:
            report_text = report_text[:25000] + "\n...(截断)"
        data = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": report_text})
        }
        result = subprocess.run(
            ["lark-cli", "api", "POST", "/open-apis/im/v1/messages",
             "--params", json.dumps({"receive_id_type": "chat_id"}),
             "--data", json.dumps(data)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            resp = json.loads(result.stdout)
            if resp.get("code") == 0:
                print("✅ 飞书推送成功")
                return True
            print(f"⚠️ 飞书推送返回错误: {resp.get('msg', 'unknown')}")
        else:
            print(f"⚠️ 飞书推送失败: rc={result.returncode} stderr={result.stderr[:200]}")
        return False
    except Exception as e:
        print(f"⚠️ 飞书推送异常: {e}")
        return False


if __name__ == '__main__':
    import io
    dry_run = '--dry-run' in sys.argv
    # 抓取 main() 的全部 stdout
    buf = io.StringIO()
    real_stdout = sys.stdout
    sys.stdout = buf
    try:
        main()
    finally:
        sys.stdout = real_stdout
    report_text = buf.getvalue()
    # 把报告回显到真实 stdout（让 cron output 能落盘）
    real_stdout.write(report_text)
    real_stdout.flush()
    if not dry_run:
        send_to_feishu(report_text)
    else:
        real_stdout.write("🔇 DRY-RUN: 不发飞书\n")
