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

_KB_ROOT = Path(__file__).resolve().parents[1]
_STRATEGY_DIR = _KB_ROOT / "01_策略引擎"
if str(_STRATEGY_DIR) not in sys.path:
    sys.path.insert(0, str(_STRATEGY_DIR))
from 九条经验规则 import NineRuleEngine, build_market_regime

# ── 替身模式桥接（LIUYAO_MOCK=1）──────────────────────
try:
    from mock_bridge import is_on as _mock_on, mf as _mock_mf, output_dir as _mock_out
except Exception:
    _mock_on = lambda: False
    _mock_mf = lambda: None
    def _mock_out(*p):
        return str(_KB_ROOT / "runtime" / "/".join(p)) + "/"

# 输出目录：替身模式下自动隔离到 runtime/mock/picks/，**绝不污染生产**
OUTPUT_DIR = _mock_out("picks") if _mock_on() else str(_KB_ROOT / "runtime" / "picks") + "/"
os.makedirs(OUTPUT_DIR, exist_ok=True)   # 替身子目录首次运行可能不存在
KLINE_POOL = FallbackPool()
_NINE_ENGINE = NineRuleEngine()


# ==================== 统一评分引擎（P0 重构） ====================
# 目标：把蜘蛛网内置评分 + 五层确认 + 九条经验合并成一套加权信号，
# 去掉重复打分，提升信号一致性和可回测性。

_UNIFIED_WEIGHTS = {
    'trend': 1.2,
    'macd': 1.0,
    'rsi': 0.8,
    'vol_ratio': 1.0,
    'momentum': 0.8,
    'nine_rules': 0.9,
    'market_regime': 1.0,
    'money_flow': 0.9,
    'sector_rotation': 0.7,
    'history': 0.8,
}


