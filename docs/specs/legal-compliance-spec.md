# Legal Compliance Spec（法律合規規格）

# ⚠️ ⚠️ ⚠️ PENDING — 草案，律師意見回來前不視為定案 ⚠️ ⚠️ ⚠️

> **本 spec 全文標示為 PENDING**。律師會議（預定 2026-04-15 後續一次性 follow-up，時程待定）尚未進行對 2026-04-30 ~ 2026-05-01 內部研究結果的逐項對表。
>
> **使用紀律**：
> - 本 spec 為 CEO（郭又華）+ Zoe（CTO）非專業人士之內部暫時推理，**不構成法律意見**。
> - 在律師正式書面意見回覆前，**不執行任何不可逆的產品變動**（例如刪除 source、改變授權架構、發出 ToS）。
> - 律師意見回覆後，逐條條目從 `pending` 升級為 `active`，並修改本 spec。
> - 每個章節結尾的 `**Status**: pending（待律師審視）` 標記為**強制保留**，律師會議前不可移除。
> - 行文用語為 **「初步推理建議：X，待律師確認」**，**不寫**「我們的策略是 X」。
>
> **必讀前置文件**（閱讀順序）：
> 1. `memory/project_legal_consultation.md` — 2026-04-15 律師會議結論與待議清單
> 2. `docs/legal/legal-research.md` — Synthesis of 國內判例 + 國際判例 + 業內 ToS（339 行）
> 3. `docs/in progress/discussions/legal-strategy-discussion.md` — 內部 CEO+Zoe 討論記錄
> 4. `docs/legal/legal-audit-crawler.md` — 7 個 source 的 robots.txt + ToS + paywall 風險清單
> 5. `memory/lessons-general.md` 法律段落（行 556-602，2026-05-01 IP 推理）
> 6. `docs/decisions.md` 行 657-689（5 條 pending legal 決策）

---

## 目錄

1. ⚠️ Pending 警告（檔案最前 + 每章末重複）
2. 法律框架概覽
3. 來源分類框架（初步推理）
4. 資料儲存乾淨度光譜（D-2026-05-01）
5. 對黑名單 source 的處理策略（pending）
6. 對 CC BY-NC-ND source 的處理（pending）
7. Aggregated 摘要 vs 個別摘要（pending）
8. AI Rephrase 不採用（pending）
9. 國際對標
10. 內部紀律
11. 律師會議待確認清單
12. 附錄：相關決策索引

---

## 1. ⚠️ Pending 警告

**本章節內容於律師意見正式書面回覆前，全文標示為 PENDING。**

本 spec 不是發布給對外客戶的法律文件，也不是工程上「立刻照做」的指令。它的角色是把 CEO + Zoe 的內部推理**結構化文件化**，供律師會議時逐項對表使用。

任何閱讀本 spec 的同事必須理解：

- **本文不構成法律意見**。CEO 與 Zoe 皆非執業律師。
- **本文的「初步推理建議」只是討論起點**，律師可能完全推翻或修正。
- 在律師正式意見前，產品設計、爬蟲行為、ToS 條款**不應**根據本 spec 做出**不可逆**的變動（例如刪除 DB 資料、停止爬取某 source、修改 robots.txt 行為）。
- 可逆的紀律性改動（例如記錄 robots.txt diff、不主動 push 偽裝 UA 至更激進路徑）可先做。

**Status**: pending（待律師審視）

---

## 2. 法律框架概覽

### 2.1 著作權法（中華民國）

讀豹的爬取 + 索引 + 報告生成行為，主要涉及著作權法以下條文（依風險高低）：

- **§22 重製權**：將他人著作以印刷、複印、錄音、錄影、攝影、筆錄或其他方法重複製作。
  - 對讀豹的對應：crawler 把 HTTP response 寫入 PG `articles.body` = 重製；indexing 把 body 切成 chunks 存入 `chunks.text` = 同份內容存兩遍，仍是重製。
  - **判例紅線**：蘋果 vs 自由（智財 108 民著訴 84，2019）— 重製比例 72.4% 即構成侵權。
- **§28 改作權**：以翻譯、改寫、編曲、改編、拍攝影片或其他方法就原著作另為創作。
  - 對讀豹的對應：AI rephrase chunk text 屬於改寫；LLM 生成的 aggregated 摘要若實質類似度高（語義保留率高）也可能構成改作。
- **§52 合理引用**：為報導、評論、教學、研究或其他正當目的之必要，在合理範圍內，得引用已公開發表之著作。
  - 對讀豹的對應：使用者透過讀豹做研究引用屬於 §52 範圍 — 但**主體論述**（讀豹是工具 vs 讀豹是行為人）仍待律師確認。
- **§65 合理使用判斷四要素**：(1) 利用之目的及性質 (2) 著作之性質 (3) 利用之質量及其在整個著作所占之比例 (4) 利用結果對著作潛在市場與現在價值之影響。
- **§91-2 意圖銷售而擅自重製**：刑事責任（Lawsnote 案被告 4 年徒刑的法源）。
- **§9 不受著作權保護的標的**：法令、公文、單純為傳達事實之新聞報導所作成之語文著作、依法令舉行之考試試題等。
  - 對讀豹的對應：純事實（「總統發表演說」）不受保護；具評論、分析、調查的新聞報導仍受保護。

