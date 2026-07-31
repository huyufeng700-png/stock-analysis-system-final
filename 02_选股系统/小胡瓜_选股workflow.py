#!/usr/bin/env python3
"""
小胡瓜量化助手 - 选股workflow v2
串接：热点舆情 → 量化筛选 → 个股分析 → 推荐输出

用法：
    python 小胡瓜_选股workflow.py          # 全流程
    python 小胡瓜_选股workflow.py --step1  # 只跑热点
    python 小胡瓜_选股workflow.py --step2  # 只跑筛选
    python 小胡瓜_选股workflow.py --pos    # 持仓诊断
    python 小胡瓜_选股workflow.py 东方财富  # 分析个股
"""

import requests
import feedparser
import re
import json
import os
import sys
import urllib.request
import ssl
from datetime import datetime
from typing import List, Dict, Optional
# 07-31 重构: 跨目录 import 01_策略引擎 + scripts
_KB_ROOT = os.path.expanduser("~/Documents/股票分析知识库")
for _p in [os.path.join(_KB_ROOT, "01_策略引擎"), os.path.join(_KB_ROOT, "scripts")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)
from 五层确认框架 import FiveLayerValidator, get_score_emoji
from 研究档案 import ResearchArchive, ResilientWorkflow, StockRAG
from 九条经验规则 import NineRuleEngine, nine_rules_to_fivelayer

# ==================== 配置 ====================
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
}
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
OUTPUT_DIR = "/Users/huyufeng/Documents/股票分析知识库/runtime/picks/"

# 持仓配置（2026-05-27更新：金地已清仓）
MY_POSITIONS = [
    {"code": "300059", "name": "东方财富", "shares": 300, "cost": 20.563},
    # 金地集团(600383)已清仓 2026-05-27
]

# 预定义热门股池（10-20元区间）
STOCK_POOL = {
    'sh601677': '明泰铝业',
    'sh600601': '方正科技', 
    'sz000967': '盈峰环境',
    'sh600416': '湘电股份',
    'sz002328': '新朋股份',
    'sz002171': '楚江新材',
    'sz002125': '湘潭电化',
    'sz000922': '佳电股份',
    'sz300416': '苏试试验',
    'sh601609': '金田股份',
    'sz000555': '神州信息',
    'sz300319': '麦捷科技',
    'sz002815': '崇达技术',
    'sh603002': '宏昌电子',
    'sz300283': '温州宏丰',
}

# ==================== 工具函数 ====================

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
            if len(fields) > 34:
                return {
                    'price': float(fields[3]),
                    'change': float(fields[31]),
                    'change_pct': float(fields[32]),
                    'high': float(fields[33]),
                    'low': float(fields[34]),
                    'amount': float(fields[38]),  # 亿元
                    'pe': float(fields[39]) if fields[39] and fields[39] != '-' else 0,
                }
    except Exception as e:
        print(f"获取{code}价格失败: {e}")
    return None

