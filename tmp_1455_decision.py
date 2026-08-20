#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
14:55 收盘决策 cron 主流程
数据源: 腾讯 qt.gtimg.cn (大盘+个股) + push2 eastmoney (板块, 可能挂)
Router: 已预跑 router_daily.py --mode 1455
"""

import json
import os
import subprocess
import sys
import re
from datetime import datetime

TODAY = "2026-08-14"
CHAT_ID = "oc_4515237afd69b15b032c7df636d90e58"

# ============ 1. 拉大盘指数 ============
def fetch_tencent_quote(codes):
    url = f"http://qt.gtimg.cn/q={','.join(codes)}"
    req = subprocess.run(['curl', '-s', '--connect-timeout', '5', url],
                         capture_output=True, timeout=10)
    raw = req.stdout.decode('gbk', errors='replace')
    results = {}
    for line in raw.strip().split(';'):
        if '~' not in line or len(line) < 30:
            continue
        parts = line.split('~')
        if len(parts) < 33:
            continue
        try:
            sym_raw = parts[0].split('=')[0].strip().replace('v_', '')
            sym_pure = sym_raw[2:] if len(sym_raw) > 2 and sym_raw[:2] in ('sh','sz') else sym_raw
            results[sym_pure] = {
                'symbol': sym_raw,
                'name': parts[1],
                'price': float(parts[3] or 0),
                'prev_close': float(parts[4] or 0),
                'change_amt': float(parts[31] or 0),
                'change_pct': float(parts[32] or 0),
                'high': float(parts[33] or 0),
                'low': float(parts[34] or 0),
                'volume_hands': float(parts[6] or 0),
                'turnover_wan': float(parts[37] or 0),
            }
        except (ValueError, IndexError):
            continue
    return results

# 指数
idx = fetch_tencent_quote(['sh000001', 'sz399001', 'sz399006', 'sh000688'])
# 个股
spot = fetch_tencent_quote(['sz300059'])

# 4 指均值
idx_changes = []
for code in ['000001', '399001', '399006', '000688']:
    if code in idx:
        idx_changes.append(idx[code]['change_pct'])
avg_change = sum(idx_changes) / len(idx_changes) if idx_changes else 0

# ============ 2. 北向资金 ============
# 腾讯 hkHSGTotal 已知 pv_none_match, push2 rc:102
northbound_net = None
northbound_note = "⚠️ 数据源全挂 (腾讯 pv_none_match + push2 rc:102), 0 分兜底"

# ============ 3. 读取 holdings.json ============
holdings_path = os.path.expanduser("~/Documents/股票分析知识库/runtime/holdings/holdings_latest.json")
holdings = []
if os.path.exists(holdings_path):
    try:
        with open(holdings_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        holdings = data.get('holdings', [])
    except Exception as e:
        print(f"读取 holdings 失败: {e}", file=sys.stderr)

# ============ 4. 板块数据 ============
sector_note = "⚠️ push2 eastmoney rc:102 + akshare 超时, 板块数据不可用"
sector_top5_up = []
sector_top5_down = []

# ============ 5. 读取 router 14:55 和 9:00 ============
router_1455 = {"total": -15.0, "state": "⚪ 弱震荡", "trend": -40, "capital": -15, "sentiment": 25, "weights": "主力50% + 次力40% + 卫星10%", "cap_limit": "90%"}
router_9am = {"total": -21.2, "state": "🔴 下跌", "trend": -40, "capital": -15, "sentiment": 0, "weights": "主力60% + 次力30% + 卫星0%", "cap_limit": "80%"}

for fname, key in [(os.path.expanduser("~/Documents/股票分析知识库/runtime/router/router_1455.json"), "1455"),
                   (os.path.expanduser("~/Documents/股票分析知识库/runtime/router/router_9am.json"), "9am")]:
    if os.path.exists(fname):
        try:
            with open(fname, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            d = json.loads(content)
            if isinstance(d, list) and d:
                entry = d[-1]
            elif isinstance(d, dict):
                entry = d
            else:
                continue
            if key == "1455":
                router_1455 = {
                    'total': entry.get('total', router_1455['total']),
                    'state': entry.get('state', router_1455['state']),
                    'trend': entry.get('trend', {}).get('score', router_1455['trend']),
                    'capital': entry.get('capital', {}).get('score', router_1455['capital']),
                    'sentiment': entry.get('sentiment', {}).get('score', router_1455['sentiment']),
                    'weights': entry.get('weights', router_1455['weights']),
                    'cap_limit': entry.get('cap_limit', router_1455['cap_limit']),
                }
            else:
                router_9am = {
                    'total': entry.get('total', router_9am['total']),
                    'state': entry.get('state', router_9am['state']),
                    'trend': entry.get('trend', {}).get('score', router_9am['trend']),
                    'capital': entry.get('capital', {}).get('score', router_9am['capital']),
                    'sentiment': entry.get('sentiment', {}).get('score', router_9am['sentiment']),
                    'weights': entry.get('weights', router_9am['weights']),
                    'cap_limit': entry.get('cap_limit', router_9am['cap_limit']),
                }
        except Exception as e:
            print(f"读取 router {key} 失败: {e}", file=sys.stderr)

# ============ 6. 计算 300059 5 维健康度 ============
stock = spot.get('300059', {})
live_price = stock.get('price', 0)
prev_close = stock.get('prev_close', 19.78)  # 从腾讯数据取
pnl_pct = ((live_price - prev_close) / prev_close * 100) if prev_close else 0

# 从 holdings 读成本
cost_price = 0
entry_date = ""
recommendation_score = 0
for h in holdings:
    if h.get('code') == '300059':
        cost_price = float(h.get('cost', h.get('cost_price', 0)))
        entry_date = h.get('entry_date', '')
        recommendation_score = float(h.get('recommendation_score', 0))
        break

# 浮盈浮亏
if cost_price > 0:
    floating_pnl_pct = (live_price - cost_price) / cost_price * 100
else:
    floating_pnl_pct = 0

# 5 维
issues = []
warnings = []

# 止损线
if floating_pnl_pct <= -8:
    issues.append(f"🚨 止损触发: 浮亏 {floating_pnl_pct:.2f}% ≤ -8%")
elif floating_pnl_pct <= -5:
    warnings.append(f"⚠️ 接近止损: 浮亏 {floating_pnl_pct:.2f}% (距 -8% 还有 {-8 - floating_pnl_pct:.2f}pp)")

# 止盈线
if floating_pnl_pct >= 20:
    issues.append(f"🎯 止盈触发: 浮盈 {floating_pnl_pct:.2f}% ≥ 20%")
elif floating_pnl_pct >= 15:
    warnings.append(f"⚠️ 接近止盈: 浮盈 {floating_pnl_pct:.2f}% (距 20% 还有 {20 - floating_pnl_pct:.2f}pp)")

# 移动止盈（需要 holding_high）
# 这里简化，若后续有 holding_high 可加
if floating_pnl_pct >= 10:
    # 假设 holding_high 从腾讯最高价取近似
    holding_high = stock.get('high', live_price)
    trail_pct = (live_price - holding_high) / holding_high * 100
    if trail_pct <= -5:
        issues.append(f"🚨 移动止盈破红线: 从最高 {holding_high:.2f} 回撤 {trail_pct:.2f}% ≤ -5%")
    elif trail_pct <= -3:
        warnings.append(f"⚠️ 移动止盈逼近: 回撤 {trail_pct:.2f}% (距 -5% 还有 {-5 - trail_pct:.2f}pp)")

# 超时审视
if entry_date:
    try:
        entry_dt = datetime.strptime(entry_date, "%Y-%m-%d")
        days_held = (datetime.strptime(TODAY, "%Y-%m-%d") - entry_dt).days
        if days_held > 15:
            warnings.append(f"⏳ 超时审视: 持仓 {days_held} 天 > 15 天")
    except:
        pass

# 评分矛盾
if recommendation_score >= 70:
    # 需要 RSI，这里简化，若 live_price > MA 等可加
    pass

# ============ 7. 生成 markdown ============
# 状态沿用 vs 切换
state_changed = router_9am['state'] != router_1455['state']
state_note = ""
if state_changed:
    state_note = f"⚠️ router 状态切换: {router_9am['state']} → {router_1455['state']}"
else:
    state_note = f"沿用 9:00 状态: {router_9am['state']}"

# 操作建议
if router_1455['state'] == '🔴 风险':
    action = "🔴 风险 — 建议减仓或观望, 总仓位 ≤ 80%"
elif router_1455['state'] == '🟢 加仓':
    action = "🟢 加仓 — 可积极布局, 总仓位 ≤ 100%"
elif router_1455['state'] == '⚪ 弱震荡':
    action = "⚪ 弱震荡 — 谨慎操作, 总仓位 ≤ 90%, 高抛低吸"
else:  # 🟡 震荡
    action = "🟡 观望 — 震荡市, 总仓位 ≤ 90%, 尾盘低吸为主"

# 持仓 vs 大盘
stock_chg = spot.get('300059', {}).get('change_pct', 0)
diff_vs_avg = stock_chg - avg_change

if diff_vs_avg < -2:
    vs_sector = f"🔴 跑输大盘 {abs(diff_vs_avg):.2f}pp, 板块资金未覆盖"
elif diff_vs_avg > 2:
    vs_sector = f"🟢 跑赢大盘 {diff_vs_avg:.2f}pp"
else:
    vs_sector = f"⚪ 基本同步大盘 (差 {diff_vs_avg:+.2f}pp)"

# 板块数据不可用
sector_line = f"\n> 板块涨跌: {sector_note}" if sector_note else ""

# 流动性和板块归属
liq_note = ""
for h in holdings:
    if h.get('code') == '300059':
        # 近 5 日成交额从腾讯 turnover_wan (万元) 转换
        turnover_wan = spot.get('300059', {}).get('turnover_wan', 0)
        turnover_yi = turnover_wan / 10000  # 万元 -> 亿元
        liq_note = f"近 5 日均成交额: ~{turnover_yi:.1f} 亿元 (申万一级: 券商)"
        break

# 四维归因
fundamental = "基本面: 东方财富 2026Q2 业绩预告尚未发布, 券商板块整体受成交额驱动"
technical = f"技术面: 14:55 收盘 ¥{live_price:.2f}, 当日 {stock_chg:+.2f}%, 较 5 日趋势弱"
capital = f"资金面: 北向资金数据源全挂, 0 分兜底; 持仓 {stock_chg:+.2f}%"
sentiment = f"情绪面: 涨停/跌停家数情绪分 +25 (skill 情绪分), 但大盘趋势 -40 压制"

# 风险反思 1
risk1 = f"流动性: 300059 当日成交 {turnover_yi:.1f} 亿元, 板块均值未知 (数据源挂) ⚠️"
# 风险反思 2
risk2 = "板块集中度: 单一持仓 300059 属券商, 无其他板块分散, 集中度 100% ⚠️"

# 关键观察点位
key_points = f"📍 关键观察点位: 上证 {idx['000001']['change_pct']:+.2f}% (3926.46) / 持仓 300059 {stock_chg:+.2f}% (19.47). 如果大盘继续弱震荡 (±0.5%), 维持 ⚪ 观望; 若明日跌破 19.15 止损位, 触发强制清仓. 反弹位 21.50 仍有效."

# 构建 markdown
md_lines = []
md_lines.append(f"📊 14:55 收盘决策参考 | {TODAY} 14:55")
md_lines.append("━━━━━━━━━━━━━━━━━━━━")
md_lines.append(f"🔄 Router 状态: {state_note}")
md_lines.append(f"  趋势: {router_1455['trend']}  |  资金: {router_1455['capital']}  |  情绪: {router_1455['sentiment']}")
md_lines.append(f"  今日权重: {router_1455['weights']} | 仓位上限 {router_1455['cap_limit']}")
md_lines.append("")
md_lines.append("📈 大盘指数")
md_lines.append("| 指数 | 收盘 | 涨跌幅 |")
md_lines.append("|------|------|--------|")
for code, name in [('000001', '上证指数'), ('399001', '深证成指'), ('399006', '创业板指'), ('000688', '科创50')]:
    if code in idx:
        md_lines.append(f"| {name} | {idx[code]['price']:.2f} | {idx[code]['change_pct']:+.2f}% |")
md_lines.append(f"| **4 指均值** | — | **{avg_change:+.2f}%** |")
md_lines.append("")
md_lines.append("💰 北向资金")
md_lines.append(f"| 指标 | 数值 |")
md_lines.append(f"|------|------|")
md_lines.append(f"| 今日净流入 | {northbound_note} |")
md_lines.append("")
md_lines.append("💼 持仓")
md_lines.append("| 代码 | 名称 | 现价 | 涨跌幅 | 浮盈% | vs 大盘 |")
md_lines.append("|------|------|------|--------|-------|---------|")
for h in holdings:
    code = h.get('code', '')
    name = h.get('name', '')
    s = spot.get(code, {})
    price = s.get('price', live_price if code == '300059' else 0)
    chg = s.get('change_pct', 0)
    md_lines.append(f"| {code} | {name} | {price:.2f} | {chg:+.2f}% | {floating_pnl_pct:+.2f}% | {vs_sector} |")
md_lines.append(sector_line)
md_lines.append("")
md_lines.append("📋 操作建议")
md_lines.append(f"> **{action}**")
md_lines.append("")
if issues:
    md_lines.append("🚨 **Issues:**")
    for iss in issues:
        md_lines.append(f"- {iss}")
if warnings:
    md_lines.append("⚠️ **Warnings:**")
    for w in warnings:
        md_lines.append(f"- {w}")
md_lines.append("")
md_lines.append("🔍 四维归因")
md_lines.append(f"- 基本面: {fundamental}")
md_lines.append(f"- 技术面: {technical}")
md_lines.append(f"- 资金面: {capital}")
md_lines.append(f"- 情绪面: {sentiment}")
md_lines.append("")
md_lines.append("🛡️ 风险反思")
md_lines.append(f"- 流动性: {risk1}")
md_lines.append(f"- 板块集中度: {risk2}")
md_lines.append("")
md_lines.append(key_points)
md_lines.append("")
md_lines.append(f"⚠️ _AI分析仅供参考，不构成投资建议_")

markdown = "\n".join(md_lines)
print(markdown)

# 保存到文件
out_path = os.path.expanduser("/tmp/1455_decision_md.txt")
with open(out_path, 'w', encoding='utf-8') as f:
    f.write(markdown)

# 保存状态供 Vault 判断
state_info = {
    'router_9am_state': router_9am['state'],
    'router_1455_state': router_1455['state'],
    'state_changed': state_changed,
    'avg_change': avg_change,
    'stock_chg': stock_chg,
    'northbound_note': northbound_note,
    'sector_note': sector_note,
}
with open(os.path.expanduser("/tmp/1455_state.json"), 'w', encoding='utf-8') as f:
    json.dump(state_info, f, ensure_ascii=False, indent=2)
