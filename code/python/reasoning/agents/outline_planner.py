"""Outline Planner Agent — Plan 4 Phase 2 / Phase 4.

Stage 5 開頭呼叫一次 LLM 規劃 BookOutline（全書章節 brief + role + transitions
+ planned_evidence_ids），供 section writer prompt 在 Phase 3 注入 outline_list
+ previous_chapter_summary block。

設計取捨（CEO 拍板項，plan §8）：
- level="low"：與其他 intent parser 一致，成本低。
- 不啟用 thinking mode：複雜度不高，避免 latency + cost。
- max_length=4096：避免 Plan 1 follow-up 修的 truncation 地雷。
- Phase 4 fallback：LLM 失敗 → build_skeleton_outline deterministic 衍生 +
  明示 narration（CLAUDE.md「不可 silent fail」紀律）。
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Union

from core.llm import ask_llm
from reasoning.prompts.outline_planner import build_outline_planner_prompt
from reasoning.schemas_live import (
    BookOutline,
    ChapterPlan,
    ContextMap,
    ContextMapTopic,
    EvidencePoolEntry,
    StyleAnalysisOutput,
)

logger = logging.getLogger(__name__)


# BookOutline 對應 JSON schema（給 ask_llm）
_BOOK_OUTLINE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "chapters": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "properties": {
                    "chapter_index": {"type": "integer"},
                    "title": {"type": "string"},
                    "brief": {"type": "string"},
                    "target_word_count": {"type": "integer"},
                    "planned_evidence_ids": {
                        "type": "array",
                        "items": {"type": "integer"},
                    },
                    "transition_hint": {"type": "string"},
                    "role": {
                        "type": "string",
                        "enum": ["intro", "body", "conclusion"],
                    },
                },
                "required": ["chapter_index", "title", "brief", "role"],
            },
        },
        "overall_arc": {"type": "string"},
        "redundancy_warnings": {
            "type": "array",
            "items": {"type": "string"},
        },
    },
    "required": ["chapters"],
}


class OutlinePlannerAgent:
    """Plan 4 outline planner — 為 Stage 5 writer 規劃 BookOutline。"""

    def __init__(self, handler, timeout: int = 90):
        """Args:
            handler: request handler（取 query_params for dev override）。
            timeout: ask_llm timeout 秒數。gpt-5.1 reasoning model 跑多章 outline
                較慢（舊 30s 預設常致 timeout → skeleton fallback），90s 對齊 writer。
        """
        self.handler = handler
        self.timeout = timeout

    async def plan_outline(
        self,
        chapter_source: List[Union[Dict[str, str], ContextMapTopic]],
        context_map: ContextMap,
        format_specs: Dict[str, Any],
        style_features: Optional[StyleAnalysisOutput] = None,
        evidence_pool: Optional[Dict[int, EvidencePoolEntry]] = None,
    ) -> BookOutline:
        """為章節清單規劃 BookOutline（單一 LLM call）。

        Args:
            chapter_source: Plan 2 _resolve_chapter_source 回傳的 source list。
            context_map: 整份 ContextMap。
            format_specs: Stage 4 拍板的格式 dict。
            style_features: Stage 3 StyleAnalysisOutput（可為 None）。
            evidence_pool: Track A (sprint 2026-05-28): 全 evidence_pool 注入
                LLM prompt (讓 LLM 看到 title+snippet 做語意配對 per-chapter
                evidence allocation); validator 也用 keys 集合驗
                planned_evidence_ids ⊆ evidence_pool.keys() invariant。

        Returns:
            驗證過的 BookOutline。

        Raises:
            ValueError / TimeoutError: LLM call 失敗或回傳不符 schema；
                呼叫端 (_run_stage_5) 應 catch 後走 build_skeleton_outline fallback。
        """
        prompt = build_outline_planner_prompt(
            chapter_source=chapter_source,
            context_map=context_map,
            format_specs=format_specs,
            style_features=style_features,
            evidence_pool=evidence_pool,
        )

        _t0 = time.perf_counter()
        logger.info(
            f"[LIVE RESEARCH] Outline planner LLM call start: "
            f"n_chapters={len(chapter_source)} prompt_len={len(prompt)} timeout={self.timeout}s"
        )
        try:
            response = await ask_llm(
                prompt,
                _BOOK_OUTLINE_SCHEMA,
                level="low",
                query_params=getattr(self.handler, "query_params", {}),
                max_length=4096,
                timeout=self.timeout,
            )
        except Exception as e:
            _elapsed = time.perf_counter() - _t0
            logger.error(
                f"[LIVE RESEARCH] Outline planner LLM call failed: "
                f"elapsed={_elapsed:.2f}s error={type(e).__name__}: {e}"
            )
            raise

        _elapsed = time.perf_counter() - _t0
        if not response:
            logger.error(
                f"[LIVE RESEARCH] Outline planner returned empty: elapsed={_elapsed:.2f}s"
            )
            raise ValueError("Outline planner LLM returned empty response")

        # Unwrap schema-wrapped response if needed
        if "chapters" not in response and "properties" in response:
            inner = response.get("properties", {})
            if isinstance(inner, dict) and "chapters" in inner:
                response = inner

        # Bug #5 fix (sprint 2026-05-28): 正規化 chapter_index by 陣列位置。
        # LLM 天生 1-index（intro 章回 chapter_index=1）→ 撞 ChapterPlan /
        # BookOutline 的 role/index 一致 validator（intro 必 index==0）→ 每次
        # ValidationError → skeleton fallback（品質降級）。LLM 按 narrative order
        # 回章節（intro 第一個、conclusion 最後），故依位置 reassign chapter_index=i
        # (0-based) 即可讓正常 path 通過 validator。
        # 只改數值、不動 role / 順序 / 其他欄位 → 保留 Gemini C-2 red-team 保護：
        # 若 LLM 把非首章標 role='intro'，正規化後其 index≠0 → validator 仍 REJECT。
        # 防禦：chapters 非 list 或空 → 不動，交既有 validation/fallback 處理。
        _chapters = response.get("chapters") if isinstance(response, dict) else None
        if isinstance(_chapters, list) and _chapters:
            for i, ch in enumerate(_chapters):
                if isinstance(ch, dict):
                    ch["chapter_index"] = i

        # Track A (sprint 2026-05-28) addendum C-4 + Gemini C-2:
        # validate with context (evidence_pool_keys for invariant +
        # role/index 已在 BookOutline-level model_validator 內驗)。
        # 紀律：只有 evidence_pool 真的有提供時才傳 keys context (validator skip
        # context 缺) — 避免 test / dry-run 沒傳 pool 時假設「pool 是空」誤觸發。
        # N-5: Pydantic ValidationError 在 prod 不可 crash user session — caller
        # _run_stage_5 catch raise 後走 skeleton fallback。
        if evidence_pool is not None:
            validation_context = {"evidence_pool_keys": set(evidence_pool.keys())}
        else:
            validation_context = None
        try:
            outline = BookOutline.model_validate(
                response, context=validation_context,
            )
        except Exception as e:
            logger.error(
                f"[LIVE RESEARCH] Outline planner output validation failed: "
                f"elapsed={_elapsed:.2f}s error={type(e).__name__}: {e} response={response}"
            )
            raise

        logger.info(
            f"[LIVE RESEARCH] Outline planner done: elapsed={_elapsed:.2f}s "
            f"n_chapters={len(outline.chapters)} "
            f"roles=[{','.join(c.role for c in outline.chapters)}]"
        )
        return outline


def _match_evidence_by_keyword(
    chapter_title: str,
    chapter_brief: str,
    evidence_pool: Dict[int, "EvidencePoolEntry"],
) -> List[int]:
    """Track A (sprint 2026-05-28): 章節 title+brief 對 evidence_pool title+snippet
    做關鍵字命中 cheap match (純函式，無 LLM call)。

    紀律：
    - body 章必須回非空 list（避免 grounding 斷線 → BlockedSection / fabrication）
    - 若無命中 → fallback 回 evidence_pool 全部 keys（讓 writer 自選；Task 3
      C-1 gate 不會誤觸發空 evidence 進 writer）
    - 抽 chapter 文字裡長度 >= 2 的連續 CJK 序列 + 英文 token >= 3 char
      (addendum Mn-2: CJK-only regex 對英文章節失效，合併英文 token 後英文章節
      也命中)
    """
    import re

    if not evidence_pool:
        return []

    text = (chapter_title + " " + chapter_brief).lower()
    if not text.strip():
        return list(sorted(evidence_pool.keys()))

    # CJK 2-char 連續 + 英文 3-char 連續
    cjk_keywords = set(re.findall(r"[一-鿿]{2,}", text))
    en_keywords = set(re.findall(r"[a-z]{3,}", text))
    keywords = cjk_keywords | en_keywords
    if not keywords:
        # 真的零 keyword 才走 fallback
        return list(sorted(evidence_pool.keys()))

    matched: List[int] = []
    for eid, entry in evidence_pool.items():
        haystack = (
            (getattr(entry, "title", "") or "")
            + " "
            + (getattr(entry, "snippet", "") or "")
        ).lower()
        if any(kw in haystack for kw in keywords):
            matched.append(eid)

    if matched:
        return sorted(matched)
    # 命中為 0 → fallback 回全部（避免 body 章為空被 Task 3 gate 攔成 BlockedSection）
    return list(sorted(evidence_pool.keys()))


def build_skeleton_outline(
    chapter_source: List[Union[Dict[str, str], ContextMapTopic]],
    context_map: ContextMap,
    format_specs: Dict[str, Any],
    evidence_pool: Optional[Dict[int, "EvidencePoolEntry"]] = None,
) -> BookOutline:
    """Plan 4 Phase 4: Deterministic skeleton fallback。

    LLM call 失敗時用 chapter_source 衍生 default BookOutline，避免 hard fail 卡 Stage 5。
    CLAUDE.md 紀律：呼叫端必須 emit narration 明示「outline planner 降級」，**不可 silent fail**。

    Track A (sprint 2026-05-28): 新增 `evidence_pool` 參數做 per-chapter keyword
    match (取代 union-to-first)；不傳 evidence_pool 沿舊行為 (backward compat)。

    Strategy:
        - title = chapter_source[i].name (override) or topic.name (fallback)
        - brief = chapter_source[i].outline (override) or topic.description (fallback)
        - target_word_count = 0 (未指定，writer 依 format_spec 自決)
        - planned_evidence_ids:
          - override 模式 + evidence_pool 有: 走 _match_evidence_by_keyword
            per-chapter keyword match (Track A);
          - override 模式 + 無 evidence_pool: 第 0 章 union, 其餘空 (backward compat);
          - fallback (ContextMapTopic) 模式：每章拿自己的 topic.evidence_ids
        - transition_hint = ""（skeleton 無 LLM 衍生能力）
        - role = "intro" if i==0 else "conclusion" if i==N-1 else "body"

    Returns:
        deterministic BookOutline。
    """
    n = len(chapter_source)
    if n == 0:
        # 防呆：n=0 不可能（caller 上游已防），仍 deterministic 衍生最小 outline
        return BookOutline(
            chapters=[ChapterPlan(chapter_index=0, title="未命名", brief="", role="intro")],
            overall_arc="skeleton fallback (empty chapter source)",
            redundancy_warnings=[],
        )

    # union evidence_ids 給 override 模式第 0 章 backward compat
    union_ids: set = set()
    for t in context_map.topics:
        union_ids.update(t.evidence_ids)
    union_sorted = sorted(union_ids)

    chapters: List[ChapterPlan] = []
    for i, item in enumerate(chapter_source):
        # role 推斷
        if n == 1:
            role = "intro"
        elif i == 0:
            role = "intro"
        elif i == n - 1:
            role = "conclusion"
        else:
            role = "body"

        if isinstance(item, dict):
            title = item.get("name", f"章節 {i + 1}")
            brief = item.get("outline", "")
            # Track A: per-chapter keyword match (取代 union-to-first)
            if evidence_pool:
                planned_evidence_ids = _match_evidence_by_keyword(
                    chapter_title=title,
                    chapter_brief=brief,
                    evidence_pool=evidence_pool,
                )
            else:
                # Backward compat: 沒 evidence_pool 沿舊行為 union-to-first
                planned_evidence_ids = list(union_sorted) if i == 0 else []
        else:
            # ContextMapTopic
            title = item.name
            brief = item.description
            planned_evidence_ids = list(item.evidence_ids) if item.evidence_ids else []

        chapters.append(
            ChapterPlan(
                chapter_index=i,
                title=title,
                brief=brief,
                target_word_count=0,
                planned_evidence_ids=planned_evidence_ids,
                transition_hint="",
                role=role,
            )
        )

    return BookOutline(
        chapters=chapters,
        overall_arc="skeleton fallback — outline planner LLM call failed; "
        "deterministic structure derived from chapter source",
        redundancy_warnings=[],
    )
