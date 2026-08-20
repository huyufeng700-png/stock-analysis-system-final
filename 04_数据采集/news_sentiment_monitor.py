#!/usr/bin/env python3
"""
蜘蛛网计划 v1.0 - 财经新闻爬虫 + AI舆情分析
功能：自动抓取新浪财经头条，AI分析情绪，输出交易参考
"""

import requests
from bs4 import BeautifulSoup
import json
import os
from datetime import datetime
from openai import OpenAI

# LLM 配置 (2026-08-08 P1+2 fix): MiniMax-M3 优先, DeepSeek 兜底
# token plan 套餐 key (来自 ~/.hermes/.env), 撞 429 自动切 DeepSeek
def _load_llm_config() -> dict:
    """按优先级 MiniMax → DeepSeek → 空; 读 ~/.hermes/.env"""
    from pathlib import Path
    env_path = Path(os.path.expanduser("~/.hermes/.env"))
    env_vars = dict(os.environ)
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env_vars[k.strip()] = v.strip().strip('"').strip("'")

    # MiniMax 优先 (2026-08-09 fix: 用户 .env 实际配置是 MINIMAX_CN_API_KEY / MINIMAX_CN_BASE_URL)
    m_key = (
        env_vars.get("MINIMAX_API_KEY")
        or env_vars.get("minimax_api_key")
        or env_vars.get("MINIMAX_CN_API_KEY")  # 国内版 key (用户真实配置)
    )
    m_url = (
        env_vars.get("MINIMAX_BASE_URL")
        or env_vars.get("MINIMAX_CN_BASE_URL")
        or "https://api.minimaxi.com/v1"
    )
    m_model = env_vars.get("MINIMAX_MODEL") or "MiniMax-M3"
    if m_key:
        return {"api_key": m_key, "base_url": m_url, "model": m_model, "provider": "MiniMax"}

    # DeepSeek 兜底
    d_key = env_vars.get("DEEPSEEK_API_KEY")
    d_url = env_vars.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if d_key:
        return {"api_key": d_key, "base_url": d_url, "model": "deepseek-chat", "provider": "deepseek"}

    # 双空: 让调用方感知
    return {"api_key": "", "base_url": m_url, "model": m_model, "provider": "MiniMax (无 key)"}

LLM = _load_llm_config()
DEEPSEEK_API_KEY = LLM["api_key"]  # 兼容旧变量名 (语义上现在泛指当前 LLM key)
BASE_URL = LLM["base_url"]
MODEL_NAME = LLM["model"]

def fetch_sina_finance_news():
    """抓取新浪财经头条新闻"""
    print("🕷️ 正在抓取新浪财经头条...")
    
    url = "https://finance.sina.com.cn/"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.encoding = 'utf-8'
        
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 提取头条新闻
        news_list = []
        
        # 查找所有新闻链接
        for a_tag in soup.find_all('a', href=True):
            title = a_tag.get_text(strip=True)
            href = a_tag['href']
            
            # 过滤：只保留财经相关，标题长度适中
            if len(title) > 8 and len(title) < 50 and 'finance' in href:
                if not any(n['title'] == title for n in news_list):
                    news_list.append({
                        'title': title,
                        'url': href,
                        'source': '新浪财经',
                        'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    })
        
        # 去重并限制数量
        unique_news = []
        seen_titles = set()
        for news in news_list:
            if news['title'] not in seen_titles:
                seen_titles.add(news['title'])
                unique_news.append(news)
                
        print(f"✅ 成功抓取 {len(unique_news)} 条新闻")
        return unique_news[:20]  # 返回前20条
        
    except Exception as e:
        print(f"❌ 抓取失败: {e}")
        return []

def analyze_sentiment(news_list):
    """用DeepSeek批量分析新闻舆情"""
    print("\n🤖 正在进行AI舆情分析...")
    
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=BASE_URL)
    
    results = []
    good_news = []
    bad_news = []
    neutral_news = []
    
    for i, news in enumerate(news_list[:10]):  # 先分析前10条
        print(f"   分析中 ({i+1}/{len(news_list[:10])}): {news['title'][:20]}...")
        
        try:
            prompt = f"""
            请分析以下财经新闻对A股市场的影响：
            
            新闻标题：{news['title']}
            
            请严格按以下JSON格式输出：
            {{
                "sentiment": "利好" or "利空" or "中性",
                "score": 0-10（10=极大利好，0=极大利空，5=中性）,
                "affected_sectors": ["影响的板块，如：银行、地产、AI、新能源、消费等"],
                "market_impact": "对大盘整体影响：正面/负面/中性",
                "trading_suggestion": "一句话交易建议"
            }}
            """
            
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500
            )
            # 2026-08-08 P2 fix: MiniMax-M3 (同 DeepSeek) 返回带 <think>...</think> 推理段
            # + ```json ... ``` markdown fence + 可能 JSON 不完整 (token 截断)
            # 取消 response_format=json_object (MiniMax 不严格遵守), 用 regex 抽 JSON
            content = response.choices[0].message.content or ""
            # strip <think>...</think>
            if "<think>" in content:
                end = content.find("</think>")
                if end != -1:
                    content = content[end + len("</think>"):].strip()
            # 找最大 {} 块
            import re
            m = re.search(r'\{[\s\S]*\}', content)
            if not m:
                raise json.JSONDecodeError(f"No JSON in response: {content[:200]}", content, 0)
            analysis = json.loads(m.group(0))
            news.update(analysis)
            results.append(news)
            
            # 分类
            if analysis['sentiment'] == '利好':
                good_news.append(news)
            elif analysis['sentiment'] == '利空':
                bad_news.append(news)
            else:
                neutral_news.append(news)
                
        except Exception as e:
            print(f"   ⚠️ 分析失败: {e}")
            news['sentiment'] = '中性'
            news['score'] = 5
            results.append(news)
    
    return results, good_news, bad_news, neutral_news

