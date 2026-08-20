#!/usr/bin/env python3
"""
九条经验选股规则引擎 v1.0
基于老胡瓜9条实战经验，全部量化为可执行的交易规则

每条经验 → 独立的规则函数 → 综合评分

用法:
    from 九条经验规则 import NineRuleEngine
    engine = NineRuleEngine()
    result = engine.evaluate('300059')
    results = engine.screen_all(codes)
    
    # 获取综合建议
    advice = engine.get_advice('300059')

九条经验映射:
    Rule 1: 本金不到10万+短线 → 没把握就歇着 → 高确定性的信号才出手
    Rule 2: 5日/10日线金叉进死叉出 → MA交叉信号
    Rule 3: 大盘跌时选强势股 → 抗跌筛选
    Rule 4: 主升浪前小阴小阳缩量 → 均量线135+缩量震荡
    Rule 5: 强者恒强，只做龙头 → 板块排名前3
    Rule 6: 缩量拿，放量跑 → 量比阈值
    Rule 7: 会卖是师父 → 止盈纪律
    Rule 8: 趋势第一 → 不逆趋势操作
    Rule 9: 复盘+银行风向标 → 大盘vs银行强弱的资金方向判断
"""

import urllib.request
import ssl
import json
import math
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE


# ==================== 数据获取层 ====================

def fetch_realtime(code: str) -> Optional[Dict]:
    """获取腾讯财经实时数据"""
    prefix = 'sz' if code.startswith(('0', '3')) else 'sh'
    url = f'https://qt.gtimg.cn=q={prefix}{code}'
    try:
        with urllib.request.urlopen(url, timeout=5, context=SSL_CTX) as r:
            data = r.read().decode('gbk')
            fields = data.split('~')
            if len(fields) > 50:
                return {
                    'code': fields[2],
                    'name': fields[1],
                    'price': float(fields[3]),
                    'prev_close': float(fields[4]),
                    'open': float(fields[5]),
                    'volume': float(fields[6]),       # 手
                    'amount': float(fields[37])/1e8 if fields[37] else 0,  # 亿元
                    'change': float(fields[31]),
                    'change_pct': float(fields[32]),
                    'high': float(fields[33]),
                    'low': float(fields[34]),
                    'pe': float(fields[39]) if fields[39] and fields[39] != '-' else 0,
                    'amplitude': float(fields[43]) if fields[43] else 0,  # 振幅%
                    'turnover': float(fields[38]) if fields[38] else 0,   # 换手率%
                    'limit_up': float(fields[47]) if fields[47] else 0,
                    'limit_down': float(fields[48]) if fields[48] else 0,
                }
    except Exception:
        return None


def fetch_kline(code: str, count: int = 20) -> Optional[List[Dict]]:
    """
    获取日K线数据（腾讯财经）
    返回最近count天的OHLCV
    """
    prefix = 'sz' if code.startswith(('0', '3')) else 'sh'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{count},qfq'
    try:
        with urllib.request.urlopen(url, timeout=8, context=SSL_CTX) as r:
            raw = r.read().decode('utf-8')
            data = json.loads(raw)
            key = f'{prefix}{code}'
            if 'data' in data and key in data['data']:
                klines = data['data'][key].get('day', data['data'][key].get('qfqday', []))
                result = []
                for k in klines:
                    if isinstance(k, list) and len(k) >= 6:
                        result.append({
                            'date': k[0],
                            'open': float(k[1]),
                            'close': float(k[2]),
                            'high': float(k[3]),
                            'low': float(k[4]),
                            'volume': float(k[5]),  # 手
                        })
                return result
    except Exception:
        return None


def fetch_index_data() -> Optional[Dict]:
    """获取上证指数数据"""
    try:
        with urllib.request.urlopen('https://qt.gtimg.cn=q=sh000001', timeout=5, context=SSL_CTX) as r:
            data = r.read().decode('gbk')
            fields = data.split('~')
            if len(fields) > 34:
                return {
                    'name': fields[1],
                    'price': float(fields[3]),
                    'change_pct': float(fields[32]),
                    'high': float(fields[33]),
                    'low': float(fields[34]),
                }
    except Exception:
        return None


def fetch_bank_index() -> Optional[Dict]:
    """获取银行板块指数（881155 银行板块）"""
    try:
        with urllib.request.urlopen('https://qt.gtimg.cn/q=sh881155', timeout=5, context=SSL_CTX) as r:
            data = r.read().decode('gbk')
            fields = data.split('~')
            if len(fields) > 34:
                return {
                    'name': fields[1],
                    'price': float(fields[3]),
                    'change_pct': float(fields[32]),
                }
    except Exception:
        return None


# ==================== Layer 3: 市场环境判断 ====================

def fetch_index_series(code: str, count: int = 60):
    """拉取指数日K序列用于判断趋势/震荡。"""
    prefix = 'sh' if code.startswith('sh') else 'sz'
    url = f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={prefix}{code},day,,,{count},qfq'
    try:
        with urllib.request.urlopen(url, timeout=8, context=SSL_CTX) as r:
            raw = r.read().decode('utf-8')
            data = json.loads(raw)
            key = f'{prefix}{code}'
            klines = ((data.get('data') or {}).get(key) or {}).get('day', []) or ((data.get('data') or {}).get(key) or {}).get('qfqday', [])
            closes = []
            for k in klines:
                if isinstance(k, list) and len(k) >= 3:
                    closes.append(float(k[2]))
            return closes
    except Exception:
        return None


