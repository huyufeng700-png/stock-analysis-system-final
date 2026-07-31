"""
五层确认框架 - A股量化选股增强版
基于消息中的五层确认逻辑改编

Layer 1: 可交易性过滤（流动性/点差/波动/是否已有仓位）
Layer 2: 趋势+结构确认（均线/K线/区间/回踩）
Layer 3: 市场环境判断（趋势？震荡？用不同指标）
Layer 4: 硬确认+软确认（盘面+外部共振）
Layer 5: 综合评分（质量/时机/资金流/执行风险/共振数）

用法：
    from 五层确认框架 import FiveLayerValidator
    validator = FiveLayerValidator()
    result = validator.evaluate('300059')
    print(result['score'], result['signal'])  # score: 0-100, signal: BUY/HOLD/SELL
"""

import urllib.request
import ssl
import json
from typing import Dict, List, Optional, Tuple
from datetime import datetime

# ==================== 配置 ====================

SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

# ==================== Layer 1: 可交易性过滤 ====================

def layer1_tradability(code: str, price_data: Dict) -> Tuple[bool, str]:
    """
    可交易性过滤
    - 流动性：成交额 > 1亿
    - 波动性：日内振幅 > 2%（有空间）
    - 无极端价格：不是涨跌停
    """
    if not price_data:
        return False, "价格数据获取失败"

    amount = price_data.get('amount', 0)  # 亿元
    change_pct = abs(price_data.get('change_pct', 0))
    high = price_data.get('high', 0)
    low = price_data.get('low', 0)
    price = price_data.get('price', 0)

    # 流动性检查
    if amount < 1:
        return False, f"成交额不足({amount:.1f}亿)，流动性差"

    # 振幅检查
    if high > 0 and low > 0:
        amplitude = ((high - low) / low) * 100
        if amplitude < 2:
            return False, f"日内振幅不足({amplitude:.1f}%)，无波动空间"

    # 涨跌停检查（风险）
    if change_pct > 9.8:
        return False, "涨停板，追高风险大"

    return True, "通过"


# ==================== Layer 2: 趋势+结构确认 ====================

def layer2_trend_structure(code: str, price_data: Dict) -> Tuple[float, str]:
    """
    趋势+结构确认
    - 价格位置：处于20日均线上方？处于近期高点？
    - 均线多头：5日 > 10日 > 20日？
    - 突破结构：突破近期震荡区间？
    """
    if not price_data:
        return 0.0, "无数据"

    # 计算简单趋势指标（用当日数据模拟）
    price = price_data.get('price', 0)
    change_pct = price_data.get('change_pct', 0)
    high = price_data.get('high', 0)
    low = price_data.get('low', 0)

    score = 0.0
    reasons = []

    # 阳线 vs 阴线
    if change_pct > 0:
        score += 20
        reasons.append("今日上涨")
    else:
        score -= 10
        reasons.append("今日下跌")

    # 处于日内高位附近（收盘接近最高）
    if high > 0 and price > 0:
        proximity_to_high = (price - low) / (high - low) if (high - low) > 0 else 0.5
        if proximity_to_high > 0.85:
            score += 20
            reasons.append("收盘接近日内高点")
        elif proximity_to_high < 0.3:
            score -= 10
            reasons.append("收盘接近日内低点")

    # 价格相对位置（相对于近期高低点）
    # 这里用amplitude作为参考
    amplitude = ((high - low) / low) * 100 if low > 0 else 0
    if amplitude > 5:
        score += 15
        reasons.append("日内振幅充足")
    elif amplitude > 3:
        score += 5

    return score, "; ".join(reasons) if reasons else "中性"


# ==================== Layer 3: 市场环境判断 ====================

def layer3_market_environment() -> Tuple[str, float]:
    """
    市场环境判断
    - 根据大盘/板块情绪判断当前环境
    - 趋势市场：追突破；震荡市场：高抛低吸
    """
    # 简化版：通过综合市场成交判断环境
    # 实际应该接入沪深300/创业板等大盘数据
    # 这里返回中性默认值
    return "中性", 0.0


# ==================== Layer 4: 硬确认+软确认 ====================