def get_batch_prices(codes: List[str]) -> List[Dict]:
    """批量获取股票价格"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    
    url = 'https://qt.gtimg.cn/q=' + ','.join(codes)
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
                    code_raw = fields[2]  # 如 601677
                    name = fields[1]
                    price = float(fields[3])
                    change_pct = float(fields[32])
                    amount = float(fields[38]) if fields[38] else 0
                    pe = float(fields[39]) if fields[39] and fields[39] != '-' else 0
                    
                    results.append({
                        'code': code_raw,
                        'name': name,
                        'price': price,
                        'change_pct': change_pct,
                        'amount': amount,
                        'pe': pe,
                    })
            return results
    except Exception as e:
        print(f"批量获取失败: {e}")
    return []

# ==================== 步骤1: 热点舆情 ====================

def fetch_hotspots() -> Dict:
    """获取全网热点"""
    results = {'newsnow': [], 'github': [], 'sopilot': []}
    
    # NewsNow知乎热点
    try:
        resp = requests.get('https://newsnow.busiyi.world/', headers=HEADERS, timeout=10)
        zhihu_pattern = r'href=\"(https://www.zhihu.com/question/\d+)\"[^>]*>([^<]+)<.*?(\d+\.?\d*)\s*万热度'
        matches = re.findall(zhihu_pattern, resp.text)
        for url, title, heat in matches[:10]:
            results['newsnow'].append({'title': title.strip(), 'heat': f'{heat}万', 'url': url})
    except Exception as e:
        print(f"NewsNow获取失败: {e}")
    
    # GitHub Trending
    try:
        resp = requests.get('https://tophub.today/n/rYqoXQ8vOD', headers=HEADERS, timeout=10)
        pattern = r'href=\"(https://github.com/[^\"]+)\"[^>]*>([^<]+)<.*?(\d+\.?\d*[万kK]?)'
        matches = re.findall(pattern, resp.text)
        for url, name, stars in matches[:10]:
            name = name.strip().replace(' ', '')
            if 'github.com' in url and '/' in name:
                results['github'].append({'name': name, 'stars': stars, 'url': url})
    except Exception as e:
        print(f"GitHub Trending获取失败: {e}")
    
    # SoPilot X爆帖
    try:
        feed = feedparser.parse('https://sopilot.net/rss/hottweets')
        for entry in feed.entries[:10]:
            results['sopilot'].append({'title': entry.get('title', '')[:80], 'url': entry.get('link', '')})
    except Exception as e:
        print(f"SoPilot获取失败: {e}")
    
    return results

# ==================== 步骤2: 量化筛选 ====================

def screen_stocks(price_min=10, price_max=20, amount_min=2, top_n=10) -> List[Dict]:
    """量化筛选股票"""
    codes = list(STOCK_POOL.keys())
    stocks = get_batch_prices(codes)
    
    # 筛选
    filtered = [
        s for s in stocks 
        if price_min <= s['price'] <= price_max 
        and s['amount'] >= amount_min 
        and s['pe'] > 0
    ]
    filtered.sort(key=lambda x: (x['amount'], x['change_pct']), reverse=True)
    return filtered[:top_n]


# ==================== 步骤2.5: 九条经验评分 ====================

def run_step25_nine_rules(cost_map: None) -> str:
    """步骤2.5: 用九条经验规则评估所有候选股票"""
    engine = NineRuleEngine()
    
    # 评估预定义股票池
    codes = [c.lstrip('shsz') for c in STOCK_POOL.keys()]
    # 加上持仓
    for pos in MY_POSITIONS:
        if pos['code'] not in codes:
            codes.append(pos['code'])
    
    results = engine.screen_all(codes, cost_map=cost_map)
    
    msg = "🧠 **九条经验综合评估**\\n\\n"
    
    for r in results:
        sig = r['composite_signal']
        emoji = {'BUY': '🟢', 'HOLD': '🟡', 'SELL': '🔴'}.get(sig, '⚪')
        price = r.get('realtime', {}).get('price', '?')
        name = r.get('realtime', {}).get('name', r['code'])
        score = r['total_score']
        
        msg += f"{emoji} **{name}({r['code']})** | {price}元 | 评分:{score:+d} | {sig}\\n"
        msg += f"   └ {r['composite_reason']}\\n"
        
        # 列出最强的2条和最弱的1条规则
        rule_scores = [(n, d['score'], d['signal']) for n, d in r['rules'].items()]
        rule_scores.sort(key=lambda x: x[1], reverse=True)
        
        top_rules = rule_scores[:2]
        bottom_rules = rule_scores[-1:]
        
        for rn, rs, rsg in top_rules:
            reg_emoji = {'BUY': '✅', 'HOLD': '➖', 'SELL': '❌'}.get(rsg, '')
            msg += f"   {reg_emoji} {rn}: {rs:+d}分\\n"
        
        if bottom_rules and bottom_rules[0][1] < 0:
            rn, rs, rsg = bottom_rules[0]
            msg += f"   ❌ {rn}: {rs:+d}分\\n"
        
        msg += "\\n"
    
    return msg

# ==================== 步骤3: 个股分析 ====================

def analyze_stock(code: str) -> Dict:
    """分析单只股票"""
    price_data = get_realtime_price(code)
    
    # 调用DeepSeek分析
    prompt = f"""你是一个专业的股票分析师。请分析股票代码{code}：

分析维度：
1. 当前走势（基于今日涨跌）
2. 技术面（压力位/支撑位）
3. 基本面（如知道）
4. 操作建议（买入/持有/止损）

