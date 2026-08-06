#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
决策仪表盘 - 生成今日分析摘要仪表盘并推送到飞书
灵感来源: ZhuLinsen/daily_stock_analysis 的决策仪表盘
"""

import json
import os
import subprocess
import glob
from datetime import datetime, timedelta

def beijing_time():
    return datetime.utcnow() + timedelta(hours=8)

def load_json(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def get_latest_file(pattern):
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)

def send_to_feishu(report_text, chat_id=None):
    """发送报告到飞书群，使用 lark-cli"""
    if chat_id is None:
        # 默认使用硬编码的聊天ID（可从环境变量或配置读取，这里先用空，调用方自行传入）
        chat_id = os.environ.get("FEISHU_CHAT_ID", "oc_ef684ee04be46f9c15054770be144b82")  # 示例 ID，实际请替换或设置 env
    try:
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
                return True
            else:
                print(f"⚠️ 飞书推送返回错误: {resp.get('msg', 'unknown')}")
                return False
        else:
            print(f"⚠️ 飞书推送失败: {result.stderr[:200]}")
            return False
    except Exception as e:
        print(f"⚠️ 发送异常: {e}")
        return False

def main():
    now = beijing_time()
    today_str = now.strftime('%Y-%m-%d')
    
    # 读取持仓数据
    holdings_path = 'runtime/holdings/holdings_latest.json'
    holdings = load_json(holdings_path)
    holding_info = holdings.get('holdings', [{}])[0] if holdings.get('holdings') else {}
    
    # 读取最新回测数据
    bt_pattern = 'runtime/picks/backtest/archive/backtest_*.json'
    latest_bt_file = get_latest_file(bt_pattern)
    backtest = load_json(latest_bt_file) if latest_bt_file else {}
    
    # 读取最新舆情数据
    sentiment_pattern = 'runtime/sentiment/舆情报告_*.json'
    latest_sentiment_file = get_latest_file(sentiment_pattern)
    sentiment = load_json(latest_sentiment_file) if latest_sentiment_file else {}
    
    # 读取最新研究档案摘要（如果有）
    research_pattern = 'runtime/logs/research_archive/*_summary.json'
    latest_research_file = get_latest_file(research_pattern)
    research = load_json(latest_research_file) if latest_research_file else {}
    
    # 生成仪表盘
    lines = []
    lines.append('# 股票分析系统 决策仪表盘')
    lines.append(f'**生成时间**: {now.strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append('')
    
    # 持仓部分
    lines.append('## 📊 持仓情况')
    if holding_info:
        code = holding_info.get('code', 'N/A')
        name = holding_info.get('name', 'N/A')
        shares = holding_info.get('shares', 0)
        cost = holding_info.get('cost', 0)
        current_price = holding_info.get('current_price', holding_info.get('price', 0))
        pnl_amt = holding_info.get('pnl_amt', 0)
        pnl_pct = holding_info.get('pnl_pct', 0)
        market_value = shares * current_price if isinstance(shares, (int, float)) and isinstance(current_price, (int, float)) else 0
        lines.append(f'- **股票**: {name} ({code})')
        lines.append(f'- **持仓**: {shares} 股')
        lines.append(f'- **成本价**: ¥{cost:.3f}')
        lines.append(f'- **现价**: ¥{current_price:.2f}')
        lines.append(f'- **今日盈亏**: ¥{pnl_amt:.2f} ({pnl_pct:+.2f}%)')
        lines.append(f'- **持仓市值**: ¥{market_value:.2f}')
        lines.append(f'- **持仓天数**: {holding_info.get("holding_days", "N/A")}')
        triggers = holding_info.get('triggers', {})
        if triggers:
            lines.append('- **触发位**:')
            for k, v in triggers.items():
                if isinstance(v, dict):
                    price = v.get('price', 0)
                    typ = v.get('type', '')
                    lines.append(f'  - {k}: ¥{price:.2f} ({typ})')
                else:
                    lines.append(f'  - {k}: {v}')
        health = holding_info.get('health_5dim_risk', 'N/A')
        lines.append(f'- **健康度**: {health}')
    else:
        lines.append('暂无持仓数据')
    lines.append('')
    
    # 回测部分
    lines.append('## 📈 回测概览（最新 90 天）')
    if backtest and backtest.get('results'):
        bt_time = backtest.get('time', 'N/A')
        if bt_time != 'N/A':
            try:
                bt_str = bt_time.replace('Z', '+00:00')
                bt_dt = datetime.fromisoformat(bt_str)
                bt_dt = bt_dt + timedelta(hours=8)
                bt_time_str = bt_dt.strftime('%Y-%m-%d %H:%M:%S')
            except Exception:
                bt_time_str = bt_time
            lines.append(f'- **回测时间**: {bt_time_str}')
        else:
            lines.append(f'- **回测时间**: {bt_time}')
        lines.append(f'- **回测天数**: {backtest.get("span_days", "N/A")}')
        results = backtest.get('results', {})
        if results:
            lines.append(f'- **回测股票数**: {len(results)}')
            sorted_items = sorted(results.items(), key=lambda x: x[1].get('avg_profit', 0))
            worst = sorted_items[:3]
            best = sorted_items[-3:] if len(sorted_items) > 3 else []
            lines.append('- **表现最差的 3 只**:')
            for code, stats in worst:
                lines.append(f'  - {code}: 交易{stats.get("trades",0)}笔, 胜率{stats.get("win_rate",0)}%, 平均盈亏{stats.get("avg_profit",0):+.2f}%')
            if best:
                lines.append('- **表现最好的 3 只**:')
                for code, stats in best:
                    lines.append(f'  - {code}: 交易{stats.get("trades",0)}笔, 胜率{stats.get("win_rate",0)}%, 平均盈亏{stats.get("avg_profit",0):+.2f}%')
        p0_filtered = backtest.get('p0_filtered_count', 0)
        p0_dropped = backtest.get('p0_dropped_count', 0)
        lines.append(f'- **P0 过滤**: {p0_filtered} 只 (胜率=0%)')
        if backtest.get('p0_dropped'):
            lines.append(f'- **P0 剔除票**: {", ".join(backtest["p0_dropped"])}')
    else:
        lines.append('暂无回测数据')
    lines.append('')
    
    # 舆情部分
    lines.append('## 📰 舆情简况')
    if sentiment:
        if '市场情绪' in sentiment:
            lines.append(f'- **市场情绪**: {sentiment.get("市场情绪")}')
        if '热点板块' in sentiment:
            lines.append(f'- **热点板块**: {", ".join(sentiment.get("热点板块", []))}')
        if '主要观点' in sentiment:
            txt = sentiment.get("主要观点", "")
            if len(txt) > 100:
                txt = txt[:100] + "..."
            lines.append(f'- **主要观点**: {txt}')
    else:
        lines.append('暂无舆情数据')
    lines.append('')
    
    # 研究档案部分
    lines.append('## 🔬 研究档案更新')
    if research and research.get('code'):
        lines.append(f'- **最新研究股票**: {research.get("code")} {research.get("metadata", {}).get("name", "")}')
        lines.append(f'- **研究时间**: {research.get("metadata", {}).get("date", "N/A")}')
        lines.append(f'- **分析结论**: {research.get("analysis", {}).get("signal", "N/A")} {research.get("analysis", {}).get("note", "")}')
    else:
        lines.append('今日无新研究档案')
    lines.append('')
    
    # 风险提示
    lines.append('## ⚠️ 风险提示')
    lines.append('- 本仪表盘仅基于历史数据和模型分析生成，不构成投资建议。')
    lines.append('- 请结合自身风险 tolerance 进行独立判断。')
    lines.append('- 市场有风险，入市需谨慎。')
    lines.append('')
    lines.append('---')
    lines.append(f'*自动生成于 {now.strftime("%Y-%m-%d %H:%M:%S")}*')
    
    report_text = '\n'.join(lines)
    
    # 写入文件
    output_dir = 'runtime/reports'
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f'decision_dashboard_{today_str}.md')
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    latest_path = os.path.join(output_dir, 'decision_dashboard_latest.md')
    with open(latest_path, 'w', encoding='utf-8') as f:
        f.write(report_text)
    
    print(f'决策仪表盘已生成: {output_path}')
    print(f'最新版本: {latest_path}')
    
    # 发送到飞书
    try:
        sent = send_to_feishu(report_text)
        if sent:
            print("飞书推送已完成")
        else:
            print("飞书推送失败，请检查 lark-cli 配置或网络")
    except Exception as e:
        print(f"发送飞书时出错: {e}")
    
if __name__ == '__main__':
    main()
