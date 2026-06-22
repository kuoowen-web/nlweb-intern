"""O2-TF: text fragment URL 組裝演算法的契約鏡像（對齊 live-research.js
buildTextFragmentUrl）。此檔不跑 JS；它鎖死「相同輸入→相同 fragment」契約，
JS 端改演算法時須同步改本檔（兩端漂移會被 reviewer 抓到）。

JS 實作正確性由瀏覽器 console round-trip（Task C Step 5）+ 真機 E2E 驗。
"""
import re
from urllib.parse import quote as pct

ANCHOR_LEN = 12   # 對齊 JS：spike 驗 12 字命中 90%（Codex 警告 10 太短）
MIN_QUOTE = 4
LOW_UNIQUENESS = re.compile(
    r"^[\s\d\W]+$"
    r"|^(中央社|聯合報|自由時報|中時|蘋果|ETtoday|CNA|Reuters)$"
    r"|^\d{4}[-/年.]\d{1,2}([-/月.]\d{1,2})?日?$",
    re.UNICODE,
)


def _enc_frag(s: str) -> str:
    """對齊 JS encFrag：percent-encode 後再額外處理 `-`（fragment directive 語法字元）。"""
    return pct(s, safe="").replace("-", "%2D")


def build_text_fragment_url(url: str, q: str):
    """Python 鏡像 —— 邏輯須與 live-research.js buildTextFragmentUrl 等價。
    （簡化：不模擬 new URL() 的既有 hash 處理；URL 無既有 #hash 時兩端等價。
    既有 hash 的 case 由 JS console round-trip + E2E 驗，不在此鏡像範圍。）"""
    if not url or not q:
        return None
    q = q.strip()
    if len(q) < MIN_QUOTE:
        return None
    clean = q.rstrip(" \t\r\n，。、；：,.;:") or q
    if len(clean) <= ANCHOR_LEN * 2:
        if LOW_UNIQUENESS.match(clean):
            return None
        directive = _enc_frag(clean)
    else:
        start, end = clean[:ANCHOR_LEN], clean[-ANCHOR_LEN:]
        if start == end or LOW_UNIQUENESS.match(start):
            return None
        directive = _enc_frag(start) + "," + _enc_frag(end)
    return f"{url}#:~:text={directive}"


def test_short_quote_returns_none():
    assert build_text_fragment_url("https://x.com", "短") is None  # < MIN_QUOTE


def test_long_quote_uses_start_end_anchors():
    url = "https://x.com/p"
    q = "丹麥在二零二三年達成風電佔比超過五成政府透過社區共有模式分配收益"
    out = build_text_fragment_url(url, q)
    assert out.startswith("https://x.com/p#:~:text=")
    assert "," in out.split("#:~:text=")[1]  # START,END 雙錨點
    # START = 前 12 字，END = 末 12 字，均 percent-encoded
    assert _enc_frag(q[:ANCHOR_LEN]) in out
    assert _enc_frag(q[-ANCHOR_LEN:]) in out


def test_medium_quote_uses_single_anchor():
    out = build_text_fragment_url("https://x.com", "丹麥風電佔比超過五成嗎")  # <= 24 字
    assert "," not in out.split("#:~:text=")[1]  # 單錨點，無逗號


def test_low_uniqueness_anchor_degrades():
    """錨點唯一性 heuristic（Codex）：純數字 / 媒體名 / 日期 → 不組 fragment。"""
    assert build_text_fragment_url("https://x.com", "2025-03-27") is None      # 日期
    assert build_text_fragment_url("https://x.com", "中央社") is None          # 媒體名
    assert build_text_fragment_url("https://x.com", "12345678") is None        # 純數字


def test_hyphen_is_extra_encoded():
    """`-` 須額外 %2D（fragment directive 語法字元，Codex）。"""
    out = build_text_fragment_url("https://x.com", "AI-生成內容的版權爭議與責任歸屬問題")
    assert "%2D" in out          # `-` 被編成 %2D
    assert "-" not in out.split("#:~:text=")[1]  # directive 內無裸 `-`


def test_empty_url_or_quote_returns_none():
    assert build_text_fragment_url("", "原文片段") is None
    assert build_text_fragment_url("https://x.com", "") is None