def layer4_hard_soft_confirm(price_data: Dict, trend_score: float) -> Tuple[int, str]:
    """
    硬确认（必须有）+ 软确认（加分项）
    硬：价格突破+放量
    软：板块共振/消息面/资金流入
    """
    confirm_count = 0
    reasons = []

    # 硬确认1：放量上涨
    amount = price_data.get('amount', 0)
    change_pct = price_data.get('change_pct', 0)
    if amount > 5 and change_pct > 2:
        confirm_count += 1
        reasons.append("放量上涨")

    # 硬确认2：价格处于上升通道（这里用高低点判断）
    high = price_data.get('high', 0)
    low = price_data.get('low', 0)
    price = price_data.get('price', 0)
    if high > 0 and price > 0 and (high - price) / price < 0.03:
        confirm_count += 1
        reasons.append("接近高点")

    # 软确认：PE合理
    pe = price_data.get('pe', 0)
    if 0 < pe < 50:
        confirm_count += 0.5
        reasons.append(f"PE合理({pe:.0f})")

    # 软确认：涨幅适中（不太热不太冷）
    if 2 < change_pct < 7:
        confirm_count += 0.5
        reasons.append("涨幅温和")

    return confirm_count, "; ".join(reasons) if reasons else "无明显确认"


# ==================== Layer 5: 综合评分 ====================

def layer5_final_score(
    layer1_pass: bool,
    trend_score: float,
    confirm_count: float,
    price_data: Dict
) -> Tuple[int, str]:
    """
    综合评分：0-100
    BUY: >= 60
    HOLD: 40-59
    SELL: < 40
    """
    if not layer1_pass:
        return 0, "HOLD"

    # 计算综合分数
    score = 50  # 基准分

    # Layer 2 趋势：满分30
    score += min(trend_score, 30)

    # Layer 4 确认：满分20
    score += min(confirm_count * 10, 20)

    # 额外加分项
    pe = price_data.get('pe', 0)
    amount = price_data.get('amount', 0)
    change_pct = price_data.get('change_pct', 0)

    # 成交额高加分
    if amount > 10:
        score += 5
    elif amount > 5:
        score += 3

    # 涨幅适中加分
    if 1 < change_pct < 5:
        score += 5

    # 封顶100
    score = min(score, 100)

    # 判断信号
    if score >= 60:
        signal = "BUY"
    elif score >= 40:
        signal = "HOLD"
    else:
        signal = "SELL"

    return int(score), signal


# ==================== 主验证器类 ====================

