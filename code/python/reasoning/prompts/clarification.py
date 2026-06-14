"""
Clarification and ambiguity detection prompt builder.
"""

from typing import Dict, Any, Optional
from datetime import datetime, timedelta


class ClarificationPromptBuilder:
    """Builder for ambiguity detection prompts."""

    def build_prompt(
        self,
        query: str,
        temporal_range: Optional[Dict[str, Any]] = None,
        has_time_ambiguity: bool = False,
    ) -> str:
        """
        Build complete ambiguity detection prompt.

        Args:
            query: User's search query
            temporal_range: Optional temporal context
            has_time_ambiguity: Whether time ambiguity was detected by rules

        Returns:
            Complete prompt string for LLM
        """
        prompt_parts = [
            self._core_instructions(query, temporal_range, has_time_ambiguity),
            self._ambiguity_types(),
            self._judgment_criteria(),
            self._output_format(),
            self._examples(),
        ]
        return "\n\n".join(prompt_parts)

    def _core_instructions(
        self,
        query: str,
        temporal_range: Optional[Dict],
        has_time_ambiguity: bool
    ) -> str:
        """Core instructions section."""
        time_status = "需要時間澄清" if has_time_ambiguity else "無時間歧義"
        return f"""你是新聞搜尋查詢歧義分析助手。分析以下查詢是否存在歧義，並生成**多維度並行澄清問題**。

**語境**：這是一個新聞搜尋系統，用戶想找相關新聞報導。

使用者查詢：「{query}」

時間解析結果：{temporal_range}
規則檢測：{time_status}

**核心指令 - 多維度並行檢測**：
我們希望在**單次交互**中解決所有可能的歧義。
如果查詢同時存在「時間不明」和「範圍過廣」的問題，請**務必同時返回**這兩個問題。
不要只返回其中一個，也不要分多次問。"""

    def _ambiguity_types(self) -> str:
        """Ambiguity types section."""
        return """請檢測以下三種歧義類型：

1. **時間歧義 (time)**：
   - 查詢涉及時間敏感的人物、政策、事件，但未指定時間範圍
   - 例如：「蔡英文的兩岸政策」（任期內 vs 卸任後？）
   - **CRITICAL**：對於「最新」「股價」「現況」等即時性查詢，**必須提供「今天」和「最近一周」選項**
   - **必須提供「全面回顧」選項**，讓用戶可以選擇不限定時間

2. **範圍歧義 (scope)**：
   - 查詢過於廣泛，涵蓋多個**新聞主題面向**（技術、政策、經濟、社會等）
   - **注意**：scope 是指大方向主題，不是功能細節或服務項目
   - 例如：「AI發展」（技術突破 vs 產業應用 vs 倫理問題？）
   - 例如：「momo科技」（財報營運 vs 產品服務 vs 市場競爭？）
   - **必須提供「全面了解」選項**，讓用戶可以選擇不限定範圍

3. **實體歧義 (entity)**：
   - 查詢中的實體有**多個不同的實體對象**（不同國家/組織/人物）
   - 例如：「晶片法案」（美國 CHIPS Act vs 歐盟晶片法案 - 這是兩個不同法案）
   - **注意**：如果是明確的專有名詞或品牌，即使有地區差異也不算歧義"""

    def _judgment_criteria(self) -> str:
        """Judgment criteria section."""
        return """**判斷標準**：
- **Time & Scope 經常並存**：像「蔡英文兩岸政策」同時有時間和範圍歧義，請同時列出
- **明確的專有名詞不澄清**：「台積電」、「ChatGPT」等（但「momo科技」有範圍歧義）
- **從新聞價值角度思考**：選項應該對應不同的新聞報導角度
- 每個問題提供 2-4 個具體選項 + 1 個「全面」選項
- 使用**對話式語氣**，問題要簡短清晰"""

    def _output_format(self) -> str:
        """Output format specification."""
        # Generate current date for time_range calculation hints
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        week_ago = (today - timedelta(days=7)).strftime('%Y-%m-%d')
        month_ago = (today - timedelta(days=30)).strftime('%Y-%m-%d')

        return f"""請返回 JSON 格式（**重要**：每個 option 必須包含 query_modifier 欄位，time 類型必須包含 time_range）：
{{
  "questions": [
    {{
      "clarification_type": "scope",
      "question": "AI發展涵蓋多個面向，你最想了解哪個部分？",
      "required": true,
      "options": [
        {{"label": "技術突破", "intent": "technology", "query_modifier": "技術突破面向"}},
        {{"label": "產業應用", "intent": "business", "query_modifier": "產業應用面向"}},
        {{"label": "倫理影響", "intent": "ethics", "query_modifier": "倫理影響面向"}},
        {{"label": "全面了解", "intent": "comprehensive", "query_modifier": "", "is_comprehensive": true}}
      ]
    }}
  ]
}}

**欄位說明**：
- `query_modifier`: 用於組合自然語言查詢的修飾詞（空字串表示全面性選項）
- `is_comprehensive`: 標記為全面性選項（選此項時會提高搜尋多元性）
- `required`: 所有問題都必須設為 true
- **注意**：你不需要提供 `question_id` 和 `option.id`，系統會自動生成

**CRITICAL - time 類型的特殊要求**：
當 clarification_type 為 "time" 時，每個 option **必須**包含 `time_range` 欄位：
- `time_range`: 結構化時間範圍，格式為 {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}
- 這是**強制約束**，用戶選擇後系統將嚴格遵守此時間範圍
- 今天的日期是：{today_str}
- **重要**：新聞只可能來自過去，不可能來自未來。若用戶提到月份但未指定年份，預設為最近的「過去」該月份。例如：現在是 {today_str}，「12月」= 2025 年 12 月（不是 2026 年 12 月）

時間選項的 time_range 計算參考：
- 「今天」: {{"start": "{today_str}", "end": "{today_str}"}}
- 「最近一周」: {{"start": "{week_ago}", "end": "{today_str}"}}
- 「最近一個月」: {{"start": "{month_ago}", "end": "{today_str}"}}
- 「全面回顧」(is_comprehensive=true): 不需要 time_range

如果沒有歧義，返回：
{{
  "questions": []
}}"""

    def _examples(self) -> str:
        """Example queries and responses with time_range for time type."""
        # Generate example dates
        today = datetime.now()
        today_str = today.strftime('%Y-%m-%d')
        week_ago = (today - timedelta(days=7)).strftime('%Y-%m-%d')

        return f"""範例 1 - **Time + Scope 並存**（最重要的案例）：
查詢：「蔡英文兩岸政策」
{{
  "questions": [
    {{
      "clarification_type": "time",
      "question": "請問是指哪個時期？",
      "required": true,
      "options": [
        {{"label": "今天", "intent": "today", "query_modifier": "今天", "time_range": {{"start": "{today_str}", "end": "{today_str}"}}}},
        {{"label": "最近一周", "intent": "week", "query_modifier": "最近一周", "time_range": {{"start": "{week_ago}", "end": "{today_str}"}}}},
        {{"label": "任期內(2016-2024)", "intent": "term_period", "query_modifier": "任期內", "time_range": {{"start": "2016-05-20", "end": "2024-05-19"}}}},
        {{"label": "卸任後(2024至今)", "intent": "post_term", "query_modifier": "卸任後", "time_range": {{"start": "2024-05-20", "end": "{today_str}"}}}},
        {{"label": "全面回顧", "intent": "comprehensive_time", "query_modifier": "", "is_comprehensive": true}}
      ]
    }},
    {{
      "clarification_type": "scope",
      "question": "關注哪個政策面向？",
      "required": true,
      "options": [
        {{"label": "軍事國防", "intent": "defense", "query_modifier": "軍事國防面向"}},
        {{"label": "外交關係", "intent": "diplomacy", "query_modifier": "外交關係面向"}},
        {{"label": "經貿交流", "intent": "economy", "query_modifier": "經貿交流面向"}},
        {{"label": "全面了解", "intent": "comprehensive_scope", "query_modifier": "", "is_comprehensive": true}}
      ]
    }}
  ]
}}

範例 2 - Scope 歧義：
查詢：「momo科技」
{{
  "questions": [
    {{
      "clarification_type": "scope",
      "question": "你想了解 momo (富邦媒) 的哪類新聞？",
      "required": true,
      "options": [
        {{"label": "營運財報與股價", "intent": "business", "query_modifier": "營運財報面向"}},
        {{"label": "產品服務發展", "intent": "product", "query_modifier": "產品服務面向"}},
        {{"label": "市場競爭態勢", "intent": "market", "query_modifier": "市場競爭面向"}},
        {{"label": "全面了解", "intent": "comprehensive", "query_modifier": "", "is_comprehensive": true}}
      ]
    }}
  ]
}}

範例 3 - Entity 歧義：
查詢：「晶片法案」
{{
  "questions": [
    {{
      "clarification_type": "entity",
      "question": "「晶片法案」在多個國家/地區都有，你想了解哪一個？",
      "required": true,
      "options": [
        {{"label": "美國 CHIPS Act", "intent": "us", "query_modifier": "美國"}},
        {{"label": "歐盟晶片法案", "intent": "eu", "query_modifier": "歐盟"}},
        {{"label": "台灣半導體政策", "intent": "taiwan", "query_modifier": "台灣"}}
      ]
    }}
  ]
}}

範例 4 - 無歧義（明確專有名詞）：
查詢：「台積電3nm製程良率」
{{
  "questions": []
}}
理由：台積電是明確專有名詞，且查詢已經具體到製程技術，不需要澄清。

範例 5 - 無歧義（查詢已經足夠具體）：
查詢：「美中貿易戰對台灣半導體產業的影響」
{{
  "questions": []
}}
理由：查詢已經明確指定了範圍（台灣半導體產業），不需要再問。

請針對上述查詢進行判斷。"""


def build_clarification_prompt(
    query: str,
    temporal_range: Optional[Dict[str, Any]] = None,
    has_time_ambiguity: bool = False,
) -> str:
    """
    Convenience function to build clarification prompt.

    Args:
        query: User's search query
        temporal_range: Optional temporal context
        has_time_ambiguity: Whether time ambiguity was detected

    Returns:
        Complete prompt string
    """
    builder = ClarificationPromptBuilder()
    return builder.build_prompt(query, temporal_range, has_time_ambiguity)
