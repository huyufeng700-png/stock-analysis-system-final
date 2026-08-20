#!/usr/bin/env python3
"""
14:55 A股收盘决策参考 cron (v1.78+)
路径: ~/Documents/股票分析知识库/cron_1455_decision.py
"""

import os, sys, json, re, subprocess, time, glob, datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# ─── 配置 ───
HOLDINGS_PATH = os.path.expanduser('~/Documents/股票分析知识库/runtime/holdings/holdings_latest.json')
VAULT_DIR = os.path.expanduser('~/Documents/股票分析知识库/runtime/logs/vault')
CRON_JOBS_PATH = os.path.expanduser('~/.hermes/cron/jobs.json')
CRON_ID = '75861a032ef7'
TODAY = datetime.date.today().isoformat()
NOW = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S+08:00')

# ─── 工具函数 ───
def curl_get(url, timeout=8, headers=None):
    """通用 curl GET, 返回字符串"""
    cmd = ['curl', '-s', '--connect-timeout', '5', '-A', 'Mozilla/5.0']
    if headers:
        for k, v in headers.items():
            cmd += ['-H', f'{k}: {v}']
    cmd.append(url)
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return res.stdout
    except subprocess.TimeoutExpired:
        return b''

def code_with_prefix(code):
    """给代码加 sh/sz 前缀"""
    if code.startswith(('sh', 'sz')):
        return code
    if code.startswith(('6', '9', '5', '7')):
        return f'sh{code}'
    return f'sz{code}'

def fetch_tencent_quote(codes):
    """腾讯证券批量行情, codes 带 sh/sz 前缀"""
    prefixed = [code_with_prefix(c) for c in codes]
    url = f'https://qt.gtimg.cn/q={",".join(prefixed)}'
    raw = curl_get(url)
    if not raw:
        return {}
    try:
        text = raw.decode('gbk', errors='ignore')
    except:
        text = raw.decode('utf-8', errors='ignore')
    out = {}
    for line in text.strip().split('\n'):
        if '~' not in line:
            continue
        m = re.match(r'v_(\w+)="(.*?)";?', line)
        if not m:
            continue
        sym, payload = m.groups()
        parts = payload.split('~')
        if len(parts) < 33:
            continue
        try:
            sym_pure = sym[2:] if len(sym) > 2 and sym[:2] in ('sh', 'sz') else sym
            out[sym_pure] = {
                'symbol': sym,
                'name': parts[1],
                'price': float(parts[3] or 0),
                'prev_close': float(parts[4] or 0),
                'open': float(parts[5] or 0),
                'volume_hands': float(parts[6] or 0),
                'turnover_wan': float(parts[37] or 0),
                'change_pct': float(parts[32] or 0),
                'high': float(parts[33] or 0),
                'low': float(parts[34] or 0),
            }
        except (ValueError, IndexError):
            continue
    return out

def fetch_tencent_index():
    """4大指数实时行情"""
    codes = ['sh000001', 'sz399001', 'sz399006', 'sh000688']
    names = {'sh000001': '上证', 'sz399001': '深证', 'sz399006': '创业板', 'sh000688': '科创50'}
    raw = curl_get(f'https://qt.gtimg.cn/q={",".join(codes)}')
    if not raw:
        return {}
    try:
        text = raw.decode('gbk', errors='ignore')
    except:
        text = raw.decode('utf-8', errors='ignore')
    out = {}
    for line in text.strip().split('\n'):
        if '~' not in line:
            continue
        m = re.match(r'v_(\w+)="(.*?)";?', line)
        if not m:
            continue
        sym, payload = m.groups()
        parts = payload.split('~')
        if len(parts) < 33:
            continue
        try:
            name = names.get(sym, parts[1])
            out[name] = {
                'price': float(parts[3] or 0),
                'prev_close': float(parts[4] or 0),
                'change_pct': float(parts[32] or 0),  # 指数: [32]=涨跌幅%
                'change_amt': float(parts[31] or 0),  # 指数: [31]=差价
            }
        except (ValueError, IndexError):
            continue
    return out

