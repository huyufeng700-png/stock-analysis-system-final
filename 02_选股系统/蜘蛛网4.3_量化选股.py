#!/usr/bin/env python3
"""
蜘蛛网计划 v4.3 — A股量化选股系统（完整版）
新增：回测验证 + 飞书推送 + 定时任务
"""
import urllib.request
import json
import re
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))
from fallback_pool import FallbackPool

OUTPUT_DIR = "/Users/huyufeng/Documents/股票分析知识库/runtime/picks/"
KLINE_POOL = FallbackPool()


# ==================== P0-013 辅助: 板块分类 + 历史推荐聚合 ====================
# 复用 scripts/sector_concentration.py 已有逻辑,避免双维护

_SECTOR_KEYWORDS = {
    "银行": ["银行", "工商", "建设", "农业", "招商", "兴业", "浦发", "交通"],
    "券商": ["证券", "华泰", "国泰", "中信"],
    "电力": ["电力", "长江"],
    "煤炭": ["煤业", "神华"],
    "新能源": ["核电", "水电"],
}


def classify_sector(name: str) -> str:
    """根据股票名称关键词推断板块 (P0-013 用)。"""
    for sector, kws in _SECTOR_KEYWORDS.items():
        if any(k in name for k in kws):
            return sector
    return "其他"


def _load_top5_history(days: int = 10) -> dict:
    """扫最近 N 天 spider v4.*.json, 聚合每只股票:
    - cnt:    Top5 出现次数
    - days:   连续在 Top5 的天数 (从最近一次出现往回数)

    返回 {code: {'cnt': int, 'days': int}}
    """
    root = Path(OUTPUT_DIR)
    files = sorted(root.glob("蜘蛛网v4.3_*.json"), reverse=True)[:days]
    code_cnt = {}
    code_day_flags = {}  # code -> [(date, in_top5)]
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            stocks = d.get("stocks") or d.get("results") or []
            top5_codes = {s.get("code") for s in stocks[:5] if s.get("code")}
            date_str = f.stem.split("_")[-1] if "_" in f.stem else f.stem
            for c in top5_codes:
                code_cnt[c] = code_cnt.get(c, 0) + 1
                code_day_flags.setdefault(c, []).append((date_str, True))
        except Exception:
            continue
    out = {}
    for code, cnt in code_cnt.items():
        # days: 连续在 Top5 的天数 (按日期排序, 从最近往回数连续 True)
        flags = sorted(code_day_flags[code], key=lambda x: x[0], reverse=True)
        days = 0
        for _, hit in flags:
            if hit:
                days += 1
            else:
                break
        out[code] = {"cnt": cnt, "days": days}
    return out


# ==================== 数据获取 ====================

def fetch_tencent_spot(codes_with_market):
    """腾讯证券实时行情"""
    codes = [f"{m}{c}" for m, c in codes_with_market]
    all_spots = []
    for i in range(0, len(codes), 50):
        batch = codes[i:i+50]
        url = f"http://qt.gtimg.cn/q={','.join(batch)}"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode('gbk', errors='replace')
                for line in raw.strip().split(';'):
                    if '~' not in line or len(line) < 20:
                        continue
                    parts = line.split('~')
                    if len(parts) < 50:
                        continue
                    try:
                        code_match = re.search(r'([szsh]{2}\d{6})', parts[0])
                        if not code_match:
                            continue
                        all_spots.append({
                            'code': code_match.group(1)[2:],
                            'name': parts[1],
                            'price': float(parts[3]) if parts[3] else 0,
                            'change_pct': float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                            'turnover': float(parts[37]) if len(parts) > 37 and parts[37] else 0,
                            'amplitude': float(parts[43]) if len(parts) > 43 and parts[43] else 0,
                        })
                    except (ValueError, IndexError):
                        continue
        except Exception as e:
            print(f"❌ 行情失败: {e}")
    return all_spots


