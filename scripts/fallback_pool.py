#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fallback_pool.py - 数据源 Fallback 池
=========================================
解决痛点: 东财 push2 频繁 502/限流 (2026-06-05 验证返回 000)
自动按 fallback 顺序探测可用源,返回第一个能用的数据

设计原则:
  - 零新依赖 (只用 urllib + 标准库)
  - 实时行情 fallback: 腾讯 → 东财 push2
  - K线三源 fallback: 新浪 → 腾讯 web.ifzq → 东财 push2his
  - 智能指数退避,避免连续触发限流
  - 健康状态缓存 5 分钟,减少重复探测

用法:
    pool = FallbackPool()
    spot = pool.get_spot(['sh600584', 'sz002050'])   # 实时行情
    kline = pool.get_kline('sz300059', scale=240, datalen=60)  # K线
    print(pool.status())  # 查看各源健康

[重构 2026-07-31] 从 kanban diff 还原: ~/.hermes/kanban/workspaces/t_97164897/artifacts/fallback_pool.current.diff
"""

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class HealthStatus:
    """单个数据源健康状态"""
    name: str
    last_ok: float = 0.0           # 上次成功时间戳
    last_fail: float = 0.0         # 上次失败时间戳
    fail_count: int = 0            # 连续失败次数
    last_latency: float = 0.0      # 上次延迟(秒)
    cooldown_until: float = 0.0    # 冷却期结束时间(避免限流期间反复探测)

    @property
    def available(self) -> bool:
        return time.time() >= self.cooldown_until

    def record_ok(self, latency: float):
        self.last_ok = time.time()
        self.last_latency = latency
        self.fail_count = 0
        self.cooldown_until = 0.0

    def record_fail(self, latency: float, cooldown: float = 60.0):
        self.last_fail = time.time()
        self.last_latency = latency
        self.fail_count += 1
        # 指数退避: 1次→30s, 2次→60s, 3次→120s, 4次+→300s
        backoff = min(30 * (2 ** (self.fail_count - 1)), 300)
        self.cooldown_until = time.time() + max(cooldown, backoff)


class FallbackPool:
    """数据源 Fallback 池管理器"""

    def __init__(self, health_ttl: int = 300):
        """
        Args:
            health_ttl: 健康状态缓存秒数,默认 5 分钟
        """
        self.health_ttl = health_ttl
        self.sources = {
            'eastmoney': HealthStatus('东方财富 push2'),
            'eastmoney_push2his': HealthStatus('东方财富 push2his'),
            'tencent':   HealthStatus('腾讯 qt.gtimg.cn'),
            'tencent_kline': HealthStatus('腾讯 web.ifzq.gtimg.cn'),
            'sina':      HealthStatus('新浪 money.finance'),
        }
        # Fallback 优先级 (东财数据最全但最易限流)
        self.spot_order = ['tencent', 'eastmoney']           # 行情优先腾讯(稳)
        self.kline_order = ['sina', 'tencent_kline', 'eastmoney_push2his']  # K线三源: 新浪→腾讯→东财历史

    def _fetch(self, url: str, timeout: float = 10.0,
               headers: Optional[dict] = None) -> tuple:
        """统一 fetch,返回 (success, raw_bytes, latency)
        注意: User-Agent 必须用 'Mozilla/5.0' 短版,
              长 UA 会被新浪 K线接口识别为机器人,只返回 null
        """
        hdrs = {'User-Agent': 'Mozilla/5.0'}
        if headers:
            hdrs.update(headers)
        req = urllib.request.Request(url, headers=hdrs)
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read()
                return True, raw, time.time() - t0
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, ConnectionError) as e:
            return False, str(e).encode('utf-8'), time.time() - t0

    def _pick(self, order: list) -> Optional[str]:
        """从候选源中挑第一个可用的"""
        for name in order:
            h = self.sources[name]
            if h.available and (time.time() - h.last_ok < self.health_ttl or h.last_ok == 0):
                return name
            if h.available and time.time() - h.last_ok >= self.health_ttl:
                return name  # 过期,重新探测
        return None  # 全部在冷却

    @staticmethod
    def _tencent_period(scale: int) -> str:
        """把内部 scale 映射为腾讯 K线周期。"""
        return {
            240: 'day',
            120: 'm120',
            60: 'm60',
            30: 'm30',
            15: 'm15',
            5: 'm5',
            1: 'm1',
        }.get(scale, 'day')

    @staticmethod
    def _parse_kline_rows(rows: list) -> list:
        """解析 [date, open, close, high, low, volume] 行为统一结构。"""
        result = []
        for row in rows:
            if len(row) >= 6:
                result.append({
                    'date': row[0],
                    'open': float(row[1] or 0),
                    'close': float(row[2] or 0),
                    'high': float(row[3] or 0),
                    'low': float(row[4] or 0),
                    'volume': float(row[5] or 0),
                })
        return result

    def get_spot(self, codes: list) -> Optional[list]:
        """
        实时行情 - 自动 fallback
        Args:
            codes: ['sh600584', 'sz002050']  (带市场前缀)
        Returns:
            list of dict, 字段: symbol, name, price, change_pct, ...
        """
        # --- 路径 1: 腾讯 (单次最多 50 只) ---
        for batch_start in range(0, len(codes), 50):
            batch = codes[batch_start:batch_start+50]
            url = f"http://qt.gtimg.cn/q={','.join(batch)}"
            ok, raw, lat = self._fetch(url, timeout=15)
            self.sources['tencent'].record_ok(lat) if ok else self.sources['tencent'].record_fail(lat, 30)
            if ok:
                result = []
                for line in raw.decode('gbk', errors='replace').split(';'):
                    if '~' not in line or len(line) < 30:
                        continue
                    parts = line.split('~')
                    if len(parts) < 32:
                        continue
                    try:
                        result.append({
                            'symbol': parts[0].split('=')[0].strip().replace('v_', ''),
                            'name': parts[1],
                            'price': float(parts[3] or 0),
                            'change_pct': float(parts[32] or 0),
                            'volume': float(parts[6] or 0) / 1e8,  # 转亿
                        })
                    except (ValueError, IndexError):
                        continue
                if result:
                    return result

        # --- 路径 2: 东财 (易限流,慎用) ---
        h = self.sources['eastmoney']
        if not h.available:
            return None
        url = f"https://push2.eastmoney.com/api/qt/clist/get?pn=1&pz=100&fs=m:0+t:6+f:!2,m:0+t:13+f:!2,m:0+t:80+f:!2,m:1+t:2+f:!2,m:1+t:23+f:!2&fields=f12,f14,f2,f3"
        ok, raw, lat = self._fetch(url, timeout=15)
        h.record_ok(lat) if ok else h.record_fail(lat, 120)
        if ok:
            try:
                d = json.loads(raw)
                data_map = {x['f12']: x for x in d.get('data', {}).get('diff', [])}
                result = []
                for c in codes:
                    code_pure = c[2:]  # 去市场前缀
                    if code_pure in data_map:
                        item = data_map[code_pure]
                        result.append({
                            'symbol': c,
                            'name': item.get('f14', ''),
                            'price': item.get('f2', 0) / 100 if item.get('f2') else 0,
                            'change_pct': item.get('f3', 0) / 100 if item.get('f3') else 0,
                        })
                return result
            except (json.JSONDecodeError, KeyError):
                pass
        return None

    def get_kline(self, code: str, scale: int = 240, datalen: int = 60) -> Optional[list]:
        """
        K线数据 - 自动 fallback + 重试
        Args:
            code: 纯代码, 'sz300059' 或 '300059' 都可
            scale: 240=日, 60=60分, 15=15分
            datalen: 数据条数
        Returns:
            list of dict, 字段: date, open, close, high, low, volume
        """
        code_pure = code[2:] if code[:2] in ('sh', 'sz') else code

        # --- 路径 1: 新浪 (稳但易临时 ban) - 重试 1 次，失败后马上切第三方源 ---
        h = self.sources['sina']
        if h.available:
            sina_symbol = code if code[:2] in ('sh', 'sz') else ('sh' if code_pure.startswith(('60', '68', '90')) else 'sz') + code_pure
            url = f"https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/CN_MarketData.getKlineData?symbol={sina_symbol}&scale={scale}&ma=no&datalen={datalen}"
            ok, raw, lat = self._fetch(url, timeout=12,
                                       headers={'Referer': 'https://finance.sina.com.cn/'})
            if ok:
                try:
                    data = json.loads(raw)
                    if data and isinstance(data, list) and len(data) > 0:
                        h.record_ok(lat)
                        return [
                            {
                                'date': r.get('day', ''),
                                'open': float(r.get('open', 0) or 0),
                                'close': float(r.get('close', 0) or 0),
                                'high': float(r.get('high', 0) or 0),
                                'low': float(r.get('low', 0) or 0),
                                'volume': float(r.get('volume', 0) or 0),
                            }
                            for r in data
                        ]
                    # 数据是 null/[], 可能是被临时 ban；不要 sleep 阻塞，直接切腾讯 K线
                    h.record_fail(lat, 180)  # 长冷却 3 分钟
                except (json.JSONDecodeError, ValueError):
                    h.record_fail(lat, 60)
            else:
                h.record_fail(lat, 60)

        # --- 路径 2: 腾讯 web.ifzq (第三方 K线源, 与新浪/东财独立) ---
        h = self.sources['tencent_kline']
        if h.available:
            period = self._tencent_period(scale)
            symbol = ('sh' if code_pure.startswith(('60', '68', '90')) else 'sz') + code_pure
            # qfq 前复权；接口通常会多返回当前交易日一条, 调用方按 len>=20 使用即可。
            url = f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={symbol},{period},,,{datalen},qfq"
            ok, raw, lat = self._fetch(url, timeout=12)
            h.record_ok(lat) if ok else h.record_fail(lat, 60)
            if ok:
                try:
                    d = json.loads(raw)
                    node = d.get('data', {}).get(symbol, {}) if d else {}
                    rows = node.get(f'qfq{period}') or node.get(period) or []
                    result = self._parse_kline_rows(rows[-datalen:])
                    if result:
                        return result
                    h.record_fail(lat, 60)
                except (json.JSONDecodeError, ValueError, KeyError, TypeError):
                    h.record_fail(lat, 60)

        # --- 路径 3: 东财 push2his (易限流；与 push2 实时分开记健康度) ---
        h = self.sources['eastmoney_push2his']
        if not h.available:
            return None
        mkt = 1 if code_pure.startswith(('60', '68', '90')) else 0
        klt = 101 if scale == 240 else scale
        url = f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={mkt}.{code_pure}&fields1=f1,f2&fields2=f51,f52,f53,f54,f55,f56&klt={klt}&fqt=1&beg=0&end=20500101&lmt={datalen}"
        ok, raw, lat = self._fetch(url, timeout=15)
        h.record_ok(lat) if ok else h.record_fail(lat, 120)
        if ok:
            try:
                d = json.loads(raw)
                if not d:
                    return None
                klines = d.get('data', {}).get('klines', []) or []
                result = []
                for line in klines[-datalen:]:
                    parts = line.split(',')
                    if len(parts) >= 6:
                        result.append({
                            'date': parts[0],
                            'open': float(parts[1] or 0),
                            'close': float(parts[2] or 0),
                            'high': float(parts[3] or 0),
                            'low': float(parts[4] or 0),
                            'volume': float(parts[5] or 0),
                        })
                return result
            except (json.JSONDecodeError, ValueError, KeyError):
                pass
        return None

    def status(self) -> dict:
        """返回各源健康状态(用于日志/报告)"""
        now = time.time()
        out = {}
        for name, h in self.sources.items():
            out[name] = {
                'name': h.name,
                'available': h.available,
                'fail_count': h.fail_count,
                'last_latency': round(h.last_latency, 3),
                'cooldown_remaining': max(0, int(h.cooldown_until - now)),
                'last_ok_ago': int(now - h.last_ok) if h.last_ok > 0 else None,
            }
        return out


# ============== CLI 自检 ==============
if __name__ == '__main__':
    pool = FallbackPool()
    print("🔍 数据源 Fallback 池自检\n")
    print("【1】实时行情测试 (三花智控 + 长电科技)")
    spots = pool.get_spot(['sz002050', 'sh600584'])
    if spots:
        for s in spots:
            print(f"  {s['symbol']} {s['name']}: {s['price']} ({s['change_pct']:+.2f}%)")
    else:
        print("  ❌ 所有源都失败")

    print("\n【2】K线测试 (东方财富 sz300059, 60 根日K)")
    klines = pool.get_kline('sz300059', scale=240, datalen=60)
    if klines:
        print(f"  ✅ 获取 {len(klines)} 根K线")
        print(f"  最新: {klines[-1]}")
    else:
        print("  ❌ 所有源都失败")

    print("\n【3】数据源健康状态")
    for name, st in pool.status().items():
        emoji = '🟢' if st['available'] and st['fail_count'] == 0 else '🟡' if st['available'] else '🔴'
        cd = f" 冷却{st['cooldown_remaining']}s" if st['cooldown_remaining'] > 0 else ""
        print(f"  {emoji} {st['name']:<25} 延迟={st['last_latency']}s  失败={st['fail_count']}次{cd}")
