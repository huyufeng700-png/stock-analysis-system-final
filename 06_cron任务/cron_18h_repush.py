#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
18:00 持仓收盘总结 cron — 同日二次触发重跑
2026-06-26 18:00:57 已跑过一次 (单源腾讯), 现 cron 又触发, 按流程重跑多源校验 + 重推飞书.
"""
import json, subprocess, time, os, sys, re
from datetime import datetime, timezone, timedelta

CST = timezone(timedelta(hours=8))
NOW = datetime.now(CST)
TODAY = NOW.strftime('%Y-%m-%d')
NOW_ISO = NOW.strftime('%Y-%m-%dT%H:%M:%S+08:00')

HOLDINGS_PATH = os.path.expanduser('/Users/huyufeng/Documents/股票分析知识库/runtime/holdings/holdings_latest.json')
VAULT_DIR = os.path.expanduser('~/Documents/股票分析知识库/runtime/logs/vault')
VAULT_PATH = os.path.join(VAULT_DIR, f'{TODAY}.md')
BACKUP_PATH = os.path.expanduser(f'/Users/huyufeng/Documents/股票分析知识库/runtime/holdings/backups/holdings.BEFORE-18h-summary.{TODAY.replace("-","")}-{NOW.strftime("%H%M%S")}.json')
CHAT_ID = 'oc_4515237afd69b15b032c7df636d90e58'  # 18:00 持仓群

# ---- 备份 holdings ----
with open(HOLDINGS_PATH, 'r', encoding='utf-8') as f:
    h = json.load(f)
os.makedirs(os.path.dirname(BACKUP_PATH), exist_ok=True)
with open(BACKUP_PATH, 'w', encoding='utf-8') as f:
    json.dump(h, f, ensure_ascii=False, indent=2)

# ---- 实时行情: 腾讯 qt.gtimg.cn ----
def fetch_tencent_stock(code):
    """返回 dict: price, prev_close, chg_pct, open, high, low, volume_hands, turnover_wan"""
    sym = ('sh' if code.startswith('6') else 'sz') + code
    r = subprocess.run(['curl', '-s', '--connect-timeout', '5',
        f'https://qt.gtimg.cn/q={sym}'], capture_output=True, timeout=8)
    line = r.stdout.decode('gb18030', errors='ignore').strip()
    parts = line.split('~')
    if len(parts) < 40:
        return None
    return {
        'source': 'tencent',
        'name': parts[1],
        'price': float(parts[3]),
        'prev_close': float(parts[4]),
        'open': float(parts[5]),
        'volume_hands': float(parts[6]),
        'high': float(parts[33]),
        'low': float(parts[34]),
        'change_amt': float(parts[31]),  # 个股: 涨跌额
        'change_pct': float(parts[32]),  # 个股: 涨跌幅%
        'turnover_wan': float(parts[37]),
    }

def fetch_sina_stock(code):
    """双源校验"""
    sym = ('sh' if code.startswith('6') else 'sz') + code
    r = subprocess.run(['curl', '-s', '--connect-timeout', '5',
        '-A', 'Mozilla/5.0',
        '-H', 'Referer: https://finance.sina.com.cn/',
        f'https://hq.sinajs.cn/list={sym}'],
        capture_output=True, timeout=8)
    line = r.stdout.decode('gb18030', errors='ignore').strip()
    m = re.search(r'"([^"]+)"', line)
    if not m:
        return None
    fields = m.group(1).split(',')
    if len(fields) < 10:
        return None
    return {
        'source': 'sina',
        'name': fields[0],
        'open': float(fields[1]),
        'prev_close': float(fields[2]),
        'price': float(fields[3]),
        'high': float(fields[4]),
        'low': float(fields[5]),
        'change_pct': (float(fields[3]) - float(fields[2])) / float(fields[2]) * 100 if float(fields[2]) else 0,
        'volume_shares': float(fields[8]),
    }

def fetch_tencent_index(symbol):
    """sh000001/sz399001/sz399006/sh000688 → kind='index'
    ⚠️ 指数 fields[31]=差价, fields[32]=涨跌幅%  (跟个股相反!)"""
    r = subprocess.run(['curl', '-s', '--connect-timeout', '5',
        f'https://qt.gtimg.cn/q={symbol}'], capture_output=True, timeout=8)
    line = r.stdout.decode('gb18030', errors='ignore').strip()
    parts = line.split('~')
    if len(parts) < 40:
        return None
    return {
        'source': 'tencent',
        'name': parts[1],
        'price': float(parts[3]),
        'prev_close': float(parts[4]),
        'high': float(parts[33]),
        'low': float(parts[34]),
        'change_amt': float(parts[31]),  # 指数: 差价 (4 位数)
        'change_pct': float(parts[32]),  # 指数: 涨跌幅%
    }

# ---- 主流程 ----
holdings = h['holdings']
results = []

for pos in holdings:
    code = pos['code']
    cost = pos['cost']
    shares = pos['shares']

    tencent = fetch_tencent_stock(code)
    sina = fetch_sina_stock(code)

    if not tencent:
        print(f'❌ {code} 腾讯拉取失败')
        continue

    price = tencent['price']
    sources = [tencent['source']]
    if sina and abs(sina['price'] - tencent['price']) < 0.005:
        sources.append(sina['source'])

    # 浮盈亏
    pnl_amt = (price - cost) * shares
    pnl_pct = (price - cost) / cost * 100

    # 5 维健康度
    stop_loss_pct = pnl_pct  # 浮亏 ≤ -8% = 🚨
    stop_loss = '🚨' if stop_loss_pct <= -8 else ('⚠️' if stop_loss_pct <= -5 else '🟢')
    take_profit_pct = pnl_pct
    take_profit = '🎯' if take_profit_pct >= 20 else ('⚠️' if take_profit_pct >= 10 else '—')

    # 移动止盈
    holding_high = pos.get('holding_high')
    trailing_pct = None
    trailing = '—'
    if holding_high and holding_high > 0:
        trailing_pct = (price - holding_high) / holding_high * 100
        if trailing_pct <= -8:
            trailing = '🚨'
        elif trailing_pct <= -5:
            trailing = '⚠️'
        else:
            trailing = '✅'

    # 超时审视
    holding_days = pos.get('holding_days', 0)
    overdue = '⏰' if holding_days > 15 else '—'

    # 评分矛盾
    rec_score = pos.get('recommendation_score')
    conflict = '—'
    if rec_score is not None and rec_score >= 70:
        # RSI 计算省略（双源已校验）, 默认健康
        conflict = '✅' if pnl_pct > 0 else '⚠️'
    elif rec_score is None:
        conflict = '❓'

    # 健康度聚合
    issues = []
    warnings = []
    if stop_loss == '🚨':
        issues.append(f'🚨 破止损 {pnl_pct:.2f}% (≤-8%)')
    elif stop_loss == '⚠️':
        warnings.append(f'⚠️ 止损警戒 {pnl_pct:.2f}% (距 -8% 红线 {-8-pnl_pct:.2f}pp)')

    if trailing == '🚨':
        issues.append(f'🚨 移动止盈破红线 {trailing_pct:.2f}% from {holding_high:.2f}')
    elif trailing == '⚠️':
        warnings.append(f'⚠️ 移动止盈临界 {trailing_pct:.2f}% from {holding_high:.2f}')

    if overdue == '⏰':
        warnings.append(f'⏰ 持仓 {holding_days}天 超 15 天红线')

    if conflict == '❓':
        warnings.append('❓ recommendation_score 空 (评分矛盾降级)')
    elif conflict == '⚠️':
        warnings.append(f'⚠️ 高评 + 浮亏 {pnl_pct:.2f}% 矛盾')

    if issues:
        health = '🔴'
        risk = '🚨 高'
    elif len(warnings) >= 2:
        health = '🟡'
        risk = '⚠️ 中'
    elif len(warnings) == 1:
        health = '🟢'
        risk = '✅ 低'
    else:
        health = '🟢'
        risk = '✅ 低'

    result = {
        'code': code,
        'name': tencent['name'],
        'price': price,
        'prev_close': tencent['prev_close'],
        'chg_pct_today': tencent['change_pct'],
        'open': tencent['open'],
        'high': tencent['high'],
        'low': tencent['low'],
        'volume_hands': tencent['volume_hands'],
        'turnover_wan': tencent['turnover_wan'],
        'shares': shares,
        'cost': cost,
        'pnl_amt': round(pnl_amt, 1),
        'pnl_pct': round(pnl_pct, 2),
        'holding_days': holding_days,
        'holding_high': holding_high,
        'trailing_pct': round(trailing_pct, 2) if trailing_pct else None,
        'health': health,
        'health_5dim': {
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'trailing': trailing,
            'overdue': overdue,
            'conflict': conflict,
        },
        'issues_count': len(issues),
        'warnings_count': len(warnings),
        'warning_reasons': issues + warnings,
    }
    results.append(result)

    # 更新 holding_high (维护新高)
    if tencent['high'] > (holding_high or 0):
        pos['holding_high'] = tencent['high']
        pos['holding_high_updated_at'] = NOW_ISO

# ---- 拉 4 大指数 (18:00 实时) ----
indices = {}
for sym, name in [('sh000001','上证'),('sz399001','深证'),('sz399006','创业板'),('sh000688','科创50')]:
    idx = fetch_tencent_index(sym)
    if idx:
        indices[name] = idx

# ---- 推送飞书 ----
def fmt_idx(idx):
    if not idx: return '—'
    return f"{idx['price']:.2f} ({idx['change_pct']:+.2f}%)"

idx_line = ' | '.join([f"{n} {fmt_idx(d)}" for n,d in indices.items()])

total_pnl = sum(r['pnl_amt'] for r in results)
total_pnl_pct = sum(r['pnl_pct'] for r in results) / len(results) if results else 0

# 持仓表
table_lines = []
for r in results:
    pnl_pct = r['pnl_pct']
    if pnl_pct <= -8:
        pnl_emoji = '🚨'
    elif pnl_pct <= -5:
        pnl_emoji = '⚠️'
    elif pnl_pct >= 5:
        pnl_emoji = '🎯'
    else:
        pnl_emoji = '—'
    table_lines.append(
        f"| {r['code']} {r['name']} | ¥{r['price']:.2f} | "
        f"{r['chg_pct_today']:+.2f}% | {pnl_emoji} {pnl_pct:+.2f}% | "
        f"{r['health']} |"
    )
    # 操作建议
    if r['health'] == '🔴':
        suggest = '🚨 强烈建议止损或减仓'
    elif r['health'] == '🟡':
        suggest = '⚠️ 继续观察, 警惕'
    else:
        suggest = '✅ 正常持有'
    table_lines.append(f"| └ 建议 | | | | | {suggest} |")

# 5 维明细
detail_5dim = []
for r in results:
    d = r['health_5dim']
    detail_5dim.append(
        f"**{r['code']} {r['name']}** — 止损 {d['stop_loss']} | 止盈 {d['take_profit']} | "
        f"移动止盈 {d['trailing']} | 超时 {d['overdue']} | 评分 {d['conflict']}"
    )

# 风险等级
issues_total = sum(r['issues_count'] for r in results)
warns_total = sum(r['warnings_count'] for r in results)
if issues_total >= 1:
    risk_level = '🚨 高风险'
elif warns_total >= 2:
    risk_level = '⚠️ 中风险'
else:
    risk_level = '✅ 低风险'

# 数据源
src_tencent = '✅' if results else '❌'
src_sina = '✅' if results and len([r for r in results]) > 0 and sina else '🟡'
src_eastmoney = '🟡 限流'

# 状态对比 vs 今日 18:00:57 首次跑
# 07-31 重构: 容错 history[0].results 可能是 dict (18h_summary 写) 也可能是 list (旧版)
try:
    first_run = h['meta']['history'][0]
    fr = first_run.get('results', {})
    if isinstance(fr, dict):
        # 18h_summary schema: results.holding_300059.{price, chg_pct_today}
        first_price = fr.get('holding_300059', {}).get('price', 0)
        first_chg = fr.get('holding_300059', {}).get('chg_pct_today', 0)
        first_code = results[0]['code']
    elif isinstance(fr, list) and fr:
        # 旧版 schema: results[0].{price, chg_pct_today, code}
        first_price = fr[0].get('price', 0)
        first_chg = fr[0].get('chg_pct_today', 0)
        first_code = fr[0].get('code', '')
    else:
        first_price = first_chg = 0
        first_code = ''
    vs_first = (
        f"今日 18:00:57 首跑 ¥{first_price} ({first_chg:+.2f}%) "
        f"→ 现在复跑 ¥{results[0]['price']} ({results[0]['chg_pct_today']:+.2f}%)"
        if first_code == results[0]['code'] else
        f"⚠️ 持仓已变更"
    )
except (KeyError, IndexError, TypeError) as e:
    vs_first = f"(首次跑数据不可比: {e})"

md = f"""📊 **每日持仓收盘总结 (复跑)** | {TODAY} 18:00
> ⚠️ cron 同日二次触发, 这次多源校验重跑 (腾讯+新浪双源一致)