def _safe_num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def score_unified(stock: dict, df: pd.DataFrame, market_regime: dict, money_flow: dict) -> dict:
    """
    统一评分入口。
    返回: {
        'code', 'name', 'price', 'score', 'signal',
        'stop_loss', 'take_profit', 'risk_reward', 'atr', 'rsi',
        'vol_ratio', 'sector', 'cnt_in_top5', 'days_in_top5',
        'breakdown': {...}
    }
    """
    code = stock.get('code')
    name = stock.get('name', '')
    price = _safe_num(stock.get('price'), 0.0)
    if not code or price <= 0 or df is None or len(df) < 20:
        return {
            'code': code, 'name': name, 'price': price, 'score': 0.0, 'signal': 'HOLD',
            'stop_loss': price, 'take_profit': price, 'risk_reward': 0.0, 'atr': price * 0.02,
            'rsi': 50.0, 'vol_ratio': 1.0, 'sector': stock.get('sector') or '其他',
            'cnt_in_top5': 0, 'days_in_top5': 0, 'breakdown': {}, 'nine_signal': 'HOLD', 'nine_score': 0.0,
        }

    latest = df.iloc[-1]
    prev = df.iloc[-2]
    vol_ratio = _safe_num(latest.get('vol_ratio'), 1.0)

    # ---------- 基础信号 ----------
    trend_up = bool(latest['close'] > latest['ema20'])
    ma_bull = bool(latest['ema5'] > latest['ema10'] > latest['ema20'])
    macd_bullish = bool((latest['dif'] > latest['dea'] and prev['dif'] <= prev['dea']) or latest['macd'] > 0)
    rsi = _safe_num(latest.get('rsi'), 50.0)
    rsi_ok = bool(35 < rsi < 65 and rsi > _safe_num(prev.get('rsi'), rsi - 1))

    # ---------- 九条经验信号 ----------
    klines = []
    for _, row in df.tail(25).iterrows():
        try:
            klines.append({
                'date': str(row.name),
                'open': _safe_num(row.get('open')),
                'close': _safe_num(row.get('close')),
                'high': _safe_num(row.get('high')),
                'low': _safe_num(row.get('low')),
                'volume': _safe_num(row.get('volume')),
            })
        except Exception:
            continue
    nine = _NINE_ENGINE.evaluate(code, cost_price=None, sector_stocks=[])
    nine_total = _safe_num((nine or {}).get('total_score'), 0.0)
    nine_signal = (nine or {}).get('composite_signal', 'HOLD')

    # ---------- 市场环境 ----------
    regime = market_regime or {}
    regime_adj = _safe_num(regime.get('score_adj'), 0.0)

    # ---------- 资金流 ----------
    mf_val = money_flow.get(code)
    if isinstance(mf_val, list) and mf_val:
        flow = _safe_num(mf_val[-1], 0.0)
    else:
        flow = _safe_num(mf_val, 0.0)

    # ---------- 板块/历史 ----------
    sector = stock.get('sector') or '其他'
    cnt_in_top5 = int(stock.get('cnt_in_top5') or 0)
    days_in_top5 = int(stock.get('days_in_top5') or 0)

    # ---------- 加权总分（0-10） ----------
    # 宽松版：中性/缺数据给 0，只对明显负面信号扣分，避免 0 命中
    parts = {
        'trend': 1.0 if trend_up else -0.5,
        'macd': 1.0 if macd_bullish else -0.5,
        'rsi': 0.5 if rsi_ok else (-1.0 if rsi > 72 or rsi < 28 else 0.0),
        'vol_ratio': 0.5 if vol_ratio > 1.3 else (-0.8 if vol_ratio < 0.9 else 0.0),
        'momentum': 0.5 if 1 <= float(stock.get('change_pct') or 0) <= 6 else 0.0,
        'nine_rules': _safe_num(nine_total / 100 * 1.5, 0.0),
        'market_regime': regime_adj,
        'money_flow': 0.5 if flow > 0 else (-0.5 if flow < 0 else 0.0),
        'sector_rotation': -0.3 if sector in ('银行', '电力') and cnt_in_top5 >= 3 else 0.0,
        'history': -0.5 if days_in_top5 >= 3 else 0.0,
    }
    raw = 0.0
    for key, val in parts.items():
        w = _UNIFIED_WEIGHTS.get(key, 1.0)
        raw += val * w
    # raw 理论范围约 -6 ~ +6，映射到 0-10
    score = max(0.0, min(10.0, (raw + 6.0) / 12.0 * 10.0))

    # ---------- 信号 ----------
    if score >= 7.2 and ma_bull:
        signal = 'BUY'
    elif score >= 6.0:
        signal = 'BUY'
    elif score >= 4.0:
        signal = 'HOLD'
    else:
        signal = 'SELL'

    # ---------- 风控 ----------
    atr = _safe_num(latest.get('atr'), price * 0.02)
    stop_loss = price - 2.0 * atr
    take_profit = price + 3.0 * atr
    rr = (take_profit - price) / max(price - stop_loss, 0.01)

    return {
        'code': code,
        'name': name,
        'price': price,
        'score': round(score, 2),
        'signal': signal,
        'stop_loss': round(stop_loss, 2),
        'take_profit': round(take_profit, 2),
        'risk_reward': round(rr, 2),
        'atr': round(atr, 2),
        'rsi': round(rsi, 1),
        'vol_ratio': round(vol_ratio, 2),
        'sector': sector,
        'cnt_in_top5': cnt_in_top5,
        'days_in_top5': days_in_top5,
        'breakdown': parts,
        'nine_signal': nine_signal,
        'nine_score': round(nine_total, 1),
    }


# ==================== P0-013 辅助: 板块分类 + 历史推荐聚合 ====================
# 复用 scripts/sector_concentration.py 已有逻辑,避免双维护