### 2.2 公平交易法（公平法）§25

> 「除本法另有規定者外，事業亦不得為其他足以影響交易秩序之欺罔或顯失公平之行為。」

對讀豹的對應：「榨取他人努力成果」是 §25 的典型適用。Lawsnote 案中法院明文：「利用專業優勢非法取得競爭對手辛苦建置之資料」即構成 §25 違反。讀豹爬取媒體獨家報導的「事實」並用於 aggregated 摘要時，仍可能踩 §25。

### 2.3 CC license（合約性質）

CC（Creative Commons）授權是**合約性質的明示授權範圍**。違反 CC license 是 license breach，**不是**著作權侵權與 fair use 的攻防 — **沒有 fair use 抗辯空間**。

讀豹的相關 source：環境資訊中心 einfo 採 **CC BY-NC-ND 4.0**（姓名標示-非商業性-禁止改作）。

### 2.4 robots.txt（意圖證據效力）

robots.txt **不是法律文件**，但在法庭審理時被視為**站方意圖證據**。

- Lawsnote 案邏輯：「靠名稱閃過 robots.txt 黑名單」是**主動規避意圖**的證據。
- Bartz v Anthropic：即使訓練 LLM 屬於 transformative use，**盜版來源**的取得本身**獨立**構成侵權。
- 國際對標：法庭看「合理人是否應預期此網站不歡迎此種爬取」，不看技術細節（黑名單字串是否命中）。

**Status**: pending（待律師審視）

---

## 3. 來源分類框架（初步推理）

讀豹現有 7 個 source（依 `docs/legal/legal-audit-crawler.md`）：LTN, UDN, CNA, Chinatimes, einfo, ESG 今周刊, MOEA。

**初步推理建議**將每個 source 歸入以下四類，**待律師確認**分類標準：

### 3.1 白名單 source（公開資訊 / 已授權）

- **判準**：(a) 政府開放資料授權 (b) 已透過商業契約取得授權 (c) 著作權人明示允許商業使用。
- **目前對應**：
  - **MOEA**（經濟部）— 政府資料開放授權條款 v1.0，明文允許商業重製、改作、公開傳輸。**唯一明確合規**的 source。
- **初步推理建議**：可存原文 + 切 chunk + embedding，唯一義務是「標示來源」（UI / 引用時標明「資料來源：經濟部」）。**待律師確認**「政府資料開放授權」是否完全免除責任。

### 3.2 中性 source（無明示禁止）

- **判準**：(a) robots.txt 對 `User-agent: *` 寬鬆 (b) ToS 沒有明文禁止商業重製 / AI 訓練 (c) 無 paywall 或讀豹路徑未涉及 paywall 區。
- **目前對應**：（七個 source 中**沒有**完全符合此判準者，所有商業媒體 ToS 都有「禁止商業重製」條款）
- **初步推理建議**：理論上採 chunk + embedding + aggregated 摘要可主張 fair use（Google Books / Field v Google 防線）— 但**台灣法院尚未採用美式 transformative use 寬鬆標準**，Lawsnote 案排斥「爬蟲是普遍工具」抗辯。**待律師確認**台灣下「中性 source」是否真的存在。

### 3.3 黑名單 source（robots.txt 明示禁止 AI bot）

- **判準**：robots.txt header 或具名 disallow 顯示對 AI/LLM/商業爬取的明確抗拒意圖。
- **目前對應**：
  - **UDN**（聯合報系）— robots.txt header 明文「禁 LLM/ML/AI 及任何商業目的」+ 具名 disallow GPTBot/Claude/ClaudeBot；ToS 點名禁訓練 LLM。**最高風險**。
  - **Chinatimes**（中時）— robots.txt 列 30+ AI bot 黑名單（含 ClaudeBot、anthropic-ai、PerplexityBot、CCBot、Bytespider 等）。
  - 其他較弱訊號：CNA（disallow CCBot）、LTN/ESG（robots.txt 寬鬆但 ToS 明文禁商業重製）。
- **初步推理建議**：採 metadata-only（標題 + URL + 日期）收錄，不存 chunk text、不做 embedding。產品功能降級但法律乾淨度高。**待律師確認** metadata-only 是否真的安全。
- 詳見 §5。

### 3.4 CC BY-NC-ND source（license 明示禁止商業 + 改作）

- **判準**：CC license 條款明文 NC（非商業）+ ND（禁改作）。
- **目前對應**：
  - **einfo**（環境資訊中心）— CC BY-NC-ND 4.0。讀豹商業 SaaS = 商業使用 = 直接違反 license terms。對文章做 chunk + LLM 摘要 + 重新組合可能構成「改作」(ND 紅線)。
