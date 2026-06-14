"""LiveResearchHandler.__init__ enable_gap_enrichment query_param extraction (Track C C2 F-1 fix).

F-1 復驗 2026-05-28：DeepResearchHandler.__init__ 只設 self.enable_kg + self.enable_web_search，
**沒**設 self.enable_gap_enrichment。LR 必須 override __init__ 補上此 attr，沿 DR
enable_web_search 同 pattern。
"""
import os
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

# Insert `code/python` into sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))


@pytest.fixture
def http_handler():
    h = MagicMock()
    h.write_stream = AsyncMock()
    return h


@pytest.mark.parametrize("raw,expected", [
    ('true', True), ('True', True), ('1', True), (True, True),
    ('false', False), ('False', False), ('', False),
])
def test_lr_handler_extracts_enable_gap_enrichment_truthy_falsy(raw, expected, http_handler):
    from methods.live_research import LiveResearchHandler
    qp = {'query': 'x', 'dry_run': 'true', 'enable_gap_enrichment': raw}
    h = LiveResearchHandler(query_params=qp, http_handler=http_handler)
    assert h.enable_gap_enrichment is expected


def test_lr_handler_default_enable_gap_enrichment_false(http_handler):
    """Backward-compat: no query_param → False (預設 safe default)."""
    from methods.live_research import LiveResearchHandler
    qp = {'query': 'x', 'dry_run': 'true'}
    h = LiveResearchHandler(query_params=qp, http_handler=http_handler)
    assert h.enable_gap_enrichment is False


# F1 (2026-06-08): enable_web_search extraction tests — TDD (failing before Task 1 implementation).
# Parallels enable_gap_enrichment pattern above. CEO 拍板 LR default-on, no UI toggle.

@pytest.mark.parametrize("raw,expected", [
    (True, True),        # JSON boolean true (前端送 enable_web_search: true)
    ('true', True),      # string backward-compat
    ('True', True),      # string backward-compat
    ('1', True),         # string backward-compat
    ('false', False),    # string false
    ('False', False),    # string False
    ('', False),         # empty string → False
])
def test_lr_handler_extracts_enable_web_search_truthy_falsy(raw, expected, http_handler):
    """F1: LiveResearchHandler.__init__ 提取 enable_web_search，吃 JSON bool + 字串。"""
    from methods.live_research import LiveResearchHandler
    qp = {'query': 'x', 'dry_run': 'true', 'enable_web_search': raw}
    h = LiveResearchHandler(query_params=qp, http_handler=http_handler)
    assert h.enable_web_search is expected


def test_lr_handler_default_enable_web_search_false(http_handler):
    """F1 backward-compat: no enable_web_search flag → False (舊呼叫不受影響)。"""
    from methods.live_research import LiveResearchHandler
    qp = {'query': 'x', 'dry_run': 'true'}
    h = LiveResearchHandler(query_params=qp, http_handler=http_handler)
    assert h.enable_web_search is False
