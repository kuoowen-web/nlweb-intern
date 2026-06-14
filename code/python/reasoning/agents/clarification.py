"""
Clarification Agent - Ambiguity resolution (stub for future implementation).
"""

from typing import Dict, Any
from reasoning.agents.base import BaseReasoningAgent


class ClarificationAgent(BaseReasoningAgent):
    """
    Clarification Agent for handling ambiguous queries (stub).

    This agent will be fully implemented when the frontend
    clarification flow is ready to handle user interactions.
    """

    def __init__(self, handler: Any, timeout: int = 60):  # Doubled: 30 -> 60 for GPT-5.1
        """
        Initialize Clarification Agent.

        Args:
            handler: Request handler with LLM configuration
            timeout: Timeout in seconds for LLM calls
        """
        super().__init__(
            handler=handler,
            agent_name="clarification",
            timeout=timeout,
            max_retries=3
        )

    async def generate_options(
        self,
        query: str,
        ambiguity_type: str = "time"
    ) -> Dict[str, Any]:
        """
        Generate clarification options for ambiguous queries.

        Args:
            query: User's ambiguous query
            ambiguity_type: Type of ambiguity (time | scope | entity)

        Returns:
            Dictionary with keys:
                - clarification_type: Type of clarification needed
                - context_hint: User-facing explanation
                - options: List of clarification options
                - fallback_suggestion: Alternative suggestion if none match
        """
        from datetime import datetime

        # Build clarification prompt based on PDF Pages 22-26
        current_date = datetime.now().strftime('%Y-%m-%d')

        prompt = f"""你是一個意圖澄清助手 (Clarification Agent)。

使用者的查詢：「{query}」

當前日期：{current_date}

歧義類型：{ambiguity_type}

請分析這個查詢並生成 2-4 個明確的選項供使用者選擇。

處理原則：
1. **時間歧義 (time)**：識別查詢中的人物/事件，查找其相關時間區間（任期、事件發生期間）。
2. **範圍歧義 (scope)**：查詢過於廣泛時，拆分成子主題。
3. **實體歧義 (entity)**：同名人物/組織需要區分時。

**重要**：不要猜測使用者意圖，你的工作是讓使用者自己選擇。

請返回 JSON 格式，包含以下欄位：
- clarification_type: "{ambiguity_type}"
- context_hint: 簡短說明為何需要澄清（顯示給使用者）
- options: 陣列，每個元素包含：
  - label: 選項顯示文字
  - intent: 系統內部使用的意圖標籤
  - time_range: (如果是時間歧義) {{"start": "YYYY-MM-DD", "end": "YYYY-MM-DD"}}
- fallback_suggestion: 若以上皆非，建議使用者如何重新描述

範例輸出格式請參考以下：
{{
  "clarification_type": "time",
  "context_hint": "蔡英文總統任期為 2016-2024 年，請問你想了解：",
  "options": [
    {{"label": "任內政策回顧 (2016-2024)", "intent": "historical", "time_range": {{"start": "2016-05-20", "end": "2024-05-19"}}}},
    {{"label": "卸任後的影響與評價 (2024至今)", "intent": "contemporary", "time_range": {{"start": "2024-05-20", "end": null}}}},
    {{"label": "完整時間軸 (政策演變)", "intent": "timeline", "time_range": null}}
  ],
  "fallback_suggestion": "或者你可以直接指定時間，例如「2022年的兩岸政策」"
}}

請針對上述查詢生成澄清選項。"""

        # Response structure for LLM
        response_structure = {
            "clarification_type": "string - time | scope | entity",
            "context_hint": "string - 簡短說明為何需要澄清",
            "options": [
                {
                    "label": "string - 選項顯示文字",
                    "intent": "string - 系統內部使用的意圖標籤",
                    "time_range": {
                        "start": "string (YYYY-MM-DD) or null",
                        "end": "string (YYYY-MM-DD) or null"
                    }
                }
            ],
            "fallback_suggestion": "string - 建議使用者如何重新描述"
        }

        try:
            # Call LLM with structured output
            from core.llm import ask_llm

            response = await ask_llm(
                prompt,
                response_structure,
                level="low",  # Use low-cost model for clarification (medium not available in ModelConfig)
                query_params=self.handler.query_params,
                max_length=1024  # Increase token limit for clarification options
            )

            if response and isinstance(response, dict):
                # Validate response has required fields
                if "clarification_type" in response and "options" in response:
                    self.logger.info(f"Generated {len(response.get('options', []))} clarification options")
                    return response
                else:
                    self.logger.warning("LLM response missing required fields")

            # Fallback: Return simple time-based options
            return self._generate_fallback_options(query, ambiguity_type, current_date)

        except Exception as e:
            self.logger.error(f"Clarification generation failed: {e}", exc_info=True)
            return self._generate_fallback_options(query, ambiguity_type, current_date)

    def _generate_fallback_options(self, query: str, ambiguity_type: str, current_date: str) -> Dict[str, Any]:
        """
        Generate fallback clarification options when LLM fails.

        Args:
            query: User's query
            ambiguity_type: Type of ambiguity
            current_date: Current date string

        Returns:
            Simple clarification options dict
        """
        if ambiguity_type == "time":
            return {
                "clarification_type": "time",
                "context_hint": "請選擇你想了解的時間範圍：",
                "options": [
                    {
                        "label": "最近一個月",
                        "intent": "recent_month",
                        "time_range": {"start": None, "end": current_date}
                    },
                    {
                        "label": "最近一年",
                        "intent": "recent_year",
                        "time_range": {"start": None, "end": current_date}
                    },
                    {
                        "label": "不限時間",
                        "intent": "all_time",
                        "time_range": None
                    }
                ],
                "fallback_suggestion": "或者你可以直接指定時間，例如「2024年的新聞」"
            }
        else:
            return {
                "clarification_type": ambiguity_type,
                "context_hint": f"查詢「{query}」需要更多資訊",
                "options": [
                    {"label": "繼續搜尋", "intent": "continue"}
                ],
                "fallback_suggestion": "請提供更具體的查詢"
            }