- **初步推理建議**：唯一合法路徑是「另行取得商業授權」（einfo 站方有「商用授權申請表」）或「不爬」。**不應**走 metadata-only / aggregated summary 等灰色路徑（license terms 明示禁止）。**待律師確認**。
- 詳見 §6。

**Status**: pending（待律師審視）

---

## 4. 資料儲存乾淨度光譜（D-2026-05-01）

對「禁止重製」的 source（robots.txt 明文禁止 / CC BY-NC-ND）的處理選項，**初步推理建議**按法律乾淨度排序如下（引用 D-2026-05-01「法律乾淨度光譜選擇：metadata > embedding > rephrase > 原文」）：

| 等級 | 方案 | §22 重製 | §28 改作 | 法律確定性 | 搜尋品質 |
|------|-------|---------|---------|----------|--------|
| 🟢 | **Metadata-only**（標題 + URL + 日期） | 無 | 無 | 確定無風險 — 書目資訊不受著作權保護 | 中（只剩標題搜尋） |
| 🟡 | **Embedding-only**（向量但不存文字） | 灰色 | 灰色 | 不確定 — 無台灣判例，學說有爭議 | 高（向量檢索完整保留） |
| 🔴 | **AI rephrase**（語義保留改寫） | 暫時重製 | 改作 | 雙重風險 | 高 |
| 🔴 | **存原文**（chunk text） | 重 | 無 | 確定有風險 | 最高 |

**搜尋品質代價反向**：原文 > rephrase > embedding > metadata。

**初步推理建議分層**（**待律師確認**）：
- **白名單 source**：存原文（已授權 / 公開資訊）。
- **中性 source**：存 chunk text + embedding（aggregated transformative 防線）。
- **黑名單 source**：metadata-only。
- **CC BY-NC-ND source**：另行取得商業授權，否則不爬。
- **AI rephrase**：**不採用任何 source**（詳見 §8）。

### 4.1 Embedding-only 的灰色狀態（特別說明）

Embedding 是高維浮點數向量（讀豹 1024D Qwen3 embedding）。從原文 → embedding 的轉換是**不可逆的**（無法從向量回推原文）。但：

- 著作權法 §22「重製」的學說有「暫時 vs 永久」爭議。Embedding 在計算過程中**暫時**載入原文到 GPU memory；計算完後**只存向量**。
- §28「改作」的「實質類似度」對 embedding 的適用性無台灣判例 — 向量本身不能讓人類閱讀，但語義保留率極高。
- 國際對標 NYT v OpenAI：OpenAI 論述「不存原文只存模型權重」**未被法庭接受** — 但 LLM 模型權重 vs RAG embedding 是不同層次（前者整合到參數，後者只是 lookup index）。
- Bartz v Anthropic：訓練 LLM = transformative，但**不法來源**獨立侵權。讀豹 RAG embedding 不是訓練，但「來源是否合法取得」仍是先決問題。

**初步推理建議**：embedding-only 適合作為「中性 source」的雙保險（已存原文時的法律緩衝），**不適合**作為「黑名單 source」的主要防線（一旦判決認為 embedding 是 §22 重製，metadata-only 才是真正乾淨）。**待律師確認** embedding 在台灣的法律狀態。

**Status**: pending（待律師審視）

---

## 5. 對黑名單 source 的處理策略（pending）

### 5.1 案例：中時（Chinatimes）

中時 robots.txt 列 30+ AI bot 黑名單（依 `docs/legal/legal-audit-crawler.md`）：

```
anthropic-ai, Claude-Web, ClaudeBot, GPTBot, PerplexityBot, CCBot,
AI2Bot, Amazonbot, Applebot-Extended, Bytespider, ChatGPT-User,
cohere-ai, Diffbot, DuckAssistBot, OAI-SearchBot, ...（共 30+）
```

但**沒列 DubaoBot**（讀豹尚未啟用 DubaoBot UA，目前偽裝 Chrome 120）。

**直覺推論**：「我不在黑名單，技術上能爬，所以合規」。

**Lawsnote 邏輯下的法律論述**（**初步推理**）：
- robots.txt 黑名單 30+ AI bot = 站方意圖明確：「不歡迎 AI bot 爬取」。
- 「合理人應預期此網站不歡迎 AI bot 爬取」 = 黑名單意圖證據成立。
- 「靠名稱閃過黑名單」 = 主動規避意圖的證據（Lawsnote 案邏輯下是加重事由，不是減輕事由）。
- robots.txt 不是法律文件，**但是站方意圖證據**。法庭看實質意圖，不看技術細節。

### 5.2 偽裝 UA 的法律意義

讀豹現狀：crawler 用 Chrome 120 UA（`code/python/crawler/core/settings.py:66`）。

**初步推理**：
- 用 Chrome UA 規避 robots.txt 不是「中立的 HTTP request」，是**主動偽裝**。
- 對 UDN（robots.txt header 明文禁 LLM/AI/商業）特別嚴重 — 站方已表達意圖，偽裝 UA = 知道意圖仍規避。
- Lawsnote 案邏輯：技術中立 ≠ 法律中立。

