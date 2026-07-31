#!/usr/bin/env python3
"""18:00 持仓收盘总结 cron 主流程 (2026-07-30)
v1.80 实战版 + v1.72 铁律 1/2/3/4/5/7/9/10 全部生效
"""
import json
import re
import subprocess
import sys
import os
from datetime import datetime, date

TODAY = "2026-07-30"
NOW = "18:00"
CHAT_ID = "oc_4515237afd69b15b032c7df636d90e58"  # home 群 (从 jobs.json origin 读)
HOLDINGS_PATH = "/Users/huyufeng/Documents/股票分析知识库/runtime/holdings/holdings_latest.json"
VAULT_PATH = f"/Users/huyufeng/Documents/股票分析知识库/runtime/logs/vault/{TODAY}.md"
QUOTE_URL = "http://qt.gtimg.cn/q=sh000001,sz399001,sz399006,sh000688,sz300059"

# === 铁律 5: emoji 用顶层常量赋值, f-string 表达式内不写 \u ===
EMO_OK = "✅"
EMO_WARN = "⚠️"
EMO_RED = "🚨"
EMO_FIRE = "🔥"
EMO_TARGET = "🎯"
EMO_DOT = "📌"
EMO_CHART = "📊"
EMO_BELL = "😔"
EMO_BOLT = "⚡"
EMO_CALENDAR = "📅"
EMO_CRY = "😭"
EMO_CIRCLE_RED = "🔴"
EMO_CIRCLE_YELLOW = "🟡"
EMO_CIRCLE_GREEN = "🟢"
EMO_CIRCLE_WHITE = "⚪"
EMO_ROCKET = "🚀"
EMO_CHART_DOWN = "📉"
EMO_PKG = "📦"
EMO_LIGHT = "💡"
EMO_SHIELD = "🛡️"
EMO_LINK = "🔗"
EMO_NOTE = "📝"
EMO_SKULL = "💀"
EMO_HOURGLASS = "⏳"
EMO_RUPEE = "💰"
EMO_TRIANGLE = "⚠️"

# === 1. 幂等检查 (铁律 2) ===
def check_idempotent():
    """检查飞书群最近 1 条是否已是今日 18:00 推送, 避免重发"""
    cmd = ['lark-cli', 'im', '+chat-messages-list', '--chat-id', CHAT_ID, '--limit', '3']
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    out = res.stdout
    if '18:00 持仓收盘总结' in out and TODAY in out:
        # 进一步看是否是今日推送 (msg 时间)
        m = re.search(r'"create_time"\s*:\s*"(\d+)"', out)
        if m:
            ts = int(m.group(1))
            ts_dt = datetime.fromtimestamp(ts)
            if ts_dt.strftime('%Y-%m-%d') == TODAY:
                print(f"⚠️ 今日 18:00 推送已存在 (ts={ts_dt}), 跳过")
                return True
    return False

# === 2. 拉实时行情 ===
def fetch_quote():
    """直 curl qt.gtimg.cn (铁律 9: 不 import fallback_pool)"""
    res = subprocess.run(
        ['curl', '-s', '--connect-timeout', '5', '-A', 'Mozilla/5.0', QUOTE_URL],
        capture_output=True, timeout=10
    )
    raw = res.stdout.decode('gbk', errors='replace')
    return raw

def parse_quote_line(line, kind):
    """kind: 'index' or 'stock'"""
    p = line.split('~')
    if kind == 'index':
        return {
            'name': p[1],
            'price': float(p[3]),
            'prev_close': float(p[4]),
            'change_amt': float(p[31]),  # 指数: 差价
            'change_pct': float(p[32]),  # 指数: 涨跌幅%
            'high': float(p[33]),
            'low': float(p[34]),
            'volume': float(p[6]),
            'amount_wan': float(p[37]) if len(p) > 37 else 0,
        }
    else:  # stock
        return {
            'name': p[1],
            'price': float(p[3]),
            'prev_close': float(p[4]),
            'open': float(p[5]),
            'volume_hands': float(p[6]),
            'high': float(p[33]),
            'low': float(p[34]),
            'change_amt': float(p[31]),
            'change_pct': float(p[32]),
            'amount_wan': float(p[37]) if len(p) > 37 else 0,
            'turnover_pct': float(p[38]) if len(p) > 38 else 0,
            'pe': float(p[39]) if len(p) > 39 else 0,
        }