def fetch_eastmoney_flow(codes, days=1):
    """东方财富资金流向：返回 {code: [main_net_inflow_day1, ...]}，失败降级为{}"""
    out = {}
    if not codes:
        return out
    if _mock_on():
        m = _mock_mf()
        for mkt, code in codes[:20]:
            out[code] = [round(m._signed("flow", code) * 5e7, 2)]
        return out
    lmt = max(1, int(days))
    for mkt, code in codes[:20]:
        secid = f"{'0' if mkt=='sz' else '1'}.{code}"
        u = f"https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={secid}&fields1=f1,f2,f3&fields2=f51,f52,f53,f54,f55,f56,f57&lmt={lmt}"
        try:
            req = urllib.request.Request(u, headers={'User-Agent':'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = json.loads(resp.read().decode())
            klines = (((raw or {}).get('data') or {}).get('klines') or [])
            vals = []
            for k in klines[-lmt:]:
                parts = k.split(',')
                # 字段顺序: date,主力净流入,小单净流入,中单净流入,大单净流入,超大单净流入,收盘价
                main_net = float(parts[1]) if len(parts) > 1 else 0.0
                vals.append(main_net)
            if vals:
                out[code] = vals
        except Exception:
            pass
    return out

_SECTOR_KEYWORDS = {
    "银行": ["银行", "工商", "建设", "农业", "招商", "兴业", "浦发", "交通"],
    "券商": ["证券", "华泰", "国泰", "中信"],
    "电力": ["电力", "长江"],
    "煤炭": ["煤业", "神华"],
    "新能源": ["核电", "水电"],
}

_SECTOR_MAP_PATH = os.path.join(os.path.dirname(OUTPUT_DIR.rstrip('/')), '..', '10_配置', 'sector_map.json')
_SECTOR_MAP = {}
if os.path.exists(_SECTOR_MAP_PATH):
    try:
        with open(_SECTOR_MAP_PATH, 'r', encoding='utf-8') as _f:
            _SECTOR_MAP = json.load(_f).get('map', {})
    except Exception:
        pass

def classify_sector(name: str) -> str:
    """根据股票名称关键词+映射表推断板块。"""
    if not name:
        return "其他"
    for sector, kws in _SECTOR_KEYWORDS.items():
        if any(k in name for k in kws):
            return sector
    return _SECTOR_MAP.get(name, "其他")


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
    if _mock_on():
        m = _mock_mf()
        out = []
        for _mkt, c in codes_with_market:
            q = m.mock_realtime(c)
            out.append({
                'code': c, 'name': q['name'], 'price': q['price'],
                'change_pct': q['change_pct'],
                # 注意：本函数的 turnover 是「成交额（万元）」（腾讯字段37），
                # 不是换手率——下游过滤条件为 > 5000
                'turnover': round(m._unit("amt", c) * 795000 + 5000, 2),
                'amplitude': q['amplitude'],
            })
        return out
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
    if _mock_on():
        df = pd.DataFrame(_mock_mf().mock_kline(code, datalen))
        for col in ['open', 'close', 'high', 'low', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')
        return df
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
        ('sh', '600000'), ('sh', '600009'), ('sh', '600016'), ('sh', '600025'), ('sh', '600028'), ('sh', '600029'), ('sh', '600030'), ('sh', '600031'), ('sh', '600036'), ('sh', '600048'),
        ('sh', '600050'), ('sh', '600085'), ('sh', '600104'), ('sh', '600111'), ('sh', '600132'), ('sh', '600150'), ('sh', '600188'), ('sh', '600196'), ('sh', '600219'), ('sh', '600276'),
        ('sh', '600309'), ('sh', '600340'), ('sh', '600346'), ('sh', '600362'), ('sh', '600372'), ('sh', '600426'), ('sh', '600436'), ('sh', '600438'), ('sh', '600489'), ('sh', '600497'),
        ('sh', '600516'), ('sh', '600519'), ('sh', '600547'), ('sh', '600570'), ('sh', '600584'), ('sh', '600585'), ('sh', '600588'), ('sh', '600600'), ('sh', '600660'), ('sh', '600690'),
        ('sh', '600711'), ('sh', '600760'), ('sh', '600763'), ('sh', '600801'), ('sh', '600809'), ('sh', '600837'), ('sh', '600862'), ('sh', '600887'), ('sh', '600893'), ('sh', '600900'),
        ('sh', '600941'), ('sh', '600958'), ('sh', '600985'), ('sh', '600988'), ('sh', '600989'), ('sh', '600995'), ('sh', '600999'), ('sh', '601006'), ('sh', '601012'), ('sh', '601021'),
        ('sh', '601066'), ('sh', '601088'), ('sh', '601100'), ('sh', '601107'), ('sh', '601111'), ('sh', '601138'), ('sh', '601166'), ('sh', '601186'), ('sh', '601211'), ('sh', '601225'),
        ('sh', '601231'), ('sh', '601238'), ('sh', '601288'), ('sh', '601318'), ('sh', '601328'), ('sh', '601336'), ('sh', '601390'), ('sh', '601398'), ('sh', '601600'), ('sh', '601601'),
        ('sh', '601618'), ('sh', '601628'), ('sh', '601633'), ('sh', '601658'), ('sh', '601668'), ('sh', '601669'), ('sh', '601688'), ('sh', '601689'), ('sh', '601699'), ('sh', '601728'),
        ('sh', '601766'), ('sh', '601800'), ('sh', '601818'), ('sh', '601857'), ('sh', '601865'), ('sh', '601868'), ('sh', '601888'), ('sh', '601898'), ('sh', '601899'), ('sh', '601939'),
        ('sh', '601985'), ('sh', '601989'), ('sh', '601995'), ('sh', '603019'), ('sh', '603027'), ('sh', '603259'), ('sh', '603288'), ('sh', '603345'), ('sh', '603501'), ('sh', '603728'),
        ('sh', '603806'), ('sh', '603899'), ('sh', '603986'), ('sh', '605117'), ('sh', '688008'), ('sh', '688012'), ('sh', '688017'), ('sh', '688036'), ('sh', '688041'), ('sh', '688111'),
        ('sh', '688126'), ('sh', '688169'), ('sh', '688223'), ('sh', '688256'), ('sh', '688396'), ('sh', '688561'), ('sh', '688599'), ('sh', '688981'), ('sz', '000002'), ('sz', '000034'),
        ('sz', '000063'), ('sz', '000166'), ('sz', '000333'), ('sz', '000425'), ('sz', '000538'), ('sz', '000568'), ('sz', '000630'), ('sz', '000651'), ('sz', '000661'), ('sz', '000768'),
        ('sz', '000776'), ('sz', '000786'), ('sz', '000792'), ('sz', '000858'), ('sz', '000878'), ('sz', '000895'), ('sz', '000938'), ('sz', '000975'), ('sz', '000977'), ('sz', '000999'),
        ('sz', '002032'), ('sz', '002039'), ('sz', '002049'), ('sz', '002050'), ('sz', '002116'), ('sz', '002129'), ('sz', '002153'), ('sz', '002179'), ('sz', '002230'), ('sz', '002236'),
        ('sz', '002241'), ('sz', '002267'), ('sz', '002271'), ('sz', '002304'), ('sz', '002340'), ('sz', '002371'), ('sz', '002410'), ('sz', '002415'), ('sz', '002428'), ('sz', '002459'),
        ('sz', '002460'), ('sz', '002463'), ('sz', '002466'), ('sz', '002472'), ('sz', '002475'), ('sz', '002501'), ('sz', '002594'), ('sz', '002625'), ('sz', '002648'), ('sz', '002709'),
        ('sz', '002714'), ('sz', '002747'), ('sz', '002821'), ('sz', '002850'), ('sz', '002916'), ('sz', '002920'), ('sz', '002938'), ('sz', '300003'), ('sz', '300014'), ('sz', '300015'),
        ('sz', '300024'), ('sz', '300033'), ('sz', '300059'), ('sz', '300073'), ('sz', '300115'), ('sz', '300122'), ('sz', '300124'), ('sz', '300136'), ('sz', '300142'), ('sz', '300223'),
        ('sz', '300274'), ('sz', '300308'), ('sz', '300316'), ('sz', '300339'), ('sz', '300347'), ('sz', '300394'), ('sz', '300408'), ('sz', '300413'), ('sz', '300418'), ('sz', '300433'),
        ('sz', '300450'), ('sz', '300454'), ('sz', '300474'), ('sz', '300496'), ('sz', '300498'), ('sz', '300502'), ('sz', '300595'), ('sz', '300661'), ('sz', '300676'), ('sz', '300677'),
        ('sz', '300750'), ('sz', '300759'), ('sz', '300760'), ('sz', '300763'), ('sz', '300782'), ('sz', '300832'), ('sz', '300896'), ('sz', '300919'), ('sz', '300979'), ('sz', '300999')
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
    
    # 加载回测黑名单 (持续亏损标的)
    blacklist_path = os.path.join(os.path.dirname(OUTPUT_DIR.rstrip('/')), '..', '10_配置', 'backtest-blacklist.json')
    blacklist_codes = set()
    if os.path.exists(blacklist_path):
        try:
            with open(blacklist_path, 'r', encoding='utf-8') as f:
                bl = json.load(f)
                blacklist_codes = set(bl.get('codes', []))
            if blacklist_codes:
                print(f"   🚫 回测黑名单: {len(blacklist_codes)} 只")
            base = [s for s in base if s['code'] not in blacklist_codes]
        except Exception:
            pass
    
    # 大盘趋势过滤
    market_filter = None
    try:
        idx_rows = KLINE_POOL.get_kline('sh000001', 240, 60)
        if idx_rows:
            idx_df = pd.DataFrame(idx_rows)
            for col in ['open','close','high','low','volume']:
                if col in idx_df.columns:
                    idx_df[col] = pd.to_numeric(idx_df[col], errors='coerce')
            idx_df = calculate_indicators(idx_df)
            if idx_df is not None and len(idx_df) > 20:
                idx_last = idx_df.iloc[-1]
                ma60_val = idx_df['close'].iloc[-60:].mean() if len(idx_df) >= 60 else idx_df['close'].mean()
                bull_market = bool(idx_last['close'] > ma60_val * 0.93 and (idx_last['dif'] > idx_last['dea'] or idx_last['macd'] > 0))
                if not bull_market:
                    market_filter = 'bearish'
                    print(f"   ⚠️ 大盘偏弱 (close={idx_last['close']}, macd={idx_last['macd']:.2f})，仅保留防御型+高评分")
    except Exception as e:
        print(f"   ⚠️ 大盘判断异常: {e}")
    
    # 板块轮动过滤：计算强势板块 top3
    _SECTOR_TOP3 = set()
    try:
        sector_scores = {}
        for s in base:
            sec = s.get('sector') or classify_sector(s.get('name', ''))
            sector_scores.setdefault(sec, []).append(s.get('change_pct', 0) or 0)
        avg_by_sector = {sec: sum(v)/len(v) for sec, v in sector_scores.items() if v}
        ranked = sorted(avg_by_sector.items(), key=lambda x: x[1], reverse=True)[:3]
        _SECTOR_TOP3 = {sec for sec, _ in ranked}
        print(f"   🏆 强势板块 TOP3: {', '.join(_SECTOR_TOP3) if _SECTOR_TOP3 else '无'}")
    except Exception as e:
        print(f"   ⚠️ 板块轮动判断异常: {e}")
    
    # 资金流过滤：东方财富主力净流入（当前网络异常降级为空）
    money_flow = {}
    try:
        money_flow = fetch_eastmoney_flow(stocks, days=3)
        if money_flow:
            print(f"   💰 主力净流入样本: {sum(1 for v in money_flow.values() if v and any(x > 0 for x in v))}/{len(money_flow)} 3日内有正流入")
        else:
            print(f"   ⚠️ 资金流数据空（网络/接口异常），跳过资金流过滤")
    except Exception as e:
        print(f"   ⚠️ 资金流获取异常: {e}")
    
    results = []
    # P0-013 修复 (2026-07-03): 历史推荐聚合, 给 cnt_in_top5 / days_in_top5 喂数
    history_stats = _load_top5_history(days=10)
    market_regime = build_market_regime()
    print(f"   🌍 市场环境: {market_regime.get('reason', '中性')}")
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

        # ===== 统一评分引擎 =====
        unified = score_unified(stock, df, market_regime, money_flow)
        if not unified:
            continue

        score = unified['score']
        signal = unified['signal']
        rr = unified['risk_reward']
        atr = unified['atr']
        rsi = unified['rsi']
        stop_loss = unified['stop_loss']
        take_profit = unified['take_profit']

        # ===== 九条经验信号一致性过滤 =====
        nine_signal = unified.get('nine_signal')
        if signal == 'BUY':
            if nine_signal == 'SELL':
                continue
            elif nine_signal == 'HOLD':
                score = max(0.0, score - 0.5)

        # ===== 北向/主力连续3日减持硬过滤 =====
        mf_series = money_flow.get(code) if isinstance(money_flow.get(code), list) else []
        if len(mf_series) >= 3 and all(v < 0 for v in mf_series[-3:]):
            continue

        # 熊市/震荡过滤：只保留高评分 + 高盈亏比
        if market_regime.get('filter_mode') == 'defensive':
            if score < 6.5 or rr < 1.3:
                continue

        if score >= 7.2 and rr >= 1.2:
            results.append({
                'code': code, 'name': stock['name'], 'price': stock['price'],
                'change': stock['change_pct'], 'turnover': stock['turnover'] / 10000,
                'score': score, 'signal': signal, 'stop_loss': stop_loss,
                'take_profit': take_profit, 'risk_reward': rr,
                'atr': atr, 'rsi': rsi, 'vol_ratio': unified['vol_ratio'],
                'sector': stock.get('sector', '其他'),
                'cnt_in_top5': stock.get('cnt_in_top5', 0),
                'days_in_top5': stock.get('days_in_top5', 0),
                'nine_signal': unified.get('nine_signal'),
                'nine_score': unified.get('nine_score'),
                'breakdown': unified.get('breakdown', {}),
            })

        if (i+1) % 20 == 0:
            print(f"   已扫描 {i+1}/{len(base)}, 命中 {len(results)}")
    
    results.sort(key=lambda x: x['score'], reverse=True)
    print(f"\n✅ 扫描完成: 命中 {len(results)} 只")
    return results[:15]


# ==================== 回测验证 ====================

def backtest(stock_code, days=90, *, atr_sl_mult=2.0, atr_tp_mult=3.0, rsi_min=30, rsi_max=70, require_vol_ratio=True, use_trailing_stop=True):
    """单股票回测（90天），使用简化信号（趋势+MACD+RSI+量能），避免嵌套统一引擎导致 O(n^2)"""
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
    highest_since_entry = 0.0
    
    for i in range(20, len(df) - 5):
        row = df.iloc[i]
        prev = df.iloc[i-1]
        
        if position == 0:
            # 简化入场信号：趋势 + MACD + RSI + 量能（不回调查统一引擎）
            trend_ok = row['close'] > row['ema20']
            macd_ok = (row['dif'] > row['dea'] and prev['dif'] <= prev['dea']) or row['macd'] > 0
            rsi_ok = rsi_min < row['rsi'] < rsi_max
            vol_ratio = row.get('vol_ratio') if pd.notna(row.get('vol_ratio')) else 1.0
            vol_ok = (not require_vol_ratio) or (vol_ratio > 1.2)
            
            buy = trend_ok and macd_ok and rsi_ok and vol_ok
            if buy:
                position = 1
                entry_price = row['close']
                entry_idx = i
                highest_since_entry = entry_price
        
        elif position == 1:
            highest_since_entry = max(highest_since_entry, row['close'])
            atr = row['atr'] if not pd.isna(row['atr']) else entry_price * 0.02
            stop_loss = entry_price - atr_sl_mult * atr
            take_profit = entry_price + atr_tp_mult * atr
            
            sell = False
            reason = None
            if row['close'] <= stop_loss:
                sell = True
                reason = 'stop_loss'
            elif row['close'] >= take_profit:
                sell = True
                reason = 'take_profit'
            elif row['dif'] < row['dea'] and prev['dif'] >= prev['dea']:
                sell = True
                reason = 'macd_death'
            elif use_trailing_stop:
                trail_stop = highest_since_entry - atr_sl_mult * atr
                if row['close'] <= trail_stop and (i - entry_idx) >= 3:
                    sell = True
                    reason = 'trailing_stop'
            
            if sell:
                exit_price = row['close']
                profit_pct = (exit_price / entry_price - 1) * 100
                hold_days = i - entry_idx
                trades.append({'profit': profit_pct, 'days': hold_days, 'win': profit_pct > 0, 'reason': reason})
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
        report_lines.append(f"   📊 评分: {s['score']:.2f}/10  信号: {s.get('nine_signal', '-')}  九条经验: {s.get('nine_score', '-')}")
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

def _load_chat_ids() -> dict:
    """(2026-08-08 P1 fix) 从 10_配置/chat_ids.yaml 读 chat_id, 禁硬编码"""
    from pathlib import Path
    import yaml
    yaml_path = Path(__file__).resolve().parent.parent / "10_配置" / "chat_ids.yaml"
    if not yaml_path.exists():
        return {"home": "oc_4515237afd69b15b032c7df636d90e58", "spider_daily": None}
    try:
        d = yaml.safe_load(yaml_path.read_text())
        return {
            "home": d.get("feishu", {}).get("home", {}).get("chat_id") or "oc_4515237afd69b15b032c7df636d90e58",
            "spider_daily": (d.get("feishu", {}).get("spider_daily") or {}).get("chat_id"),
        }
    except Exception:
        return {"home": "oc_4515237afd69b15b032c7df636d90e58", "spider_daily": None}


def send_to_feishu(report_text, chat_id=None):
    """发送报告到飞书群 (chat_id 默认从 chat_ids.yaml 读 spider_daily, None 时静默)"""
    if chat_id is None:
        ids = _load_chat_ids()
        chat_id = ids.get("spider_daily") or ids.get("home")  # spider 未配 → 兜 home
    if not chat_id:
        print("⚠️ chat_id 未配置 (10_配置/chat_ids.yaml), 跳过推送")
        return
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
        if bt and (bt['win_rate'] == 0.0 or bt['avg_profit'] < -5.0):
            p0_dropped.append(f"{s['code']} {s['name']} (wr={bt['win_rate']}% avg={bt['avg_profit']:+.2f}%)")
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

    # 6. 新老股票池追踪 (2026-08-16 加; 失败不影响主流程)
    try:
        import subprocess, os as _os
        _tracker = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), '..', 'scripts', 'universe_tracking.py')
        subprocess.run([sys.executable, _tracker], timeout=60,
                       capture_output=True, cwd=_os.path.dirname(_os.path.abspath(__file__)))
    except Exception:
        pass

    return stocks, backtest_results


if __name__ == "__main__":
    main()
