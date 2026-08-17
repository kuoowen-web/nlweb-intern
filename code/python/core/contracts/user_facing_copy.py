"""使用者可見故障文案的**唯一權威**（票 2026-08-14-a）。

## 為什麼需要這個檔

票 2026-08-03-b 建立的 `user_facing_error.py` 治的是「不外洩」——擋住
exception 的 str 直達螢幕。它**不管**留下來的那句話說得清不清楚。

2026-08-14 CEO 拍板：「如果問了問題，但有實質系統錯誤，那就要用非系統
術語、一般使用者看到且能接受的方法說明。不要的是，沒有 clarity 的訊息。」

⇒ 真正的軸是 clarity，不是「說不說」。**「不講技術細節」不必以「不提供
任何有用資訊」為代價**——現行文案兩者兼犯。反例是本檔取代的
`meta_narrative_guard` 舊文案「本次研究在整理階段發生內部處理問題」：
使用者讀完不知道自己的問題有沒有被理解、是暫時的還是這題永遠不行、
下一步該做什麼。

## 這個模組的職責邊界

**純字串常數與純字串組裝。禁止 import LLM / handler / state /
orchestrator / 任何有副作用的模組。** 同 `lr_copy.py` 的紀律第 4 條——
文案模組被任何層 import 都不該拖進依賴，這是它能當「單一事實源」的前提。

它與 `user_facing_error.py` 的分工：
- `user_facing_error.py`：輸入 exception，輸出「不含內部資訊」的字串。管**淨化**。
- 本模組：輸出「說得清楚」的字串。管**品質**。
兩者正交且互補，**不互相 import**（本模組零 import，那是它的硬約束）。

## ⚠ 能力邊界（誠實版，`coding-conventions` §5.5。R2 依 AR R1 SF-1 降級措辭）

1. **本模組擋不住「有人不用它」**——它的機械防線是
   `tests/unit/contracts/test_user_facing_copy.py` 的 jargon guard，
   而那條只掃**本檔**與已知消費點的 sink。有人在別的地方新寫一句
   帶術語的中文，這裡不會紅。
2. **三要素只有第三個（能做什麼）有測，而且那也只是弱代理，不是
   「機械可判」。** ⚠ R1 版把它寫成「機械可判」，AR 實跑證明高估了。
   ⚠ **R4 更新措辭（AR R3 Codex）**：R2/R3 這裡寫「擋不住含關鍵字但
   語意否定的句子」——**那句已經過期**，`is_actionable()` 的 v3 正是用
   子句切分與否定詞相對位置補這一類（12 個應拒句漏擋 0）。
   **現行的誠實邊界是：擋得住「零關鍵字的純現象描述」（票文反例正是
   這種）與「含關鍵字但語意否定」，擋不住否定詞表以外的構詞**——
   反諷（「你當然可以再試，只是那不會改變任何事」）、雙重否定、
   跨子句指代、條件句（「如果系統修好了，你可以再試」）。
   ✅ AR R3 造了 12 個這類句子，12/12 全繞過。**它們落在本模組宣告的
   邊界內，不是新缺陷；協調員裁定不收，理由是本判準只是測試裡的弱代理
   ——擋的是我方作者寫出爛文案，不是攻擊者，而作者不會反諷自己。**
   「發生了什麼／為什麼」則完全沒測——需要語意理解，測不到就不假裝
   測得到，那兩個靠 code review。
3. **`auth/auth_service.py` 只收了 A 類 17 條英文**（票 2026-08-14-a Task 7，
   協調員裁定擴 scope）。**B 類（中文但只有第一要素，約 6 條）另開票，
   C 級其餘不動** ⇒ **不得宣稱「全 repo user-facing 文案已統一」**。
   `webserver/routes/user_data.py`（14）/ `sessions.py`（4）**完全未動**。
4. 🔴 **`AUTH_SESSION_*` 三條的中文，使用者多半看不到**（R4 誠實記錄，
   AR R3 SF-3）。`:431` `:433` `:435` 走 `refresh_token()` → route 401 →
   前端 `auth-manager.js` 的 `refreshToken()` catch → `_handleAuthFailure()`，
   而 ✅ 親讀確認**該函式不收參數、零處讀 `e.message`，直接彈登入 modal**
   ⇒ 那三句被吞掉。**改中文的收益在 log 可讀性與 `/api/auth/refresh` 的
   直接呼叫者，不在使用者畫面。**
   ⚠ **記這條的目的是不讓下一張票以為它們已經生效。** 同一條路上的
   `AUTH_ACCOUNT_DEACTIVATED`（帳號停用）是真的該讓使用者看到卻同樣
   被吞——那是前端的問題，本票不收。
5. **前端側有防線但很窄**：`static/js/features/error-copy.js` 的判準有
   `node --test` 覆蓋（走 `scripts/test-frontend.sh`），但那只鎖住本票修好
   的行為，**擋不住「有人在別的 JS 檔新寫一句英文兜底文案」**。

## 語域選擇：本模組用「你」，不用「您」（R2 明文記錄，AR R1 N-3）

被取代的舊文案全部用「您」（「請重新表述您的問題」），`api.py` 現存中文
與 `to_user_facing_error` 也用「您」。本模組**刻意改用「你」**——語域比較
親近，符合讀豹的 brand voice。

⚠ **後果要知道**：本票改完後，同一個畫面上可能同時出現「你」（本模組）
與「您」（`to_user_facing_error` 出口）。這是**已知且接受的不一致**，
統一它是另一張票的事。

⛔ **不要當成筆誤「修正」回「您」**——那會把有意識的選擇改掉。要改是
產品決定，不是校對。

## 四種失敗情境，使用者能做的事不同

寫新文案前先判斷落在哪一格，**不要套同一個句式**：

- 檢索無結果 → 換關鍵字／放寬範圍（說「稍後再試」是錯的，再試一樣沒有）
- 暫時性失敗（逾時／額度）→ 稍後再試真的有用
- 回傳格式壞掉 → 重問一次通常就好
- 系統配置問題 → 使用者什麼都做不了，要說「不是你的問題」+ 給回報管道
  （代碼或聯絡方式），**絕對不可以說「請聯繫管理員」**（那是把系統問題
  丟回給使用者）

🔴 **判斷「這個下一步做不做得到」時，要追到使用者眼前那塊螢幕（R5 立，AR R5 BL-R5-1/-2）**

本模組的 auth 文案**連續三輪各被抓出一條「下一步在該情境下做不到」**，
而第三輪那條是修第二輪那條時自己寫進去的 ⇒ **「已修過兩次」不構成「這次寫對了」。**
前四輪的驗法只走到 service 層（讀呼叫點 + 前置檢查 → 推情境），**結構上看不到後兩條**。
寫任何一句「你可以去做 X」之前，逐項問完這四題：

0. 🔴 **這個錯誤在現行 schema / 控制流下，真的到得了嗎？（R6 補立，AR R6 BL-R6-1）**
   ——**先驗可達性，再辯措辭。** 三個 `AUTH_ORG_*` 連錯五輪的結構原因就是五輪都跳過
   這一題：`ON DELETE CASCADE` 讓 org row 消失時 membership 一併消失 ⇒ 三處出口上游的
   membership 守衛全部先擋 ⇒ **那三句話在 FK 正常執行時不會被說出來。**
   ⚠ **怎麼驗**：從 `raise` 往上讀到函式開頭，逐個守衛問「用的是不是同一個識別碼／
   同一張表」；再對 schema 查該表的 FK 語意。兩步都是幾行 `sed` 與 `grep`。
   ⚠ **答案是「不可達」時，處置不是刪文案**（可達性依賴別的模組的實作細節），
   而是**把承諾降級成與可達性相符的形態**——系統配置問題那一格：說「不是你的問題」
   + 給回報管道，不假裝使用者能自救；**同時不得宣稱「使用者現在看得到這句話」**。

1. **X 需要的東西，在這個錯誤發生的當下渲染得出來嗎？**
   ——`AUTH_ORG_NAME_UNAVAILABLE` 曾叫使用者「把組織名稱提供給……」，
   而觸發該錯誤的前提（org row 缺席）與前端顯示組織名的唯一來源
   （`list_user_orgs` 的 **INNER JOIN**）是同一筆 row ⇒ 他螢幕上沒有那個名稱。
2. **X 指涉的畫面真的存在嗎？** ——`AUTH_ORG_NOT_FOUND` 曾叫人「重新從組織列表進入」，
   而 `grep -rn "組織列表" static/ code/python/` **零命中**。
3. **讀到這句話的人，會不會就是這句話叫他去找的那個人？**
   ——呼叫點的前置守衛決定讀者身分。`admin_resend_activation` 的
   `只有管理員可以重寄啟用信` 守衛，讓「找你的組織管理員」變成「找你自己」。
   ⚠ 這一題同樣適用於 `_JARGON_EXEMPT` 的豁免理由（寫「只有 X 能解」之前先確認讀者不是 X）。

⚠ **「請稍後再試」不是萬用答案**：它是 `is_actionable` 詞表裡最泛用的詞，
**塞它是讓測試變綠最省力的路徑**，而那正是本檔開篇要治的「四種情境套同一句式」。
判準是「**造成這個錯誤的狀態，會不會因為時間經過而自己改變**」——不會就不准寫重試
（`test_user_facing_copy.py` 的 `_ZERO_RETRY_COPY` 負向鎖會擋）。
"""

