"""
Style Analysis Prompt Builder.

Builds the prompt for Stage 3 of Live Research:
analysing a user-provided writing sample to extract actionable style features.
"""

from core.prompts import generate_boundary_token, wrap_content_with_boundary


class StyleAnalysisPromptBuilder:
    """
    建構 Style Analysis 提示詞。

    單一公開方法：build_style_analysis_prompt(writing_sample)
    從使用者提供的寫作範本中提取可具體指導 Writer Agent 的文筆特徵。
    """

    def build_style_analysis_prompt(self, writing_sample: str) -> str:
        """
        建構文筆分析提示詞。

        Args:
            writing_sample: 使用者提供的寫作範本文字。

        Returns:
            完整的系統提示詞字串。
        """
        # 以 boundary token 包裹寫作範本，防止間接注入攻擊
        boundary = generate_boundary_token()
        isolated_sample = wrap_content_with_boundary(writing_sample, boundary)

        prompt = f"""## 角色定義

你是文筆分析專家。你的任務是從使用者提供的寫作範本中，提取可以具體指導 AI 寫作的文筆特徵。

---

## 先判斷輸入本質（重要守門）

下方「寫作範本」區塊裡的文字，**不一定真的是一段寫作範本**。使用者可能誤把以下內容貼進來：
- **調整指令**：對先前分析的微調訴求，如「語氣再生動一點」「句子短一點」「多用數據」。
- **流程 / meta 指令**：如「用預設就好」「跳過」「幫我決定」。
- **閒聊 / 提問**：與提供範本無關的對話。

判斷規則：
- 若這是一段**可供分析文筆的實際文章 / 段落 / 範本**（即使較短）→ 視為寫作範本，正常抽特徵，
  並在輸出 `input_is_writing_sample` 設為 `true`。
- 若這其實是**一句調整指令 / 流程指令 / 閒聊**，而**不是**可供分析的寫作範本 →
  **不要**勉強把這句話當範本硬抽特徵。請在輸出 `input_is_writing_sample` 設為 `false`，
  features 仍輸出至少 1 個（內容可為通用占位，下游會忽略），其餘欄位照填即可。

預設立場：**絕大多數輸入是真的範本**。只有當你**相當確定**這不是可供分析的寫作範本時才設 `false`，
避免把正常但較短的範本誤判為指令。

---

## 寫作範本

{isolated_sample}

---

## 分析維度

請從以下維度分析：

1. **句式結構**：平均句長、長短句交替節奏、是否使用破折號/省略號/引號等
2. **用詞層次**：專業術語 vs 白話文比例、是否使用比喻、是否有口語化表達
3. **段落節奏**：段落平均長度、是否有「先總結後展開」或「先鋪陳後結論」的模式
4. **論證風格**：偏好歸納（例子→結論）還是演繹（原則→推導）、是否大量使用數據
5. **語氣和立場**：客觀報告式、帶評論的分析式、還是敘事式；是否使用第一人稱
6. **引用習慣**：如何引入他人觀點、是直接引述還是間接轉述
7. **結構偏好**：是否使用小標題、列表、表格；段落之間如何轉接

---

## 特徵提取引導

每個特徵都要轉化成**具體的寫作指令**。這些指令必須是可操作的，不能是模糊描述。

**範例格式：**
- 觀察：「範本中句子平均 20 字，每 3 句短句後用 1 句長句過渡」
- 指令：「維持句子平均 20 字以內，每 3-4 句短句後用一句 30-40 字的長句銜接」

**禁止**輸出模糊的描述如「文風流暢」。**必須**輸出可操作的具體指令。

---

## 輸出規格

請以 StyleAnalysisOutput JSON schema 輸出，欄位說明如下：

- **features**（List[StyleFeature]，至少 3 個，最多 10 個）：從範本提取的文筆特徵列表。
  每個 StyleFeature 包含：
  - `dimension`（str）：分析維度名稱，例如「句式結構」、「用詞層次」
  - `observation`（str）：在範本中觀察到的具體現象
  - `instruction`（str）：給 Writer 遵循的可操作指令

- **overall_tone**（str）：整體語氣的一句話摘要，例如「學術嚴謹但不枯燥」

- **sample_quality_note**（str，可選）：若範本太短或有特殊限制，在此說明。
  例如：「範本文字較短（約 200 字），句式分析可能不夠代表性，建議使用者提供更長的範本。」
  若範本品質良好，留空即可。

- **citation_format**（enum，必填，四選一）：分析範本中的引用格式偏好，**必須**輸出以下離散值之一：
  - `"author_year"` → 範本使用 APA 風格 (作者, 年份)，例如：「(Smith, 2020) 指出…」、「(王, 2024)」
  - `"numeric"` → 範本使用數字編號 [N]，例如：「研究指出…[1][2]」（**預設選項**）
  - `"footnote"` → 範本使用腳註編號，例如：「研究指出…¹」、「…²」
  - `"none"` → 範本不使用任何引用標記，純敘述

  **重要**：請僅輸出 enum 的字串值（如 `"numeric"`），**不要**輸出「(作者, 年份)」這類描述格式的字串。
  Writer 會根據此 enum 自動選用對應的引用語法。若無法從範本判斷，請保留預設 `"numeric"`。

- **input_is_writing_sample**（bool，必填）：見上方「先判斷輸入本質」守門。
  `true` = 輸入是可分析的寫作範本；`false` = 輸入其實是調整指令 / 流程指令 / 閒聊。
  預設傾向 `true`，只有相當確定非範本時才 `false`。

**重要**：features 的 instruction 欄位必須是 Writer 可以直接執行的操作指令，而非描述性陳述。"""

        return prompt
