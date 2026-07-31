# Findings: planning-with-files 实战 + 18:00 cron 复盘

## 关键发现(知识库召回真值化)

### 发现 1: cron-knowledge-base-recall skill v1.0 真生效(2026-06-25 12:41)
18:00 cron 12:39 dry-run 输出里,LLM 主动引用 3 处历史决策:

1. **Vault 决策日志**:"依据 2026-06-24 决策日志: 昨收 20.18, 浮盈+0.06%, 🔴 健康度"
2. **7am 晨报预警兑现**:"7am 晨报预警兑现: 距 20.50 减仓临界位已突破 ✅"
3. **hermes-memory 硬规则**:"300059 浮亏 -8% 不强平, 持续观察"

→ 这就是 LLM-Wiki 文章说的"久旱逢甘霖"效果,但**通过 cron prompt 强制召回 + Obsidian 三层记忆**实现,不靠用户对话触发。

### 发现 2: pwf 5 templates 是 stdlib only(2026-06-25 17:23)
| Template | 用途 | 大小 |
|---|---|---|
| task_plan.md | 主计划(5 phase + decisions + errors) | 4613 bytes |
| findings.md | 研究发现(本文件) | 3561 bytes |
| progress.md | session log(时序记录) | 4001 bytes |
| analytics_task_plan.md | 数据分析专用 | 3980 bytes |
| analytics_findings.md | 数据分析发现专用 | 3336 bytes |

→ analytics_×2 是 v3 新增,适合量化策略复盘类任务。

### 发现 3: pre_llm_call hook 行为(2026-06-25 17:24)
读了 hooks.py 第 14-23 行,确认逻辑:
```python
def pre_llm_call(**kwargs) -> dict | None:
    project_dir = normalize_cwd()
    if not (project_dir / "task_plan.md").exists():
        return None  # ← 关键:没 plan 文件就不注入
    ...
```
→ **零侵入保证**:日常对话没 task_plan.md,plugin 不做任何事。

## 待验证假设
1. ❓ **pre_llm_call hook 是否真在下一轮对话 prompt 里自动注入**?——**未验**,需要下次 turn 才能确认
2. ❓ **今晚 18:00 真跑** vs **12:39 dry-run** 输出差异——**待验**
3. ❓ **pwf 5 phase 模型** 用在"装 skill + 改 cron + 验证"类任务上是否真提速——**待验**

## 数据来源
- hermes-safe-modify skill v1.13.0(7 步 SOP)
- cron-knowledge-base-recall skill v1.0(本任务产出)
- memory-lifecycle-policy skill v1.0(三层记忆定位)
- 18:00 cron 输出:`~/.hermes/cron/output/6bed57e2b8ba/2026-06-25_12-41-57.md`