# ─────────────────────────────────────────────────────────────
# 一、自由對話（free conversation）路徑
#     出口：methods/generate_answer.py::synthesize_free_conversation
#     上螢幕途徑（Task 0 T0-5 實測）：msg_type="nlws" → 前端 default merge
#     → chat.js:238 讀 chatData.answer → addChatMessage
# ─────────────────────────────────────────────────────────────

#: LLM 呼叫失敗或回空（逾時／額度／provider 掛掉）。
#: 情境＝暫時性失敗 ⇒ 「稍後再試」是真的有用的建議。
#: 取代舊文案「抱歉，系統暫時無法生成回應，請稍後再試。」——舊句只有
#: 第三要素，使用者不知道是自己問錯了還是系統壞了。
CHAT_LLM_UNAVAILABLE = (
    "我這次沒能整理出回答。這通常是暫時的狀況，稍後再問一次多半就好了。"
    "如果還是不行，可以把問題問得窄一點，或換個說法試試。"
)

#: LLM 有回應但缺 answer 欄位（回傳格式壞掉）。
#: 情境＝格式壞掉 ⇒ 重問通常就好，換個說法也有幫助。
#: 取代舊文案「抱歉，我無法生成回應。請重新表述您的問題。」——舊句只講
#: 「你重講」，沒說是系統這邊出了狀況，使用者會以為自己問得不好。
CHAT_MALFORMED_RESPONSE = (
    "我這次的回答沒能正常組出來，不是你問題的問題。"
    "麻煩再問一次，或換個說法描述你想知道的事。"
)