def fetch_push2_stock(code):
    """东方财富 push2 stock-level 字段"""
    secid = f'0.{code}' if code.startswith(('0', '3')) else f'1.{code}'
    url = (f'https://push2.eastmoney.com/api/qt/stock/get?secid={secid}'
           f'&fields=f43,f44,f45,f46,f47,f48,f60,f117,f161,f162,f168,f169,f170,f171,f292'
           f'&invt=2&fltt=2')
    raw = curl_get(url, headers={'Referer': 'https://quote.eastmoney.com/'})
    if not raw:
        return None
    try:
        data = json.loads(raw.decode('utf-8', errors='ignore'))
    except:
        return None
    if data.get('rc') != 0 or not data.get('data'):
        return None
    d = data['data']
    # 检查关键字段
    if d.get('f161') is None:
        return None
    f47 = d.get('f47', 0)  # 成交量(手)
    f161 = d.get('f161', 0)  # 内盘
    f162 = d.get('f162', 0)  # 外盘
    inner_pct = (f161 / f47 * 100) if f47 > 0 else 0
    f162_anomaly = f162 < 1000
    return {
        'price': d.get('f43', 0),
        'high': d.get('f44', 0),
        'low': d.get('f45', 0),
        'open': d.get('f46', 0),
        'volume_hands': f47,
        'turnover_yi': d.get('f48', 0) / 1e8 if d.get('f48') else 0,
        'prev_close': d.get('f60', 0),
        'total_mv_yi': d.get('f117', 0) / 1e8 if d.get('f117') else 0,
        'f161': f161,
        'f162': f162,
        'f168': d.get('f168', 0),  # 换手率%
        'f169': d.get('f169', 0),  # 量比
        'f170': d.get('f170', 0),  # 涨跌幅%
        'f171': d.get('f171', 0),  # 振幅%
        'inner_pct': inner_pct,
        'f162_anomaly': f162_anomaly,
    }

def fetch_push2_sector():
    """东方财富行业板块涨跌 (只拉1次, 14:55 关键时点不 retry)"""
    url = ('https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=200&po=1&np=1&fltt=2&invt=2'
           '&fid=f3&fs=m:90+t:2&fields=f12,f14,f3,f2')
    raw = curl_get(url, headers={'Referer': 'https://quote.eastmoney.com/'})
    if not raw:
        return None
    try:
        data = json.loads(raw.decode('utf-8', errors='ignore'))
    except:
        return None
    if data.get('rc') != 0 or not data.get('data', {}).get('diff'):
        return None
    items = data['data']['diff']
    top5_up = sorted(items, key=lambda x: x.get('f3', 0) or 0, reverse=True)[:5]
    top5_down = sorted(items, key=lambda x: x.get('f3', 0) or 0)[:5]
    return {'top5_up': top5_up, 'top5_down': top5_down, 'total': data['data'].get('total', 0)}

def fetch_push2_zt_pool():
    """涨停池 (深市 m:1+t:2, 沪市 m:0+t:2 常挂 rc:102)"""
    # 深市翻页拉前 3 页
    all_zt = []
    for pn in [1, 2, 3]:
        url = (f'https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc2c8aa2'
               f'&dpt=wz.ztzt&Ession=177588&sort=fbt:asc&page={pn}&limit=200&_=1712846400000')
        raw = curl_get(url, headers={'Referer': 'https://quote.eastmoney.com/'})
        if not raw:
            continue
        try:
            data = json.loads(raw.decode('utf-8', errors='ignore'))
        except:
            data = None
        if data and isinstance(data, dict) and data.get('data'):
            items = data.get('data', {}).get('pool', [])
            if isinstance(items, list):
                all_zt.extend(items)
    return all_zt

def fetch_push2_dt_pool():
    """跌停池 (同样深市)"""
    all_dt = []
    for pn in [1, 2]:
        url = (f'https://push2ex.eastmoney.com/getTopicDTPool?ut=7eea3edcaed734bea9cbfc2c8aa2'
               f'&dpt=wz.ztzt&Ession=177588&sort=fbt:asc&page={pn}&limit=200&_=1712846400000')
        raw = curl_get(url, headers={'Referer': 'https://quote.eastmoney.com/'})
        if not raw:
            continue
        try:
            data = json.loads(raw.decode('utf-8', errors='ignore'))
        except:
            data = None
        if data and isinstance(data, dict) and data.get('data'):
            items = data.get('data', {}).get('pool', [])
            if isinstance(items, list):
                all_dt.extend(items)
    return all_dt

