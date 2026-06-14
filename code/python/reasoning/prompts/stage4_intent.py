"""Stage 4 intent classifier — distinguish format_spec vs structure_change。

把 user 在 Stage 4 checkpoint 的回覆分類成
format_spec / structure_change / mixed / auto_continue 四種意圖。

TypeAgent refactor (2026-05-19)：
- new_chapters / special_elements 為兩條獨立 typed channel，由 `type` Literal 強制
- **不**用 keyword heuristic 兜底（OQ-2 CEO 拍板）— 強化 few-shot drives correct first-parse
- prompt 含正面範例（五章 + 比較表 typed JSON）+ 反面範例（不應把比較表當 chapter）
"""


class Stage4IntentPromptBuilder:
    """組裝 LLM prompt — 把 user 的 Stage 4 reply 分類成意圖類型。"""

    def build_intent_classifier_prompt(self, user_message: str) -> str:
        return f"""你是一個意圖分類器（TypeAgent typed schema 紀律）。

背景：在 Live Research 流程中，Stage 4 是「格式確認」階段，
這個階段只能收「格式偏好」（字數、引用樣式、表格 / 列表偏好等），
不能收「結構性訴求」（章數、章節合併拆分、段落內容主題等）。
結構性訴求要在 Stage 1（研究結構提案）討論。

使用者訊息：
\"\"\"{user_message}\"\"\"

分類使用者意圖，回傳 JSON。**注意：new_chapters 與 special_elements 是兩條獨立
typed channel**（schema 強制 `type` literal），不可混淆。

- intent:
  * "format_spec": 純粹是格式偏好（例如「每段 500 字」「用 APA 引用」「加表格」）
  * "structure_change": 純粹是結構性訴求（例如「改成 5 章」「合併第 1+3 章」
    「拆分第 2 章」「全部重新規劃章節」「只留前 3 個主題」）
  * "mixed": 兩者都有（例如「改成 5 章、每段 500 字」）
  * "auto_continue": 表示「你決定」「都好」「隨便」這類交給系統處理

- format_spec_extracted:
  - intent="format_spec" 時，原文（user_message 本身）
  - intent="mixed" 時，只保留格式偏好部分（截掉結構訴求）
  - 其他情況留空字串

- special_elements: 從訊息中抽出「特殊格式元素」訴求（typed list, type ∈
  {{"table","list","chart","diagram","code_block"}}）。每個 element 為
  {{"type": <enum>, "target_chapter": <str>, "description": <str>}}：
  * type: enum literal，必須是上述五個值之一（schema 強制，不可寫其他字）
  * target_chapter: user 明確指定的章節名稱（例「結果與討論」「結論」「前言」）。
    若 user 沒明確指定章節（例「加表格」未說在哪章），填空字串 ""。
  * description: 對該 element 內容的繁體中文描述（例「5 國能源使用率比較」「三點政策建議」）。
  無特殊格式訴求時為空陣列 []。

### special_elements 抽取範例（typed JSON）

訊息：「最後加一個 5 國能源比較表」
→ special_elements=[{{"type":"table","target_chapter":"","description":"5 國能源比較"}}]
（user 沒明確章節名 → target_chapter 空字串）

訊息：「結果章節用列表呈現政策建議」
→ special_elements=[{{"type":"list","target_chapter":"結果","description":"政策建議列表"}}]

訊息：「在前言加一張產業概況圖」
→ special_elements=[{{"type":"chart","target_chapter":"前言","description":"產業概況圖"}}]

訊息：「APA 引用 + 每段 500 字」
→ special_elements=[]（純格式偏好，沒提任何 element）

- new_chapters: 當 intent="structure_change" / "mixed" 時，**抽出 user 訊息中的章節 outline**
  （typed list, 每個 entry 含 `type:"narrative_chapter"` literal）。
  每個 entry 為 {{"type":"narrative_chapter", "name": <str>, "description": <str>, "relevance": <enum>}}：
  * type: 固定 literal "narrative_chapter"（schema 強制，**只能寫此值**）
  * name = 章節標題（**user 原文**，不增刪、不重排、不改寫）
  * description = optional 該章描述
  * relevance = optional 'core' / 'supporting' / 'peripheral'

### new_chapters 抽取範例（typed JSON — 正面 + 反面）

✅ 正面範例（mixed：五章 + 比較表）：

  訊息：「五章：前言、國內案例、國外案例、結果與討論、結論，各章 1000 字，最後加 5 國能源比較表，APA」
  → intent="mixed"
  → new_chapters=[
      {{"type":"narrative_chapter","name":"前言"}},
      {{"type":"narrative_chapter","name":"國內案例"}},
      {{"type":"narrative_chapter","name":"國外案例"}},
      {{"type":"narrative_chapter","name":"結果與討論"}},
      {{"type":"narrative_chapter","name":"結論"}}
    ]
  → special_elements=[{{"type":"table","target_chapter":"","description":"5 國能源比較"}}]
  → format_spec_extracted="各章 1000 字"
  → citation_style_extracted="author_year"

❌ 反面範例（**絕對錯誤** — 不要這樣寫）：

  訊息同上「五章：... 最後加 5 國能源比較表」
  ❌ 錯誤 output：new_chapters 含 6 個 entry，第 6 個 name="5 國能源比較表"
  錯誤原因：「5 國能源比較表」是 special_element（type="table"），**不是**一個 narrative chapter。
  正確 channel 應是 special_elements，type "table"。
  schema 強制：new_chapters[].type 只能是 "narrative_chapter"，比較表 / 列表 / 圖
  屬於 special_elements 的 type enum。**這兩個 channel 互斥不重疊**。

✅ 正面範例（純 structure_change）：

  訊息：「改成 3 章 A / B / C」
  → intent="structure_change"
  → new_chapters=[
      {{"type":"narrative_chapter","name":"A"}},
      {{"type":"narrative_chapter","name":"B"}},
      {{"type":"narrative_chapter","name":"C"}}
    ]

✅ 正面範例（純 format_spec）：

  訊息：「每段 500 字、APA」
  → intent="format_spec"
  → new_chapters=[]

✅ 正面範例（mixed：章節 + element + 格式）：

  訊息：「改成 5 章，第三章用表格比較三家公司，結論用 bullet 列三點」
  → intent="mixed"
  → format_spec_extracted="第三章用表格比較三家公司，結論用 bullet 列三點"
  → new_chapters=[{{"type":"narrative_chapter","name":"第 1 章"}}, ...（user 沒列名 → LLM 不要硬造）]
  → special_elements=[
      {{"type":"table","target_chapter":"第三章","description":"三家公司比較"}},
      {{"type":"list","target_chapter":"結論","description":"三點"}}
    ]

- citation_style_extracted: 從訊息中抽出引用格式偏好（四選一 enum 或 null）：
  * "author_year"：user 提到 APA / (作者, 年份) / 哈佛 / 社會科學引用樣式
  * "numeric"：user 提到 [N] / 數字編號 / IEEE / 編號引用
  * "footnote"：user 提到 腳註 / footnote / 上標
  * "none"：user 明確說「不要引用」「不標來源」
  * null：user 沒提引用

### citation_style 抽取範例

訊息：「五章 / 7000 字 / 含表格 / APA」
→ citation_style_extracted="author_year"

訊息：「用 [1] 數字編號引用」
→ citation_style_extracted="numeric"

訊息：「腳註上標方式」
→ citation_style_extracted="footnote"

訊息：「每段 500 字」
→ citation_style_extracted=null（沒提引用）

只回傳 JSON，不要其他文字。"""

    def build_response_classifier_prompt(
        self,
        user_message: str,
        pending_reframe: bool,
        pending_format_confirmation: bool,
    ) -> str:
        """組 Stage 4 response classifier prompt — 產出 Stage4Response typed action。

        TypeAgent dispatcher 入口（取代舊 _parse_stage_4_intent / 自由 keyword 分流）。
        state context: pending_reframe / pending_format_confirmation 影響 confirm action 是否合法。
        """
        pending_block = (
            f"目前 state（dispatcher context）：\n"
            f"  pending_reframe = {pending_reframe}\n"
            f"  pending_format_confirmation = {pending_format_confirmation}\n"
        )
        return f"""你是 Stage 4 response classifier（TypeAgent typed action 紀律）。

Stage 4 是 Live Research 報告「格式 / 結構確認」階段。使用者剛剛收到 system
提案（reframe / format / 或 Stage 4 checkpoint 詢問），現在 reply 一句訊息。
請依下方 10-action enum 分類為 typed Stage4Response。

{pending_block}

使用者訊息：
\"\"\"{user_message}\"\"\"

分類 action（10 選 1）並回傳 Stage4Response JSON：

- "confirm_reframe": user 接受既有 reframe 提案（**pending_reframe=True 才合法**）
  → confirm_target='reframe'
- "confirm_format": user 接受 format dialog（**pending_format_confirmation=True 才合法**）
  → confirm_target='format'
- "confirm_both": 兩個 pending 都 confirm
  → confirm_target='both'
- "cancel_reframe": user 拒絕 reframe 提案，回到原結構
- "adjust_chapters": user 想改章節 outline（reframe 提案章節不對 / 加章 / 改名）
  → structural_content={{"new_chapters": [{{"type":"narrative_chapter","name":"..."}}, ...]}}
- "adjust_format": user 改格式偏好（字數 / 引用樣式 / etc）
  → format_content={{"format_spec_extracted": "...", "citation_style_extracted": "author_year|numeric|footnote|none|null", "target_word_count": <int or null>, "special_elements": []}}
- "add_special_element": user **只**補 table / list / chart 等 element（不改章節結構、不改格式 spec）
  → format_content={{"special_elements": [{{"type":"table|list|chart|diagram|code_block","target_chapter":"...","description":"..."}}, ...]}}
- "new_structure_request": user 提出全新章節結構（無 pending_reframe 時）
  → structural_content={{"new_chapters": [...]}}
- "auto_continue": user 講「你決定」「都好」「隨便」「系統決定就好」
- "unclear": 訊息模糊、無法判斷
  → clarifying_question = '繁體中文澄清問句'

### Few-shot 範例

case 1 — pending_reframe=True、user reply「OK」/「好」/「就這樣」：
  → {{"action":"confirm_reframe", "confirm_target":"reframe"}}

case 2 — pending_format_confirmation=True、user reply「好就這樣」/「沒問題」：
  → {{"action":"confirm_format", "confirm_target":"format"}}

case 3 — pending_format_confirmation=True、user reply「比較表加到結果與討論章節裡」：
  → {{"action":"add_special_element", "format_content":{{"special_elements":[
      {{"type":"table","target_chapter":"結果與討論","description":""}}
    ]}}}}

case 4 — 無 pending、user reply「改成 5 章 前言/國內/國外/結果討論/結論」：
  → {{"action":"new_structure_request", "structural_content":{{"new_chapters":[
      {{"type":"narrative_chapter","name":"前言"}},
      {{"type":"narrative_chapter","name":"國內案例"}},
      {{"type":"narrative_chapter","name":"國外案例"}},
      {{"type":"narrative_chapter","name":"結果與討論"}},
      {{"type":"narrative_chapter","name":"結論"}}
    ]}}}}

case 5 — pending_reframe=True、user reply「不對，第 3 章換成國外案例」：
  → {{"action":"adjust_chapters", "structural_content":{{"new_chapters":[...更新後章節列表...]}}}}

case 6 — user reply「每段 500 字、用 APA」：
  → {{"action":"adjust_format", "format_content":{{
      "format_spec_extracted":"每段 500 字",
      "citation_style_extracted":"author_year",
      "target_word_count":null,
      "special_elements":[]
    }}}}

case 6b — user reply「APA 引用格式，五千字左右」（典型 mixed format spec）：
  → {{"action":"adjust_format", "format_content":{{
      "format_spec_extracted":"五千字左右",
      "citation_style_extracted":"author_year",
      "target_word_count":5000,
      "special_elements":[]
    }}}}

case 6c — user reply「七千字、含表格、APA」：
  → {{"action":"adjust_format", "format_content":{{
      "format_spec_extracted":"含表格",
      "citation_style_extracted":"author_year",
      "target_word_count":7000,
      "special_elements":[
        {{"type":"table","target_chapter":"","description":""}}
      ]
    }}}}

case 6d — user reply「三千多字、APA」：
  → {{"action":"adjust_format", "format_content":{{
      "format_spec_extracted":"",
      "citation_style_extracted":"author_year",
      "target_word_count":3000,
      "special_elements":[]
    }}}}

### target_word_count 抽取規則

- user 講「五千字」「五千字左右」「約五千字」→ 5000
- user 講「七千字」「七千字上下」→ 7000
- user 講「三千多字」「至少三千字」→ 3000
- user 講「八千」（無單位但脈絡是字數）→ 8000
- user 講「每段 500 字」（per-paragraph，不是總字數）→ target_word_count=null
  （per-paragraph 偏好留在 format_spec_extracted）
- user 沒提字數 → null
- 中文數字（一千 / 兩千 / 三千 / 四千 / 五千 / 六千 / 七千 / 八千 / 九千 / 一萬）
  必須 parse 成阿拉伯數字 int

case 7 — user reply「你決定」/「都好」：
  → {{"action":"auto_continue"}}

case 8 — user 訊息模糊「我覺得這樣不太好」：
  → {{"action":"unclear", "clarifying_question":"想請你具體說明 — 是章節結構不對，還是格式偏好要改？"}}

case 9 — pending_format_confirmation=True、user reply「用 APA」（短 confirm-with-tweak）：
  → {{"action":"adjust_format", "format_content":{{
      "format_spec_extracted":"",
      "citation_style_extracted":"author_year",
      "target_word_count":null,
      "special_elements":[]
    }}, "clarifying_question":""}}

### clarifying_question 欄位規則（Blocker C 2026-05-19 修正）

- action='unclear' → clarifying_question 必填繁體中文問句（非空字串）
- 其他 9 個 action → clarifying_question 必填**空字串 ""**（不可填 null）
- **絕對不可** output `"clarifying_question": null` — schema 不允許 null，
  必須用 empty string `""`

只回傳 JSON，不要其他文字。"""