### 5.3 初步推理建議

**初步推理建議**（**待律師確認**）：

1. **UA 紀律**：將 crawler UA 改為 `DubaoBot/1.0 (+https://twdubao.com/about-bot)`（明示身份 + 聯絡資訊）。Google Books / Field v Google 的「good-faith」防線需要明示身份作為前提。
2. **黑名單 source 的處理**：採 metadata-only 收錄（標題 + URL + 日期），不存 chunk text、不做 embedding。
3. **不執行不可逆變動**：在律師意見前，不刪除既存於 PG `articles.body` 的內容（可逆變動），但**停止對黑名單 source 新增 chunk text**（可暫停 indexing pipeline 對 UDN/Chinatimes 的處理）。
4. **個人使用階段**：CEO 拍板「現在還沒正式商業化，全部爬做個人使用階段，無妨」 — 法律嚴謹度弱（§51「個人或家庭非營利目的」主體是自然人/家庭，公司行為不適用），但現實風險低（沒對外 = 沒原告）。**正式商業化前必須清理**，律師會議前不刪除（給律師看到完整現狀）。

### 5.4 待律師確認

- robots.txt 黑名單沒列 DubaoBot 的法律狀態（站方意圖證據 vs 技術合規）。
- metadata-only（標題 + URL + 日期）是否真的不踩 §22 重製。
- 「個人使用階段全爬」的法律緩衝期是否成立 — 公司設立前 vs 後、商業化前 vs 後的時間切點。
- 偽裝 UA 是否在台灣判例中已被視為「主動規避意圖」加重事由。

**Status**: pending（待律師審視）

---

## 6. 對 CC BY-NC-ND source 的處理（pending）

### 6.1 案例：環境資訊中心（einfo）

einfo 採 CC BY-NC-ND 4.0（姓名標示-非商業性-禁止改作）：
- robots.txt 全 Allow，技術上完全允許爬取。
- 授權條款明文：「商業使用須申請」、「轉載須符合非商業性條件」。
- ToS 提供「商用授權申請表」管道。

### 6.2 license breach ≠ 著作權 fair use 攻防

**初步推理**：
- CC license 是**合約性質的明示授權範圍**。著作權人主動聲明「我允許 X 使用方式，不允許 Y 使用方式」。
- 違反 CC license = license breach（合約違約 + 著作權侵權）。
- **沒有 fair use 抗辯空間** — 著作權人已主動放棄部分權利但保留商業/改作權利，使用者無法主張「未經授權但符合 fair use」。
- 對比著作權侵權的攻防：未授權使用 + fair use 抗辯（4 要素分析）。CC license 違反不走這條路徑。

### 6.3 讀豹商業 SaaS 對 CC BY-NC-ND 的衝突

**初步推理**：
- 讀豹是付費搜尋平台 → **NC 紅線**（非商業性條件直接踩）。
- 讀豹做 chunk + LLM 摘要 + 重新組合 → **可能踩 ND 紅線**（禁改作）。
- 雙重違反 license terms。

### 6.4 初步推理建議

**初步推理建議**（**待律師確認**）：

1. **唯一合法路徑**：透過 einfo 站方「商用授權申請表」**另行取得商業授權**。律師會議前可先聯繫站方探詢授權條件（不簽約 — 商業條件需律師審）。
2. **若無法取得授權**：**不爬**。從 source list 移除 einfo。
3. **不採用 metadata-only / embedding-only / aggregated summary 路徑** — license terms 明示禁止商業使用，灰色路徑無法主張 fair use。
4. **既存資料處理**：律師意見前不主動刪除（保留法律對話素材），但**停止新增**（暫停 einfo 的 indexing pipeline）。

### 6.5 待律師確認

- CC BY-NC-ND 的「商業性」定義是否擴大到 metadata（標題 + URL + 日期）等書目資訊。
- CC license 在台灣法院的合約強制力（國內外有無判例）。
- 「另行取得商業授權」的合約模板與條件。
- 授權失敗時的「不爬」是否需要清理既存資料。

**Status**: pending（待律師審視）

---

## 7. Aggregated 摘要 vs 個別摘要（pending）

### 7.1 個別摘要的風險

**個別摘要**：對單一文章做 1:1 摘要（一篇文章 → 一段摘要）。

**初步推理**（**法律風險高**）：
- 容易被認定為 **§28 衍生著作**（基於原著作另為創作）。
- 容易被認定為 **「實質類似」**（蘋果 vs 自由案 72.4% 紅線 — 重製比例過高即構成侵權，「摘要」即使不是逐字 copy，語義對應度高仍可能踩線）。
- F4「對原作市場潛在影響」強 — 使用者讀完摘要後可能不再點原文，直接替代原媒體訂閱。

### 7.2 Aggregated 摘要的優勢

**Aggregated 摘要**：跨多篇文章綜合成一段分析（N 篇 → 一段綜合判斷）。

