"""Task 12 (🔧R4 R3-SF-2): §依賴方向圖不變式 gate — core/sse/send.py 對 reasoning
靜態 + lazy 皆零 import。

用 indexer 搜 'import reasoning'（CLAUDE.md 強制主搜尋）做主要定位，並用 AST 解析
send.py 判定「真 import 語句」（top-level + 任意函式體 lazy import 皆涵蓋），排除
docstring / 註解中僅提及 'import reasoning' 的散文（否則 send.py 的設計註解會假紅）。
"""
import ast
import subprocess
import sys
import pathlib


def _reasoning_imports_in_source(src: str):
    """回傳 send.py AST 中所有 import reasoning.* / from reasoning... 的語句字串。
    ast.walk 會遍歷函式體內的 lazy import，故 top-level 與 function-body import 皆涵蓋。
    docstring / 註解不是 Import/ImportFrom 節點 → 天然排除。"""
    tree = ast.parse(src)
    offending = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "reasoning" or alias.name.startswith("reasoning."):
                    offending.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "reasoning" or mod.startswith("reasoning."):
                offending.append(f"line {node.lineno}: from {mod} import ...")
    return offending


def _send_py_source():
    repo_root = pathlib.Path(__file__).resolve().parents[5]  # code/python/tests/unit/core -> repo 根
    return (repo_root / "code" / "python" / "core" / "sse" / "send.py").read_text(encoding="utf-8")


def test_core_sse_send_has_zero_reasoning_import():
    """indexer 搜 'import reasoning'（CLAUDE.md 強制主搜尋）；命中 send.py 時回讀原始碼
    以 AST 判是否為真 import 語句（indexer 會匹配到註解字面，故不能只靠命中檔名）。"""
    repo_root = pathlib.Path(__file__).resolve().parents[5]
    # encoding="utf-8" + errors="replace": indexer 輸出含中文 UTF-8；Windows 預設
    # cp950 解碼會炸（reader thread UnicodeDecodeError → stdout=None）。明指 UTF-8。
    out = subprocess.run(
        [sys.executable, "tools/indexer.py", "--search", "import reasoning"],
        cwd=repo_root, capture_output=True, check=True,
        encoding="utf-8", errors="replace",
    ).stdout or ""
    hit_send = ("core/sse/send.py" in out) or ("core\\sse\\send.py" in out)
    if hit_send:
        offending = _reasoning_imports_in_source(_send_py_source())
        assert not offending, (
            "core/sse/send.py 有真 reasoning import 語句（違 §依賴方向圖不變式）：\n"
            + "\n".join(offending))


def test_core_sse_send_source_has_no_reasoning_import():
    """直接讀 send.py 原始碼、AST 判真 import（第二道牙，不依賴 indexer 索引新鮮度）。"""
    offending = _reasoning_imports_in_source(_send_py_source())
    assert not offending, (
        "core/sse/send.py 原始碼含真 reasoning import 語句（違反依賴方向）：\n"
        + "\n".join(offending))
