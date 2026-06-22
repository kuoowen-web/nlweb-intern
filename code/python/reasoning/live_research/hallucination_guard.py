"""Live Research per-section Hallucination Guard.

Port from DR `code/python/reasoning/orchestrator.py:1095-1131`：DR 在 final
report 跑完後做一次性 subset check（writer.sources_used ⊆ analyst_citations）
+ 自動修正 + confidence='Low' + tracer.condition_branch('HALLUCINATION_GUARD',
...)。LR 因為 per-section 寫作，每寫完一個 section 就跑一次 guard。

LR 額外加 literal placeholder regex check（DR 沒有）：偵測 section content
含字面 (Author, Year) / (作者, 年份) / 裸 [N] placeholder 樣式，若有則同樣
flag hallucination_corrected。這是 P0-C prompt 防護（commit 3945be5 移除
negative example）之後的 secondary defense。
"""

import re
from typing import Any, Callable, List, Optional, Set, Tuple

from misc.logger.logging_config_helper import get_configured_logger
from reasoning.schemas_live import LiveWriterSectionOutput

logger = get_configured_logger("live_research.hallucination_guard")


# 偵測字面 placeholder（不是真實引用，Writer 沒按 citation_format enum 寫）
# 這些 pattern 觸發 → flag hallucination_corrected + confidence='Low'
LITERAL_PLACEHOLDER_PATTERNS = [
    r"\(Author,\s*Year\)",       # 英文字面 placeholder
    r"\(作者,\s*年份\)",            # 中文字面 placeholder
    r"\(作者,\s*年代\)",            # 中文字面 placeholder（另一寫法）
    r"\[N\]",                     # 裸 [N] placeholder（注意：真實 [3]、[12] 不該誤觸發）
]


import unicodedata


def _normalize_for_match(s: str) -> str:
    """正規化供字面比對：NFKC（全半形/相容字統一）+ casefold + 去所有空白。

    僅用於 deterministic「字面命中 grounded」捷徑（C，省 trivial LLM call）。
    刻意不做語意處理——語意改寫（台電/台灣電力公司）交給 low model 語意層判
    （CEO 決策①：資料源做好後 low 即足以判讀，tier 維持 low）。
    """
    if not s:
        return ""
    norm = unicodedata.normalize("NFKC", s).casefold()
    return "".join(norm.split())


def _deterministic_grounded_filter(
    candidates: List[str], chapter_evidence_text: str
) -> List[str]:
    """字面命中 evidence 的 candidate 直接視為 grounded（移除），回剩餘待 LLM 判定者。

    **定位（CEO 方向）**：這是「零成本 grounded 捷徑」，只省「字面已完全命中」這種
    trivially-grounded 的 LLM call——**不承擔同義改寫的正確性責任**（那是 low model 語意層
    + 全 pool 完整資料源的工作）。字面找不到不代表 ungrounded，只代表「要交給 LLM 判」。

    Returns:
        List[str]: 字面找不到、需交 LLM 語意判定的 candidate（順序保留）。
    """
    norm_evidence = _normalize_for_match(chapter_evidence_text)
    needs_llm: List[str] = []
    grounded_by_literal: List[str] = []
    for c in candidates:
        nc = _normalize_for_match(c)
        if nc and nc in norm_evidence:
            grounded_by_literal.append(c)
        else:
            needs_llm.append(c)
    if grounded_by_literal:
        logger.info(
            f"[entity_grounding_check] 字面捷徑判 grounded（免 LLM）: "
            f"{grounded_by_literal}"
        )
    return needs_llm


# 中文 / 英文句尾標點切句（標點隨前句）
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？!?\n])")
# citation 標記：[3] / [3,12] / [3、12]（半形括號，與 render 端一致）
_CITATION_RE = re.compile(r"\[\d+(?:\s*[,、]\s*\d+)*\]")
# 句首/句中上下文依賴連接詞（刪去會留殘骸 / 指代不明）
_CONJUNCTION_MARKERS = (
    "但是", "然而", "因此", "所以", "因而", "於是", "不過", "因此", "故",
    "此外", "另外", "其中", "其", "該", "上述", "前述", "這", "此",
)


