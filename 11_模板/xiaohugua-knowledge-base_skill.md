---
name: xiaohugua-knowledge-base
description: 小胡瓜股票分析知识库 — 所有 cron / 决策 / 飞书推送运行前自动加载知识库 09_认知沉淀 + 10_配置 + runtime/ 最新快照。Use when cron job runs / user asks 复盘/分析/诊断/看 X/持仓/选股.
---

# xiaohugua-knowledge-base Skill

**作用**:把小胡瓜股票分析知识库变成 cron / 决策 / 飞书推送的"前置过滤器"。所有需要市场上下文的操作,先调这个 skill 拉"知识快照",再开始干活。

## 触发条件

1. **Cron 自动**:所有挂载了 `quant-trading-workflow` / `xiaohugua-strategy-router` / `lark-im` / `xiaohugua-system-check` 的 cron
2. **用户触发**:飞书发"分析 X" / "复盘" / "诊断" / "看持仓" / "选股建议"
3. **Agent 主动**:任何决策类 skill 运行前自动 prepend 本 skill 输出

## 知识快照(注入到 prompt 的内容)

调用本 skill 后,自动拼出如下"知识快照"插到 prompt 头部:

```
[小胡瓜知识库快照 v2026-07-31]

📋 认知沉淀(必读,精简版):
- 九条经验:01_策略引擎/九条经验规则.py (100 行,人话版见 09_认知沉淀/九条经验.md)
- 五层确认:01_策略引擎/五层确认框架.py
- 投资边界:❌ 绝不推个股代码/买入建议,只给"状态判断+仓位权重+候选清单"
- 内容黑名单:❌ 美女少妇+单一外形 / explicit pose / 旗袍汉服无场景修饰

⚙️ 配置(动态读):
- KB 根:~/Documents/股票分析知识库
- 持仓动态:KB/runtime/holdings/ (gitignore,实时)
- 选股最新:KB/runtime/picks/ (gitignore,实时)
- 持仓总结 chat:见 10_配置/chat_ids.yaml
- 飞书推 Home:oc_4515237afd69b15b032c7df636d90e58

🕷️ 蜘蛛网 v4.3 当前评分阈值(2026-07-31):
- 推荐阈值:8.0
- 候选阈值:6.0
- 排除阈值:< 6.0

📊 最近优化报告(2026-07-29/30/31):
- KB/07_文档记录/优化报告_20260729.md
- KB/07_文档记录/优化报告_20260730.md
- KB/07_文档记录/优化报告_20260731.md
```

## 实现细节

### 路径解析
- `KB_ROOT` 默认 `~/Documents/股票分析知识库`
- 可被环境变量 `XIAOHUGUA_KB_ROOT` 覆盖
- 子目录一律相对 KB_ROOT,不要硬编码 `~/Desktop/...`

### 数据源读取优先级
1. **最新**:runtime/ 下的当日快照
2. **回退**:07_文档记录/ 下的最近优化报告
3. **兜底**:09_认知沉淀/ 下的人话版

### 不读的内容
- ❌ `~/.hermes/MEMORY.md` 完整版(避免重复)
- ❌ 飞书历史推送(只读"今日")
- ❌ git 历史 / 归档

## 用法示例

### 1. 在 cron job prompt 里挂载
```
技能挂载: [xiaohugua-knowledge-base, quant-trading-workflow]
→ cron 跑前自动 prepend 知识快照到 prompt 头部
```

### 2. 用户问"分析 600276"
- 触发本 skill
- 注入知识快照
- 然后调 `lark-doc` 拉该股最近 N 天舆情 / 调 `akshare` 拉行情
- 输出"状态判断+仓位建议",**绝不推买入**

### 3. cron 跑 9am 量化日报
- 触发本 skill
- 注入知识快照
- 调 `xiaohugua-strategy-router` 判市场状态
- 调 `02_选股系统/小胡瓜_选股workflow.py` 拉今日选股
- 调 `lark-im` 推 Home 群(用 10_配置/chat_ids.yaml 的 home.chat_id)

## 反模式(别这么做)

- ❌ 在 skill 输出里写个股代码
- ❌ 在 skill 输出里写"买入/卖出/止损价"建议
- ❌ 读 `~/.hermes/MEMORY.md` 完整版(token 浪费)
- ❌ 跑本 skill 触发 09_认知沉淀 下 .py 文件(那些是规则引擎,不是给人读的)
- ❌ 飞书推送不带时间戳/状态码

## 维护

- 09_认知沉淀 更新后,本 skill 的"知识快照"模板要同步
- runtime/ 路径变化(本重构后已稳定)不要改这里
- 10_配置 路径变化要同步改本 skill 的"数据源读取优先级"