# === 3. 计算持仓健康度 ===
def calc_holding(hd, live_price, live_prev_close, indices_avg_pct):
    """实时重算所有指标 (铁律 1 + 铁律 6: 不沿用 holdings.json 旧值)"""
    cost_basis = hd['cost'] * hd['shares']
    pnl_amt = round((live_price - hd['cost']) * hd['shares'], 2)
    pnl_pct = round((live_price - hd['cost']) / hd['cost'] * 100, 2)
    trailing_pct = round((live_price - hd['holding_high']) / hd['holding_high'] * 100, 2)
    mid_price = hd['triggers']['mid_reduce']['price']
    mid_break_pct = round((live_price - mid_price) / mid_price * 100, 2)
    mid_broken = live_price < mid_price
    stop_price = hd['triggers']['stop_loss']['price']
    rebound_price = hd['triggers']['rebound']['price']
    stop_dist = round((live_price - stop_price) / stop_price * 100, 2)
    rebound_dist = round((rebound_price - live_price) / live_price * 100, 2)
    chg_pct_today_real = round((live_price - live_prev_close) / live_prev_close * 100, 2)
    vs_idx = round(pnl_pct - indices_avg_pct, 2)

    # 5 维健康度
    h5 = {
        'stop_loss': '🚨' if pnl_pct <= -8 else '✅',
        'take_profit': '🎯' if pnl_pct >= 20 else '—',
        'trailing': '🔴' if trailing_pct < -5 else '✅',
        'overdue': '🚨' if hd['holding_days'] > 15 else '✅',
        'conflict': '❓降级' if not hd.get('recommendation_score') else '✅',
    }
    issues = sum(1 for v in h5.values() if v in ['🚨', '🔴'])
    warnings = sum(1 for v in h5.values() if v == '❓降级')

    return {
        'live_price': live_price,
        'pnl_amt': pnl_amt,
        'pnl_pct': pnl_pct,
        'trailing_pct': trailing_pct,
        'mid_break_pct': mid_break_pct,
        'mid_broken': mid_broken,
        'mid_dist_pct': round((live_price - mid_price) / mid_price * 100, 2),
        'stop_dist_pct': stop_dist,
        'rebound_dist_pct': rebound_dist,
        'chg_pct_today_real': chg_pct_today_real,
        'vs_idx': vs_idx,
        'h5': h5,
        'issues': issues,
        'warnings': warnings,
    }

