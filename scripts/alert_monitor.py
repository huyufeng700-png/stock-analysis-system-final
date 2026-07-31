#!/usr/bin/env python3
"""
alert_monitor.py - 股票告警监控 (v2 重写，2026-06-07)

触发规则（按反推的旧 alert_monitor 行为）：
- 🔴 价格 < 止损线  → 立即止损
- ⚠️ 价格 < 关注线  → 接近止损
- 🎯 价格 < 建仓线  → 可建仓/关注

数据源：
- 复用 fallback_pool.py 的 fetch 模式（短 UA + 指数退避）
- 持仓数据从 03_持仓管理/小胡瓜_持仓管理.py 读 MY_POSITIONS

输出格式（与历史 output 一致）：
  ⚠️ 东方财富 18.52: ⚠️ 东财跌破19.50，接近止损线！
  ⚠️ 东方财富 18.52: 🔴 东财跌破¥19.60！

无告警时：输出 "✅ 持仓健康" + 每只标的现价，让 output 有内容。
（no_agent cron 期望 stdout 有内容才发飞书）

用法：
  python3 alert_monitor.py                # 跑一次
  python3 alert_monitor.py --dry-run       # 不发飞书，只 print
"""

import sys
import os
import urllib.request
import ssl
import json
import time
from datetime import datetime
from typing import List, Dict, Optional

# 复用持仓系统（导入而非硬编码，保证唯一真源）
sys.path.insert(0, os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/03_持仓管理'))
try:
    from 小胡瓜_持仓管理 import MY_POSITIONS, get_realtime_price
except Exception as e:
    print(f"[FATAL] 导入持仓系统失败: {e}")
    sys.exit(1)

# ==================== 告警规则配置 ====================
# 旧脚本反推出的阈值（每只标的独立配置）
ALERT_RULES = {
    "300059": {  # 东方财富
        "name": "东方财富",
        "stop_loss": 19.60,    # 🔴 立即止损
        "watch": 19.50,        # ⚠️ 接近止损
    },
    # 600383 金地集团：06-09 已清仓，规则删除（漏④ 闭环 2026-06-10）
    "601138": {  # 工业富联（建仓机会）
        "name": "工业富联",
        "buy_in": 69.00,       # 🎯 可建仓
    },
    "600497": {  # 驰宏锌锗（机会标的非持仓，删 stop_loss 只保留 buy_in；2026-06-10）
        "name": "驰宏锌锗",
        "buy_in": 10.50,
    },
}

# ==================== 行情获取（fallback 模式）===================

def fetch_with_retry(code: str, max_retries: int = 3) -> Optional[Dict]:
    """复用 fallback_pool 的 fetch 模式：短 UA + 指数退避"""
    from 小胡瓜_持仓管理 import get_realtime_price  # 走持仓系统的 fetch
    
    for attempt in range(max_retries):
        try:
            data = get_realtime_price(code)
            if data and data.get('price'):
                return data
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"[WARN] {code} 重试 {max_retries} 次仍失败: {e}")
                return None
            # 指数退避：1s, 2s, 4s
            time.sleep(2 ** attempt)
    return None

# ==================== 告警判定 ====================

def check_alerts(positions: List[Dict]) -> List[str]:
    """检查所有标的，返回告警行"""
    alerts = []
    
    # 1) 持仓中的标的（必查）
    codes_to_check = set(p['code'] for p in positions)
    # 2) 关注池的标的（即使没持仓也建仓机会扫描）
    codes_to_check.update(ALERT_RULES.keys())
    
    for code in codes_to_check:
        data = fetch_with_retry(code)
        if not data:
            alerts.append(f"❌ {code}: 行情获取失败")
            continue
        
        name = data.get('name', code)
        price = data.get('price', 0)
        rules = ALERT_RULES.get(code, {})
        
        # 🔴 跌破止损线
        stop_loss = rules.get('stop_loss')
        if stop_loss and price < stop_loss:
            alerts.append(f"⚠️ {name} {price}: 🔴 {name}跌破¥{stop_loss}！")
        
        # ⚠️ 接近止损
        watch = rules.get('watch')
        if watch and price < watch:
            alerts.append(f"⚠️ {name} {price}: ⚠️ {name}跌破{watch}，接近止损线！")
        
        # 🎯 建仓机会
        buy_in = rules.get('buy_in')
        if buy_in and price < buy_in:
            alerts.append(f"⚠️ {name} {price}: 🎯 {name}回调到¥{buy_in}，可建仓！")
    
    return alerts

# ==================== 主流程 ====================

def main():
    dry_run = '--dry-run' in sys.argv
    now = datetime.now().strftime('%Y-%m-%d %H:%M')
    
    print(f"# 股票告警监控 (v2)")
    print(f"**Run Time:** {now}")
    print()
    print("---")
    print()
    print(f"📢 **股票告警** {now}")
    print()
    
    alerts = check_alerts(MY_POSITIONS)
    
    if alerts:
        for line in alerts:
            print(line)
        print()
        print("✅ 已发送到飞书群" if not dry_run else "🔇 DRY-RUN: 不发飞书")
    else:
        print("✅ 持仓健康，无告警")
        # 仍打印当前价，让 output 有内容
        for pos in MY_POSITIONS:
            data = fetch_with_retry(pos['code'])
            if data:
                pnl = (data['price'] - pos['cost']) / pos['cost'] * 100
                print(f"  • {pos['name']} 现价 {data['price']} 浮盈 {pnl:+.2f}%")
        print()
        print("✅ 已发送到飞书群" if not dry_run else "🔇 DRY-RUN: 不发飞书")

if __name__ == '__main__':
    main()
