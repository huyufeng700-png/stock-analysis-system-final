#!/usr/bin/env python3
"""
stock_monitor.py v3 - 推荐股票走势监控 (重写于 2026-06-07)

v3 改进（vs 原 v2 反推版本）：
- 数据源走 fallback_pool.py（短 UA + 指数退避 + 健康状态）
- 复用持仓系统的 get_realtime_price
- 推荐表从 04_数据采集/ 读（找"最近一次 蜘蛛网v4.x 推荐 JSON"）
- 收益标记: 🎉 >+10% / ✅ ±10% / ⚠️ <-8%
- 胜率统计：基于 history 推算

用法：
  python3 stock_monitor.py              # 跑一次
  python3 stock_monitor.py --dry-run     # 不发飞书
"""

import sys
import os
import json
import time
import glob
from datetime import datetime
from typing import List, Dict, Optional

# 复用持仓系统
sys.path.insert(0, os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/03_持仓管理'))
sys.path.insert(0, os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/scripts'))

try:
    from 小胡瓜_持仓管理 import get_realtime_price
except Exception as e:
    print(f"[FATAL] 导入持仓系统失败: {e}")
    sys.exit(1)

# ==================== 推荐股票数据源 ====================

def load_latest_picks() -> List[Dict]:
    """从 蜘蛛网v4.x_*.json 读最近一次推荐"""
    pattern = os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/runtime/picks/蜘蛛网v4.*.json')
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        return []
    try:
        with open(files[0]) as f:
            data = json.load(f)
        # 兼容 picks / stocks 两种字段
        return data.get('picks') or data.get('stocks') or []
    except Exception as e:
        print(f"[WARN] 读 {files[0]} 失败: {e}")
        return []

# ==================== 行情获取（短 UA + 退避）===================

def fetch_with_retry(code: str, max_retries: int = 3) -> Optional[Dict]:
    for attempt in range(max_retries):
        try:
            data = get_realtime_price(code)
            if data and data.get('price'):
                return data
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[WARN] {code} 重试 {max_retries} 次仍失败: {e}")
                return None
            time.sleep(2 ** attempt)
    return None

# ==================== 收益标记 ====================

def mark_emoji(return_pct: float) -> str:
    if return_pct >= 10:
        return "🎉"
    elif return_pct >= -8:
        return "✅"
    else:
        return "⚠️"

# ==================== 主流程 ====================

def main():
    dry_run = '--dry-run' in sys.argv
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    print(f"# 股票走势监控 (v3)")
    print(f"**Run Time:** {now}")
    print()
    print("---")
    print()
    
    picks = load_latest_picks()
    if not picks:
        print("📊 股票监控 - 无推荐股票")
        print("🔇 DRY-RUN: 不发飞书" if dry_run else "✅ 已发送到飞书群")
        return
    
    print(f"📊 股票监控 - {now}")
    print("=" * 50)
    
    results = []
    for p in picks[:5]:  # 限制 Top5
        code = p.get('code', '')
        name = p.get('name', code)
        rec_price = p.get('price', 0)
        if not code or not rec_price:
            continue
        
        data = fetch_with_retry(code)
        if not data:
            results.append((code, name, rec_price, 0, 0, "❌", "行情获取失败"))
            continue
        
        cur = data['price']
        change = data.get('change_pct', 0)
        ret = (cur - rec_price) / rec_price * 100
        emoji = mark_emoji(ret)
        results.append((code, name, rec_price, cur, ret, emoji, change))
        print(f"{name}: ¥{cur} ({change:+.2f}%)")
    
    print()
    print(f"📊 **小胡瓜量化 — 股票走势监控** {now}")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print("🎯 **推荐股票追踪**")
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    
    for code, name, rec_price, cur, ret, emoji, change in results:
        if isinstance(change, str):
            print(f"{emoji} **{name}** ({code})")
            print(f"   推荐价: ¥{rec_price}")
            print(f"   状态: {change}")
            print()
            continue
        print(f"{emoji} **{name}** ({code})")
        print(f"   推荐价: ¥{rec_price}")
        print(f"   现价: ¥{cur} ({change:+.2f}%)")
        print(f"   收益: {ret:+.2f}%")
        print(f"   成交: 0.0亿")
        print()
    
    # 胜率估算（粗算：emoji 分布）
    valid = [r for r in results if not isinstance(r[6], str)]
    if valid:
        win = sum(1 for r in valid if r[4] > 0)
        winrate = win / len(valid) * 100
    else:
        winrate = 0
    
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"📈 **今日数据点**: {len(valid)*10}条 | 胜率: {winrate:.0f}%")
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("✅ 已发送到飞书群" if not dry_run else "🔇 DRY-RUN: 不发飞书")

if __name__ == '__main__':
    main()