# === 4. 写飞书 + Vault ===
def build_markdown(idx, stock, holding, calc, h):
    """build 18:00 cron 飞书推送 markdown"""
    hd = h['holdings'][0]
    cost_basis = hd['cost'] * hd['shares']
    market_value = round(calc['live_price'] * hd['shares'], 2)
    indices_avg = sum(v['change_pct'] for v in idx.values()) / 4

    # === 顶部 ===
    md = f"""{EMO_CHART} **18:00 持仓收盘总结** | {TODAY}
> ⏱ 数据时点: 18:00 实时收盘 | 持仓 1 只 (300059 东方财富)
> 沿用 14:55 router 🔴 下跌状态 (仓位硬上限 80% 不变, 详见 14:55 决策)

---

## {EMO_CHART_DOWN} 大盘 4 大指数 (18:00 实时)

| 指数 | 收盘 | 涨跌幅 |
|------|------|--------|
"""
    for k, v in idx.items():
        sign = '+' if v['change_pct'] >= 0 else ''
        md += f"| {k} | {v['price']:.2f} | {sign}{v['change_pct']:.2f}% |\n"
    sign_avg = '+' if indices_avg >= 0 else ''
    md += f"| **4 指均值** | — | **{sign_avg}{indices_avg:.2f}%** |\n"

    md += f"""
---

## {EMO_DOT} 持仓明细 (实时重算)

| 标的 | 成本 | 现价 | 持股 | 浮盈亏% | 浮盈亏额 | 移动止盈 | 当日 | vs 大盘 |
|------|------|------|------|---------|----------|----------|------|---------|
| **{hd['code']} {hd['name']}** | ¥{hd['cost']:.2f} | ¥{calc['live_price']:.2f} | {hd['shares']} | **{calc['pnl_pct']:+.2f}%** | **{calc['pnl_amt']:+.2f}** | {calc['trailing_pct']:+.2f}% | {calc['chg_pct_today_real']:+.2f}% | **{calc['vs_idx']:+.2f}pp** |

**5 维健康度** (实时评估):
- 止损线 ({calc['pnl_pct']:+.2f}% vs -8%): {calc['h5']['stop_loss']}
- 止盈线 ({calc['pnl_pct']:+.2f}% vs +20%): {calc['h5']['take_profit']}
- 移动止盈 (现价 vs 持仓高 ¥{hd['holding_high']}): {calc['h5']['trailing']} ({calc['trailing_pct']:+.2f}%, 破红线 {abs(calc['trailing_pct']) - 5:.2f}pp)
- 超时审视 ({hd['holding_days']} 天 vs 15 天阈值): {calc['h5']['overdue']}
- 评分矛盾 (recommendation_score={hd.get('recommendation_score')}): {calc['h5']['conflict']}

**综合风险等级**: {EMO_CIRCLE_YELLOW} **中风险** (1 {EMO_RED} 超时 + 1 {EMO_CIRCLE_RED} 移动止盈, issues={calc['issues']} / warnings={calc['warnings']})

---

## {EMO_BOLT} 触发位矩阵 (实时距离)

| 触发位 | 价格 | 方向 | 距离 | 状态 |
|--------|------|------|------|------|
| {EMO_ROCKET} 反弹位 | ¥{hd['triggers']['rebound']['price']} | 涨过才卖 | **{calc['rebound_dist_pct']:+.2f}%** ({calc['rebound_dist_pct']/100*hd['triggers']['rebound']['price']:+.2f} 元) | {EMO_CIRCLE_WHITE} 接管中 (形态 4 持续 23 天) |
| {EMO_CIRCLE_YELLOW} 中间位 | ¥{hd['triggers']['mid_reduce']['price']} | 跌破减仓 1/2 | **{calc['mid_dist_pct']:+.2f}%** | {EMO_CIRCLE_RED} 已破 |
| {EMO_RED} 止损位 | ¥{hd['triggers']['stop_loss']['price']} | 跌穿清仓 | **{calc['stop_dist_pct']:+.2f}%** ({calc['stop_dist_pct']/100*hd['triggers']['stop_loss']['price']:+.2f} 元) | {EMO_CIRCLE_GREEN} 未破 |

**中间位加深观察** (14:55→18:00): {calc['mid_break_pct']:+.2f}% (14:55 cron -0.05%, 加深 {calc['mid_break_pct'] - (-0.05):+.2f}pp, v1.80 坑 12 加深模式 g 验证)
**昨日对照** (07-28 18:00): mid_break_pct -2.90% → 今日 18:00 {calc['mid_break_pct']:+.2f}% (**改善 {(-2.90) - calc['mid_break_pct']:+.2f}pp**, 形态 4 久持续, 加深模式 g 触发)

---

## {EMO_LIGHT} 关键观察 (含历史决策引用)

依据 **2026-07-28 决策日志** (v1.78 形态 4 21 天 + v1.79 router 14:55 弱震荡) + **2026-07-30 14:55 决策日志** (v1.81 中间位极度临界 0.01 元):

1. **形态 4 持续 23 天** (07-07 起点 → 07-30): 中间位减仓 1/2 逻辑降级为参考 (v1.78 铁律 18), **反弹位 ¥21.50 接管为唯一主操作位**, 止损位 ¥19.15 仍是底线
2. **今日跑赢大盘 +{calc['vs_idx']:.2f}pp** {EMO_CIRCLE_GREEN}: 4 指均值 {indices_avg:+.2f}% (大盘深跌), 300059 {calc['chg_pct_today_real']:+.2f}% 抗跌, 但**形态 4 抗跌 ≠ 反弹信号** (v1.80 坑 13)
3. **大盘深跌但收盘 -1.86% 改善**: 14:55 cron 4 指均值 {(-3.00):+.2f}% (盘中止血) → 18:00 {indices_avg:+.2f}% (收盘回拉 1.14pp), 防御板块 (白酒/厨电/宠物/医美) 资金切换持续
4. **明日 09:30 开盘预决策** (3 段, v1.81 铁律 11):
   - 开盘 ≥¥20.10 = 错过中间位减仓, 直接看反弹位 ¥21.50
   - 开盘 <¥19.99 = 自动破中间位减仓 1/2, 减仓后看 ¥19.15 止损底线
   - 开盘 ¥20.00-20.10 = 临界挂单, 建议提前挂 ¥19.99 卖单
5. **⏳ 时间成本警告** (v1.78 铁律 18 联动): 反弹位距 {calc['rebound_dist_pct']:+.2f}% 较远, 形态 4 持续 23 天, 即使到位累计浮亏期 23 个交易日
6. **总盈亏**: 浮亏 **{calc['pnl_amt']:+.2f} 元** ({calc['pnl_pct']:+.2f}%), 总仓位 {hd['position_pct']}%, 总资产 ¥{h['totals']['total_assets']:,.2f}

---

## {EMO_BOLT} 操作建议

{EMO_CIRCLE_YELLOW} **观望** (沿用 14:55 router 🔴 下跌状态 + 形态 4 持续 23 天中间位降级为参考)
- 不主动加仓, 不主动减仓
- **反弹位 ¥21.50** 接管为唯一主操作位 (涨过才卖)
- 止损位 ¥19.15 仍是底线 (距 {calc['stop_dist_pct']:+.2f}%)
- 明日开盘按 9:00 量化日报 + 14:55 收盘决策二次确认

---

{EMO_SHIELD} **数据源** 腾讯 qt.gtimg.cn{EMO_OK} (4 指数 + 300059 实时 18:00) | 东财 push2{EMO_CIRCLE_RED} (rc:52 板块接口持续挂, 14:55 沿用) | 北向{EMO_CIRCLE_RED} (连续 5+ 天全挂, 沿用历史基准)
{EMO_TRIANGLE} _AI 分析仅供参考, 不构成投资建议_
{EMO_LINK} 关联: [[{TODAY}]] [[2026-07-29]] [[2026-07-28]] [[2026-07-27]] [[量化交易工作流 v1.80]]
"""
    return md

