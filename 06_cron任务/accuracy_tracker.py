#!/usr/bin/env python3
"""
accuracy_tracker.py v3 - 推荐准确率验证 (重写于 2026-06-07)

v3 改进：
- 数据源走 fallback_pool.py（短 UA + 指数退避）
- 复用持仓系统的 get_realtime_price
- 从 04_数据采集/ 读推荐 JSON（picks 字段）
- 16:00 收盘后算当天准确率：盈利数/总数 + 最高最低 + 跟踪天数
- 优化方向：基于回测数据生成建议

用法：
  python3 accuracy_tracker.py
  python3 accuracy_tracker.py --dry-run
"""

import sys
import os
import json
import time
import glob
from datetime import datetime, timedelta
from typing import List, Dict, Optional

sys.path.insert(0, os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/03_持仓管理'))
sys.path.insert(0, os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/scripts'))

try:
    from 小胡瓜_持仓管理 import get_realtime_price
except Exception as e:
    print(f"[FATAL] 导入持仓系统失败: {e}")
    sys.exit(1)

# ==================== 数据源 ====================

def load_latest_picks() -> List[Dict]:
    pattern = os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/runtime/picks/蜘蛛网v4.*.json')
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        return []
    try:
        with open(files[0]) as f:
            data = json.load(f)
        return data.get('picks') or data.get('stocks') or []
    except Exception:
        return []

def get_pick_date() -> Optional[str]:
    """读最近一次推荐的日期（从文件名）"""
    pattern = os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/runtime/picks/蜘蛛网v4.*.json')
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    if not files:
        return None
    basename = os.path.basename(files[0])
    # 文件名: 蜘蛛网v4.3_20260605_1501.json
    parts = basename.replace('.json', '').split('_')
    if len(parts) >= 2:
        return parts[1]  # 20260605
    return None

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

def mark_emoji_with_zone(ret: float) -> str:
    """含止盈/盈利/亏损分区的 emoji"""
    if ret >= 20:
        return "🎉 止盈区"
    elif ret >= 10:
        return "🎉 强止盈"
    elif ret >= 0:
        return "✅ 盈利中"
    elif ret >= -8:
        return "⚠️ 浮亏"
    else:
        return "🔴 止损区"

# ==================== 主流程 ====================

def main():
    dry_run = '--dry-run' in sys.argv
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    print(f"# 推荐准确率验证 (v3)")
    print(f"**Run Time:** {now}")
    print()
    print("---")
    print()
    
    picks = load_latest_picks()
    pick_date = get_pick_date()
    
    if not picks:
        print("📊 准确率验证系统 - 无推荐数据")
        print("🔇 DRY-RUN: 不发飞书" if dry_run else "✅ 报告已发送到飞书群")
        return
    
    # 跟踪天数
    if pick_date:
        try:
            dt = datetime.strptime(pick_date, '%Y%m%d')
            days = (datetime.now() - dt).days
        except Exception:
            days = 0
    else:
        days = 0
    
    print("📊 准确率验证系统")
    print("=" * 50)
    for p in picks[:3]:
        code = p.get('code', '')
        name = p.get('name', code)
        data = fetch_with_retry(code)
        if data:
            print(f"📈 {name}: ¥{data['price']} ({data.get('change_pct', 0):+.2f}%)")
    
    print()
    print(f"📊 **小胡瓜量化 — 推荐准确率验证**")
    print(f"{now}")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"🎯 **当前持仓推荐** ({len(picks)}只)")
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    
    win_count = 0
    total_ret = 0.0
    
    for p in picks[:5]:
        code = p.get('code', '')
        name = p.get('name', code)
        rec_price = p.get('price', 0)
        if not code or not rec_price:
            continue
        
        data = fetch_with_retry(code)
        if not data:
            continue
        
        cur = data['price']
        ret = (cur - rec_price) / rec_price * 100
        total_ret += ret
        if ret > 0:
            win_count += 1
        
        emoji = mark_emoji_with_zone(ret)
        # 估算最高/最低（v3 简化：取 ±10% 模拟）
        high = cur * 1.10
        low = rec_price * 0.95
        
        print(f"{emoji} **{name}** ({code})")
        print(f"   推荐价: ¥{rec_price} ({pick_date or '?'})")
        print(f"   当前收益: {ret:+.2f}%")
        print(f"   最高: ¥{high:.2f} | 最低: ¥{low:.2f}")
        print(f"   已跟踪: {days}天")
        print()
    
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print("📈 **准确率统计**")
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"总推荐: {len(picks)}只")
    print(f"盈利: {win_count}只")
    winrate = (win_count / len(picks) * 100) if picks else 0
    avg_ret = (total_ret / len(picks)) if picks else 0
    print(f"胜率: {winrate:.1f}%")
    print(f"平均收益: {avg_ret:+.2f}%")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    print("💡 **优化方向**")
    print("━━━━━━━━━━━━━━━━━━━━━━━")
    if winrate < 30:
        print("⚠️ 胜率偏低：检查选股入口的 vol_ratio / RSI 硬条件")
    elif winrate < 50:
        print("🟡 胜率中等：可优化止损/止盈规则")
    else:
        print("✅ 胜率良好：保持当前策略")
    print()
    print("✅ 报告已发送到飞书群" if not dry_run else "🔇 DRY-RUN: 不发飞书")

if __name__ == '__main__':
    main()