def split_and_filter_ungrounded_sentences(
    content: str,
    ungrounded_entities: List[str],
    grounded_entities: List[str],
) -> Tuple[str, int, int]:
    """切句，**只硬刪「純未驗證句」**，保留其餘 + 回報「不可安全硬刪」的句子數。

    R3 句子分類：候選刪除句（含 ungrounded entity 字面）若**任一**成立則**不硬刪**——
      (1) 同句又含已驗證 entity（grounded_entities 之一）；
      (2) 含 citation 標記（`[N]` / `[N,M]`）；
      (3) 被上下文依賴連接詞綁定（但是/因此/然而/所以…/代名詞指代）。
    → 該句保留進 kept，並 unsafe_count += 1（caller 據此走退化路徑 (a)：保留＋標註，
      **不做 LLM 改寫補通順**——CEO 已否決，那會引回模糊化）。
    只有「純未驗證句」（三條都不成立）才 regex 直接刪。

    命中判定用正規化 substring（與 _deterministic_grounded_filter 同口徑），
    避免全半形/空白差異漏判。

    Returns:
        (kept_content, removed_count, unsafe_count)
        - removed_count：被硬刪的純未驗證句數
        - unsafe_count：含 ungrounded 但「不可安全硬刪」而保留的混合/依賴句數
    """
    if not content or not ungrounded_entities:
        return content, 0, 0
    norm_ung = [_normalize_for_match(e) for e in ungrounded_entities if e]
    norm_ung = [e for e in norm_ung if e]
    if not norm_ung:
        return content, 0, 0
    norm_grounded = [_normalize_for_match(e) for e in (grounded_entities or []) if e]
    norm_grounded = [e for e in norm_grounded if e]

    sentences = [s for s in _SENTENCE_SPLIT_RE.split(content) if s]
    kept_parts: List[str] = []
    removed = 0
    unsafe = 0
    for s in sentences:
        ns = _normalize_for_match(s)
        hits_ungrounded = any(u in ns for u in norm_ung)
        if not hits_ungrounded:
            kept_parts.append(s)
            continue
        # 句子分類：判斷是否「不可安全硬刪」
        has_verified = any(g in ns for g in norm_grounded)
        has_citation = bool(_CITATION_RE.search(s))
        has_conjunction = any(m in s for m in _CONJUNCTION_MARKERS)
        if has_verified or has_citation or has_conjunction:
            # 不硬刪：混合句 / 有 citation / 上下文依賴 → 保留，回報 unsafe（caller 退化 (a)）
            kept_parts.append(s)
            unsafe += 1
            continue
        # 純未驗證句 → 硬刪
        removed += 1
    return "".join(kept_parts), removed, unsafe


def apply_hallucination_guard(
    section: LiveWriterSectionOutput,
    valid_evidence_ids: Set[int],
) -> Tuple[LiveWriterSectionOutput, bool]:
    """Port DR Hallucination Guard 到 LR per-section。

    Args:
        section: Writer 寫完的單一 section output
        valid_evidence_ids: 白名單（聯集 ContextMap.topics.evidence_ids）

    Returns:
        (corrected_section, was_corrected):
        - was_corrected=False → 回傳原 section（沒 mutate）
        - was_corrected=True → 回傳新 section（sources 移除 phantom、
          confidence_level='Low'、methodology_note 加註記）

    觸發條件（任一即觸發）：
    1. sources_used 含 valid_evidence_ids 外的 id（phantom）
    2. section_content 含字面 (Author, Year) / (作者, 年份) / 裸 [N] placeholder
    """
    needs_correction = False
    reason_parts = []

    # Check 1: sources_used subset of valid_evidence_ids（對應 DR orchestrator.py:1098）
    invalid_ids = set(section.sources_used) - valid_evidence_ids
    if invalid_ids:
        needs_correction = True
        reason_parts.append(f"移除未驗證來源 {sorted(invalid_ids)}")

    # Check 2: literal placeholder regex（LR 特有，DR 沒有）
    placeholder_hit = None
    for pattern in LITERAL_PLACEHOLDER_PATTERNS:
        if re.search(pattern, section.section_content):
            placeholder_hit = pattern
            break
    if placeholder_hit:
        needs_correction = True
        reason_parts.append(
            f"偵測字面 placeholder/佔位符（pattern={placeholder_hit!r}）"
        )

    # Check 3: typed citations[i].evidence_id ⊆ valid_evidence_ids
    # TypeAgent Target 3 (2026-05-19)：Writer LLM output 的 citations structured
    # data 也要 subset 驗證，phantom evidence_id 移除避免 render 漏失。
    phantom_citations = [
        c for c in section.citations
        if c.evidence_id not in valid_evidence_ids
    ]
    if phantom_citations:
        needs_correction = True
        reason_parts.append(
            f"移除未驗證 citation evidence_id "
            f"{sorted(c.evidence_id for c in phantom_citations)}"
        )

    if not needs_correction:
        return section, False

    corrected_sources = sorted(set(section.sources_used) & valid_evidence_ids)
    corrected_citations = [
        c for c in section.citations
        if c.evidence_id in valid_evidence_ids
    ]
    reason_str = "; ".join(reason_parts)
    existing_note = section.methodology_note or ""
    new_note = (
        f"{existing_note} [自動修正：{reason_str}]"
        if existing_note
        else f"[自動修正：{reason_str}]"
    ).strip()

    corrected = section.model_copy(update={
        "sources_used": corrected_sources,
        "citations": corrected_citations,
        "confidence_level": "Low",
        "methodology_note": new_note,
    })

    # 不可 silent fail —— Hallucination Guard 觸發必須 log（risk A: LR 無 tracer，
    # 用 logger.warning 確保 observability）
    logger.warning(
        f"[LIVE RESEARCH] Hallucination guard triggered for section "
        f"{section.section_title!r}: {reason_str} "
        f"(no tracer to record — using log fallback)"
    )
    return corrected, True