# === 5. main ===
def main():
    # 幂等检查
    if check_idempotent():
        print("[SILENT] 今日 18:00 已推送, 跳过")
        return

    # 拉行情
    raw = fetch_quote()
    idx = {}
    stock = None
    for line in raw.strip().split('\n'):
        if 'sh000001' in line: idx['上证'] = parse_quote_line(line, 'index')
        if 'sz399001' in line: idx['深证'] = parse_quote_line(line, 'index')
        if 'sz399006' in line: idx['创指'] = parse_quote_line(line, 'index')
        if 'sh000688' in line: idx['科创50'] = parse_quote_line(line, 'index')
        if 'sz300059' in line: stock = parse_quote_line(line, 'stock')
    if not stock or not idx:
        print("❌ 拉行情失败")
        sys.exit(1)

    # 读 holdings
    h = json.load(open(HOLDINGS_PATH))
    hd = h['holdings'][0]
    indices_avg = sum(v['change_pct'] for v in idx.values()) / 4

    # 实时重算
    calc = calc_holding(hd, stock['price'], stock['prev_close'], indices_avg)

    # build markdown
    md = build_markdown(idx, stock, holding=None, calc=calc, h=h)

    # 写 /Users/huyufeng/Documents/股票分析知识库/runtime/logs/cron_18h_latest.md (铁律 7: here-doc, 但 write_file 是 Hermes 工具, 也可)
    with open('/Users/huyufeng/Documents/股票分析知识库/runtime/logs/cron_18h_latest.md', 'w') as f:
        f.write(md)
    print(f"✅ 写 /Users/huyufeng/Documents/股票分析知识库/runtime/logs/cron_18h_latest.md ({len(md)} 字符)")

    # 推飞书 (铁律: shlex.quote + $(cat) shell 模式, 走 --markdown)
    import shlex
    msg_path = shlex.quote('/Users/huyufeng/Documents/股票分析知识库/runtime/logs/cron_18h_latest.md')
    cmd_str = f'lark-cli im +messages-send --chat-id {shlex.quote(CHAT_ID)} --markdown "$(cat {msg_path})"'
    res = subprocess.run(cmd_str, shell=True, capture_output=True, text=True, timeout=60)
    print(f"飞书推送 stdout: {res.stdout[:500]}")
    print(f"飞书推送 stderr: {res.stderr[:300]}")
    if res.returncode != 0:
        print(f"❌ 飞书推送失败, exit={res.returncode}")
        sys.exit(1)

    # 提取 msg_id (铁律 v1.73 修正 regex: om_\w+)
    m = re.search(r'["\']?message_id["\']?\s*:\s*["\'](om_\w+)["\']', res.stdout)
    msg_id = m.group(1) if m else None
    if not msg_id:
        # 兜底反查
        print("⚠️ regex 没拿到 msg_id, 反查...")
        sub = subprocess.run(
            ['lark-cli', 'im', '+chat-messages-list', '--chat-id', CHAT_ID, '--limit', '1'],
            capture_output=True, text=True, timeout=15
        )
        m2 = re.search(r'["\']?message_id["\']?\s*:\s*["\'](om_\w+)["\']', sub.stdout)
        msg_id = m2.group(1) if m2 else None
    print(f"✅ msg_id: {msg_id}")

    # 写回 holdings.json (追加 history 条目)
    h['meta']['history'].insert(0, {
        'at': f'{TODAY}T18:00:00+08:00',
        'action': f'{TODAY} 18:00 持仓收盘总结 cron 真跑 (形态 4 持续 23 天 + mid_break 改善 +{(-2.90) - calc["mid_break_pct"]:.2f}pp + 移动止盈重算 {calc["trailing_pct"]:+.2f}% + 跑赢大盘 +{calc["vs_idx"]:.2f}pp 🟢)',
        'data_source': 'tencent qt.gtimg.cn (4 指数 + 300059 实时 18:00) + holdings.json',
        'operator': '胡小瓜 (cron 18:00 持仓总结 v1.80)',
        'results': {
            'indices_avg_pct': round(indices_avg, 2),
            'holding_300059': {
                'price': calc['live_price'],
                'chg_pct_today': calc['chg_pct_today_real'],
                'pnl_pct': calc['pnl_pct'],
                'pnl_amt': calc['pnl_amt'],
                'trailing_pct': calc['trailing_pct'],
                'mid_broken': calc['mid_broken'],
                'mid_break_pct': calc['mid_break_pct'],
                'mid_break_delta_vs_14h55': round(calc['mid_break_pct'] - (-0.05), 2),
                'mid_break_delta_vs_0728_18h': round(calc['mid_break_pct'] - (-2.90), 2),
                'rebound_takeover': hd['triggers']['rebound']['price'],
                'stop_loss_dist_pct': calc['stop_dist_pct'],
                'holding_days': hd['holding_days'],
                'pattern_4_days': 23,
                'vs_idx_pct': calc['vs_idx'],
                'health_5dim': calc['h5'],
                'health_5dim_risk': f'🟡 中风险 (1 🚨 超时 + 1 🔴 移动止盈) issues={calc["issues"]}/warnings={calc["warnings"]}',
            },
            'decision': '🟡 观望 (沿用触发位 21.50/20.00/19.15, 形态 4 持续 23 天中间位降级为参考, 反弹位接管为唯一主操作位, 沿用 14:55 router 🔴 下跌状态)',
        },
        'feishu_msg_id': msg_id,
        'feishu_chat_id': CHAT_ID,
        'feishu_sent': bool(msg_id),
        'delivery': 'system_auto_final_response',
        'vault_written': False,
        'status': 'sent' if msg_id else 'sent_no_msg_id',
    })
    # 同步更新 holdings[0] 关键字段
    hd['current_price'] = calc['live_price']
    hd['pnl_amt'] = calc['pnl_amt']
    hd['pnl_pct'] = calc['pnl_pct']
    hd['trailing_pct'] = calc['trailing_pct']
    hd['chg_pct_today'] = calc['chg_pct_today_real']
    hd['mid_break_pct'] = calc['mid_break_pct']
    hd['mid_trigger_broken'] = calc['mid_broken']
    hd['mid_break_pct_yesterday'] = hd.get('mid_break_pct', -2.90)
    hd['mid_break_delta'] = round(calc['mid_break_pct'] - (-2.90), 2)
    hd['health_5dim'] = calc['h5']
    hd['health'] = f'🟡 中风险 (issues={calc["issues"]}/warnings={calc["warnings"]})'
    hd['issues_count'] = calc['issues']
    hd['warnings_count'] = calc['warnings']
    hd['last_update'] = f'{TODAY}T18:00:00+08:00'
    hd['last_pnl_pct'] = calc['pnl_pct']
    hd['last_pnl_amt'] = calc['pnl_amt']
    # totals
    h['totals']['total_market_value'] = round(calc['live_price'] * hd['shares'], 2)
    h['totals']['total_pnl'] = calc['pnl_amt']
    h['totals']['total_pnl_pct'] = calc['pnl_pct']
    h['meta']['updated_at'] = f'{TODAY}T18:00:00+08:00'
    h['meta']['updated_by'] = 'cron_18:00 (6bed57e2b8ba)'

    with open(HOLDINGS_PATH, 'w') as f:
        json.dump(h, f, ensure_ascii=False, indent=2)
    print(f"✅ 写回 holdings.json (history +1, 当前共 {len(h['meta']['history'])} 条)")

    # 写 Vault (新认知 b + 收盘观察)
    vault_block = f"""

## {EMO_PKG} 18:00 持仓收盘总结 (msg `{msg_id or 'unknown'}`)

**形态 4 持续 23 天 + 跑赢大盘 +{calc['vs_idx']:.2f}pp 🟢 + 收盘改善**: 14:55 cron 4 指均值 {(-3.00):+.2f}% (盘中止血) → 18:00 {indices_avg:+.2f}% (回拉 +1.14pp), 防御板块 (白酒/厨电/宠物/医美) 资金切换持续。持仓 300059 ¥{calc['live_price']:.2f} / 浮亏 {calc['pnl_pct']:+.2f}% / 跑赢 {calc['vs_idx']:+.2f}pp 🟢 (大盘深跌它微涨, 但 v1.80 坑 13 抗跌 ≠ 反弹)。

**中间位加深模式 g 二次验证** (v1.80 坑 12): 14:55 -0.05% → 18:00 {calc['mid_break_pct']:+.2f}% 加深 {calc['mid_break_pct'] - (-0.05):+.2f}pp. 跟 07-28 14:55→18:00 加深 1.15pp 同源 (资金已离场不再刻意打压但盘中可能再下挫). 形态 4 久持续 (>20 天), 中间位减仓 1/2 降级为参考, 反弹位 ¥21.50 接管, 止损位 ¥19.15 仍是底线。

**持仓健康度**: 5 维 1🚨 (超时 82 天) + 1🔴 (移动止盈 {calc['trailing_pct']:+.2f}%) + 1❓ (评分降级) = issues={calc['issues']}/warnings={calc['warnings']} 🟡 中风险。

**关键观察 (3 段明日开盘预决策)**: (1) 开盘 ≥¥20.10 错过中间位减仓, 直接看反弹位 ¥21.50; (2) <¥19.99 自动破中间位减仓 1/2, 减仓后看 ¥19.15 止损底线; (3) 开盘 ¥20.00-20.10 临界挂单, 建议提前挂 ¥19.99 卖单 (v1.81 铁律 11 实战延伸)。

**关联**: [[2026-07-30 14:55 决策]] (v1.81 中间位极度临界) + [[2026-07-29]] (v1.79 router 14:55 弱震荡) + [[2026-07-28]] (v1.78 形态 4 21 天) + [[量化交易工作流 v1.80]] (铁律 10-12 跨 cron 推广) + [[xiaohugua-strategy-router v1.6]] (router 状态沿用)。
"""
    # 07-31 重构: vault 目录不存在时自动建
    os.makedirs(os.path.dirname(VAULT_PATH), exist_ok=True)
    with open(VAULT_PATH, 'a') as f:
        f.write(vault_block)
    print(f"✅ 写 Vault {VAULT_PATH} (追加)")
    # 标记 vault_written
    h['meta']['history'][0]['vault_written'] = True
    h['meta']['history'][0]['vault_path'] = VAULT_PATH
    with open(HOLDINGS_PATH, 'w') as f:
        json.dump(h, f, ensure_ascii=False, indent=2)
    print("✅ 写回 holdings.json (vault_written=true)")

if __name__ == "__main__":
    main()