**初步推理**（**法律狀態好很多**）：
- 事實取自多源 — 不再是「單一原作的衍生」。
- 表達是讀豹自己的（LLM 生成的綜合分析） — Factor 1 transformative 強。
- F4 市場替代弱 — 使用者要看具體報導仍須回原站。
- **法律支撐**：Wikipedia 條目的合法基礎 — 「2024 台積電財報」是綜合多家媒體寫的事實彙整，沒被告。

### 7.3 公平法 §25 風險（仍存在）

**catch**：aggregated 摘要仍有公平法 §25「榨取他人努力成果」風險，特別是當：
- **包含獨家事實**（某媒體調查努力的成果）— 必須 paraphrase + 註明來源，不可 verbatim 引用獨家文字。
- **彙整資料庫風險**：全網全量爬取 + 系統性彙整 = 仍可能踩 §25（Lawsnote 案邏輯）。
- **量化問題**：aggregated 多少篇才夠 transformative？沒有明確標準。

### 7.4 律師上次會議的「主觀認定」

律師（2026-04-15 會議）原話：「我們做摘要，如果太詳細，會出事。但這個很看主觀認定」。CEO 追問「跟 RSS 一樣的詳細度可以嗎」，律師回「不確定」。

**初步推理建議邊界**（CEO 規則 + Zoe 雙保險，**待律師確認**）：

| 搜尋結果數 | 處理方式 | 法律論述 |
|-----------|--------|--------|
| **< 5** | **不做摘要** — 直接 reference list + 著作權說明，推薦使用者點 [N] 連結看原文 | 文章量少 → 個別摘要風險最高（接近 1:1 對應）→ 強制走「純 reference 模式」 |
| **≥ 5** | **Aggregated + Wikipedia reference** 雙保險 — LLM 生成跨多篇綜合分析，報告末尾 references 列出所有引用來源 | 多源綜合 → Factor 1 transformative 強，F4 市場替代弱 |

### 7.5 工程實作（待律師確認後啟動）

- reasoning pipeline 加判斷分支：
  - `IF result_count < 5 → bypass summary generation → 直接 render reference list + 著作權說明`
  - `ELSE → 走 aggregated 摘要 + Wikipedia 模式`
- LLM prompt 約束：「不可 verbatim 連續 > 20 字」（防止 LLM 偷懶整段 copy）。
- 對「獨家事實」的處理：必須 paraphrase + 註明來源。
- references 區塊強制顯示（現有 `[N]` + Source Map 已涵蓋，**禁止為精簡 UI 拿掉**）。
- citation hyperlink `target="_blank"`（待確認前端 code 是否已加）。

### 7.6 待律師確認

- Aggregated 摘要的「主觀認定」邊界 — N 篇 aggregated 才夠 transformative？律師上次說「不確定」。
- 「獨家事實」的識別標準 — 哪些事實屬於某媒體的獨家調查努力？
- 公平法 §25「榨取他人努力」的具體適用條件 — 全網全量爬 + 系統性彙整是否一定踩線？
- < 5 結果的「純 reference 模式」是否真的不踩 §22 重製。

**Status**: pending（待律師審視）

---

## 8. AI Rephrase 不採用（pending）

### 8.1 直覺與反直覺

**直覺推論**：「把爬下來的 chunks 用 AI rephrase 後存『描述』而非原文」看似乾淨（原文不存 = 不重製）。

**反直覺結論**（**法律上更糟，不是更好**）：
- 著作權法 **§3 改寫** = **§28 改作** 侵權。
- 實質類似度（語義對應）AI rephrase 可達 **95%+**，**遠超**蘋果 vs 自由案的 **72.4% 紅線**。
- **雙重侵權**：§22 暫時重製（rephrase 過程載入原文）+ §28 改作（最終存的 rephrase 版本）。

### 8.2 國際對標

- **NYT v OpenAI**（S.D.N.Y., 2023/12 立案；2025/04/04 多數 motion to dismiss 被駁回，進入 discovery）：OpenAI 同樣論述「不存原文只存模型權重」**未被法庭接受**。雖然 LLM 訓練 vs RAG embedding/rephrase 是不同層次，但「轉換後的儲存形式 = 不侵權」這個直覺已被國際法庭多次拒絕。

### 8.3 四種模式對照（D-2026-05-01）

| 方案 | §22 重製 | §28 改作 | 法律確定性 | 搜尋品質 |
|------|---------|---------|----------|--------|
| 全爬 + 存原文（現狀） | 🔴 重 | 🟢 無 | 確定有風險 | 🟢 高 |
| 全爬 + AI rephrase | 🟠 暫時重製 | 🔴 改作 | 雙重風險 | 🟢 高 |
| Embedding-only（方向 B） | 🟡 灰色 | 🟡 灰色 | 不確定（無判例） | 🟢 高 |
| Metadata-only（方向 C） | 🟢 無 | 🟢 無 | 確定無風險 | 🟠 中（只剩標題搜尋） |

### 8.4 初步推理建議

**初步推理建議**（**待律師確認**）：