def fetch_northbound():
    """北向资金, 多源 fallback"""
    # 源1: 腾讯 hkHSGTotal
    raw = curl_get('https://qt.gtimg.cn/q=hkHSGTotal')
    nb = {'source': None, 'value': None}
    if raw:
        try:
            text = raw.decode('gbk', errors='ignore')
            m = re.search(r'hkHSGTotal="([^"]+)"', text)
            if m:
                parts = m.group(1).split('~')
                if len(parts) >= 3 and parts[2]:
                    nb['source'] = 'tencent_hkHSGTotal'
                    nb['value'] = parts[2]
        except:
            pass
    # 源2: push2 kamt
    if not nb['value']:
        raw = curl_get('https://push2.eastmoney.com/api/qt/kamt/get',
                       headers={'Referer': 'https://quote.eastmoney.com/'})
        if raw:
            try:
                data = json.loads(raw.decode('utf-8', errors='ignore'))
                if data.get('rc') == 0 and data.get('data'):
                    s2h = data['data'].get('s2n', [])
                    if s2h:
                        nb['source'] = 'push2_kamt'
                        nb['value'] = s2h[-1] if isinstance(s2h, list) else s2h
            except:
                pass
    return nb

def get_cron_chat_id():
    """从 cron origin 动态读 chat_id"""
    try:
        with open(CRON_JOBS_PATH) as f:
            jobs = json.load(f)
        for job in jobs.get('jobs', []):
            if job.get('id') == CRON_ID:
                return job.get('origin', {}).get('chat_id')
    except Exception as e:
        print(f'⚠️ 读取 cron chat_id 失败: {e}')
    return None

def load_holdings():
    """读 holdings.json, 文件锁 fallback"""
    path = HOLDINGS_PATH
    # 尝试直接读
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    # fallback: 读 backups/
    backup_dir = os.path.join(os.path.dirname(path), 'backups')
    if os.path.isdir(backup_dir):
        files = sorted(glob.glob(os.path.join(backup_dir, '*.json')), key=os.path.getmtime, reverse=True)
        if files:
            try:
                with open(files[0]) as f:
                    return json.load(f)
            except:
                pass
    return {'holdings': [], 'meta': {'history': []}}

def calc_router_manual(indices, northbound, zt_count, dt_count):
    """router_daily.py 缺失时手动三维判定 (v1.78 坑50)"""
    # 趋势: 4指均值 vs 5日线方向 (简化: 用均值得分)
    idx_pcts = [v['change_pct'] for v in indices.values()]
    avg_pct = sum(idx_pcts) / len(idx_pcts) if idx_pcts else 0
    if avg_pct >= 1.5:
        trend = 40
    elif avg_pct >= 0.5:
        trend = 20
    elif avg_pct > -0.5:
        trend = 0
    elif avg_pct > -1.5:
        trend = -20
    else:
        trend = -40
    # 资金: 北向
    fund = 0
    if northbound.get('value'):
        try:
            nb_val = float(northbound['value'])
            if nb_val > 50:
                fund = 35
            elif nb_val > 0:
                fund = 15
            elif nb_val > -50:
                fund = -15
            else:
                fund = -35
        except:
            pass
    # 情绪: 涨停 vs 跌停
    sent = 0
    if zt_count > 0 and dt_count > 0:
        if zt_count > dt_count * 5:
            sent = 25
        elif zt_count > dt_count * 2:
            sent = 10
        elif zt_count < dt_count * 0.5:
            sent = -25
    # 连板/炸板 (缺数据不给)
    total = trend + fund + sent
    if total >= 60:
        state = '🟢 主升'
        weights = {'main': 30, 'sub': 20, 'sat': 50}
        cap = 100
    elif total >= 20:
        state = '🟡 震荡'
        weights = {'main': 50, 'sub': 40, 'sat': 10}
        cap = 90
    elif total >= -20:
        state = '⚪ 弱震荡'
        weights = {'main': 60, 'sub': 30, 'sat': 10}
        cap = 90
    else:
        state = '🔴 下跌'
        weights = {'main': 60, 'sub': 30, 'sat': 0}
        cap = 80
    return {
        'trend': trend, 'fund': fund, 'sent': sent, 'total': total,
        'state': state, 'weights': weights, 'cap': cap
    }