def fetch_sina_kline(code, scale=240, datalen=60):
    """K线数据：统一走 fallback_pool（三源：新浪 → 腾讯 → 东财 push2his）。"""
    rows = KLINE_POOL.get_kline(code, scale=scale, datalen=datalen)
    if rows:
        df = pd.DataFrame(rows)
        for col in ['open', 'close', 'high', 'low', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
    return None


# ==================== 技术指标 ====================

def calculate_indicators(df):
    if df is None or len(df) < 20:
        return None
    df['ema5'] = df['close'].ewm(span=5).mean()
    df['ema10'] = df['close'].ewm(span=10).mean()
    df['ema20'] = df['close'].ewm(span=20).mean()
    ema12 = df['close'].ewm(span=12).mean()
    ema26 = df['close'].ewm(span=26).mean()
    df['dif'] = ema12 - ema26
    df['dea'] = df['dif'].ewm(span=9).mean()
    df['macd'] = (df['dif'] - df['dea']) * 2
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / (loss + 1e-10)
    df['rsi'] = 100 - (100 / (1 + rs))
    df['bb_mid'] = df['close'].rolling(20).mean()
    bb_std = df['close'].rolling(20).std()
    df['bb_upper'] = df['bb_mid'] + 2 * bb_std
    df['bb_lower'] = df['bb_mid'] - 2 * bb_std
    high = df['high']
    low = df['low']
    close = df['close'].shift(1)
    tr = pd.concat([high - low, abs(high - close), abs(low - close)], axis=1).max(axis=1)
    df['atr'] = tr.rolling(14).mean()
    df['vol_ma5'] = df['volume'].rolling(5).mean()
    df['vol_ratio'] = df['volume'] / (df['vol_ma5'] + 1e-10)
    return df


# ==================== 股票列表 ====================

def get_stock_list():
    return [
        ('sh', '600036'), ('sh', '601318'), ('sh', '600519'), ('sh', '601166'), ('sh', '600030'),
        ('sh', '601328'), ('sh', '600887'), ('sh', '601288'), ('sh', '600276'), ('sh', '601668'),
        ('sh', '600309'), ('sh', '601899'), ('sh', '600031'), ('sh', '601012'), ('sh', '600585'),
        ('sh', '601601'), ('sh', '600588'), ('sh', '601888'), ('sh', '600050'), ('sh', '601989'),
        ('sh', '600547'), ('sh', '601398'), ('sh', '600028'), ('sh', '601857'), ('sh', '600809'),
        ('sh', '601628'), ('sh', '600016'), ('sh', '601225'), ('sh', '600436'), ('sh', '601088'),
        ('sh', '600900'), ('sh', '601688'), ('sh', '600570'), ('sh', '601186'), ('sh', '600000'),
        ('sh', '601818'), ('sh', '600104'), ('sh', '601939'), ('sh', '600048'), ('sh', '601211'),
        ('sh', '600837'), ('sh', '601336'), ('sh', '600690'), ('sh', '601006'), ('sh', '600340'),
        ('sh', '601111'), ('sh', '600111'), ('sh', '601800'), ('sh', '600489'), ('sh', '601658'),
        ('sh', '600025'), ('sh', '601985'), ('sh', '600516'), ('sh', '601238'),  # 600383金地已清仓移除
        ('sz', '300059'), ('sz', '300750'), ('sz', '300124'), ('sz', '300015'), ('sz', '300408'),
        ('sz', '300142'), ('sz', '300760'), ('sz', '300033'), ('sz', '300450'), ('sz', '300274'),
        ('sz', '300496'), ('sz', '300661'), ('sz', '300782'), ('sz', '300347'), ('sz', '300136'),
        ('sz', '300003'), ('sz', '300413'), ('sz', '300595'), ('sz', '300676'), ('sz', '300832'),
        ('sz', '300896'), ('sz', '300999'), ('sz', '300979'), ('sz', '300919'),
        ('sz', '000538'), ('sz', '000878'), ('sz', '000975'), ('sz', '002039'), ('sz', '002116'),
        ('sz', '002267'), ('sz', '002428'), ('sz', '002501'), ('sh', '600497'), ('sh', '600995'),
        ('sh', '601107'), ('sh', '603027'),
        ('sh', '688981'), ('sh', '688256'), ('sh', '688012'), ('sh', '688041'), ('sh', '688111'),
        ('sh', '688169'), ('sh', '688223'), ('sh', '688396'), ('sz', '002230'), ('sz', '002236'),
        ('sz', '002415'), ('sz', '000977'), ('sz', '000063'), ('sz', '000034'), ('sz', '002049'),
        ('sz', '002153'), ('sz', '300474'), ('sz', '300223'), ('sz', '300115'), ('sz', '300308'),
        ('sz', '300502'),
    ]


# ==================== 选股引擎 ====================

def screen_stocks():
    """选股扫描"""
    print("🕷️ 蜘蛛网 v4.3 选股扫描...")
    stocks = get_stock_list()
    print(f"   监控 {len(stocks)} 只股票")
    
    spots = fetch_tencent_spot(stocks)
    print(f"   ✅ 获取 {len(spots)} 只实时行情")
    
    base = [s for s in spots if s['price'] > 0 and -3 < s['change_pct'] < 9.5 and s['turnover'] > 5000]
    base = [s for s in base if 'ST' not in s['name'] and '退' not in s['name']]
    print(f"   基础过滤后: {len(base)} 只")
    
    results = []
    # P0-013 修复 (2026-07-03): 历史推荐聚合, 给 cnt_in_top5 / days_in_top5 喂数
    history_stats = _load_top5_history(days=10)
    for i, stock in enumerate(base):
        code = stock['code']
        market = 'sh' if code.startswith('6') else 'sz'
        df = fetch_sina_kline(f"{market}{code}", 240, 60)
        if df is None or len(df) < 20:
            continue

        df = calculate_indicators(df)
        if df is None:
            continue

        latest = df.iloc[-1]
        prev = df.iloc[-2]

        # P0-013: vol_ratio NaN 容错 (新浪 K 线偶尔空 volume 列) — NaN 当无信号,不扣不加
        vol_ratio = latest.get('vol_ratio') if pd.notna(latest.get('vol_ratio')) else 1.0
        # P0-013: 用 sector_concentration.classify 推断板块
        stock['sector'] = classify_sector(stock['name'])
        # P0-013: 从历史 json 聚合 cnt/days (Top5 出现次数 / 连续出现天数)
        h = history_stats.get(code, {'cnt': 0, 'days': 0})
        stock['cnt_in_top5'] = h['cnt']
        stock['days_in_top5'] = h['days']
        
        score = 0
        if latest['ema5'] > latest['ema10'] > latest['ema20']: score += 2
        if latest['close'] > latest['ema20']: score += 1
        if latest['dif'] > latest['dea'] and prev['dif'] <= prev['dea']: score += 2
        elif latest['macd'] > 0: score += 1
        if 35 < latest['rsi'] < 65 and latest['rsi'] > prev['rsi']: score += 1
        if vol_ratio > 1.3: score += 1
        if 1 <= stock['change_pct'] <= 6: score += 1
        if stock['amplitude'] < 8: score += 1

        # ===== v5 评分补丁 (2026-06-12 by 小胡瓜优化师, 应用) — P0-013 修复 (07-03) =====
        # P0-004: 超买卖出/超卖加分
        if latest['rsi'] > 75: score -= 2
        if latest['rsi'] < 30: score += 1
        # P1-009: 量能硬条件 (P0-013 用 vol_ratio 局部变量替代 latest, 已容错 NaN)
        if vol_ratio < 1.3: score -= 1
        # P0-005: 板块集中度惩罚 (P0-013: 现在 sector/cnt 有数了, 银行/电力 + 3+ 次 → 扣 1)
        if stock.get('sector') in ('银行', '电力') and stock.get('cnt_in_top5', 0) >= 3:
            score -= 1
        # P2-002: 防止连续推荐加分 (P0-013: days_in_top5 现在来自历史 json)
        if stock.get('days_in_top5', 0) >= 3:
            score -= 0.5
        # ===== v5 end =====

        atr = latest['atr'] if not pd.isna(latest['atr']) else stock['price'] * 0.02
        entry = stock['price']
        stop_loss = entry - 2 * atr
        take_profit = entry + 3 * atr
        rr = (take_profit - entry) / max(entry - stop_loss, 0.01)
        
        if score >= 4 and rr >= 1.2:
            results.append({
                'code': code, 'name': stock['name'], 'price': stock['price'],
                'change': stock['change_pct'], 'turnover': stock['turnover'] / 10000,
                'score': score, 'stop_loss': round(stop_loss, 2),
                'take_profit': round(take_profit, 2), 'risk_reward': round(rr, 2),
                'atr': round(atr, 2), 'rsi': round(latest['rsi'], 1),
                # P0-013 (07-03) 第二层修复: 序列化补 4 字段, 让 P1-015 工具检查全绿
                'vol_ratio': round(vol_ratio, 2),
                'sector': stock.get('sector', '其他'),
                'cnt_in_top5': stock.get('cnt_in_top5', 0),
                'days_in_top5': stock.get('days_in_top5', 0),
            })
        
        if (i+1) % 20 == 0:
            print(f"   已扫描 {i+1}/{len(base)}, 命中 {len(results)}")
    
    results.sort(key=lambda x: x['score'], reverse=True)
    print(f"\n✅ 扫描完成: 命中 {len(results)} 只")
    return results[:15]


# ==================== 回测验证 ====================

def backtest(stock_code, days=90):
    """单股票回测（90天）"""
    market = 'sh' if stock_code.startswith('6') else 'sz'
    df = fetch_sina_kline(f"{market}{stock_code}", 240, days + 60)
    if df is None or len(df) < 60:
        return None
    
    df = calculate_indicators(df)
    if df is None:
        return None
    
    trades = []
    position = 0
    entry_price = 0
    entry_idx = 0
    
    for i in range(20, len(df) - 5):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        if position == 0:
            # 五层确认增强：MA20趋势过滤 + MA60大趋势过滤 + 量能确认 + 涨幅过滤
            if i >= 3:
                ma20_trend = df.iloc[i]['ema20'] >= df.iloc[i-3]['ema20'] * 0.99
            else:
                ma20_trend = True
            ma60_bull = row.get('close', 0) > df.iloc[max(0,i-60):i+1].get('close', pd.Series()).mean() * 0.93 if len(df) > 60 else True
            
            buy = (
                (row['dif'] > row['dea'] and prev['dif'] <= prev['dea']) and
                (35 < row['rsi'] < 65) and
                (row['close'] > row['ema20']) and
                ma20_trend
            )
            if buy:
                position = 1
                entry_price = row['close']
                entry_idx = i
        
        elif position == 1:
            # ATR动态止损止盈（2xATR止损，3xATR止盈，盈亏比1.5）
            atr = row['atr'] if not pd.isna(row['atr']) else entry_price * 0.02
            stop_loss = entry_price - 2 * atr
            take_profit = entry_price + 3 * atr
            
            sell = (
                row['close'] <= stop_loss or
                row['close'] >= take_profit or
                (row['dif'] < row['dea'] and prev['dif'] >= prev['dea'])
            )
            
            if sell:
                exit_price = row['close']
                profit_pct = (exit_price / entry_price - 1) * 100
                hold_days = i - entry_idx
                trades.append({'profit': profit_pct, 'days': hold_days, 'win': profit_pct > 0})
                position = 0
    
    if not trades:
        return None
    
    wins = [t for t in trades if t['win']]
    return {
        'trades': len(trades),
        'win_rate': round(len(wins) / len(trades) * 100, 1),
        'avg_profit': round(sum(t['profit'] for t in trades) / len(trades), 2),
        'max_win': round(max(t['profit'] for t in trades), 2),
        'max_loss': round(min(t['profit'] for t in trades), 2),
    }


# ==================== 报告生成 ====================

def generate_report(stocks, backtest_results=None):
    """生成报告"""
    if not stocks:
        print("⚠️ 今日无符合条件的股票")
        return None
    
    report_lines = []
    report_lines.append(f"🕷️ 蜘蛛网 v4.3 选股报告 | {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    report_lines.append(f"推荐: {len(stocks)} 只 | 平均评分: {np.mean([s['score'] for s in stocks]):.1f}/10")
    report_lines.append("=" * 60)
    
    for i, s in enumerate(stocks[:10], 1):
        report_lines.append(f"\n{i}. {s['name']} ({s['code']})")
        report_lines.append(f"   💰 ¥{s['price']}  涨跌幅: {s['change']:+.2f}%  成交额: {s['turnover']:.1f}亿")
        report_lines.append(f"   📊 评分: {s['score']}/10  RSI: {s['rsi']}  ATR: {s['atr']}")
        report_lines.append(f"   🛑 止损: ¥{s['stop_loss']}  🎯 止盈: ¥{s['take_profit']}  风险收益比: {s['risk_reward']}")
        
        if backtest_results and s['code'] in backtest_results:
            bt = backtest_results[s['code']]
            if bt:
                report_lines.append(f"   📈 回测: {bt['trades']}笔  胜率{bt['win_rate']}%  平均{bt['avg_profit']:+.2f}%")
    
    if backtest_results:
        report_lines.append(f"\n{'='*60}")
        report_lines.append("📊 回测验证（近90天）")
        report_lines.append("=" * 60)
        for code, bt in list(backtest_results.items())[:5]:
            if bt:
                name = next((s['name'] for s in stocks if s['code'] == code), code)
                emoji = "✅" if bt['win_rate'] >= 50 else "⚠️"
                report_lines.append(f"  {emoji} {name}: {bt['trades']}笔  胜率{bt['win_rate']}%  平均{bt['avg_profit']:+.2f}%  最大盈{bt['max_win']:+.2f}%  最大亏{bt['max_loss']:+.2f}%")
    
    report_text = "\n".join(report_lines)
    print(report_text)

    # 保存选股结果
    with open(f"{OUTPUT_DIR}蜘蛛网v4.3_{datetime.now().strftime('%Y%m%d_%H%M')}.json", 'w') as f:
        json.dump({'time': datetime.now().isoformat(), 'stocks': stocks}, f, ensure_ascii=False, indent=2, default=str)

    return report_text


# ==================== 飞书推送 ====================

def send_to_feishu(report_text, chat_id="oc_ef684ee04be46f9c15054770be144b82"):
    """发送报告到飞书群"""
    try:
        import json
        data = {
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": report_text})
        }
        result = subprocess.run(
            ["lark-cli", "api", "POST", "/open-apis/im/v1/messages",
             "--params", json.dumps({"receive_id_type": "chat_id"}),
             "--data", json.dumps(data)],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            resp = json.loads(result.stdout)
            if resp.get("code") == 0:
                print("✅ 飞书推送成功")
            else:
                print(f"⚠️ 飞书推送返回错误: {resp.get('msg', 'unknown')}")
        else:
            print(f"⚠️ 飞书推送失败: {result.stderr[:200]}")
    except Exception as e:
        print(f"⚠️ 飞书推送异常: {e}")


# ==================== 主函数 ====================

def main():
    # 1. 选股
    stocks = screen_stocks()
    
    if not stocks:
        print("⚠️ 今日无符合条件的股票")
        return
    
    # 2. 回测验证（前5只）— 07-03 修复 bt30→bt90 (回测天数对齐函数签名)
    print(f"\n📊 回测验证...")
    backtest_results = {}
    for s in stocks[:5]:
        bt = backtest(s['code'], 90)
        if bt:
            backtest_results[s['code']] = bt
            print(f"  {s['name']}: {bt['trades']}笔  胜率{bt['win_rate']}%  平均{bt['avg_profit']:+.2f}%")
    
    # 3. P0-026 修复 (2026-07-04 by 小胡瓜优化师): 0% 胜率过滤 + 推送前预检
    #    - 把 backtest 命中但 win_rate=0% 的票踢出推荐列表
    #    - 同时保留补位逻辑: 仅剔除被回测覆盖的前 5 名, 不动 6~15 名
    p0_filtered = []
    p0_dropped = []
    for s in stocks:
        bt = backtest_results.get(s['code'])
        if bt and bt['win_rate'] == 0.0 and bt['avg_profit'] < 0:
            p0_dropped.append(f"{s['code']} {s['name']} (wr=0% avg={bt['avg_profit']:+.2f}%)")
        else:
            p0_filtered.append(s)
    if p0_dropped:
        print(f"\n🛑 P0 过滤 (win_rate=0%): {', '.join(p0_dropped)}")
        stocks = p0_filtered
        if not stocks:
            print("⚠️ 过滤后无票, 跳过推送")
            return stocks, backtest_results

    # 4. 生成报告
    report = generate_report(stocks, backtest_results)

    # 07-31 重构: 落回测结果到 runtime/picks/backtest/(P0 STALE 假阳性根因之一: 从不落盘 → 永远 stale)
    if backtest_results:
        _bt_dir = os.path.join(os.path.dirname(OUTPUT_DIR.rstrip('/')), 'picks', 'backtest')
        os.makedirs(_bt_dir, exist_ok=True)
        _bt_path = os.path.join(_bt_dir, f"backtest_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
        with open(_bt_path, 'w', encoding='utf-8') as f:
            json.dump({
                'time': datetime.now().isoformat(),
                'span_days': 90,
                'results': backtest_results,
                'p0_filtered_count': len(p0_filtered),
                'p0_dropped_count': len(p0_dropped),
                'p0_dropped': p0_dropped
            }, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n💾 回测结果已存: {_bt_path}")

    # 5. 推送飞书
    if report:
        send_to_feishu(report)

    return stocks, backtest_results


if __name__ == "__main__":
    main()
