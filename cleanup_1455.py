#!/usr/bin/env python3
import json

# 清理 holdings.json
path = '/Users/huyufeng/Documents/股票分析知识库/runtime/holdings/holdings_latest.json'
with open(path) as f:
    data = json.load(f)

history = data.get('meta', {}).get('history', [])
new_history = [h for h in history if '14:55:37' not in h.get('at', '')]
data['meta']['history'] = new_history

with open(path, 'w') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f'holdings.json 清理完成: {len(history)} -> {len(new_history)}')

# 清理 Vault
vault_path = '/Users/huyufeng/Documents/股票分析知识库/runtime/logs/vault/2026-08-11.md'
vault_content = '''# 决策日志 2026-08-11

## 14:55 收盘决策 (14:56)
14:55 决策: 🟡 观望 (震荡)
router: ⚪ 弱震荡 (-20分)
东方财富(300059): 现价 19.83 今日 -1.00% 浮盈 -3.56% 触发位 23.50/19.80/18.91
触发: c) 🔴 子信号

'''
with open(vault_path, 'w') as f:
    f.write(vault_content)
print('Vault 清理完成')