#: 自由對話整條路徑拋例外的兜底。
#: 情境＝不明失敗 ⇒ 只能給重試 + 回報代碼。
#: 取代舊文案「抱歉，處理您的問題時發生錯誤。請再試一次。」
CHAT_UNEXPECTED_FAILURE = (
    "處理你這個問題時出了狀況，我沒能把回答生出來。"
    "可以再問一次；如果連續幾次都這樣，換個說法或把範圍縮小通常會有幫助。"
)

# ─────────────────────────────────────────────────────────────
# 二、搜尋主線（synthesizeAnswer）
#     出口：methods/generate_answer.py::synthesizeAnswer
#     上螢幕途徑（T0-6）：unified ⇒ msg_type="answer" ⇒ search.js:1344
#     → renderAnswerProgressive → convertMarkdownToHtml（注意：非 escape）
# ─────────────────────────────────────────────────────────────

#: 檢索一筆都沒有。情境＝檢索無結果 ⇒ 「稍後再試」是錯的建議，
#: 要給的是「換關鍵字／放寬時間／換說法」。
#: 取代舊文案「抱歉，找不到與您問題相關的資訊。」——舊句零可行動資訊。
SEARCH_NO_RESULTS = (
    "這個問題我在收錄的報導裡找不到相關內容。"
    "可能是這個主題還沒被收錄，也可能是關鍵字跟報導用的說法不一樣。"
    "可以換個講法、放寬時間範圍，或改用更常見的名詞再找一次。"
)

#: 生成階段的系統性失敗（prompt 找不到／設定不對）。
#: 🔴 這格取代的是本票最嚴重的一條：「抱歉，無法生成回答。系統配置可能
#: 有問題，請聯繫管理員。」——它把系統自己的問題丟給使用者，還要他去找
#: 一個他根本不認識的「管理員」。
#: 情境＝系統配置問題 ⇒ 使用者什麼都做不了。誠實說「不是你的問題」、
#: 說「我們會看到」，並給可回報的代碼（代碼由 search_generation_broken()
#: 組進去，見下方函式）。
SEARCH_GENERATION_BROKEN = (
    "我這次沒能把回答寫出來，這是系統這邊的狀況，跟你問的問題無關。"
    "已經記錄下來了，我們會看到。你可以稍後再試一次；"
    "下面的報導清單仍然可以直接閱讀。"
)

#: synthesizeAnswer 整條路徑拋例外的兜底。
#: 取代舊文案「抱歉，生成回答時發生錯誤，請重新嘗試。」
SEARCH_UNEXPECTED_FAILURE = (
    "整理這次的回答時出了狀況，我沒能寫完。"
    "下面的報導清單仍然可以直接看；想要我重新整理的話，再問一次就行。"
)

# ─────────────────────────────────────────────────────────────
# 三、逐筆排序（rankItem）的卡片摘要兜底
#     出口：core/ranking.py::rankItem、methods/generate_answer.py::rankItem
#
#     🔴 可達性（Task 0 T0-C1，協調員 2026-08-14 **實跑推翻 plan 初版**）：
#     初版說「主路徑上到不了卡片」，依據只覆蓋 11 個 retriever 中的
#     postgres_client 一個。實測 grep -c description：**六個 retriever
#     完全不供應 description**（cwb_weather / tw_company / twse /
#     user_postgres / wikipedia / yfinance）。前端 `schema.description
#     || article.description` 在 schema 側為空時 fallback
#     ⇒ **這句英文有實際路徑上卡片，是正在漏，不是 defense-in-depth。**
#     ⚠ 尤其 user_postgres_provider＝私人文件檢索：使用者付費上傳自己的
#     資料，卡片上卻出現英文內部術語。
#     ❌ 不得把上述清單反向讀成「其餘五個安全」——grep -c 是存在性檢查
#     不是條件性檢查，「提到 description」不等於「所有回傳路徑都寫」。
# ─────────────────────────────────────────────────────────────

#: 取代 "LLM ranking failed"（英文 + 內部術語）。
#: 情境＝單筆評分失敗 ⇒ 使用者能做的是「自己點進去看」。
#: 刻意寫得短：它出現在卡片摘要的位置，長句會擠掉版面。
CARD_SUMMARY_UNAVAILABLE = "這則報導的摘要沒能整理出來，可以點開全文閱讀。"