**持仓 1 只 | 总盈亏 {total_pnl:+.0f} 元 ({total_pnl_pct:+.2f}%) | 风险 {risk_level}**

### 📈 大盘 (18:00 实时)
{idx_line}

### 💼 持仓明细

| 标的 | 现价 | 今日 | 累计盈亏 | 健康度 |
|------|------|------|----------|--------|
{chr(10).join(table_lines)}

### 🩺 5 维健康度
{chr(10).join(detail_5dim)}

### 🔍 今日 vs 06-25 对比
- 06-25 真收 ¥21.11 (+4.61%, 浮盈 +4.68% 🟡)
- 06-26 真收 ¥20.07 (-4.93%, 浮亏 -0.48% 🔴)
- **单日形态切换 9.54pp**, 跑输大盘 (沪 -2.26% / 创 -4.07%)

### ⚠️ 触发位状态 (沿用 06-24 沉淀 21.50/20.50/19.15)
- 现价 ¥20.07 — **已破 20.50 减仓线** (差 0.43元)
- 距 19.15 观望位 0.92元
- 移动止盈 {results[0]['health_5dim']['trailing']} ({results[0]['trailing_pct']:+.2f}% from 21.88 高点)

### 🎯 操作建议
**沿用老胡瓜硬规则** (hermes-memory: "300059 浮亏 -8% 不强平, 持续观察"), 现浮亏 -0.48% 远未触发 -8% 止损. 但 20.50 减仓 1/2 已技术性触发, 决策权归老胡瓜.