1. **不採用 AI rephrase 路徑**。任何「rephrase 後存」的設計都應該被拒絕。
2. **指數光譜選擇**：metadata-only（最乾淨）→ embedding-only（灰色）→ AI rephrase（紅線）→ 存原文（紅線）。律師回覆前避免任何 rephrase 實作。
3. **既存資料**：讀豹目前不存 rephrase，未來不應新增。

### 8.5 待律師確認

- AI rephrase 的「實質類似度」紅線 — 蘋果 vs 自由的 72.4% 是否直接套用到 LLM rephrase。
- AI rephrase 的「§28 改作」適用性 — 學說對「機器改作」是否需要人類創作意圖。
- 對既存（如有）rephrase 內容的處理。

**Status**: pending（待律師審視）

---

## 9. 國際對標

### 9.1 Lawsnote 案（新北地院 111 智訴 8，2025/06/24）— 國內最強警示

| 項目 | 內容 |
|------|------|
| 案號 | 新北地院 111 年度智訴字第 8 號 |
| 判決 | 創辦人 4 年徒刑、技術主管 2 年、公司罰金 150 萬、民事連帶賠 1 億 545 萬 |
| 法源 | 著作權法 §91-2（意圖銷售重製）+ 刑法 §359（無故取得電磁紀錄）+ 公平法 §25（榨取他人努力） |

**對讀豹的核心威脅論述**：
- 「利用專業優勢非法取得競爭對手辛苦建置之資料」。
- 引用美國 Alsup 法官：「轉化性使用原則上成立，但**須以合法取得為前提**」。
- 排斥「爬蟲是普遍工具」的抗辯。

**初步推理**：
- 「合法取得」前提**排除**美式 transformative use 寬鬆抗辯 — Google Books fair use 防線在台灣不能直接套用到「違反 robots.txt 明文禁止」或「繞過 paywall」取得的內容。
- 對讀豹的對應：(1) 全文重製到 retrieval index → §91-2 (2) 商業 SaaS + 流量損害 → §25 (3) 若爬 paywall → §359。

### 9.2 Bartz v Anthropic（N.D. Cal. 2025/06 + 2025/09 和解）

- 訓練 LLM = transformative ✅
- **但**：不法來源（盜版資料集）**獨立**構成侵權 ❌
- 對讀豹的對應：**絕對不可爬 paywall 內容**。即使後續 RAG 處理 transformative，盜版來源獨立侵權。

### 9.3 NYT v OpenAI（S.D.N.Y., 2023/12 立案）

- 2025/04/04 多數 motion to dismiss 被駁回，進入 discovery。
- OpenAI 論述「不存原文只存模型權重」**未被法庭接受**。
- 對讀豹的對應：「rephrase 後不存原文」這個直覺已被國際法庭多次拒絕（§8.2）。

### 9.4 Perplexity 訴訟群（進行中）

- News Corp v. Perplexity（S.D.N.Y., 2024/10）— 將是第一個 RAG-only AI search 的 fair use 實體判決。
- Britannica v. Perplexity（S.D.N.Y., 2025/09）。
- Chicago Tribune v. Perplexity（S.D.N.Y., 2025/12）— 主張 Comet 瀏覽器 bypass paywall。
- 對讀豹的對應：高度關注 News Corp v. Perplexity 實體判決，將定義 RAG-only fair use 邊界。

### 9.5 Google Books（最佳有利錨點）

- Authors Guild v. Google, 2d Cir. 2015, 804 F.3d 202。
- Google 全文掃描 2,000 萬本書 + 16% snippet + 永久封鎖部分區塊 + 連結回書店 = **fair use**。
- 對讀豹的 4 個複製路徑：
  - 後端全文 index → ✅ 一致（為搜尋功能必要）
  - 限制 snippet 長度 → ⚠️ 讀豹**必須實作**
  - 連結回原站 → ✅ 已是設計
  - **防 snippet 重組（多次查詢拼回全文）** → ⚠️ 讀豹**尚未實作**

### 9.6 hiQ v LinkedIn / Meta v Bright Data

- 公開資料爬取**不違反 CFAA**。
- 「未登入訪客」不受會員 ToS 約束。
- 對讀豹的對應：**永遠以未登入訪客身份爬取**，不要建立帳號爬付費內容。

### 9.7 Thomson Reuters v ROSS（D. Del. 2025/02）— 第一個 AI fair use 敗訴

- 紅線：與原告**直接競爭** + **derivative market**（授權市場）受損。
- 對讀豹的對應：B2B 客戶**不能是「替代媒體訂閱」的市場**。

**Status**: pending（待律師審視）

---

## 10. 內部紀律

以下紀律屬於「可逆變動 + 律師會議前可立即執行」的範圍。**不涉及**任何不可逆的產品變動。

### 10.1 DubaoBot user-agent 紀律

**現狀**：crawler 用 Chrome 120 UA（`code/python/crawler/core/settings.py:66`）。