#: 取代 whoRanking 的 "Failed to rank" 與 f"Error: {str(e)}"。
#: 後者是本票併收的 08-03-b 同一病種（Python 例外字串直達使用者）。
#: ⚠ 初稿寫「這個來源的說明沒能整理出來。」——plan 階段實跑三要素檢查時
#: 被自己的測試抓到只有第一要素（見 plan Task 0 的乾跑輸出）。補上第三要素。
SITE_SUMMARY_UNAVAILABLE = "這個來源的說明沒能整理出來，可以直接點開來源看看。"

# ─────────────────────────────────────────────────────────────
# 四、DR 報告被判定為污染時的整段替換文
#     出口：reasoning/meta_narrative_guard.py::sanitize_meta_narrative_draft
#
#     ⛔ 硬約束：這段會被塞進 WriterComposeOutput.final_report，
#     該欄位 min_length=200（schemas.py:78-82），
#     WriterComposeOutputEnhanced 繼承未覆寫（schemas_enhanced.py:230-235）。
#     跌破 200 是 crash 不是文案問題。鎖住這件事的是
#     test_user_facing_copy.py::test_meta_narrative_replacement_meets_writer_schema_min_length。
#     改文案時**不需要**改那條測試的數字——它鎖的是絕對下限，不是本文案的長度。
# ─────────────────────────────────────────────────────────────

#: 🔴 這條是票文 §一 點名的反例本體。舊文案「本次研究在整理階段發生內部
#: 處理問題」講了三句話卻零 clarity：使用者不知道自己的問題有沒有被理解、
#: 是暫時的還是這題永遠不行、下一步該做什麼。
#: 情境＝報告內容產出異常 ⇒ 重問通常有用，縮小範圍也有用；
#: 且要明說「不代表沒找到資料」，否則使用者會以為這題沒東西可查。
#:
#: ⚠ **長度安全邊際**：初稿實測 210 字元，只比 `min_length=200` 多 10——
#: 票 2026-08-14-c R4 已經因為「只多 1 字元、微調措辭就會跌破門檻重演
#: crash」被 AR 抓過一次（該票把文案墊到 236 拉開邊際）。本文案照同一個
#: 教訓加寫一段，**plan 階段實測 254 字元、邊際 54**（見 plan Task 0 T0-18）。
#: **不要為了精簡再砍回 210。**
DR_REPORT_SANITIZED = (
    "這個問題我沒能整理出一份可靠的報告。資料是有找到的，"
    "但把它們組成完整分析的這一步出了狀況，所以我不能把半成品當成結論給你——"
    "那樣的內容看起來像回答，實際上沒有依據，那比不給你答案更糟。\n\n"
    "你可以做的事：直接把同樣的問題再問一次，多數情況下重跑就正常了；"
    "或者把範圍縮小到具體的公司、事件或時間區間，"
    "題目越具體，我越容易整理出有依據的內容。舉例來說，"
    "與其問一個產業的整體趨勢，不如指名某家公司在某一年的動向。\n\n"
    "如果同一個問題連續幾次都這樣，那多半是這題本身有什麼地方卡住了，"
    "換個角度描述它會比重複重試更有機會。"
)

#: Critic 評論欄位被判定污染時的替換文。
#: ⚠ **R2 更正（AR R1 N-1）**：R1 版註解寫「沿用票 2026-08-14-c 既有文案，
#: 本票不改語意，只搬進本模組」——**描述不實**。實跑核對：舊文案 140 字元、
#: 本文案 114 字元，用詞也不同（本版用「未查核」這個說法，舊版沒有）。
#: 正確描述＝**重寫，語境與長度約束同舊版**。
#: 長度需求：critique min_length=50 / explanation min_length=20，
#: 本文案遠高於兩者。
CRITIC_FIELD_SANITIZED = (
    "這一項查核意見沒能正常產出，所以這裡看不到具體的評論內容。"
    "這不代表報告本身沒問題，只是查核這一步自己出了狀況。"
    "建議把這次的查核結果當成「未查核」看待——"
    "報告內容若有讓你覺得可疑的地方，請自行斟酌查證，或重新問一次讓系統再跑一遍。"
)

# ─────────────────────────────────────────────────────────────
# 五、組裝函式（R2 新增，AR R1 SF-6）
# ─────────────────────────────────────────────────────────────