def generate_daily_report(results, good_news, bad_news, neutral_news):
    """生成每日舆情报告"""
    print("\n" + "="*80)
    print("📰 小胡瓜AI量化 - 每日舆情早报")
    print("="*80)
    print(f"🕐 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"📊 共分析新闻: {len(results)} 条")
    print(f"👍 利好: {len(good_news)} 条 | 👎 利空: {len(bad_news)} 条 | ➖ 中性: {len(neutral_news)} 条")
    
    # 计算市场情绪指数
    total_score = sum(r.get('score', 5) for r in results)
    sentiment_index = total_score / len(results) if results else 5
    print(f"📈 市场情绪指数: {sentiment_index:.1f}/10")
    
    if sentiment_index >= 7:
        mood = "😊 情绪偏乐观，可适度看多"
    elif sentiment_index <= 3:
        mood = "😰 情绪偏悲观，注意控制风险"
    else:
        mood = "😐 情绪中性，震荡市思路"
    print(f"🎯 情绪判断: {mood}")
    
    print("\n" + "-"*80)
    print("🔥 重点利好新闻（可能存在交易机会）")
    print("-"*80)
    for news in sorted(good_news, key=lambda x: x['score'], reverse=True)[:3]:
        print(f"\n📌 {news['title']}")
        print(f"   情绪评分: {news['score']}/10 | 影响板块: {', '.join(news.get('affected_sectors', ['未知']))}")
        print(f"   💡 建议: {news.get('trading_suggestion', '观察为主')}")
    
    if bad_news:
        print("\n" + "-"*80)
        print("⚠️ 重点利空新闻（注意规避风险）")
        print("-"*80)
        for news in sorted(bad_news, key=lambda x: x['score'])[:3]:
            print(f"\n❌ {news['title']}")
            print(f"   情绪评分: {news['score']}/10 | 影响板块: {', '.join(news.get('affected_sectors', ['未知']))}")
            print(f"   💡 建议: {news.get('trading_suggestion', '注意规避')}")
    
    # 板块热度统计
    print("\n" + "-"*80)
    print("🏭 板块热度排名")
    print("-"*80)
    sector_count = {}
    for r in results:
        for sector in r.get('affected_sectors', []):
            sector_count[sector] = sector_count.get(sector, 0) + 1
    
    for sector, count in sorted(sector_count.items(), key=lambda x: x[1], reverse=True)[:5]:
        print(f"   {sector}: {'🔥' * min(count, 5)}")
    
    print("\n" + "="*80)
    print("💡 今日交易策略建议")
    print("="*80)
    
    if sentiment_index >= 7:
        print("""
        ✅ 整体偏多，可保持60-70%仓位
        ✅ 重点关注利好提及的板块
        ✅ 回调即是买入机会
        """)
    elif sentiment_index <= 3:
        print("""
        ⚠️ 情绪偏空，建议仓位控制在30%以内
        ⚠️ 规避利空板块，持有现金为主
        ⚠️ 等待更明确的信号
        """)
    else:
        print("""
        🔄 震荡市，高抛低吸，仓位50%左右
        🔄 精选个股，避免追高
        🔄 板块轮动操作
        """)
    
    print("="*80)
    print("⚠️ 免责声明：AI分析仅供参考，不构成投资建议")
    print("="*80)
    
    # 保存报告
    report_data = {
        'generate_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'sentiment_index': sentiment_index,
        'news_count': len(results),
        'good_news': good_news,
        'bad_news': bad_news,
        'all_news': results
    }
    
    with open(f"/Users/huyufeng/Documents/股票分析知识库/runtime/sentiment/舆情报告_{datetime.now().strftime('%Y%m%d')}.json", 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)
    
    return report_data

def main():
    print("="*80)
    print("🕷️ 蜘蛛网计划 - AI舆情监控系统 v1.0")
    print("="*80)
    
    # 1. 抓取新闻
    news_list = fetch_sina_finance_news()
    
    if not news_list:
        print("❌ 没有抓到任何新闻，退出")
        return
    
    # 2. 舆情分析
    results, good_news, bad_news, neutral_news = analyze_sentiment(news_list)
    
    # 3. 生成报告
    report = generate_daily_report(results, good_news, bad_news, neutral_news)
    
    print(f"\n✅ 报告已保存: /Users/huyufeng/Documents/股票分析知识库/runtime/sentiment/舆情报告_{datetime.now().strftime('%Y%m%d')}.json")
    print("\n🎯 爬虫 + 舆情系统 运行完成！")
    print("💡 下一步：接入策略系统，舆情信号与技术信号共振")

if __name__ == "__main__":
    main()
