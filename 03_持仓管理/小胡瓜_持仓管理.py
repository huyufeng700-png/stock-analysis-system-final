#!/usr/bin/env python3
"""
小胡瓜量化助手 - 持仓管理workflow
功能：
1. 持仓实时诊断（浮亏/浮盈）
2. 持仓预警（亏损超限提醒）
3. 操作建议（补仓/止损/持有）
4. 每日持仓报告

用法：
    python 小胡瓜_持仓管理.py          # 全流程诊断
    python 小胡瓜_持仓管理.py --report  # 生成今日报告
    python 小胡瓜_持仓管理.py --alert  # 检查预警
"""

import os
import sys
import urllib.request
import ssl
import json
from datetime import datetime
from typing import List, Dict, Optional

# ==================== 配置 ====================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"

# 持仓配置
MY_POSITIONS = [
    {"code": "300059", "name": "东方财富", "shares": 300, "cost": 20.563},
    # 金地集团(600383)已清仓 2026-05-27，不再跟踪
]

# 止损配置
STOP_LOSS_THRESHOLD = -15  # 亏损15%预警
PROFIT_TAKE_THRESHOLD = 10  # 盈利10%考虑止盈

# ==================== 行情获取 ====================

def get_realtime_price(code: str) -> Optional[Dict]:
    """获取个股实时价格"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    prefix = 'sz' if code.startswith('3') else 'sh'
    url = f'https://qt.gtimg.cn/q={prefix}{code}'
    
    try:
        with urllib.request.urlopen(url, timeout=5, context=ctx) as r:
            data = r.read().decode('gbk')
            fields = data.split('~')
            if len(fields) > 39:
                return {
                    'name': fields[1],
                    'price': float(fields[3]),
                    'yesterday_close': float(fields[4]),
                    'open': float(fields[5]),
                    'volume': int(fields[6]),  # 手
                    'change': float(fields[31]),
                    'change_pct': float(fields[32]),
                    'high': float(fields[33]),
                    'low': float(fields[34]),
                    'amount': float(fields[38]),  # 亿元
                    'pe': float(fields[39]) if fields[39] and fields[39] != '-' else 0,
                    'time': fields[30],
                }
    except Exception as e:
        print(f"获取{code}价格失败: {e}")
    return None

def get_batch_prices(codes: List[str]) -> List[Dict]:
    """批量获取股票价格"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    if not codes:
        return []
    
    # 构造带前缀的代码
    codes_with_prefix = []
    for code in codes:
        prefix = 'sz' if code.startswith('3') else 'sh'
        codes_with_prefix.append(f'{prefix}{code}')
    
    url = 'https://qt.gtimg.cn/q=' + ','.join(codes_with_prefix)
    try:
        with urllib.request.urlopen(url, timeout=10, context=ctx) as r:
            data = r.read().decode('gbk')
            stocks = data.strip().split('\n')
            
            results = []
            for stock in stocks:
                if not stock.strip():
                    continue
                eq_pos = stock.find('=')
                if eq_pos < 0:
                    continue
                raw = stock[eq_pos+1:]
                fields = raw.split('~')
                
                if len(fields) > 39:
                    code_raw = fields[2]
                    name = fields[1]
                    price = float(fields[3])
                    yesterday_close = float(fields[4])
                    change = float(fields[31])
                    change_pct = float(fields[32])
                    amount = float(fields[38]) if fields[38] else 0
                    pe = float(fields[39]) if fields[39] and fields[39] != '-' else 0
                    
                    results.append({
                        'code': code_raw,
                        'name': name,
                        'price': price,
                        'yesterday_close': yesterday_close,
                        'change': change,
                        'change_pct': change_pct,
                        'amount': amount,
                        'pe': pe,
                        'time': fields[30],
                    })
            return results
    except Exception as e:
        print(f"批量获取失败: {e}")
    return []

# ==================== 持仓诊断 ====================