def search_generation_broken(correlation_id: str | None = None) -> str:
    """SEARCH_GENERATION_BROKEN + 可回報代碼。

    ⚠ **R2 為什麼新增這個函式（AR R1 SF-6）**：plan R1 的設計總綱、該常數
    的註解、spec 段、測試的 actionable 清單**四處都承諾了「給回報代碼」**，
    但常數本體裡沒有代碼，而且 R1 的模組 docstring 預告了「本模組有
    f-string 組裝函式（correlation 提示）」，實作段卻只有常數——plan 自己
    內部不一致。

    這一格（系統配置問題）之所以非給代碼不可：使用者**什麼都做不了**，
    除了代碼之外他沒有任何可行動的東西。少了代碼，第三要素就退化成
    「稍後再試」——而配置沒改，再試一樣壞。那正是本 plan 開頭批評的
    「四種情境套同一句式」。

    ⛔ **設計約束：本函式收參數，不自己讀 contextvar。** 本模組的零 import
    是硬約束（見檔頭），讀 `correlation_id_var` 需要 import
    `misc.logger.logger`，會破掉它。由 caller 讀了傳進來——
    `methods/generate_answer.py` 本來就 import `misc.logger`，零額外依賴。

    ⚠ 拿不到 correlation id 時退化成純常數（`== SEARCH_GENERATION_BROKEN`），
    不是 silent fail：那條路徑上本來就沒有代碼可給，硬編一個假代碼更糟。
    """
    if correlation_id:
        return (
            f"{SEARCH_GENERATION_BROKEN}"
            f"如果要回報這次的狀況，附上這組代碼會快很多：{correlation_id}"
        )
    return SEARCH_GENERATION_BROKEN


# ─────────────────────────────────────────────────────────────
# 六、帳號與組織（auth）—— R2 新增（AR R1 B-6，協調員裁定擴 scope 收 A 類）
#
#     出口（Task 0 T0-20 實跑窮舉，11/11 全達使用者）：
#     auth/auth_service.py 的 raise ValueError
#     → webserver/routes/auth.py 的 except ValueError as e: {'error': str(e)}
#     → 前端。其中 validate_bootstrap_token 那三條更是渲染進 HTML 頁面
#       （auth.py:1109 `_setup_error_page(str(e))`）。
#
#     ⚠ 這批的情境判斷與搜尋線不同：多數是「使用者做錯了或狀態不對」，
#     不是「系統壞了」。所以第三要素給的是**具體的下一步**，不是「稍後再試」。
# ─────────────────────────────────────────────────────────────

#: 取代 "Invalid bootstrap token"
AUTH_BOOTSTRAP_TOKEN_INVALID = (
    "這個初始設定連結無效。請確認網址完整複製沒有截斷，"
    "或向提供連結給你的人再要一次。"
)

#: 取代 "Bootstrap token has already been used"
AUTH_BOOTSTRAP_TOKEN_USED = (
    "這個初始設定連結已經用過了，一條連結只能建立一個帳號。"
    "如果你已經建好帳號，直接登入就可以；還沒建好的話請再要一條新連結。"
)

#: 取代 "Bootstrap token has expired"
AUTH_BOOTSTRAP_TOKEN_EXPIRED = (
    "這個初始設定連結已經過期。請向提供連結給你的人再要一條新的。"
)

#: 取代 "Bootstrap token is required"
AUTH_BOOTSTRAP_TOKEN_REQUIRED = (
    "建立帳號需要一條初始設定連結。請改用你收到的完整連結再試一次。"
)

#: 取代 "Organization not found"（**兩處**：admin_create_user `:212` /
#: invite_member `:770`）
#:
#: ⚠ **R4 修正（AR R3 SF-4）：`:941` 那一處不共用本條，見下面的
#: `AUTH_ORG_NAME_UNAVAILABLE`。** R3 版讓三處共用，但 `:941` 的語意不同
#: ⇒ 這句給的下一步在那個情境下做不到。
#:
#: 🔴 **R6 整條改寫（AR R6 BL-R6-1）——改的不是「該說什麼」，是「這句話會不會被說出來」。**
#:
#: ✅ **本條的兩個出口在 FK 正常執行時都不可達**（協調員親讀 + 控制流模擬）：
#:   - `:206`  `raise ValueError("只有管理員可以建立使用者")`（admin_create_user）
#:   - `:765`  `raise ValueError("只有管理員可以邀請成員")`（invite_member）
#:     ⇒ **兩處守衛都用同一個 `org_id` 去查 `org_memberships`**，且都在 `:212` / `:770` 之前。
#:   - `org_memberships.org_id` 對 `organizations.id` 是 **`ON DELETE CASCADE`**
#:     （`auth_db.py:471` SQLite ／ `:681` PG ／ `alembic/versions/9df501ad9a13:103`，三處一致；
#:      SQLite 側 `auth_db.py:212` 有 `PRAGMA foreign_keys = ON`）
#:   ⇒ **org row 被刪 → membership 因 cascade 一併消失 → 守衛先擋 → 執行流到不了 `:212` / `:770`。**
#:
#: 🔴 **R5 的「請重新登入一次」因此是無效的下一步**（AR R6 runner 實證）：
#:   在 FK 生效的世界，重新登入後 membership 已經 cascade 消失 ⇒ 新 JWT 的
#:   `org_id = None` ⇒ route 層回「No organization context」400。**使用者不會回到
#:   正常狀態，只是換一種失敗。**
#:
#: ⇒ **本條的正確歸屬是四情境表的「系統配置問題」格**：使用者什麼都做不了，
#:   要說的是「不是你的問題」+ 給回報管道。**不再給任何自救動作。**
#:
#: ⛔ **不可達不等於該刪這個常數。** 判準同本 plan 對 `ranking.py` 的 defense-in-depth
#:   判定：**可達性依賴的是「別的模組（DB schema）現在剛好怎麼實作」，不是本模組
#:   自己的性質**。FK 哪天被 migration 拿掉 / raw SQL 繞過 / PG 側 `NOT VALID`，
#:   這句話就會上螢幕 ⇒ 那時它必須**已經**是寫對的。
#:   ⚠ **反過來也不得宣稱「使用者現在看得到這句話」**——它是兜底，不是正在漏的病。
#:
#: ⚠ **回報管道為什麼寫 email 不寫「意見回饋」**（AR R6 SF-R6-2）：那個入口是**主頁
#:   popover menu 的按鈕**（`news-search-prototype.html:157`），而本條兩個出口都渲染在
#:   org modal 這層 overlay 裡 ⇒ **按鈕此刻被蓋住**。
#:   🔴 **R7 降級措辭（AR R7 Codex SF-1）**：R6 原寫 `support@twdubao.com` 是
#:   「唯一不依賴 UI 可見性的管道」**過強**——email 字串本身仍要被渲染才看得到。
#:   **正確性質是「看得到／選得起／複製得走，但不可點」**：它勝過「意見回饋」按鈕的
#:   地方是**不依賴另一個入口此刻沒被 overlay 蓋住**，不是完全不依賴 UI。
#:
#: ⚠ **成因描述為什麼一併拿掉**：R5 版寫「可能是它已經被刪除」——✅ R6 實跑
#:   grep 全 `code/python/`，**沒有任何刪除或停用 org 的產品碼路徑** ⇒ 那個成因
#:   **沒有對應的產生者，是猜測不是資訊**。
AUTH_ORG_NOT_FOUND = (
    "讀不到這個組織的資料，這次的操作沒有完成。"
    "這不是你的問題，請回報給 support@twdubao.com。"
)