def build_market_regime():
    """
    返回 market_regime:
      - regime: bull | bear | sideways
      - score_adj: 全局评分调节值
      - filter_mode: normal | defensive
    使用沪深300 + 创业板指 + 银行板相对强弱。
    """
    try:
        sh_closes = fetch_index_series('sh000300', 60) or []
        cy_closes = fetch_index_series('sz399006', 60) or []
        bank = fetch_bank_index()
    except Exception:
        return {'regime': 'sideways', 'score_adj': 0, 'filter_mode': 'normal', 'reason': '指数获取异常，默认中性'}

    regime = 'sideways'
    score_adj = 0
    filter_mode = 'normal'
    reasons = []

    def last_ma(closes, n):
        if len(closes) < n:
            return None
        return sum(closes[-n:]) / n

    sh_ma20 = last_ma(sh_closes, 20)
    sh_ma60 = last_ma(sh_closes, 60)
    cy_ma20 = last_ma(cy_closes, 20)

    sh_bull = bool(sh_closes and sh_ma20 and sh_ma60 and sh_closes[-1] > sh_ma20 * 0.97 and sh_closes[-1] > sh_ma60 * 0.93)
    cy_bull = bool(cy_closes and cy_ma20 and cy_closes[-1] > cy_ma20 * 0.97)
    sh_downtrend = bool(sh_closes and sh_ma20 and sh_ma60 and sh_ma20 < sh_ma60 * 0.98)

    if sh_bull and cy_bull:
        regime = 'bull'
        score_adj = 1
        filter_mode = 'normal'
        reasons.append('沪深300+创业板偏强')
    elif sh_downtrend:
        regime = 'bear'
        score_adj = -2
        filter_mode = 'defensive'
        reasons.append('沪深300偏弱，降低仓位预期')
    else:
        regime = 'sideways'
        score_adj = 0
        filter_mode = 'normal'
        reasons.append('指数震荡')

    if bank and bank.get('change_pct') is not None:
        idx = fetch_index_data()
        if idx and idx.get('change_pct') is not None:
            diff = bank['change_pct'] - idx['change_pct']
            if diff < -1.5:
                score_adj += 1
                reasons.append('银行弱于大盘，资金偏进攻')
            elif diff > 1.5:
                score_adj -= 1
                reasons.append('银行强于大盘，偏防守')

    return {
        'regime': regime,
        'score_adj': score_adj,
        'filter_mode': filter_mode,
        'reason': '；'.join(reasons) if reasons else '中性',
    }


# ==================== 技术工具函数 ====================

def calc_ma(closes: List[float], period: int) -> Optional[float]:
    """计算简单移动平均"""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_ema(values: List[float], period: int) -> Optional[float]:
    """计算指数移动平均"""
    if len(values) < period:
        return None
    k = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * k + ema * (1 - k)
    return ema


def calc_volume_ma(volumes: List[float], period: int = 135) -> Optional[float]:
    """计算均量线（默认135日，经验4要求）"""
    return calc_ma(volumes, min(period, len(volumes)))


def is_golden_cross(closes: List[float]) -> bool:
    """MA5上穿MA10（金叉）"""
    if len(closes) < 11:
        return False
    ma5_prev = calc_ma(closes[:-1], 5)
    ma10_prev = calc_ma(closes[:-1], 10)
    ma5_curr = calc_ma(closes, 5)
    ma10_curr = calc_ma(closes, 10)
    if all(v is not None for v in [ma5_prev, ma10_prev, ma5_curr, ma10_curr]):
        return ma5_prev <= ma10_prev and ma5_curr > ma10_curr
    return False


def is_death_cross(closes: List[float]) -> bool:
    """MA5下穿MA10（死叉）"""
    if len(closes) < 11:
        return False
    ma5_prev = calc_ma(closes[:-1], 5)
    ma10_prev = calc_ma(closes[:-1], 10)
    ma5_curr = calc_ma(closes, 5)
    ma10_curr = calc_ma(closes, 10)
    if all(v is not None for v in [ma5_prev, ma10_prev, ma5_curr, ma10_curr]):
        return ma5_prev >= ma10_prev and ma5_curr < ma10_curr
    return False


def is_ma_bullish(closes: List[float]) -> bool:
    """均线多头排列：MA5 > MA10 > MA20"""
    ma5 = calc_ma(closes, 5)
    ma10 = calc_ma(closes, 10)
    ma20 = calc_ma(closes, 20)
    if all(v is not None for v in [ma5, ma10, ma20]):
        return ma5 > ma10 > ma20
    return False


def avg_volume_recent(volumes: List[float], days: int = 5) -> Optional[float]:
    """最近N日平均成交量"""
    if len(volumes) < days:
        return None
    return sum(volumes[-days:]) / days


# ==================== 九条经验规则引擎 ====================

