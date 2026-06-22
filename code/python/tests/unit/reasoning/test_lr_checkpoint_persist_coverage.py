"""LR orchestrator checkpoint persist 覆蓋率（plan: lr-sse-reconnect-resume, 2026-06-15）。

C2/C-1：每個 durable boundary（set_checkpoint / complete_stage 後最近的 return state）
return 前必須有 _persist_checkpoint_boundary / _persist_progress（否則離線跑到 checkpoint
的 state 沒存到 = CEO 最在意的部分白跑）。

靜態 sweep 驗零漏點：對每個「durable boundary return state」，往回掃同函式至最近的
set_checkpoint/complete_stage，確認其間有 persist 呼叫。
"""
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

ORCH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..',
    'reasoning', 'live_research', 'orchestrator.py'))


def _indent(s):
    return len(s) - len(s.lstrip())


def _durable_boundary_returns(lines):
    """回傳 (return_lineno, persisted_bool) for each durable-boundary return state."""
    out = []
    for i, l in enumerate(lines):
        if not l.strip().startswith('return state'):
            continue
        j = i - 1
        found_cp = False
        persisted = False
        while j >= 0:
            s = lines[j].strip()
            if re.match(r'\s*(async def |def )', lines[j]) and _indent(lines[j]) <= 4:
                break
            if s.startswith('return state'):
                break
            if '_persist_checkpoint_boundary(' in s or '_persist_progress(' in s:
                persisted = True
            if (('set_checkpoint(' in s and 'def set_checkpoint' not in s)
                    or ('complete_stage(' in s and 'def complete_stage' not in s)):
                found_cp = True
                break
            j -= 1
        if found_cp:
            out.append((i + 1, persisted))
    return out


def test_every_durable_boundary_return_persists():
    """零漏點：每個 set_checkpoint/complete_stage 的 return 前都有 persist 呼叫。"""
    lines = open(ORCH, encoding='utf-8').read().split('\n')
    boundaries = _durable_boundary_returns(lines)
    assert len(boundaries) >= 40, f"sweep 只找到 {len(boundaries)} 個 boundary，疑似掃描失效"
    missing = [ln for ln, persisted in boundaries if not persisted]
    assert not missing, f"以下 durable-boundary return 前缺 persist（離線會白跑）: lines {missing}"


def test_persist_boundary_helper_exists():
    src = open(ORCH, encoding='utf-8').read()
    assert 'async def _persist_checkpoint_boundary(' in src
    assert 'def _mark_offline_since(' in src
    assert 'def _offline_cap_reached(' in src
    assert 'def _maybe_reset_offline_counters(' in src