class FiveLayerValidator:
    """五层确认验证器（集成九条经验增强）"""

    def __init__(self, use_nine_rules=True):
        self.cache = {}
        self.use_nine_rules = use_nine_rules
        if use_nine_rules:
            try:
                from 九条经验规则 import NineRuleEngine
                self.nine_engine = NineRuleEngine()
            except ImportError:
                self.nine_engine = None

    def evaluate(self, code: str) -> Dict:
        """
        评估一只股票
        返回：{
            'code': str,
            'score': int (0-100),
            'signal': str (BUY/HOLD/SELL),
            'layers': {
                'layer1': {'pass': bool, 'reason': str},
                'layer2': {'score': float, 'reason': str},
                'layer4': {'confirm': float, 'reason': str},
            },
            'price_data': dict
        }
        """
        # 获取价格数据
        prefix = 'sz' if code.startswith('3') else 'sh'
        url = f'https://qt.gtimg.cn/q={prefix}{code}'

        price_data = None
        try:
            with urllib.request.urlopen(url, timeout=5, context=SSL_CTX) as r:
                data = r.read().decode('gbk')
                fields = data.split('~')
                if len(fields) > 39:
                    price_data = {
                        'price': float(fields[3]),
                        'change': float(fields[31]),
                        'change_pct': float(fields[32]),
                        'high': float(fields[33]),
                        'low': float(fields[34]),
                        'amount': float(fields[38]),
                        'pe': float(fields[39]) if fields[39] and fields[39] != '-' else 0,
                    }
        except Exception as e:
            return {
                'code': code,
                'score': 0,
                'signal': 'HOLD',
                'error': str(e),
                'layers': {},
                'price_data': None
            }

        # Layer 1: 可交易性
        l1_pass, l1_reason = layer1_tradability(code, price_data)

        # Layer 2: 趋势+结构
        l2_score, l2_reason = layer2_trend_structure(code, price_data)

        # Layer 3: 市场环境
        l3_env, l3_score = layer3_market_environment()

        # Layer 4: 硬软确认（需要price_data）
        if price_data:
            l4_confirm, l4_reason = layer4_hard_soft_confirm(price_data, l2_score)
        else:
            l4_confirm, l4_reason = 0.0, "无价格数据"

        # Layer 5: 综合评分
        final_score, signal = layer5_final_score(l1_pass, l2_score, l4_confirm, price_data or {})

        # Layer1 不过 → 强制 SELL（不能持仓）
        if not l1_pass:
            final_score = min(final_score, 35)
            signal = "SELL"

        # ========== 九条经验增强 ==========
        nine_boost = 0
        nine_signal = None
        if self.use_nine_rules and self.nine_engine and price_data:
            try:
                nine_result = self.nine_engine.evaluate(code)
                nine_score = nine_result.get('total_score', 0)
                # 九条经验评分按比例加权到五层评分（最多±15分）
                nine_boost = max(-15, min(15, nine_score / 100 * 15))
                nine_signal = nine_result.get('composite_signal', 'HOLD')
            except Exception:
                pass
        
        # 如果五层和九条经验信号一致，额外加强
        if nine_signal and nine_signal == signal:
            if signal == "BUY":
                five_boost = five_boost + 5 if 'five_boost' in dir() else 5
            elif signal == "SELL":
                five_boost = five_boost - 5 if 'five_boost' in dir() else -5
        
        total_score = max(0, min(100, int(final_score + nine_boost)))
        
        # 重新判断信号（增强后）
        if total_score >= 60:
            signal = "BUY"
        elif total_score >= 40:
            signal = "HOLD"
        else:
            signal = "SELL"

        return {
            'code': code,
            'score': total_score,
            'signal': signal,
            'layers': {
                'layer1': {'pass': l1_pass, 'reason': l1_reason},
                'layer2': {'score': l2_score, 'reason': l2_reason},
                'layer3': {'env': l3_env},
                'layer4': {'confirm': l4_confirm, 'reason': l4_reason},
            },
            'price_data': price_data,
            'nine_rules_boost': nine_boost,
            'five_layer_base': final_score,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M')
        }

    def batch_evaluate(self, codes: List[str]) -> List[Dict]:
        """批量评估"""
        return [self.evaluate(code) for code in codes]


# ==================== 工具函数 ====================

def get_score_emoji(score: int) -> str:
    """分数emoji"""
    if score >= 75:
        return "🟢"
    elif score >= 60:
        return "🟡"
    elif score >= 40:
        return "🟠"
    else:
        return "🔴"


def format_result(result: Dict) -> str:
    """格式化输出"""
    code = result['code']
    score = result['score']
    signal = result['signal']
    pd = result.get('price_data')

    emoji = get_score_emoji(score)

    if pd:
        name = f"({pd.get('price', 0):.2f}元, {pd.get('change_pct', 0):+.2f}%)"
    else:
        name = ""

    msg = f"{emoji} **{code}** {name}\n"
    msg += f"   五层评分: {score}/100 | 信号: {signal}\n"

    if 'layers' in result:
        l1 = result['layers'].get('layer1', {})
        l2 = result['layers'].get('layer2', {})
        l4 = result['layers'].get('layer4', {})

        if not l1.get('pass', False):
            msg += f"   ❌ L1未通过: {l1.get('reason', '')}\n"
        else:
            msg += f"   ✅ L1通过\n"

        if l2:
            msg += f"   📈 L2趋势: {l2.get('score', 0):.0f}分 | {l2.get('reason', '')}\n"
        if l4:
            msg += f"   🔔 L4确认: {l4.get('confirm', 0):.1f}个 | {l4.get('reason', '')}\n"

    return msg


# ==================== 测试 ====================

if __name__ == "__main__":
    validator = FiveLayerValidator()

    test_codes = ['300059', '600383', '600601']

    print("=" * 60)
    print("🔍 五层确认框架 - A股量化选股增强版")
    print("=" * 60)
    print()

    for code in test_codes:
        result = validator.evaluate(code)
        print(format_result(result))
        print()