class NineRuleEngine:
    """
    九条经验选股规则引擎
    
    每条规则返回: (score: float, signal: str, reason: str)
    score: -10 到 +10
    signal: BUY / HOLD / SELL
    """
    
    def __init__(self, stop_loss_pct: float = -8.0):
        self.cache = {}
        self.stop_loss_pct = stop_loss_pct
    
    # ---------- 经验1: 本金不到10万+短线 → 没把握就歇着 ----------
    def rule1_market_timing(self, code: str, klines: List[Dict]) -> Tuple[float, str, str]:
        """
        市场节奏判断：
        - 大盘趋势不明朗或下跌时，不操作
        - 有明确上涨趋势时才出手
        - 震荡期减少操作频率
        """
        if not klines or len(klines) < 10:
            return 0, "HOLD", "数据不足"
        
        closes = [k['close'] for k in klines]
        
        # 近10日趋势
        trend_10d = (closes[-1] - closes[-10]) / closes[-10] * 100 if closes[-10] != 0 else 0
        
        # 近5日趋势
        trend_5d = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] != 0 else 0
        
        # 趋势一致性：5日和10日同向
        same_direction = (trend_10d > 0 and trend_5d > 0) or (trend_10d < 0 and trend_5d < 0)
        
        score = 0
        if trend_10d > 3 and trend_5d > 1:
            score = 8
            signal = "BUY"
            reason = f"上升趋势明确(10日+{trend_10d:.1f}%,5日+{trend_5d:.1f}%)"
        elif trend_10d > 1 and same_direction:
            score = 4
            signal = "BUY"
            reason = f"温和上升(10日+{trend_10d:.1f}%)"
        elif trend_10d < -3 and trend_5d < -1:
            score = -8
            signal = "SELL"
            reason = f"下降趋势(10日{trend_10d:.1f}%)，没把握就歇着"
        elif trend_10d < -1:
            score = -4
            signal = "HOLD"
            reason = f"偏弱(10日{trend_10d:.1f}%)，减少操作"
        else:
            score = 0
            signal = "HOLD"
            reason = "震荡期，没把握就歇着"
        
        return score, signal, reason
    
    # ---------- 经验2: 5日/10日线金叉进死叉出 ----------
    def rule2_ma_cross(self, code: str, klines: List[Dict]) -> Tuple[float, str, str]:
        """
        均线金叉/死叉规则：
        - MA5上穿MA10 → 买入信号
        - MA5下穿MA10 → 卖出信号
        - 价格站稳MA10上方 → 持有
        - 跌破MA10 → 减仓
        """
        if not klines or len(klines) < 25:
            return 0, "HOLD", "K线数据不足"
        
        closes = [k['close'] for k in klines]
        current_price = closes[-1]
        
        # 金叉检测
        golden = is_golden_cross(closes)
        death = is_death_cross(closes)
        
        # 均线多头
        bullish = is_ma_bullish(closes)
        
        ma10 = calc_ma(closes, 10)
        ma5 = calc_ma(closes, 5)
        
        if golden:
            score = 9
            signal = "BUY"
            reason = "🟢 MA5上穿MA10（金叉），买入信号"
        elif death:
            score = -9
            signal = "SELL"
            reason = "🔴 MA5下穿MA10（死叉），卖出信号"
        elif bullish and current_price > ma10:
            score = 5
            signal = "HOLD"
            reason = f"均线多头排列，站稳MA10({ma10:.2f})上方"
        elif current_price < ma10:
            score = -5
            signal = "SELL"
            reason = f"跌破MA10({ma10:.2f})，建议减仓"
        else:
            score = 0
            signal = "HOLD"
            reason = f"MA5({ma5:.2f}) MA10({ma10:.2f})，方向不明"
        
        return score, signal, reason
    
    # ---------- 经验3: 大盘跌时选强势股 ----------
    def rule3_strong_in_dip(self, code: str, klines: List[Dict], 
                             market_change: float = 0) -> Tuple[float, str, str]:
        """
        大盘下跌时的强势股筛选：
        - 大盘跌但该票跌幅<2% → 抗跌，潜力股
        - 大盘跌但该票上涨 → 强于大盘
        - 均线多头+抗跌 = 重点机会
        """
        if not klines or len(klines) < 3:
            return 0, "HOLD", "数据不足"
        
        closes = [k['close'] for k in klines]
        stock_change_1d = (closes[-1] - closes[-2]) / closes[-2] * 100 if closes[-2] != 0 else 0
        
        # 只在市场下跌时触发此规则
        if market_change >= 0:
            # 市场不跌时，仅做常规判断
            if stock_change_1d > 2 and is_ma_bullish(closes):
                return 3, "BUY", f"市场平稳，个股上涨{stock_change_1d:.1f}%，均线多头"
            return 0, "HOLD", f"市场不跌(大盘{market_change:+.1f}%)，此规则不触发"
        
        # 市场下跌时的抗跌筛选
        outperformance = stock_change_1d - market_change  # 跑赢大盘幅度
        
        score = 0
        signal = "HOLD"
        reason = ""
        
        if stock_change_1d > 2:
            score = 8
            signal = "BUY"
            reason = f"大盘跌{market_change:.1f}%但该股涨{stock_change_1d:.1f}%，强势！跑赢{outperformance:.1f}%"
        elif stock_change_1d > 0:
            score = 6
            signal = "BUY"
            reason = f"大盘跌{market_change:.1f}%但该股微涨{stock_change_1d:.1f}%，抗跌"
        elif stock_change_1d > -2:
            if is_ma_bullish(closes):
                score = 5
                signal = "BUY"
                reason = f"大盘跌{market_change:.1f}%，该股跌{stock_change_1d:.1f}%<2%，均线多头，潜力股"
            else:
                score = 3
                signal = "BUY"
                reason = f"大盘跌{market_change:.1f}%，该股跌{stock_change_1d:.1f}%<2%，轻度抗跌"
        elif stock_change_1d > market_change:
            score = 1
            signal = "HOLD"
            reason = f"大盘跌{market_change:.1f}%，该股跌{stock_change_1d:.1f}%，跑赢大盘{outperformance:.1f}%"
        else:
            score = -2
            signal = "HOLD"
            reason = f"大盘跌{market_change:.1f}%，该股跌{stock_change_1d:.1f}%，弱于大盘"
        
        return score, signal, reason
    
    # ---------- 经验4: 主升浪前小阴小阳缩量 ----------
    def rule4_surge_prepare(self, code: str, klines: List[Dict]) -> Tuple[float, str, str]:
        """
        主升浪启动前特征：
        - 近5-8日小阴小阳（涨跌幅<3%）
        - 缩量震荡（量能压均量线135下方）
        - 股价在相对低位
        - 一旦放量突破，容易起飞
        """
        if not klines or len(klines) < 20:
            return 0, "HOLD", "数据不足"
        
        closes = [k['close'] for k in klines]
        volumes = [k['volume'] for k in klines]
        highs = [k['high'] for k in klines]
        lows = [k['low'] for k in klines]
        
        # 最近5日K线振幅
        recent_5 = klines[-5:]
        max_high = max(k['high'] for k in recent_5)
        min_low = min(k['low'] for k in recent_5)
        range_pct = (max_high - min_low) / min_low * 100 if min_low > 0 else 0
        
        # 最近5日每根K线的涨跌幅
        daily_changes = []
        for k in recent_5:
            if k['open'] > 0:
                daily_changes.append(abs((k['close'] - k['open']) / k['open'] * 100))
        avg_daily_change = sum(daily_changes) / len(daily_changes) if daily_changes else 5
        
        # 量能 vs 均量线（经验4要求135日）
        vol_ma135 = calc_volume_ma(volumes, 135)
        recent_vol_avg = avg_volume_recent(volumes, 5)
        
        # 价格位置：近20日高低点
        high_20d = max(highs)
        low_20d = min(lows)
        price_position = (closes[-1] - low_20d) / (high_20d - low_20d) if (high_20d - low_20d) > 0 else 0.5
        
        # 综合判断
        small_daily = avg_daily_change < 3  # 小阴小阳
        narrow_range = range_pct < 8         # 震荡区间窄
        low_volume = (vol_ma135 and recent_vol_avg and 
                      recent_vol_avg < vol_ma135 * 0.8)  # 缩量
        low_position = price_position < 0.4   # 低位
        
        score = 0
        signal = "HOLD"
        reason = ""
        
        conditions_met = sum([1 if small_daily else 0, 1 if narrow_range else 0, 1 if low_volume else 0, 1 if low_position else 0])
        
        if conditions_met >= 3:
            score = 8
            signal = "BUY"
            reason = f"⭐主升浪前兆！小阴小阳(日均{avg_daily_change:.1f}%)+缩量+低位，{conditions_met}/4条件满足"
        elif conditions_met >= 2:
            score = 4
            signal = "BUY"
            reason = f"可能蓄势(日均{avg_daily_change:.1f}%,量能{'缩' if low_volume else '平'})，{conditions_met}/4条件"
        elif small_daily and narrow_range:
            score = 2
            signal = "HOLD"
            reason = f"震荡区间收窄({range_pct:.1f}%)，观察量能变化"
        else:
            score = 0
            signal = "HOLD"
            reason = f"非主升浪前特征(日均波幅{avg_daily_change:.1f}%,位置{price_position:.0%})"
        
        return score, signal, reason
    
    # ---------- 经验5: 强者恒强，只做龙头 ----------
    def rule5_leader_strength(self, code: str, klines: List[Dict],
                                sector_stocks: List[Dict] = None) -> Tuple[float, str, str]:
        """
        强者恒强 / 龙头策略：
        - 近5日涨幅在板块中排名前3
        - 跌幅小于板块平均（抗跌）
        - 放量领涨
        """
        if not klines or len(klines) < 5:
            return 0, "HOLD", "数据不足"
        
        closes = [k['close'] for k in klines]
        volumes = [k['volume'] for k in klines]
        
        # 5日涨幅
        change_5d = (closes[-1] - closes[-5]) / closes[-5] * 100 if closes[-5] != 0 else 0
        
        # 10日涨幅
        change_10d = (closes[-1] - closes[-10]) / closes[-10] * 100 if len(closes) >= 10 and closes[-10] != 0 else 0
        
        # 是否放量上涨（近5日均量 vs 前5日均量）
        vol_recent5 = sum(volumes[-5:]) / 5
        vol_prev5 = sum(volumes[-10:-5]) / 5 if len(volumes) >= 10 else vol_recent5
        vol_expand = vol_recent5 > vol_prev5 * 1.2 if vol_prev5 > 0 else False
        
        # 是否持续上涨（近5日至少4天收阳）
        up_days = sum(1 for k in klines[-5:] if k['close'] >= k['open'])
        
        score = 0
        signal = "HOLD"
        reason = ""
        
        if change_5d > 10 and vol_expand:
            score = 8
            signal = "BUY"
            reason = f"🔥强势龙头！5日涨{change_5d:.1f}%，放量{1 + (vol_recent5/vol_prev5 - 1):.0%}"
        elif change_5d > 5 and up_days >= 3:
            score = 6
            signal = "BUY"
            reason = f"领涨股(5日+{change_5d:.1f}%,{up_days}/5日收阳)"
        elif change_5d > 0 and change_10d > 0:
            score = 3
            signal = "BUY"
            reason = f"趋势向上(5日+{change_5d:.1f}%,10日+{change_10d:.1f}%)"
        elif change_5d < -5:
            score = -4
            signal = "HOLD"
            reason = f"5日跌{change_5d:.1f}%，非龙头，回避"
        elif change_5d < 0:
            score = -2
            signal = "HOLD"
            reason = f"表现偏弱(5日{change_5d:.1f}%)"
        else:
            score = 0
            signal = "HOLD"
            reason = f"5日涨{change_5d:.1f}%，未显强势"
        
        return score, signal, reason
    
    # ---------- 经验6: 缩量拿，放量跑 ----------
    def rule6_volume_core(self, code: str, klines: List[Dict], 
                           pr_data: Dict = None) -> Tuple[float, str, str]:
        """
        成交量核心规则：
        - 上升趋势中缩量 → 继续持有（筹码稳定）
        - 放巨量/天量 → 准备离场（可能是主力出货）
        - 下跌趋势中放量 → 加速下跌信号
        """
        if not klines or len(klines) < 10:
            return 0, "HOLD", "数据不足"
        
        volumes = [k['volume'] for k in klines]
        closes = [k['close'] for k in klines]
        
        # 前20日平均量（用可用数据）
        avg_vol = calc_ma(volumes, min(10, len(volumes)))
        # 今日量（用最近一日）
        today_vol = volumes[-1]
        # 量比
        volume_ratio = today_vol / avg_vol if avg_vol and avg_vol > 0 else 1
        
        # 趋势方向
        trend = (closes[-1] - closes[-5]) / closes[-5] * 100 if len(closes) >= 5 and closes[-5] != 0 else 0
        
        score = 0
        signal = "HOLD"
        reason = ""
        
        if trend > 0:
            # 上升趋势
            if volume_ratio > 2.5:
                score = -6
                signal = "SELL"
                reason = f"⚠️天量！量比{volume_ratio:.1f}倍，上升趋势中放量过大，主力可能出货"
            elif volume_ratio > 1.8:
                score = -3
                signal = "HOLD"
                reason = f"放量上涨(量比{volume_ratio:.1f})，注意控制仓位"
            elif volume_ratio < 0.6:
                score = 5
                signal = "BUY"
                reason = f"缩量上涨(量比{volume_ratio:.1f})，筹码稳定，继续持有"
            elif volume_ratio < 0.8:
                score = 3
                signal = "HOLD"
                reason = f"温和缩量(量比{volume_ratio:.1f})，趋势健康"
            else:
                score = 2
                signal = "HOLD"
                reason = f"量能正常(量比{volume_ratio:.1f})，趋势{trend:+.1f}%"
        else:
            # 下降或震荡
            if volume_ratio > 2.0:
                score = -7
                signal = "SELL"
                reason = f"放量大跌！量比{volume_ratio:.1f}倍，加速离场"
            elif volume_ratio > 1.5:
                score = -4
                signal = "SELL"
                reason = f"放量下跌(量比{volume_ratio:.1f})，离场"
            elif volume_ratio < 0.5:
                score = 2
                signal = "HOLD"
                reason = f"缩量调整(量比{volume_ratio:.1f})，可观察"
            else:
                score = -1
                signal = "HOLD"
                reason = f"量比{volume_ratio:.1f}，下降趋势，保持观望"
        
        return score, signal, reason
    
    # ---------- 经验7: 会卖是师父 ----------
    def rule7_sell_discipline(self, code: str, klines: List[Dict],
                                cost_price: float = None) -> Tuple[float, str, str]:
        """
        止盈纪律：
        - 盈利>8% → 开始分批减仓（确定性信号）
        - 盈利>15% → 大幅减仓
        - 盈利>20% → 基本清仓（守住利润）
        - 从最高点回撤>5% → 止盈线触发
        - 亏损>5% → 严格止损
        """
        if not klines or len(klines) < 5:
            return 0, "HOLD", "数据不足"
        
        closes = [k['close'] for k in klines]
        current = closes[-1]
        high_10d = max(closes[-10:]) if len(closes) >= 10 else max(closes)
        
        score = 0
        signal = "HOLD"
        reason = ""
        
        if cost_price and cost_price > 0:
            profit_pct = (current - cost_price) / cost_price * 100
            drawdown_from_high = (high_10d - current) / high_10d * 100 if high_10d > 0 else 0
            
            if profit_pct >= 20:
                score = -9
                signal = "SELL"
                reason = f"🎯盈利{profit_pct:.1f}%>20%，大幅减仓锁定利润！从高点回撤{drawdown_from_high:.1f}%"
            elif profit_pct >= 15:
                score = -6
                signal = "SELL"
                reason = f"🎯盈利{profit_pct:.1f}%>15%，分批减仓"
            elif profit_pct >= 8:
                score = -3
                signal = "SELL"
                reason = f"💰盈利{profit_pct:.1f}%≥8%，开始减仓守住利润"
            elif profit_pct <= self.stop_loss_pct:
                score = -8
                signal = "SELL"
                reason = f"🛑严格止损！亏损{profit_pct:.1f}%≥{abs(self.stop_loss_pct):.0f}%，执行止损"
            elif drawdown_from_high >= 5 and profit_pct > 0:
                score = -4
                signal = "SELL"
                reason = f"📉从10日高点回撤{drawdown_from_high:.1f}%≥5%，触发止盈线"
            elif profit_pct > 0:
                score = 2
                signal = "HOLD"
                reason = f"盈利{profit_pct:.1f}%，持有观察"
            else:
                score = -1
                signal = "HOLD"
                reason = f"亏损{profit_pct:.1f}%，关注"
        else:
            # 无成本数据：纯技术止盈
            if high_10d > 0:
                drawdown = (high_10d - current) / high_10d * 100
                if drawdown >= 8:
                    score = -5
                    signal = "SELL"
                    reason = f"从高点回撤{drawdown:.1f}%，建议减仓"
                elif drawdown >= 5:
                    score = -3
                    signal = "SELL"
                    reason = f"从高点回撤{drawdown:.1f}%，注意止盈"
        
        return score, signal, reason
    
    # ---------- 经验8: 趋势第一 ----------
    def rule8_trend_first(self, code: str, klines: List[Dict]) -> Tuple[float, str, str]:
        """
        趋势第一原则：
        - 不因为价格低就买，不因为价格高就卖
        - 上涨不言顶，下跌不言底
        - 恐高的人容易错过主升浪
        - 核心：只看趋势方向，不看绝对价格
        """
        if not klines or len(klines) < 25:
            return 0, "HOLD", "数据不足"
        
        closes = [k['close'] for k in klines]
        
        # MA趋势系统
        ma5 = calc_ma(closes, 5)
        ma10 = calc_ma(closes, 10)
        ma20 = calc_ma(closes, 20)
        
        if not all(v is not None for v in [ma5, ma10, ma20]):
            return 0, "HOLD", "均线计算失败"
        
        # 趋势强度：价格相对MA20的位置
        price_vs_ma20 = (closes[-1] - ma20) / ma20 * 100
        
        # 均线上MA5>MA10>MA20 = 上升趋势
        strong_uptrend = ma5 > ma10 > ma20
        mild_uptrend = ma5 > ma10
        downtrend = ma5 < ma10 < ma20
        mild_downtrend = ma5 < ma10
        
        score = 0
        signal = "HOLD"
        reason = ""
        
        if strong_uptrend and price_vs_ma20 > 5:
            score = 7
            signal = "BUY"
            reason = f"强劲上升趋势(MA5>MA10>MA20)，价格高于MA20 {price_vs_ma20:.1f}%，恐高者勿入但趋势第一"
        elif strong_uptrend:
            score = 5
            signal = "BUY"
            reason = f"均线多头排列，上升趋势确立(相对MA20 {price_vs_ma20:+.1f}%)"
        elif mild_uptrend:
            score = 2
            signal = "BUY"
            reason = f"温和上升(MA5>MA10)"
        elif downtrend and price_vs_ma20 < -5:
            score = -7
            signal = "SELL"
            reason = f"下跌不言底！均线空头，低于MA20 {price_vs_ma20:.1f}%，趋势第一=不要抄底"
        elif downtrend:
            score = -5
            signal = "SELL"
            reason = f"均线空头排列(MA5<MA10<MA20)，趋势向下"
        elif mild_downtrend:
            score = -2
            signal = "HOLD"
            reason = f"偏弱趋势(MA5<MA10)"
        else:
            score = 0
            signal = "HOLD"
            reason = f"趋势不明(MA5={ma5:.2f}, MA10={ma10:.2f}, MA20={ma20:.2f})"
        
        return score, signal, reason
    
    # ---------- 经验9: 复盘+银行板块风向标 ----------
    def rule9_bank_signal(self, index_data: Dict = None,
                           bank_data: Dict = None) -> Tuple[float, str, str]:
        """
        银行板块风向标：
        - 银行比大盘弱 → 大资金进攻 → 市场容易涨 → 偏多
        - 银行比大盘强 → 大资金防守 → 大盘容易调 → 偏空
        - 两者差不多 → 盘整
        
        注意：这是市场级别信号，不针对个股
        """
        if not index_data or not bank_data:
            return 0, "HOLD", "缺少大盘/银行数据"
        
        market_chg = index_data.get('change_pct', 0)
        bank_chg = bank_data.get('change_pct', 0)
        diff = bank_chg - market_chg  # 银行相对强弱
        
        score = 0
        signal = "HOLD"
        reason = ""
        
        if diff < -1.5:
            score = 6
            signal = "BUY"
            reason = f"📈进攻信号！银行{bank_chg:+.1f}% << 大盘{market_chg:+.1f}%，大资金抛银行买科技，大盘容易涨"
        elif diff < -0.5:
            score = 3
            signal = "BUY"
            reason = f"偏进攻(银行{bank_chg:+.1f}% < 大盘{market_chg:+.1f}%)，市场偏暖"
        elif diff > 1.5:
            score = -6
            signal = "SELL"
            reason = f"🛡️防守信号！银行{bank_chg:+.1f}% >> 大盘{market_chg:+.1f}%，大资金买银行避险，大盘容易调"
        elif diff > 0.5:
            score = -3
            signal = "SELL"
            reason = f"偏防守(银行{bank_chg:+.1f}% > 大盘{market_chg:+.1f}%)，市场偏弱"
        else:
            score = 0
            signal = "HOLD"
            reason = f"盘整期(银行{bank_chg:+.1f}% ≈ 大盘{market_chg:+.1f}%)，等待方向"
        
        return score, signal, reason
    
    # ==================== 综合分析 ====================
    
    def evaluate(self, code: str, cost_price: float = None,
                   sector_stocks: List[Dict] = None) -> Dict:
        """
        综合评估一只股票
        返回每条规则的打分和综合建议
        """
        # 获取数据
        klines = fetch_kline(code, count=25)
        rt_data = fetch_realtime(code)
        
        if not klines:
            return {
                'code': code,
                'total_score': 0,
                'composite_signal': 'HOLD',
                'composite_reason': 'K线数据获取失败',
                'rules': {},
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
            }
        
        # 大盘数据
        index_data = fetch_index_data()
        bank_data = fetch_bank_index()
        market_change = index_data['change_pct'] if index_data else 0
        
        # 逐条规则评估
        rules = {}
        
        # 经验1: 市场节奏
        r1_score, r1_sig, r1_reason = self.rule1_market_timing(code, klines)
        rules['经验1_市场节奏'] = {'score': r1_score, 'signal': r1_sig, 'reason': r1_reason}
        
        # 经验2: 均线金叉死叉
        r2_score, r2_sig, r2_reason = self.rule2_ma_cross(code, klines)
        rules['经验2_均线交叉'] = {'score': r2_score, 'signal': r2_sig, 'reason': r2_reason}
        
        # 经验3: 大盘跌时选强势股
        r3_score, r3_sig, r3_reason = self.rule3_strong_in_dip(code, klines, market_change)
        rules['经验3_抗跌选股'] = {'score': r3_score, 'signal': r3_sig, 'reason': r3_reason}
        
        # 经验4: 主升浪前兆
        r4_score, r4_sig, r4_reason = self.rule4_surge_prepare(code, klines)
        rules['经验4_主升浪'] = {'score': r4_score, 'signal': r4_sig, 'reason': r4_reason}
        
        # 经验5: 强者恒强
        r5_score, r5_sig, r5_reason = self.rule5_leader_strength(code, klines, sector_stocks)
        rules['经验5_龙头策略'] = {'score': r5_score, 'signal': r5_sig, 'reason': r5_reason}
        
        # 经验6: 量能核心
        r6_score, r6_sig, r6_reason = self.rule6_volume_core(code, klines, rt_data)
        rules['经验6_量能核心'] = {'score': r6_score, 'signal': r6_sig, 'reason': r6_reason}
        
        # 经验7: 止盈纪律
        r7_score, r7_sig, r7_reason = self.rule7_sell_discipline(code, klines, cost_price)
        rules['经验7_止盈纪律'] = {'score': r7_score, 'signal': r7_sig, 'reason': r7_reason}
        
        # 经验8: 趋势第一
        r8_score, r8_sig, r8_reason = self.rule8_trend_first(code, klines)
        rules['经验8_趋势第一'] = {'score': r8_score, 'signal': r8_sig, 'reason': r8_reason}
        
        # 经验9: 银行风向标（市场级信号）
        r9_score, r9_sig, r9_reason = self.rule9_bank_signal(index_data, bank_data)
        rules['经验9_银行风向标'] = {'score': r9_score, 'signal': r9_sig, 'reason': r9_reason}
        
        # ==================== 综合评分 ====================
        # 核心规则权重（经验2、6、8是核心，权重更高）
        weights = {
            '经验1_市场节奏': 1.0,
            '经验2_均线交叉': 1.5,   # 高权重：金叉死叉最实用
            '经验3_抗跌选股': 0.8,
            '经验4_主升浪': 1.0,
            '经验5_龙头策略': 1.0,
            '经验6_量能核心': 1.5,   # 高权重：量不会骗人
            '经验7_止盈纪律': 1.3,   # 较高权重：会卖是师父
            '经验8_趋势第一': 1.2,   # 较高权重：趋势第一
            '经验9_银行风向标': 0.8,  # 市场级信号，对个股影响间接
        }
        
        weighted_total = 0
        weight_sum = 0
        for rule_name, rule_data in rules.items():
            w = weights.get(rule_name, 1.0)
            weighted_total += rule_data['score'] * w
            weight_sum += w
        
        total_score = weighted_total / weight_sum if weight_sum > 0 else 0
        # 归一化到 -100 ~ +100
        total_score = max(-100, min(100, total_score * 3))
        
        # 综合信号
        # 信号投票
        buy_votes = sum(1 for r in rules.values() if r['signal'] == 'BUY')
        sell_votes = sum(1 for r in rules.values() if r['signal'] == 'SELL')
        
        if total_score >= 30 and buy_votes >= 4:
            composite_signal = 'BUY'
            composite_reason = f'强烈买入({buy_votes}/9看涨，综合分{total_score:.0f})'
        elif total_score >= 15 and buy_votes >= 3:
            composite_signal = 'BUY'
            composite_reason = f'买入({buy_votes}/9看涨，综合分{total_score:.0f})'
        elif total_score <= -30 and sell_votes >= 4:
            composite_signal = 'SELL'
            composite_reason = f'强烈卖出({sell_votes}/9看空，综合分{total_score:.0f})'
        elif total_score <= -15 and sell_votes >= 3:
            composite_signal = 'SELL'
            composite_reason = f'卖出({sell_votes}/9看空，综合分{total_score:.0f})'
        else:
            composite_signal = 'HOLD'
            composite_reason = f'观望(buy={buy_votes},sell={sell_votes},综合分{total_score:.0f})'
        
        result = {
            'code': code,
            'total_score': round(total_score),
            'composite_signal': composite_signal,
            'composite_reason': composite_reason,
            'buy_votes': buy_votes,
            'sell_votes': sell_votes,
            'rules': rules,
            'kline_summary': {
                'current_price': klines[-1]['close'] if klines else None,
                'date': klines[-1]['date'] if klines else None,
            },
            'market_context': {
                'index': index_data,
                'bank': bank_data,
            },
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M'),
        }
        
        if rt_data:
            result['realtime'] = rt_data
        
        return result
    
    def screen_all(self, codes: List[str], cost_map: Dict[str, float] = None) -> List[Dict]:
        """
        批量筛选多只股票
        cost_map: {code: cost_price} 成本价映射
        """
        results = []
        for code in codes:
            cost = cost_map.get(code) if cost_map else None
            result = self.evaluate(code, cost_price=cost)
            results.append(result)
        
        # 按综合分排序
        results.sort(key=lambda x: x['total_score'], reverse=True)
        return results
    
    def get_advice(self, code: str, cost_price: float = None) -> str:
        """获取格式化的操作建议"""
        result = self.evaluate(code, cost_price=cost_price)
        
        signal_emoji = {'BUY': '🟢', 'HOLD': '🟡', 'SELL': '🔴'}
        emoji = signal_emoji.get(result['composite_signal'], '⚪')
        
        lines = []
        lines.append(f"## {emoji} {code} — 九条经验分析报告")
        lines.append(f"**综合评分: {result['total_score']} | 信号: {result['composite_signal']}**")
        lines.append(f"➤ {result['composite_reason']}")
        lines.append("")
        
        if result.get('kline_summary', {}).get('current_price'):
            lines.append(f"现价: {result['kline_summary']['current_price']}")
            lines.append("")
        
        lines.append("### 九条规则逐项评估:")
        for name, rule in result['rules'].items():
            rule_emoji = signal_emoji.get(rule['signal'], '⚪')
            lines.append(f"- {rule_emoji} **{name}** [{rule['signal']}] 得分:{rule['score']:+}")
            lines.append(f"  {rule['reason']}")
        
        if result.get('realtime'):
            rt = result['realtime']
            lines.append("")
            lines.append(f"---")
            lines.append(f"📊 实时: {rt.get('name','')} | {rt.get('price',0):.2f}元 | {rt.get('change_pct',0):+.2f}% | 成交{rt.get('amount',0):.1f}亿")
        
        return "\n".join(lines)