# ============================================================================
# Track A (LR DR-parity sprint 2026-05-28) — Task 5:
# Per-section content-aware entity grounding check (cheap LLM call)
# ============================================================================


async def _extract_entities_for_grounding(
    section_content: str, handler: Any, level: str = "low",
    on_extraction_failed: "Optional[Callable[[], None]]" = None,
) -> List[str]:
    """LLM 只列出 prose 中的具體 entity（不判 grounded）。抽取用 low（便宜、夠用）。"""
    from core.llm import ask_llm, LLMError

    prompt = (
        "請從下列段落列出所有**具體 entity**（國家 / 城市 / 地名 / 機構 / 風場 / "
        "法規 / 人名 / 具體數字）。\n"
        "趨勢判斷、背景常識、抽象論點**不算 entity**（不要列）。\n\n"
        f"## 段落\n\n{section_content}\n\n"
        '回傳 JSON：{"entities": ["e1", ...]}。無具體 entity 回 {"entities": []}。'
    )
    schema = {
        "type": "object",
        "properties": {"entities": {"type": "array", "items": {"type": "string"}}},
        "required": ["entities"],
    }
    try:
        resp = await ask_llm(
            prompt, schema, level=level,
            query_params=getattr(handler, "query_params", {}),
            max_length=2048, timeout=30,  # 2026-06-19 15→30：冷門外國 entity low model 判讀慢，給夠時間（穩定的慢非偶發抖動，調長優於 retry）
        )
    except Exception as e:
        # 非 LLM 的意外錯誤（如 prompt building）— ask_llm 失敗本身回 LLMError sentinel
        # 不 raise（commit 4936392c），由下方 isinstance 分支處理。
        logger.warning(
            f"[LIVE RESEARCH] candidate entity 抽取 LLM fail (non-fatal): "
            f"{type(e).__name__}: {e}"
        )
        # Task 3: 抽取故障是「系統出狀況」≠「沒 candidate」。fail-open 方向不變（仍回
        # []，grounding 跳過——抽不出 candidate 沒東西要查，方向安全 verified），但通知
        # caller 補旁白（517115a7 精神：系統故障要讓 user 看見）。
        # callback 是 sync set-flag（永不 raise），直接呼叫（F-2：移除多餘 inner try/except）。
        if on_extraction_failed is not None:
            on_extraction_failed()
        return []
    # commit 4936392c 後 ask_llm 失敗（timeout/provider_error/config_error）改 **return**
    # LLMError sentinel 不 raise → 不進上方 except。LLMError 是 falsy 空 dict 子類，若直接
    # `(resp or {}).get` 會把主要故障模式（LLM provider error / timeout）靜默吞成 []，
    # callback 從不觸發。此處顯式偵測 → 補旁白後 fail-open（與 except 路徑同義）。
    if isinstance(resp, LLMError):
        logger.warning(
            f"[LIVE RESEARCH] candidate entity 抽取回 LLMError "
            f"(kind={resp.error_kind}); fail-open + 通知 caller 補旁白"
        )
        if on_extraction_failed is not None:
            on_extraction_failed()
        return []
    ents = (resp or {}).get("entities") or []
    if not isinstance(ents, list):
        return []
    return [str(x) for x in ents if x]