def diagnose_positions() -> str:
    """诊断所有持仓"""
    msg = "📊 **持仓诊断报告**\n\n"
    msg += f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
    
    total_unrealized = 0
    total_cost = 0
    alerts = []
    
    # 批量获取价格
    codes = [p['code'] for p in MY_POSITIONS]
    prices = {p['code']: p for p in get_batch_prices(codes)}
    
    for pos in MY_POSITIONS:
        code = pos['code']
        name = pos['name']
        shares = pos['shares']
        cost = pos['cost']
        
        price_data = prices.get(code)
        if not price_data:
            msg += f"⚠️ **{name}** ({code}) - 价格获取失败\n\n"
            continue
            
        current_price = price_data['price']
        change_pct = price_data['change_pct']
        market_value = current_price * shares
        cost_value = cost * shares
        unrealized = market_value - cost_value
        unrealized_pct = (unrealized / cost_value) * 100
        total_unrealized += unrealized
        total_cost += cost_value
        
        # 状态图标
        if unrealized >= 0:
            status = "🟢 盈利"
        elif unrealized_pct > -5:
            status = "🟡 微亏"
        elif unrealized_pct > -15:
            status = "🟠 亏损"
        else:
            status = "🔴 深套"
        
        msg += f"{status} **{name}** ({code})\n"
        msg += f"   现价: {current_price:.2f} | 成本: {cost:.2f}\n"
        msg += f"   数量: {shares}股 | 市值: {market_value:.0f}元\n"
        msg += f"   浮亏: {unrealized:+.0f}元 ({unrealized_pct:+.2f}%)\n"
        msg += f"   今日: {change_pct:+.2f}%\n"
        
        # 操作建议
        advice = get_advice(name, cost, current_price, unrealized_pct, price_data)
        msg += f"   {advice}\n\n"
        
        # 预警
        if unrealized_pct < -15:
            alerts.append(f"⚠️ {name}亏损{unrealized_pct:.1f}%，建议止损")
        elif unrealized_pct > 10:
            alerts.append(f"✅ {name}盈利{unrealized_pct:.1f}%，考虑止盈")
    
    # 总览
    if total_cost > 0:
        total_pct = (total_unrealized / total_cost) * 100
        msg += "─" * 40 + "\n"
        msg += f"**总浮亏: {total_unrealized:+.0f}元 ({total_pct:+.2f}%)**\n"
        msg += f"**持仓市值: {total_cost + total_unrealized:.0f}元**\n"
    
    # 预警汇总
    if alerts:
        msg += "\n🚨 **预警提醒**\n"
        for a in alerts:
            msg += f"{a}\n"
    
    return msg

def get_advice(name: str, cost: float, current: float, unrealized_pct: float, data: Dict) -> str:
    """给出操作建议"""
    # 计算止损位和目标位
    stop_loss = cost * 0.90  # 10%止损
    target_1 = cost * 1.10   # 10%目标
    target_2 = cost * 1.20   # 20%目标
    
    if unrealized_pct < -15:
        return f"⚠️ 亏损超过15%，建议止损位{stop_loss:.2f}"
    elif unrealized_pct < -10:
        return f"📍 接近止损位，建议关注{stop_loss:.2f}"
    elif unrealized_pct < -5:
        return f"📍 谨慎持有，等待反弹"
    elif unrealized_pct > 15:
        return f"🎯 盈利丰厚，建议分批止盈（目标{target_1:.2f}/{target_2:.2f}）"
    elif unrealized_pct > 10:
        return f"💰 盈利超过10%，考虑部分止盈"
    else:
        return f"✅ 正常持有"

# ==================== AI深度分析 ====================

