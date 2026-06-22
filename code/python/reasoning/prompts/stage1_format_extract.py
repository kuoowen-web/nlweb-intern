"""Stage 1 初始 query 格式 spec 抽取 prompt builder。

把使用者**初始**研究問題中內嵌的格式需求（章節架構 / 字數 / 引用格式 /
特殊元素）抽成 InitialFormatSpec。保守紀律：沒明確指定的維度一律 null / 空，
不腦補、不推測。
"""


class Stage1FormatExtractPromptBuilder:
    """組裝初始格式抽取 prompt（單一 user query 文字 → InitialFormatSpec）。"""

    def build_extract_prompt(self, query: str) -> str:
        return f"""你是研究報告格式需求的抽取器。下面是使用者的研究委託原文。

請**只**抽出使用者**明確寫出來**的格式需求，輸出結構化欄位。

嚴格紀律：
- 只抽使用者原文明說的需求。**沒明確指定的維度一律留 null / 空 list**，
  絕對不要推測、不要腦補「常見作法」、不要自己決定章節或字數。
- chapters：使用者**明確列出章節標題**時才填（保留原文，不增刪不重排）。
  每章若 user 另為該章指定字數（「第一章 2000 字」）→ 填該章 word_target，否則 null。
  **使用者只說「分成五章」但沒給任何標題 → chapters 留空 list**（不要自己編標題；
  章數-only 不落 chapters，避免把編造的標題當成 user 拍板的章節 override）。
- total_word_count：使用者明說的整份總字數（「約7000字」→ 7000）。沒提 → null。
  注意：模糊語句「寫長一點 / 詳細一點 / 完整一點」**沒有具體數字** → total_word_count=null
  （不要腦補一個字數；模糊訴求不是「指定」）。
- citation_style：「APA」「（作者, 年份）」「哈佛」→ author_year；
  「[1]」「數字編號」「IEEE」→ numeric；「腳註」→ footnote；
  「不要引用」→ none；沒提 → null。
- special_elements：使用者明說要的表格 / 清單 / 圖表 / 流程圖 / 程式碼區塊。
  type ∈ table/list/chart/diagram/code_block；
  若指明放哪一章則填 target_chapter，否則留空字串。沒提 → 空 list。

使用者委託原文：
\"\"\"
{query}
\"\"\"
"""