**初步推理建議**（**待律師確認**）：
- 改為 `DubaoBot/1.0 (+https://twdubao.com/about-bot)` 或類似明示身份格式。
- 移除 Chrome UA 偽裝。
- Field v Google 的「good-faith」防線需要明示身份作為前提。

**注意**：此變動會立刻被 UDN/Chinatimes 的 robots.txt 黑名單命中（黑名單會擴張到 DubaoBot），即觸發本 spec §5（黑名單 source 處理）。**律師會議前若改 UA，必須同步處理黑名單 source 的爬取行為**。

### 10.2 爬取頻率限制

**現狀**：依 `docs/specs/laptop-crawler-spec.md` / `docs/specs/gcp-crawler-spec.md`，現有 rate limit 設計。

**初步推理建議**：律師會議前不變動，但記錄現狀（每個 source 的 RPS、總 daily volume）作為律師討論素材。

### 10.3 robots.txt 遵守機制

**D-2026-04-15 律師會議前降為 P1**（status.md 既有紀錄）。

**初步推理建議**：律師會議後**升回 P0**（依 `docs/legal/legal-research.md` §五保命設計清單）。律師會議前可先實作 read-only 的 robots.txt diff 監控（記錄 source 端 robots.txt 變動），不啟動 enforcement。

### 10.4 Paywall detection

**現狀**：7 個 parser 全無 paywall detection 邏輯，目前未踩到主要靠運氣。

**初步推理建議**：律師會議前可開始設計 detection heuristic（HTML class/text、articleBody 結尾截斷詞），但**不啟動 enforcement**（避免影響現有抓取行為，給律師看到完整現狀）。

### 10.5 既存資料處理

**初步推理建議**：律師意見回來前**不主動刪除**任何既存於 PG `articles.body` / `chunks.text` 的內容。理由：
- 可逆變動 → 不可逆變動的轉換需要律師背書。
- 保留法律對話素材（律師看完整現狀後決定如何處理）。
- 「個人使用階段全爬」的法律緩衝期是否成立 — 待律師確認。

**Status**: pending（待律師審視）

---

## 11. 律師會議待確認清單

以下為**必須在下次律師會議釐清**的核心問題（與 `memory/project_legal_consultation.md` 的「想請律師確認的問題」對齊，並補充本 spec 衍生問題）：

### 11.1 著作權法核心問題

1. **§22 重製的範圍**：retrieval index（PG 存全文 + chunking + embedding）是否屬於著作權法 §22 的重製？學說有爭議（暫時 vs 永久），台灣尚無向量資料庫的判決。
2. **§52 合理引用主體**：「使用者透過讀豹工具做研究引用」這個主體論述能否站？類比圖書館影印機判例。
3. **§28 改作權**：AI rephrase chunks 是改作（風險高），但 embedding 是否屬於改作？學說有爭議。

### 11.2 來源處理問題

4. **「不法取得 vs 合法取得」的 robots.txt 角色**：robots.txt 違反是否會把 fair use / 合理使用整個翻盤（類比 Lawsnote 案引用 Alsup 法官的論述）？
5. **黑名單 source 的 metadata-only 是否真的安全**：標題 + URL + 日期是否完全不踩 §22 重製？律師之前有提過「主觀認定」是否擴大解釋到書目資訊？
6. **CC BY-NC-ND（einfo）的 metadata-only 處理**：CC license 是否擴大解釋到 metadata（標題 + URL + 日期）？

### 11.3 摘要設計問題

7. **Aggregated 摘要 vs 個別摘要的法律分界**（律師上次會議說「主觀認定」 — 是否能更具體化）：
   - N 篇 aggregated 才夠 transformative？
   - 「獨家事實」的識別標準？
   - 公平法 §25 的具體適用條件？

### 11.4 商業化前提問題

8. **「個人使用階段全爬」的可行性**：開發/測試階段尚未上線，全爬資料庫但不對外，是否有 §51 適用空間？正式商業化前必須做哪些清理？
9. **公司正式設立時程與 ToS 生效時點的關係**。

### 11.5 跨境管轄問題

10. **跨境管轄 / 適用法律**：
    - B2B 客戶在境外（如新加坡、日本）的服務是否落入該境法律管轄？
    - 美國判例（Google Books / Bartz / NYT v OpenAI）對台灣判決的參照價值多大？
    - 歐盟 DSM 第 4 條 TDM exception 的 opt-out 機制 — 若進入歐盟市場必須處理。

### 11.6 業界比較問題

11. **公關資料庫業界比較**：意藍、傳立、Newsleopard 等公關資料庫業者收錄新聞包裝販售，業內做法為何 OK？讀豹差別在哪？
12. **企業 AI 內部調查的合法性**：金融/顧問業拿網路公開但有著作權的資料做 AI 內部調查，為什麼合法？（內部使用 vs 對外販售？個別員工合理使用 vs 系統性收錄？）
13. **基於 11+12 的盤點**：(a) 是否能類比公關資料庫定位 (b) 能否走「企業內部研究工具」路線 (c) 業界普遍做法是否暗示某種台灣法律的事實默認（silent acquiescence）。

