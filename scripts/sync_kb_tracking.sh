#!/bin/bash
# 每日同步: 股票池追踪表 → Hu gua harness 知识库 + git 推送
# 由蜘蛛网选股流程末尾调用 (Hermes 进程, 可读 ~/Documents)
SRC="/Users/huyufeng/Documents/股票分析知识库/runtime/tracking/universe_tracking.md"
DST="/Users/huyufeng/Obsidian/Hu gua harness/自动化/股票系统新老池追踪.md"
VAULT="/Users/huyufeng/Obsidian/Hu gua harness"
LOG="/tmp/sync_kb_tracking.log"

exec >> "$LOG" 2>&1
echo "=== sync $(date '+%F %T') ==="

[ -f "$SRC" ] || { echo "源文件不存在: $SRC"; exit 0; }

# 提取表格 (从表头行起, 丢弃源文件的标题/说明)
TABLE=$(awk '/^\| 运行时间/{found=1} found' "$SRC")
if [ -z "$TABLE" ]; then echo "表格为空, 跳过"; exit 0; fi

{
  echo '---'
  echo 'tags:'
  echo '  - 自动化'
  echo '  - 股票分析'
  echo '  - 胡小瓜'
  echo 'created: 2026-08-16'
  echo '---'
  echo
  echo '# 新老股票池追踪对比 (2026-08-16 起)'
  echo
  echo "> 自动同步快照 $(date '+%Y-%m-%d %H:%M')。最新数据在 \`~/Documents/股票分析知识库/runtime/tracking/universe_tracking.md\`（蜘蛛网选股后自动追加）。"
  echo '> 背景: 2026-08-16 股票池扩充 111→230 只，熊市参数放宽（score≥4.5/RR≥1.3）。'
  echo
  echo "$TABLE"
} > "$DST"

cd "$VAULT"
git add -A
if git diff --cached --quiet; then
  echo "无变更, 跳过提交"
else
  git -c user.email="hugua700@sixyao.com" -c user.name="胡二瓜" \
      commit -m "每日同步股票池追踪表 $(date '+%Y-%m-%d %H:%M')" -q
  git push origin main 2>&1 | tail -1
fi
echo "=== done ==="
