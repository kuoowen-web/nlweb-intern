"""
Associator Prompt Builder.

Contains all prompt building logic for the AssociatorAgent:
- build_context_map_prompt: Build initial Context Map (Master B) from research question
- derive_search_plan_prompt: Derive search plan (A) from current B
- refine_context_map_prompt: Update B to B' after retrieval

Prompt language: Traditional Chinese instructions.
Code comments: English.
"""

from typing import List, Optional
from core.prompts import generate_boundary_token, wrap_content_with_boundary


class AssociatorPromptBuilder:
    """
    Builds prompts for AssociatorAgent's three operations.

    Follows the same pattern as AnalystPromptBuilder:
    - String templates with f-string interpolation
    - Conditional blocks for optional inputs
    - Boundary token isolation for untrusted content
    - All prompt instructions in Traditional Chinese
    """

    def build_context_map_prompt(
        self,
        query: str,
        initial_context: Optional[str] = None,
        user_prior_knowledge: Optional[str] = None
    ) -> str:
        """
        Build prompt for Phase 0: Construct initial Context Map (Master B).

        Args:
            query: User's research question
            initial_context: Optional initial retrieval results (formatted with [ID] citations)
            user_prior_knowledge: Optional prior knowledge provided by user in dialogue

        Returns:
            Complete prompt string for AssociatorBuildOutput
        """
        # Block 1: Role definition (static)
        prompt = """你是研究結構設計師。你的任務是從研究問題出發，建立一個全面的知識地圖（Context Map），作為後續研究的骨架。

知識地圖（Context Map）是整個研究迴圈的核心，它記錄了研究問題涉及的所有議題（topics）、議題之間的關係（relations），以及後續需要搜尋的方向（search_seeds）。

"""

        # Block 2: Research question injection (dynamic)
        prompt += f"""## 研究問題

{query}

"""

        # Block 3: Initial context injection (conditional, boundary-isolated)
        if initial_context:
            boundary = generate_boundary_token()
            isolated_initial = wrap_content_with_boundary(initial_context, boundary)
            prompt += f"""## 初始資料（供參考）

以下是系統在你思考的同時抓取的初始相關資料，可作為建立知識地圖的起點：

{isolated_initial}

"""

        # Block 4: User prior knowledge injection (conditional)
        if user_prior_knowledge:
            prompt += f"""## 使用者先備知識

使用者提供了以下先備知識或已知資訊，請將其整合進知識地圖：

{user_prior_knowledge}

"""

        # Block 5: Cross-domain associative thinking guidance (static)
        prompt += """## 聯想引導

不要只想到最直接的關聯。請以寬廣的視野思考這個研究問題，涵蓋：

- **上下游關係**：誰受到影響？影響誰？
- **類比關係**：有沒有其他領域的類似情況可以借鑑？
- **因果鏈**：什麼原因導致這個現象？它又會造成什麼？
- **時間演進**：這個議題是如何隨時間發展的？
- **利害關係人**：誰支持？誰反對？為什麼？
- **潛在衝突**：有沒有互相矛盾的面向？
- **國際比較**：其他國家/地區有沒有類似的情況或解決方案？

每個 topic 要有明確的 `domain`（領域分類）和 `relevance`（core/supporting/peripheral）。

"""

        # Block 6: Output schema specification (static)
        prompt += """## 輸出規格

你必須回傳符合 `AssociatorBuildOutput` schema 的 JSON。欄位說明：

**`context_map`**（ContextMap 物件）：
- `research_question`：你收到的研究問題（直接複製）
- `working_hypothesis`：目前的工作假設——你認為研究結果可能的方向（可留空或初步推測）
- `topics`：議題列表，每個議題包含：
  - `name`：議題名稱（具體，例如：'德國社區共有能源模式'）
  - `domain`：領域分類（例如：'能源政策'、'治理模式'）
  - `description`：此議題在研究中的角色
  - `relevance`：'core'（直接回答研究問題）/ 'supporting'（提供背景）/ 'peripheral'（邊緣相關）
  - `confidence`：'high' / 'medium' / 'low'（你對此議題確實相關的信心）
- `relations`：議題之間的關係，每個關係包含：
  - `source_topic_id`、`target_topic_id`：關係的兩端（使用 topic 的 UUID）
  - `relation_type`：'causes'/'enables'/'prevents'/'contradicts'/'supports'/'part_of'/'precedes'/'analogous_to'
  - `description`：關係說明
- `followup_questions`：後續可能需要探索的問題
- `search_seeds`：初步建議的搜尋方向（留給 derive_search_plan 步驟詳細規劃，這裡可以列出幾個）

**`narration`**（字串）：
- 用自然的繁體中文，說明你為什麼這樣建構知識地圖

"""

        # Block 7: Narration behavior guidance (static)
        prompt += """## 敘述行為

你在邊做邊說。`narration` 欄位中用自然的語氣說明你為什麼這樣建構知識地圖。

**重要：這是給一般使用者看的說明，不是給工程師看的。**
絕對不可使用任何程式欄位名稱，例如：topics、relations、is_stable、followup_questions、v0、v1、context_map、search_seeds、confidence、delta、source_topic_id、target_topic_id 等。
請用自然語言描述你正在做什麼，例如「我先確認研究的幾個核心方向」而非「我設定了 topics」。

**好的敘述範例**：
"我先從台灣綠能衝突的三個主要面向開始 — 土地使用、社區參與、電網整合 — 因為這三個是研究問題的核心。我把德國的 Energiewende 和日本的地方電力模式列為輔助參考，因為它們提供了有用的比較框架，但不是研究的直接主體。我還注意到利害關係人衝突這個面向，可能值得深入探討..."

**語氣**：像一個思考中的研究夥伴在說話，不要機械化地列出清單。

**長度**：2-4 句，聚焦在最重要的設計決策。
"""

        return prompt

    def derive_search_plan_prompt(
        self,
        context_map_summary: str,
        executed_searches: List[str]
    ) -> str:
        """
        Build prompt for Phase 1: Derive search plan (A) from current Context Map (B).

        Args:
            context_map_summary: Markdown summary of current ContextMap
                                 (produced by context_map_to_summary or
                                 context_map_extract_for_section)
            executed_searches: List of search queries already executed
                               (to avoid duplication)

        Returns:
            Complete prompt string for AssociatorDeriveOutput
        """
        # Block 1: Role definition (static)
        prompt = """你是研究搜尋策略師。你根據目前的知識地圖（Context Map），決定接下來該找什麼資料、去哪找、為什麼找。

你的目標是填補知識地圖中的空缺：找出哪些議題還缺乏足夠的佐證，以及有哪些關係假設需要驗證。

"""

        # Block 2: Context Map injection (dynamic, boundary-isolated)
        boundary = generate_boundary_token()
        isolated_map = wrap_content_with_boundary(context_map_summary, boundary)
        prompt += f"""## 目前的知識地圖

{isolated_map}

"""

        # Block 3: Executed searches (conditional)
        if executed_searches:
            searches_formatted = "\n".join(f"- {s}" for s in executed_searches)
            prompt += f"""## 已執行的搜尋

以下搜尋已執行，請避免重複：

{searches_formatted}

"""

        # Block 4: Search strategy guidance (static)
        prompt += """## 搜尋策略引導

每個搜尋要有三個要素：

1. **目標議題**（`target_topic_id`）：這個搜尋服務於知識地圖中的哪個 topic
2. **具體 query**（`query`）：精確的搜尋字串，不要太寬泛
3. **搜尋理由**（`rationale`）：為什麼這個搜尋對研究有幫助

**優先順序原則**：
- 優先填補 `confidence: low` 的議題
- 優先填補 `core` 議題缺少的佐證
- 不要重複已執行的搜尋
- 每個搜尋都應該能明確判斷「找到了 / 沒找到」

**搜尋來源策略**（`source_strategy`）：
- `internal`：使用系統內部索引（適合台灣新聞、本地時事、中文政策報導）
- `web`：網路搜尋（適合**國外案例、非台灣地名、國際組織、跨國法規、學術文獻、政策對照**）
- `both`：兩者都用（**不確定哪邊有資料，或預期站內 corpus < 3 條**時優先選此）

**何時選 web 的紀律**（Track C，sprint 2026-05-28）：
- query 含明顯**非台灣地名**（如「德國 Energiewende」「丹麥 Horns Rev」「日本 FIT」「美國 IRA」）→ 優先選 `web` 或 `both`
- query 涉及**國際組織 / 跨國法規**（IEA / IRENA / EU CBAM / Paris Agreement / IPCC）→ 優先選 `web`
- 不確定 → 選 `both`（雙路跑成本可接受）
- 純台灣本地議題（如「台灣 2024 用電結構」「經濟部能源局政策」）→ 選 `internal`

"""

        # Block 5: Propose-Verify reminder (static)
        prompt += """## Propose-Verify 提醒

你的搜尋建議是 falsifiable hypothesis（可證偽假說）。

對每個搜尋，你應該隱含地問自己：「如果這個搜尋沒找到預期的東西，我應該如何調整知識地圖？」

**好的搜尋種子範例**：
- 明確的 query：「德國 Energiewende 社區共有電廠 2019-2023」
- 清楚的 rationale：「需要確認德國社區共有模式的規模與政策框架，目前這個議題的 confidence 是 low」
- 可驗偽：「如果搜不到社區層級的案例，可能德國主要是 utility-scale，需要調整分類」

"""

        # Block 6: Output schema specification (static)
        prompt += """## 輸出規格

你必須回傳符合 `AssociatorDeriveOutput` schema 的 JSON。欄位說明：

**`search_seeds`**（ContextMapSearchSeed 列表）：
- `query`：具體搜尋 query
- `target_topic_id`：此搜尋服務的議題 UUID（必須是知識地圖中存在的 topic_id）
- `rationale`：為何需要此搜尋
- `source_strategy`：'internal' / 'web' / 'both'
- `priority`：'high' / 'medium' / 'low'
- `status`：固定為 'pending'

**`narration`**（字串）：
- 用自然的繁體中文說明你計畫搜尋什麼以及為什麼這樣優先排序

"""

        # Block 7: Narration behavior guidance (static)
        prompt += """## 敘述行為

在 `narration` 欄位中，用自然的語氣說明你的搜尋策略。

**重要：這是給一般使用者看的說明，不是給工程師看的。**
絕對不可使用任何程式欄位名稱，例如：topics、relations、is_stable、followup_questions、v0、v1、context_map、search_seeds、confidence、delta、source_topic_id、target_topic_id 等。
請用自然語言描述你在做什麼，例如「我決定先補充這個方向的資料」而非「我設定了 search_seeds」。

**好的敘述範例**：
"研究地圖裡有幾個地方還很薄弱。德國社區共有模式的細節不夠，我決定先補這個——這是整個比較框架的核心。台灣方面，我注意到還沒有搜尋過具體的反彈案例，這個也很重要。我暫時跳過日本的部分，先把核心搜完再說..."

**語氣**：像策略師在說明搜尋計畫，不要只列清單。

**長度**：2-4 句，說明優先順序的邏輯。
"""

        return prompt

    def refine_context_map_prompt(
        self,
        current_context_map_summary: str,
        retrieval_results: str,
        initial_context_map_summary: str
    ) -> str:
        """
        Build prompt for Phase 4: Update Context Map B to B' after retrieval.

        Args:
            current_context_map_summary: Markdown summary of current ContextMap
            retrieval_results: Formatted retrieval results with [ID] citations
            initial_context_map_summary: Markdown summary of initial ContextMap (version 0)
                                         for drift awareness

        Returns:
            Complete prompt string for AssociatorRefineOutput
        """
        # Block 1: Role definition (static)
        prompt = """你是研究結構更新師。你根據新的搜尋結果，更新知識地圖（Context Map），讓它更精確地反映實際的研究發現。

你的目標是在「吸收新資料」和「保持研究焦點不漂移」之間取得平衡。

"""

        # Block 2: Current Context Map injection (dynamic, boundary-isolated)
        boundary_current = generate_boundary_token()
        isolated_current = wrap_content_with_boundary(current_context_map_summary, boundary_current)
        prompt += f"""## 目前的知識地圖（待更新）

{isolated_current}

"""

        # Block 3: Retrieval results injection (dynamic, boundary-isolated)
        boundary_retrieval = generate_boundary_token()
        isolated_retrieval = wrap_content_with_boundary(retrieval_results, boundary_retrieval)
        prompt += f"""## 新的搜尋結果

{isolated_retrieval}

"""

        # Block 4: Initial Context Map injection (dynamic, for drift awareness)
        boundary_initial = generate_boundary_token()
        isolated_initial = wrap_content_with_boundary(initial_context_map_summary, boundary_initial)
        prompt += f"""## 初始知識地圖（v0，供漂移判斷）

以下是研究開始時的初始知識地圖。提供它的目的是讓你意識到：更新後的知識地圖是否還在回答同一個研究問題。

{isolated_initial}

"""

        # Block 5: Refinement guidance (static)
        prompt += """## 精煉引導

根據新資料，判斷以下幾個面向：

**(a) Confidence 更新**：
- 新資料是否支持現有議題？→ 提高 confidence
- 新資料是否顯示現有議題有誤？→ 降低 confidence 或調整分類

**(b) 新議題（topic）**：
- 新資料是否揭示了之前未注意到的重要面向？→ 新增 topic
- 注意：只有當新面向對研究問題有直接或重要貢獻時才新增

**(c) 議題降級或移除**：
- 有沒有既有議題搜索後發現其實不相關？→ 從 core 降為 supporting 或 peripheral
- 有沒有已確認「不存在」或「不相關」的議題？→ 移除（記錄在 delta 的 removed_topics）

**(d) 新關係（relation）**：
- 新資料是否揭示議題之間的因果、支持、衝突關係？→ 新增 relation

**記住**：每次修改都要記錄在 `delta` 中（added/removed/modified topics 和 relations），並說明原因。

"""

        # Block 6: Stability judgment guidance (static)
        prompt += """## 穩定性判斷

判斷 `is_stable` 的標準：

**穩定條件**（`is_stable=True`）：
- 過去兩次 refinement 都沒有加入新的 `core` topic
- 過去兩次 refinement 都沒有發生 major relation change
- 所有 core 議題的 confidence 都已達到 'high' 或 'medium'

**不穩定條件**（`is_stable=False`，繼續搜尋）：
- 還有 confidence='low' 的 core 議題
- 這次 refinement 新增了重要的 core topic
- 新資料顯示研究問題可能有重要面向尚未探索

**重要原則**：寧可多查一輪，不要漏重要面向。如果有疑慮，`is_stable=False`。

"""

        # Block 7: Output schema specification (static)
        prompt += """## 輸出規格

你必須回傳符合 `AssociatorRefineOutput` schema 的 JSON。欄位說明：

**`updated_context_map`**（ContextMap 物件）：
- 完整的更新後知識地圖（包含所有 topics、relations、search_seeds）
- `version`：遞增（比原版本 +1）
- `last_refined_at`：更新為目前時間

**`delta`**（ContextMapDelta 物件）：
- `from_version`：原版本號
- `to_version`：新版本號
- `added_topics`：新增的 topic_id 列表
- `removed_topics`：移除的 topic_id 列表
- `modified_topics`：修改的 topic_id 列表
- `added_relations`：新增的 relation_id 列表
- `removed_relations`：移除的 relation_id 列表
- `reason`：此次精煉的原因摘要

**`is_stable`**（布林值）：依照上述穩定性判斷標準

**`narration`**（字串）：用自然的繁體中文說明你做了什麼修改以及為什麼

"""

        # Block 8: Narration behavior guidance (static)
        prompt += """## 敘述行為

在 `narration` 欄位中，用自然的語氣說明你做了什麼修改，以及你在過程中有什麼發現或疑慮。

**重要：這是給一般使用者看的說明，不是給工程師看的。**
絕對不可使用任何程式欄位名稱，例如：topics、relations、is_stable、followup_questions、v0、v1、context_map、search_seeds、confidence、delta、source_topic_id、target_topic_id 等。
請用自然語言描述你的發現，例如「我把這個方向的重要性降低了，因為新資料顯示它和研究核心關聯不大」或「研究結構目前還不夠穩定，需要繼續搜集資料」，而不要說「is_stable = False」或「修改了 confidence 欄位」。

**好的敘述範例**：
"等一下，我剛把德國模式歸類為社區共有，但新資料顯示它其實是政策主導的大型電廠模式，社區參與的部分很少。所以我把這個方向的重要性降低，因為它不再直接回答我們的研究問題。另一方面，新資料裡出現了一個有趣的台灣案例——彰化漁電共生的地主衝突——我把這個加進研究核心，值得深入追蹤..."

**語氣**：像一個邊做邊思考的研究夥伴，誠實說出你的發現和疑慮。

**長度**：3-5 句，重點說明最重要的修改及原因。
"""

        return prompt