#: 取代 `admin_resend_activation` `:941` 的 "Organization not found"
#:
#: 🔴 **R4 新增（AR R3 SF-4），R5 整條重寫（AR R5 BL-R5-1），R6 再改一次。
#: 「下一步在該情境下做不到」這個病種在本票的第三次發作——
#: 而第三次是 R4 修第二次時自己寫出來的。**
#:
#: ✅ R5 親讀 `auth_service.py:936-941`：註解自陳「取得 org 名稱（email
#:   template 需要）」⇒ 語意是**「寄啟用信時查不到組織名」**，該 org row
#:   根本不在表裡 ⇒ 共用 `AUTH_ORG_NOT_FOUND` 在這裡做不到。
#:
#: 🔴 **R4 版的三件事全部做不到，逐條實跑證出（見 AR-history §R5-V1）：**
#:   ①「請把這個組織的名稱提供給……」⇒ **他拿不到那個名稱**（`list_user_orgs()`
#:     是 `INNER JOIN organizations`，row 缺席 ⇒ 組織名稱根本沒被寫進畫面）。
#:   ②「找你的組織管理員」⇒ **那就是他自己**（`:908` 的守衛
#:     `只有管理員可以重寄啟用信` 保證讀者 100% 是 org admin）。
#:   ③「請稍後再試一次」⇒ **org row 不會因為等待而回來**。
#:
#: 🔴 **R6 再改一次（AR R6 BL-R6-1 + SF-R6-2）——改的是兩件事：**
#:   **(1) 可達性。** `:908` 的守衛用**同一個 `org_id`** 查 `org_memberships`，
#:   而該欄對 `organizations.id` 是 **`ON DELETE CASCADE`** ⇒ **org row 消失時
#:   membership 一併消失 ⇒ `:908` 先擋 ⇒ `:941` 在 FK 正常執行時不可達**。
#:   ⚠ 上面 ①②③ 三條 R5 的論證仍然成立（它們講「假如使用者讀到這句話」）；
#:     R6 補的是更前面一層：**在現行 schema 下他讀不到**。
#:   **(2) 回報管道只給了動詞沒給位置。** R5 寫「請直接回報」，只做到四情境表
#:   要求的前半 ⇒ R6 改指向 `support@twdubao.com`。
#:
#: ⛔ **不可達不等於該刪常數**、⚠ **也不得宣稱使用者現在看得到這句話**（同上方常數）。
#:
#: ⚠ **長度取捨（SF-R5-1 vs SF-R6-2 的正面衝突，誠實記下）**：消費端是
#:   `auth-ui.js:214-247` 的 `#orgInviteFeedback`——**13px 窄提示條、3 秒後 `display:none`**
#:   （`static/news-search.css:6399-6403`），同位置其他文案 4-16 字。
#:   R5 為此壓到 40 字；**R6 的 52 字比它長，但其中 19 字是 email**——email 是掃視目標
#:   不是閱讀目標。⇒ **刻意取捨：寧可長 12 字，也不要給一個他找不到入口的「請直接回報」。**
#:   ⚠ **渲染層仍然沒修**（3 秒 / 13px 的約束還在），那張票在 Task 6 Step 4 的另開票清單。
#:   🔴 **R7 補明適用範圍（協調員親驗）**：**3 秒消失只適用本條這個消費點**
#:   （`auth-ui.js` 的重寄啟用信路徑，兩條分支都有 `setTimeout(..., 3000)`）。
#:   **上方 `AUTH_ORG_NOT_FOUND` 走的 `:770` 邀請成員路徑是另一個寫入點**
#:   （`news-search.js:1274,1292-1295`），**只設 `textContent` + `display='block'`、
#:   全段無 `setTimeout` ⇒ 常駐顯示** ⇒ **那一格沒有長度壓力，別把本條的約束套過去。**
AUTH_ORG_NAME_UNAVAILABLE = (
    "讀不到這個組織的資料，啟用信沒有寄出。"
    "這不是你的問題，請回報給 support@twdubao.com。"
)