请用简洁的方式输出，适合普通投资者理解。"""
    
    analysis = "（暂时无法分析）"
    try:
        from openai import OpenAI
        client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=500
        )
        analysis = response.choices[0].message.content
    except Exception as e:
        analysis = f"分析失败: {e}"
    
    return {'code': code, 'price_data': price_data, 'analysis': analysis}

# ==================== 持仓诊断 ====================

def diagnose_positions() -> str:
    """诊断我的持仓"""
    msg = "📊 **持仓诊断报告**\n\n"
    total_unrealized = 0
    total_cost = 0
    
    for pos in MY_POSITIONS:
        code = pos['code']
        name = pos['name']
        shares = pos['shares']
        cost = pos['cost']
        
        price_data = get_realtime_price(code)
        if price_data:
            current_price = price_data['price']
            change_pct = price_data['change_pct']
            market_value = current_price * shares
            cost_value = cost * shares
            unrealized = market_value - cost_value
            unrealized_pct = (unrealized / cost_value) * 100
            total_unrealized += unrealized
            total_cost += cost_value
            
            emoji = "🟢" if unrealized >= 0 else "🔴"
            msg += f"{emoji} **{name}** ({code})\n"
            msg += f"   现价: {current_price:.2f} | 成本: {cost:.2f}\n"
            msg += f"   数量: {shares}股 | 市值: {market_value:.0f}元\n"
            msg += f"   浮亏: {unrealized:.0f}元 ({unrealized_pct:+.2f}%)\n"
            
            # 简单建议
            if unrealized_pct < -15:
                msg += f"   ⚠️ 亏损超过15%，建议关注止损\n"
            elif unrealized_pct > 10:
                msg += f"   ✅ 盈利超过10%，考虑分批止盈\n"
            msg += "\n"
        else:
            msg += f"⚠️ {name} 价格获取失败\n\n"
    
    if total_cost > 0:
        msg += f"**总浮亏: {total_unrealized:.0f}元 ({total_unrealized/total_cost*100:+.2f}%)**\n"
    
    return msg

# ==================== 主流程 ====================

def run_step1_hotspots():
    """步骤1: 热点舆情"""
    hotspots = fetch_hotspots()
    msg = "🔥 **全网热点舆情**\n\n"
    
    if hotspots['newsnow']:
        msg += "📊 **知乎热点**\n"
        for i, h in enumerate(hotspots['newsnow'][:5], 1):
            msg += f"{i}. {h['title']} ({h['heat']})\n"
        msg += "\n"
    
    if hotspots['github']:
        msg += "💻 **GitHub Trending**\n"
        for i, h in enumerate(hotspots['github'][:5], 1):
            msg += f"{i}. {h['name']} ⭐{h['stars']}\n"
        msg += "\n"
    
    return msg

def run_step2_screen():
    """步骤2: 量化筛选"""
    stocks = screen_stocks(price_min=10, price_max=20, amount_min=2, top_n=10)
    validator = FiveLayerValidator()

    msg = "📈 **量化筛选结果**（10-20元，成交额>2亿）\n\n"

    if stocks:
        # 批量五层确认
        codes = [s['code'] for s in stocks]
        results = validator.batch_evaluate(codes)
        result_map = {r['code']: r for r in results}

        msg += "| 代码 | 名称 | 价格 | 涨幅 | 成交额 | PE | 评分 | 信号 |\n"
        msg += "|------|------|------|------|--------|----|-------|------|\n"
        for s in stocks:
            r = result_map.get(s['code'], {})
            score = r.get('score', 0)
            signal = r.get('signal', '-')
            emoji = get_score_emoji(score)
            signal_map = {'BUY': '🟢买', 'HOLD': '🟡持', 'SELL': '🔴卖'}
            signal_str = signal_map.get(signal, signal)
            msg += f"| {s['code']} | {s['name']} | {s['price']:.2f} | {s['change_pct']:+.2f}% | {s['amount']:.1f}亿 | {s['pe']:.1f} | {emoji}{score} | {signal_str} |\n"
    else:
        msg += "今日暂无符合条件的股票\n"

    return msg

def run_full_workflow():
    """完整workflow"""
    print("🚀 小胡瓜量化助手 - 选股workflow\n")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    
    hotspots_msg = run_step1_hotspots()
    screen_msg = run_step2_screen()
    nine_msg = run_step25_nine_rules(cost_map={p['code']: p['cost'] for p in MY_POSITIONS if p.get('cost')})
    positions_msg = diagnose_positions()
    
    print("=" * 60)
    print("📋 **今日选股简报**\n")
    print(hotspots_msg)
    print(screen_msg)
    print(nine_msg)
    print(positions_msg)
    
    # 保存到研究档案
    try:
        archive = ResearchArchive()
        workflow = ResilientWorkflow(archive)
        
        for pos in MY_POSITIONS:
            code = pos['code']
            price_data = get_realtime_price(code)
            if price_data:
                workflow.run_analysis(
                    stock_code=code,
                    price_data=price_data,
                    metadata={"source": "选股workflow", "date": datetime.now().strftime("%Y-%m-%d")}
                )
        
        print("\n📦 分析结果已存档到 研究档案")
    except Exception as e:
        print(f"\n存档失败: {e}")
    
    return {'hotspots': hotspots_msg, 'screen': screen_msg, 'positions': positions_msg}

def analyze_single_stock(name: str):
    """分析单只股票"""
    code_map = {
        '东方财富': '300059',  # '金地集团': '600383' 已清仓移除
        '明泰铝业': '601677', '方正科技': '600601',
        '盈峰环境': '000967', '湘电股份': '600416',
        '新朋股份': '002328', '楚江新材': '002171',
    }
    
    code = code_map.get(name)
    if not code:
        # 模糊匹配
        for full_name, c in code_map.items():
            if name in full_name or full_name in name:
                code = c
                name = full_name
                break
    
    if code:
        print(f"正在分析 {name} ({code})...")
        result = analyze_stock(code)
        print(f"\n{'='*60}")
        print(f"📊 **{name} 分析报告**\n")
        if result['price_data']:
            pd = result['price_data']
            print(f"现价: {pd['price']} ({pd['change_pct']:+.2f}%)")
            print(f"涨跌: {pd['change']:+.2f}")
            print(f"最高: {pd['high']} | 最低: {pd['low']}\n")
        print(result['analysis'])
    else:
        print(f"未找到股票: {name}")

# ==================== 入口 ====================

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == '--step1':
            print(run_step1_hotspots())
        elif arg == '--step2':
            print(run_step2_screen())
        elif arg == '--pos':
            print(diagnose_positions())
        else:
            analyze_single_stock(arg)
    else:
        run_full_workflow()
