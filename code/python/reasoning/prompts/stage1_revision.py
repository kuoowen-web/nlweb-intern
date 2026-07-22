"""Stage 1 ContextMap revision intent parser prompt builder。

LLM 解析使用者對 Stage 1 ContextMap 提案的自然語言回覆，
轉成 confirm/adjust + mutation operations。
"""

from datetime import datetime

from reasoning.schemas_live import ContextMap


class Stage1RevisionPromptBuilder:
    """組裝 LLM prompt — 把 user 的 Stage 1 checkpoint reply
    解析成 confirm/adjust 意圖 + ContextMap mutation operations。
    """

    def build_intent_parse_prompt(
        self, user_message: str, context_map: ContextMap
    ) -> str:
        # Track E (sprint 2026-05-28): 注入 current_date 供 time_range_extracted
        # 抽取「最近三年」「過去 5 年」等相對表達使用。
        current_date = datetime.now().strftime("%Y-%m-%d")
        topic_lines = []
        relevance_label = {"core": "核心", "supporting": "輔助", "peripheral": "周邊"}
        for t in context_map.topics:
            topic_lines.append(
                f"[{t.topic_id}] {t.name}（{relevance_label.get(t.relevance, '')}）"
                f"— {t.description}"
            )
        topics_str = "\n".join(topic_lines)

        rel_lines = []
        name_map = {t.topic_id: t.name for t in context_map.topics}
        for r in context_map.relations[:10]:
            src_name = name_map.get(r.source_topic_id, "?")
            tgt_name = name_map.get(r.target_topic_id, "?")
            rel_lines.append(f"- {src_name} --{r.relation_type}--> {tgt_name}")
        relations_str = "\n".join(rel_lines) if rel_lines else "（無）"

        return f"""你是一個意圖分析器。使用者剛看到一份研究結構提案，正在回覆。

目前的研究結構（v{context_map.version}）：
研究問題：{context_map.research_question}
工作假設：{context_map.working_hypothesis or "（未指定）"}

議題清單（topic_id → name → relevance → description）：
{topics_str}

主要關係：
{relations_str}

使用者回覆：
\"\"\"{user_message}\"\"\"

請判斷 user 的意圖，回傳 JSON：

- action: "confirm"（使用者明確且只表達接受、沒有提出任何修改，例如：
              「OK」/ 「沒問題」/ 「你決定」/ 「直接用」/「就這樣」/ 「都好」/ 「結構很好，繼續」）
          ⚠️ 只有**整句只有接受、零修改內容**才是 confirm。若接受語之後還接了
          任何結構訴求（「……。請用這個架構：…」「……，改成三章…」「……。另外幫我合併…」），
          那是複合句 → 一律 "adjust"（見下方「複合句紀律」few-shot）。
          / "adjust"（使用者要求修改結構，例如：
              「合併第 1 個和第 3 個 topic」/ 「把 X 拆成兩個」/ 「刪掉 X」/
              「改名為 Y」/ 「新增一個 X 議題」/ 「X 應該是 core」/ 「合併 1+3」/
              「前面引言，然後國內外案例比較，結尾討論結論這樣」/
              「前言、國內案例、國外案例、結果討論、結論這五章」/
              「想寫成案例比較類型的，分前言、案例、討論、結論」）
          → 上述後三條為 **outline 列舉句型**（連接詞 / 頓號列舉 / 文體宣告），
            一律走 `reframe_structure` op_type，不要拆成多個 incremental ops。

- operations: 若 action="adjust"，列出 mutation 清單。每個 operation 用以下其中一種 op_type：

  * merge_topics: 合併多個 topic 成一個
      {{"op_type": "merge_topics", "source_topic_ids": ["topic_id_1", "topic_id_2"],
        "merged_name": "新議題名稱"}}
  * split_topic: 把一個 topic 拆成多個
      {{"op_type": "split_topic", "split_from_topic_id": "topic_id",
        "split_into": [
          {{"name": "新議題 A", "description": "...", "evidence_ids": []}},
          {{"name": "新議題 B", "description": "...", "evidence_ids": []}}
        ]}}
  * add_topic: 新增議題
      {{"op_type": "add_topic", "new_topic_name": "...",
        "new_topic_description": "...", "new_topic_relevance": "core|supporting|peripheral"}}
  * remove_topic: 刪除議題
      {{"op_type": "remove_topic", "target_topic_id": "topic_id"}}
  * rename_topic: 改名
      {{"op_type": "rename_topic", "target_topic_id": "topic_id", "new_name": "..."}}
  * change_relevance: 改核心程度
      {{"op_type": "change_relevance", "target_topic_id": "topic_id",
        "new_relevance": "core|supporting|peripheral"}}
  * change_description: 改 description
      {{"op_type": "change_description", "target_topic_id": "topic_id",
        "new_description": "..."}}
  * reframe_structure: **整體重組為新章節結構**（UX-9，detail-rich confirm）
      {{"op_type": "reframe_structure",
        "new_chapters": [
          {{"name": "第 1 章名稱", "description": "...", "relevance": "core", "word_target": 500}},
          {{"name": "第 2 章名稱", "description": "...", "relevance": "core", "word_target": 0}},
          ...
        ],
        "new_research_question": "（optional，整體研究問題若 user 也想改）",
        "proposal_markdown": "（必填，detail-rich confirm 用，格式見下方 D-6 spec）"
      }}
      └─ word_target：user 若為該章指定字數（「前言~500、國內~2500、國外~2500、
         結果討論~1000、結論~500」）就填對應整數；user 沒給該章字數 → 填 0。

    ┌─ ⚠️ 約束詞逐字保留鐵律（FIX-4 / Cayenne #6）─────────────────
    │ 生成每章 description 時，**必須把 user 原話中對該章的精確約束詞
    │ 逐字保留進 description**，禁止用通用、抽象的描述把它們抹掉。
    │ 需保留的約束詞類型（命中即必須出現在對應章 description）：
    │  - **案例 / 地名取捨**：如「拿掉智利」「不要智利案例」「排除日本」
    │    → description 必須含「（不納入智利案例）」之類逐字限制。
    │  - **相似性 / 選材標準**：如「與我國相似」「分屬不同能源」「規模相近」
    │    → description 必須逐字保留「與我國相似」「分屬不同能源」等 qualifier。
    │  - **具體性要求**：如「要寫明確地名」「要寫回饋金數字」「要寫法規名稱」
    │    → description 必須逐字保留「需寫出具體地名、回饋金數字、法規名稱」。
    │  - **聚焦 / 排除訴求**：如「聚焦個別案例揭露的問題」「不要抽象結論」
    │    → 逐字保留。
    │ 反例（**禁止**）：user 說「國外案例拿掉智利、要寫明確地名」，
    │  你卻生成「國際對照案例蒐集與分析」← user 的取捨與具體性要求被抹掉，
    │  下游 writer 拿不到 → 重蹈 Cayenne 必須重打三次的覆轍。
    │ 正例：description = "國際對照案例蒐集與分析（不納入智利案例；
    │  須寫出具體地名、回饋金數字與法規名稱，聚焦個別案例揭露的問題）"。
    │ 規則：原話 qualifier 字面照抄進 description 括號或子句，**不要**改寫成
    │  同義的通用詞；user 沒說的不要無中生有。
    └──────────────────────────────────────────────────────────────

    ┌─ Few-shot 完整 input → output 對照（Cayenne R1）────────────
    │ Input user_message:
    │   「我其實想寫成案例比較類型的，前面引言，然後國內外案例比較，
    │    結尾討論結論這樣。電力供需跟半導體那塊太細，我們先放放。」
    │
    │ Expected output JSON（reframe_structure，不要拆 remove+add）：
    │ {{
    │   "action": "adjust",
    │   "operations": [{{
    │     "op_type": "reframe_structure",
    │     "new_chapters": [
    │       {{"name": "引言", "description": "案例比較研究的背景與問題意識", "relevance": "core"}},
    │       {{"name": "國內案例", "description": "台灣相關案例蒐集與分析", "relevance": "core"}},
    │       {{"name": "國外案例", "description": "國際對照案例蒐集與分析", "relevance": "core"}},
    │       {{"name": "討論", "description": "國內外案例對照比較，提煉異同與啟示", "relevance": "core"}},
    │       {{"name": "結論", "description": "研究發現總結與後續建議", "relevance": "core"}}
    │     ],
    │     "new_research_question": "",
    │     "proposal_markdown": "## 我準備重組為 5 章：\\n### 第 1 章：引言（背景與問題意識）\\n### 第 2 章：國內案例\\n### 第 3 章：國外案例\\n### 第 4 章：討論\\n### 第 5 章：結論\\n\\n確認這個結構嗎？"
    │   }}],
    │   "summary": "整體重組為案例比較類型 5 章"
    │ }}
    │
    │ 注意：上方 proposal_markdown 是 **few-shot 精簡示意**（節省 token）。
    │ 實際 output 必須**完整 follow 下方 D-6 spec**（每章「預期內容」+
    │「包含資料」雙 bullet），不要照抄此精簡版。
    │
    │ Why reframe_structure 而非 remove+add：
    │ - user 用 outline 列舉句型（「前面 X，然後 Y，結尾 Z」連接詞 + 文體宣告）
    │ - 命中 D-5 訊號 4（outline 列舉句型）
    │ - 「電力供需太細，先放放」是局部排除，但整體訴求是「想寫成案例比較類型」整體重組
    │ - 拆成多個 remove + 多個 add 會失去 user 心智上的「整體重組」意圖
    └────────────────────────────────────────────────────────────

    ┌─ Few-shot 複合句紀律（讚美前綴 + mutation 訴求；Cayenne B1 2026-07-15）──
    │ user 常用「先讚美、再提訴求」的複合句。**句中只要含任何結構訴求，
    │ 一律 action="adjust"** —— 開頭的讚美/接受語（「結構很好」「方向就這樣」
    │ 「不錯」）只是禮貌前綴，不構成 confirm。
    │
    │ Input user_message（真實誤判案例）:
    │   「結構很好，方向就這樣。請用這個架構：三章——前言、國際案例分析、結論。」
    │ ❌ 錯誤 output：{{"action": "confirm"}} ← 被前綴「結構很好，方向就這樣」誤導，
    │    後半「請用這個架構：三章…」的重組訴求被整個丟棄（絕對禁止）
    │ ✅ 正確 output（頓號列舉 3 章 + 「請用這個架構」＝ D-5 訊號 4b → reframe_structure）:
    │ {{
    │   "action": "adjust",
    │   "operations": [{{
    │     "op_type": "reframe_structure",
    │     "new_chapters": [
    │       {{"name": "前言", "description": "研究背景與問題意識", "relevance": "core"}},
    │       {{"name": "國際案例分析", "description": "國際代表案例的衝突來源與治理策略分析", "relevance": "core"}},
    │       {{"name": "結論", "description": "研究發現總結與對台灣的啟示", "relevance": "core"}}
    │     ],
    │     "new_research_question": "",
    │     "proposal_markdown": "（依 D-6 spec 完整生成）"
    │   }}],
    │   "summary": "確認方向，並整體重組為三章"
    │ }}
    │
    │ 同型變體（都是 adjust，不是 confirm）：
    │   「不錯，就照這方向。把這些主題重組成三章：A、B、C」→ reframe_structure
    │   「很好。另外幫我把第 1 個和第 3 個 topic 合併」→ merge_topics（讚美 + incremental）
    │
    │ 對照（讚美-only、句尾無任何訴求 → 才是 confirm，不可矯枉過正）：
    │   「結構很好，繼續」→ {{"action": "confirm", "operations": []}}
    │   「結構很好，方向就這樣。」→ {{"action": "confirm", "operations": []}}
    └──────────────────────────────────────────────────────────────

  ┌─ Reframe vs Incremental 判斷 heuristic（D-5）─────────────────
  │ 當 user reply 含**以下任一**訊號時，選 reframe_structure：
  │ 1. user 列出 ≥ 3 個明確 chapter 名稱，且 ≥ 50% 不在現有 topic 清單中
  │ 2. user 用整體語氣：「整個」「整體」「大方向」「最後架構」
  │    「重新規劃」「改成 X 章」「全部重排」「重新整理為 N 章」
  │ 3. user 同時表達 research_question 方向 shift + 章節名稱
  │ 4. **outline 列舉句型**（命中以下任一 sub-pattern 即可，
  │    不需再過訊號 1 的「≥ 50% 不在現有 topic」量化條件）：
  │    4a. 連接詞列舉：「前面/前言 X，然後/接著 Y，結尾/最後 Z」
  │        — 例：「前面引言，然後國內外案例比較，結尾討論結論這樣」
  │    4b. 頓號 / 逗號列舉 ≥ 3 章節名 + 收斂語：
  │        「A、B、C 這 N 章」/「A、B、C 這幾章」/「共 N 章」/「N 章類型」
  │        — 例：「前言、國內案例、國外案例、結果討論、結論這五章」
  │    4c. 文體宣告 + 章節列舉：
  │        「想寫成 [文體 / 類型] 的 / 類型的，A、B、C」
  │        — 例：「想寫成案例比較類型的，分前言、案例、討論、結論」
  │
  │ 否則 → 用 incremental ops (remove / add / merge)。
  │
  │ **正面範例**（重要）：Cayenne R1 原文
  │   「我其實想寫成案例比較類型的，前面引言，然後國內外案例比較，
  │    結尾討論結論這樣。電力供需跟半導體那塊太細，我們先放放。」
  │ → 命中訊號 4a（前面 X，然後 Y，結尾 Z）+ 訊號 4c（文體宣告
  │   「想寫成案例比較類型的」+ 章節列舉）→ reframe_structure。
  │ **不要**拆成「remove 電力供需 + remove 半導體 + add 引言 + add 國外
  │ 案例…」這類多 incremental ops（會失去 user 心智上的「整體重組」意圖）。
  └──────────────────────────────────────────────────────────────

  ┌─ Reframe relevance default heuristic（D-3）───────────────────
  │ new_chapters[i].relevance 推斷規則（chapter name match 任一即套用）：
  │ - 前言 / 緒論 / 引言 / 概述 / 摘要 → "core"
  │ - 結論 / 結語 / 結尾 / 總結 → "core"
  │ - 方法 / 結果 / 討論 / 比較 / 分析 / 案例 → "core"
  │ - 背景 / 文獻 / 延伸 / 附錄 → "supporting"
  │ - **default → "core"**（Stage 2 BAB loop 只跑 core，預設全 core
  │   確保 user 列出的所有章節都會被寫入）
  └──────────────────────────────────────────────────────────────

  ┌─ proposal_markdown 必填內容規格（D-6 detail-rich proposal）────
  │ reframe_structure 必須在 proposal_markdown 欄位輸出以下結構
  │ 的繁體中文 markdown，**幫助 user 一眼判斷新結構是否合用**：
  │
  │ ## 我準備重組為 N 章：
  │
  │ ### 第 1 章：[chapter_name]
  │ - **預期內容**：[1-2 句描述本章主軸]
  │ - **包含資料**：
  │   - [既有 topic A 中相關的面向]
  │   - [可能補充的新角度]
  │
  │ ### 第 2 章：[chapter_name]
  │ （同 pattern）
  │
  │ ...
  │
  │ **整體研究問題**：[new_research_question 或 既有 research_question]
  │
  │ 確認這個結構嗎？或者哪一段要調整？
  │
  │ 規則：
  │ - 「包含資料」段若可對應到既有 topic，**就明列既有 topic 名稱**，
  │   讓 user 知道哪些既有議題會被吸收進新章節
  │ - 若新章節是全新角度（既有 topic 沒涵蓋），明寫「需新增蒐集」
  │ - 整體研究問題段：若 op 有提供 new_research_question 就用新的，
  │   否則沿用現有 research_question
  └──────────────────────────────────────────────────────────────

  紀律（三分支 — 嚴格依下表分類；互斥，同一 reply 只命中一條）：

  ┌─ 路徑 A（分支 A）：明確 mutation 訴求 ─────────────────────────────────
  │ user reply 含 mutation 動詞（合併/拆/刪/移除/改名/重命名/新增/加入/
  │   調整為/應該是 / merge / split / remove / delete / rename / add），
  │ 或有**具體章節名稱 / outline 列舉句型**（命中 D-5 任一訊號）
  │ → action="adjust" + operations=[具體 op] + clarifying_question=""
  │ 即使語氣溫和、看似只是「建議」也不能 classify 為 confirm
  │ **讚美/接受前綴不豁免**：「結構很好，……」「不錯，……」「方向就這樣。請……」
  │ 開頭的複合句，只要後半含 mutation 訴求 → 仍是分支 A（adjust），
  │ 禁止因前綴判 confirm（見上方複合句紀律 few-shot）
  │ topic_id 必須是上方清單中存在的 ID，不要編造
  │ 同一輪可以包含多個 operation（user 一次給多項建議時）
  │ reframe_structure 與 incremental ops 不可混用（見 D-5 heuristic）
  │ 若 user 訴求**抽象但有意願**（「結構大改」「全部重做」「整個重排」
  │ 無具體章節名）→ 仍歸路徑 A（分支 A），依語氣推斷 reframe 預設 N 章 outline
  │ 或 incremental，**不可** return `operations=[]`
  └─────────────────────────────────────────────────────────────

  ┌─ 路徑 B（分支 B）：純 confirm ────────────────────────────────────────
  │ user reply 完全沒提任何修改、純粹表達接受
  │ （「OK」「就這樣」「都好」「沒問題」「結構不錯，繼續」）
  │ → action="confirm" + operations=[] + clarifying_question=""
  └─────────────────────────────────────────────────────────────

  ┌─ 路徑 C（分支 C）：無法 mapping（vague / 看不懂 / 模糊）─────────────────
  │ user reply 有提到內容（不是純語助詞，也不是純接受），但**無法對應
  │ 到任一 op_type**，例如：
  │   - 「電力供需太細，先放放」（不確定是 remove 還是 change_relevance）
  │   - 「想寫案例比較類型」（不確定要 reframe 還是 add_topic）
  │   - 純語助詞「呃」「？？」「無」「不知道」「你看著辦」（完全無內容）
  │ 共通點：**不要硬生成 operations**，也**不要**回固定例句
  │ → action="adjust" + operations=[] + clarifying_question=<繁體中文問句>
  │
  │ clarifying_question 規格：
  │   - 必須是**繁體中文問句**（句尾「？」或「嗎」）
  │   - **針對 user 剛剛說的內容**具體追問，**禁止複製**下方範例的固定例句
  │     （禁止：「例如『把第 1 章合併』或『新增國際案例段落』」這類無關範例）
  │   - 目標：把 user 模糊訴求縮窄成 1-2 個可選方向，方便下一輪 mapping
  │   - 長度：1-3 句，不超過 80 字
  │
  │   正面範例 1（針對「電力供需太細，先放放」）：
  │     「你說『太細』是希望整段刪掉，還是把它降為輔助議題保留資料？」
  │   正面範例 2（針對「想寫案例比較類型」）：
  │     「案例比較是指國際案例 vs 台灣案例的對照嗎？還是不同政策方案的比較？
  │      預計要幾個案例？」
  │   正面範例 3（針對「呃」「？？」）：
  │     「目前的結構你看了之後，最不順眼的是哪一塊？或者哪一段想多看一點？」
  │
  │   反面範例（**不要**這樣寫）：
  │     「我沒看懂你的建議。可以再說一次嗎？例如『把第 1 章和第 3 章合併』
  │      或『新增國際案例段落』。」（複製固定例句、跟 user 訴求無關）
  └─────────────────────────────────────────────────────────────

- citation_style: user 在 reply 中**順帶**提到的引用格式偏好（即使主訴求是結構重組，
                  只要句中含引用格式關鍵字也要抽）。對照：
                    * 「APA」「（作者, 年份）」「哈佛」「author-year」→ "author_year"
                    * 「[1]」「數字編號」「IEEE」「編號引用」→ "numeric"
                    * 「腳註」「footnote」「上標」→ "footnote"
                    * 「不要引用」「不標來源」→ "none"
                    * 完全沒提引用格式 → null
- total_word_count: user 提到的**整份報告**總字數 budget（中文字數整數）。
                    「總共約 7000 字」「全文 7000 字上下」→ 7000；沒提 → null。
                    若 user 只給各章字數（放進 new_chapters[i].word_target）沒給總數 → null。

- time_range_extracted: 若 user reply 中**順帶提到時間訴求**（如「2024 後」、
  「最近三年」、「2020-2023」、「過去五年」、「2024 年到現在」），抽出時間範圍 dict：
    {{"start_date": "YYYY-MM-DD" 或 null, "end_date": "YYYY-MM-DD" 或 null,
      "raw_phrase": "user 原話片段", "user_selected": true}}
  若 user reply 沒提時間 → null

  抽取紀律（今天日期：{current_date}）：
  * 「2024 後」「2024 之後」「從 2024 開始」→ start_date="2024-01-01", end_date=null
  * 「2024」（單獨一年）→ start_date="2024-01-01", end_date="2024-12-31"
  * 「最近三年」「過去 3 年」→ start_date = 今天減 3 年（年份用 {current_date} 計算）, end_date=null
  * 「2020 到 2023」「2020-2023」→ start_date="2020-01-01", end_date="2023-12-31"
  * 「N 年前到現在」→ start_date = 今天減 N 年, end_date=null
  * user 沒提時間 → null
  user_selected 在 Stage 1 dialog 場景一律 true（user 主動講出時間訴求）。

  ┌─ Few-shot F-T1（純時間訴求 + 結構 confirm）─────────────────
  │ Input user_message:「結構 OK，但只看 2024 之後的就好」
  │ Expected output JSON:
  │ {{
  │   "action": "confirm",
  │   "operations": [],
  │   "summary": "確認結構，限定 2024 後資料",
  │   "clarifying_question": "",
  │   "citation_style": null,
  │   "total_word_count": null,
  │   "time_range_extracted": {{
  │     "start_date": "2024-01-01",
  │     "end_date": null,
  │     "raw_phrase": "2024 之後",
  │     "user_selected": true
  │   }}
  │ }}
  └────────────────────────────────────────────────

  ┌─ Few-shot F-T2（時間訴求 + reframe）──────────────────────
  │ Input user_message:「想重組為案例比較 5 章（前言、國內、國外、討論、結論），
  │                    只看 2020-2023」
  │ Expected output JSON（reframe + time_range_extracted）:
  │ {{
  │   "action": "adjust",
  │   "operations": [{{ "op_type": "reframe_structure", "new_chapters": [...],
  │                     "proposal_markdown": "...", "new_research_question": "" }}],
  │   "summary": "整體重組為 5 章 + 限定 2020-2023",
  │   "clarifying_question": "",
  │   "citation_style": null,
  │   "total_word_count": null,
  │   "time_range_extracted": {{
  │     "start_date": "2020-01-01",
  │     "end_date": "2023-12-31",
  │     "raw_phrase": "2020-2023",
  │     "user_selected": true
  │   }}
  │ }}
  └────────────────────────────────────────────────

- summary: 一句話繁體中文摘要 user 的訴求（會記錄到 revision_history）。
           路徑 C（分支 C） 時可摘要為「user 訴求模糊：<原話前 30 字>」。

- clarifying_question: 分支 C 必填繁體中文問句，分支 A/B 留空字串。
                      規格見上方分支 C 段。

只回傳 JSON，不要其他文字。"""
