#!/usr/bin/env python3
import json

path = '/Users/huyufeng/Documents/股票分析知识库/runtime/holdings/holdings_latest.json'
with open(path) as f:
    data = json.load(f)

h = data['holdings'][0]
h['current_price'] = 19.83
h['prev_close'] = 20.03  # Tencent 权威昨收
h['pnl_pct'] = -3.56
h['pnl_amt'] = -219.9
h['trailing_pct'] = -6.46
h['chg_pct_today'] = -1.0
h['mid_break_pct'] = 0.15
h['mid_trigger_broken'] = False
h['last_update'] = '2026-08-11T14:56:53+08:00'

data['meta']['updated_at'] = '2026-08-11T14:56:53+08:00'

with open(path, 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print('✅ holdings.json 字段已更新')
