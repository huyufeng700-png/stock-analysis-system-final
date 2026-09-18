#!/usr/bin/env python3
"""
mock_bridge.py — A股替身模式桥接（env 驱动，零侵入）

作用
----
让股票系统的取数函数在**不改调用方**的前提下切换到确定性替身数据。

开关
----
    LIUYAO_MOCK=1            开启替身模式
    LIUYAO_MOCK_SEED=42      指定种子（默认 42）

安全铁律（来自 2026-09-19 生产污染事故的教训）
---------------------------------------------
替身模式**绝不能**写生产目录。调用方必须用 `output_dir()` 取输出路径，
替身模式下它会返回 `runtime/mock/` 子树。

用法
----
    from mock_bridge import is_on, mf, output_dir
    if is_on():
        return mf().mock_realtime(code)
"""

import os
import sys

WORKSPACE = "/Users/huyufeng/.openclaw/workspace"
if WORKSPACE not in sys.path:
    sys.path.insert(0, WORKSPACE)

try:
    import mock_feed_equity as _mf
except Exception:
    _mf = None

_ENABLED = None
_KB_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def is_on():
    """替身模式是否开启（惰性初始化，只算一次）"""
    global _ENABLED
    if _ENABLED is None:
        flag = os.environ.get("LIUYAO_MOCK", "").strip().lower() in ("1", "true", "yes", "on")
        if flag and _mf is not None:
            try:
                seed = int(os.environ.get("LIUYAO_MOCK_SEED", "42") or 42)
            except ValueError:
                seed = 42
            _mf.enable(seed)
        _ENABLED = bool(flag and _mf is not None)
    return _ENABLED


def mf():
    """返回替身数据模块（未开启时返回 None）"""
    return _mf if is_on() else None


def seed():
    return _mf.current_seed() if _mf else None


def output_dir(*parts):
    """
    取输出目录。替身模式下自动落到 runtime/mock/，**绝不污染生产**。
        output_dir("picks")          生产: runtime/picks/
                                     替身: runtime/mock/picks/
    """
    base = os.path.join(_KB_ROOT, "runtime")
    if is_on():
        base = os.path.join(base, "mock")
    return os.path.join(base, *parts) + os.sep


def guard_note():
    """给日志用的一行说明"""
    if is_on():
        return f"🎭 替身模式 (seed={seed()}) — 无网络，输出落 runtime/mock/"
    return ""