def ai_analyze_position(code: str, name: str, shares: int, cost: float) -> str:
    """用AI深度分析持仓"""
    price_data = get_realtime_price(code)
    if not price_data:
        return "价格获取失败"
    
    current = price_data['price']
    unrealized_pct = ((current - cost) / cost) * 100
    market_value = current * shares
    cost_value = cost * shares
    unrealized = market_value - cost_value
    
    prompt = f"""你是一个专业的股票投资顾问。请分析以下持仓：

**持仓信息：**
- 股票：{name}（{code}）
- 持仓数量：{shares}股
- 成本价：{cost:.2f}元
- 当前价：{current:.2f}元
- 浮亏：{unrealized:.0f}元（{unrealized_pct:+.2f}%）
- 今日涨跌：{price_data['change_pct']:+.2f}%
- 成交额：{price_data['amount']:.1f}亿
- 市盈率：{price_data['pe']}

**请给出：**
1. 简短的持仓评估（1-2句话）
2. 操作建议（补仓/持有/减仓/止损）
3. 关键价位（支撑位、压力位）
4. 止损位和止盈位建议

请用简洁易懂的语言，适合普通投资者理解。"""
    
    try:
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"AI分析失败: {e}"

def analyze_all_positions():
    """AI深度分析所有持仓"""
    print("\n" + "=" * 60)
    print("🤖 AI深度分析持仓...\n")
    
    for pos in MY_POSITIONS:
        print(f"分析 {pos['name']}...")
        result = ai_analyze_position(
            pos['code'], pos['name'], pos['shares'], pos['cost']
        )
        print(f"\n📊 **{pos['name']} AI分析**\n{result}\n")
        print("-" * 40)

# ==================== 预警检查 ====================

def check_alerts() -> List[str]:
    """检查持仓预警"""
    alerts = []
    codes = [p['code'] for p in MY_POSITIONS]
    prices = {p['code']: p for p in get_batch_prices(codes)}
    
    for pos in MY_POSITIONS:
        code = pos['code']
        name = pos['name']
        shares = pos['shares']
        cost = pos['cost']
        
        price_data = prices.get(code)
        if not price_data:
            continue
            
        current = price_data['price']
        unrealized_pct = ((current - cost) / cost) * 100
        
        # 止损预警
        if unrealized_pct < -15:
            alerts.append(f"🔴 【止损预警】{name}亏损{unrealized_pct:.1f}%，建议考虑止损")
        elif unrealized_pct < -10:
            alerts.append(f"🟠 【关注】{name}亏损{unrealized_pct:.1f}%，接近止损线")
        
        # 止盈提示
        if unrealized_pct > 15:
            alerts.append(f"🟢 【止盈提示】{name}盈利{unrealized_pct:.1f}%，建议分批止盈")
        
        # 今日大跌预警
        if price_data['change_pct'] < -3:
            alerts.append(f"📉 【异动】{name}今日下跌{price_data['change_pct']:.1f}%，注意风险")
    
    return alerts

def show_alerts():
    """显示预警"""
    alerts = check_alerts()
    if alerts:
        print("\n🚨 **持仓预警**\n")
        for a in alerts:
            print(a)
    else:
        print("\n✅ 暂无预警，持仓正常")

# ==================== 主报告 ====================

def generate_daily_report() -> str:
    """生成每日持仓报告"""
    report = f"""
📊 **小胡瓜每日持仓报告**
{datetime.now().strftime('%Y年%m月%d日 %H:%M')}

{diagnose_positions()}

---
💡 *本报告仅供参考，不构成投资建议*
"""
    return report

# ==================== 入口 ====================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        
        if arg == '--report':
            print(generate_daily_report())
        elif arg == '--alert':
            show_alerts()
        elif arg == '--ai':
            analyze_all_positions()
        else:
            print("未知参数")
            print("用法：")
            print("  python 小胡瓜_持仓管理.py          # 全流程")
            print("  python 小胡瓜_持仓管理.py --report # 每日报告")
            print("  python 小胡瓜_持仓管理.py --alert  # 预警检查")
            print("  python 小胡瓜_持仓管理.py --ai     # AI深度分析")
    else:
        # 全流程
        print(generate_daily_report())
