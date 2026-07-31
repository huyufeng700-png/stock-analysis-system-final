# 股票分析知识库 — 统一入口
# 用法:make help
# 作者:小胡瓜 / 重构 2026-07-31

KB_ROOT := $(shell pwd)
KB_RUNTIME := $(KB_ROOT)/runtime
KB_LOGS := $(KB_RUNTIME)/logs
NOW := $(shell date +%Y%m%d_%H%M%S)

.PHONY: help lint run-7am run-9am run-10h run-1455 run-15h run-1505 run-16h run-18h run-2am clean status install

help:
	@echo "========================================"
	@echo "  股票分析系统 — Makefile 入口"
	@echo "========================================"
	@echo ""
	@echo "  make status          系统状态总览"
	@echo "  make lint            静态检查(09_认知沉淀/01_策略引擎)"
	@echo ""
	@echo "  [按时间] 跑对应 cron 的等价命令(非 cron)"
	@echo "  make run-7am         7:00  A股晨报"
	@echo "  make run-9am         9:00  小胡瓜量化日报"
	@echo "  make run-10h         10:00 盘中持仓"
	@echo "  make run-1455        14:55 东方财富收盘分析"
	@echo "  make run-15h         15:00 蜘蛛网选股"
	@echo "  make run-1505        15:05 收盘复盘"
	@echo "  make run-16h         16:00 准确率验证"
	@echo "  make run-18h         18:00 持仓总结"
	@echo "  make run-2am         02:00 凌晨优化"
	@echo ""
	@echo "  [运维]"
	@echo "  make install         安装依赖(akshare / openai / rich)"
	@echo "  make clean           清 runtime/cache + 旧 logs"
	@echo "  make verify          验证所有路径 / 脚本可执行"
	@echo ""

status:
	@echo "==== 知识库根 ===="
	@echo "$(KB_ROOT)"
	@du -sh $(KB_ROOT) 2>/dev/null
	@echo ""
	@echo "==== 运行时数据 ===="
	@du -sh $(KB_RUNTIME)/* 2>/dev/null
	@echo ""
	@echo "==== 脚本可执行性 ===="
	@find $(KB_ROOT) -name "*.py" -not -path "*/__pycache__/*" -exec python3 -c "import ast,sys;ast.parse(open(sys.argv[1]).read())" {} \; 2>&1 | head -3
	@echo "(空白=全部语法 OK)"

lint:
	@python3 -c "import ast,glob,sys" \
	  "for f in glob.glob('09_认知沉淀/*.py') + glob.glob('01_策略引擎/*.py')" \
	  "    try: ast.parse(open(f).read())" \
	  "    except Exception as e: print(f, e); sys.exit(1)" \
	  "else: print('all OK')"

run-7am:
	@cd $(KB_ROOT) && python3 scripts/cron_7am_premarket_fetch.py 2>&1 | tee $(KB_LOGS)/manual_7am_$(NOW).log

run-9am:
	@cd $(KB_ROOT) && python3 02_选股系统/小胡瓜_选股workflow.py 2>&1 | tee $(KB_LOGS)/manual_9am_$(NOW).log

run-10h:
	@cd $(KB_ROOT) && python3 03_持仓管理/小胡瓜_持仓管理.py 2>&1 | tee $(KB_LOGS)/manual_10h_$(NOW).log

run-1455:
	@cd $(KB_ROOT) && python3 02_选股系统/小胡瓜_选股workflow.py 2>&1 | tee $(KB_LOGS)/manual_1455_$(NOW).log

run-15h:
	@cd $(KB_ROOT) && python3 02_选股系统/蜘蛛网4.3_量化选股.py 2>&1 | tee $(KB_LOGS)/manual_15h_$(NOW).log

run-1505:
	@cd $(KB_ROOT) && python3 06_cron任务/market_review.py 2>&1 | tee $(KB_LOGS)/manual_1505_$(NOW).log

run-16h:
	@cd $(KB_ROOT) && python3 06_cron任务/accuracy_tracker.py 2>&1 | tee $(KB_LOGS)/manual_16h_$(NOW).log

run-18h:
	@cd $(KB_ROOT) && python3 06_cron任务/cron_18h_holdings_summary.py 2>&1 | tee $(KB_LOGS)/manual_18h_$(NOW).log

run-2am:
	@echo "(凌晨优化由 cron d9b3bcf63b6f 自动跑)"

install:
	@pip3 install -q akshare openai rich pyyaml 2>&1 | tail -3

clean:
	@echo "==== 清 runtime/cache(慎用) ===="
	@rm -rf $(KB_RUNTIME)/cache/* 2>/dev/null
	@echo "==== 删 30 天前的 logs ===="
	@find $(KB_LOGS) -name "*.log" -mtime +30 -delete 2>/dev/null
	@echo "done"

verify:
	@bash -c 'for f in 02_选股系统/蜘蛛网4.3_量化选股.py 02_选股系统/小胡瓜_选股workflow.py 03_持仓管理/小胡瓜_持仓管理.py 04_数据采集/news_sentiment_monitor.py 06_cron任务/cron_18h_holdings_summary.py 06_cron任务/cron_7am_premarket_fetch.py 06_cron任务/market_review.py 06_cron任务/accuracy_tracker.py scripts/stock_monitor.py scripts/stock_heartbeat.py scripts/alert_monitor.py scripts/daily_diagnostic.py 01_策略引擎/九条经验规则.py 01_策略引擎/五层确认框架.py; do test -f "$(KB_ROOT)/$$f" && echo "✅ $$f" || echo "❌ MISSING: $$f"; done'