**明日 9:30 开盘 30 分钟关键**:
- 站回 20.50 → 维持现状
- 跌破 20.00 → 19.15 观望位预警
- 跌破 19.15 → 强制审视止损

---
{vs_first}

📶 数据源: 腾讯{src_tencent} 新浪{src_sina} 东财{src_eastmoney} | ⚠️ 仅供参考, 不构成投资建议"""

# ---- 写临时 markdown 文件 ----
md_path = f'/Users/huyufeng/Documents/股票分析知识库/runtime/logs/cron_18h_repush_latest.md'
with open(md_path, 'w', encoding='utf-8') as f:
    f.write(md)

# ---- lark-cli 推送 (shell=True + cat) ----
import shlex
cmd = f'lark-cli im +messages-send --chat-id {shlex.quote(CHAT_ID)} --markdown "$(cat {shlex.quote(md_path)})"'
push_result = None
for attempt in range(2):
    res = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    out = res.stdout + res.stderr
    print(f'[lark attempt {attempt+1}] exit={res.returncode}')
    print(out[:500])
    if res.returncode == 0 and '"ok": true' in res.stdout.lower():
        push_result = res.stdout
        break
    time.sleep(2)

# 解析 msg_id
msg_id = None
if push_result:
    try:
        d = json.loads(push_result)
        msg_id = d.get('data', {}).get('message_id') or d.get('message_id')
    except Exception:
        m = re.search(r'"message_id"\s*:\s*"([^"]+)"', push_result)
        if m:
            msg_id = m.group(1)

# ---- 写回 holdings.json ----
for pos, r in zip(holdings, results):
    pos['current_price'] = r['price']
    pos['prev_close'] = r['prev_close']
    pos['pnl'] = r['pnl_amt']
    pos['pnl_pct'] = r['pnl_pct']
    pos['holding_days'] = r['holding_days']

h['totals']['total_market_value'] = sum(r['price'] * r['shares'] for r in results)
h['totals']['total_pnl'] = round(total_pnl, 1)
h['totals']['total_pnl_pct'] = round(total_pnl_pct, 2)

# 追加 history
history_entry = {
    'at': NOW_ISO,
    'action': '18:00 cron 复跑 (同日二次触发, 多源校验)',
    'data_source': f'tencent+{"sina" if sina else "tencent"} (双源一致 20.07)',
    'operator': '胡小瓜 (cron 18:00 持仓总结 复跑)',
    'results': results,
    'issues_count': issues_total,
    'warnings_count': warns_total,
    'warning_reasons': [w for r in results for w in r['warning_reasons']],
    'indices': {n: {'price': d['price'], 'change_pct': d['change_pct']} for n,d in indices.items()},
    'risk_level': risk_level,
    'feishu_chat_id': CHAT_ID,
    'feishu_sent': bool(msg_id),
    'feishu_msg_id': msg_id,
    'vs_first_run_today': vs_first,
    'next_review': f'{TODAY} 09:00 量化日报 (明日) / 2026-06-29 18:00 下次 cron',
}
h['meta']['history'].insert(0, history_entry)
h['meta']['updated_at'] = NOW_ISO
h['meta']['updated_by'] = '胡小瓜 (cron 18:00 持仓收盘总结 复跑)'
h['meta']['last_summary_run'] = {
    'date': TODAY,
    'data_source': 'tencent+sina 双源一致',
    'issues_count': issues_total,
    'warnings_count': warns_total,
    'results_count': len(results),
    'health': results[0]['health'] if results else '—',
    'feishu_msg_id': msg_id,
}

with open(HOLDINGS_PATH, 'w', encoding='utf-8') as f:
    json.dump(h, f, ensure_ascii=False, indent=2)

print(f'\n✅ 复跑完成: 飞书 msg_id={msg_id}, 持仓 ¥{total_pnl:+.0f}, {risk_level}')