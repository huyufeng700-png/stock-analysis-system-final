# Cron 任务模板 — 新增 cron job 时复制此模板

## 1. 元信息
- **job_id**:`your-job-name` (kebab-case,英文)
- **name**:📊 你的 cron 名
- **schedule**:`分 时 日 月 周` cron 表达式
- **重复**:`forever` (永远) / `count` (有限次)
- **deliver**:`local` (本地存档) / `feishu:<chat_id>` (推飞书) / `telegram:<chat_id>`

## 2. 字段设计
- **workdir**:`~/Documents/股票分析知识库` (统一基准)
- **script**(脚本模式,无 agent):
  - 主脚本:`06_cron任务/your_script.py`
  - 周边:`scripts/your_monitor.py`
- **prompt**(LLM 模式,带 agent):
  - 参考 `11_模板/优化报告_template.md` 风格
  - 必含路径:`cd ~/Documents/股票分析知识库` (或 workdir 已设)
  - 必含硬规则:不硬编码持仓 / 持仓从 `runtime/holdings/` 读

## 3. Skills 挂载
- `quant-trading-workflow`:大多数主力 cron 必挂
- `xiaohugua-strategy-router`:14:55 / 15:00 / 18:00 必挂
- `xiaohugua-system-check`:On-call 健康度专属
- `lark-im`:9am 量化日报(必发飞书)
- `lark-calendar`:7am 晨报(配 lark-calendar 拉日程)

## 4. 创建命令(hermes-tools)
```python
cronjob(
  action="create",
  name="📊 你的 cron 名",
  schedule="分 时 日 月 周",
  prompt="[必含:路径 + 硬规则 + 数据源 + 输出格式]",
  workdir="~/Documents/股票分析知识库",
  skills=["quant-trading-workflow", ...],
  deliver="local"
)
```

## 5. 创建后必做
- [ ] 同步更新 `10_配置/cron_schedule.yaml`
- [ ] dry-run 1 次 `cronjob action=run`
- [ ] 看 stdout/stderr,无 error 才 enable
- [ ] 1 周观察后,标 stable

## 6. 暂停/恢复
- 暂停:`cronjob action=pause job_id=...`
- 恢复:`cronjob action=resume job_id=...`
- 删:`cronjob action=remove job_id=...` (慎用,先 pause 一周确认)

## 7. 反模式(别这么做)
- ❌ workdir 写 `~/Desktop/...` (已踩坑,会随桌面清理丢)
- ❌ prompt 里写死 `cd ~/Desktop/...`
- ❌ script 路径不带 `06_cron任务/` 前缀
- ❌ deliver 写 `origin` 不写具体 chat_id(测试 OK,生产别用)
- ❌ 18:00 ±30min 改 schedule(撞持仓总结 cron 窗)
