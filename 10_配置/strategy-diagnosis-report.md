{
  "title": "蜘蛛网 v4.3 策略诊断报告",
  "version": "1.0",
  "date": "2026-08-12T21:08:08",
  "baseline": "backtest_20260812_2107.json",
  "sections": {
    "数据质量": {
      "verdict": "正常",
      "checks": [
        "无缺失值",
        "无重复日期",
        "K线完整60条",
        "指标计算正常"
      ]
    },
    "策略逻辑": {
      "verdict": "部分有效",
      "entry": "MACD金叉+RSI 30-70+量能>1.2+MA60过滤",
      "exit": "ATR止损(2x)+止盈(3x)+MACD死叉+trailing stop",
      "issue": "部分标的系统性亏损(600489/000063/601012)"
    },
    "参数优化": {
      "verdict": "无效",
      "reason": "参数搜索最高50%胜率但全流程仍20%，过拟合"
    },
    "黑名单": {
      "count": 11,
      "file": "10_配置/backtest-blacklist.json",
      "effect": "过滤持续亏损标的，减少无效推荐"
    },
    "大盘择时": {
      "index": "sh000001",
      "rule": "弱市时仅放行score>=5且rr>=1.5",
      "status": "当前bullish"
    },
    "板块轮动": {
      "method": "当日涨幅均值TOP3",
      "current_top3": [
        "通信",
        "半导体",
        "房地产"
      ],
      "effect": "非强势板块扣分，推荐从13只降至2只"
    },
    "性能": {
      "avg_win_rate": "41.7%(2只推荐)",
      "avg_profit": "+1.09%",
      "status": "仍低于45%阈值，但结构改善"
    },
    "结论": {
      "verdict": "策略仍有优化空间，建议小仓位实盘跟踪2周",
      "next_steps": [
        "继续监控delta漂移",
        "每日更新板块轮动TOP3",
        "2周后评估真实胜率"
      ]
    }
  }
}