# ==================== 与五层确认框架集成 ====================

def nine_rules_to_fivelayer(nine_result: Dict) -> Dict:
    """
    将九条经验的结果转换为五层确认框架的增强输入
    可以在现有五层框架基础上叠加九条经验的信号
    """
    rules = nine_result.get('rules', {})
    
    # Layer 1增强：流动性已经在原框架处理
    
    # Layer 2增强：趋势+结构 ← 经验2(均线交叉)+经验8(趋势第一)
    l2_boost = 0
    r2 = rules.get('经验2_均线交叉', {})
    r8 = rules.get('经验8_趋势第一', {})
    if r2.get('signal') == 'BUY' and r8.get('signal') == 'BUY':
        l2_boost = 15  # 均线金叉+趋势第一 = 强趋势确认
    elif r2.get('signal') == 'SELL' and r8.get('signal') == 'SELL':
        l2_boost = -15  # 死叉+下降趋势 = 强下降确认
    
    # Layer 3增强：市场环境 ← 经验9(银行风向标)+经验1(市场节奏)
    l3_boost = 0
    r9 = rules.get('经验9_银行风向标', {})
    r1 = rules.get('经验1_市场节奏', {})
    if r9.get('signal') == 'BUY' and r1.get('signal') == 'BUY':
        l3_boost = 10  # 进攻信号+上升趋势
    elif r9.get('signal') == 'SELL' and r1.get('signal') == 'SELL':
        l3_boost = -10
    
    # Layer 4增强：确认信号 ← 经验4(主升浪)+经验5(龙头)+经验6(量能)
    l4_boost = 0
    confirm_signals = 0
    for key in ['经验4_主升浪', '经验5_龙头策略', '经验6_量能核心']:
        if rules.get(key, {}).get('signal') == 'BUY':
            confirm_signals += 1
    if confirm_signals >= 2:
        l4_boost = 1.5  # 加确认数
    
    return {
        'layer2_boost': l2_boost,
        'layer3_boost': l3_boost,
        'layer4_boost': l4_boost,
        'nine_rules_score': nine_result.get('total_score', 0),
        'nine_rules_signal': nine_result.get('composite_signal', 'HOLD'),
    }


# ==================== 测试入口 ====================

if __name__ == "__main__":
    engine = NineRuleEngine()
    
    print("=" * 60)
    print("🧠 九条经验选股规则引擎 v1.0")
    print("=" * 60)
    print()
    
    # 测试持仓股
    test_stocks = [
        ('300059', 20.56),  # 东方财富，成本
        ('600383', 3.51),   # 金地集团，成本
    ]
    
    for code, cost in test_stocks:
        print(engine.get_advice(code, cost_price=cost))
        print()
        print("=" * 60)
        print()
    
    # 测试其他股票
    extra_codes = ['600601', '601677', '000967']
    print("\n📊 批量筛选结果:")
    print("-" * 60)
    results = engine.screen_all(extra_codes)
    for r in results:
        sig = r['composite_signal']
        emoji = {'BUY': '🟢', 'HOLD': '🟡', 'SELL': '🔴'}.get(sig, '⚪')
        price = r.get('kline_summary', {}).get('current_price', '?')
        print(f"{emoji} {r['code']} | 价格:{price} | 评分:{r['total_score']:+d} | {sig} | {r['composite_reason']}")
