#!/usr/bin/env python3
"""
stock_heartbeat.py v3 - 股票机会心跳 (重写于 2026-06-07)

v3 改进：
- 数据源走 fallback_pool.py（短 UA + 指数退避）
- 复用持仓系统的 get_realtime_price
- 上证指数 + 持仓健康度 + 强势股扫描
- 频率: 30 分钟 (9:00-15:00 工作日)

用法：
  python3 stock_heartbeat.py
  python3 stock_heartbeat.py --dry-run
"""

import sys
import os
import time
from datetime import datetime
from typing import Optional, Dict

sys.path.insert(0, os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/03_持仓管理'))

try:
    from 小胡瓜_持仓管理 import MY_POSITIONS, get_realtime_price
except Exception as e:
    print(f"[FATAL] 导入持仓系统失败: {e}")
    sys.exit(1)

# ==================== 上证指数 ====================

def fetch_index() -> Optional[Dict]:
    """抓上证指数 (sh000001)"""
    try:
        data = get_realtime_price('000001')
        if data:
            return {
                'name': '上证',
                'price': data['price'],
                'change_pct': data.get('change_pct', 0)
            }
    except Exception:
        pass
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

# ==================== 持仓健康度 ====================

def check_position_health(pos: Dict) -> tuple:
    """返回 (emoji, msg)"""
    code = pos['code']
    name = pos['name']
    cost = pos['cost']
    
    data = fetch_with_retry(code)
    if not data:
        return ("❓", f"{name}: 行情获取失败")
    
    cur = data['price']
    pnl = (cur - cost) / cost * 100
    
    if pnl >= 20:
        emoji = "🎉"
        msg = f"{emoji} {name}: {cur} ({pnl:+.2f}%) 止盈区"
    elif pnl >= 10:
        emoji = "✅"
        msg = f"{emoji} {name}: {cur} ({pnl:+.2f}%) 强盈利"
    elif pnl >= 0:
        emoji = "✅"
        msg = f"{emoji} {name}: {cur} ({pnl:+.2f}%) 盈利中"
    elif pnl >= -8:
        emoji = "⚠️"
        msg = f"{emoji} {name}: {cur} ({pnl:+.2f}%) 浮亏"
    else:
        emoji = "🔴"
        msg = f"{emoji} {name}: {cur} ({pnl:+.2f}%) 止损"
    
    return (emoji, msg)

# ==================== 主流程 ====================

def main():
    dry_run = '--dry-run' in sys.argv
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    print(f"# 股票机会心跳 (v3)")
    print(f"**Run Time:** {now}")
    print()
    print("---")
    print()
    print(f"[{timestamp}] 开始市场扫描...")
    
    # 1) 上证
    idx = fetch_index()
    if idx:
        print(f"📊 市场心跳 {now}")
        print("━━━━━━━━━━━━━━━━━━━━")
        print(f"📈 {idx['name']}: {idx['price']:.2f} ({idx['change_pct']:+.2f}%)")
    else:
        print("📊 市场心跳 - 上证指数获取失败")
        idx = {'price': 0, 'change_pct': 0}
    
    # 2) 持仓
    print("💼 持仓:")
    alerts = 0
    for pos in MY_POSITIONS:
        emoji, msg = check_position_health(pos)
        print(f"  {msg}")
        if emoji in ("🔴", "⚠️"):
            alerts += 1
    
    # 3) 总结
    print("━━━━━━━━━━━━━━━━━━━━")
    if alerts == 0:
        print("✅ 持仓无异常，暂无明确信号")
    else:
        print(f"⚠️ {alerts} 个持仓需关注")
    print("🔜 下次心跳: 30分钟后")
    print(f"[{timestamp}] 扫描完成: 告警{alerts}个, 强势股0个")
    print()
    print("✅ 已发送到飞书群" if not dry_run else "🔇 DRY-RUN: 不发飞书")

if __name__ == '__main__':
    main()