### 11.7 訴訟動向問題

14. **Lawsnote 案二審展望**：對讀豹的法律地圖是否會有實質改變？
15. **News Corp v. Perplexity 實體判決**（追蹤中）：若判決出來會如何影響讀豹？
16. **中央社 vs fineweb-zhtw 偵查結果**：第一個台灣 AI 訓練資料集刑案，對讀豹有何啟示？

**Status**: pending（待律師審視）

---

## 12. 附錄：相關決策索引

### 12.1 Active 決策（與本 spec 相關）

- **資料來源策略：只收錄可信來源，不做全網爬取**（D-2025-11，`docs/decisions.md`）— 「目標用戶需要引用於正式場合，資料來源的可信度是核心價值」。
- **來源選擇：先從主要媒體 + 環境研究來源開始**（D-2025-10，`docs/decisions.md`）— 「先收錄主要新聞來源（LTN/UDN/CNA/Chinatimes），加上環境研究相關（einfo/ESG BT/MOEA）」。
- **Web Search 限制**（D-2026-01，`docs/decisions.md`）— 「純搜尋功能應基於可信來源，不混入外部網路結果」。

### 12.2 Pending 決策（直接對應本 spec）

5 條 pending 決策（**Date**: 2026-05-01，**Status**: pending（待律師審視）），全文見 `docs/decisions.md` 行 657-689：

1. **D-2026-05-01「對 robots.txt 黑名單 source 的處理策略」** — 對應本 spec §5。
2. **D-2026-05-01「對 CC BY-NC-ND license source（einfo）的處理決策」** — 對應本 spec §6。
3. **D-2026-05-01「Aggregated 摘要 vs 個別摘要的產品設計決策」** — 對應本 spec §7。
4. **D-2026-05-01「不做 AI rephrase（雙重侵權風險）」** — 對應本 spec §8。
5. **D-2026-05-01「法律乾淨度光譜選擇：metadata > embedding > rephrase > 原文」** — 對應本 spec §4。

### 12.3 Lessons（IP 推理）

`memory/lessons-general.md` 行 556-602「法律與著作權（IP 風險推理）」段落（2026-05-01）：

- AI rephrase 是 §28 改作不是 metadata。
- 法律乾淨度光譜：metadata > embedding > rephrase > 原文。
- Lawsnote 案的「合法取得」前提排除 transformative use 抗辯。
- CC BY-NC-ND license 不能靠 fair use / 合理使用救。
- 「不在 robots.txt 黑名單就算合規」是錯誤推論。
- Aggregated 摘要 vs 個別摘要的法律分界。

### 12.4 相關 Spec / 文件

- `docs/legal/legal-research.md` — Synthesis（339 行）。
- `docs/legal/legal-audit-crawler.md` — 7 source 的 robots.txt + ToS + paywall 風險清單。
- `docs/in progress/discussions/legal-strategy-discussion.md` — 內部 CEO+Zoe 討論記錄。
- `memory/project_legal_consultation.md` — 律師會議結論（2026-04-15）+ 待議。
- `docs/specs/laptop-crawler-spec.md` / `docs/specs/gcp-crawler-spec.md` — Crawler 規格。
- `docs/scratch/legal-research-w1-domestic.md` / `legal-research-w2-international.md` / `legal-research-w3-tos.md` — 原始研究草稿。

**Status**: pending（待律師審視）

---

## ⚠️ 結語：Pending 紀律

本 spec 的所有條目在律師正式書面意見回覆前，**全文標示為 PENDING**。

工程同事 / 產品同事在閱讀本 spec 時：
- **不要**將「初步推理建議」當成「立即執行的指令」。
- **不要**為了「乾淨度光譜」而執行任何不可逆的產品變動（刪除 source、刪 DB 內容、改 ToS）。
- **可以**做可逆的紀律性記錄（robots.txt diff、paywall heuristic 設計草稿、UA 改名計畫文件）。
- **必須**在律師會議結束、正式書面意見回覆後，逐條更新本 spec 為 `active`，並啟動實作。

律師會議完成後，本 spec 的更新流程：
1. 律師逐條意見對表 → 對齊 §3-§8 的「初步推理建議」是否成立。
2. 升級條目從 `pending` → `active`，並修改本 spec。
3. 同步更新 `memory/project_legal_consultation.md` 的「期望律師會議產出」清單。
4. 同步更新 `docs/decisions.md` 的 5 條 pending 決策（D-2026-05-01）為 `active`。
5. 啟動工程實作 — 例如 metadata-only schema、reasoning pipeline 的 < 5 結果分支、UA 改 DubaoBot、robots.txt 升回 P0 等。

**Status**: pending（待律師審視）

---

*文件性質：內部 spec 草稿，全文 PENDING。律師意見回覆前不視為定案。商業決策前須諮詢執業律師。*

*建立日期：2026-05-04*
*建立者：Zoe（CTO）— 整理 CEO + Zoe 內部討論*
*最後更新：2026-05-04（建立）*