class GroundingCheckUnavailable(Exception):
    """grounding 判讀 LLM 不可用（exception / 爆窗 / 無法解析）。

    **R1 fail-closed（專案鐵律「不可 silent fail」）**：grounding 判讀失敗時
    **絕不**回傳空陣列（= 判定全部 grounded = fail-open = 悄悄放行幻覺）。改 raise
    本 exception，由 caller（orchestrator）捕捉後走 DR 式退化路徑 (a)：保留正文不動
    + confidence 降 Low + methodology note 明確標註「grounding 系統驗證失敗，本章未經
    完整查證」+ log 明確錯誤。錯誤必須浮現，不可吞掉。
    """


async def _semantic_grounding_check(
    candidates: List[str], chapter_evidence_text: str, handler: Any, level: str,
) -> List[str]:
    """對「字面找不到」的殘餘做語意判定：evidence 是否以同義/改寫/全名涵蓋該 entity。
    回真正 ungrounded（語意也找不到）的子集。level 由 caller 指定（CEO 決策①：維持 low）。

    **R1 fail-closed**：LLM exception / 爆 low-model context window / 回傳無法解析
    → raise `GroundingCheckUnavailable`（**不 return []**）。fail-open（return [] = 全
    grounded）會在 evidence 變多爆窗時悄悄放行所有幻覺，違反「不可 silent fail」鐵律。
    """
    if not candidates:
        return []
    from core.llm import ask_llm

    cand_list = "\n".join(f"- {c}" for c in candidates)
    prompt = (
        "你是 fact-checking 助手。下列 entity 的**字面**未直接出現在 evidence 中。\n"
        "請判斷每個 entity 是否**在語意上**有 evidence 支撐"
        "（evidence 以同義詞 / 全名 / 改寫 / 上位詞涵蓋了它，例：evidence 寫"
        "「台灣電力公司」可支撐 prose 的「台電」）。\n"
        "- 語意上有支撐 → 不列入 ungrounded\n"
        "- evidence 完全沒提到（字面與語意都無）→ 列入 ungrounded\n"
        "- 趨勢判斷 / 背景常識 / 抽象論點不算 entity（若混入請排除）\n\n"
        f"## 待判定 entity\n{cand_list}\n\n"
        f"## Evidence\n\n{chapter_evidence_text}\n\n"
        '回傳 JSON：{"ungrounded_entities": ["e1", ...]}。'
        '全部有支撐回 {"ungrounded_entities": []}。'
    )
    schema = {
        "type": "object",
        "properties": {
            "ungrounded_entities": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["ungrounded_entities"],
    }
    try:
        resp = await ask_llm(
            prompt, schema, level=level,
            query_params=getattr(handler, "query_params", {}),
            max_length=2048, timeout=30,  # 2026-06-19 15→30：冷門外國 entity low model 語意判讀慢，給夠時間（穩定的慢非偶發抖動，調長優於 retry）
        )
    except Exception as e:
        # R1 fail-closed：LLM exception（含爆 low-model context window）→ 不吞、不回 []。
        # 明確 log error（不是 warning）+ raise，讓 caller 走 DR 式退化路徑 (a)。
        logger.error(
            f"[LIVE RESEARCH] semantic grounding LLM 失敗（fail-CLOSED，不放行）："
            f"{type(e).__name__}: {e}"
        )
        raise GroundingCheckUnavailable(str(e)) from e
    if not isinstance(resp, dict) or "ungrounded_entities" not in resp:
        # R1 fail-closed：回傳無法解析（None / 非 dict / 缺 key）→ 視同失敗，不當全 grounded。
        logger.error(
            f"[LIVE RESEARCH] semantic grounding 回傳無法解析（fail-CLOSED，不放行）：{resp!r}"
        )
        raise GroundingCheckUnavailable(f"unparseable grounding response: {resp!r}")
    ung = resp.get("ungrounded_entities") or []
    if not isinstance(ung, list):
        logger.error(
            f"[LIVE RESEARCH] semantic grounding ungrounded_entities 非 list"
            f"（fail-CLOSED，不放行）：{ung!r}"
        )
        raise GroundingCheckUnavailable(f"ungrounded_entities not a list: {ung!r}")
    cand_set = set(candidates)  # 防 LLM hallucinate 不在候選清單的 entity
    return [str(x) for x in ung if x and str(x) in cand_set]


async def entity_grounding_check(
    section: LiveWriterSectionOutput,
    chapter_evidence_text: str,
    handler: Any,
    grounding_level: str = "low",
    on_extraction_failed: "Optional[Callable[[], None]]" = None,
) -> List[str]:
    """列出 section 中**字面與語意都找不到 evidence 對應**的具體 entity（ungrounded）。

    CEO 方向「良好資料來源 → low model 判讀」三段式：
    1. LLM 抽 candidate entity（只抽不判，low tier）。
    2. deterministic 字面捷徑：字面命中 evidence → grounded，移除（零成本，省 trivial call）。
    3. 殘餘字面不命中者 → LLM 語意判定，tier=grounding_level（CEO 決策①預設 low，
       搭配 caller 餵的全 pool+不截斷+跨章 evidence 視圖 → 資料源做好後 low 也判得對）。

    grounding_level: 第 3 步 LLM tier，預設 "low"。CEO 拍板（決策①）「low 就可以，
        原始資料好的話誰都判得出來」→ orchestrator 兩呼叫點不傳，保持 low。
        參數保留只為未來彈性，非本 plan 動作（ModelConfig 僅 low/high，無 medium）。

    **R1 fail-closed（紀律「不可 silent fail」）**：
    - 抽取階段（第 1 步）LLM fail → 回 [] 是安全的（抽不出 candidate = 沒東西要查 =
      不會放行幻覺；abstract 詞本就不該擋），保留原 graceful。
    - **語意判定階段（第 3 步）LLM fail / 爆窗 / 無法解析 → `_semantic_grounding_check`
      raise `GroundingCheckUnavailable`，本函式不吞**。orchestrator 兩呼叫點外層捕捉此
      exception → 走 DR 式退化路徑 (a)（保留正文 + 降 Low + methodology 標「grounding
      系統驗證失敗，本章未經完整查證」）。**絕不回 [] 當作全 grounded。**
    """
    candidates = await _extract_entities_for_grounding(
        section.section_content, handler, level="low",
        on_extraction_failed=on_extraction_failed,
    )
    if not candidates:
        return []
    needs_llm = _deterministic_grounded_filter(candidates, chapter_evidence_text)
    if not needs_llm:
        return []  # 全字面命中 → 不打語意 LLM（字面確證，非「判讀失敗」）
    # R1：此處 raise GroundingCheckUnavailable 不捕捉，故意往上拋給 orchestrator 退化處理。
    return await _semantic_grounding_check(
        needs_llm, chapter_evidence_text, handler, level=grounding_level,
    )


def specificity_check(
    section,
    chapter_evidence_text: str,
    section_entities: list,
    evidence_has_concrete: bool = True,
) -> bool:
    """A 的對稱守門：偵測「evidence 有具體資訊，但本章 prose 幾乎沒有具體 entity」。

    與 entity_grounding_check 對稱：
    - entity_grounding_check：prose 有 entity 但 evidence 沒有（fabrication 方向）
    - specificity_check：evidence 有具體資訊但 prose 抽象（under-specification 方向）

    回 True = 太抽象、需 auto-rewrite。回 False = 通過。

    Args:
        section: LiveWriterSectionOutput-like（有 section_content / status）
        chapter_evidence_text: 本章 evidence 文字（title + snippet 拼接）
        section_entities: 已從本章 prose 抽出的具體 entity 清單（caller 重用 T7 抽取結果）
        evidence_has_concrete: caller 判定本章 evidence 是否含具體資訊；
            False（純概念章）→ 不 flag（避免誤殺）
    """
    # blocked / 非 drafted 章不檢查
    if getattr(section, "status", "drafted") != "drafted":
        return False
    # evidence 本身無具體資訊 → 抽象是合理的，不 flag
    if not evidence_has_concrete:
        return False
    content = getattr(section, "section_content", "") or ""
    # 太短的章（如已 blocked 的 fallback 文字）不檢查
    if len(content) < 200:
        return False
    # 核心判定：drafted body chapter + evidence 有具體資訊 + prose 抽不到任何具體 entity
    # → 太抽象。section_entities 由 caller 用既有 _extract_section_entities 抽好傳入
    # （零額外 LLM call，重用 T7 寫完本就會跑的抽取結果）。
    if not section_entities:
        return True
    return False