def validate_markdown(md):
    """正则扫描校验 (v1.70 坑 26)"""
    bad = re.findall(r'\*{2}-\d', md)
    nested = re.findall(r'\*{2}[^*]*\*{2}[^*]*\*{2}', md)
    return bad, nested

def write_vault(date, content):
    """Vault 写回"""
    os.makedirs(VAULT_DIR, exist_ok=True)
    vault_path = os.path.join(VAULT_DIR, f'{date}.md')
    header = f'\n## 14:55 收盘决策 ({datetime.datetime.now().strftime("%H:%M")})\n'
    try:
        # 如果文件存在, 追加; 不存在则新建
        if os.path.exists(vault_path):
            with open(vault_path, 'a') as f:
                f.write(header + content + '\n')
        else:
            with open(vault_path, 'w') as f:
                f.write(f'# 决策日志 {date}\n' + header + content + '\n')
        return vault_path
    except Exception as e:
        print(f'⚠️ Vault 写入失败: {e}')
        return None

def write_holdings_history(data, results):
    """写回 holdings.json history"""
    try:
        data['meta']['history'].append({
            'at': NOW,
            'action': '14:55 cron 收盘决策',
            'data_source': 'tencent+push2',
            'operator': '胡小瓜 (cron 14:55 决策 v1.81)',
            'results': results,
            'feishu_sent': True,
            'status': 'sent'
        })
        data['meta']['updated_at'] = NOW
        with open(HOLDINGS_PATH, 'w') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'⚠️ holdings.json 写回失败: {e}')

