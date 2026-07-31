#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
研究档案.py - A股个股研究档案 + RAG 检索 (重构版)
==========================================

[重构 2026-07-31] 
原文件已丢失,飞书/kanban/桌面 全部 0 命中。
基于 workflow.py 实际用法(line 386-393)重写最小可用版本:

用法 1 (workflow 调用):
    archive = ResearchArchive()
    workflow = ResilientWorkflow(archive)
    workflow.run_analysis(
        stock_code='300059',
        price_data={'price': 20.30, 'change_pct': +1.55, ...},
        metadata={'source': '选股workflow', 'date': '2026-07-31'}
    )

数据落点: runtime/logs/research_archive/
  - {code}_summary.json     (个股研究摘要)
  - {code}_rag_index.json   (RAG 索引)
  - meta.json               (全档案元信息)

P7 计划:如需更复杂 RAG/embedding,从以下 3 路找
  1. 老胡瓜 Obsidian Vault ~/ObsidianVault/小胡瓜/研究档案/
  2. 飞书 DM 历史(老胡瓜跟小胡瓜的研报对话)
  3. Win 笔记本 192.168.1.2 (Time Machine 找)
"""

import os
import json
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Any

# 北京时间
CST = timezone(timedelta(hours=8))

# 数据落点:相对 KB 根
def _archive_dir() -> str:
    """返回研究档案数据目录,自动建"""
    d = os.path.expanduser("~/Documents/股票分析知识库/runtime/logs/research_archive")
    os.makedirs(d, exist_ok=True)
    return d


def _now_iso() -> str:
    return datetime.now(CST).strftime('%Y-%m-%dT%H:%M:%S+08:00')


class ResearchArchive:
    """个股研究档案 — 存储/读取个股基本面/技术面/舆情摘要"""

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or _archive_dir()
        self.meta_path = os.path.join(self.base_dir, "meta.json")
        self._load_meta()

    def _load_meta(self):
        if os.path.exists(self.meta_path):
            with open(self.meta_path) as f:
                self.meta = json.load(f)
        else:
            self.meta = {
                "version": "2.0-rebuild",
                "created": _now_iso(),
                "updated": _now_iso(),
                "codes": []
            }
            self._save_meta()

    def _save_meta(self):
        self.meta["updated"] = _now_iso()
        with open(self.meta_path, 'w', encoding='utf-8') as f:
            json.dump(self.meta, f, ensure_ascii=False, indent=2)

    def _summary_path(self, code: str) -> str:
        return os.path.join(self.base_dir, f"{code}_summary.json")

    def _index_path(self, code: str) -> str:
        return os.path.join(self.base_dir, f"{code}_rag_index.json")

    def save_summary(self, code: str, research_data: Dict) -> bool:
        """保存个股研究摘要"""
        path = self._summary_path(code)
        # 加时间戳
        research_data = dict(research_data)
        research_data['updated_at'] = _now_iso()
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(research_data, f, ensure_ascii=False, indent=2)
            if code not in self.meta['codes']:
                self.meta['codes'].append(code)
                self._save_meta()
            return True
        except Exception as e:
            print(f"⚠️ save_summary {code} 失败: {e}")
            return False

    def load_summary(self, code: str) -> Dict:
        """读取个股研究摘要(无则空)"""
        path = self._summary_path(code)
        if not os.path.exists(path):
            return {}
        with open(path, encoding='utf-8') as f:
            return json.load(f)

    def list_codes(self) -> List[str]:
        """列出所有已存档的代码"""
        return list(self.meta.get('codes', []))

    def index_rag(self, code: str, docs: List[Dict]) -> bool:
        """建 RAG 索引(docs: [{text, source, ts}, ...])"""
        index = {
            'code': code,
            'created': _now_iso(),
            'docs': docs,
            'count': len(docs)
        }
        try:
            with open(self._index_path(code), 'w', encoding='utf-8') as f:
                json.dump(index, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"⚠️ index_rag {code} 失败: {e}")
            return False

    def query_rag(self, code: str, question: str) -> List[Dict]:
        """简单关键词匹配查询(无 embedding)"""
        path = self._index_path(code)
        if not os.path.exists(path):
            return []
        with open(path, encoding='utf-8') as f:
            index = json.load(f)
        # 关键词包含匹配(降级版,真要 RAG 用 embedding)
        q_words = set(question.split())
        results = []
        for d in index.get('docs', []):
            text = d.get('text', '')
            if any(w in text for w in q_words):
                results.append(d)
        return results


class ResilientWorkflow:
    """容错工作流 — 编排个股分析步骤"""

    def __init__(self, archive: ResearchArchive = None):
        self.archive = archive or ResearchArchive()
        self.steps = []  # 已执行的步骤

    def add_step(self, step_name: str, data: Any = None):
        """记录一个步骤(供回溯)"""
        self.steps.append({
            'name': step_name,
            'ts': _now_iso(),
            'data': data
        })

    def run_analysis(self, stock_code: str, price_data: Dict, metadata: Dict = None) -> Dict:
        """
        跑单只股票的分析(workflow 主调用)
        Args:
            stock_code: '300059'
            price_data: 实时行情 dict (从 fallback_pool.get_spot() 拉)
            metadata: 上下文 ({'source': '选股workflow', 'date': 'YYYY-MM-DD'})
        Returns:
            研究摘要 dict(已落 runtime/logs/research_archive/{code}_summary.json)
        """
        metadata = metadata or {}
        result = {
            'code': stock_code,
            'price_data': price_data,
            'metadata': metadata,
            'analysis': {
                'ts': _now_iso(),
                'price': price_data.get('price', 0),
                'change_pct': price_data.get('change_pct', 0),
                'name': price_data.get('name', ''),
                'signal': self._simple_signal(price_data),
                'note': f"重构版研究档案 v2.0 — {metadata.get('source', 'unknown')}"
            },
            'steps': list(self.steps)
        }
        # 落档案
        self.archive.save_summary(stock_code, result)
        return result

    @staticmethod
    def _simple_signal(price_data: Dict) -> str:
        """简化版信号判断(基于 change_pct)"""
        pct = price_data.get('change_pct', 0)
        if pct > 5:
            return "🟢 强势"
        elif pct > 1:
            return "🟢 偏强"
        elif pct > -1:
            return "🟡 震荡"
        elif pct > -5:
            return "🟠 偏弱"
        else:
            return "🔴 弱势"


class StockRAG:
    """个股 RAG 检索(简化版)"""

    def __init__(self, archive: ResearchArchive = None):
        self.archive = archive or ResearchArchive()

    def index(self, code: str, docs: List[Dict]) -> bool:
        """建索引"""
        return self.archive.index_rag(code, docs)

    def query(self, code: str, question: str) -> str:
        """查询(返回匹配的 docs 拼接文本)"""
        results = self.archive.query_rag(code, question)
        if not results:
            return f"(无匹配结果,代码 {code} 档案可能为空)"
        # 拼接 top 3
        lines = [f"[{r.get('source', '?')} {r.get('ts', '?')}] {r.get('text', '')}"
                 for r in results[:3]]
        return "\n".join(lines)