#: 取代 "Invalid refresh token"
AUTH_SESSION_INVALID = (
    "你的登入狀態無法驗證，可能是在別的裝置登出過。請重新登入一次。"
)

#: 取代 "Refresh token has been revoked"
AUTH_SESSION_REVOKED = (
    "這個登入階段已經被結束了，通常是因為在別的地方登出或改過密碼。"
    "請重新登入一次。"
)

#: 取代 "Refresh token expired"
AUTH_SESSION_EXPIRED = (
    "你已經有一段時間沒有操作，登入狀態過期了。請重新登入一次。"
)

#: 取代 "Account is deactivated"
#: ⚠ 這條用「請聯絡你的組織管理員」是**對的**——企業版帳號停用確實只有
#: 該組織管理員能解，那是使用者真正可行動的下一步。這與
#: generate_answer.py:930 那句「系統配置可能有問題，請聯繫管理員」
#: 完全不同（後者是把系統自己的 bug 丟給使用者）。判準是「使用者照做
#: 有沒有用」，不是字面。
#: ⚠ 但因此它會命中 jargon guard 的「管理員」——本模組的 guard 只掃本檔，
#: 所以必須把本條列進 _JARGON_EXEMPT（見下方），並附這段理由。
AUTH_ACCOUNT_DEACTIVATED = (
    "這個帳號目前已停用，所以沒辦法登入。"
    "請聯絡你的組織管理員重新啟用；只有他們能解除這個狀態。"
)

#: 取代 "User not found"（change_password `:548`）
#:
#: ⚠ **R3 修正（AR R2 in-house SF-d）**：R2 版寫「找不到這個帳號，可能是它
#: 已經被刪除。**請重新登入一次**……」——**下一步給錯了**，而且錯的方式
#: 正是本票要治的病（四種情境套同一句式）。
#:
#: ✅ R3 親讀 `auth_service.py:543-548` 與 route 側 `routes/auth.py:255`：
#:   查詢是 `WHERE id = ? AND is_active = ?`（`True`），呼叫者是
#:   `user_info['id']`——**已通過驗證、此刻正登入著的使用者在改自己的密碼**。
#:   ⇒ 三種真實觸發情境是：①`is_active = False`（帳號被停用）
#:     ②`password_hash IS NULL`（帳號建了但還沒完成啟用）③帳號真的被刪
#:   使用者**此刻正登入著**，叫他「重新登入一次」在情境 ①② 下必然失敗
#:   ⇒ 第一個下一步是無效建議。而且最可能的實際成因（停用）被寫成了
#:   最不可能的成因（刪除）。
#:
#: R3 改法：主因改成狀態問題，**把唯一有效的下一步放前面**。
AUTH_USER_NOT_FOUND = (
    "這個帳號目前無法變更密碼，可能是它已被停用或還沒完成啟用。"
    "請聯絡你的組織管理員確認帳號狀態。"
)

#: 取代 "User not found in organization"（三處：set_user_active /
#: delete_user / change_member_role）
AUTH_MEMBER_NOT_IN_ORG = (
    "這位成員已經不在這個組織裡了，可能剛剛被其他管理者移除。"
    "請重新整理成員列表再確認一次。"
)

#: 取代 "role must be 'admin' or 'member'"
#: ⚠ 原文洩漏內部欄位取值（同時撞到票 08-03-b 的「不外洩」維度），
#: 新文案改講使用者在畫面上看得到的東西。
AUTH_ROLE_INVALID = (
    "這個權限身分不是有效的選項。請從成員設定畫面上的下拉選單重新選一次。"
)

#: 取代 "Not a member of this organization"
AUTH_NOT_A_MEMBER = (
    "你不是這個組織的成員，所以看不到它的成員列表。"
    "如果你認為這是錯的，請聯絡該組織的管理員邀請你加入。"
)

# ─────────────────────────────────────────────────────────────
# 註冊表（測試用）
# ─────────────────────────────────────────────────────────────

#: 不屬於「故障文案」、不需跑三要素檢查的常數名。
#: 目前為空集合；未來若加入標題／前綴類常數，加在這裡並附理由。
_NOT_FAILURE_COPY: set = set()