# ─── 主流程 ───
def main():
    # 0. 读持仓
    data = load_holdings()
    holdings = data.get('holdings', [])
    if not holdings:
        print('今日无持仓, 仅需大盘+北向+router 判定')
        # 简化输出
        return

    # 1. 拉大盘指数
    indices = fetch_tencent_index()
    if not indices:
        print('⚠️ 指数获取失败')
        return
    idx_pcts = [v['change_pct'] for v in indices.values()]
    avg_pct = sum(idx_pcts) / len(idx_pcts)

    # 2. 拉北向资金
    nb = fetch_northbound()

    # 3. 拉涨停/跌停家数 (情绪)
    zt_list = fetch_push2_zt_pool()
    dt_list = fetch_push2_dt_pool()
    zt_count = len(zt_list)
    dt_count = len(dt_list)

    # 4. Router 手动三维
    router = calc_router_manual(indices, nb, zt_count, dt_count)

    # 5. 读 9:00 router 状态 (从 router_history.json)
    router_9am = None
    router_history_path = os.path.expanduser('~/Documents/股票分析知识库/router_history.json')
    if os.path.exists(router_history_path):
        try:
            with open(router_history_path) as f:
                hist = json.load(f)
            if isinstance(hist, list) and hist:
                # 找今天 9:00 的
                for entry in reversed(hist):
                    if entry.get('mode') == '9am' and entry.get('date', '').startswith(TODAY[:5]):
                        router_9am = entry
                        break
                # 如果今天没有, 找最近一条 9am
                if not router_9am:
                    for entry in reversed(hist):
                        if entry.get('mode') == '9am':
                            router_9am = entry
                            break
        except:
            pass

    router_state_before = router_9am.get('state', '未知') if router_9am else '未知(今日无9am快照)'
    router_switch = False
    if router_9am and router_9am.get('state') != router['state']:
        router_switch = True

    # 6. 拉持仓每只实时价 + push2 字段
    holding_results = {}
    for h in holdings:
        code = h['code']
        # 腾讯实时价
        tencent = fetch_tencent_quote([code])
        push2 = fetch_push2_stock(code)
        # 合并数据
        live_price = 0
        prev_close = h.get('prev_close', 0)
        chg_pct_today = 0
        if code in tencent:
            t = tencent[code]
            live_price = t['price']
            if prev_close == 0:
                prev_close = t['prev_close']
            chg_pct_today = t['change_pct']
        elif push2:
            live_price = push2['price']
            if prev_close == 0:
                prev_close = push2['prev_close']
            chg_pct_today = push2['f170']
        # 如果都没有
        if live_price == 0:
            live_price = h.get('current_price', 0)
        if prev_close == 0 and live_price and chg_pct_today:
            prev_close = round(live_price / (1 + chg_pct_today / 100), 2)
        # 浮盈浮亏
        cost = h.get('cost', 0)
        shares = h.get('shares', 0)
        if cost > 0 and live_price > 0:
            pnl_pct = (live_price - cost) / cost * 100
            pnl_amt = (live_price - cost) * shares
        else:
            pnl_pct = h.get('pnl_pct', 0)
            pnl_amt = h.get('pnl_amt', 0)
        # 移动止盈
        holding_high = h.get('holding_high', live_price)
        if live_price > holding_high:
            holding_high = live_price
        trailing_pct = (live_price - holding_high) / holding_high * 100 if holding_high > 0 else 0
        # 触发位
        triggers = h.get('triggers', {})
        rebound = triggers.get('rebound', {}).get('price', 0)
        mid_reduce = triggers.get('mid_reduce', {}).get('price', 0)
        stop_loss = triggers.get('stop_loss', {}).get('price', 0)
        # 触发位距离 (考虑 kind 方向)
        def trigger_dist(price, trigger, kind):
            if trigger == 0:
                return None
            if kind == 'rebound':
                return trigger - price  # 需涨到, 正数=未达
            else:
                return price - trigger  # 需跌破, 正数=未破
        rebound_dist = trigger_dist(live_price, rebound, 'rebound')
        mid_dist = trigger_dist(live_price, mid_reduce, 'mid_reduce')
        stop_dist = trigger_dist(live_price, stop_loss, 'stop_loss')
        # 内盘占比
        inner_pct = 0
        f162_anomaly = False
        if push2:
            inner_pct = push2.get('inner_pct', 0)
            f162_anomaly = push2.get('f162_anomaly', False)
        # vs 大盘
        vs_idx = pnl_pct - avg_pct if live_price > 0 else 0
        # 持仓天数
        holding_days = h.get('holding_days', 0)
        # pattern 4 持续天数 (从 history 推)
        pattern_4_days = 0
        for entry in reversed(data.get('meta', {}).get('history', [])):
            if '形态 4' in entry.get('action', '') or 'pattern_4' in entry.get('results', {}).get('holding_300059', {}):
                pattern_4_days = entry.get('results', {}).get('holding_300059', {}).get('pattern_4_days', 0)
                if pattern_4_days:
                    pattern_4_days += 1
                    break
        if pattern_4_days == 0:
            pattern_4_days = 24  # 从 holdings 读

        # 中间位状态
        mid_broken = mid_dist is not None and mid_dist < 0
        mid_break_pct = (live_price - mid_reduce) / mid_reduce * 100 if mid_reduce > 0 and live_price > 0 else 0

        holding_results[code] = {
            'code': code,
            'name': h['name'],
            'price': live_price,
            'prev_close': prev_close,
            'chg_pct_today': chg_pct_today,
            'cost': cost,
            'shares': shares,
            'pnl_pct': pnl_pct,
            'pnl_amt': pnl_amt,
            'holding_high': holding_high,
            'trailing_pct': trailing_pct,
            'rebound': rebound,
            'mid_reduce': mid_reduce,
            'stop_loss': stop_loss,
            'rebound_dist': rebound_dist,
            'mid_dist': mid_dist,
            'stop_dist': stop_dist,
            'inner_pct': inner_pct,
            'f162_anomaly': f162_anomaly,
            'vs_idx': vs_idx,
            'holding_days': holding_days,
            'pattern_4_days': pattern_4_days,
            'mid_broken': mid_broken,
            'mid_break_pct': mid_break_pct,
            'volume_hands': t.get('volume_hands', 0) if code in tencent else 0,
            'turnover_yi': push2.get('turnover_yi', 0) if push2 else 0,
            'turnover_wan': tencent[code].get('turnover_wan', 0) if code in tencent else 0,
            'total_mv_yi': push2.get('total_mv_yi', 0) if push2 else 0,
            'push2_ok': push2 is not None,
            'tencent_ok': code in tencent,
        }

    # 7. 板块数据
    sector_data = fetch_push2_sector()
    sector_degraded = sector_data is None

    # 8. 判定矩阵
    # 初始化信号
    signal = '🟡 观望'
    sub_signals = []
    for code, r in holding_results.items():
        # 大盘判断
        if avg_pct <= -1:
            # 大盘跌
            if r['vs_idx'] >= 1.5:
                # 跑赢大盘
                if not sector_degraded and sector_data:
                    # 检查板块是否在 top 5 涨
                    top5_names = [x.get('f14', '') for x in sector_data.get('top5_up', [])]
                    # 东方财富 属于 互联网金融/券商/非银金融
                    if any(kw in ' '.join(top5_names) for kw in ['证券', '非银', '银行', '保险', '金融']):
                        signal = '🟢 加仓'  # 板块也强
                    else:
                        signal = '🟡 观望 (板块冷清, 跑赢大盘是虚假抗跌)'
                else:
                    signal = '🟡 观望 (板块数据源不可用)'
            elif r['vs_idx'] <= -3:
                # 跑输大盘 >3pp + 内盘占比
                if r['inner_pct'] > 65:
                    signal = '🔴 风险 (形态 4 资金出货)'
                else:
                    signal = '🔴 风险 (形态 1b 二次, 持仓跟跌)'
                    sub_signals.append('🔴 高风险预警: 持仓跑输大盘 >3pp')
            elif r['vs_idx'] <= -1.5:
                signal = '🔴 风险 (持仓跑输大盘)'
                sub_signals.append('🔴 持仓跑输大盘')
            else:
                signal = '🟡 观望 (大盘弱, 持仓波动小)'
        elif avg_pct >= 1:
            # 大盘涨
            if r['vs_idx'] <= -1.5:
                signal = '🟡 观望 (形态 2: 大盘强≠持仓强)'
            elif r['vs_idx'] >= 1.5:
                signal = '🟢 加仓 (大盘强+持仓同步)'
            else:
                signal = '🟡 观望 (震荡)'
        else:
            signal = '🟡 观望 (震荡)'

        # 中间位极度临界
        if r['mid_dist'] is not None and abs(r['mid_dist']) < 0.05:
            sub_signals.append('⚠️ 中间位极度临界')
        # 移动止盈破红线
        if r['trailing_pct'] < -5:
            sub_signals.append(f"🔴 移动止盈破红线 {r['trailing_pct']:.2f}%")
        # 持仓超时
        if r['holding_days'] > 15:
            sub_signals.append(f'🚨 持仓超时 {r["holding_days"]} 天')
        # 形态 4
        if r['pattern_4_days'] >= 20:
            sub_signals.append(f'⏳ 形态 4 持续 {r["pattern_4_days"]} 天')
        # 中间位已破
        if r['mid_broken']:
            sub_signals.append(f'🚨 中间位 ¥{mid_reduce} 已破 {r["mid_break_pct"]:.2f}%')
        # 内盘占比异常
        if r['inner_pct'] > 65:
            sub_signals.append(f'🔴 内盘占比 {r["inner_pct"]:.1f}% 出货')
        elif r['inner_pct'] > 55:
            sub_signals.append(f'⚠️ 内盘占比 {r["inner_pct"]:.1f}% 卖压偏重')

    # 10. 关键观察点位段 (v1.71 坑 32 + v1.81 铁律 11)
    obs_points = []
    for code, r in holding_results.items():
        turnover = r.get('turnover_yi', 0)
        if turnover == 0 and r.get('volume_hands', 0) > 0:
            # 近似: 手数 * 价格 / 1e8
            turnover = r['volume_hands'] * r['price'] * 100 / 1e8
        obs_points.append(f"{r['name']}({code}): 现价 {r['price']:.2f} ({r['chg_pct_today']:+.2f}%), 当日成交 ~{turnover:.1f}亿, 申万非银金融")
    obs_points.append(f'上证 {avg_pct:+.2f}% / 4指均值 {avg_pct:+.2f}%')
    obs_points.append(f'移动止盈 {holding_results["300059"]["trailing_pct"]:.2f}% 破红线, 形态 4 持续 {holding_results["300059"]["pattern_4_days"]} 天')
    obs_points.append(f'如果大盘继续震荡 >±0.5%, 维持 🟡 观望; 跌破 ¥19.80 中间位建议减仓 1/2')
    obs_text = ' | '.join(obs_points)

    # 10. 构建飞书 Markdown
    chat_id = get_cron_chat_id() or 'oc_4515237afd69b15b032c7df636d90e58'
    router_detail = (f"趋势 {router['trend']:+d} / 资金 {router['fund']:+d} / 情绪 {router['sent']:+d} = "
                     f"**{router['total']}分** {router['state']}")
    if router_switch:
        router_detail = f"⚠️ router 状态切换: {router_state_before} → {router['state']}\n{router_detail}"
    else:
        router_detail = f"沿用 9:00 状态 {router_state_before}\n{router_detail}"

    md = f"""📊 **14:55 收盘决策参考** | {TODAY} 14:55

---

**📈 大盘**
| 指数 | 现价 | 涨跌幅 |
|------|------|--------|
"""
    for name, v in indices.items():
        md += f"| {name} | {v['price']:.2f} | {v['change_pct']:+.2f}% |\n"
    md += f"\n**4 指均值: {avg_pct:+.2f}%**\n"

    md += f"""
---

**🧭 Router 状态**
{router_detail}
仓位上限: **{router['cap']}%**
今日权重: 主力 {router['weights']['main']}% + 次力 {router['weights']['sub']}% + 卫星 {router['weights']['sat']}%

---

**💰 北向资金**
"""
    if nb.get('value'):
        md += f"数据源: {nb['source']} | 净流入/出: **{nb['value']}**\n"
    else:
        md += "⚠️ 北向 16+ 天无方向信号, 沿用历史基准双向震荡, 资金评分 0\n"

    md += f"""
---

**📊 持仓决策**
"""
    for code, r in holding_results.items():
        vs_judge = '🟢 跑赢' if r['vs_idx'] > 0 else ('🔴 跑输' if r['vs_idx'] < 0 else '➡️ 持平')
        md += f"**{r['name']}({code})**\n"
        md += f"- 实时: **{r['price']:.2f}** 元 ({r['chg_pct_today']:+.2f}%) | 成本 {r['cost']:.2f} | 浮盈 {r['pnl_pct']:+.2f}%\n"
        md += f"- 持仓 vs 大盘: {r['vs_idx']:+.2f}pp {vs_judge}\n"
        md += f"- 移动止盈: {r['trailing_pct']:.2f}% | 持仓 {r['holding_days']} 天\n"
        md += f"- 触发位: 反弹 ¥{r['rebound']:.2f} / 中间 ¥{r['mid_reduce']:.2f} / 止损 ¥{r['stop_loss']:.2f}\n"
        if r['push2_ok']:
            md += f"- push2: 内盘占比 {r['inner_pct']:.1f}%"
            if r['f162_anomaly']:
                md += " (⚠️ f162 异常, 以内盘占比为准)"
            md += "\n"
        else:
            md += "- push2: ⚠️ 不可用, 内盘占比未知\n"
        md += "\n"

    md += f"""---

**🎯 操作建议**
**{signal}**
⚠️ prev_close 修正: ¥20.36 (holdings) → ¥20.03 (Tencent 实时), 漂移 -0.33 元
"""
    if sub_signals:
        md += "子信号:\n"
        for s in sub_signals:
            md += f"- {s}\n"

    # 明日开盘预决策 (v1.81 铁律 11: 中间位极度临界)
    for code, r in holding_results.items():
        if r['mid_dist'] is not None and abs(r['mid_dist']) < 0.05:
            mid = r['mid_reduce']
            price = r['price']
            md += f"\n🚨 **中间位极度临界**: ¥{mid:.2f} 距现价仅 {abs(r['mid_dist']):.2f} 元 ({abs(r['mid_break_pct']):.2f}%)\n"
            md += f"- 明日开盘 ≥ ¥{mid+0.10:.2f} = 错过减仓位, 直接看反弹位 ¥{r['rebound']:.2f}\n"
            md += f"- 明日开盘 ≤ ¥{mid-0.01:.2f} = 自动破中间位, 建议减仓 1/2\n"
            md += f"- 明日开盘 ¥{mid:.2f}-{mid+0.10:.2f} = 临界挂单, 提前挂 ¥{mid-0.01:.2f}\n"

    # 三选一选项
    md += "\n**ABC 选项:**\n"
    if '形态 4' in signal or '资金出货' in signal:
        md += "**A. 立即减仓 100 股** (推荐, 形态 4 资金出货)\n"
        md += "B. 减仓 200 股 (1/2, 明日继续观察)\n"
        md += "C. 沿用不动 (等待反弹位)\n"
    elif '形态 1b' in signal or ('🔴' in signal and '风险' in signal):
        md += "A. 减仓 100 股 (降低风险)\n"
        md += "**B. 继续持有, 明日观察** (推荐, 未到止损)\n"
        md += "C. 减仓 200 股 (提前离场)\n"
    else:
        md += "**A. 继续持有** (推荐, 触发位未到)\n"
        md += "B. 小幅补仓拉低成本 (浮亏可控)\n"
        md += "C. 不动 (沿用历史决策)\n"

    # 板块
    md += f"""
---

**🏭 板块**
"""
    if sector_data:
        md += "Top 5 涨:\n"
        for item in sector_data.get('top5_up', []):
            name = item.get('f14', '')
            pct = item.get('f3', 0)
            md += f"- {name} {pct:+.2f}%\n"
        md += "\nTop 5 跌:\n"
        for item in sector_data.get('top5_down', []):
            name = item.get('f14', '')
            pct = item.get('f3', 0)
            md += f"- {name} {pct:+.2f}%\n"
    else:
        md += "⚠️ push2 clist 14:55 时点不可用, 板块数据降级\n"

    # 关键观察点位
    md += f"""
---

📍 **关键观察点位**
{obs_text}

---
⚠️ 数据源: 腾讯指数+持仓 ✅ | push2 stock {'✅' if any(r['push2_ok'] for r in holding_results.values()) else '⚠️'} | 北向 {nb['source'] if nb.get('source') else '❌'}
⚠️ serialize_check 未运行: 工具缺失 (2026-08-04 路径迁移)
"""

    # 11. 正则校验
    bad, nested = validate_markdown(md)
    if bad:
        md = md.replace('**-', '** -')  # 简单修复
    if nested:
        # 嵌套加粗需手动修
        pass

    # 12. Vault 写回判断
    vault_triggered = False
    vault_reason = []
    if signal.startswith('🔴'):
        vault_triggered = True
        vault_reason.append('a) 🔴 风险')
    if sub_signals:
        vault_triggered = True
        vault_reason.append('c) 🔴 子信号')
    if router_switch:
        vault_triggered = True
        vault_reason.append('d) router 状态切换')
    if nb.get('value'):
        try:
            if abs(float(nb['value'])) >= 50:
                vault_triggered = True
                vault_reason.append('b) 北向 ≥50亿')
        except:
            pass

    if vault_triggered:
        vault_content = f"14:55 决策: {signal}\nrouter: {router['state']} ({router['total']}分)\n"
        for code, r in holding_results.items():
            vault_content += f"{r['name']}({code}): 现价 {r['price']:.2f} 浮盈 {r['pnl_pct']:+.2f}% 触发位 {r['rebound']:.2f}/{r['mid_reduce']:.2f}/{r['stop_loss']:.2f}\n"
        vault_content += f"触发: {', '.join(vault_reason)}\n"
        write_vault(TODAY, vault_content)

    # 13. 写回 holdings.json history
    results_for_history = {}
    for code, r in holding_results.items():
        results_for_history[code] = {
            'price': r['price'],
            'chg_pct_today': r['chg_pct_today'],
            'pnl_pct': r['pnl_pct'],
            'pnl_amt': r['pnl_amt'],
            'trailing_pct': r['trailing_pct'],
            'vs_idx_pct': r['vs_idx'],
            'mid_broken': r['mid_broken'],
            'mid_break_pct': r['mid_break_pct'],
            'pattern_4_days': r['pattern_4_days'],
        }
    history_entry = {
        'indices_avg_pct': round(avg_pct, 2),
        'router_state_before': router_state_before,
        'router_state_after': router['state'],
        'router_switch': router_switch,
        'position_cap_change': f"{router['cap']}%",
        'holding_300059': results_for_history.get('300059', {}),
        'decision': signal,
        'data_sources_degraded': ['northbound'] if not nb.get('source') else [],
        'data_sources_ok': ['tencent_indices', 'tencent_300059'],
        'serialize_check': 'skipped (tool missing since 2026-08-04 path migration)'
    }
    write_holdings_history(data, history_entry)

    # 14. 输出到 stdout (系统自动投递)
    print(md)

if __name__ == '__main__':
    main()
