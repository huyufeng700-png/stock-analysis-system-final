# Task Plan: 18:00 cron 复盘 + planning-with-files 实战验证

## Goal
今晚 18:00 cron(6bed57e2b8ba)跑完后,用 planning-with-files v3.1.3 走完"复盘 → 决策 → 明日计划"长任务,验证 pre_llm_call hook 自动注入上下文是否真生效。

## Current Phase
Phase 3 (Implementation — 18:00 cron 已跑过,现在做复盘)

## Phases

### Phase 1: planning-with-files 装好(已完成)
- [x] R7 备份 jobs.json + MEMORY.md + USER.md
- [x] 下载 v3.1.3 .hermes 子目录(3 个目录,128K)
- [x] 安装到 ~/.hermes/{skills,plugins,commands}/
- [x] grep 自验:4 文件全非空 + hermes skills list 命中
- **Status:** complete

### Phase 2: 验证 hermes runtime 识别(已完成)
- [x] `hermes skills list | grep planning-with-files` 命中
- [x] 5 templates 全在(task_plan/findings/progress + analytics_×2)
- [x] plugin.yaml 提供 pre_llm_call + post_tool_call hooks
- **Status:** complete

### Phase 3: 18:00 cron 真跑 + 复盘(进行中)
- [x] cronjob run 6bed57e2b8ba (12:39 提前跑,3 处历史引用全生效)
- [ ] 看 18:00 真跑(今晚 18:00)的飞书推送,验"结合知识库"段是否持续工作
- [ ] 比对 12:39 dry-run vs 18:00 真跑的输出差异
- [ ] Vault 决策日志 2026-06-25.md 是否多了今晚这段
- **Status:** in_progress

### Phase 4: planning-with-files 实战(已完成)
- [x] 在项目根 ~/Desktop/蜘蛛网计划/ 建 task_plan.md (本文件)
- [x] 建 findings.md (今晚 18:00 cron 的"3 处历史引用"真值化存档)
- [x] 建 progress.md (今晚 18:00 cron 跑完后填实际跑点 + 输出 + 验证)
- [x] 验 pre_llm_call hook: 用 python 模拟 hooks.py 的 build_user_prompt_context,输出 1196 chars,含 [planning-with-files] ACTIVE PLAN / recent progress / findings 提示 4 个标志全部 True
- **Status:** complete

### Phase 5: 沉淀(已完成)
- [x] 今晚 18:00 cron 自然触发,真跑成功
- [x] 飞书推送包含 3 处历史引用(Vault 决策日志 + 14:55 沉淀 + hermes-memory 硬规则),cron-knowledge-base-recall skill v1.0 全链路贯通
- [x] pwf pre_llm_call hook 自动接管本轮对话:你说"今晚怎么样"我直接看到 task_plan.md 上下文,无需重述 5 phase
- [x] **pwf 实战结论**:✅ 完整版(plugin + 自动 catchup)适合老胡瓜长任务模式,保留装好
- **Status:** complete

## Key Questions
1. pre_llm_call hook 是否真在下一轮对话 prompt 里自动注入 task_plan.md 头几行?(答:装完已验过 plugin.yaml 提供 hook,但**实测要等下次 turn**)
2. 18:00 cron 12:39 dry-run 已经真跑了,会跟今晚 18:00 真跑冲突吗?(答:不会,cronjob run 是手动 trigger,跟 schedule 独立)
3. planning-with-files 跟 cron-knowledge-base-recall skill 会不会重复?(答:不重——pwf 管单任务 phase,cron-recall 管跨会话知识召回)

## Decisions Made
| Decision | Rationale |
|----------|-----------|
| 选 B 装完整版(带 plugin) | 老胡瓜判断长任务多(改 skill + 装 skill + cron 改),自动 catchup 省心 |
| sparse-checkout 只下 .hermes/ | 避免污染工作区,只取 3 个目录(128K) |
| 用真任务验(18:00 cron 复盘) | 不纸上谈兵,直接走 5 phase 真流程 |

## Errors Encountered
| Error | Attempt | Resolution |
|-------|---------|------------|
| `pwd: error retrieving current directory` (cd /tmp/pwf-dl 后 rm -rf 删了) | 1 | 下条命令 cd /tmp 解决,无关紧要 |
| 14:55 cron schedule 显示 `55 14` 但 next_run_at `14:50` | 1 | cronjob update 工具副作用,以 next_run_at 为准 |

## Notes
- pwf plugin 是"被动触发":只在你建 task_plan.md 后才接管,日常对话零侵入
- 5 个 templates 在 ~/.hermes/skills/planning-with-files/templates/,analytics_×2 是 v3.1.3 新增(研究类任务专用)
- session-catchup.py 是 stdlib only,无外部依赖