#: jargon guard 的**具名**豁免（(常數名, 被豁免的詞) 對，不是整條放行）。
#: 每一條都必須寫理由，且理由必須是「該詞在這個語境對使用者是可行動
#: 資訊」，不是「這樣比較好寫」。
#: ⛔ 不得用來放行「系統自己的 bug 丟給使用者」那類句子——那正是本票
#: 要治的病（generate_answer.py:930）。判準是**使用者照做有沒有用**。
#:
#: ⚠ 豁免必須**逐詞**而非逐條：`AUTH_ACCOUNT_DEACTIVATED` 豁免的只有
#: 「管理員」，它若哪天多寫了「系統配置」照樣要紅。
#:
#: 🔴 **R5 刪除第 4 條（AR R5 BL-R5-1）**：R4 為 `AUTH_ORG_NAME_UNAVAILABLE`
#: 寫的豁免理由是「只有管理員能查那筆 org row 的狀態」，而**那個論證在該呼叫點
#: 自我否定**——`:908` 的守衛保證讀者本身就是 org admin。R5 改掉文案後
#: 該條不再含「管理員」⇒ **豁免回到 3 條**。
#:
#: ⚠ **這一格的教訓值得記**：豁免理由寫的是「只有 X 能解」時，要先問
#: **「讀到這句話的人會不會就是 X」**——呼叫點的前置守衛決定了讀者身分。
_JARGON_EXEMPT = {
    # 企業版帳號停用只有該組織管理員能解 ⇒ 這句給的是使用者真正的下一步。
    # ✅ 讀者不是管理員：`:441` 走登入路徑，被停用的是一般帳號。
    ("AUTH_ACCOUNT_DEACTIVATED", "管理員"),
    # 帳號查無此人，重新登入不行時只有管理員能確認帳號狀態。
    # ✅ 讀者不是管理員：`:548` 是 change_password，呼叫者是一般 member。
    ("AUTH_USER_NOT_FOUND", "管理員"),
    # 不是組織成員 ⇒ 唯一能改變這件事的人就是該組織管理員。
    # ✅ 讀者不是管理員：`:876` 的守衛只檢查 membership 存在，讀到這句的人
    #    根本不在該組織裡（R5 逐條複核，見 AR-history §R5-V4）。
    ("AUTH_NOT_A_MEMBER", "管理員"),
}

#: 全部故障文案的註冊表。測試靠它逐條跑三要素檢查；
#: test_all_failure_copy_is_registered 反過來確保沒有常數漏註冊。
#:
#: ⛔ **Task 7 Step 3 不需要再加一次**——那一步只需確認本表已含這 14 條。
#: 兩處都加會變成重複維護，兩份會漂。
ALL_FAILURE_COPY = {
    # ── 搜尋 / 對話 / DR / 卡片線（10 條）──
    "CHAT_LLM_UNAVAILABLE": CHAT_LLM_UNAVAILABLE,
    "CHAT_MALFORMED_RESPONSE": CHAT_MALFORMED_RESPONSE,
    "CHAT_UNEXPECTED_FAILURE": CHAT_UNEXPECTED_FAILURE,
    "SEARCH_NO_RESULTS": SEARCH_NO_RESULTS,
    "SEARCH_GENERATION_BROKEN": SEARCH_GENERATION_BROKEN,
    "SEARCH_UNEXPECTED_FAILURE": SEARCH_UNEXPECTED_FAILURE,
    "CARD_SUMMARY_UNAVAILABLE": CARD_SUMMARY_UNAVAILABLE,
    "SITE_SUMMARY_UNAVAILABLE": SITE_SUMMARY_UNAVAILABLE,
    "DR_REPORT_SANITIZED": DR_REPORT_SANITIZED,
    "CRITIC_FIELD_SANITIZED": CRITIC_FIELD_SANITIZED,
    # ── 帳號與組織線（14 條，票 2026-08-14-a Task 7）──
    "AUTH_BOOTSTRAP_TOKEN_INVALID": AUTH_BOOTSTRAP_TOKEN_INVALID,
    "AUTH_BOOTSTRAP_TOKEN_USED": AUTH_BOOTSTRAP_TOKEN_USED,
    "AUTH_BOOTSTRAP_TOKEN_EXPIRED": AUTH_BOOTSTRAP_TOKEN_EXPIRED,
    "AUTH_BOOTSTRAP_TOKEN_REQUIRED": AUTH_BOOTSTRAP_TOKEN_REQUIRED,
    "AUTH_ORG_NOT_FOUND": AUTH_ORG_NOT_FOUND,
    "AUTH_ORG_NAME_UNAVAILABLE": AUTH_ORG_NAME_UNAVAILABLE,   # R4 新增（SF-4）
    "AUTH_SESSION_INVALID": AUTH_SESSION_INVALID,
    "AUTH_SESSION_REVOKED": AUTH_SESSION_REVOKED,
    "AUTH_SESSION_EXPIRED": AUTH_SESSION_EXPIRED,
    "AUTH_ACCOUNT_DEACTIVATED": AUTH_ACCOUNT_DEACTIVATED,
    "AUTH_USER_NOT_FOUND": AUTH_USER_NOT_FOUND,
    "AUTH_MEMBER_NOT_IN_ORG": AUTH_MEMBER_NOT_IN_ORG,
    "AUTH_ROLE_INVALID": AUTH_ROLE_INVALID,
    "AUTH_NOT_A_MEMBER": AUTH_NOT_A_MEMBER,
}
