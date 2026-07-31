"""
test_fallback_pool.py - fallback_pool.py 单测
[重构 2026-07-31] 从 kanban diff 还原
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fallback_pool", ROOT / "scripts" / "fallback_pool.py")
fallback_pool = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fallback_pool)


def test_kline_falls_back_to_tencent_when_sina_empty_and_push2his_unavailable():
    pool = fallback_pool.FallbackPool()
    calls = []

    sina_url = "CN_MarketData.getKlineData"
    tencent_url = "web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    push2his_url = "push2his.eastmoney.com"

    def fake_fetch(url, timeout=10.0, headers=None):
        calls.append(url)
        if sina_url in url:
            return True, b"null", 0.1
        if tencent_url in url:
            return True, b'{"code":0,"data":{"sz300059":{"qfqday":[["2026-06-01","19.05","18.97","19.27","18.85","2445990.000"]]}}}', 0.2
        if push2his_url in url:
            return False, b"Empty reply from server", 0.3
        raise AssertionError(f"unexpected url: {url}")

    pool._fetch = fake_fetch

    data = pool.get_kline("sz300059", scale=240, datalen=5)

    assert data == [{
        "date": "2026-06-01",
        "open": 19.05,
        "close": 18.97,
        "high": 19.27,
        "low": 18.85,
        "volume": 2445990.0,
    }]
    assert any(sina_url in url for url in calls)
    assert any(tencent_url in url for url in calls)
    assert not any(push2his_url in url for url in calls), "Tencent success should stop before push2his"
    assert pool.status()["sina"]["fail_count"] == 1
    assert pool.status()["tencent_kline"]["fail_count"] == 0


def test_kline_uses_push2his_as_third_source_when_sina_and_tencent_fail():
    pool = fallback_pool.FallbackPool()
    calls = []

    def fake_fetch(url, timeout=10.0, headers=None):
        calls.append(url)
        if "CN_MarketData.getKlineData" in url:
            return True, b"[]", 0.1
        if "web.ifzq.gtimg.cn/appstock/app/fqkline/get" in url:
            return False, b"timeout", 0.2
        if "push2his.eastmoney.com" in url:
            return True, b'{"data":{"klines":["2026-06-01,19.05,18.97,19.27,18.85,2445990"]}}', 0.3
        raise AssertionError(f"unexpected url: {url}")

    pool._fetch = fake_fetch

    data = pool.get_kline("sz300059", scale=240, datalen=5)

    assert data and data[0]["date"] == "2026-06-01"
    assert any("CN_MarketData.getKlineData" in url for url in calls)
    assert any("web.ifzq.gtimg.cn/appstock/app/fqkline/get" in url for url in calls)
    assert any("push2his.eastmoney.com" in url for url in calls)
    assert pool.status()["eastmoney_push2his"]["fail_count"] == 0


def test_sina_kline_uses_market_prefixed_symbol():
    pool = fallback_pool.FallbackPool()
    urls = []

    def fake_fetch(url, timeout=10.0, headers=None):
        urls.append(url)
        return True, b'[{"day":"2026-06-01","open":"19.05","close":"18.97","high":"19.27","low":"18.85","volume":"2445990"}]', 0.1

    pool._fetch = fake_fetch

    data = pool.get_kline("sz300059", scale=240, datalen=5)

    assert data and data[0]["date"] == "2026-06-01"
    assert "symbol=sz300059" in urls[0]


def test_scale_to_tencent_period_mapping():
    assert fallback_pool.FallbackPool._tencent_period(240) == "day"
    assert fallback_pool.FallbackPool._tencent_period(60) == "m60"
    assert fallback_pool.FallbackPool._tencent_period(15) == "m15"
